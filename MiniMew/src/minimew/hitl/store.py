"""Checkpoint/Run/Message/Journal 同事务发布，Thread → Run 锁顺序。"""

import asyncio
import hashlib
import hmac
import json
import secrets
from dataclasses import asdict
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select, update

from minimew.conversations import run_messages, run_response
from minimew.execution.control import CancellationRequested, OwnershipLost
from minimew.execution.journal import append_event
from minimew.execution.tools import ToolFailure
from minimew.hitl.checkpoint import decode, encode, validate_document
from minimew.hitl.policy import CheckpointExpired
from minimew.hitl.schemas import ResumeConflict, ResumeCredential
from minimew.models import Run, RunCheckpoint


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class HitlStore:
    def __init__(self, repository):
        self.repository = repository
        self.sessions = repository.sessions

    async def pause(self, claim, request, context, pause, outer, budget, deadline):
        from minimew.knowledge import NAME

        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise ToolFailure("AGENT_DEADLINE_EXCEEDED")
        scope = context.tool_sessions[NAME].scope
        if scope is None or scope.user_id != claim.user_id:
            raise ToolFailure("KNOWLEDGE_SCOPE_INVALID")
        document = validate_document(
            {
                "version": 1,
                "run_id": str(claim.run_id),
                "catalog_hash": request.snapshot.catalog_hash(),
                "state": encode(
                    {
                        "outer": outer,
                        "budget": asdict(budget),
                        "remaining": remaining,
                        "graph": pause.graph,
                        "scope": scope,
                        "evidence": context.evidence.items,
                        "queried": context.evidence.queried,
                        "skill_digest": context.skill.digest if context.skill is not None else None,
                    }
                ),
            }
        )
        async with self.sessions() as session, session.begin():
            run = await self.repository._locked_run(
                session, claim.user_id, claim.thread_id, claim.run_id
            )
            now = await session.scalar(select(func.clock_timestamp()))
            self.active(run, claim, now)
            from minimew.skills.schemas import parse_snapshot

            if (
                request.skill is None
                or parse_snapshot(run.skill_snapshot).digest != request.skill.digest
            ):
                raise ValueError("Checkpoint Skill mismatch")
            if run.execution_deadline_at is None or run.execution_deadline_at <= now:
                raise ToolFailure("AGENT_DEADLINE_EXCEEDED")
            expires = min(
                now + timedelta(seconds=context.hitl.wait_seconds),
                scope.expires_at - timedelta(seconds=remaining + 1),
            )
            if expires <= now:
                raise ToolFailure("KNOWLEDGE_SCOPE_INVALID")
            checkpoint = RunCheckpoint(
                id=uuid4(),
                run_id=run.id,
                document=document,
                prompt=pause.prompt,
                expires_at=expires,
            )
            session.add(checkpoint)
            (await run_messages(session, run))[1].status = "waiting_input"
            await append_event(
                session,
                run,
                "hitl.required",
                {
                    "checkpoint_id": checkpoint.id,
                    "prompt": checkpoint.prompt,
                    "expires_at": expires,
                },
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
                    Run.cancel_requested_at.is_(None),
                )
                .values(
                    status="waiting_input",
                    owner_token=None,
                    lease_until=None,
                    execution_deadline_at=None,
                )
            )
            if changed.rowcount != 1:
                raise OwnershipLost

    @staticmethod
    def active(run, claim, now):
        if (
            run.status != "running"
            or run.owner_token != claim.owner_token
            or run.lease_until <= now
        ):
            raise OwnershipLost
        if run.cancel_requested_at is not None:
            raise CancellationRequested

    async def load(self, claim, snapshot):
        async with self.sessions() as session:
            cp = await session.scalar(
                select(RunCheckpoint).where(RunCheckpoint.run_id == claim.run_id)
            )
            if cp is None:
                return None
            run = await session.get(Run, claim.run_id)
            now = await session.scalar(select(func.clock_timestamp()))
            self.active(run, claim, now)
            document = validate_document(cp.document)
            if (
                document["run_id"] != str(claim.run_id)
                or document["catalog_hash"] != snapshot.catalog_hash()
                or cp.accepted_at is None
            ):
                raise ValueError("Checkpoint identity mismatch")
            state = decode(document["state"])
            from minimew.skills.schemas import parse_snapshot

            if state["skill_digest"] != parse_snapshot(run.skill_snapshot).digest:
                raise ValueError("Checkpoint Skill mismatch")
            if (
                now >= cp.expires_at
                or now + timedelta(seconds=state["remaining"]) >= state["scope"].expires_at
            ):
                raise CheckpointExpired
            if state["scope"].user_id != claim.user_id:
                raise ValueError("Checkpoint scope mismatch")
            state["answer"] = cp.answer
            return state

    async def credential(self, user_id, thread_id, run_id):
        async with self.sessions() as session, session.begin():
            run = await self.repository._locked_run(session, user_id, thread_id, run_id)
            cp = await session.scalar(select(RunCheckpoint).where(RunCheckpoint.run_id == run_id))
            now = await session.scalar(select(func.clock_timestamp()))
            if run.status != "waiting_input" or cp is None or cp.expires_at <= now:
                raise ResumeConflict
            token = secrets.token_urlsafe(32)
            cp.token_hash = digest(token)
            return ResumeCredential(
                checkpoint_id=cp.id, prompt=cp.prompt, expires_at=cp.expires_at, token=token
            )

    async def accept(self, user_id, thread_id, run_id, data):
        fingerprint = digest(json.dumps([str(data.checkpoint_id), data.answer], ensure_ascii=False))
        async with self.sessions() as session, session.begin():
            run = await self.repository._locked_run(session, user_id, thread_id, run_id)
            cp = await session.scalar(select(RunCheckpoint).where(RunCheckpoint.run_id == run_id))
            if cp is None or cp.id != data.checkpoint_id:
                raise ResumeConflict
            if cp.accepted_at is not None:
                if cp.resume_key != data.idempotency_key or cp.input_hash != fingerprint:
                    raise ResumeConflict
                return run_response(run, await run_messages(session, run))
            now = await session.scalar(select(func.clock_timestamp()))
            if (
                run.status != "waiting_input"
                or cp.expires_at <= now
                or cp.token_hash is None
                or not hmac.compare_digest(cp.token_hash, digest(data.token))
            ):
                raise ResumeConflict
            cp.accepted_at, cp.resume_key, cp.input_hash = now, data.idempotency_key, fingerprint
            cp.answer, cp.token_hash = data.answer, None
            (await run_messages(session, run))[1].status = "pending"
            await append_event(session, run, "hitl.accepted", {"checkpoint_id": cp.id})
            run.status = "resume_pending"
            await session.flush()
            return run_response(run, await run_messages(session, run))

    async def expire_waiting(self, limit):
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    select(Run.user_id, Run.thread_id, Run.id)
                    .join(RunCheckpoint, RunCheckpoint.run_id == Run.id)
                    .where(
                        Run.status.in_(["waiting_input", "resume_pending"]),
                        RunCheckpoint.expires_at <= func.clock_timestamp(),
                    )
                    .order_by(RunCheckpoint.expires_at)
                    .limit(limit)
                )
            ).all()
        count = 0
        for user, thread, rid in rows:
            async with self.sessions() as session, session.begin():
                run = await self.repository._locked_run(session, user, thread, rid)
                if run.status not in {"waiting_input", "resume_pending"}:
                    continue
                await self.repository._write_terminal(
                    session,
                    run,
                    content="",
                    error_code="HITL_EXPIRED",
                    state="failed",
                    conditions=[Run.status == run.status],
                )
                count += 1
        return count
