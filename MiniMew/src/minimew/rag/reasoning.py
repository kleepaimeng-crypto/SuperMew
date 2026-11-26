"""B包冻结配置与严格评判契约；无网络或运行时状态。"""

import re
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
    structured_output: Literal["json-repair-v1"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class RepairSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MINIMEW_REPAIRS_")
    enabled: bool = True


class RepairPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    answer_repair_limit: Literal[1] = 1
    aggregation: Literal["representatives-v1"] = "representatives-v1"
    finalization: Literal["reserved-v1"] | None = None


class FinalizationReserved(Exception):
    """计算尚未超额，但剩余调用只供最终回答与格式纠正使用。"""


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
    try:
        result = await caller(
            "grade",
            {
                "question": question,
                "materials": [m.model_dump(mode="json") for m in draft.matches],
                "aspects": aspects,
            },
            Grade,
        )
    except FinalizationReserved:
        return Grade(
            status="insufficient",
            supported=(),
            missing=("模型预算已为最终回答及格式纠正预留，材料充分性未完成验证",),
        )
    validate_grade(result, {m.citation_id for m in draft.matches})
    return result


def validate_grade(result: Grade, ids: set[str]):
    from minimew.execution.diagnostics import Diagnostic
    from minimew.execution.provider import ProviderFailure

    reason = (
        "unknown_evidence_reference"
        if not set(result.supported) <= ids
        else "inconsistent_assessment"
        if result.status == "sufficient" and (not result.supported or result.missing)
        else None
    )
    if reason:
        raise ProviderFailure("PROVIDER_INVALID_RESPONSE", diagnostic=Diagnostic("grade", reason))


_COMPACT_RE = re.compile(r"[\s\-._/]+")
_ANCHOR_RE = re.compile(r"[A-Za-z0-9]+(?:[-._/][A-Za-z0-9]+)*")


def _compact(text: str) -> str:
    return _COMPACT_RE.sub("", text).lower()


def missing_anchors(question: str, query: str) -> tuple[str, ...]:
    folded_query = _compact(query)
    seen: set[str] = set()
    missing: list[str] = []
    for raw in _ANCHOR_RE.findall(question):
        token = _compact(raw)
        if token and token not in seen and token not in folded_query:
            seen.add(token)
            missing.append(raw)
    return tuple(missing)


def validate_rewrite(result: Rewrite, question: str) -> str:
    from minimew.execution.diagnostics import Diagnostic
    from minimew.execution.provider import ProviderFailure

    query = result.query.strip()
    if not query or "\x00" in query:
        raise ProviderFailure(
            "PROVIDER_INVALID_RESPONSE",
            diagnostic=Diagnostic(
                "rewrite",
                "invalid_rewrite_query",
                issues=({"field": "query", "rule": "non_empty"},),
            ),
        )
    if missing_anchors(question, query):
        raise ProviderFailure(
            "PROVIDER_INVALID_RESPONSE",
            diagnostic=Diagnostic(
                "rewrite",
                "missing_anchor",
                issues=({"field": "query", "rule": "missing_anchor"},),
            ),
        )
    return query


def validate_plan(result: Plan) -> tuple[str, ...]:
    from minimew.execution.diagnostics import Diagnostic
    from minimew.execution.provider import ProviderFailure

    queries = tuple(q.strip() for q in result.questions)
    if any(not q or len(q) > 1024 or "\x00" in q for q in queries):
        raise ProviderFailure(
            "PROVIDER_INVALID_RESPONSE",
            diagnostic=Diagnostic(
                "plan",
                "invalid_subquestion",
                issues=({"field": "questions", "rule": "non_empty"},),
            ),
        )
    if len(set(queries)) != len(queries):
        raise ProviderFailure(
            "PROVIDER_INVALID_RESPONSE",
            diagnostic=Diagnostic(
                "plan",
                "duplicate_subquestion",
                issues=({"field": "questions", "rule": "unique"},),
            ),
        )
    return queries
