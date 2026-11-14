"""SSE 复用的事件读取边界：快照口径和 ORM 缓存不成为第二份事实。"""

import asyncio
import os

import pytest
from execution_helpers import activate_claim, repository
from test_execution_pg import create_run
from test_runs_pg import client as client

from minimew.execution.queries import events_after
from minimew.models import Run

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Explicit temporary PostgreSQL required"
)


def test_read_bounds_page_to_same_run_snapshot_during_concurrent_append(client, monkeypatch):
    _, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository() as repo:
            claim = await repo.claim(owner, thread, run_id)
            await activate_claim(repo, claim)
            async with repo.sessions() as session:
                original = session.scalar

                async def append_after_run_read(statement, *args, **kwargs):
                    result = await original(statement, *args, **kwargs)
                    if statement.column_descriptions[0].get("entity") is Run:
                        assert await repo.append_delta(claim, "下一次读取才可见")
                    return result

                with monkeypatch.context() as patch:
                    patch.setattr(session, "scalar", append_after_run_read)
                    first = await events_after(session, owner, thread, run_id)
                assert first.status == "running" and first.last_sequence == 2
                assert [e.sequence for e in first.page.items] == [1, 2]
                next_read = await events_after(session, owner, thread, run_id, after=2)
                assert next_read.last_sequence == 3
                assert [e.sequence for e in next_read.page.items] == [3]
            assert await repo.finish(claim, error_code="PROVIDER_ERROR")

    asyncio.run(scenario())


def test_read_refreshes_retained_orm_identity_and_exposes_true_terminal_tail(client):
    _, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository() as repo, repo.sessions() as session:
            async with session.begin():
                cached = await session.get(Run, run_id)
                assert cached.status == "pending"
            claim = await repo.claim(owner, thread, run_id)
            await activate_claim(repo, claim)
            assert await repo.finish(claim, content="answer")
            result = await events_after(session, owner, thread, run_id, limit=1)
            assert cached.status == "succeeded"
            assert result.terminal and result.last_sequence == 4
            assert len(result.page.items) == 1 and result.page.has_more
            end = await events_after(session, owner, thread, run_id, after=4)
            assert end.terminal and end.last_sequence == 4 and end.page.items == []

    asyncio.run(scenario())
