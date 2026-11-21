"""Fixed Bailian Embedding preset shared by the indexing and query paths."""

import math
from uuid import UUID

import httpx2

from minimew.account_models.credentials import CredentialResolver
from minimew.account_models.network import PublicTransport
from minimew.embeddings.errors import EmbeddingError
from minimew.execution.control import ExecutionStorageUnavailable
from minimew.model_control import ModelConfigurationUnavailable
from minimew.vector_store.manifest import BAILIAN_PROFILE

BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MAX_BATCH = 10


class BailianEmbedding:
    def __init__(self, sessions, settings, *, transport_factory=PublicTransport):
        self.credentials = CredentialResolver(sessions, settings)
        self.transport_factory = transport_factory

    async def encode(self, user_id: UUID, credential_id: UUID | None, texts: list[str]):
        if credential_id is None:
            raise EmbeddingError("EMBEDDING_CREDENTIAL_UNAVAILABLE")
        try:
            endpoint, key = await self.credentials.retrieval_endpoint(user_id, credential_id)
        except ModelConfigurationUnavailable:
            raise EmbeddingError("EMBEDDING_CREDENTIAL_UNAVAILABLE") from None
        except ExecutionStorageUnavailable:
            raise EmbeddingError("EMBEDDING_CREDENTIAL_STORAGE_UNAVAILABLE") from None
        vectors = []
        async with httpx2.AsyncClient(
            transport=self.transport_factory(),
            trust_env=False,
            follow_redirects=False,
            timeout=httpx2.Timeout(15.0, connect=5.0),
        ) as client:
            for start in range(0, len(texts), MAX_BATCH):
                batch = texts[start : start + MAX_BATCH]
                try:
                    response = await client.post(
                        endpoint + "/embeddings",
                        headers={"Authorization": "Bearer " + key.get_secret_value()},
                        json={
                            "model": BAILIAN_PROFILE.model_id,
                            "dimensions": BAILIAN_PROFILE.dimension,
                            "encoding_format": "float",
                            "input": batch,
                        },
                    )
                    response.raise_for_status()
                except httpx2.HTTPStatusError as exc:
                    if exc.response.status_code in (401, 403):
                        raise EmbeddingError("EMBEDDING_PROVIDER_AUTH_FAILED") from None
                    raise EmbeddingError("EMBEDDING_PROVIDER_UNAVAILABLE") from None
                except httpx2.HTTPError:
                    raise EmbeddingError("EMBEDDING_PROVIDER_UNAVAILABLE") from None
                vectors.extend(_read_vectors(response, len(batch)))
        return vectors


def _read_vectors(response, count):
    try:
        body = response.json()
        if body["model"] != BAILIAN_PROFILE.model_id or len(body["data"]) != count:
            raise ValueError
        rows = {}
        for row in body["data"]:
            index, raw = row["index"], row["embedding"]
            if (
                type(index) is not int
                or not 0 <= index < count
                or index in rows
                or not isinstance(raw, list)
                or len(raw) != BAILIAN_PROFILE.dimension
                or any(type(x) not in (int, float) for x in raw)
            ):
                raise ValueError
            norm = math.hypot(*raw)
            if not math.isfinite(norm) or norm == 0:
                raise ValueError
            rows[index] = [value / norm for value in raw]
        return [rows[i] for i in range(count)]
    except (ValueError, TypeError, KeyError, OverflowError):
        raise EmbeddingError("EMBEDDING_INVALID_OUTPUT") from None
