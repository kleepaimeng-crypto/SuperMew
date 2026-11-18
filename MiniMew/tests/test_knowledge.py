"""知识Tool的范围、结果投影和错误语义；所有重资源为测试注入。"""

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from test_vector_store import FakeBackend, candidate

import minimew.knowledge as knowledge
from minimew.documents.retrieval import RetrievalScope
from minimew.embeddings.errors import EmbeddingError
from minimew.execution.messages import ToolCall
from minimew.execution.tools import ToolContext, ToolFailure, ToolRegistry
from minimew.knowledge import CAPABILITY, NAME, KnowledgeService
from minimew.vector_store.adapter import CandidateIndex
from minimew.vector_store.manifest import VectorError


class Encoder:
    def encode_query(self, query):
        return [1.0] + [0.0] * 511


class Runtime:
    def __init__(self, backend=None):
        self.encoder = Encoder()
        self.index = CandidateIndex(backend or FakeBackend())
        self.calls = []

    async def sync(self, operation, *args, **kwargs):
        self.calls.append(operation)
        return operation(*args, **kwargs)


def setup_service(monkeypatch):
    monkeypatch.setattr(knowledge, "validate_delivery", AsyncMock())
    target = candidate()
    backend = FakeBackend()
    index = CandidateIndex(backend)
    index.stage(target)
    scope = RetrievalScope(
        target.manifest.scope.identity.user_id,
        (target.manifest,),
        datetime.now(UTC) + timedelta(minutes=1),
    )
    scoped = AsyncMock(return_value=scope)
    hits = index.search(index.verify(target.manifest), [1.0] + [0.0] * 511, knowledge.PROFILE)
    search = AsyncMock(return_value=hits)
    monkeypatch.setattr(knowledge, "published_scope", scoped)
    monkeypatch.setattr(knowledge, "search_published", search)
    runtime = Runtime(backend)
    service = KnowledgeService(object(), runtime)
    registry = ToolRegistry((service.spec(),))
    context = registry.bind_context(
        ToolContext(scope.user_id, uuid4(), uuid4(), frozenset({CAPABILITY}))
    )
    return service, registry, context, runtime, scoped, search, hits


def invoke(registry, context, query="餐费"):
    calls = registry.prepare((ToolCall("call_1", NAME, json.dumps({"query": query})),), context)
    return registry.invoke(calls[0], context)


def test_result_contains_original_text_and_version_location_without_storage_internals(monkeypatch):
    service, registry, context, runtime, scoped, search, hits = setup_service(monkeypatch)
    result = json.loads(asyncio.run(invoke(registry, context)))
    assert result["status"] == "ok" and len(result["matches"]) == 3
    first = result["matches"][0]
    assert first["text"] == hits[0]["payload"]["source_text"] and "150" in first["text"]
    assert first["version_id"] == str(scoped.return_value.targets[0].scope.identity.version_id)
    assert first["chunk_id"] == hits[0]["chunk_id"]
    assert first["start_line"] >= 1 and first["end"] - first["start"] == len(first["text"])
    assert "embedding_text" not in json.dumps(result) and "vector" not in json.dumps(result)
    assert len(json.dumps(result, ensure_ascii=False)) < 12000
    assert search.await_args.kwargs["offload"] == runtime.sync


def test_split_table_header_reaches_tool_result_without_changing_cited_source(monkeypatch):
    _, registry, context, _, _, search, hits = setup_service(monkeypatch)
    row = {**hits[0], "payload": {**hits[0]["payload"], "table_header": "|类型|说明|\n|---|---|\n"}}
    search.return_value = [row]
    result = json.loads(asyncio.run(invoke(registry, context)))
    match = result["matches"][0]
    assert match["table_header"] == row["payload"]["table_header"]
    assert match["text"] == row["payload"]["source_text"]
    assert context.evidence.items[0].text == match["text"]
    assert "table_header" not in context.evidence.items[0].model_dump()


def test_one_scope_per_run_and_new_run_gets_fresh_scope(monkeypatch):
    _, registry, context, runtime, scoped, search, hits = setup_service(monkeypatch)

    async def scenario():
        await invoke(registry, context, "餐费")
        first_scope = context.tool_sessions[NAME].scope
        changed = replace(first_scope, targets=())
        scoped.return_value = changed
        await invoke(registry, context, "住宿")
        assert scoped.await_count == 1 and search.await_count == 2
        assert search.await_args.args[2] is first_scope
        fresh = registry.bind_context(
            ToolContext(context.user_id, context.thread_id, uuid4(), context.capabilities)
        )
        assert fresh.tool_sessions[NAME] is not context.tool_sessions[NAME]
        assert json.loads(await invoke(registry, fresh))["status"] == "no_documents"
        assert scoped.await_count == 2 and len(runtime.calls) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("empty", ["catalog", "matches"])
def test_healthy_empty_results_have_explicit_meaning(monkeypatch, empty):
    _, registry, context, runtime, scoped, search, _ = setup_service(monkeypatch)
    if empty == "catalog":
        scoped.return_value = replace(scoped.return_value, targets=())
    else:
        search.return_value = []
    value = json.loads(asyncio.run(invoke(registry, context)))
    assert value == {
        "status": "no_documents" if empty == "catalog" else "no_matches",
        "matches": [],
        "limited": False,
    }
    assert len(runtime.calls) == (0 if empty == "catalog" else 1)


