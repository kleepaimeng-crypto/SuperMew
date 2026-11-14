"""正式索引/Run链与真实 PG；模型、向量受控，无收费或隐式网络。"""

import asyncio
import json
import os
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from execution_helpers import FakeProvider
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from test_documents_pg import account, database_call, upload
from test_execution_pg import facts as journal_facts
from test_indexing_pg import client as client
from test_indexing_pg import worker
from test_knowledge import Runtime
from test_knowledge_pg import thread, tool_turn
from test_runs_pg import configure_model, submit

from minimew.execution.messages import ModelTurn
from minimew.execution.provider import ProviderCompleted
from minimew.execution.repository import Claim, ExecutionRepository, OwnershipLost
from minimew.execution.service import RunExecutor
from minimew.execution.tools import ToolRegistry
from minimew.hitl.checkpoint import decode
from minimew.hitl.schemas import ResumeConflict, ResumeInput
from minimew.hitl.store import HitlStore
from minimew.knowledge import CAPABILITY, KnowledgeService
from minimew.models import Message, Run, RunCheckpoint


async def facts(repo, rid):
    saved = await journal_facts(repo, rid)
    async with repo.sessions() as session:
        saved["run"] = await session.get(Run, rid)
        saved["messages"] = (
            await session.scalars(
                select(Message).where(Message.run_id == rid).order_by(Message.sequence)
            )
        ).all()
    return saved


pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Explicit temporary PostgreSQL required"
)


