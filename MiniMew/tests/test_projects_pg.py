"""项目/手工记忆/下线的真实PG测试；向量依赖显式受控。"""

import asyncio
import os
from uuid import UUID

import pytest
from sqlalchemy import select
from test_documents_pg import account, database_call, upload
from test_indexing_pg import client as client
from test_indexing_pg import worker
from test_runs_pg import configure_model, submit
from test_vector_store import FakeBackend

from minimew.documents.catalog import withdraw
from minimew.documents.jobs import IndexJobRepository, OwnershipLost
from minimew.documents.retrieval import published_scope, search_published
from minimew.models import IndexCleanupJob, Run
from minimew.vector_store.adapter import CandidateIndex
from minimew.vector_store.manifest import PROFILE

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned PG required"
)


class NamespacedBackend(FakeBackend):
    def __init__(self):
        super().__init__()
        self.spaces = {}

    def for_namespace(self, name):
        backend = self.spaces.setdefault(name, FakeBackend())
        backend.namespace = name
        return backend


def project(client, headers, name="论文"):
    result = client.post("/v1/projects", headers=headers, json={"name": name})
    assert result.status_code == 201, result.text
    return result.json()["id"]


def thread(client, headers, project_id=None):
    result = client.post("/v1/threads", headers=headers, json={"project_id": project_id})
    assert result.status_code == 201, result.text
    return result.json()["id"]


def project_upload(client, headers, pid, key="key"):
    return client.post(
        "/v1/documents",
        headers=headers,
        data={"idempotency_key": key, "project_id": pid},
        files={"file": ("policy.md", "# 制度\n项目餐费150元。".encode())},
    )


def test_projects_threads_and_documents_are_scoped_and_owned(client):
    h, user = account(client)
    other, _ = account(client)
    p = project(client, h)
    q = project(client, h, "另一个项目")
    plain = thread(client, h)
    a = thread(client, h, p)
    thread(client, h, q)
    assert [t["id"] for t in client.get("/v1/threads", headers=h).json()["items"]] == [plain]
    assert [
        t["id"]
        for t in client.get("/v1/threads", headers=h, params={"project_id": p}).json()["items"]
    ] == [a]
    assert client.post("/v1/threads", headers=other, json={"project_id": p}).status_code == 404
    assert client.get("/v1/projects/" + p, headers=other).status_code == 404
    root = upload(client, h, key="same").json()
    one = project_upload(client, h, p, "same")
    two = project_upload(client, h, q, "same")
    assert one.status_code == two.status_code == 202, (one.text, two.text)
    assert (
        len(
            {
                root["document"]["knowledge_base_id"],
                one.json()["document"]["knowledge_base_id"],
                two.json()["document"]["knowledge_base_id"],
            }
        )
        == 3
    )
    assert len(client.get("/v1/documents", headers=h).json()["items"]) == 1
    assert (
        client.get("/v1/documents", headers=h, params={"project_id": p}).json()["items"][0]["id"]
        == one.json()["document"]["id"]
    )
    assert project_upload(client, other, p).status_code == 404


def test_memory_is_explicit_versioned_and_frozen_per_run(client, model_configuration):
    h, user = account(client)
    p = project(client, h)
    a = thread(client, h, p)
    b = thread(client, h, p)
    plain = thread(client, h)
    asyncio.run(configure_model("projects-model", model_configuration))
    path = f"/v1/projects/{p}/memories"
    saved = client.post(path, headers=h, json={"content": "实验只使用2024年前数据"})
    assert saved.status_code == 201, saved.text
    memory = saved.json()
    r1 = submit(client, h, a).json()["id"]
    r2 = submit(client, h, b).json()["id"]
    changed = client.patch(
        path + "/" + memory["id"],
        headers=h,
        json={"content": "改为2023年前", "revision": 1, "enabled": True},
    )
    assert changed.status_code == 200 and changed.json()["revision"] == 2
    assert (
        client.patch(
            path + "/" + memory["id"], headers=h, json={"content": "旧版本覆盖", "revision": 1}
        ).status_code
        == 409
    )
    r3 = submit(client, h, a, key="later").json()["id"]
    r4 = submit(client, h, plain).json()["id"]

    async def snapshots(sessions):
        async with sessions() as session:
            return [(await session.get(Run, UUID(i))).memory_snapshot for i in (r1, r2, r3, r4)]

    async def request_snapshot(sessions):
        from sqlalchemy import update
        from sqlalchemy.exc import DBAPIError

        from minimew.execution.repository import ExecutionRepository

        async with sessions() as session:
            with pytest.raises(DBAPIError):
                async with session.begin():
                    await session.execute(
                        update(Run).where(Run.id == UUID(r1)).values(memory_snapshot=[])
                    )
        repo = ExecutionRepository(sessions)
        claim = await repo.claim(user, UUID(a), UUID(r1))
        assert claim
        request = await repo.request(claim)
        assert "2024年前" in request.messages[0].content
        assert "2023年前" not in request.messages[0].content
        await repo.request_cancel(user, UUID(a), UUID(r1))
        await repo.finish(claim, content="owned test cleanup")

    asyncio.run(database_call(request_snapshot))
    snapshots = asyncio.run(database_call(snapshots))
    assert snapshots[0] == snapshots[1]
    assert (
        snapshots[0][0]["revision"] == 1 and snapshots[2][0]["revision"] == 2 and snapshots[3] == []
    )
    assert client.delete(path + "/" + memory["id"], headers=h).status_code == 204
    assert client.get(path, headers=h).json()["items"] == []
    assert client.post(path, headers=h, json={"content": "  "}).status_code == 422

    async def cancel_remaining(sessions):
        from minimew.execution.repository import ExecutionRepository

        repo = ExecutionRepository(sessions)
        for tid, rid in ((b, r2), (a, r3), (plain, r4)):
            await repo.request_cancel(user, UUID(tid), UUID(rid))

    asyncio.run(database_call(cancel_remaining))


