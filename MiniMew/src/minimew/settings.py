"""只读取 MINIMEW_ 环境变量，不自动加载上级项目的 .env。"""

from typing import Annotated, Literal

from fastapi import Depends, Request
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MINIMEW_", frozen=True, hide_input_in_errors=True)

    app_name: str = Field(default="MiniMew", min_length=1)
    environment: Literal["development", "test", "production"] = "development"
    db_host: str = Field(default="127.0.0.1", min_length=1)
    db_port: int = Field(default=55432, ge=1, le=65535)
    db_name: str = Field(default="minimew", min_length=1)
    db_user: str = Field(default="minimew", min_length=1)
    db_password: SecretStr = Field(min_length=1)
    db_timeout_seconds: float = Field(default=3, gt=0, le=30)
    redis_host: str = Field(default="127.0.0.1", min_length=1)
    redis_port: int = Field(default=56379, ge=1, le=65535)
    redis_password: SecretStr = Field(min_length=1)
    redis_key_prefix: str = Field(
        default="minimew:run_events:v1", pattern=r"^minimew:[A-Za-z0-9:_-]+$"
    )
    redis_stream_maxlen: int = Field(default=1000, ge=1, le=100000)
    redis_stream_ttl_seconds: int = Field(default=86400, ge=1, le=604800)
    notification_scan_seconds: float = Field(default=0.5, gt=0, le=30)
    event_poll_seconds: float = Field(default=5, gt=0, le=30)
    deepseek_api_key: SecretStr = SecretStr("")
    execution_scan_seconds: float = Field(default=0.5, gt=0, le=30)
    execution_max_active: int = Field(default=2, ge=1, le=32)
    cancellation_poll_seconds: float = Field(default=0.5, gt=0, le=30)
    jwt_secret: SecretStr = Field(min_length=32)
    access_token_minutes: int = Field(default=15, ge=1, le=60)
    refresh_token_days: int = Field(default=7, ge=1, le=30)
    refresh_cookie_secure: bool = False

    @property
    def model_credentials_available(self) -> bool:
        return bool(self.deepseek_api_key.get_secret_value().strip())

    @model_validator(mode="after")
    def production_cookie_security(self):
        if self.environment == "production" and not self.refresh_cookie_secure:
            raise ValueError("Production requires a Secure refresh cookie")
        return self


async def get_settings(request: Request) -> Settings:
    """取得本次请求所属应用的配置，不在每个请求里重新读取环境变量。"""
    return request.app.state.settings


SettingsDependency = Annotated[Settings, Depends(get_settings)]
