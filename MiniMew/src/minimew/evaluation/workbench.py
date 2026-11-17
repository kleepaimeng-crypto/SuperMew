"""工作台的公开投影；不返回私有检查点或整份人工评分输入。"""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from minimew.evaluation.gate import GatePolicy
from minimew.evaluation.protocol import Counts, FrozenModel, Material
from minimew.evaluation.result_protocol import JudgeResult


class CorpusView(FrozenModel):
    corpus_id: UUID
    source_ids: tuple[str, ...]
    corpus_fingerprint: str
    expires_at: datetime


class CorpusPage(FrozenModel):
    items: tuple[CorpusView, ...]
    next_offset: int | None


class JobSummary(FrozenModel):
    job_id: UUID
    dataset_id: str
    status: str
    created_at: datetime
    decision: str | None


class JobPage(FrozenModel):
    items: tuple[JobSummary, ...]
    next_offset: int | None


class ValidatedJob(FrozenModel):
    status: str = "valid"
    case_count: int
    total_model_limit: int
    mode: str = "controlled"


class CaseDetail(FrozenModel):
    case_id: str
    question: str
    status: str
    answer: str | None
    error_code: str | None
    judge_error: str | None
    counts: Counts | None
    materials: tuple[Material, ...]
    judge: JudgeResult | None
    requested: bool
    accepted: bool
    resumed: bool


class CompareRequest(FrozenModel):
    baseline_id: UUID
    policy: GatePolicy = Field(default_factory=lambda: GatePolicy(allowed_modes=("controlled",)))
