"""Journal 唯一追加入口；调用方持有 Run 行锁或正在创建该 Run。"""

import json
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from minimew.generated.run_event_v1 import EVENT_ADAPTER, RunEvent
from minimew.models import EventOutbox, Run, RunEventRecord


def event_json(row: RunEventRecord) -> str:
    """同一个公开形状供 Journal 查询和通知发布使用。"""
    return json.dumps(
        {
            "schema_version": row.schema_version,
            "event_id": str(row.event_id),
            "sequence": row.sequence,
            "run_id": str(row.run_id),
            "thread_id": str(row.thread_id),
            "type": row.type,
            "timestamp": row.timestamp.isoformat(),
            "data": row.data,
        }
    )


async def append_event(session: AsyncSession, run: Run, event_type: str, data: dict) -> RunEvent:
    if not session.in_transaction() or run.status in {"succeeded", "failed", "cancelled"}:
        raise RuntimeError("Events require an open transaction and a nonterminal locked Run")
    now = await session.scalar(select(func.clock_timestamp()))
    sequence = run.last_event_sequence + 1
    event = EVENT_ADAPTER.validate_python(
        {
            "schema_version": 1,
            "event_id": uuid4(),
            "sequence": sequence,
            "run_id": run.id,
            "thread_id": run.thread_id,
            "type": event_type,
            "timestamp": now,
            "data": data,
        }
    )
    run.last_event_sequence = sequence
    session.add(
        RunEventRecord(
            event_id=event.event_id,
            schema_version=event.schema_version,
            run_id=event.run_id,
            thread_id=event.thread_id,
            sequence=event.sequence,
            type=event.type,
            timestamp=event.timestamp,
            data=event.data.model_dump(mode="json"),
        )
    )
    session.add(EventOutbox(event_id=event.event_id))
    return event
