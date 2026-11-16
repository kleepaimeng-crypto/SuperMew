"""可比性与逐条接受政策；不自动选取baseline或触发部署。"""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from minimew.evaluation.protocol import Digest, FrozenModel, Mode, fingerprint, unique
from minimew.evaluation.scoring import Rate, Report

MetricName = Literal[
    "material_coverage", "outcome_match", "citation_structure", "budget_ok", "semantic_correctness"
]
Variable = Literal["implementation", "dependencies", "model", "strategy", "index"]
Status = Literal["pass", "fail", "unavailable", "incomparable"]


class Threshold(FrozenModel):
    metric: MetricName
    minimum: Rate


class GatePolicy(FrozenModel):
    schema_version: Literal[1] = 1
    allowed_modes: tuple[Mode, ...] = ("contract_smoke",)
    required_metrics: tuple[MetricName, ...] = (
        "material_coverage",
        "outcome_match",
        "citation_structure",
    )
    thresholds: tuple[Threshold, ...] = (
        Threshold(metric="material_coverage", minimum=1.0),
        Threshold(metric="outcome_match", minimum=1.0),
        Threshold(metric="citation_structure", minimum=1.0),
    )
    require_baseline: bool = False
    permitted_changes: tuple[Variable, ...] = ()
    regression_metrics: tuple[MetricName, ...] = ("material_coverage", "outcome_match")

    @model_validator(mode="after")
    def consistent(self):
        for name in (
            "allowed_modes",
            "required_metrics",
            "permitted_changes",
            "regression_metrics",
        ):
            unique(getattr(self, name), name)
        if not self.allowed_modes or not self.required_metrics:
            raise ValueError("Source and metric requirements cannot be empty")
        unique([t.metric for t in self.thresholds], "threshold")
        if any(t.metric not in self.required_metrics for t in self.thresholds):
            raise ValueError("Threshold must name a required metric")
        return self


class Rule(FrozenModel):
    name: str = Field(min_length=1, max_length=200)
    status: Status
    expected: str = Field(min_length=1, max_length=200)
    actual: str = Field(min_length=1, max_length=200)


class Difference(FrozenModel):
    field: Variable
    baseline: Digest
    candidate: Digest
    permitted: bool


class Delta(FrozenModel):
    metric: MetricName
    baseline: Rate | None
    candidate: Rate | None
    change: Annotated[float, Field(ge=-1, le=1)] | None


def decision(rules):
    statuses = {r.status for r in rules}
    if "incomparable" in statuses:
        return "incomparable"
    if "fail" in statuses:
        return "fail"
    if "unavailable" in statuses:
        return "insufficient"
    return "pass"


class GateResult(FrozenModel):
    schema_version: Literal[1] = 1
    report_fingerprint: Digest
    policy_fingerprint: Digest
    baseline_fingerprint: Digest | None
    decision: Literal["pass", "fail", "insufficient", "incomparable"]
    rules: tuple[Rule, ...] = Field(min_length=1)
    differences: tuple[Difference, ...]
    deltas: tuple[Delta, ...]

    @model_validator(mode="after")
    def consistent(self):
        unique([r.name for r in self.rules], "gate rule")
        if self.decision != decision(self.rules):
            raise ValueError("Gate decision disagrees with rules")
        for delta in self.deltas:
            expected = (
                delta.candidate - delta.baseline
                if delta.candidate is not None and delta.baseline is not None
                else None
            )
            if delta.change != expected:
                raise ValueError("Delta mismatch")
        if self.decision == "incomparable" and self.deltas:
            raise ValueError("Incomparable reports cannot have deltas")
        return self


class EvaluationArtifact(FrozenModel):
    schema_version: Literal[1] = 1
    report: Report
    gate: GateResult

    @model_validator(mode="after")
    def consistent(self):
        if self.gate.report_fingerprint != fingerprint(self.report):
            raise ValueError("Gate belongs to another report")
        return self


