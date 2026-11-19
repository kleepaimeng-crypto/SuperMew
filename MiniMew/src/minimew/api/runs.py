from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.exc import SQLAlchemyError

from minimew.answers import AnswerResponse, read_answer
from minimew.api.auth import (
    AuthenticatedAccess,
    CurrentUser,
    bearer,
    get_current_access,
    unauthorized,
)
from minimew.api.threads import no_store
from minimew.conversation_schemas import CreateRunRequest, RunEventPage, RunResponse
from minimew.conversations import ConversationNotFound, IdempotencyConflict, get_run, reserve_run
from minimew.database import SessionDependency
from minimew.execution.queries import EventCursorAhead, events_after
from minimew.execution.repository import ExecutionRepository
from minimew.execution.streaming import EventReadUnavailable, EventStreamResponse, EventSubscription
from minimew.hitl.schemas import ResumeConflict, ResumeCredential, ResumeInput
from minimew.hitl.store import HitlStore
from minimew.model_control import ModelConfigurationUnavailable
from minimew.settings import SettingsDependency
from minimew.skills.catalog import SkillUnavailable

router = APIRouter(
    prefix="/v1/threads/{thread_id}/runs", tags=["runs"], dependencies=[Depends(no_store)]
)


@router.post("/{run_id}/resume-credential", response_model=ResumeCredential)
async def resume_credential(thread_id: UUID, run_id: UUID, user: CurrentUser, request: Request):
    try:
        return await HitlStore(ExecutionRepository(request.app.state.session_factory)).credential(
            user.id, thread_id, run_id
        )
    except ConversationNotFound:
        raise HTTPException(404, "Conversation not found") from None
    except ResumeConflict:
        raise HTTPException(409, detail={"code": "HITL_RESUME_CONFLICT"}) from None
    except (SQLAlchemyError, TimeoutError, OSError):
        raise HTTPException(503, detail={"code": "HITL_RESUME_UNAVAILABLE"}) from None


@router.post("/{run_id}/resume", response_model=RunResponse, status_code=202)
async def resume_run(
    thread_id: UUID, run_id: UUID, data: ResumeInput, user: CurrentUser, request: Request
):
    try:
        return await HitlStore(ExecutionRepository(request.app.state.session_factory)).accept(
            user.id, thread_id, run_id, data
        )
    except ConversationNotFound:
        raise HTTPException(404, "Conversation not found") from None
    except ResumeConflict:
        raise HTTPException(409, detail={"code": "HITL_RESUME_CONFLICT"}) from None
    except (SQLAlchemyError, TimeoutError, OSError):
        raise HTTPException(503, detail={"code": "HITL_RESUME_UNAVAILABLE"}) from None


async def cancel_access(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: SettingsDependency,
    session: SessionDependency,
) -> AuthenticatedAccess:
    try:
        return await get_current_access(credentials, settings, session)
    except HTTPException as failure:
        failure.headers = {**(failure.headers or {}), "Cache-Control": "no-store"}
        raise
    except (SQLAlchemyError, TimeoutError, OSError):
        raise HTTPException(
            503,
            detail={"code": "RUN_CANCELLATION_UNAVAILABLE"},
            headers={"Cache-Control": "no-store"},
        ) from None


@router.post(
    "/{run_id}/cancel",
    response_model=RunResponse,
    responses={
        202: {"model": RunResponse, "description": "Cancellation request persisted"},
        503: {"description": "RUN_CANCELLATION_UNAVAILABLE; query or retry the same Run"},
    },
)
async def cancel_run(
    thread_id: UUID,
    run_id: UUID,
    request: Request,
    response: Response,
    access: Annotated[AuthenticatedAccess, Depends(cancel_access)],
) -> RunResponse:
    try:
        result, pending = await ExecutionRepository(
            request.app.state.session_factory
        ).request_cancel(
            access.user.id,
            thread_id,
            run_id,
        )
    except ConversationNotFound:
        raise HTTPException(
            404, "Conversation not found", headers={"Cache-Control": "no-store"}
        ) from None
    except (SQLAlchemyError, TimeoutError, OSError):
        raise HTTPException(
            503,
            detail={"code": "RUN_CANCELLATION_UNAVAILABLE"},
            headers={"Cache-Control": "no-store"},
        ) from None
    response.status_code = 202 if pending else 200
    return result


