"""Bounded qwen3-rerank calls for the existing Hybrid retrieval pipeline."""

import asyncio
import math

import httpx2

from minimew.account_models.credentials import CredentialResolver
from minimew.account_models.network import PublicTransport
from minimew.model_control import ModelConfigurationUnavailable
from minimew.rag.protocol import BAILIAN_RERANKER
from minimew.rag.reranker import RerankError

RERANK_URL = "https://dashscope.aliyuncs.com/compatible-api/v1/reranks"


class BailianReranker:
    def __init__(self, sessions, settings, *, transport_factory=PublicTransport):
        self.credentials = CredentialResolver(sessions, settings)
        self.transport_factory = transport_factory

    async def score(self, owner, credential, query, passages, *, deadline=None):
        if credential is None:
            raise RerankError("RERANK_CREDENTIAL_UNAVAILABLE")
        if not 1 <= len(passages) <= 24:
            raise RerankError("RERANK_INVALID_INPUT")
        loop = asyncio.get_running_loop()
        if deadline is not None and loop.time() >= deadline:
            raise RerankError("RERANK_DEADLINE_EXCEEDED")
        try:
            async with asyncio.timeout_at(deadline):
                key = await self.credentials(owner, credential, RERANK_URL)
                async with httpx2.AsyncClient(
                    transport=self.transport_factory(),
                    trust_env=False,
                    follow_redirects=False,
                    timeout=httpx2.Timeout(12.0, connect=4.0),
                ) as client:
                    response = await client.post(
                        RERANK_URL,
                        headers={"Authorization": "Bearer " + key.get_secret_value()},
                        json={
                            "model": BAILIAN_RERANKER,
                            "query": query,
                            "documents": passages,
                            "top_n": len(passages),
                            "return_documents": False,
                        },
                    )
                    response.raise_for_status()
                values = read_scores(response, len(passages))
        except ModelConfigurationUnavailable:
            raise RerankError("RERANK_CREDENTIAL_UNAVAILABLE") from None
        except TimeoutError:
            raise RerankError("RERANK_DEADLINE_EXCEEDED") from None
        except httpx2.TimeoutException:
            if deadline is not None and loop.time() >= deadline:
                raise RerankError("RERANK_DEADLINE_EXCEEDED") from None
            raise RerankError("RERANK_PROVIDER_TIMEOUT") from None
        except httpx2.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                raise RerankError("RERANK_PROVIDER_AUTH_FAILED") from None
            raise RerankError("RERANK_PROVIDER_UNAVAILABLE") from None
        except httpx2.HTTPError:
            raise RerankError("RERANK_PROVIDER_UNAVAILABLE") from None
        if deadline is not None and loop.time() >= deadline:
            raise RerankError("RERANK_DEADLINE_EXCEEDED")
        return values


def read_scores(response, count):
    try:
        body = response.json()
        if body["model"] != BAILIAN_RERANKER or len(body["results"]) != count:
            raise ValueError
        indexed = {}
        for row in body["results"]:
            index, score = row["index"], row["relevance_score"]
            if (
                type(index) is not int
                or not 0 <= index < count
                or index in indexed
                or type(score) not in (int, float)
                or not math.isfinite(score)
                or not 0 <= score <= 1
            ):
                raise ValueError
            indexed[index] = float(score)
        return [indexed[i] for i in range(count)]
    except (ValueError, TypeError, KeyError, OverflowError):
        raise RerankError("RERANK_INVALID_OUTPUT") from None
