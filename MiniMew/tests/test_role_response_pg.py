"""正式检索/Run终态中的评判修复及错误透传；PG真实，模型和向量受控。"""

import asyncio
import json
import os
from uuid import UUID

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
from minimew.execution.process import ProcessReporter
from minimew.execution.provider import ProviderCompleted
from minimew.execution.repository import ExecutionRepository
from minimew.execution.service import RunExecutor
from minimew.execution.tools import ToolRegistry
from minimew.knowledge import CAPABILITY, KnowledgeService
from minimew.settings import Settings

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned temporary PostgreSQL required"
)


@pytest.mark.parametrize("recover", [False, True])
def test_grade_repair_uses_original_retrieval_and_error_reaches_persisted_events(
    client, tmp_path, model_configuration, monkeypatch, recover
):
    monkeypatch.setenv("MINIMEW_REASONING_ENABLED", "true")
    monkeypatch.setenv("MINIMEW_REPAIRS_ENABLED", "true")
    monkeypatch.setattr("minimew.execution.service.ProcessReporter", ProcessReporter)
    asyncio.run(configure_model("role-pg-" + str(recover).lower(), model_configuration))
    headers, user = account(client)
    upload(client, headers, content="# 制度\n餐费150元。".encode())
    tid = thread(client, headers)
    rid = UUID(submit(client, headers, str(tid), content="餐费多少？").json()["id"])

    async def scenario(sessions):
        w = worker(sessions, tmp_path)
        try:
            assert await w.run_once()
            registry = ToolRegistry((KnowledgeService(sessions, Runtime(w.backend)).spec(),))
            repo = ExecutionRepository(sessions)
            grades = 0
            materials = []

            async def answer(req):
                nonlocal grades
                if req.purpose == "grade":
                    grades += 1
                    assert req.json_output
                    payload = json.loads(req.messages[-1].content)
                    if grades == 1:
                        materials.extend(payload["materials"])
                    else:
                        assert payload["input"]["materials"] == materials
                    value = (
                        {
                            "status": "sufficient",
                            "supported": [materials[0]["citation_id"]],
                            "missing": [],
                        }
                        if recover and grades == 2
                        else {"status": "not-a-status"}
                    )
                    yield ProviderCompleted(ModelTurn("text", json.dumps(value)))
                elif req.messages[-1].role != "tool":
                    yield tool_turn()
                else:
                    yield ProviderCompleted(ModelTurn("text", "## 文档依据\n餐费150元。[E1]"))

            provider = FakeProvider(answer)
            outcome = await RunExecutor(
                repo, provider, registry=registry, capabilities=frozenset({CAPABILITY})
            ).execute(user, tid, rid)
            assert outcome == ("succeeded" if recover else "failed")
            assert grades == 2
            saved = await facts(repo, rid)
            events = saved["events"]
            assert sum(e.type == "tool.started" for e in events) == 1
            assert (
                sum(
                    e.type == "process.step"
                    and e.data.kind == "retrieval"
                    and e.data.status == "completed"
                    for e in events
                )
                == 1
            )
            if recover:
                assert saved["error"] is None
                assert sum(e.type == "tool.completed" for e in events) == 1
            else:
                assert saved["error"] == "PROVIDER_INVALID_RESPONSE"
                failure = next(e for e in events if e.type == "tool.failed")
                assert failure.data.error_code == "PROVIDER_INVALID_RESPONSE"
                assert any(
                    e.type == "process.step"
                    and e.data.kind == "assessment"
                    and e.data.status == "failed"
                    and e.data.outcome == "PROVIDER_INVALID_RESPONSE"
                    for e in events
                )
                assert events[-1].data.error_code == "PROVIDER_INVALID_RESPONSE"
        finally:
            w.pool.shutdown()

    asyncio.run(database_call(scenario))
    response = client.get(f"/v1/threads/{tid}/runs/{rid}", headers=headers)
    assert response.status_code == 200
    assert response.json()["error_code"] == (None if recover else "PROVIDER_INVALID_RESPONSE")
    records = []
    for path in Settings().diagnostic_log_dir.glob("execution-*.jsonl"):
        content = path.read_text(encoding="utf-8")
        assert "not-a-status" not in content
        records.extend(json.loads(line) for line in content.splitlines())
    owned = [row for row in records if row.get("run_id") == str(rid)]
    assert any(row["event"] == "role_response_repair_started" for row in owned)
    if not recover:
        terminal = next(row for row in owned if row["event"] == "execution_failure_handled")
        assert terminal["code"] == "PROVIDER_INVALID_RESPONSE"
        assert terminal["reason"] == "schema_validation_failed"
        assert terminal["issues"][0]["field"] == "status"
