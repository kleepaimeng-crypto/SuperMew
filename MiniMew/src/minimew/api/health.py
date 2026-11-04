import asyncio
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from minimew.database import SessionDependency
from minimew.settings import SettingsDependency

router = APIRouter(prefix="/health", tags=["health"])


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"
    app_name: str


@router.get("/live", response_model=LivenessResponse)
async def liveness(settings: SettingsDependency) -> LivenessResponse:
    """仅检查 HTTP 服务存活，不表示外部依赖或业务已就绪。"""
    return LivenessResponse(app_name=settings.app_name)


class ReadinessResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    database: Literal["ok", "unavailable"]


@router.get(
    "/ready", response_model=ReadinessResponse, responses={503: {"model": ReadinessResponse}}
)
async def readiness(
    session: SessionDependency, settings: SettingsDependency, response: Response
) -> ReadinessResponse:
    """有界数据库探测；不暴露底层连接异常，也不保证迁移或模型已就绪。"""
    try:
        async with asyncio.timeout(settings.db_timeout_seconds):
            await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError, TimeoutError):
        response.status_code = 503
        return ReadinessResponse(status="unavailable", database="unavailable")
    return ReadinessResponse(status="ok", database="ok")
