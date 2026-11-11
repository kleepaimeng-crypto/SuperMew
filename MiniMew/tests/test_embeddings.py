"""离线验证编码边界；固定假 Encoder，不下载/加载真实权重。"""

import json
import math
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from minimew.documents.profiles import preset
from minimew.embeddings.adapter import LocalEmbeddingAdapter
from minimew.embeddings.artifacts import REQUIRED_FILES, local_snapshot, prepare_model
from minimew.embeddings.cli import main
from minimew.embeddings.config import PROJECT_ROOT, EmbeddingRuntimeSettings
from minimew.embeddings.errors import EmbeddingError

PROFILE = preset("bge-small-zh-v1.5").embedding


class FakeEncoder:
    def __init__(self):
        self.inputs = []
        self.lengths = None
        self.result = None

    def token_lengths(self, texts):
        # 固定测试计量，不用它证明中文真实 Token 数。
        return self.lengths if self.lengths is not None else [len(text) + 2 for text in texts]

    def encode(self, texts, batch_size):
        self.inputs.append(list(texts))
        if self.result is not None:
            return self.result
        return [[1.0] + [0.0] * 511 for _ in texts]


def make_adapter(tmp_path, backend=None, batch_size=2):
    backend = backend if backend is not None else FakeEncoder()
    return LocalEmbeddingAdapter(
        PROFILE, EmbeddingRuntimeSettings(cache_dir=tmp_path, batch_size=batch_size), backend
    )


def write_snapshot(settings):
    path = (
        settings.resolved_cache_dir
        / "models--BAAI--bge-small-zh-v1.5"
        / "snapshots"
        / PROFILE.revision
    )
    for name in REQUIRED_FILES:
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fixture-only")
    return path


def test_runtime_settings_do_not_require_api_secrets_and_anchor_relative_paths(monkeypatch):
    monkeypatch.setenv("MINIMEW_EMBEDDING_CACHE_DIR", "data/test-models")
    monkeypatch.setenv("MINIMEW_EMBEDDING_BATCH_SIZE", "3")
    settings = EmbeddingRuntimeSettings()
    assert settings.resolved_cache_dir == PROJECT_ROOT / "data/test-models"
    assert settings.batch_size == 3


@pytest.mark.parametrize("size", [0, 65])
def test_invalid_batch_size_is_rejected(size):
    with pytest.raises(ValidationError):
        EmbeddingRuntimeSettings(batch_size=size)


def test_queries_get_prefix_once_documents_keep_supplied_title_context(tmp_path):
    backend = FakeEncoder()
    adapter = make_adapter(tmp_path, backend)
    text = "差旅报销 → 餐费\n每日150元"
    adapter.encode_documents([text, "另一段", "第三段"])
    query = adapter.encode_query("餐费报多少？")
    assert backend.inputs == [[text, "另一段"], ["第三段"], [PROFILE.query_prefix + "餐费报多少？"]]
    assert len(query) == 512
    assert math.isclose(sum(value * value for value in query), 1)


def test_empty_document_batch_does_not_call_model(tmp_path):
    backend = FakeEncoder()
    assert make_adapter(tmp_path, backend).encode_documents([]) == []
    assert backend.inputs == []


@pytest.mark.parametrize("value", ["bare string", [""], ["   "], [None], [123]])
def test_invalid_document_inputs_are_rejected(tmp_path, value):
    backend = FakeEncoder()
    with pytest.raises(EmbeddingError, match="EMBEDDING_INVALID_INPUT"):
        make_adapter(tmp_path, backend).encode_documents(value)
    assert backend.inputs == []


@pytest.mark.parametrize("query", [False, True])
def test_special_tokens_and_query_prefix_count_before_encoding(tmp_path, query):
    backend = FakeEncoder()
    adapter = make_adapter(tmp_path, backend)
    prefix_length = len(PROFILE.query_prefix) if query else 0
    text = "x" * (512 - prefix_length - 2)
    if query:
        adapter.encode_query(text)
    else:
        adapter.encode_documents([text])
    assert len(backend.inputs) == 1
    with pytest.raises(EmbeddingError, match="EMBEDDING_INPUT_TOO_LONG"):
        if query:
            adapter.encode_query(text + "x")
        else:
            adapter.encode_documents([text + "x"])
    assert len(backend.inputs) == 1


@pytest.mark.parametrize(
    "output",
    [[], [[1.0] + [0.0] * 510], [[0.0] * 512], [[float("nan")] * 512], [[float("inf")] * 512]],
)
def test_invalid_vectors_are_not_returned_as_success(tmp_path, output):
    backend = FakeEncoder()
    backend.result = output
    with pytest.raises(EmbeddingError, match="EMBEDDING_INVALID_OUTPUT"):
        make_adapter(tmp_path, backend).encode_documents(["text"])


def test_backend_failure_is_typed_and_not_retried(tmp_path):
    backend = FakeEncoder()
    backend.encode = Mock(side_effect=RuntimeError("private backend error"))
    with pytest.raises(EmbeddingError, match="^EMBEDDING_ENCODING_FAILED$"):
        make_adapter(tmp_path, backend).encode_documents(["text"])
    assert backend.encode.call_count == 1


def test_failure_in_later_batch_does_not_return_partial_result(tmp_path):
    backend = FakeEncoder()
    backend.encode = Mock(side_effect=[[[1.0] + [0.0] * 511], RuntimeError("failed")])
    with pytest.raises(EmbeddingError, match="EMBEDDING_ENCODING_FAILED"):
        make_adapter(tmp_path, backend, batch_size=1).encode_documents(["first", "second"])
    assert backend.encode.call_count == 2