def evaluate(report: Report, policy: GatePolicy, baseline: Report | None = None) -> GateResult:
    rules, differences, deltas = [], [], []

    def rule(name, ok, expected, actual, failure="fail"):
        rules.append(
            Rule(
                name=name,
                status="pass" if ok else failure,
                expected=str(expected),
                actual=str(actual),
            )
        )

    rule(
        "source.mode",
        report.provenance.mode in policy.allowed_modes,
        ",".join(policy.allowed_modes),
        report.provenance.mode,
    )
    quality = report.quality
    rule(
        "quality.completed",
        quality.completed == quality.expected,
        quality.expected,
        quality.completed,
    )
    rule("quality.errors", quality.errors == 0, 0, quality.errors)
    rule(
        "fault.outcomes",
        all(c.outcome_match for c in report.cases if c.kind == "fault"),
        True,
        all(c.outcome_match for c in report.cases if c.kind == "fault"),
    )
    rule(
        "all.budget",
        all(c.budget_ok for c in report.cases),
        True,
        all(c.budget_ok for c in report.cases),
    )
    for case in report.cases:
        if not case.critical:
            continue
        rule(f"critical.{case.case_id}.outcome", case.outcome_match, True, case.outcome_match)
        if case.gold_count:
            rule(
                f"critical.{case.case_id}.coverage",
                case.material_coverage == 1,
                1,
                case.material_coverage,
            )
        if case.citation_structure is not None:
            rule(
                f"critical.{case.case_id}.citation",
                case.citation_structure,
                True,
                case.citation_structure,
            )
    thresholds = {t.metric: t.minimum for t in policy.thresholds}
    for name in policy.required_metrics:
        metric = getattr(quality.metrics, name)
        complete = metric.evaluated == metric.eligible and metric.eligible > 0
        rule(
            f"metric.{name}.available",
            complete,
            "all eligible evaluated",
            f"{metric.evaluated}/{metric.eligible}",
            "unavailable",
        )
        if name in thresholds and metric.value is not None:
            rule(
                f"metric.{name}.minimum",
                metric.value >= thresholds[name],
                thresholds[name],
                metric.value,
            )
    if baseline is None:
        rule(
            "baseline.present",
            not policy.require_baseline,
            "required" if policy.require_baseline else "optional",
            "not_provided",
            "unavailable",
        )
    else:
        for name in ("dataset_fingerprint", "corpus_fingerprint", "scorer_version"):
            rule(
                f"compare.{name}",
                getattr(report, name) == getattr(baseline, name),
                getattr(baseline, name),
                getattr(report, name),
                "incomparable",
            )
        rule(
            "compare.mode",
            report.provenance.mode == baseline.provenance.mode,
            baseline.provenance.mode,
            report.provenance.mode,
            "incomparable",
        )
        for name in ("implementation", "dependencies", "model", "strategy", "index"):
            before, after = getattr(baseline.provenance, name), getattr(report.provenance, name)
            if before != after:
                permitted = name in policy.permitted_changes
                differences.append(
                    Difference(field=name, baseline=before, candidate=after, permitted=permitted)
                )
                rule(
                    f"compare.{name}",
                    permitted,
                    "declared experiment variable",
                    "permitted" if permitted else "undeclared change",
                    "incomparable",
                )
        if not any(r.status == "incomparable" for r in rules):
            for name in policy.regression_metrics:
                before = getattr(baseline.quality.metrics, name).value
                after = getattr(report.quality.metrics, name).value
                change = after - before if before is not None and after is not None else None
                deltas.append(Delta(metric=name, baseline=before, candidate=after, change=change))
                rule(
                    f"regression.{name}",
                    change is not None and change >= 0,
                    "no regression",
                    "unavailable" if change is None else change,
                    "unavailable" if change is None else "fail",
                )
    return GateResult(
        report_fingerprint=fingerprint(report),
        policy_fingerprint=fingerprint(policy),
        baseline_fingerprint=fingerprint(baseline) if baseline is not None else None,
        decision=decision(rules),
        rules=tuple(rules),
        differences=tuple(differences),
        deltas=tuple(deltas),
    )