@pytest.mark.parametrize(
    "mode",
    [
        "resume",
        "cancel",
        "rollback",
        "duplicate_race",
        "cancel_race",
        "expire",
        "accepted_expire",
        "freeze",
        "one_clarification",
        "scope_limit",
        "resume_cancel",
        "db_guard",
        "receipt_hash_null",
        "receipt_answer_null",
        "pause_ack",
        "accept_ack",
        "accept_rollback",
    ],
)
def test_pause_refresh_and_resume_same_run_tool_message_scope_snapshot_budget(
    client, tmp_path, model_configuration, monkeypatch, mode
):
    for key in ["REASONING", "REPAIRS", "HITL"]:
        monkeypatch.setenv(f"MINIMEW_{key}_ENABLED", "true")
    asyncio.run(configure_model("hitl-" + uuid4().hex, model_configuration))
    headers, owner = account(client)
    upload(client, headers, content="# 差旅\n国内餐费150元。\n海外餐费按当地标准。".encode())
    tid = thread(client, headers)
    created = submit(client, headers, str(tid), content="培训餐费多少？").json()
    rid = UUID(created["id"])
    path = f"/v1/threads/{tid}/runs/{rid}"

    async def scenario(sessions):
        w = worker(sessions, tmp_path)
        stages, grades = [], []
        old_claims = []
        deadlines = []

        async def answer(request):
            stages.append(request.purpose)
            assert request.snapshot.schema_version == 5
            assert (
                request.snapshot.answer.configuration.model_name == model_configuration.model_name
            )
            if request.purpose == "grade":
                data = json.loads(request.messages[-1].content)
                grades.append(data)
                ambiguous = len(grades) == 1 or mode == "one_clarification"
                output = {
                    "status": "ambiguous" if ambiguous else "sufficient",
                    "supported": [data["materials"][0]["citation_id"]],
                    "missing": ["国内还是海外？"] if ambiguous else [],
                }
                yield ProviderCompleted(ModelTurn("text", json.dumps(output)))
            elif request.messages[-1].role != "tool":
                async with sessions() as session:
                    r = await session.get(Run, rid)
                    old_claims.append(Claim(owner, tid, rid, r.owner_token, r.lease_until))
                yield tool_turn()
            else:
                result = json.loads(request.messages[-1].content)
                assert result["clarification"] == "国内"
                match = result["matches"][0]
                yield ProviderCompleted(
                    ModelTurn("text", f"## 文档依据\n{match['text']}[{match['citation_id']}]")
                )

        def executor():
            class ObservedExecutor(RunExecutor):
                async def _watched_generate(self, claim, request, deadline, context):
                    deadlines.append(deadline - asyncio.get_running_loop().time())
                    return await super()._watched_generate(claim, request, deadline, context)

            return ObservedExecutor(
                ExecutionRepository(sessions),
                FakeProvider(answer),
                registry=ToolRegistry((KnowledgeService(sessions, Runtime(w.backend)).spec(),)),
                capabilities=frozenset({CAPABILITY}),
            )

        try:
            assert await w.run_once()
            repo = ExecutionRepository(sessions)
            if mode == "db_guard":
                claim = await repo.claim(owner, tid, rid)
                with pytest.raises(IntegrityError):
                    async with sessions() as session, session.begin():
                        run = await session.get(Run, rid)
                        (
                            await session.scalars(
                                select(Message).where(
                                    Message.run_id == rid, Message.role == "assistant"
                                )
                            )
                        ).one().status = "waiting_input"
                        run.status, run.owner_token, run.lease_until = "waiting_input", None, None
                assert (await facts(repo, rid))["status"] == "running"
                await repo.finish(claim, error_code="EXECUTOR_INTERRUPTED")
                return
            if mode in {"expire", "accepted_expire", "scope_limit"}:
                import minimew.knowledge as knowledge

                original_scope = knowledge.published_scope

                async def short_scope(*args):
                    scope = await original_scope(*args)
                    async with sessions() as session:
                        now = await session.scalar(select(func.clock_timestamp()))
                    seconds = (
                        1 if mode == "scope_limit" else model_configuration.timeout_seconds + 6
                    )
                    return replace(scope, expires_at=now + timedelta(seconds=seconds))

                monkeypatch.setattr(knowledge, "published_scope", short_scope)
            if mode == "rollback":
                import minimew.hitl.store as store

                original = store.append_event

                async def broken(session, run, kind, data):
                    await original(session, run, kind, data)
                    if kind == "hitl.required":
                        raise OSError("controlled pause write failure")

                monkeypatch.setattr(store, "append_event", broken)
                with pytest.raises(OSError):
                    await executor().execute(owner, tid, rid)
                async with sessions() as session:
                    assert (
                        await session.scalar(
                            select(RunCheckpoint).where(RunCheckpoint.run_id == rid)
                        )
                        is None
                    )
                saved = await facts(repo, rid)
                assert saved["run"].status == "running"
                assert not any(e.type == "hitl.required" for e in saved["events"])
                return
            if mode == "scope_limit":
                assert await executor().execute(owner, tid, rid) == "failed"
                saved = await facts(repo, rid)
                assert saved["error"] == "KNOWLEDGE_SCOPE_INVALID"
                assert not any(e.type == "hitl.required" for e in saved["events"])
                return
            if mode == "pause_ack":
                original_pause = HitlStore.pause

                async def committed_pause(self, *args, **kwargs):
                    await original_pause(self, *args, **kwargs)
                    raise OSError("controlled pause acknowledgement loss")

                with monkeypatch.context() as fault:
                    fault.setattr(HitlStore, "pause", committed_pause)
                    with pytest.raises(OSError, match="acknowledgement"):
                        await executor().execute(owner, tid, rid)
                assert await executor().execute(owner, tid, rid) == "not_claimed"
            else:
                assert await executor().execute(owner, tid, rid) == "waiting_input"
            saved = await facts(repo, rid)
            assert saved["run"].status == "waiting_input"
            assert saved["run"].owner_token is None and saved["run"].lease_until is None
            assert len(saved["messages"]) == 2 and saved["messages"][1].status == "waiting_input"
            assert not any(e.type == "message.completed" for e in saved["events"])
            async with sessions() as session:
                cp = await session.scalar(select(RunCheckpoint).where(RunCheckpoint.run_id == rid))
                checkpoint_id, original_doc = cp.id, cp.document
                snapshot_hash = saved["run"].model_catalog_hash
                assert cp.token_hash is None
                state = decode(cp.document["state"])
                assert state["budget"]["model_requests"] == 1
                assert state["budget"]["internal_requests"] == 1
                assert state["budget"]["tool_calls"] == 1
                assert state["budget"]["retrieval_queries"] == 1
                assert state["budget"]["clarification_used"] is True
                assert 0 < state["remaining"] < model_configuration.timeout_seconds
                expires = cp.expires_at
            if mode == "resume":
                with pytest.raises(IntegrityError):
                    async with sessions() as session, session.begin():
                        await session.execute(
                            update(RunCheckpoint)
                            .where(RunCheckpoint.run_id == rid)
                            .values(document={"version": 2})
                        )
            if mode in {"receipt_hash_null", "receipt_answer_null"}:
                with pytest.raises(IntegrityError) as rejected:
                    async with sessions() as session, session.begin():
                        await session.execute(
                            update(RunCheckpoint)
                            .where(RunCheckpoint.run_id == rid)
                            .values(
                                accepted_at=func.clock_timestamp(),
                                resume_key="forged",
                                input_hash=None if mode == "receipt_hash_null" else "0" * 64,
                                answer=None if mode == "receipt_answer_null" else "国内",
                            )
                        )
                assert "ck_checkpoint_receipt" in str(rejected.value.orig)
                assert (await facts(repo, rid))["status"] == "waiting_input"
            with pytest.raises(OwnershipLost):
                await repo.check_control(old_claims[0])
            assert await repo.finish(old_claims[0], content="stale") is False
            assert (
                await repo.record_tool(
                    old_claims[0],
                    "tool.completed",
                    {"tool_call_id": "call_1", "tool_name": "knowledge_search"},
                )
                is False
            )
            queued = UUID(
                submit(client, headers, str(tid), key="queued", content="后续问题").json()["id"]
            )
            assert await repo.claim(owner, tid, queued) is None
            # 新 HTTP 查询/事件重放可重建等待；凭据只经认证 POST 返回。
            assert client.get(path, headers=headers).json()["status"] == "waiting_input"
            page = client.get(path + "/events", headers=headers).json()
            assert page["items"][-1]["type"] == "hitl.required"
            credential = client.post(path + "/resume-credential", headers=headers).json()
            assert credential["checkpoint_id"] == str(checkpoint_id)
            body = {
                "checkpoint_id": str(checkpoint_id),
                "token": credential["token"],
                "idempotency_key": "resume-one",
                "answer": "国内",
            }
            # 轮换后旧凭据失效；非所有者无法读取或恢复。
            fresh = client.post(path + "/resume-credential", headers=headers).json()
            assert client.post(path + "/resume", headers=headers, json=body).status_code == 409
            body["token"] = fresh["token"]

            async def forbidden_scope(*args):
                raise AssertionError("resume cannot reacquire published scope")

            monkeypatch.setattr("minimew.knowledge.published_scope", forbidden_scope)
            other, _ = account(client)
            assert client.post(path + "/resume-credential", headers=other).status_code == 404
            assert client.post(path + "/resume", headers=other, json=body).status_code == 404
            if mode == "cancel":
                assert (
                    client.post(path + "/cancel", headers=headers).json()["status"] == "cancelled"
                )
                assert client.post(path + "/resume", headers=headers, json=body).status_code == 409
                assert await repo.claim(owner, tid, rid) is None
                return
            if mode == "freeze":
                async with sessions() as session:
                    from minimew.model_control import set_current_model

                    await set_current_model(
                        session,
                        "changed-" + uuid4().hex,
                        model_configuration.model_copy(update={"model_name": "future-only-model"}),
                    )
            if mode in {"duplicate_race", "cancel_race"}:
                barrier = asyncio.Barrier(3)
                store = HitlStore(repo)

                async def accept():
                    await barrier.wait()
                    try:
                        return await store.accept(owner, tid, rid, ResumeInput(**body))
                    except ResumeConflict:
                        return None

                async def competing():
                    await barrier.wait()
                    if mode == "duplicate_race":
                        return await store.accept(owner, tid, rid, ResumeInput(**body))
                    return await repo.request_cancel(owner, tid, rid)

                tasks = [asyncio.create_task(accept()), asyncio.create_task(competing())]
                await barrier.wait()
                await asyncio.gather(*tasks)
                if mode == "cancel_race":
                    saved = await facts(repo, rid)
                    assert saved["status"] == "cancelled"
                    assert sum(e.type == "run.cancelled" for e in saved["events"]) == 1
                    assert await executor().execute(owner, tid, rid) == "not_claimed"
                    return
            if mode == "accept_ack":
                original_accept = HitlStore.accept

                async def committed_accept(self, *args, **kwargs):
                    await original_accept(self, *args, **kwargs)
                    raise OSError("controlled accept acknowledgement loss")

                with monkeypatch.context() as fault:
                    fault.setattr(HitlStore, "accept", committed_accept)
                    assert (
                        client.post(path + "/resume", headers=headers, json=body).status_code == 503
                    )
                assert (await facts(repo, rid))["status"] == "resume_pending"
            if mode == "accept_rollback":
                import minimew.hitl.store as store_module

                original_event = store_module.append_event

                async def rolled_back_accept(session, run, kind, data):
                    await original_event(session, run, kind, data)
                    if kind == "hitl.accepted":
                        raise OSError("controlled accept rollback")

                with monkeypatch.context() as fault:
                    fault.setattr(store_module, "append_event", rolled_back_accept)
                    assert (
                        client.post(path + "/resume", headers=headers, json=body).status_code == 503
                    )
                assert (await facts(repo, rid))["status"] == "waiting_input"
            if mode != "expire":
                assert (
                    client.post(path + "/resume", headers=headers, json=body).json()["status"]
                    == "resume_pending"
                )
            if mode in {"expire", "accepted_expire"}:
                async with asyncio.timeout(10):
                    while True:
                        async with sessions() as session:
                            now = await session.scalar(select(func.clock_timestamp()))
                        if now >= expires:
                            break
                        await asyncio.sleep(0.01)  # 等业务期限；并发竞争由上方屏障控制。
                if mode == "expire":
                    assert (
                        client.post(path + "/resume", headers=headers, json=body).status_code == 409
                    )
                    await HitlStore(repo).expire_waiting(100)
                else:
                    assert await executor().execute(owner, tid, rid) == "failed"
                saved = await facts(repo, rid)
                assert saved["error"] == "HITL_EXPIRED" and saved["status"] == "failed"
                assert sum(e.type == "run.failed" for e in saved["events"]) == 1
                return
            if mode == "resume_cancel":
                assert (
                    client.post(path + "/cancel", headers=headers).json()["status"] == "cancelled"
                )
                assert await executor().execute(owner, tid, rid) == "not_claimed"
                return
            assert client.post(path + "/resume", headers=headers, json=body).status_code == 202
            assert (
                client.post(
                    path + "/resume", headers=headers, json={**body, "answer": "海外"}
                ).status_code
                == 409
            )
            # 新 executor/registry/graph 实例，续接信息全部从 PG 读取。
            assert await executor().execute(owner, tid, rid) == "succeeded"
            assert 0 < deadlines[1] <= state["remaining"] < deadlines[0]
            saved = await facts(repo, rid)
            assert saved["run"].model_catalog_hash == snapshot_hash
            assert len(saved["messages"]) == 2
            assert str(saved["messages"][1].id) == created["assistant_message_id"]
            assert stages == ["answer", "grade", "grade", "answer"]
            assert len(grades) == 2 and grades[1]["aspects"][0]["clarification"] == "国内"
            assert [e.type for e in saved["events"]] == [
                "run.created",
                "run.started",
                "tool.started",
                "hitl.required",
                "hitl.accepted",
                "run.resumed",
                "tool.completed",
                "message.completed",
                "run.completed",
            ]
            assert (
                len({e.data.tool_call_id for e in saved["events"] if e.type.startswith("tool.")})
                == 1
            )
            async with sessions() as session:
                cp = await session.scalar(select(RunCheckpoint).where(RunCheckpoint.run_id == rid))
                assert cp.document == original_doc and cp.token_hash is None
                assert fresh["token"] not in json.dumps(cp.document)
                assert cp.answer == "国内"
            assert fresh["token"] not in str(page)
            assert await repo.claim(owner, tid, queued) is not None
        finally:
            await asyncio.to_thread(w.pool.shutdown)

    asyncio.run(database_call(scenario))
