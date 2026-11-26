"""只有 prepare 显式联网；正常运行只核对固定 revision 的本地快照。"""

from pathlib import Path

from minimew.documents.profiles import EmbeddingProfile, preset
from minimew.embeddings.config import EmbeddingRuntimeSettings
from minimew.embeddings.errors import EmbeddingError

REQUIRED_FILES = (
    "config.json",
    "config_sentence_transformers.json",
    "modules.json",
    "sentence_bert_config.json",
    "1_Pooling/config.json",
    "model.safetensors",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
)


def require_supported_profile(profile: EmbeddingProfile) -> None:
    if profile != preset("bge-small-zh-v1.5").embedding:
        raise EmbeddingError("EMBEDDING_UNSUPPORTED_PROFILE")


def local_snapshot(profile: EmbeddingProfile, settings: EmbeddingRuntimeSettings) -> Path:
    require_supported_profile(profile)
    # Hugging Face 的版本快照布局；不读取 refs/main 或尝试寻找其他 revision。
    path = (
        settings.resolved_cache_dir
        / ("models--" + profile.model_id.replace("/", "--"))
        / "snapshots"
        / profile.revision
    )
    try:
        complete = all(
            (path / name).is_file() and (path / name).stat().st_size for name in REQUIRED_FILES
        )
    except OSError as exc:
        raise EmbeddingError("EMBEDDING_CACHE_UNAVAILABLE") from exc
    if not complete:
        raise EmbeddingError("EMBEDDING_NOT_PREPARED")
    return path


def local_tokenizer_file(profile: EmbeddingProfile, settings: EmbeddingRuntimeSettings) -> Path:
    require_supported_profile(profile)
    cached = (
        settings.resolved_cache_dir
        / ("models--" + profile.model_id.replace("/", "--"))
        / "snapshots"
        / profile.revision
        / "tokenizer.json"
    )
    bundled = Path(__file__).resolve().parent / "assets" / profile.id / "tokenizer.json"
    for candidate in (cached, bundled):
        try:
            if candidate.is_file() and candidate.stat().st_size:
                return candidate
        except OSError as exc:
            raise EmbeddingError("EMBEDDING_CACHE_UNAVAILABLE") from exc
    raise EmbeddingError("EMBEDDING_NOT_PREPARED")


def prepare_model(profile: EmbeddingProfile, settings: EmbeddingRuntimeSettings) -> Path:
    require_supported_profile(profile)
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise EmbeddingError("EMBEDDING_DEPENDENCIES_UNAVAILABLE") from exc
    try:
        snapshot_download(
            repo_id=profile.model_id,
            revision=profile.revision,
            cache_dir=settings.resolved_cache_dir,
            allow_patterns=list(REQUIRED_FILES),
            token=False,
            endpoint="https://huggingface.co",
            max_workers=2,
        )
        return local_snapshot(profile, settings)
    except EmbeddingError:
        raise
    except Exception as exc:
        raise EmbeddingError("EMBEDDING_PREPARATION_FAILED") from exc
