"""8.3关键规则、实验变量、缺失评价与基线可比性。"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from minimew.evaluation.gate import EvaluationArtifact, GatePolicy, evaluate
from minimew.evaluation.protocol import Corpus, Dataset, ObservationSet, fingerprint, load
from minimew.evaluation.scoring import score

FIXTURES = Path(__file__).resolve().parents[2] / "evals/rag/fixtures"


def report(change=lambda value: None):
    dataset = load(FIXTURES / "dataset.json", Dataset)
    corpus = load(FIXTURES / "corpus.json", Corpus)
    value = load(FIXTURES / "observation.json", ObservationSet).model_dump(mode="json")
    change(value)
    return score(dataset, corpus, ObservationSet.model_validate_json(json.dumps(value)))


def test_smoke_policy_accepts_contract_only_and_has_no_semantic_quality_claim():
    value = report()
    gate = evaluate(value, GatePolicy())
    assert gate.decision == "pass" and gate.baseline_fingerprint is None
    assert value.quality.metrics.semantic_correctness.value is None
    assert evaluate(value, GatePolicy(allowed_modes=("live",))).decision == "fail"


def test_explicit_saved_smoke_baseline_matches_authored_facts():
    baseline = load(FIXTURES / "baseline.json", EvaluationArtifact)
    policy = load(FIXTURES / "gate-policy.json", GatePolicy)
    assert baseline.report == report()
    assert baseline.gate == evaluate(report(), policy)
    assert baseline.report.provenance.mode == "contract_smoke"
    assert baseline.report.quality.metrics.semantic_correctness.value is None


def test_required_semantic_metric_is_insufficient_not_zero_or_pass():
    policy = GatePolicy(required_metrics=(*GatePolicy().required_metrics, "semantic_correctness"))
    gate = evaluate(report(), policy)
    assert gate.decision == "insufficient"
    rule = next(r for r in gate.rules if r.name == "metric.semantic_correctness.available")
    assert rule.status == "unavailable" and rule.actual == "0/3"


def test_missing_required_baseline_and_same_report_comparison():
    policy = GatePolicy(require_baseline=True)
    assert evaluate(report(), policy).decision == "insufficient"
    gate = evaluate(report(), policy, report())
    assert gate.decision == "pass" and all(d.change == 0 for d in gate.deltas)


@pytest.mark.parametrize("field", ["implementation", "dependencies", "model", "strategy", "index"])
def test_undeclared_condition_changes_are_incomparable_but_can_be_explicit_variables(field):
    baseline = report()
    candidate = report(lambda v: v["provenance"].update({field: "a" * 64}))
    rejected = evaluate(candidate, GatePolicy(), baseline)
    assert rejected.decision == "incomparable" and rejected.deltas == ()
    accepted = evaluate(candidate, GatePolicy(permitted_changes=(field,)), baseline)
    assert accepted.decision == "pass"
    assert accepted.differences[0].field == field and accepted.differences[0].permitted


@pytest.mark.parametrize("field", ["dataset_fingerprint", "corpus_fingerprint"])
def test_dataset_and_corpus_are_fixed_controls(field):
    baseline = report()
    candidate = baseline.model_copy(update={field: "a" * 64})
    result = evaluate(candidate, GatePolicy(permitted_changes=("strategy",)), baseline)
    assert result.decision == "incomparable" and not result.deltas


def test_source_mode_cannot_be_treated_as_strategy_variable():
    candidate = report(lambda v: v["provenance"].update(mode="controlled"))
    policy = GatePolicy(allowed_modes=("contract_smoke", "controlled"))
    assert evaluate(candidate, policy, report()).decision == "incomparable"


def test_critical_loss_remains_failure_even_with_no_average_threshold():
    def change(v):
        v["cases"][1]["materials"].pop()
        v["cases"][1]["citation_sources"].pop()

    result = evaluate(report(change), GatePolicy(thresholds=()), report())
    assert result.decision == "fail"
    assert next(r for r in result.rules if r.name == "critical.exception.coverage").status == "fail"
    assert next(d for d in result.deltas if d.metric == "material_coverage").change == -0.25


@pytest.mark.parametrize(
    "change,rule",
    [
        (lambda v: v["cases"][3].update(error_code="WRONG_ERROR"), "fault.outcomes"),
        (lambda v: v["cases"][0]["counts"].update(model_attempts=5), "all.budget"),
        (lambda v: v["cases"][0].update(outcome="waiting"), "quality.completed"),
    ],
)
def test_operational_rules_are_not_hidden_by_averages(change, rule):
    result = evaluate(report(change), GatePolicy())
    assert result.decision == "fail"
    assert next(r for r in result.rules if r.name == rule).status == "fail"


@pytest.mark.parametrize(
    "field,value",
    [("required_metrics", []), ("allowed_modes", []), ("permitted_changes", ["mode"])],
)
def test_policy_rejects_empty_requirements_and_undeclared_axes(field, value):
    policy = GatePolicy().model_dump(mode="json")
    policy[field] = value
    with pytest.raises(ValidationError):
        GatePolicy.model_validate_json(json.dumps(policy))


def test_artifact_rejects_mismatched_report_and_gate_and_fabricated_decision():
    value = report()
    artifact = EvaluationArtifact(report=value, gate=evaluate(value, GatePolicy()))
    saved = artifact.model_dump(mode="json")
    saved["gate"]["report_fingerprint"] = "a" * 64
    with pytest.raises(ValidationError):
        EvaluationArtifact.model_validate_json(json.dumps(saved))
    saved = artifact.model_dump(mode="json")
    saved["gate"]["decision"] = "fail"
    with pytest.raises(ValidationError):
        EvaluationArtifact.model_validate_json(json.dumps(saved))
    assert artifact.gate.report_fingerprint == fingerprint(value)
