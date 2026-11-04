"""Refresh 生命周期：每条写路径持有 User 锁；错误结果在事务提交后交给 HTTP 层。"""

import hashlib
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from minimew.models import RefreshToken, User
from minimew.security import issue_access_token
from minimew.settings import Settings


@dataclass(frozen=True)
class TokenPair:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: datetime


def refresh_digest(raw: str | None) -> str | None:
    if raw is None or re.fullmatch(r"[A-Za-z0-9_-]{43}", raw) is None:
        return None
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


async def issue_pair(
    session: AsyncSession, user_id: UUID, settings: Settings, now: datetime
) -> TokenPair:
    """调用方必须在当前事务内先持有该 User 的 FOR UPDATE 锁。"""
    raw = secrets.token_urlsafe(32)
    expires_at = now + timedelta(days=settings.refresh_token_days)
    pair = TokenPair(issue_access_token(user_id, settings), raw, expires_at)
    session.add(
        RefreshToken(
            token_hash=refresh_digest(raw),
            user_id=user_id,
            created_at=now,
            expires_at=expires_at,
        )
    )
    await session.flush()
    return pair


async def locked_refresh(session: AsyncSession, digest: str) -> RefreshToken | None:
    # 首次读取只定位用户，不授予使用凭据的权限。
    user_id = await session.scalar(
        select(RefreshToken.user_id).where(RefreshToken.token_hash == digest)
    )
    if user_id is None:
        return None
    owner = await session.scalar(select(User.id).where(User.id == user_id).with_for_update())
    if owner is None:
        return None
    return await session.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == digest)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def rotate_refresh(
    session: AsyncSession, raw: str | None, settings: Settings
) -> TokenPair | None:
    digest = refresh_digest(raw)
    if digest is None:
        return None
    pair = None
    async with session.begin():
        credential = await locked_refresh(session, digest)
        if credential is not None:
            # 锁等待后读取数据库当前时间，避免用事务起始时间判断过期。
            now = await session.scalar(select(func.clock_timestamp()))
            if credential.expires_at <= now:
                pass  # 必须先判自然过期，不撤销其他有效凭据。
            elif credential.revoked_at is not None:
                await session.execute(
                    update(RefreshToken)
                    .where(
                        RefreshToken.user_id == credential.user_id,
                        RefreshToken.revoked_at.is_(None),
                        RefreshToken.expires_at > now,
                    )
                    .values(revoked_at=now)
                )
            else:
                credential.revoked_at = now
                pair = await issue_pair(session, credential.user_id, settings, now)
    # 重放分支也正常退出事务并提交；HTTP 层此后才能返回 401。
    return pair


async def revoke_refresh(session: AsyncSession, raw: str | None) -> None:
    digest = refresh_digest(raw)
    if digest is None:
        return
    async with session.begin():
        credential = await locked_refresh(session, digest)
        if credential is not None and credential.revoked_at is None:
            credential.revoked_at = await session.scalar(select(func.clock_timestamp()))