def test_project_retrieval_and_withdrawal_gate_inflight_results(client, tmp_path):
    h, user = account(client)
    p = project(client, h)
    a = thread(client, h, p)
    thread(client, h)
    uploaded = project_upload(client, h, p).json()
    doc = uploaded["document"]["id"]

    async def scenario(sessions):
        backend = NamespacedBackend()
        w = worker(sessions, tmp_path, backend=backend)
        try:
            assert await w.run_once()
            assert (await published_scope(sessions, user)).targets == ()
            scope = await published_scope(sessions, user, thread_id=UUID(a))
            assert scope.namespace == "minimew_project_" + UUID(p).hex
            index = CandidateIndex(backend)
            hits = await search_published(
                sessions, index, scope, user, [1.0] + [0.0] * 511, PROFILE
            )
            assert hits and len(backend.rows) == 0 and backend.spaces[scope.namespace].rows

            async def offload(fn, *args, **kwargs):
                result = fn(*args, **kwargs)
                async with sessions() as session:
                    await withdraw(session, user, UUID(doc))
                return result

            assert (
                await search_published(
                    sessions, index, scope, user, [1.0] + [0.0] * 511, PROFILE, offload=offload
                )
                == []
            )
            assert (await published_scope(sessions, user, thread_id=UUID(a))).targets == ()
            async with sessions() as session:
                assert await session.scalar(select(IndexCleanupJob.artifact_id))
        finally:
            w.pool.shutdown(wait=True)

    asyncio.run(database_call(scenario))
    assert client.get("/v1/documents/" + doc, headers=h).json()["deleted_at"]
    assert client.get("/v1/documents", headers=h, params={"project_id": p}).json()["items"] == []
    assert (
        client.get("/v1/documents", headers=h, params={"project_id": p, "archived": True}).json()[
            "items"
        ][0]["id"]
        == doc
    )
    assert client.delete("/v1/documents/" + doc, headers=h).status_code == 200
    assert (
        client.post(
            "/v1/documents/" + doc + "/versions",
            headers=h,
            data={"idempotency_key": "new"},
            files={"file": ("new.md", b"new")},
        ).status_code
        == 409
    )


def test_withdraw_pending_document_fences_existing_worker(client):
    h, user = account(client)
    uploaded = upload(client, h).json()
    doc = uploaded["document"]["id"]

    async def scenario(sessions):
        repo = IndexJobRepository(sessions)
        lease = await repo.claim("test", {uploaded["version"]["build_fingerprint"]})
        async with sessions() as session:
            await withdraw(session, user, UUID(doc))
        with pytest.raises(OwnershipLost):
            await repo.require(lease)
        assert await repo.claim("other", {uploaded["version"]["build_fingerprint"]}) is None

    asyncio.run(database_call(scenario))


def test_withdrawal_after_pipeline_before_tool_delivery_registers_no_evidence(
    client, tmp_path, monkeypatch
):
    from uuid import uuid4

    from test_knowledge import Runtime

    from minimew.execution.tools import ToolContext, ToolFailure, ToolRegistry
    from minimew.knowledge import CAPABILITY, KnowledgeQuery, KnowledgeService
    from minimew.rag.pipeline import RagPipeline

    headers, user = account(client)
    pid = project(client, headers)
    tid = UUID(thread(client, headers, pid))
    document = UUID(project_upload(client, headers, pid).json()["document"]["id"])

    async def scenario(sessions):
        w = worker(sessions, tmp_path, backend=NamespacedBackend())
        original = RagPipeline.search
        try:
            assert await w.run_once()

            async def delayed(pipeline, *args, **kwargs):
                result = await original(pipeline, *args, **kwargs)
                assert result.rows
                async with sessions() as session:
                    await withdraw(session, user, document)
                return result

            monkeypatch.setattr(RagPipeline, "search", delayed)
            runtime = Runtime(w.backend)
            runtime.reranker = None  # 此用例发布Dense，不启动重排模型。
            service = KnowledgeService(sessions, runtime)
            registry = ToolRegistry((service.spec(),))
            context = registry.bind_context(
                ToolContext(user, tid, uuid4(), frozenset({CAPABILITY}))
            )
            with pytest.raises(ToolFailure, match="KNOWLEDGE_SCOPE_INVALID"):
                await service.search(context, KnowledgeQuery(query="餐费"))
            assert context.evidence.items == ()
        finally:
            w.pool.shutdown(wait=True)

    asyncio.run(database_call(scenario))


def test_memory_budget_and_cross_project_changes_are_denied(client):
    h, _ = account(client)
    other, _ = account(client)
    p = project(client, h)
    q = project(client, h)
    path = f"/v1/projects/{p}/memories"
    saved = client.post(path, headers=h, json={"content": "手工记忆"}).json()
    assert (
        client.patch(
            f"/v1/projects/{q}/memories/{saved['id']}",
            headers=h,
            json={"content": "越界", "revision": 1},
        ).status_code
        == 404
    )
    assert client.delete(path + "/" + saved["id"], headers=other).status_code == 404
    for index in range(19):
        assert client.post(path, headers=h, json={"content": str(index)}).status_code == 201
    assert client.post(path, headers=h, json={"content": "超限"}).status_code == 409
    assert len(client.get(path, headers=h).json()["items"]) == 20
    assert (
        client.post(path, headers=h, json={"content": "停用项", "enabled": False}).status_code
        == 201
    )
