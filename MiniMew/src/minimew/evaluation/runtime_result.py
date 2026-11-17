"""v2受控评价产物；v1的纯结构评分与旧资产不改义。"""

from typing import Literal

from pydantic import Field, model_validator

from minimew.evaluation.gate import EvaluationArtifact, GatePolicy, Rule, decision, evaluate
from minimew.evaluation.protocol import (
    Corpus,
    Dataset,
    FrozenModel,
    Identifier,
    ObservationSet,
    fingerprint,
    unique,
)
from minimew.evaluation.result_protocol import JudgeResult
from minimew.evaluation.scoring import score


class CaseEvaluation(FrozenModel):
    case_id: Identifier
    answer: str | None = Field(default=None, max_length=100000)
    judge: JudgeResult | None = None
    judge_error: str | None = None
    requested: bool = False
    matched: bool = False
    accepted: bool = False
    resumed: bool = False
    clarification_required: bool = False
    judge_attempts: int = Field(default=0, ge=0, le=2)

    @model_validator(mode="after")
    def consistent(self):
        if self.judge is not None and self.judge_error is not None:
            raise ValueError("Judge value and error are distinct")
        if (
            (self.resumed and not self.accepted)
            or (self.accepted and not self.matched)
            or (self.matched and not self.requested)
        ):
            raise ValueError("Invalid clarification sequence")
        return self


class ObservationV2(FrozenModel):
    schema_version: Literal[2] = 2
    structural: ObservationSet
    evaluations: tuple[CaseEvaluation, ...]

    @model_validator(mode="after")
    def consistent(self):
        unique([e.case_id for e in self.evaluations], "evaluation")
        if {e.case_id for e in self.evaluations} != {c.case_id for c in self.structural.cases}:
            raise ValueError("Evaluation directory mismatch")
        if self.structural.provenance.mode != "controlled":
            raise ValueError("This runtime only produces controlled observations")
        return self


class JudgeDelta(FrozenModel):
    metric: Literal["correctness", "completeness", "grounding"]
    baseline: float | None
    candidate: float | None
    change: float | None

    @model_validator(mode="after")
    def arithmetic(self):
        expected = (
            None
            if self.baseline is None or self.candidate is None
            else self.candidate - self.baseline
        )
        if self.change != expected:
            raise ValueError("Judge delta arithmetic mismatch")
        return self


class ArtifactV2(FrozenModel):
    schema_version: Literal[2] = 2
    scorer_version: Literal["controlled-judge-v2"] = "controlled-judge-v2"
    source_stages: Literal["local-retrieval/controlled-generation/controlled-judge"] = (
        "local-retrieval/controlled-generation/controlled-judge"
    )
    structural: EvaluationArtifact
    evaluations: tuple[CaseEvaluation, ...]
    judge_minimum: int = Field(ge=0, le=2)
    decision: Literal["pass", "fail", "insufficient", "incomparable"]
    total_model_attempts: int = Field(ge=0)
    baseline_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    judge_deltas: tuple[JudgeDelta, ...] = ()

    @model_validator(mode="after")
    def consistent(self):
        unique([e.case_id for e in self.evaluations], "evaluation")
        unique([d.metric for d in self.judge_deltas], "Judge delta")
        if self.judge_deltas and self.baseline_fingerprint is None:
            raise ValueError("Judge comparison requires explicit baseline")
        if {e.case_id for e in self.evaluations} != {
            c.case_id for c in self.structural.report.cases
        }:
            raise ValueError("Evaluation directory mismatch")
        if self.total_model_attempts != self.structural.report.summary.counts.model_attempts + sum(
            e.judge_attempts for e in self.evaluations
        ):
            raise ValueError("Joint attempt count mismatch")
        expected = result_decision(
            self.structural, self.evaluations, self.judge_minimum, self.judge_deltas
        )
        if expected != self.decision:
            raise ValueError("Decision disagrees with stage results")
        return self


def result_decision(structural, evaluations, minimum, deltas=()):
    statuses = [structural.gate.decision]
    if any(d.change is None for d in deltas):
        statuses.append("insufficient")
    if any(d.change is not None and d.change < 0 for d in deltas):
        statuses.append("fail")
    cases = {c.case_id: c for c in structural.report.cases}
    for result in evaluations:
        if result.clarification_required and not result.resumed:
            statuses.append("fail")
        if cases[result.case_id].kind == "fault" or cases[result.case_id].outcome in {
            "error",
            "waiting",
            "cancelled",
        }:
            continue
        if result.judge is None:
            statuses.append("insufficient")
        elif (
            min(result.judge.correctness, result.judge.completeness, result.judge.grounding)
            < minimum
        ):
            statuses.append("fail")
    return next((s for s in ("incomparable", "fail", "insufficient") if s in statuses), "pass")


def score_v2(dataset: Dataset, corpus: Corpus, observations: ObservationV2, minimum=2, policy=None):
    report = score(dataset, corpus, observations.structural)
    artifact = EvaluationArtifact(
        report=report, gate=evaluate(report, policy or GatePolicy(allowed_modes=("controlled",)))
    )
    return ArtifactV2(
        structural=artifact,
        evaluations=observations.evaluations,
        judge_minimum=minimum,
        decision=result_decision(artifact, observations.evaluations, minimum),
        total_model_attempts=report.summary.counts.model_attempts
        + sum(e.judge_attempts for e in observations.evaluations),
    )


def compare_v2(candidate: ArtifactV2, baseline: ArtifactV2, policy):
    gate = evaluate(candidate.structural.report, policy, baseline.structural.report)
    if candidate.judge_minimum != baseline.judge_minimum:
        rules = (
            *gate.rules,
            Rule(
                name="compare.judge_policy",
                status="incomparable",
                expected=str(baseline.judge_minimum),
                actual=str(candidate.judge_minimum),
            ),
        )
        gate = gate.model_copy(update={"rules": rules, "decision": decision(rules), "deltas": ()})
    core = EvaluationArtifact(report=candidate.structural.report, gate=gate)
    deltas = []
    if gate.decision != "incomparable":
        quality_ids = {c.case_id for c in candidate.structural.report.cases if c.kind == "quality"}
        for metric in ("correctness", "completeness", "grounding"):
            before = [
                getattr(e.judge, metric)
                for e in baseline.evaluations
                if e.case_id in quality_ids and e.judge
            ]
            after = [
                getattr(e.judge, metric)
                for e in candidate.evaluations
                if e.case_id in quality_ids and e.judge
            ]
            previous = (
                sum(before) / len(quality_ids)
                if quality_ids and len(before) == len(quality_ids)
                else None
            )
            current = (
                sum(after) / len(quality_ids)
                if quality_ids and len(after) == len(quality_ids)
                else None
            )
            deltas.append(
                JudgeDelta(
                    metric=metric,
                    baseline=previous,
                    candidate=current,
                    change=current - previous
                    if previous is not None and current is not None
                    else None,
                )
            )
    return ArtifactV2(
        structural=core,
        evaluations=candidate.evaluations,
        judge_minimum=candidate.judge_minimum,
        judge_deltas=tuple(deltas),
        baseline_fingerprint=fingerprint(baseline),
        total_model_attempts=candidate.total_model_attempts,
        decision=result_decision(core, candidate.evaluations, candidate.judge_minimum, deltas),
    )
