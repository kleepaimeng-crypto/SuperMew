"""8.2评分分母、来源覆盖、切片和不可伪造汇总。"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from minimew.evaluation.protocol import Corpus, Dataset, ObservationSet, canonical, load, text_hash
from minimew.evaluation.scoring import Report, score

FIXTURES = Path(__file__).resolve().parents[2] / "evals/rag/fixtures"


def inputs():
    return (
        load(FIXTURES / "dataset.json", Dataset),
        load(FIXTURES / "corpus.json", Corpus),
        load(FIXTURES / "observation.json", ObservationSet),
    )


def changed_report(change):
    dataset, corpus, observations = inputs()
    value = observations.model_dump(mode="json")
    change(value)
    return score(dataset, corpus, ObservationSet.model_validate_json(json.dumps(value)))


def test_manually_expected_counts_metrics_and_fault_slice():
    report = score(*inputs())
    assert report.summary.expected == 4
    assert report.summary.completed == 3 and report.summary.errors == 1
    assert report.quality.expected == 3 and report.quality.errors == 0
    assert report.fault.expected == 1 and report.fault.errors == 1
    assert report.fault.metrics.outcome_match.value == 1
    assert report.quality.metrics.outcome_match.value == 1
    coverage = report.summary.metrics.material_coverage
    assert (coverage.value, coverage.eligible, coverage.evaluated) == (1, 2, 2)
    assert report.summary.metrics.semantic_correctness.value is None
    assert report.summary.metrics.semantic_correctness.evaluated == 0
    assert report.summary.counts.model_attempts == 4
    assert all(c.semantic_correctness is None for c in report.cases)


def test_missing_exception_material_lowers_coverage_without_changing_outcome():
    def change(v):
        v["cases"][1]["materials"].pop()
        v["cases"][1]["citation_sources"].pop()

    report = changed_report(change)
    case = next(c for c in report.cases if c.case_id == "exception")
    assert case.material_coverage == 0.5 and case.outcome_match
    assert case.missing_gold[0].start == 19
    assert report.summary.metrics.material_coverage.value == 0.75


@pytest.mark.parametrize(
    "spans,expected", [([(0, 4), (4, 9)], 1), ([(0, 3), (4, 9)], 0), ([(0, 9), (2, 7)], 1)]
)
def test_interval_union_does_not_double_count_or_fill_gaps(spans, expected):
    dataset, corpus, observations = inputs()
    text = corpus.sources[0].text
    value = observations.model_dump(mode="json")
    value["cases"][0]["materials"] = [
        {
            "material_id": f"m{i}",
            "source_id": "policy",
            "start": a,
            "end": b,
            "content_sha256": text_hash(text[a:b]),
        }
        for i, (a, b) in enumerate(spans)
    ]
    value["cases"][0]["citation_sources"] = ["m0"]
    report = score(dataset, corpus, ObservationSet.model_validate_json(json.dumps(value)))
    assert next(c for c in report.cases if c.case_id == "meal").material_coverage == expected


def test_quality_error_stays_in_outcome_denominator_and_coverage_error_count():
    def change(v):
        v["cases"][0].update(
            outcome="error", error_code="PROVIDER_TIMEOUT", materials=[], citation_sources=[]
        )

    report = changed_report(change)
    assert report.quality.metrics.outcome_match.value == 2 / 3
    assert report.quality.expected == 3 and report.quality.errors == 1
    metric = report.quality.metrics.material_coverage
    assert (metric.eligible, metric.evaluated, metric.errors, metric.unavailable) == (2, 1, 1, 0)
    assert metric.value == 1  # 有效材料的宏平均，错误另留分母，不能据此声称全部Case成功。


def test_healthy_no_gold_is_unavailable_not_perfect_coverage():
    report = score(*inputs())
    unknown = next(c for c in report.cases if c.case_id == "unknown")
    assert unknown.material_coverage is None and unknown.citation_structure is None
    assert unknown.outcome_match


def test_waiting_is_not_completed_or_scored_as_material_success():
    def change(v):
        v["cases"][0].update(outcome="waiting", materials=[], citation_sources=[])

    report = changed_report(change)
    assert report.quality.completed == 2 and report.quality.waiting == 1
    assert report.quality.metrics.material_coverage.unavailable == 1


@pytest.mark.parametrize("citations", [[], ["foreign-material"]])
def test_missing_and_unbound_citations_are_reported_as_structure_failures(citations):
    report = changed_report(lambda v: v["cases"][0].update(citation_sources=citations))
    assert next(c for c in report.cases if c.case_id == "meal").citation_structure is False


def test_budget_overrun_is_recorded_without_rewriting_actual_counts():
    def change(v):
        v["cases"][0]["counts"]["model_attempts"] = 5

    report = changed_report(change)
    assert report.summary.counts.model_attempts == 8
    assert report.summary.metrics.budget_ok.value == 0.75


def test_observation_order_does_not_change_metrics_or_case_order():
    dataset, corpus, observations = inputs()
    reverse = observations.model_copy(update={"cases": tuple(reversed(observations.cases))})
    first, second = score(dataset, corpus, observations), score(dataset, corpus, reverse)
    assert first.cases == second.cases and first.summary == second.summary
    assert canonical(score(dataset, corpus, observations)) == canonical(first)
    assert Report.model_validate_json(canonical(first)) == first


@pytest.mark.parametrize("change", ["aggregate", "semantic"])
def test_report_rejects_fabricated_summary_or_semantic_scores(change):
    value = score(*inputs()).model_dump(mode="json")
    if change == "aggregate":
        value["summary"]["errors"] = 0
    else:
        value["cases"][0]["semantic_correctness"] = 1.0
    with pytest.raises(ValidationError):
        Report.model_validate_json(json.dumps(value))
