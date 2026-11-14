"""B 包沿原 Run/发布目录验收；隔离 PG，模型和向量受控。"""

import asyncio
import json
import os
from uuid import UUID, uuid4

import pytest
from execution_helpers import FakeProvider
from test_cancellation_pg import cancel
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
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Explicit temporary PostgreSQL required"
)


@pytest.mark.parametrize("mode", ["grade", "rewrite", "plan", "cancel"])
def test_v3_graph_keeps_original_question_private_models_and_run_lifecycle(
    client, tmp_path, model_configuration, monkeypatch, mode
):
    monkeypatch.setenv("MINIMEW_REASONING_ENABLED", "true")
    asyncio.run(configure_model("reasoning-" + uuid4().hex, model_configuration))
    headers, owner = account(client)
    upload(client, headers, content="# 制度\n餐费150元。\n住宿350元。".encode())
    tid = thread(client, headers)
    original = "餐费住宿适用条件是什么？"
    rid = UUID(submit(client, headers, str(tid), content=original).json()["id"])

    async def scenario(sessions):
        w = worker(sessions, tmp_path)
        entered, closed = asyncio.Event(), asyncio.Event()
        calls, grades, results = [], [], []

        async def answer(request):
            calls.append(request.purpose)
            assert request.snapshot.schema_version == 3
            if request.purpose != "answer":
                assert not request.tools
                data = json.loads(request.messages[-1].content)
                if request.purpose == "grade":
                    grades.append(data)
                    entered.set()
                    if mode == "cancel":
                        try:
                            await asyncio.Event().wait()
                        finally:
                            closed.set()
                    first = len(grades) == 1 and mode in {"rewrite", "plan"}
                    output = {
                        "status": "insufficient" if first else "sufficient",
                        "supported": [] if first else [data["materials"][0]["citation_id"]],
                        "missing": ["住宿"] if first else [],
                        "decompose": first and mode == "plan",
                    }
                elif request.purpose == "plan":
                    output = {"questions": ["餐费", "住宿"]}
                else:
                    output = {"query": "餐费住宿适用条件"}
                yield ProviderDelta("PRIVATE_INTERNAL_MODEL_TEXT")
                yield ProviderCompleted(ModelTurn("text", json.dumps(output)))
            elif request.messages[-1].role != "tool":
                yield tool_turn()
            else:
                result = json.loads(request.messages[-1].content)
                results.append(result)
                match = result["matches"][0]
                yield ProviderCompleted(
                    ModelTurn("text", f"## 文档依据\n{match['text']}[{match['citation_id']}]")
                )

        try:
            assert await w.run_once()
            repo = ExecutionRepository(sessions)
            async with sessions() as session:
                run = await session.get(Run, rid)
                frozen = json.dumps(run.model_snapshot_json, sort_keys=True)
                assert run.model_snapshot_json["reasoning"]["model_limit"] == 12
            executor = RunExecutor(
                repo,
                FakeProvider(answer),
                cancellation_poll=0.01,
                registry=ToolRegistry((KnowledgeService(sessions, Runtime(w.backend)).spec(),)),
                capabilities=frozenset({CAPABILITY}),
            )
            task = asyncio.create_task(executor.execute(owner, tid, rid))
            if mode == "cancel":
                await asyncio.wait_for(entered.wait(), 3)
                assert cancel(client, headers, tid, rid).status_code == 202
                assert await asyncio.wait_for(task, 3) == "cancelled"
                assert closed.is_set() and not results
            else:
                assert await asyncio.wait_for(task, 10) == "succeeded"
                assert results[0]["assessment"]["status"] == "sufficient"
                assert grades[0]["question"] == grades[-1]["question"] == original
                assert len(calls) <= 12 and calls.count("plan") <= 1 and calls.count("rewrite") <= 1
            saved = await facts(repo, rid)
            assert len(saved["messages"]) == 2
            assert "PRIVATE_INTERNAL_MODEL_TEXT" not in str(saved)
            assert not any(e.type == "message.delta" for e in saved["events"])
            assert (
                len(
                    [
                        e
                        for e in saved["events"]
                        if e.type.startswith("run.")
                        and e.type in {"run.completed", "run.cancelled", "run.failed"}
                    ]
                )
                == 1
            )
            async with sessions() as session:
                run = await session.get(Run, rid)
                assert json.dumps(run.model_snapshot_json, sort_keys=True) == frozen
        finally:
            await asyncio.to_thread(w.pool.shutdown)

    asyncio.run(database_call(scenario))
