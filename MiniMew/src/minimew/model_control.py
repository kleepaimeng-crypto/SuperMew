"""模型配置的唯一管理入口与无 Secret 快照；不创建模型客户端或执行外部请求。"""

import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from minimew.models import ModelAssignment, ModelProfile, Run
from minimew.rag.protocol import RagPolicy


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
    schema_version: Literal[1, 2] = 1
    answer: ProfileSnapshot
    rag: RagPolicy | None = None

    @model_validator(mode="after")
    def policy_version(self):
        if (self.schema_version == 2) != (self.rag is not None):
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


class ModelConfigurationUnavailable(Exception):
    """当前模型未配置或持久配置非法；HTTP 层只返回稳定公开错误。"""


class ModelSnapshotUnavailable(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


async def assignment_row(session: AsyncSession, *, shared: bool) -> ModelAssignment:
    row = await session.scalar(
        select(ModelAssignment)
        .where(ModelAssignment.role == "answer")
        .with_for_update(read=shared)
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise ModelConfigurationUnavailable
    return row


async def freeze_current_snapshot(session: AsyncSession) -> ModelSnapshot:
    """在调用方的 Run 预留事务中读取；与配置写入采用相同锁顺序。"""
    if not session.in_transaction():
        raise RuntimeError("Model snapshot must be frozen inside the reservation transaction")
    assignment = await assignment_row(session, shared=True)
    if assignment.profile_id is None:
        raise ModelConfigurationUnavailable
    profile = await session.scalar(
        select(ModelProfile)
        .where(ModelProfile.id == assignment.profile_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if profile is None:
        raise ModelConfigurationUnavailable
    try:
        configuration = ModelConfiguration.model_validate(profile.configuration)
        return ModelSnapshot(
            schema_version=2,
            rag=RagPolicy(strategy="hybrid"),
            answer=ProfileSnapshot(
                profile_id=profile.id, profile_version=profile.version, configuration=configuration
            ),
        )
    except ValidationError:
        raise ModelConfigurationUnavailable from None


async def set_current_model(
    session: AsyncSession, name: str, configuration: ModelConfiguration
) -> ModelSnapshot:
    """本地管理命令与测试均调用此入口；按 name 创建/更新并选择为当前 answer。"""
    # 重新校验，防止调用者使用 model_construct/model_copy 绕过输入检查。
    configuration = ModelConfiguration.model_validate_json(configuration.model_dump_json())
    name = ProfileName(name=name).name
    async with session.begin():
        assignment = await assignment_row(session, shared=False)
        profile = await session.scalar(
            select(ModelProfile)
            .where(ModelProfile.name == name)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        data = configuration.model_dump(mode="json")
        if profile is None:
            profile = ModelProfile(name=name, version=1, configuration=data)
            session.add(profile)
            await session.flush()
        elif profile.configuration != data:
            profile.configuration = data
            profile.version += 1
        assignment.profile_id = profile.id
        await session.flush()
        result = await freeze_current_snapshot(session)
    return result


class ProfileName(FrozenModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")


def snapshot_for_run(run: Run) -> ModelSnapshot:
    """未来执行器只能读取此 Run 的副本；禁止用当前配置补猜旧数据。"""
    if run.model_snapshot_json is None or run.model_catalog_hash is None:
        raise ModelSnapshotUnavailable("MODEL_SNAPSHOT_MISSING")
    try:
        snapshot = ModelSnapshot.model_validate_json(
            json.dumps(run.model_snapshot_json, allow_nan=False)
        )
    except (ValidationError, ValueError, TypeError):
        raise ModelSnapshotUnavailable("MODEL_SNAPSHOT_INVALID") from None
    if snapshot.catalog_hash() != run.model_catalog_hash:
        raise ModelSnapshotUnavailable("MODEL_SNAPSHOT_INVALID")
    return snapshot
