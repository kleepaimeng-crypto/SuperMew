"""B包冻结配置与严格评判契约；无网络或运行时状态。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ReasoningSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MINIMEW_REASONING_")
    enabled: bool = True


class ReasoningPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    model_limit: Literal[12] = 12
    outer_limit: Literal[4] = 4
    tool_limit: Literal[6] = 6
    retrieval_limit: Literal[8] = 8
    concurrency: Literal[2] = 2
    grade_tokens: Literal[1024] = 1024
    plan_tokens: Literal[512] = 512
    rewrite_tokens: Literal[256] = 256


class RepairSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MINIMEW_REPAIRS_")
    enabled: bool = True


class RepairPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    answer_repair_limit: Literal[1] = 1
    aggregation: Literal["representatives-v1"] = "representatives-v1"


class Grade(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    status: Literal["sufficient", "insufficient", "ambiguous", "conflicting"]
    supported: tuple[Annotated[str, Field(pattern=r"^E[1-9][0-9]*$")], ...] = Field(max_length=18)
    missing: tuple[Annotated[str, Field(min_length=1, max_length=256)], ...] = Field(max_length=8)
    decompose: bool = False


class Rewrite(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=1024)


class Plan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    questions: tuple[str, ...] = Field(min_length=2, max_length=4)


async def grade_materials(caller, question, draft, aspects=()):
    result = await caller(
        "grade",
        {
            "question": question,
            "materials": [m.model_dump(mode="json") for m in draft.matches],
            "aspects": aspects,
        },
        Grade,
    )
    ids = {m.citation_id for m in draft.matches}
    if not set(result.supported) <= ids or (
        result.status == "sufficient" and (not result.supported or result.missing)
    ):
        from minimew.execution.provider import ProviderFailure

        raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
    return result
