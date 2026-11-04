"""已提交事件的唯一分页查询；读取不推进数据库状态，也不决定 Run 终态。"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from minimew.conversation_schemas import RunEventPage
from minimew.conversations import ConversationNotFound, owned_thread
from minimew.execution.journal import event_json
from minimew.generated.run_event_v1 import EVENT_ADAPTER, RunStatus
from minimew.models import Run, RunEventRecord


class EventCursorAhead(Exception):
    pass


@dataclass(frozen=True)
class EventRead:
    page: RunEventPage
    status: RunStatus
    last_sequence: int

    @property
    def terminal(self) -> bool:
        return self.status in {"succeeded", "failed", "cancelled"}


async def events_after(
    session: AsyncSession,
    user_id: UUID,
    thread_id: UUID,
    run_id: UUID,
    *,
    after: int = 0,
    limit: int = 100,
) -> EventRead:
    if type(after) is not int or not 0 <= after <= 2**63 - 1:
        raise ValueError("Invalid event cursor")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Invalid event page size")
    async with session.begin():
        await owned_thread(session, user_id, thread_id)
        run = await session.scalar(
            select(Run)
            .where(Run.id == run_id, Run.thread_id == thread_id, Run.user_id == user_id)
            .execution_options(populate_existing=True)
        )
        if run is None:
            raise ConversationNotFound
        if after > run.last_event_sequence:
            raise EventCursorAhead
        # 固定本次读取范围；下一次新 Session 再发现其后提交的事件。
        last_sequence, status = run.last_event_sequence, run.status
        rows = list(
            (
                await session.scalars(
                    select(RunEventRecord)
                    .where(
                        RunEventRecord.run_id == run_id,
                        RunEventRecord.thread_id == thread_id,
                        RunEventRecord.sequence > after,
                        RunEventRecord.sequence <= last_sequence,
                    )
                    .order_by(RunEventRecord.sequence)
                    .limit(limit + 1)
                )
            ).all()
        )
        items = [EVENT_ADAPTER.validate_json(event_json(row)) for row in rows[:limit]]
        return EventRead(
            page=RunEventPage(
                items=items,
                next_cursor=items[-1].sequence if items else after,
                has_more=len(rows) > limit,
            ),
            status=status,
            last_sequence=last_sequence,
        )
