from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict


class HitlSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MINIMEW_HITL_")
    enabled: bool = True


class HitlPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    clarification_limit: Literal[1] = 1
    wait_seconds: Literal[900] = 900


class PauseRequested(Exception):
    """仅在图已产生可恢复检查点后向外层传播，不是工具失败。"""

    def __init__(self, prompt, graph):
        self.prompt, self.graph = prompt, graph


class CheckpointExpired(Exception):
    """业务等待期限耗尽，与数据库/网络 TimeoutError 分开。"""
