"""提问预留与持久事实查询；不调用模型或创建后台任务。"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from minimew.conversation_schemas import CreateRunRequest, MessagePage, MessageResponse, RunResponse
from minimew.execution.journal import append_event
from minimew.model_control import ModelConfigurationUnavailable, freeze_current_snapshot
from minimew.models import Message, Run, Thread


class ConversationNotFound(Exception):
    pass


class IdempotencyConflict(Exception):
    pass


def title_from_content(content: str) -> str:
    title = " ".join(content.split())
    return title if len(title) <= 30 else title[:30] + "…"


async def owned_thread(session: AsyncSession, user_id: UUID, thread_id: UUID, *, lock=False):
    query = select(Thread).where(Thread.id == thread_id, Thread.user_id == user_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    thread = await session.scalar(query)
    if thread is None:
        raise ConversationNotFound
    return thread


async def run_messages(session: AsyncSession, run: Run) -> list[Message]:
    rows = list(
        (
            await session.scalars(
                select(Message)
                .where(Message.run_id == run.id, Message.thread_id == run.thread_id)
                .order_by(Message.sequence)
            )
        ).all()
    )
    if len(rows) != 2 or [row.role for row in rows] != ["user", "assistant"]:
        raise RuntimeError("Run message pair is inconsistent")
    return rows


def run_response(run: Run, messages: list[Message]) -> RunResponse:
    return RunResponse(
        id=run.id,
        thread_id=run.thread_id,
        status=run.status,
        user_message_id=messages[0].id,
        assistant_message_id=messages[1].id,
        created_at=run.created_at,
        finished_at=run.finished_at,
        cancel_requested_at=run.cancel_requested_at,
        error_code=run.error_code,
    )


async def reserve_run(
    session: AsyncSession,
    user_id: UUID,
    thread_id: UUID,
    data: CreateRunRequest,
    *,
    credentials_available: bool = True,
) -> tuple[RunResponse, bool]:
    async with session.begin():
        thread = await owned_thread(session, user_id, thread_id, lock=True)
        existing = await session.scalar(
            select(Run).where(
                Run.thread_id == thread_id,
                Run.user_id == user_id,
                Run.idempotency_key == data.idempotency_key,
            )
        )
        if existing is not None:
            messages = await run_messages(session, existing)
            if messages[0].content != data.content:
                raise IdempotencyConflict
            result, created = run_response(existing, messages), False
        else:
            # 已有幂等结果优先；凭据是否就绪只影响新的工作。
            if not credentials_available:
                raise ModelConfigurationUnavailable
            snapshot = await freeze_current_snapshot(session)
            run = Run(
                thread_id=thread_id,
                user_id=user_id,
                idempotency_key=data.idempotency_key,
                model_snapshot_json=snapshot.model_dump(mode="json"),
                model_catalog_hash=snapshot.catalog_hash(),
            )
            session.add(run)
            await session.flush()
            messages = [
                Message(
                    thread_id=thread_id,
                    run_id=run.id,
                    sequence=thread.last_sequence + 1,
                    role="user",
                    status="completed",
                    content=data.content,
                ),
                Message(
                    thread_id=thread_id,
                    run_id=run.id,
                    sequence=thread.last_sequence + 2,
                    role="assistant",
                    status="pending",
                    content="",
                ),
            ]
            session.add_all(messages)
            if thread.last_sequence == 0:
                thread.title = title_from_content(data.content)
            thread.last_sequence += 2
            await session.flush()
            await append_event(
                session,
                run,
                "run.created",
                {
                    "status": "pending",
                    "assistant_message_id": messages[1].id,
                },
            )
            result, created = run_response(run, messages), True
    return result, created  # 包括标题的整个事务已提交，才向 HTTP 层返回。


async def get_run(
    session: AsyncSession, user_id: UUID, thread_id: UUID, run_id: UUID
) -> RunResponse:
    async with session.begin():
        await owned_thread(session, user_id, thread_id)
        run = await session.scalar(
            select(Run).where(Run.id == run_id, Run.thread_id == thread_id, Run.user_id == user_id)
        )
        if run is None:
            raise ConversationNotFound
        return run_response(run, await run_messages(session, run))


async def get_history(
    session: AsyncSession, user_id: UUID, thread_id: UUID, limit: int, before: int | None
) -> MessagePage:
    async with session.begin():
        await owned_thread(session, user_id, thread_id)
        query = select(Message).where(Message.thread_id == thread_id)
        if before is not None:
            query = query.where(Message.sequence < before)
        rows = list(
            (await session.scalars(query.order_by(Message.sequence.desc()).limit(limit + 1))).all()
        )
        page = rows[:limit]
        return MessagePage(
            items=[MessageResponse.model_validate(row) for row in reversed(page)],
            previous_cursor=page[-1].sequence if len(rows) > limit else None,
        )
