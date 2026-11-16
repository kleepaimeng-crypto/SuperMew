"""纯评分：来源区间覆盖、结果与引用结构；不推断语义正确性。"""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from minimew.evaluation.protocol import (
    Code,
    Corpus,
    Counts,
    Dataset,
    Digest,
    FrozenModel,
    Identifier,
    ObservationSet,
    Outcome,
    Provenance,
    Span,
    fingerprint,
    unique,
    validate_inputs,
)

Rate = Annotated[float, Field(ge=0, le=1)]
SCORER_VERSION = "source-span-v1"


class Metric(FrozenModel):
    value: Rate | None
    eligible: int = Field(ge=0)
    evaluated: int = Field(ge=0)
    errors: int = Field(ge=0)
    unavailable: int = Field(ge=0)

    @model_validator(mode="after")
    def consistent(self):
        if self.eligible != self.evaluated + self.errors + self.unavailable:
            raise ValueError("Metric denominator mismatch")
        if (self.value is None) != (self.evaluated == 0):
            raise ValueError("Missing or fabricated metric")
        return self


class CaseScore(FrozenModel):
    case_id: Identifier
    kind: Literal["quality", "fault"]
    critical: bool
    outcome: Outcome
    error_code: Code | None
    outcome_match: bool
    gold_count: int = Field(ge=0)
    covered_count: int = Field(ge=0)
    missing_gold: tuple[Span, ...]
    material_coverage: Rate | None
    citation_structure: bool | None
    budget_ok: bool
    counts: Counts
    semantic_correctness: None = None

    @model_validator(mode="after")
    def consistent(self):
        if self.gold_count != self.covered_count + len(self.missing_gold):
            raise ValueError("Coverage count mismatch")
        expected = (
            self.covered_count / self.gold_count
            if self.gold_count and self.outcome not in {"error", "waiting", "cancelled"}
            else None
        )
        if self.material_coverage != expected:
            raise ValueError("Coverage value mismatch")
        if (self.outcome == "error") != (self.error_code is not None):
            raise ValueError("Error status mismatch")
        return self


class Metrics(FrozenModel):
    material_coverage: Metric
    outcome_match: Metric
    citation_structure: Metric
    budget_ok: Metric
    semantic_correctness: Metric


class Summary(FrozenModel):
    expected: int = Field(ge=0)
    observed: int = Field(ge=0)
    completed: int = Field(ge=0)
    waiting: int = Field(ge=0)
    cancelled: int = Field(ge=0)
    errors: int = Field(ge=0)
    missing: Literal[0] = 0
    counts: Counts
    metrics: Metrics


def metric(cases, field, eligible=lambda c: True):
    selected = [c for c in cases if eligible(c)]
    values = [float(v) for c in selected if (v := getattr(c, field)) is not None]
    errors = sum(c.outcome == "error" and getattr(c, field) is None for c in selected)
    return Metric(
        value=sum(values) / len(values) if values else None,
        eligible=len(selected),
        evaluated=len(values),
        errors=errors,
        unavailable=len(selected) - len(values) - errors,
    )


def summarize(cases) -> Summary:
    return Summary(
        expected=len(cases),
        observed=len(cases),
        completed=sum(c.outcome not in {"error", "waiting", "cancelled"} for c in cases),
        waiting=sum(c.outcome == "waiting" for c in cases),
        cancelled=sum(c.outcome == "cancelled" for c in cases),
        errors=sum(c.outcome == "error" for c in cases),
        counts=Counts(**{k: sum(getattr(c.counts, k) for c in cases) for k in Counts.model_fields}),
        metrics=Metrics(
            material_coverage=metric(cases, "material_coverage", lambda c: c.gold_count > 0),
            outcome_match=metric(cases, "outcome_match"),
            citation_structure=metric(
                cases, "citation_structure", lambda c: c.citation_structure is not None
            ),
            budget_ok=metric(cases, "budget_ok"),
            semantic_correctness=metric(cases, "semantic_correctness"),
        ),
    )


class Report(FrozenModel):
    schema_version: Literal[1] = 1
    scorer_version: Literal["source-span-v1"] = SCORER_VERSION
    dataset_fingerprint: Digest
    corpus_fingerprint: Digest
    observation_fingerprint: Digest
    provenance: Provenance
    cases: tuple[CaseScore, ...] = Field(min_length=1)
    summary: Summary
    quality: Summary
    fault: Summary

    @model_validator(mode="after")
    def consistent(self):
        unique([c.case_id for c in self.cases], "scored case")
        if list(self.cases) != sorted(self.cases, key=lambda c: c.case_id):
            raise ValueError("Unstable case ordering")
        for name, values in (
            ("summary", self.cases),
            ("quality", [c for c in self.cases if c.kind == "quality"]),
            ("fault", [c for c in self.cases if c.kind == "fault"]),
        ):
            if getattr(self, name) != summarize(values):
                raise ValueError("Report aggregate disagrees with case facts")
        return self


def covered(gold: Span, materials) -> bool:
    cursor = gold.start
    intervals = sorted((m.start, m.end) for m in materials if m.source_id == gold.source_id)
    for start, end in intervals:
        if start > cursor:
            break
        cursor = max(cursor, end)
        if cursor >= gold.end:
            return True
    return False


def score(dataset: Dataset, corpus: Corpus, observations: ObservationSet) -> Report:
    validate_inputs(dataset, corpus, observations)
    actual = {o.case_id: o for o in observations.cases}
    scores = []
    for case in sorted(dataset.cases, key=lambda c: c.case_id):
        observation = actual[case.case_id]
        complete = observation.outcome not in {"error", "waiting", "cancelled"}
        missing = tuple(
            s for s in case.evidence_gold if not complete or not covered(s, observation.materials)
        )
        count = len(case.evidence_gold) - len(missing)
        material_ids = {m.material_id for m in observation.materials}
        citations = observation.citation_sources
        citations_valid = (
            set(citations) <= material_ids and (not case.requires_citations or bool(citations))
            if case.requires_citations or citations
            else None
        )
        scores.append(
            CaseScore(
                case_id=case.case_id,
                kind=case.kind,
                critical=case.critical,
                outcome=observation.outcome,
                error_code=observation.error_code,
                outcome_match=observation.outcome in case.allowed_outcomes
                and observation.error_code == case.expected_error,
                gold_count=len(case.evidence_gold),
                covered_count=count,
                missing_gold=missing,
                material_coverage=count / len(case.evidence_gold)
                if case.evidence_gold and complete
                else None,
                citation_structure=citations_valid,
                budget_ok=all(
                    getattr(observation.counts, k) <= getattr(observation.budget, k)
                    for k in Counts.model_fields
                ),
                counts=observation.counts,
            )
        )
    cases = tuple(scores)
    return Report(
        dataset_fingerprint=fingerprint(dataset),
        corpus_fingerprint=dataset.corpus_fingerprint(),
        observation_fingerprint=fingerprint(observations),
        provenance=observations.provenance,
        cases=cases,
        summary=summarize(cases),
        quality=summarize([c for c in cases if c.kind == "quality"]),
        fault=summarize([c for c in cases if c.kind == "fault"]),
    )