def test_missing_or_partial_snapshot_never_loads_or_downloads(tmp_path, monkeypatch):
    settings = EmbeddingRuntimeSettings(cache_dir=tmp_path)
    factory = Mock()
    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=factory)
    )
    with pytest.raises(EmbeddingError, match="EMBEDDING_NOT_PREPARED"):
        LocalEmbeddingAdapter.load_cpu(PROFILE, settings)
    path = write_snapshot(settings)
    (path / "model.safetensors").unlink()
    with pytest.raises(EmbeddingError, match="EMBEDDING_NOT_PREPARED"):
        LocalEmbeddingAdapter.load_cpu(PROFILE, settings)
    factory.assert_not_called()


def test_m3_and_altered_profile_are_rejected_without_fallback(tmp_path):
    settings = EmbeddingRuntimeSettings(cache_dir=tmp_path)
    for profile in [preset("bge-m3").embedding, PROFILE.model_copy(update={"revision": "0" * 40})]:
        with pytest.raises(EmbeddingError, match="EMBEDDING_UNSUPPORTED_PROFILE"):
            prepare_model(profile, settings)


def test_explicit_preparation_downloads_only_pinned_required_files(tmp_path, monkeypatch):
    settings = EmbeddingRuntimeSettings(cache_dir=tmp_path)
    download = Mock(side_effect=lambda **kwargs: write_snapshot(settings))
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=download))
    assert prepare_model(PROFILE, settings) == local_snapshot(PROFILE, settings)
    kwargs = download.call_args.kwargs
    assert kwargs["repo_id"] == PROFILE.model_id
    assert kwargs["revision"] == PROFILE.revision
    assert kwargs["token"] is False
    assert kwargs["endpoint"] == "https://huggingface.co"
    assert kwargs["allow_patterns"] == list(REQUIRED_FILES)
    assert "pytorch_model.bin" not in kwargs["allow_patterns"]


def test_download_failure_has_stable_error_without_success_marker(tmp_path, monkeypatch):
    download = Mock(side_effect=OSError("private path/credential"))
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=download))
    settings = EmbeddingRuntimeSettings(cache_dir=tmp_path)
    with pytest.raises(EmbeddingError, match="^EMBEDDING_PREPARATION_FAILED$"):
        prepare_model(PROFILE, settings)
    with pytest.raises(EmbeddingError, match="EMBEDDING_NOT_PREPARED"):
        local_snapshot(PROFILE, settings)
    assert download.call_count == 1


def test_loader_uses_local_cpu_snapshot_without_implicit_prompts(tmp_path, monkeypatch):
    settings = EmbeddingRuntimeSettings(cache_dir=tmp_path)
    path = write_snapshot(settings)
    model = Mock()
    model.max_seq_length = 512
    model.get_embedding_dimension.return_value = 512
    model.__getitem__ = Mock(return_value=SimpleNamespace(pooling_mode="cls"))
    model.tokenizer.return_value = {"input_ids": [[101, 5, 102]]}
    model.encode.return_value = [[1.0] + [0.0] * 511]
    factory = Mock(return_value=model)
    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=factory)
    )
    adapter = LocalEmbeddingAdapter.load_cpu(PROFILE, settings)
    adapter.encode_query("question")
    factory.assert_called_once_with(
        str(path),
        device="cpu",
        local_files_only=True,
        trust_remote_code=False,
        token=False,
        model_kwargs={"use_safetensors": True},
    )
    assert model.tokenizer.call_args.kwargs == {
        "add_special_tokens": True,
        "truncation": False,
        "padding": False,
    }
    assert model.encode.call_args.args[0] == [PROFILE.query_prefix + "question"]
    assert model.encode.call_args.kwargs["prompt"] == ""
    assert model.encode.call_args.kwargs["normalize_embeddings"] is True


def test_status_cannot_claim_encoding_verified(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINIMEW_EMBEDDING_CACHE_DIR", str(tmp_path))
    write_snapshot(EmbeddingRuntimeSettings())
    monkeypatch.setattr(sys, "argv", ["embedding", "status"])
    assert main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "files_present"
    assert result["encoding_verified"] is False


@pytest.mark.parametrize(
    "dimension,limit,pooling", [(1024, 512, "cls"), (512, 128, "cls"), (512, 512, "mean")]
)
def test_loaded_model_must_match_frozen_encoding_policy(
    tmp_path, monkeypatch, dimension, limit, pooling
):
    settings = EmbeddingRuntimeSettings(cache_dir=tmp_path)
    write_snapshot(settings)
    model = Mock()
    model.max_seq_length = limit
    model.get_embedding_dimension.return_value = dimension
    model.__getitem__ = Mock(return_value=SimpleNamespace(pooling_mode=pooling))
    factory = Mock(return_value=model)
    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=factory)
    )
    with pytest.raises(EmbeddingError, match="EMBEDDING_MODEL_MISMATCH"):
        LocalEmbeddingAdapter.load_cpu(PROFILE, settings)


def test_importing_adapter_and_cli_does_not_import_model_libraries():
    code = (
        "import sys; import minimew.embeddings.adapter; import minimew.embeddings.cli; "
        "assert not any(n in sys.modules for n in "
        "['torch', 'sentence_transformers', 'huggingface_hub'])"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=15)