@pytest.mark.parametrize(
    "mode,code",
    [
        ("vector", "KNOWLEDGE_RETRIEVAL_FAILED"),
        ("scope", "KNOWLEDGE_SCOPE_INVALID"),
        ("encoding", "KNOWLEDGE_ENCODING_FAILED"),
        ("length", "KNOWLEDGE_QUERY_TOO_LONG"),
    ],
)
def test_resource_failures_are_not_empty_and_keep_typed_code(monkeypatch, mode, code):
    _, registry, context, runtime, scoped, search, _ = setup_service(monkeypatch)
    if mode in ("vector", "scope"):
        search.side_effect = VectorError(
            "VECTOR_UNAVAILABLE" if mode == "vector" else "INDEX_SCOPE_EXPIRED"
        )
    else:

        def fail(query):
            raise EmbeddingError(
                "EMBEDDING_INPUT_TOO_LONG" if mode == "length" else "private-model-failure"
            )

        runtime.encoder.encode_query = fail
    with pytest.raises(ToolFailure) as failure:
        asyncio.run(invoke(registry, context))
    assert str(failure.value) == code


def test_catalog_failure_is_not_empty(monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    _, registry, context, _, scoped, search, _ = setup_service(monkeypatch)
    scoped.side_effect = SQLAlchemyError("private database details")
    with pytest.raises(ToolFailure, match="KNOWLEDGE_RETRIEVAL_FAILED"):
        asyncio.run(invoke(registry, context))
    search.assert_not_awaited()


@pytest.mark.parametrize("field", ["user_id", "thread_id", "run_id"])
def test_rebound_context_cannot_use_original_run_session(monkeypatch, field):
    _, registry, context, _, scoped, _, _ = setup_service(monkeypatch)
    with pytest.raises(ToolFailure, match="TOOL_PERMISSION_DENIED"):
        asyncio.run(invoke(registry, replace(context, **{field: uuid4()})))
    scoped.assert_not_awaited()


@pytest.mark.parametrize("field", ["query", "user_id", "filter"])
def test_model_cannot_supply_identity_or_arbitrary_filter(monkeypatch, field):
    _, registry, context, _, scoped, _, _ = setup_service(monkeypatch)
    arguments = {"query": 123} if field == "query" else {"query": "餐费", field: "forged"}
    with pytest.raises(ToolFailure, match="TOOL_INVALID_ARGUMENTS"):
        registry.prepare((ToolCall("c", NAME, json.dumps(arguments)),), context)
    scoped.assert_not_awaited()


@pytest.mark.parametrize("field", ["chunk_id", "source_text", "identity", "section"])
def test_invalid_match_identity_or_content_is_not_returned(monkeypatch, field):
    from copy import deepcopy

    _, registry, context, _, _, search, hits = setup_service(monkeypatch)
    bad = deepcopy(hits[0])
    if field == "chunk_id":
        bad["chunk_id"] = str(uuid4())
    elif field == "source_text":
        bad["payload"]["source_text"] = "changed"
    elif field == "identity":
        bad["payload"]["identity"]["user_id"] = str(uuid4())
    else:
        bad["payload"]["section"]["section_id"] = str(uuid4())
    search.return_value = [bad]
    with pytest.raises(ToolFailure, match="KNOWLEDGE_RESULT_INVALID"):
        asyncio.run(invoke(registry, context))


def test_output_budget_keeps_whole_matches_and_reports_limited(monkeypatch):
    service, registry, context, _, _, _, hits = setup_service(monkeypatch)
    # 仅测试投影后的结果预算；真实payload验证在上面的用例中覆盖。
    scope = knowledge.published_scope.return_value
    text = "x" * 5000
    original = service.project(hits[0], scope)
    match = original.model_copy(
        update={
            "text": text,
            "end": original.start + len(text),
            "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
        }
    )
    monkeypatch.setattr(service, "project", lambda row, scope: match)
    result = json.loads(asyncio.run(invoke(registry, context)))
    assert result["limited"] is True and 0 < len(result["matches"]) < 3
    assert all(m["text"] == "x" * 5000 for m in result["matches"])
    assert {m["citation_id"] for m in result["matches"]} == {
        item.citation_id for item in context.evidence.items
    }


def test_tool_session_directory_is_immutable_and_scope_not_exposed_in_schema(monkeypatch):
    _, registry, context, _, _, _, _ = setup_service(monkeypatch)
    with pytest.raises(TypeError):
        context.tool_sessions[NAME] = object()
    parameters = registry.visible(context)[0].wire()["function"]["parameters"]
    assert set(parameters["properties"]) == {"query"}
    assert "scope" not in json.dumps(parameters)
    with pytest.raises(ValueError):
        registry.bind_context(context)
    with pytest.raises(ValueError, match="session factory"):
        replace(registry.specs[0], session_factory="not-callable")


@pytest.mark.parametrize(
    "code",
    [
        "KNOWLEDGE_ENCODING_FAILED",
        "KNOWLEDGE_RETRIEVAL_FAILED",
        "KNOWLEDGE_SCOPE_INVALID",
        "KNOWLEDGE_RESULT_INVALID",
        "KNOWLEDGE_QUERY_TOO_LONG",
    ],
)
def test_knowledge_codes_round_trip_contract(code):
    from minimew.generated.run_event_v1 import EVENT_ADAPTER

    value = {
        "schema_version": 1,
        "event_id": str(uuid4()),
        "run_id": str(uuid4()),
        "thread_id": str(uuid4()),
        "sequence": 1,
        "type": "run.failed",
        "timestamp": datetime.now(UTC).isoformat(),
        "data": {"status": "failed", "error_code": code},
    }
    parsed = EVENT_ADAPTER.validate_json(json.dumps(value))
    assert parsed.data.error_code == code
    assert EVENT_ADAPTER.validate_json(EVENT_ADAPTER.dump_json(parsed)) == parsed
