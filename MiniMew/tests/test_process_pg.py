"""正式Journal/owner/Outbox与同Run暂停恢复的公开过程验收。"""

import asyncio
import os
from dataclasses import replace
from uuid import UUID

import pytest
from execution_helpers import activate_claim
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from test_documents_pg import account, database_call, upload
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
        details = []

        async def sink(data):
            assert await repo.record_process(claim, data)
            assert await repo.record_process(claim, data)  # unknown acknowledgement
            events.append(data)

        async def detail_sink(data):
            assert await repo.record_process(claim, data, detail=True)
            assert await repo.record_process(claim, data, detail=True)
            details.append(data)

        async with ProcessReporter(rid, sink, detail_sink).step(
            "model", "model/1", purpose="model_decision", attempt=1
        ) as step:
            step.finish(summary="模型请求结束")
        assert (
            await repo.record_process(replace(claim, owner_token=UUID(int=0)), events[0]) is False
        )
        with pytest.raises(ValueError):
            await repo.record_process(claim, {**events[-1], "summary": "different"})
        assert not await repo.record_process(
            replace(claim, owner_token=UUID(int=0)), details[0], detail=True
        )
        with pytest.raises(ValueError):
            await repo.record_process(claim, {**details[-1], "attempt": 2}, detail=True)
        async with sessions() as session:
            rows = (
                await session.scalars(
                    select(RunEventRecord).where(
                        RunEventRecord.run_id == rid,
                        RunEventRecord.type.in_(["process.step", "process.detail"]),
                    )
                )
            ).all()
            assert len(rows) == 4
            pending = (
                await session.scalars(
                    select(EventOutbox).where(EventOutbox.event_id.in_([r.event_id for r in rows]))
                )
            ).all()
            assert len(pending) == 4
        await repo.finish(claim, content="done")
        assert await repo.record_process(claim, events[0]) is False
        assert await repo.record_process(claim, details[0], detail=True) is False

    asyncio.run(database_call(scenario))


def test_detail_document_source_requires_same_user_project_and_real_version(
    client, model_configuration
):
    from test_projects_pg import project, project_upload

    asyncio.run(configure_model("process-source", model_configuration))
    headers, owner, tid, rid = create_run(client)
    personal = upload(client, headers).json()
    other_headers, _ = account(client)
    foreign = upload(client, other_headers).json()
    project_id = project(client, headers)
    project_document = project_upload(client, headers, project_id).json()

    def source(value):
        return dict(
            document_id=UUID(value["document"]["id"]),
            version_id=UUID(value["version"]["id"]),
            name="a.md",
            version_number=value["version"]["version_number"],
            sections=["准备工作"],
        )

    async def scenario(sessions):
        repo = ExecutionRepository(sessions)
        claim = await repo.claim(owner, tid, rid)
        await activate_claim(repo, claim)

        async def sink(data):
            assert await repo.record_process(claim, data)

        async with ProcessReporter(rid, sink).step("retrieval", "initial") as step:
            data = dict(
                step_id=step.data["step_id"],
                stage="result",
                phase="initial",
                purpose="",
                attempt=0,
                missing=[],
                questions=[],
                sources=[],
            )
            for invalid in (
                source(foreign),
                source(project_document),
                {**source(personal), "version_number": 99},
            ):
                with pytest.raises(IntegrityError):
                    await repo.record_process(claim, {**data, "sources": [invalid]}, detail=True)
            with pytest.raises(IntegrityError):
                await repo.record_process(claim, {**data, "step_id": "0" * 32}, detail=True)
            assert await repo.record_process(
                claim, {**data, "sources": [source(personal)]}, detail=True
            )
        await repo.finish(claim, content="done")

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
