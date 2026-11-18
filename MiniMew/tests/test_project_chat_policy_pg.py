"""通过正式上传/发布和请求构建，验证项目目录权限与普通模式隔离。"""

import asyncio
import os
from uuid import UUID

import pytest
from test_documents_pg import account, database_call, upload
from test_indexing_pg import client as client
from test_indexing_pg import worker
from test_projects_pg import NamespacedBackend, project, thread
from test_runs_pg import configure_model, submit

from minimew.execution.project_context import load_project_context
from minimew.execution.repository import ExecutionRepository
from minimew.execution.service import RunExecutor
from minimew.execution.tools import ToolContext

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned temporary PostgreSQL required"
)


def project_doc(client, headers, pid, title, key):
    result = client.post(
        "/v1/documents",
        headers=headers,
        data={"project_id": pid, "idempotency_key": key},
        files={"file": (title, "# Java\n使用try-catch处理异常。".encode())},
    )
    assert result.status_code == 202, result.text
    return result.json()["document"]["id"]


def test_catalog_is_owned_published_active_and_project_scoped(client, tmp_path):
    headers, owner = account(client)
    other, stranger = account(client)
    p = project(client, headers, "Java学习")
    q = project(client, headers, "财务")
    r = project(client, other, "他人私有")
    tid = UUID(thread(client, headers, p))
    plain = UUID(thread(client, headers))
    project_doc(client, headers, p, "Java异常处理.md", "java")
    withdrawn = project_doc(client, headers, p, "已下线.md", "withdrawn")
    project_doc(client, headers, q, "其他项目.md", "finance")
    project_doc(client, other, r, "其他用户.md", "private")
    upload(client, headers, key="personal")

    async def scenario(sessions):
        w = worker(sessions, tmp_path, backend=NamespacedBackend())
        try:
            for _ in range(5):
                assert await w.run_once()
            withdrawn_response = client.delete("/v1/documents/" + withdrawn, headers=headers)
            assert withdrawn_response.status_code == 200
            project_doc(client, headers, p, "等待构建.md", "pending")
            async with sessions() as session:
                context = await load_project_context(session, owner, tid)
                assert context.name == "Java学习"
                assert context.document_titles == ("Java异常处理.md",)
                assert not context.catalog_limited
                assert await load_project_context(session, owner, plain) is None
                with pytest.raises(ValueError, match="not owned"):
                    await load_project_context(session, stranger, tid)
        finally:
            w.pool.shutdown()

    asyncio.run(database_call(scenario))


def test_request_constructs_project_policy_without_requiring_memories(client, model_configuration):
    headers, owner = account(client)
    pid = project(client, headers, "Java学习")
    tid = UUID(thread(client, headers, pid))
    plain = UUID(thread(client, headers))
    asyncio.run(configure_model("project-policy-" + str(tid), model_configuration))
    rid = UUID(
        submit(client, headers, str(tid), key="p", content="Java异常处理有哪些？").json()["id"]
    )
    plain_run = UUID(
        submit(client, headers, str(plain), key="plain", content="Java异常处理有哪些？").json()[
            "id"
        ]
    )

    async def scenario(sessions):
        repo = ExecutionRepository(sessions)
        for thread_id, run_id, expected_project in ((tid, rid, True), (plain, plain_run, False)):
            claim = await repo.claim(owner, thread_id, run_id)
            assert claim
            request = await repo.request(claim)
            assert request.messages[-1].content == "Java异常处理有哪些？"
            assert bool(request.project_context) == expected_project
            context = ToolContext(owner, thread_id, run_id, frozenset())
            prepared = RunExecutor._with_instructions(request, context)
            assert ('"conversation_scope": "project"' in prepared.instructions) == expected_project
            if expected_project:
                assert request.project_context.document_titles == ()
                assert "Java学习" in prepared.instructions
            await repo.activate(claim, request.snapshot.answer.configuration.timeout_seconds)
            await repo.request_cancel(owner, thread_id, run_id)
            assert await repo.finish(claim, error_code="EXECUTOR_INTERRUPTED") == "cancelled"

    asyncio.run(database_call(scenario))
