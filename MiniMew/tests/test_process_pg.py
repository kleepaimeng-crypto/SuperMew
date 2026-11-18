"""正式Journal/owner/Outbox与同Run暂停恢复的公开过程验收。"""

import asyncio
import os
from dataclasses import replace
from uuid import UUID

import pytest
from execution_helpers import activate_claim
from sqlalchemy import select
from test_documents_pg import database_call
from test_execution_pg import create_run
from test_indexing_pg import client as client
from test_runs_pg import configure_model

from minimew.execution.process import ProcessReporter
from minimew.execution.repository import ExecutionRepository
from minimew.models import EventOutbox, RunEventRecord

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned PG required"
)


def test_process_persistence_retry_outbox_fencing_and_downgrade_guard(client, model_configuration):
    asyncio.run(configure_model("process-test", model_configuration))
    headers, owner, tid, rid = create_run(client)

    async def scenario(sessions):
        repo = ExecutionRepository(sessions)
        claim = await repo.claim(owner, tid, rid)
        await activate_claim(repo, claim)
        events = []

        async def sink(data):
            assert await repo.record_process(claim, data)
            assert await repo.record_process(claim, data)  # unknown acknowledgement
            events.append(data)

        async with ProcessReporter(rid, sink).step("model", "model/1") as step:
            step.finish(summary="模型请求结束")
        assert (
            await repo.record_process(replace(claim, owner_token=UUID(int=0)), events[0]) is False
        )
        with pytest.raises(ValueError):
            await repo.record_process(claim, {**events[-1], "summary": "different"})
        async with sessions() as session:
            rows = (
                await session.scalars(
                    select(RunEventRecord).where(
                        RunEventRecord.run_id == rid, RunEventRecord.type == "process.step"
                    )
                )
            ).all()
            assert len(rows) == 2
            pending = (
                await session.scalars(
                    select(EventOutbox).where(EventOutbox.event_id.in_([r.event_id for r in rows]))
                )
            ).all()
            assert len(pending) == 2
        await repo.finish(claim, content="done")
        assert await repo.record_process(claim, events[0]) is False

    asyncio.run(database_call(scenario))


def test_process_enabled_original_hitl_resume(client, tmp_path, model_configuration, monkeypatch):
    from test_hitl_pg import (
        test_pause_refresh_and_resume_same_run_tool_message_scope_snapshot_budget,
    )

    monkeypatch.setattr("minimew.execution.service.ProcessReporter", ProcessReporter)
    test_pause_refresh_and_resume_same_run_tool_message_scope_snapshot_budget(
        client, tmp_path, model_configuration, monkeypatch, "resume"
    )

    async def verify(sessions):
        async with sessions() as session:
            rows = (
                await session.scalars(
                    select(RunEventRecord)
                    .where(RunEventRecord.type == "process.step")
                    .order_by(RunEventRecord.run_id, RunEventRecord.sequence)
                )
            ).all()
            assert any(
                r.data["query"].endswith("用户补充：国内")
                for r in rows
                if r.data["kind"] == "retrieval"
            )
            by = {}
            for row in rows:
                key = (row.run_id, row.data["step_id"])
                by.setdefault(key, []).append(row.data["status"])
            assert all(v == ["running", "completed"] for v in by.values())

    asyncio.run(database_call(verify))
