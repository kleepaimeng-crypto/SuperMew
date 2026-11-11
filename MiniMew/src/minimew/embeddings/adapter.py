"""同步 CPU 编码；未来异步 Worker 调用时须在受控线程中运行。"""

import math
from collections.abc import Sequence
from typing import Protocol

from minimew.documents.profiles import EmbeddingProfile
from minimew.embeddings.artifacts import local_snapshot, require_supported_profile
from minimew.embeddings.config import EmbeddingRuntimeSettings
from minimew.embeddings.errors import EmbeddingError


class EncoderBackend(Protocol):
    def token_lengths(self, texts: list[str]) -> list[int]: ...

    def encode(self, texts: list[str], batch_size: int) -> Sequence[Sequence[float]]: ...


class _SentenceTransformerBackend:
    def __init__(self, model):
        self.model = model

    def token_lengths(self, texts: list[str]) -> list[int]:
        encoded = self.model.tokenizer(
            texts, add_special_tokens=True, truncation=False, padding=False
        )
        return [len(ids) for ids in encoded["input_ids"]]

    def encode(self, texts: list[str], batch_size: int) -> Sequence[Sequence[float]]:
        return self.model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
            precision="float32",
            prompt="",  # 前缀由 Adapter 按冻结配置添加，禁止库再自动添加一次。
        )


class LocalEmbeddingAdapter:
    def __init__(
        self,
        profile: EmbeddingProfile,
        settings: EmbeddingRuntimeSettings,
        backend: EncoderBackend,
    ):
        require_supported_profile(profile)
        self.profile = profile
        self.settings = settings
        self._backend = backend

    @classmethod
    def load_cpu(
        cls, profile: EmbeddingProfile, settings: EmbeddingRuntimeSettings
    ) -> "LocalEmbeddingAdapter":
        path = local_snapshot(profile, settings)
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingError("EMBEDDING_DEPENDENCIES_UNAVAILABLE") from exc
        try:
            model = SentenceTransformer(
                str(path),
                device="cpu",
                local_files_only=True,
                trust_remote_code=False,
                token=False,
                model_kwargs={"use_safetensors": True},
            )
            if (
                model.get_embedding_dimension() != profile.dimension
                or model.max_seq_length < profile.max_input_tokens
            ):
                raise EmbeddingError("EMBEDDING_MODEL_MISMATCH")
            # 使用快照的 CLS pooling，不重新构造或替换模型模块。
            pooling = model[1]
            if pooling.pooling_mode != "cls":
                raise EmbeddingError("EMBEDDING_MODEL_MISMATCH")
            model.max_seq_length = profile.max_input_tokens
            model.eval()
            return cls(profile, settings, _SentenceTransformerBackend(model))
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError("EMBEDDING_LOAD_FAILED") from exc

    def encode_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """调用方先组织好标题+正文；Adapter 不猜测来源或标题。"""
        return self._encode(texts, self.profile.document_prefix)

    def encode_query(self, text: str) -> list[float]:
        return self._encode([text], self.profile.query_prefix)[0]

    def _encode(self, texts: Sequence[str], prefix: str) -> list[list[float]]:
        if isinstance(texts, (str, bytes)) or any(
            not isinstance(text, str) or not text.strip() for text in texts
        ):
            raise EmbeddingError("EMBEDDING_INVALID_INPUT")
        inputs = [prefix + text for text in texts]
        if not inputs:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(inputs), self.settings.batch_size):
            batch = inputs[start : start + self.settings.batch_size]
            try:
                lengths = self._backend.token_lengths(batch)
                if len(lengths) != len(batch):
                    raise EmbeddingError("EMBEDDING_INVALID_OUTPUT")
                if any(length > self.profile.max_input_tokens for length in lengths):
                    raise EmbeddingError("EMBEDDING_INPUT_TOO_LONG")
                result = self._backend.encode(batch, self.settings.batch_size)
                if len(result) != len(batch):
                    raise EmbeddingError("EMBEDDING_INVALID_OUTPUT")
                for row in result:
                    vector = [float(value) for value in row]
                    if (
                        len(vector) != self.profile.dimension
                        or not all(math.isfinite(value) for value in vector)
                        or not math.isclose(math.sqrt(sum(v * v for v in vector)), 1, abs_tol=1e-4)
                    ):
                        raise EmbeddingError("EMBEDDING_INVALID_OUTPUT")
                    vectors.append(vector)
            except EmbeddingError:
                raise
            except Exception as exc:
                raise EmbeddingError("EMBEDDING_ENCODING_FAILED") from exc
        return vectors
