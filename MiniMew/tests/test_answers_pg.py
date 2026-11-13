"""答案依据走原终结事务；隔离PostgreSQL、受控工具/Provider，无外网。"""

import asyncio
import os
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from execution_helpers import FakeProvider, repository
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from test_documents_pg import account as document_account
from test_documents_pg import client as document_client
from test_documents_pg import database_call, upload
from test_evidence import evidence, mixed
from test_execution_pg import create_run, facts
from test_indexing_pg import worker
from test_knowledge import Runtime
from test_knowledge_pg import tool_turn
from test_runs_pg import account, configure_model, submit

import minimew.execution.repository as storage
from minimew.evidence import parse_answer
from minimew.execution.messages import ModelTurn
from minimew.execution.provider import ProviderCompleted
from minimew.execution.repository import ExecutionRepository
from minimew.execution.service import RunExecutor
from minimew.execution.tools import ToolRegistry
from minimew.knowledge import CAPABILITY, KnowledgeService
from minimew.models import AnswerRecord, Document, Run

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Explicit temporary PostgreSQL required"
)


@pytest.fixture
def client(monkeypatch, tmp_path, model_configuration):
    for browser in document_client.__wrapped__(monkeypatch, tmp_path):
        asyncio.run(configure_model("answer-fixture-" + uuid4().hex, model_configuration))
        yield browser


async def record_count(repo, rid):
    async with repo.sessions() as session:
        return await session.scalar(
            select(func.count()).select_from(AnswerRecord).where(AnswerRecord.run_id == rid)
        )


def test_empty_pending_plain_and_legacy_answer_materials_are_explicit(client):
    headers, owner, tid, rid = create_run(client)
    path = f"/v1/threads/{tid}/runs/{rid}/answer"
    response = client.get(path, headers=headers)
    assert response.status_code == 200 and response.json()["materials"] is None
    assert response.json()["status"] == "pending"

    async def scenario():
        async with repository() as repo:
            assert await RunExecutor(repo, FakeProvider()).execute(owner, tid, rid) == "succeeded"

    asyncio.run(scenario())
    response = client.get(path, headers=headers)
    assert response.json()["status"] == "succeeded" and response.json()["materials"] is None
    assert response.headers["cache-control"] == "no-store"
    assert client.get(path).status_code == 401
    other_headers, other_thread = account(client)
    assert client.get(path, headers=other_headers).status_code == 404
    assert (
        client.get(f"/v1/threads/{other_thread}/runs/{rid}/answer", headers=headers).status_code
        == 404
    )


def test_real_publication_answer_survives_new_version_and_resources_closing(
    client, tmp_path, model_configuration, monkeypatch
):
    import json

    asyncio.run(configure_model("answers-" + uuid4().hex, model_configuration))
    headers, owner = document_account(client)
    first = upload(client, headers, content="# 制度\n餐费150元。".encode()).json()
    tid = UUID(client.post("/v1/threads", headers=headers, json={}).json()["id"])
    rid = UUID(submit(client, headers, str(tid)).json()["id"])

    async def scenario(sessions):
        w = worker(sessions, tmp_path)
        try:
            assert await w.run_once()
            service = KnowledgeService(sessions, Runtime(w.backend))

            async def respond(data):
                if data.messages[-1].role != "tool":
                    yield tool_turn()
                else:
                    result = json.loads(data.messages[-1].content)
                    assert result["matches"][0]["version_id"] == first["version"]["id"]
                    yield ProviderCompleted(ModelTurn("text", mixed()))

            repo = ExecutionRepository(sessions)
            assert (
                await RunExecutor(
                    repo,
                    FakeProvider(respond),
                    registry=ToolRegistry((service.spec(),)),
                    capabilities=frozenset({CAPABILITY}),
                ).execute(owner, tid, rid)
                == "succeeded"
            )
            saved = await facts(repo, rid)
            assert len(saved["messages"]) == 2 and saved["messages"][1][2] == mixed()
            completion = saved["events"][-2]
            assert set(completion.data.model_dump()) == {"message_id", "status", "content"}
            assert await record_count(repo, rid) == 1
            newer = upload(
                client,
                headers,
                document=first["document"]["id"],
                key="v2",
                content="# 制度\n餐费200元。".encode(),
            ).json()
            assert await w.run_once()
            async with sessions() as session:
                doc = await session.get(Document, UUID(first["document"]["id"]))
                assert doc.current_version_id == UUID(newer["version"]["id"])
            return completion.data.message_id
        finally:
            await asyncio.to_thread(w.pool.shutdown)

    mid = asyncio.run(database_call(scenario))
    path = f"/v1/threads/{tid}/runs/{rid}/answer"
    response = client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["message_id"] == str(mid)
    assert data["materials"]["evidence"][0]["version_id"] == first["version"]["id"]
    assert "150" in data["materials"]["evidence"][0]["text"]
    assert [s["kind"] for s in data["materials"]["sections"]] == ["document", "supplement"]
    assert client.get(path, headers=headers).json() == data  # 不依赖已经关闭的Worker/模型资源


