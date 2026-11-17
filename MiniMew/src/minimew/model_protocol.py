"""无Secret冻结模型契约；不导入数据库或创建客户端。"""

import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

from minimew.hitl.policy import HitlPolicy
from minimew.rag.protocol import RagPolicy
from minimew.rag.reasoning import ReasoningPolicy, RepairPolicy


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, hide_input_in_errors=True, allow_inf_nan=False
    )


class ThinkingPolicy(FrozenModel):
    type: Literal["disabled"]


class ModelConfiguration(FrozenModel):
    provider: Literal["openai_compatible"]
    model_name: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
    base_url: str = Field(min_length=1, max_length=2048)
    temperature: float = Field(ge=0, le=2)
    max_output_tokens: int = Field(ge=1, le=65536)
    timeout_seconds: float = Field(gt=0, le=300)
    thinking: ThinkingPolicy

    @field_validator("base_url")
    @classmethod
    def credential_free_url(cls, value: str) -> str:
        url = AnyHttpUrl(value)
        if url.username is not None or url.password is not None or url.query is not None:
            raise ValueError("Provider URL must not contain credentials or a query")
        if url.fragment is not None:
            raise ValueError("Provider URL must not contain a fragment")
        return str(url).rstrip("/")


class ProfileSnapshot(FrozenModel):
    profile_id: UUID
    profile_version: int = Field(ge=1)
    configuration: ModelConfiguration


class ModelSnapshot(FrozenModel):
    schema_version: Literal[1, 2, 3, 4, 5] = 1
    answer: ProfileSnapshot
    rag: RagPolicy | None = None
    reasoning: ReasoningPolicy | None = None
    repairs: RepairPolicy | None = None
    hitl: HitlPolicy | None = None

    @model_validator(mode="after")
    def policy_version(self):
        if (
            (self.schema_version >= 2) != (self.rag is not None)
            or (self.schema_version >= 3) != (self.reasoning is not None)
            or (self.schema_version >= 4) != (self.repairs is not None)
            or (self.schema_version == 5) != (self.hitl is not None)
        ):
            raise ValueError("Snapshot strategy/version mismatch")
        return self

    def catalog_hash(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json", exclude_none=True),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
