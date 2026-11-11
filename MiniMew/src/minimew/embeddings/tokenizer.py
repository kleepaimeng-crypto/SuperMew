"""固定本地模型的计数/字符对齐；不加载权重，也不联网下载。"""

from minimew.documents.profiles import EmbeddingProfile
from minimew.embeddings.artifacts import local_snapshot
from minimew.embeddings.config import EmbeddingRuntimeSettings
from minimew.embeddings.errors import EmbeddingError


class LocalTokenizer:
    def __init__(self, profile: EmbeddingProfile, backend):
        if not backend.is_fast:
            raise EmbeddingError("EMBEDDING_TOKENIZER_UNSUPPORTED")
        self.profile = profile
        self._backend = backend

    @classmethod
    def load(
        cls, profile: EmbeddingProfile, settings: EmbeddingRuntimeSettings
    ) -> "LocalTokenizer":
        path = local_snapshot(profile, settings)
        try:
            from transformers import AutoTokenizer
        except ImportError as exc:
            raise EmbeddingError("EMBEDDING_DEPENDENCIES_UNAVAILABLE") from exc
        try:
            return cls(
                profile,
                AutoTokenizer.from_pretrained(
                    str(path),
                    use_fast=True,
                    local_files_only=True,
                    trust_remote_code=False,
                    token=False,
                ),
            )
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError("EMBEDDING_TOKENIZATION_FAILED") from exc

    def count(self, text: str) -> int:
        try:
            return len(
                self._backend(text, add_special_tokens=True, truncation=False, verbose=False)[
                    "input_ids"
                ]
            )
        except Exception as exc:
            raise EmbeddingError("EMBEDDING_TOKENIZATION_FAILED") from exc

    def offsets(self, text: str) -> list[tuple[int, int]]:
        try:
            result = self._backend(
                text,
                add_special_tokens=False,
                truncation=False,
                return_offsets_mapping=True,
                verbose=False,
            )["offset_mapping"]
            return [(start, end) for start, end in result if end > start]
        except Exception as exc:
            raise EmbeddingError("EMBEDDING_TOKENIZATION_FAILED") from exc
