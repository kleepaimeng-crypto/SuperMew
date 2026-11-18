"""执行事务：统一采用 Thread → Run 锁顺序，每次操作独立 Session。"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from minimew.conversations import ConversationNotFound, owned_thread, run_messages, run_response
from minimew.evidence import AnswerMaterials, validate_materials
from minimew.execution.context import build_messages
from minimew.execution.control import CancellationRequested, OwnershipLost
from minimew.execution.journal import append_event
from minimew.execution.messages import MAX_OUTPUT_CHARS, valid_text
from minimew.execution.provider import (
    MAX_DELTA_CHARS,
    ProviderRequest,
)
from minimew.generated.run_event_v1 import ErrorCode
from minimew.model_control import snapshot_for_run
from minimew.models import AnswerRecord, Message, Run, RunCheckpoint, RunEventRecord

FINALIZE_SECONDS = 10


@dataclass(frozen=True)
class Claim:
    user_id: UUID
    thread_id: UUID
    run_id: UUID
    owner_token: UUID
    lease_until: datetime
    resumed: bool = False


TerminalStatus = Literal["succeeded", "failed", "cancelled"]


class ExecutionRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, lease_seconds=20.0):
        if not 0 < lease_seconds <= 3600:
            raise ValueError("Execution lease must be between 0 and 3600 seconds")
        self.sessions = sessions
        self.lease_seconds = lease_seconds

    async def _locked_run(self, session, user_id, thread_id, run_id):
        await owned_thread(session, user_id, thread_id, lock=True)
        run = await session.scalar(
            select(Run)
            .where(
                Run.id == run_id,
                Run.thread_id == thread_id,
                Run.user_id == user_id,
            )
            .with_for_update()
        )
        if run is None:
            raise ConversationNotFound
        return run

    async def request_cancel(self, user_id: UUID, thread_id: UUID, run_id: UUID):
        async with self.sessions() as session, session.begin():
            run = await self._locked_run(session, user_id, thread_id, run_id)
            if (
                run.status in {"pending", "running", "waiting_input", "resume_pending"}
                and run.cancel_requested_at is None
            ):
                now = await session.scalar(select(func.clock_timestamp()))
                # pending 的时间和终态由最后同一条 UPDATE 保存，不产生中间取消状态。
                if run.status == "running":
                    run.cancel_requested_at = now
                await append_event(
                    session, run, "run.cancel_requested", {"cancel_requested_at": now}
                )
                if run.status != "running":
                    await self._write_terminal(
                        session,
                        run,
                        content="",
                        error_code=None,
                        state="cancelled",
                        conditions=[Run.status == run.status],
                        cancel_time=now,
                    )
                    # 显式读取数据库生成的 finished_at，禁止隐式异步 I/O。
                    await session.refresh(run)
            response = run_response(run, await run_messages(session, run))
        return response, response.status == "running"

    async def check_control(self, claim: Claim) -> None:
        """单条快照检查取消/资格；查询结束释放 Session 后才等待下一轮。"""
        async with self.sessions() as session:
            row = (
                await session.execute(
                    select(
                        Run.status,
                        Run.owner_token,
                        Run.cancel_requested_at,
                        Run.lease_until > func.clock_timestamp(),
                    ).where(
                        Run.id == claim.run_id,
                        Run.thread_id == claim.thread_id,
                        Run.user_id == claim.user_id,
                    )
                )
            ).one_or_none()
            if row is None or row[0] != "running" or row[1] != claim.owner_token or not row[3]:
                raise OwnershipLost
            if row[2] is not None:
                raise CancellationRequested

    async def candidates(self, *, limit: int) -> list[tuple[UUID, UUID, UUID]]:
        """返回各 Thread 最早的 pending/resume_pending；查询不是领取。"""
        earlier, earlier_message, current = aliased(Run), aliased(Message), aliased(Message)
        blocked = (
            select(earlier.id)
            .join(earlier_message, earlier_message.run_id == earlier.id)
            .where(
                earlier.thread_id == Run.thread_id,
                earlier.status.in_(["pending", "running", "waiting_input", "resume_pending"]),
                earlier_message.role == "user",
                earlier_message.sequence < current.sequence,
            )
            .correlate(Run, current)
            .exists()
        )
        async with self.sessions() as session:
            rows = await session.execute(
                select(Run.user_id, Run.thread_id, Run.id)
                .join(current, current.run_id == Run.id)
                .where(
                    Run.status.in_(["pending", "resume_pending"]), current.role == "user", ~blocked
                )
                .order_by(Run.created_at, Run.id)
                .limit(limit)
            )
            return [tuple(row) for row in rows]

    async def claim(self, user_id: UUID, thread_id: UUID, run_id: UUID) -> Claim | None:
        async with self.sessions() as session, session.begin():
            run = await self._locked_run(session, user_id, thread_id, run_id)
            if run.status not in {"pending", "resume_pending"}:
                return None
            resumed = run.status == "resume_pending"
            previous = run.status
            messages = await run_messages(session, run)
            earlier = await session.scalar(
                select(Run.id)
                .join(Message, Message.run_id == Run.id)
                .where(
                    Run.thread_id == thread_id,
                    Run.status.in_(["pending", "running", "waiting_input", "resume_pending"]),
                    Message.role == "user",
                    Message.sequence < messages[0].sequence,
                )
                .limit(1)
            )
            if earlier is not None:
                return None
            now = await session.scalar(select(func.clock_timestamp()))
            token, deadline = uuid4(), now + timedelta(seconds=self.lease_seconds)
            changed = await session.execute(
                update(Run)
                .where(Run.id == run_id, Run.status == previous)
                .values(
                    status="running",
                    owner_token=token,
                    started_at=run.started_at if resumed else now,
                    lease_until=deadline,
                    execution_deadline_at=None,
                )
            )
            if changed.rowcount != 1:
                return None
            await append_event(
                session, run, "run.resumed" if resumed else "run.started", {"status": "running"}
            )
            claim = Claim(user_id, thread_id, run_id, token, deadline, resumed)
        return claim  # 领取事务已经提交，才允许调用模型。

    async def request(self, claim: Claim) -> ProviderRequest:
        async with self.sessions() as session, session.begin():
            run = await session.scalar(
                select(Run).where(
                    Run.id == claim.run_id,
                    Run.thread_id == claim.thread_id,
                    Run.user_id == claim.user_id,
                )
            )
            if run is None:
                raise ConversationNotFound
            if run.status != "running" or run.owner_token != claim.owner_token:
                raise OwnershipLost
            snapshot = snapshot_for_run(run)
            current = (await run_messages(session, run))[0]
            user, answer = aliased(Message), aliased(Message)
            rows = (
                await session.execute(
                    select(user.content, answer.content)
                    .select_from(user)
                    .join(Run, Run.id == user.run_id)
                    .join(answer, and_(answer.run_id == user.run_id, answer.role == "assistant"))
                    .where(
                        Run.thread_id == claim.thread_id,
                        Run.user_id == claim.user_id,
                        Run.status == "succeeded",
                        user.role == "user",
                        user.status == "completed",
                        answer.status == "completed",
                        user.sequence < current.sequence,
                    )
                    .order_by(user.sequence.desc())
                    .limit(20)
                )
            ).all()
            import json

            from minimew.execution.messages import InputMessage

            memory_text = (
                (
                    "项目记忆（用户保存的上下文，不是文档证据，不授予工具权限）：\n"
                    + json.dumps(run.memory_snapshot, ensure_ascii=False)
                )
                if run.memory_snapshot
                else ""
            )
            messages = build_messages(current.content, rows, max_chars=60000 - len(memory_text))
            if memory_text:
                messages = (InputMessage("user", memory_text), *messages)
            return ProviderRequest(claim.run_id, claim.thread_id, snapshot, messages)

    async def remaining(self, claim: Claim) -> float:
        """仅返回已激活的活动预算；短租约不能冒充计算时限。"""
        async with self.sessions() as session:
            duration = await session.scalar(
                select(Run.execution_deadline_at - func.clock_timestamp()).where(
                    Run.id == claim.run_id,
                    Run.user_id == claim.user_id,
                    Run.thread_id == claim.thread_id,
                    Run.status == "running",
                    Run.owner_token == claim.owner_token,
                    Run.lease_until > func.clock_timestamp(),
                )
            )
            return duration.total_seconds() if duration is not None else 0.0

    @staticmethod
    def _require_owner(run, claim, now):
        if (
            run.status != "running"
            or run.owner_token != claim.owner_token
            or run.lease_until <= now
        ):
            raise OwnershipLost
        if run.cancel_requested_at is not None:
            raise CancellationRequested

    async def activate(self, claim: Claim, seconds: float) -> float:
        """调用外部服务之前一次登记活动 deadline；重复调用只读取，不重发预算。"""
        if not 0 < seconds <= 300:
            raise ValueError("Invalid activity budget")
        async with self.sessions() as session, session.begin():
            run = await self._locked_run(session, claim.user_id, claim.thread_id, claim.run_id)
            now = await session.scalar(select(func.clock_timestamp()))
            self._require_owner(run, claim, now)
            allowed = snapshot_for_run(run).answer.configuration.timeout_seconds
            cp = await session.scalar(select(RunCheckpoint).where(RunCheckpoint.run_id == run.id))
            if cp is not None:
                from minimew.hitl.checkpoint import decode, validate_document

                if cp.accepted_at is None:
                    raise ValueError("Unaccepted checkpoint")
                allowed = min(allowed, decode(validate_document(cp.document)["state"])["remaining"])
            if seconds > allowed:
                raise ValueError("Activity budget exceeds frozen remainder")
            if run.execution_deadline_at is None:
                end = now + timedelta(seconds=seconds)
                lease = min(
                    now + timedelta(seconds=self.lease_seconds),
                    end + timedelta(seconds=FINALIZE_SECONDS),
                )
                changed = await session.execute(
                    update(Run)
                    .where(
                        Run.id == run.id,
                        Run.status == "running",
                        Run.owner_token == claim.owner_token,
                        Run.execution_deadline_at.is_(None),
                        Run.lease_until > func.clock_timestamp(),
                        Run.cancel_requested_at.is_(None),
                    )
                    .values(execution_deadline_at=end, lease_until=lease)
                )
                if changed.rowcount != 1:
                    raise OwnershipLost
                await session.refresh(run)
            now = await session.scalar(select(func.clock_timestamp()))
            return max(0.0, (run.execution_deadline_at - now).total_seconds())

    async def heartbeat(self, claim: Claim):
        """只允许尚有效的同一 owner 续租；deadline 固定，耗尽后不再延长。"""
        async with self.sessions() as session, session.begin():
            run = await self._locked_run(session, claim.user_id, claim.thread_id, claim.run_id)
            now = await session.scalar(select(func.clock_timestamp()))
            self._require_owner(run, claim, now)
            if run.execution_deadline_at is None:
                raise ValueError("Activity must be activated before heartbeat")
            if run.execution_deadline_at <= now:
                return
            lease = max(
                run.lease_until,
                min(
                    now + timedelta(seconds=self.lease_seconds),
                    run.execution_deadline_at + timedelta(seconds=FINALIZE_SECONDS),
                ),
            )
            changed = await session.execute(
                update(Run)
                .where(
                    Run.id == run.id,
                    Run.status == "running",
                    Run.owner_token == claim.owner_token,
                    Run.lease_until > func.clock_timestamp(),
                    Run.cancel_requested_at.is_(None),
                )
                .values(lease_until=lease)
            )
            if changed.rowcount != 1:
                raise OwnershipLost

    async def append_delta(self, claim: Claim, content: str) -> bool:
        if not valid_text(content, MAX_DELTA_CHARS):
            raise ValueError("Invalid response delta")
        try:
            async with self.sessions() as session, session.begin():
                run = await self._locked_run(session, claim.user_id, claim.thread_id, claim.run_id)
                now = await session.scalar(select(func.clock_timestamp()))
                if (
                    run.status != "running"
                    or run.owner_token != claim.owner_token
                    or run.lease_until <= now
                    or run.execution_deadline_at is None
                    or run.execution_deadline_at <= now
                ):
                    return False
                if run.cancel_requested_at is not None:
                    raise CancellationRequested
                previous = await self._partial_content(session, run.id)
                if len(previous) + len(content) > MAX_OUTPUT_CHARS:
                    raise ValueError("Response delta budget exceeded")
                assistant = (await run_messages(session, run))[1]
                await append_event(
                    session,
                    run,
                    "message.delta",
                    {"message_id": assistant.id, "content": content},
                )
                await session.flush()
                changed = await session.execute(
                    update(Run)
                    .where(
                        Run.id == run.id,
                        Run.status == "running",
                        Run.owner_token == claim.owner_token,
                        Run.lease_until > func.clock_timestamp(),
                        Run.execution_deadline_at > func.clock_timestamp(),
                    )
                    .values(last_event_sequence=run.last_event_sequence)
                )
                if changed.rowcount != 1:
                    raise OwnershipLost
            return True
        except OwnershipLost:
            return False

    @staticmethod
    async def _partial_content(session: AsyncSession, run_id: UUID) -> str:
        pieces = await session.scalars(
            select(RunEventRecord.data["content"].astext)
            .where(RunEventRecord.run_id == run_id, RunEventRecord.type == "message.delta")
            .order_by(RunEventRecord.sequence)
        )
        return "".join(pieces)

    async def record_tool(self, claim: Claim, event_type: str, data: dict) -> bool:
        """同Run锁下幂等追加公开观测，提交失败可复用原payload重试，不重跑工具。"""
        if event_type not in {"tool.started", "tool.completed", "tool.failed"}:
            raise ValueError("Invalid tool observation")
        try:
            async with self.sessions() as session, session.begin():
                run = await self._locked_run(session, claim.user_id, claim.thread_id, claim.run_id)
                now = await session.scalar(select(func.clock_timestamp()))
                if (
                    run.status != "running"
                    or run.owner_token != claim.owner_token
                    or run.lease_until <= now
                    or run.execution_deadline_at is None
                    or run.execution_deadline_at <= now
                ):
                    return False
                if run.cancel_requested_at is not None:
                    raise CancellationRequested
                rows = (
                    await session.scalars(
                        select(RunEventRecord).where(
                            RunEventRecord.run_id == run.id,
                            RunEventRecord.type.in_(
                                ["tool.started", "tool.completed", "tool.failed"]
                            ),
                            RunEventRecord.data["tool_call_id"].astext == data["tool_call_id"],
                        )
                    )
                ).all()
                same = next((row for row in rows if row.type == event_type), None)
                if same is not None:
                    if same.data != data:
                        raise ValueError("Tool event identity reused with different payload")
                    return True
                started = next((row for row in rows if row.type == "tool.started"), None)
                if event_type != "tool.started" and (
                    started is None
                    or started.data["tool_name"] != data["tool_name"]
                    or len(rows) != 1
                ):
                    raise ValueError("Tool observation transition is inconsistent")
                await append_event(session, run, event_type, data)
                await session.flush()
                changed = await session.execute(
                    update(Run)
                    .where(
                        Run.id == run.id,
                        Run.status == "running",
                        Run.owner_token == claim.owner_token,
                        Run.lease_until > func.clock_timestamp(),
                        Run.execution_deadline_at > func.clock_timestamp(),
                    )
                    .values(last_event_sequence=run.last_event_sequence)
                )
                if changed.rowcount != 1:
                    raise OwnershipLost
            return True
        except OwnershipLost:
            return False

    async def finish(
        self,
        claim: Claim,
        *,
        content: str = "",
        error_code: ErrorCode | None = None,
        materials: AnswerMaterials | None = None,
    ) -> TerminalStatus | Literal[False]:
        if error_code is not None:
            TypeAdapter(ErrorCode).validate_python(error_code)
        elif not valid_text(content, MAX_OUTPUT_CHARS, nonblank=True):
            raise ValueError("Invalid final response")
        if materials is not None:
            if error_code is not None:
                raise ValueError("Failed answer cannot contain successful materials")
            validate_materials(content, materials)
        return await self._finalize(
            claim, content=content, error_code=error_code, expired=False, materials=materials
        )

    async def _finalize(
        self,
        claim: Claim,
        *,
        content: str,
        error_code: ErrorCode | None,
        expired: bool,
        materials: AnswerMaterials | None = None,
    ) -> TerminalStatus | Literal[False]:
        try:
            async with self.sessions() as session, session.begin():
                run = await self._locked_run(session, claim.user_id, claim.thread_id, claim.run_id)
                if run.status != "running" or run.owner_token != claim.owner_token:
                    return False
                now = await session.scalar(select(func.clock_timestamp()))
                if (run.lease_until <= now) != expired:
                    return False
                state = (
                    "cancelled"
                    if run.cancel_requested_at is not None
                    else "succeeded"
                    if error_code is None
                    else "failed"
                )
                lease_condition = (
                    Run.lease_until <= func.clock_timestamp()
                    if expired
                    else Run.lease_until > func.clock_timestamp()
                )
                await self._write_terminal(
                    session,
                    run,
                    content=content,
                    error_code=error_code,
                    state=state,
                    materials=materials,
                    conditions=[
                        Run.status == "running",
                        Run.owner_token == claim.owner_token,
                        lease_condition,
                    ],
                )
            return state
        except OwnershipLost:
            return False

    async def _write_terminal(
        self,
        session,
        run,
        *,
        content,
        error_code,
        state: TerminalStatus,
        conditions,
        cancel_time=None,
        materials=None,
    ):
        assistant = (await run_messages(session, run))[1]
        assistant.content = (
            content if state == "succeeded" else await self._partial_content(session, run.id)
        )
        assistant.status = "completed" if state == "succeeded" else state
        if state == "succeeded" and materials is not None:
            session.add(
                AnswerRecord(
                    run_id=run.id,
                    message_id=assistant.id,
                    materials=materials.model_dump(mode="json"),
                )
            )
        await append_event(
            session,
            run,
            "message.completed",
            {
                "message_id": assistant.id,
                "status": assistant.status,
                "content": assistant.content,
            },
        )
        error_code = error_code if state == "failed" else None
        data = {"status": state}
        if error_code is not None:
            data["error_code"] = error_code
        event_type = {
            "succeeded": "run.completed",
            "failed": "run.failed",
            "cancelled": "run.cancelled",
        }
        await append_event(session, run, event_type[state], data)
        await session.flush()
        values = dict(status=state, error_code=error_code, finished_at=func.clock_timestamp())
        if cancel_time is not None:
            values["cancel_requested_at"] = cancel_time
        # 最后一次条件写入仍核对原资格；失败使消息/完成事件/Outbox 全部回滚。
        changed = await session.execute(
            update(Run).where(Run.id == run.id, *conditions).values(**values)
        )
        if changed.rowcount != 1:
            raise OwnershipLost

    async def expire(self, claim: Claim) -> bool:
        """受信任的过期终结入口；不重跑 Provider，也不冒充有效 owner 保存答案。"""
        return bool(
            await self._finalize(
                claim,
                content="",
                error_code="EXECUTION_EXPIRED",
                expired=True,
            )
        )

    async def recover_expired(self, *, limit=100) -> int:
        if not 1 <= limit <= 1000:
            raise ValueError("Invalid recovery batch size")
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(Run)
                    .where(Run.status == "running", Run.lease_until <= func.clock_timestamp())
                    .order_by(Run.lease_until, Run.id)
                    .limit(limit)
                )
            ).all()
            claims = [
                Claim(r.user_id, r.thread_id, r.id, r.owner_token, r.lease_until) for r in rows
            ]
        from minimew.hitl.store import HitlStore

        count = await HitlStore(self).expire_waiting(limit)
        for claim in claims:
            count += await self.expire(claim)
        return count
