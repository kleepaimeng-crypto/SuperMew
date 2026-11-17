import asyncio
import json
import os
from uuid import UUID, uuid4

import pytest
from execution_helpers import FakeProvider
from test_documents_pg import account, database_call, upload
from test_execution_pg import facts
from test_indexing_pg import client as client
from test_indexing_pg import worker
from test_knowledge import Runtime
from test_knowledge_pg import thread, tool_turn
from test_runs_pg import configure_model, submit

from minimew.execution.messages import ModelTurn
from minimew.execution.provider import ProviderCompleted, ProviderDelta
from minimew.execution.repository import ExecutionRepository
from minimew.execution.service import RunExecutor
from minimew.execution.tools import ToolRegistry
from minimew.knowledge import CAPABILITY, KnowledgeService
from minimew.models import Run

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned temporary PostgreSQL required"
)


@pytest.mark.parametrize("mode", ["success", "changed_body"])
def test_real_graph_stops_expansion_and_commits_answer_with_reserved_repair(
    client, tmp_path, monkeypatch, model_configuration, mode
):
    monkeypatch.setenv("MINIMEW_REASONING_ENABLED", "true")
    monkeypatch.setenv("MINIMEW_REPAIRS_ENABLED", "true")
    asyncio.run(configure_model("reserve-" + uuid4().hex, model_configuration))
    headers, owner = account(client)
    upload(client, headers, content="# 制度\n餐费上限150元，需票据。".encode())
    tid = thread(client, headers)
    rid = UUID(submit(client, headers, str(tid), content="餐费有哪些规定？").json()["id"])

    async def scenario(sessions):
        w = worker(sessions, tmp_path)
        roles = []
        grades = 0

        async def answer(request):
            nonlocal grades
            roles.append(request.purpose)
            if request.purpose == "grade":
                grades += 1
                value = {
                    "status": "insufficient",
                    "supported": [],
                    "missing": ["待验证"],
                    "decompose": grades == 1,
                }
            elif request.purpose == "plan":
                value = {"questions": [f"餐费条件{i}" for i in range(4)]}
            elif request.purpose == "rewrite":
                value = {"query": "餐费适用条件"}
            elif request.purpose == "repair":
                payload = json.loads(request.messages[-1].content)
                value = "## 文档依据\n" + payload["original_answer"]
                if mode == "changed_body":
                    value = value.replace("150", "200")
                yield ProviderDelta(value)
                yield ProviderCompleted(ModelTurn("text", value))
                return
            elif request.messages[-1].role == "tool":
                assert request.force_answer
                data = json.loads(request.messages[-1].content)
                assert data["assessment"]["status"] == "insufficient"
                source = data["matches"][0]
                yield ProviderCompleted(
                    ModelTurn("text", f"{source['text']}[{source['citation_id']}]")
                )
                return
            else:
                yield tool_turn()
                return
            yield ProviderCompleted(ModelTurn("text", json.dumps(value)))

        try:
            assert await w.run_once()
            repo = ExecutionRepository(sessions)
            before = await facts(repo, rid)
            async with sessions() as session:
                row = await session.get(Run, rid)
                frozen = dict(row.model_snapshot_json)
                digest = row.model_catalog_hash
                assert frozen["repairs"]["finalization"] == "reserved-v1"
            executor = RunExecutor(
                repo,
                FakeProvider(answer),
                registry=ToolRegistry((KnowledgeService(sessions, Runtime(w.backend)).spec(),)),
                capabilities=frozenset({CAPABILITY}),
                cancellation_poll=0.01,
            )
            assert await executor.execute(owner, tid, rid) == (
                "succeeded" if mode == "success" else "failed"
            )
            assert len(roles) <= 12 and roles.count("answer") + roles.count("repair") <= 4
            assert roles[-2:] == ["answer", "repair"]
            after = await facts(repo, rid)
            assert [m[0] for m in after["messages"]] == [m[0] for m in before["messages"]]
            assert not any(e.type == "message.delta" for e in after["events"])
            assert sum(e.type == "tool.completed" for e in after["events"]) == 1
            if mode == "success":
                assert sum(e.type == "run.completed" for e in after["events"]) == 1
                response = client.get(f"/v1/threads/{tid}/runs/{rid}/answer", headers=headers)
                assert response.status_code == 200 and response.json()["materials"]["citations"]
            else:
                assert after["error"] == "PROVIDER_INVALID_RESPONSE"
            async with sessions() as session:
                row = await session.get(Run, rid)
                assert row.model_snapshot_json == frozen and row.model_catalog_hash == digest
        finally:
            w.pool.shutdown(wait=True)

    asyncio.run(database_call(scenario))
