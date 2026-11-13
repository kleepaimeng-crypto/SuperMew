"""从持久Message与答案材料读取同一份引用；不重新检索当前文档。"""

import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from minimew.conversations import ConversationNotFound
from minimew.evidence import AnswerMaterials, validate_materials
from minimew.generated.run_event_v1 import RunStatus
from minimew.models import AnswerRecord, Message, Run, Thread


class AnswerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: UUID
    message_id: UUID
    status: RunStatus
    materials: AnswerMaterials | None


async def read_answer(session, user_id: UUID, thread_id: UUID, run_id: UUID):
    async with session.begin():
        row = (
            await session.execute(
                select(Run, Message, AnswerRecord)
                .join(Thread, (Thread.id == Run.thread_id) & (Thread.user_id == Run.user_id))
                .join(
                    Message,
                    (Message.run_id == Run.id)
                    & (Message.thread_id == Run.thread_id)
                    & (Message.role == "assistant"),
                )
                .outerjoin(
                    AnswerRecord,
                    (AnswerRecord.run_id == Run.id) & (AnswerRecord.message_id == Message.id),
                )
                .where(
                    Run.id == run_id,
                    Run.thread_id == thread_id,
                    Run.user_id == user_id,
                    Thread.user_id == user_id,
                )
            )
        ).one_or_none()
        if row is None:
            raise ConversationNotFound
        run, message, record = row
        materials = None
        if record is not None:
            if run.status != "succeeded" or message.status != "completed":
                raise ValueError("Answer record without successful terminal")
            materials = AnswerMaterials.model_validate_json(json.dumps(record.materials))
            validate_materials(message.content, materials)
        return AnswerResponse(
            run_id=run.id, message_id=message.id, status=run.status, materials=materials
        )
