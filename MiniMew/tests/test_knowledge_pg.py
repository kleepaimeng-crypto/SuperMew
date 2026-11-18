"""真实发布目录和原Run；仅编码/向量/回答模型受控，不访问外部模型。"""

import asyncio
import json
import os
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from execution_helpers import FakeProvider
from test_documents_pg import account, database_call, upload
from test_execution_pg import facts
from test_indexing_pg import client as client
from test_indexing_pg import worker
from test_knowledge import Runtime
from test_runs_pg import configure_model, submit

from minimew.documents.retrieval import published_scope
from minimew.embeddings.errors import EmbeddingError
from minimew.execution.messages import ModelTurn, ToolCall
from minimew.execution.provider import ProviderCompleted
from minimew.execution.repository import ExecutionRepository
from minimew.execution.service import RunExecutor
from minimew.execution.tools import ToolContext, ToolRegistry
from minimew.knowledge import CAPABILITY, NAME, KnowledgeQuery, KnowledgeService, KnowledgeSession
from minimew.vector_store.manifest import VectorError

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Explicit temporary PostgreSQL required"
)


def thread(client, headers):
    response = client.post("/v1/threads", headers=headers, json={})
    assert response.status_code == 201
    return UUID(response.json()["id"])


def tool_turn(identity="call_1"):
    return ProviderCompleted(
        ModelTurn("tool_calls", "", (ToolCall(identity, NAME, '{"query":"餐费标准"}'),))
    )


def test_run_keeps_first_scope_during_new_publication_and_next_run_uses_new_version(
    client, tmp_path, model_configuration
):
    asyncio.run(configure_model("knowledge-" + uuid4().hex, model_configuration))
    headers, owner = account(client)
    uploaded = upload(client, headers, content="# 制度\n餐费150元。\n住宿350元。".encode()).json()
    tid = thread(client, headers)
    rid = UUID(submit(client, headers, str(tid), content="餐费住宿标准").json()["id"])

    async def scenario(sessions):
        w = worker(sessions, tmp_path)
        try:
            assert await w.run_once()
            service = KnowledgeService(sessions, Runtime(w.backend))
            repo = ExecutionRepository(sessions)
            seen, requests = [], []

            async def answer(data):
                requests.append(data)
                if len(requests) == 1:
                    yield tool_turn("meal")
                elif len(requests) == 2:
                    seen.append(json.loads(data.messages[-1].content))
                    newer = upload(
                        client,
                        headers,
                        document=uploaded["document"]["id"],
                        key="v2",
                        content="# 制度\n餐费200元。\n住宿400元。".encode(),
                    ).json()
                    assert await w.run_once()
                    assert (await published_scope(sessions, owner)).targets[
                        0
                    ].scope.identity.version_id == UUID(newer["version"]["id"])
                    yield tool_turn("hotel")
                else:
                    seen.append(json.loads(data.messages[-1].content))
                    match = seen[-1]["matches"][0]
                    yield ProviderCompleted(
                        ModelTurn("text", f"## 文档依据\n{match['text']}[{match['citation_id']}]")
                    )

            executor = RunExecutor(
                repo,
                FakeProvider(answer),
                registry=ToolRegistry((service.spec(),)),
                capabilities=frozenset({CAPABILITY}),
            )
            assert await executor.execute(owner, tid, rid) == "succeeded"
            assert all(
                match["version_id"] == uploaded["version"]["id"]
                for result in seen
                for match in result["matches"]
            )
            assert all("150" in result["matches"][0]["text"] for result in seen)
            saved = await facts(repo, rid)
            assert [e.type for e in saved["events"]] == [
                "run.created",
                "run.started",
                "tool.started",
                "tool.completed",
                "tool.started",
                "tool.completed",
                "message.completed",
                "run.completed",
            ]
            assert len(saved["messages"]) == 2 and "150" in saved["messages"][1][2]

            next_run = UUID(
                submit(client, headers, str(tid), key="second", content="再查餐费").json()["id"]
            )
            next_results = []

            async def next_answer(data):
                if data.messages[-1].role != "tool":
                    yield tool_turn()
                else:
                    next_results.append(json.loads(data.messages[-1].content))
                    match = next_results[-1]["matches"][0]
                    yield ProviderCompleted(
                        ModelTurn("text", f"## 文档依据\n{match['text']}[{match['citation_id']}]")
                    )

            next_executor = RunExecutor(
                repo,
                FakeProvider(next_answer),
                registry=ToolRegistry((service.spec(),)),
                capabilities=frozenset({CAPABILITY}),
            )
            assert await next_executor.execute(owner, tid, next_run) == "succeeded"
            assert "200" in next_results[0]["matches"][0]["text"]
            assert next_results[0]["matches"][0]["version_id"] != uploaded["version"]["id"]
        finally:
            await asyncio.to_thread(w.pool.shutdown)

    asyncio.run(database_call(scenario))


