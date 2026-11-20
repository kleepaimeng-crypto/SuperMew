"""受控持久评测协议v2；不加载在线模型或连接数据库。"""

from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field, model_validator

from minimew.evaluation.protocol import (
    Dataset,
    FrozenModel,
    Identifier,
    Text,
    unique,
)
from minimew.evaluation.result_protocol import CorpusV2 as CorpusV2
from minimew.evaluation.result_protocol import JudgeResult as JudgeResult
from minimew.evaluation.result_protocol import SourceTextV2 as SourceTextV2
from minimew.hitl.policy import HitlPolicy
from minimew.model_protocol import ModelConfiguration, ModelSnapshot, ProfileSnapshot
from minimew.rag.protocol import BAILIAN_RERANKER, RagPolicy
from minimew.rag.reasoning import ReasoningPolicy, RepairPolicy


def controlled_snapshot():
    return ModelSnapshot(
        schema_version=5,
        answer=ProfileSnapshot(
            profile_id=uuid5(NAMESPACE_URL, "minimew:controlled-evaluation-v1"),
            profile_version=1,
            configuration=ModelConfiguration(
                provider="openai_compatible",
                model_name="controlled-evaluation-v1",
                base_url="https://controlled.example.invalid/v1",
                temperature=0.0,
                max_output_tokens=1024,
                timeout_seconds=120.0,
                thinking={"type": "disabled"},
            ),
        ),
        rag=RagPolicy(),
        reasoning=ReasoningPolicy(),
        repairs=RepairPolicy(finalization="reserved-v1"),
        hitl=HitlPolicy(),
    )


class RoleReply(FrozenModel):
    purpose: Literal["grade", "plan", "rewrite"]
    payload: dict


class Scenario(FrozenModel):
    case_id: Identifier
    roles: tuple[RoleReply, ...] = Field(max_length=10)
    answer: Text
    repair_answer: Text | None = None
    expected_prompt: str | None = Field(default=None, min_length=1, max_length=1024)
    supplement: str | None = Field(default=None, min_length=1, max_length=1024)
    judge: JudgeResult | None = None
    judge_fault: Literal["timeout", "invalid", "transient"] | None = None

    @model_validator(mode="after")
    def paired(self):
        if (self.expected_prompt is None) != (self.supplement is None):
            raise ValueError("Clarification input requires an exact trigger contract")
        return self


class JobRequest(FrozenModel):
    schema_version: Literal[2] = 2
    corpus_id: UUID
    dataset: Dataset
    scenarios: tuple[Scenario, ...] = Field(min_length=1)
    case_seconds: int = Field(default=120, ge=10, le=600)
    job_seconds: int = Field(default=1800, ge=30, le=7200)
    snapshot: ModelSnapshot = Field(default_factory=controlled_snapshot)
    judge_minimum: int = Field(default=2, ge=0, le=2)
    total_model_limit: int = Field(default=112, ge=1, le=10000)

    @model_validator(mode="after")
    def complete(self):
        if self.snapshot.schema_version != 5 or self.snapshot.repairs.finalization != "reserved-v1":
            raise ValueError("Evaluation requires frozen v5 reserved finalization")
        if self.snapshot.answer.configuration != controlled_snapshot().answer.configuration:
            raise ValueError("Only controlled generation is enabled")
        if self.snapshot.rag.reranker == BAILIAN_RERANKER:
            raise ValueError("API reranking requires an evaluation model binding")
        unique([s.case_id for s in self.scenarios], "scenario")
        if {c.case_id for c in self.dataset.cases} != {s.case_id for s in self.scenarios}:
            raise ValueError("Scenario directory must exactly match dataset")
        return self
