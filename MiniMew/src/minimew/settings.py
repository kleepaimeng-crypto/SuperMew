"""只读取 MINIMEW_ 环境变量，不自动加载上级项目的 .env。"""

from pathlib import Path
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
    credential_master_key: SecretStr = SecretStr("")
    default_model_name: str = "deepseek-flash"
    default_model_base_url: str = "https://api.deepseek.com"
    default_model_adapter: Literal["deepseek", "kimi", "qwen", "generic"] = "deepseek"
    default_model_max_tokens: int = Field(default=4096, ge=1, le=65536)
    default_model_timeout: float = Field(default=120, gt=0, le=300)
    execution_scan_seconds: float = Field(default=0.5, gt=0, le=30)
    execution_max_active: int = Field(default=2, ge=1, le=32)
    cancellation_poll_seconds: float = Field(default=0.5, gt=0, le=30)
    execution_lease_seconds: float = Field(default=20, gt=0, le=60)
    execution_heartbeat_seconds: float = Field(default=5, gt=0, le=30)
    jwt_secret: SecretStr = Field(min_length=32)
    access_token_minutes: int = Field(default=15, ge=1, le=60)
    refresh_token_days: int = Field(default=7, ge=1, le=30)
    refresh_cookie_secure: bool = False

    document_upload_dir: Path = Path("data/documents")
    document_max_upload_bytes: int = Field(default=5 * 1024 * 1024, ge=1, le=50 * 1024 * 1024)
    embedding_profile: Literal["bge-small-zh-v1.5", "bge-m3"] = "bge-small-zh-v1.5"
    knowledge_enabled: bool = False
    local_models_enabled: bool = True
    diagnostic_log_dir: Path = Path(".artifacts/logs")
    document_index_layout: Literal["dense-cosine-v1", "hybrid-jieba-bm25-v1"] = (
        "hybrid-jieba-bm25-v1"
    )

    @property
    def model_credentials_available(self) -> bool:
        return bool(self.deepseek_api_key.get_secret_value().strip())

    @model_validator(mode="after")
    def production_cookie_security(self):
        if self.execution_heartbeat_seconds >= self.execution_lease_seconds:
            raise ValueError("Heartbeat must be shorter than execution lease")
        if self.environment == "production" and not self.refresh_cookie_secure:
            raise ValueError("Production requires a Secure refresh cookie")
        return self


async def get_settings(request: Request) -> Settings:
    """取得本次请求所属应用的配置，不在每个请求里重新读取环境变量。"""
    return request.app.state.settings


SettingsDependency = Annotated[Settings, Depends(get_settings)]
