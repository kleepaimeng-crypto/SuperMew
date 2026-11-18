"""临时PG中的最终失败事件与历史投影；不调用外部模型或向量服务。"""

import asyncio
import os
from uuid import UUID

import pytest
from execution_helpers import FakeProvider, activate_claim, repository
from test_execution_pg import client as client
from test_execution_pg import create_run, facts
from test_runs_pg import submit

from minimew.execution.messages import ModelTurn
from minimew.execution.provider import ProviderCompleted
from minimew.execution.service import RunExecutor

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Explicit temporary PostgreSQL required"
)


def test_unretrieved_reference_is_persisted_as_failure_not_a_success(client):
    headers, owner, tid, rid = create_run(client)

    async def scenario():
        async with repository() as repo:

            async def answer(request):
                assert '"knowledge_status": "not_searched"' in request.instructions
                yield ProviderCompleted(ModelTurn("text", "本次查询的结果是150元。[E1]"))

            assert (
                await RunExecutor(repo, FakeProvider(answer)).execute(owner, tid, rid) == "failed"
            )
            saved = await facts(repo, rid)
            assert saved["error"] == "PROVIDER_INVALID_RESPONSE"
            assert saved["messages"][1][1:] == ("failed", "")
            assert [e.type for e in saved["events"]] == [
                "run.created",
                "run.started",
                "message.completed",
                "run.failed",
            ]

    asyncio.run(scenario())
    response = client.get(f"/v1/threads/{tid}/runs/{rid}", headers=headers)
    assert response.status_code == 200
    assert response.json()["error_code"] == "PROVIDER_INVALID_RESPONSE"


def test_history_is_projected_for_next_run_without_rewriting_persisted_message(client):
    headers, owner, tid, first = create_run(client)
    legacy = "## 文档依据\n上次检索得到餐费150元。[E1]"

    async def scenario():
        async with repository() as repo:
            # 模拟修复前已持久化、没有来源绑定的历史回答；不回写历史修补证据。
            claim = await repo.claim(owner, tid, first)
            await activate_claim(repo, claim)
            assert await repo.finish(claim, content=legacy) == "succeeded"
            second = UUID(
                submit(client, headers, str(tid), key="followup", content="解释上条").json()["id"]
            )

            async def answer(request):
                assert request.messages[-1].content == "解释上条"
                history = request.messages[-2].content
                assert "上次检索得到餐费150元。" in history
                assert "[E1]" not in history
                assert "历史助手回答" in history
                assert '"knowledge_status": "not_searched"' in request.instructions
                yield ProviderCompleted(ModelTurn("text", "这是对历史回答的解释，本次未检索。"))

            assert (
                await RunExecutor(repo, FakeProvider(answer)).execute(owner, tid, second)
                == "succeeded"
            )
            assert (await facts(repo, first))["messages"][1][2] == legacy
            assert not any(
                e.type.startswith("tool.") for e in (await facts(repo, second))["events"]
            )

    asyncio.run(scenario())
