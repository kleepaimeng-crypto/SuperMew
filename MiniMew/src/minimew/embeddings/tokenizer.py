"""固定本地模型的计数/字符对齐；只用轻量 tokenizers，不加载权重、torch 或联网下载。"""

from minimew.documents.profiles import EmbeddingProfile
from minimew.embeddings.artifacts import local_tokenizer_file
from minimew.embeddings.config import EmbeddingRuntimeSettings
from minimew.embeddings.errors import EmbeddingError


class LocalTokenizer:
    def __init__(self, profile: EmbeddingProfile, backend):
        if getattr(backend, "is_fast", True) is False or not callable(
            getattr(backend, "encode", None)
        ):
            raise EmbeddingError("EMBEDDING_TOKENIZER_UNSUPPORTED")
        self.profile = profile
        self._backend = backend

    @classmethod
    def load(
        cls, profile: EmbeddingProfile, settings: EmbeddingRuntimeSettings
    ) -> "LocalTokenizer":
        path = local_tokenizer_file(profile, settings)
        try:
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise EmbeddingError("EMBEDDING_DEPENDENCIES_UNAVAILABLE") from exc
        try:
            backend = Tokenizer.from_file(str(path))
            backend.no_truncation()
            backend.no_padding()
            return cls(profile, backend)
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError("EMBEDDING_TOKENIZATION_FAILED") from exc

    def count(self, text: str) -> int:
        try:
            return len(self._backend.encode(text, add_special_tokens=True).ids)
        except Exception as exc:
            raise EmbeddingError("EMBEDDING_TOKENIZATION_FAILED") from exc

    def offsets(self, text: str) -> list[tuple[int, int]]:
        try:
            result = self._backend.encode(text, add_special_tokens=False).offsets
            return [(start, end) for start, end in result if end > start]
        except Exception as exc:
            raise EmbeddingError("EMBEDDING_TOKENIZATION_FAILED") from exc
