"""Thread 的唯一 HTTP 入口；所有读写均限制到认证用户。"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from minimew.api.auth import CurrentUser
from minimew.conversation_schemas import MessagePage
from minimew.conversations import ConversationNotFound, get_history
from minimew.database import SessionDependency
from minimew.models import Thread


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/v1/threads", tags=["threads"], dependencies=[Depends(no_store)])


@router.get("/{thread_id}/messages", response_model=MessagePage)
async def history(
    thread_id: UUID,
    user: CurrentUser,
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before: Annotated[int | None, Query(ge=1)] = None,
) -> MessagePage:
    try:
        return await get_history(session, user.id, thread_id, limit, before)
    except ConversationNotFound:
        raise HTTPException(404, "Conversation not found") from None


class CreateThreadRequest(BaseModel):
    # 当前只创建默认对话；不接受客户端指定 owner、ID 或标题。
    model_config = ConfigDict(extra="forbid")


class ThreadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    created_at: datetime


class ThreadListResponse(BaseModel):
    items: list[ThreadResponse]
    next_offset: int | None


@router.post("", status_code=201, response_model=ThreadResponse)
async def create_thread(
    data: CreateThreadRequest, user: CurrentUser, session: SessionDependency
) -> ThreadResponse:
    async with session.begin():
        thread = Thread(user_id=user.id, title="新对话")
        session.add(thread)
        await session.flush()
        result = ThreadResponse.model_validate(thread)
    # 提交成功才返回；flush 仅用于取得数据库生成的 ID 和时间。
    return result


@router.get("", response_model=ThreadListResponse)
async def list_threads(
    user: CurrentUser,
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ThreadListResponse:
    async with session.begin():
        rows = (
            await session.scalars(
                select(Thread)
                .where(Thread.user_id == user.id)
                .order_by(Thread.created_at.desc(), Thread.id.desc())
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
        return ThreadListResponse(
            items=[ThreadResponse.model_validate(row) for row in rows[:limit]],
            next_offset=offset + limit if len(rows) > limit else None,
        )


@router.get("/{thread_id}", response_model=ThreadResponse)
async def get_thread(
    thread_id: UUID, user: CurrentUser, session: SessionDependency
) -> ThreadResponse:
    async with session.begin():
        thread = await session.scalar(
            select(Thread).where(Thread.id == thread_id, Thread.user_id == user.id)
        )
        if thread is None:
            raise HTTPException(404, "Thread not found")
        return ThreadResponse.model_validate(thread)