def test_materials_message_events_outbox_rollback_then_same_claim_retries_save(client, monkeypatch):
    headers, owner, tid, rid = create_run(client)
    answer = parse_answer(mixed(), (evidence(),))

    async def scenario():
        async with repository() as repo:
            claim = await repo.claim(owner, tid, rid)
            original = storage.append_event

            async def fail(session, run, kind, data):
                if kind == "run.completed":
                    raise SQLAlchemyError("injected terminal failure")
                return await original(session, run, kind, data)

            monkeypatch.setattr(storage, "append_event", fail)
            with pytest.raises(SQLAlchemyError):
                await repo.finish(claim, content=answer.content, materials=answer.materials)
            assert await record_count(repo, rid) == 0
            state = await facts(repo, rid)
            assert state["status"] == "running" and state["messages"][1][1:] == ("pending", "")
            assert [e.type for e in state["events"]] == ["run.created", "run.started"]
            monkeypatch.setattr(storage, "append_event", original)
            assert (
                await repo.finish(claim, content=answer.content, materials=answer.materials)
                == "succeeded"
            )
            assert (
                await repo.finish(claim, content=answer.content, materials=answer.materials)
                is False
            )
            assert await record_count(repo, rid) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["cancel", "expired", "stale_token", "final_fence"])
def test_cancel_or_owner_fencing_never_commits_success_materials(client, monkeypatch, mode):
    _, owner, tid, rid = create_run(client)
    answer = parse_answer(mixed(), (evidence(),))

    async def scenario():
        async with repository(lease_seconds=0.03 if mode == "expired" else 330) as repo:
            claim = await repo.claim(owner, tid, rid)
            if mode == "cancel":
                await repo.request_cancel(owner, tid, rid)
            elif mode == "expired":
                await asyncio.sleep(0.06)
            elif mode == "stale_token":
                claim = replace(claim, owner_token=uuid4())
            else:
                # 注入原终结事务最后的条件写入失败，已经flush的材料也必须回滚。
                original = repo._write_terminal

                async def fenced(session, run, **kwargs):
                    kwargs["conditions"] = [Run.id == uuid4()]
                    await original(session, run, **kwargs)

                monkeypatch.setattr(repo, "_write_terminal", fenced)
            outcome = await repo.finish(claim, content=answer.content, materials=answer.materials)
            assert outcome == ("cancelled" if mode == "cancel" else False)
            assert await record_count(repo, rid) == 0
            saved = await facts(repo, rid)
            assert saved["status"] == ("cancelled" if mode == "cancel" else "running")
            assert "run.completed" not in [e.type for e in saved["events"]]

    asyncio.run(scenario())


def test_two_runs_keep_distinct_e1_and_cross_run_message_fk(client):
    first = create_run(client)
    second = create_run(client)

    async def scenario():
        async with repository() as repo:

            async def complete(identity, text):
                _, owner, tid, rid = identity
                claim = await repo.claim(owner, tid, rid)
                answer = parse_answer(mixed(text), (evidence(text),))
                assert (
                    await repo.finish(claim, content=answer.content, materials=answer.materials)
                    == "succeeded"
                )

            await asyncio.gather(complete(first, "A私有资料"), complete(second, "B私有资料"))
            async with repo.sessions() as session:
                record = await session.get(AnswerRecord, first[3])
                wrong_message = (await facts(repo, second[3]))["messages"][1][0]
                with pytest.raises(IntegrityError):
                    record.message_id = wrong_message
                    await session.commit()
                await session.rollback()

    asyncio.run(scenario())
    for item, text in [(first, "A私有资料"), (second, "B私有资料")]:
        headers, _, tid, rid = item
        response = client.get(f"/v1/threads/{tid}/runs/{rid}/answer", headers=headers).json()
        assert response["materials"]["evidence"][0]["text"] == text
        assert response["materials"]["evidence"][0]["citation_id"] == "E1"


def test_read_failure_is_not_empty_materials(client, monkeypatch):
    headers, owner, tid, rid = create_run(client)
    monkeypatch.setattr(
        "minimew.api.runs.read_answer", AsyncMock(side_effect=SQLAlchemyError("private DSN"))
    )
    response = client.get(f"/v1/threads/{tid}/runs/{rid}/answer", headers=headers)
    assert response.status_code == 503 and response.json() == {
        "detail": {"code": "ANSWER_READ_UNAVAILABLE"}
    }
    assert "private DSN" not in response.text and response.headers["cache-control"] == "no-store"

    async def cleanup():
        async with repository() as repo:
            await repo.request_cancel(owner, tid, rid)

    asyncio.run(cleanup())