def test_candidate_invisible_and_scope_is_user_owned(client, tmp_path):
    headers, owner = account(client)
    other_headers, other = account(client)
    first = upload(client, headers, content=b"A private knowledge").json()
    second = upload(client, other_headers, key="other", content=b"B private knowledge").json()

    async def scenario(sessions):
        from minimew.models import Thread

        async with sessions() as session, session.begin():
            owned = Thread(user_id=owner, title="scope fixture")
            other_thread = Thread(user_id=other, title="other scope fixture")
            session.add_all([owned, other_thread])
            await session.flush()
            tid, other_tid = owned.id, other_thread.id
        w = worker(sessions, tmp_path)
        service = KnowledgeService(sessions, Runtime(w.backend))
        registry = ToolRegistry((service.spec(),))
        context = registry.bind_context(ToolContext(owner, tid, uuid4(), frozenset({CAPABILITY})))
        try:
            assert (
                await service.search(context, KnowledgeQuery(query="private"))
            ).status == "no_documents"
            assert await w.run_once() and await w.run_once()
            assert (
                await service.search(context, KnowledgeQuery(query="private"))
            ).status == "no_documents"
            fresh = registry.bind_context(ToolContext(owner, tid, uuid4(), context.capabilities))
            result = await service.search(fresh, KnowledgeQuery(query="private"))
            assert result.status == "ok"
            assert {m.version_id for m in result.matches} == {UUID(first["version"]["id"])}
            assert "B private" not in result.model_dump_json()
            other_context = registry.bind_context(
                ToolContext(other, other_tid, uuid4(), context.capabilities)
            )
            other_result = await service.search(other_context, KnowledgeQuery(query="private"))
            assert {m.version_id for m in other_result.matches} == {UUID(second["version"]["id"])}
        finally:
            await asyncio.to_thread(w.pool.shutdown)

    asyncio.run(database_call(scenario))


@pytest.mark.parametrize(
    "mode,code",
    [
        ("encoding", "KNOWLEDGE_ENCODING_FAILED"),
        ("length", "KNOWLEDGE_QUERY_TOO_LONG"),
        ("vector", "KNOWLEDGE_RETRIEVAL_FAILED"),
        ("scope", "KNOWLEDGE_SCOPE_INVALID"),
    ],
)
def test_knowledge_failures_commit_typed_run_failure_without_no_knowledge(
    client, tmp_path, model_configuration, mode, code
):
    asyncio.run(configure_model("failure-" + uuid4().hex, model_configuration))
    headers, owner = account(client)
    upload(client, headers, content=b"owned knowledge")
    tid = thread(client, headers)
    rid = UUID(submit(client, headers, str(tid)).json()["id"])

    async def scenario(sessions):
        w = worker(sessions, tmp_path)
        try:
            assert await w.run_once()
            runtime = Runtime(w.backend)
            if mode in ("encoding", "length"):

                def fail(query):
                    raise EmbeddingError(
                        "EMBEDDING_INPUT_TOO_LONG" if mode == "length" else "private failure"
                    )

                runtime.encoder.encode_query = fail
            elif mode == "vector":

                def fail(*args):
                    raise VectorError("VECTOR_UNAVAILABLE")

                w.backend.search = fail
            service = KnowledgeService(sessions, runtime)
            specification = service.spec()
            if mode == "scope":
                expired = replace(
                    await published_scope(sessions, owner),
                    expires_at=(await published_scope(sessions, owner)).expires_at
                    - timedelta(hours=1),
                )
                specification = replace(
                    specification,
                    session_factory=lambda c: KnowledgeSession(
                        c.user_id, c.thread_id, c.run_id, expired
                    ),
                )

            async def answer(data):
                yield tool_turn()

            repo = ExecutionRepository(sessions)
            assert (
                await RunExecutor(
                    repo,
                    FakeProvider(answer),
                    registry=ToolRegistry((specification,)),
                    capabilities=frozenset({CAPABILITY}),
                ).execute(owner, tid, rid)
                == "failed"
            )
            saved = await facts(repo, rid)
            assert saved["error"] == code and saved["messages"][1][1:] == ("failed", "")
            assert saved["events"][-1].data.error_code == code
        finally:
            await asyncio.to_thread(w.pool.shutdown)

    asyncio.run(database_call(scenario))


def test_api_lifespan_wires_real_knowledge_tool_into_automatic_run(
    client, tmp_path, model_configuration, monkeypatch
):
    import httpx
    from sse_helpers import serve_http
    from test_runtime_pg import runtime_app, wait_run

    from minimew.knowledge_runtime import KnowledgeRuntime
    from minimew.vector_store.adapter import CandidateIndex

    asyncio.run(configure_model("automatic-" + uuid4().hex, model_configuration))
    headers, owner = account(client)
    upload(client, headers, content="# 制度\n餐费150元。".encode())

    async def build(sessions):
        w = worker(sessions, tmp_path)
        try:
            assert await w.run_once()
            return w.backend
        finally:
            await asyncio.to_thread(w.pool.shutdown)

    backend = asyncio.run(database_call(build))
    starts = []

    def load(self):
        from test_knowledge import Encoder

        starts.append(self)
        self.encoder, self.backend, self.index = Encoder(), None, CandidateIndex(backend)

    monkeypatch.setattr(KnowledgeRuntime, "_load", load)
    monkeypatch.setenv("MINIMEW_KNOWLEDGE_ENABLED", "true")

    async def answer(data):
        if data.messages[-1].role != "tool":
            yield tool_turn()
        else:
            value = json.loads(data.messages[-1].content)
            assert value["status"] == "ok" and "150" in value["matches"][0]["text"]
            match = value["matches"][0]
            yield ProviderCompleted(
                ModelTurn("text", f"## 文档依据\n{match['text']}[{match['citation_id']}]")
            )

    provider = FakeProvider(answer)
    with serve_http(runtime_app(monkeypatch, provider)) as server:
        with httpx.Client(base_url=server.base_url, timeout=8) as browser:
            tid = thread(browser, headers)
            response = submit(browser, headers, str(tid))
            assert response.status_code == 201
            state = wait_run(browser, headers, tid, response.json()["id"])
            assert state["status"] == "succeeded" and len(provider.requests) == 2
    assert len(starts) == 1 and starts[0]._closed
