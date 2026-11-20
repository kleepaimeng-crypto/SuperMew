import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx2
import pytest
from pydantic import SecretStr
from test_rag_materials import materials

from minimew.execution.process import ProcessReporter
from minimew.execution.tools import ToolFailure
from minimew.rag.bailian_reranker import RERANK_URL, BailianReranker, read_scores
from minimew.rag.pipeline import RagPipeline
from minimew.rag.protocol import BAILIAN_RERANK_REVISION, BAILIAN_RERANKER, RagPolicy
from minimew.rag.reranker import RerankError


@pytest.mark.parametrize("code", [200, 401, 503])
def test_pipeline_uses_selected_api_and_preserves_original_passages(code):
    scope, runtime, rows = materials()
    runtime.encoder = SimpleNamespace(encode_query=lambda _: [1.0] + [0.0] * 511)
    sends = []

    def respond(request):
        assert str(request.url) == RERANK_URL and request.method == "POST"
        assert request.headers["authorization"] == "Bearer fixture-secret"
        body = json.loads(request.content)
        assert body["model"] == BAILIAN_RERANKER and body["return_documents"] is False
        assert body["top_n"] == len(body["documents"])
        assert set(body["documents"]) == {row["payload"]["embedding_text"] for row in rows}
        sends.append(body)
        return httpx2.Response(
            code,
            json={
                "model": BAILIAN_RERANKER,
                "results": [
                    {"index": i, "relevance_score": (i + 1) / len(body["documents"])}
                    for i in reversed(range(len(body["documents"])))
                ],
            },
        )

    runtime.bailian_reranker = BailianReranker(
        None, None, transport_factory=lambda: httpx2.MockTransport(respond)
    )
    runtime.bailian_reranker.credentials = AsyncMock(return_value=SecretStr("fixture-secret"))
    runtime.reranker = None
    policy = RagPolicy(
        strategy="hybrid", reranker=BAILIAN_RERANKER, revision=BAILIAN_RERANK_REVISION
    )
    credential = uuid4()
    timeline = AsyncMock()
    result = asyncio.run(
        RagPipeline(None, runtime, retriever=AsyncMock(return_value=rows)).search(
            "query",
            scope,
            scope.user_id,
            policy,
            rerank_credential_id=credential,
            process=ProcessReporter(uuid4(), timeline),
        )
    )
    assert len(sends) == result.trace.attempts == 1
    assert (
        result.trace.fallback
        == {
            200: None,
            401: "RERANK_PROVIDER_AUTH_FAILED",
            503: "RERANK_PROVIDER_UNAVAILABLE",
        }[code]
    )
    assert len(result.rows) == len(rows)
    assert {row["score_kind"] for row in result.rows} == {"rerank_raw" if code == 200 else "rrf"}
    runtime.bailian_reranker.credentials.assert_awaited_once_with(
        scope.user_id, credential, RERANK_URL
    )
    steps = [call.args[0] for call in timeline.await_args_list]
    assert any(s["kind"] == "rerank" and BAILIAN_RERANKER in s["summary"] for s in steps)
    assert "fixture-secret" not in repr(steps)


def test_absolute_deadline_stops_instead_of_returning_rrf():
    scope, runtime, rows = materials()
    runtime.encoder = SimpleNamespace(encode_query=lambda _: [1.0] + [0.0] * 511)
    runtime.bailian_reranker = BailianReranker(None, None)
    runtime.bailian_reranker.credentials = AsyncMock()
    policy = RagPolicy(
        strategy="hybrid", reranker=BAILIAN_RERANKER, revision=BAILIAN_RERANK_REVISION
    )

    async def scenario():
        with pytest.raises(ToolFailure, match="AGENT_DEADLINE_EXCEEDED"):
            await RagPipeline(None, runtime, retriever=AsyncMock(return_value=rows)).search(
                "query",
                scope,
                scope.user_id,
                policy,
                rerank_credential_id=uuid4(),
                deadline=asyncio.get_running_loop().time() - 1,
            )
        runtime.bailian_reranker.credentials.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "results",
    [
        [{"index": 0, "relevance_score": 0.1}],
        [{"index": 0, "relevance_score": 0.1}, {"index": 0, "relevance_score": 0.8}],
        [{"index": 0, "relevance_score": 0.1}, {"index": 1, "relevance_score": float("nan")}],
        [{"index": 0, "relevance_score": 0.1}, {"index": 4, "relevance_score": 0.8}],
    ],
)
def test_result_must_cover_each_index_once_with_finite_score(results):
    with pytest.raises(RerankError, match="RERANK_INVALID_OUTPUT"):
        read_scores(
            SimpleNamespace(json=lambda: {"model": BAILIAN_RERANKER, "results": results}), 2
        )
