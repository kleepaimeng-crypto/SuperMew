"""v4在真实PG中的同Run纠正/失败/取消；模型及向量受控。"""

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
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned PG required"
)


@pytest.mark.parametrize("mode", ["success", "invalid", "cancel"])
def test_v4_repair_keeps_single_message_pair_private_output_frozen_policy_and_cancel(
    client, tmp_path, model_configuration, monkeypatch, mode
):
    monkeypatch.setenv("MINIMEW_REASONING_ENABLED", "true")
    monkeypatch.setenv("MINIMEW_REPAIRS_ENABLED", "true")
    asyncio.run(configure_model("repair-" + uuid4().hex, model_configuration))
    headers, owner = account(client)
    upload(client, headers, content="# 制度\n餐费最高150元，须有票据。".encode())
    tid = thread(client, headers)
    rid = UUID(submit(client, headers, str(tid), content="餐费是多少？").json()["id"])

    async def scenario(sessions):
        w = worker(sessions, tmp_path)
        entered, closed = asyncio.Event(), asyncio.Event()
        roles = []

        async def answer(request):
            roles.append(request.purpose)
            assert request.snapshot.schema_version == 4
            if request.purpose == "grade":
                data = json.loads(request.messages[-1].content)
                yield ProviderCompleted(
                    ModelTurn(
                        "text",
                        json.dumps(
                            {
                                "status": "sufficient",
                                "supported": [m["citation_id"] for m in data["materials"]],
                                "missing": [],
                            }
                        ),
                    )
                )
            elif request.purpose == "repair":
                assert not request.tools and request.private_output
                data = json.loads(request.messages[-1].content)
                assert "根据资料，以下为说明。\n\n" in data["original_answer"]
                entered.set()
                try:
                    yield ProviderDelta("PRIVATE_REPAIR_TEXT")
                    if mode == "cancel":
                        await asyncio.Event().wait()
                    source = data["evidence"][0]
                    text = (
                        data["original_answer"]
                        if mode == "invalid"
                        else (
                            "## 文档依据\n根据资料，以下为说明。"
                            f"{source['text']}[{source['citation_id']}]"
                        )
                    )
                    yield ProviderCompleted(ModelTurn("text", text))
                finally:
                    closed.set()
            elif request.messages[-1].role != "tool":
                yield tool_turn()
            else:
                source = json.loads(request.messages[-1].content)["matches"][0]
                yield ProviderCompleted(
                    ModelTurn(
                        "text",
                        "## 文档依据\n根据资料，以下为说明。\n\n"
                        f"{source['text']}[{source['citation_id']}]",
                    )
                )

        try:
            assert await w.run_once()
            repo = ExecutionRepository(sessions)
            before = await facts(repo, rid)
            async with sessions() as session:
                row = await session.get(Run, rid)
                snapshot, digest = row.model_snapshot_json, row.model_catalog_hash
                assert snapshot["repairs"]["answer_repair_limit"] == 1
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
            assert (
                await asyncio.wait_for(task, 5)
                == {"success": "succeeded", "invalid": "failed", "cancel": "cancelled"}[mode]
            )
            assert closed.is_set() and roles == ["answer", "grade", "answer", "repair"]
            saved = await facts(repo, rid)
            assert [m[0] for m in saved["messages"]] == [m[0] for m in before["messages"]]
            assert "PRIVATE_REPAIR_TEXT" not in str(saved)
            assert all("根据资料，以下为说明。\n\n" not in m[2] for m in saved["messages"])
            assert not any(e.type == "message.delta" for e in saved["events"])
            if mode == "success":
                result = client.get(f"/v1/threads/{tid}/runs/{rid}/answer", headers=headers)
                assert (
                    result.status_code == 200
                    and result.json()["materials"]["citations"][0]["citation_id"] == "E1"
                )
            else:
                assert saved["messages"][1][2] == ""
                assert saved["error"] == (
                    "PROVIDER_INVALID_RESPONSE" if mode == "invalid" else None
                )
            async with sessions() as session:
                row = await session.get(Run, rid)
                assert row.model_snapshot_json == snapshot and row.model_catalog_hash == digest
        finally:
            await asyncio.to_thread(w.pool.shutdown)

    asyncio.run(database_call(scenario))
