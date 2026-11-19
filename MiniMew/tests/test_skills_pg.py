"""Skill预留事务、不可变快照、原执行/HITL链；仅自有临时PG。"""

import asyncio
import json
import os
from uuid import UUID

import pytest
from execution_helpers import FakeProvider
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from test_documents_pg import database_call
from test_execution_pg import create_run
from test_indexing_pg import client as client
from test_runs_pg import configure_model

from minimew.execution.messages import ModelTurn, ToolCall
from minimew.execution.provider import ProviderCompleted
from minimew.execution.repository import ExecutionRepository
from minimew.execution.service import RunExecutor
from minimew.models import Run, RunEventRecord
from minimew.skills.catalog import SkillUnavailable, load_skill
from minimew.skills.schemas import content_digest, parse_snapshot

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned PG required"
)


def test_catalog_selection_replay_conflict_rollback_frozen_version_and_db_guard(
    client, model_configuration, monkeypatch
):
    asyncio.run(configure_model("skill-receipt", model_configuration))
    headers, owner, tid, rid = create_run(client)
    path = f"/v1/threads/{tid}/runs"
    directory = client.get("/v1/skills", headers=headers)
    assert directory.status_code == 200 and directory.headers["cache-control"] == "no-store"
    assert [s["id"] for s in directory.json()] == ["default", "document-qa"]
    assert "instructions" not in directory.text and "resources" not in directory.text
    assert client.get("/v1/skills").status_code == 401
    body = dict(
        idempotency_key="chosen",
        content="资料问答",
        skill={"id": "document-qa", "version": "1.0.0"},
    )
    receipt = client.post(path, headers=headers, json=body)
    assert receipt.status_code == 201 and receipt.json()["skill"]["id"] == "document-qa"
    assert client.post(path, headers=headers, json={**body, "skill": None}).status_code == 409
    assert (
        client.post(
            path,
            headers=headers,
            json={**body, "idempotency_key": "bad", "skill": {"id": "missing"}},
        ).status_code
        == 422
    )

    original = load_skill()
    payload = original.model_dump(mode="json", exclude={"digest"})
    payload["version"] = "2.0.0"
    later = parse_snapshot({**payload, "digest": content_digest(payload)})
    calls = []

    def new_release(selection=None):
        calls.append(selection)
        return later

    monkeypatch.setattr("minimew.conversations.load_skill", new_release)
    replay = client.post(path, headers=headers, json=body)
    assert replay.status_code == 200 and replay.json() == receipt.json()
    assert calls == []
    fresh = client.post(
        path, headers=headers, json={"idempotency_key": "future", "content": "新问题"}
    )
    assert fresh.status_code == 201 and fresh.json()["skill"]["version"] == "2.0.0"
    assert client.get(path + "/" + str(rid), headers=headers).json()["skill"]["version"] == "1.0.0"

    async def scenario(sessions):
        async with sessions() as session:
            run = await session.get(Run, UUID(receipt.json()["id"]))
            assert parse_snapshot(run.skill_snapshot).resources[0].id == "answer-guide"
            assert run.skill_request == body["skill"]
        for changes in (
            {"skill_request": {}},
            {"skill_snapshot": original.model_dump(mode="json")},
        ):
            with pytest.raises(IntegrityError):
                async with sessions() as session, session.begin():
                    await session.execute(update(Run).where(Run.id == run.id).values(**changes))
        async with sessions() as session:
            saved = (await session.scalars(select(Run).where(Run.thread_id == tid))).all()
            assert len(saved) == 3  # 未知包的Run/消息事务没有留下半成品。

    asyncio.run(database_call(scenario))


def test_frozen_resource_uses_formal_executor_tool_journal_and_no_catalog_reload(
    client, model_configuration, monkeypatch
):
    asyncio.run(configure_model("skill-execution", model_configuration))
    headers, owner, tid, first = create_run(client)
    path = f"/v1/threads/{tid}/runs"
    value = client.post(
        path,
        headers=headers,
        json=dict(idempotency_key="resource", content="使用问答规范", skill={"id": "document-qa"}),
    ).json()
    rid = UUID(value["id"])

    async def scenario(sessions):
        repo = ExecutionRepository(sessions)
        await repo.request_cancel(owner, tid, first)
        calls = []

        async def answer(request):
            calls.append(request)
            assert request.skill.id == "document-qa"
            assert "本次任务采用文档问答模式" in request.instructions
            if len(calls) == 1:
                yield ProviderCompleted(
                    ModelTurn(
                        "tool_calls",
                        "",
                        (ToolCall("r1", "read_skill_resource", '{"resource_id":"answer-guide"}'),),
                    )
                )
            else:
                resource = json.loads(request.messages[-1].content)
                assert resource["content"] == request.skill.resources[0].content
                yield ProviderCompleted(ModelTurn("text", "已按参考规范整理回答"))

        monkeypatch.setattr(
            "minimew.skills.catalog.load_skill",
            lambda *args: (_ for _ in ()).throw(SkillUnavailable()),
        )
        assert await RunExecutor(repo, FakeProvider(answer)).execute(owner, tid, rid) == "succeeded"
        assert len(calls) == 2
        async with sessions() as session:
            events = (
                await session.scalars(
                    select(RunEventRecord)
                    .where(RunEventRecord.run_id == rid)
                    .order_by(RunEventRecord.sequence)
                )
            ).all()
            assert [e.type for e in events] == [
                "run.created",
                "run.started",
                "tool.started",
                "tool.completed",
                "message.completed",
                "run.completed",
            ]
            assert events[2].data["tool_name"] == "read_skill_resource"
            assert sum(e.type == "run.completed" for e in events) == 1

    asyncio.run(database_call(scenario))


def test_document_skill_hitl_resumes_frozen_content_even_if_catalog_unavailable(
    client, tmp_path, model_configuration, monkeypatch
):
    import test_hitl_pg

    from minimew.hitl.store import HitlStore

    def submit_selected(client, headers, thread, key="key", content="question"):
        return client.post(
            f"/v1/threads/{thread}/runs",
            headers=headers,
            json=dict(
                idempotency_key=key,
                content=content,
                skill={"id": "document-qa", "version": "1.0.0"} if key != "queued" else None,
            ),
        )

    paused = []

    class FrozenStore(HitlStore):
        async def pause(self, claim, request, *args):
            assert request.skill.id == "document-qa"
            await super().pause(claim, request, *args)
            paused.append(request.skill.digest)

            def unavailable_selected(selection=None):
                if selection is not None:
                    raise SkillUnavailable()
                return load_skill()

            monkeypatch.setattr("minimew.conversations.load_skill", unavailable_selected)

    monkeypatch.setattr(test_hitl_pg, "submit", submit_selected)
    monkeypatch.setattr("minimew.execution.service.HitlStore", FrozenStore)
    test_hitl_pg.test_pause_refresh_and_resume_same_run_tool_message_scope_snapshot_budget(
        client, tmp_path, model_configuration, monkeypatch, "resume"
    )
    assert len(paused) == 1
