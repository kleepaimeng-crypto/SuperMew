from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from minimew.auth_http import REFRESH_COOKIE, clear_refresh_cookie, set_refresh_cookie
from minimew.database import SessionDependency
from minimew.models import User
from minimew.refresh import issue_pair, revoke_refresh, rotate_refresh
from minimew.security import hash_password, verify_access_token, verify_password
from minimew.settings import SettingsDependency

router = APIRouter(prefix="/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-z0-9_]+$")
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class RegisterRequest(LoginRequest):
    password: SecretStr = Field(min_length=12, max_length=128)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    username: str
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


def unauthorized() -> HTTPException:
    return HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Bearer"})


@dataclass(frozen=True, slots=True)
class AuthenticatedAccess:
    user: User
    expires_at: datetime


async def get_current_access(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: SettingsDependency,
    session: SessionDependency,
) -> AuthenticatedAccess:
    if credentials is None:
        raise unauthorized()
    try:
        access = verify_access_token(credentials.credentials, settings)
    except InvalidTokenError:
        raise unauthorized() from None
    async with session.begin():
        user = await session.get(User, access.user_id)
    if user is None:
        raise unauthorized()
    return AuthenticatedAccess(user=user, expires_at=access.expires_at)


CurrentAccess = Annotated[AuthenticatedAccess, Depends(get_current_access)]


async def get_current_user(access: CurrentAccess) -> User:
    return access.user


CurrentUser = Annotated[User, Depends(get_current_user)]


@router.post("/register", status_code=201, response_model=UserResponse)
async def register(data: RegisterRequest, session: SessionDependency) -> UserResponse:
    password_hash = await run_in_threadpool(hash_password, data.password.get_secret_value())
    try:
        async with session.begin():
            user = User(username=data.username, password_hash=password_hash)
            session.add(user)
            await session.flush()
            result = UserResponse.model_validate(user)
    except IntegrityError as error:
        cause = error.orig.__cause__
        if getattr(cause, "constraint_name", None) == "uq_users_username":
            raise HTTPException(409, "Username unavailable") from None
        raise
    return result


@router.post("/login", response_model=TokenResponse)
async def login(
    data: LoginRequest,
    request: Request,
    response: Response,
    session: SessionDependency,
    settings: SettingsDependency,
) -> TokenResponse:
    async with session.begin():
        user = await session.scalar(select(User).where(User.username == data.username))
    # 不存在的用户也执行一次哈希验证；统一错误响应，不回显用户名。
    encoded = user.password_hash if user is not None else request.app.state.dummy_password_hash
    valid = await run_in_threadpool(verify_password, data.password.get_secret_value(), encoded)
    if user is None or not valid:
        raise unauthorized()
    # 密码计算不占用行锁；验证完成后按统一 User → RefreshToken 锁序签发。
    async with session.begin():
        locked_user = await session.scalar(
            select(User)
            .where(User.id == user.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked_user is None or locked_user.password_hash != encoded:
            raise unauthorized()
        now = await session.scalar(select(func.clock_timestamp()))
        pair = await issue_pair(session, locked_user.id, settings, now)
    set_refresh_cookie(response, pair, settings)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return TokenResponse(
        access_token=pair.access_token,
        expires_in=settings.access_token_minutes * 60,
    )


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser, response: Response) -> UserResponse:
    response.headers["Cache-Control"] = "no-store"
    return UserResponse.model_validate(user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request, response: Response, session: SessionDependency, settings: SettingsDependency
):
    pair = await rotate_refresh(session, request.cookies.get(REFRESH_COOKIE), settings)
    if pair is None:
        # rotate_refresh 已提交可能发生的重放撤销；不在事务中抛出 HTTPException。
        failure = JSONResponse(status_code=401, content={"detail": "Invalid refresh token"})
        clear_refresh_cookie(failure, settings)
        return failure
    set_refresh_cookie(response, pair, settings)
    return TokenResponse(
        access_token=pair.access_token, expires_in=settings.access_token_minutes * 60
    )


@router.post("/logout", status_code=204)
async def logout(
    request: Request, session: SessionDependency, settings: SettingsDependency
) -> Response:
    await revoke_refresh(session, request.cookies.get(REFRESH_COOKIE))
    response = Response(status_code=204)
    clear_refresh_cookie(response, settings)
    return response
