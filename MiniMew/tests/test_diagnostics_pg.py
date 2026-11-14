"""真实PG核对日志关联与持久错误契约；仅自有临时库。"""

import asyncio
import os

import pytest
from execution_helpers import FakeProvider, repository
from test_agent import completed
from test_agent_pg import agent
from test_execution_diagnostics import SECRET, records
from test_execution_pg import create_run, facts
from test_runs_pg import client as client
from test_tools import call

from minimew.execution.provider import ProviderFailure
from minimew.execution.service import RunExecutor

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned PG required"
)


@pytest.mark.parametrize("mode", ["provider", "tool"])
def test_diagnostic_stays_server_side_and_history_retains_same_terminal_code(client, caplog, mode):
    headers, owner, thread, rid = create_run(client)
    code = "PROVIDER_TIMEOUT" if mode == "provider" else "TOOL_EXECUTION_FAILED"

    async def handler(context, args):
        raise RuntimeError(SECRET)

    async def answer(data):
        if mode == "provider":
            raise ProviderFailure("PROVIDER_TIMEOUT")
        yield completed(call())

    async def scenario():
        async with repository() as repo:
            executor = (
                RunExecutor(repo, FakeProvider(answer))
                if mode == "provider"
                else agent(repo, FakeProvider(answer), handler)
            )
            assert await executor.execute(owner, thread, rid) == "failed"
            saved = await facts(repo, rid)
            assert saved["error"] == code and len(saved["messages"]) == 2
            terminal = saved["events"][-1]
            assert terminal.type == "run.failed" and terminal.data.error_code == code
            assert set(terminal.data.model_dump()) == {"status", "error_code"}
            if mode == "tool":
                assert [e.type for e in saved["events"]].count("tool.failed") == 1

    asyncio.run(scenario())
    response = client.get(f"/v1/threads/{thread}/runs/{rid}", headers=headers)
    assert response.status_code == 200 and response.json()["error_code"] == code
    entry = next(r for r in records(caplog) if r["event"] == "execution_failure_handled")
    assert entry["run_id"] == str(rid) and entry["thread_id"] == str(thread)
    assert SECRET not in caplog.text and "diagnostic" not in response.text