@router.post(
    "",
    status_code=201,
    response_model=RunResponse,
    responses={
        200: {"model": RunResponse},
        503: {
            "description": "MODEL_CONFIGURATION_UNAVAILABLE: model/credentials missing or invalid"
        },
    },
)
async def create_run(
    thread_id: UUID,
    data: CreateRunRequest,
    user: CurrentUser,
    session: SessionDependency,
    response: Response,
    settings: SettingsDependency,
) -> RunResponse:
    try:
        result, created = await reserve_run(
            session,
            user.id,
            thread_id,
            data,
            credentials_available=settings.model_credentials_available,
            settings=settings,
        )
    except ConversationNotFound:
        raise HTTPException(404, "Conversation not found") from None
    except IdempotencyConflict:
        raise HTTPException(
            409, "Idempotency key already used with different content or Skill"
        ) from None
    except SkillUnavailable:
        raise HTTPException(422, detail={"code": "SKILL_UNAVAILABLE"}) from None
    except ModelConfigurationUnavailable:
        raise HTTPException(503, detail={"code": "MODEL_CONFIGURATION_UNAVAILABLE"}) from None
    response.status_code = 201 if created else 200
    return result


@router.get("/{run_id}/events", response_model=RunEventPage)
async def read_events(
    thread_id: UUID,
    run_id: UUID,
    user: CurrentUser,
    session: SessionDependency,
    after: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> RunEventPage:
    try:
        result = await events_after(session, user.id, thread_id, run_id, after=after, limit=limit)
        return result.page
    except ConversationNotFound:
        raise HTTPException(404, "Conversation not found") from None
    except EventCursorAhead:
        raise HTTPException(422, detail={"code": "EVENT_CURSOR_AHEAD"}) from None


async def stream_access(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: SettingsDependency,
    session: SessionDependency,
) -> AuthenticatedAccess:
    # 只包住同一正式认证函数的存储边界，不建立第二套验签/身份逻辑。
    try:
        return await get_current_access(credentials, settings, session)
    except (SQLAlchemyError, TimeoutError, OSError):
        raise HTTPException(503, detail={"code": "EVENT_STREAM_UNAVAILABLE"}) from None


@router.get(
    "/{run_id}/events/stream",
    status_code=200,
    response_class=EventStreamResponse,
    responses={
        200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}},
        401: {"description": "Invalid or expired Access Token"},
        404: {"description": "Conversation not found or not owned by this user"},
        204: {"description": "Terminal journal already consumed"},
        503: {"description": "EVENT_STREAM_UNAVAILABLE"},
    },
)
async def stream_events(
    thread_id: UUID,
    run_id: UUID,
    request: Request,
    access: Annotated[AuthenticatedAccess, Depends(stream_access)],
    after: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
) -> Response:
    subscription = EventSubscription(
        request.app.state.session_factory,
        access.user.id,
        thread_id,
        run_id,
        after=after,
        expires_at=access.expires_at,
        options=request.app.state.event_stream_options,
        notifier=request.app.state.event_notifier,
    )
    try:
        initial = await subscription.read_page(after)
    except ConversationNotFound:
        raise HTTPException(404, "Conversation not found") from None
    except EventCursorAhead:
        raise HTTPException(422, detail={"code": "EVENT_CURSOR_AHEAD"}) from None
    except EventReadUnavailable:
        raise HTTPException(503, detail={"code": "EVENT_STREAM_UNAVAILABLE"}) from None
    if subscription.expired():
        raise unauthorized()
    if initial.terminal and after >= initial.last_sequence:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    return EventStreamResponse(subscription, initial)


@router.get("/{run_id}", response_model=RunResponse)
async def read_run(
    thread_id: UUID, run_id: UUID, user: CurrentUser, session: SessionDependency
) -> RunResponse:
    try:
        return await get_run(session, user.id, thread_id, run_id)
    except ConversationNotFound:
        raise HTTPException(404, "Conversation not found") from None


@router.get(
    "/{run_id}/answer",
    response_model=AnswerResponse,
    responses={
        404: {"description": "Conversation not found"},
        503: {"description": "ANSWER_READ_UNAVAILABLE"},
    },
)
async def read_answer_materials(
    thread_id: UUID, run_id: UUID, user: CurrentUser, session: SessionDependency
) -> AnswerResponse:
    try:
        return await read_answer(session, user.id, thread_id, run_id)
    except ConversationNotFound:
        raise HTTPException(
            404, "Conversation not found", headers={"Cache-Control": "no-store"}
        ) from None
    except (SQLAlchemyError, ValueError, TimeoutError, OSError):
        raise HTTPException(
            503, detail={"code": "ANSWER_READ_UNAVAILABLE"}, headers={"Cache-Control": "no-store"}
        ) from None
