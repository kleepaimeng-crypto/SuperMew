"""v2明确版本、受控Judge与比较血缘；无运行依赖。"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_cli import cli

from minimew.evaluation.gate import GatePolicy
from minimew.evaluation.protocol import (
    Corpus,
    Dataset,
    EvaluationInputError,
    ObservationSet,
    fingerprint,
    load,
)
from minimew.evaluation.result_protocol import JudgeResult
from minimew.evaluation.runtime_result import (
    CaseEvaluation,
    JudgeDelta,
    ObservationV2,
    compare_v2,
    score_v2,
)
from minimew.evaluation.versioned import load_observations

FIXTURES = Path(__file__).resolve().parents[2] / "evals/rag/fixtures"


def inputs():
    dataset = load(FIXTURES / "dataset.json", Dataset)
    corpus = load(FIXTURES / "corpus.json", Corpus)
    original = load(FIXTURES / "observation.json", ObservationSet)
    structural = original.model_copy(
        update={"provenance": original.provenance.model_copy(update={"mode": "controlled"})}
    )
    evaluations = tuple(
        CaseEvaluation(
            case_id=c.case_id,
            answer="受控答卷",
            judge=JudgeResult(correctness=2, completeness=2, grounding=2, reason="接线"),
            judge_attempts=1,
        )
        for c in structural.cases
    )
    return dataset, corpus, ObservationV2(structural=structural, evaluations=evaluations)


def test_v2_judge_is_separate_and_comparison_records_full_baseline():
    dataset, corpus, obs = inputs()
    baseline = score_v2(dataset, corpus, obs)
    result = compare_v2(baseline, baseline, GatePolicy(allowed_modes=("controlled",)))
    assert result.decision == "pass"
    assert result.baseline_fingerprint == fingerprint(baseline)
    assert len(result.judge_deltas) == 3
    assert all(d.baseline == d.candidate == 2 and d.change == 0 for d in result.judge_deltas)
    assert result.structural.report.quality.metrics.semantic_correctness.value is None
    assert result.total_model_attempts == result.structural.report.summary.counts.model_attempts + 4


@pytest.mark.parametrize("variant", ["missing", "low", "clarification"])
def test_quality_failure_and_unavailable_are_not_hidden(variant):
    dataset, corpus, obs = inputs()
    first = obs.evaluations[0]
    change = (
        {"judge": None, "judge_error": "JUDGE_TIMEOUT"}
        if variant == "missing"
        else {"judge": JudgeResult(correctness=0, completeness=2, grounding=2, reason="失败")}
        if variant == "low"
        else {"clarification_required": True}
    )
    obs = obs.model_copy(
        update={"evaluations": (first.model_copy(update=change), *obs.evaluations[1:])}
    )
    result = score_v2(dataset, corpus, obs)
    assert result.decision == ("insufficient" if variant == "missing" else "fail")


def test_v2_cli_round_trip_and_cross_version_baseline(tmp_path):
    _, _, obs = inputs()
    path = tmp_path / "observation-v2.json"
    path.write_text(obs.model_dump_json(), encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    assert cli("score", "--observations", path, "--output", baseline).returncode == 0
    compared = cli("compare", "--observations", path, "--baseline", baseline)
    assert compared.returncode == 0, compared.stderr
    assert json.loads(compared.stdout)["baseline_fingerprint"]
    text = cli("compare", "--observations", path, "--baseline", baseline, "--format", "text")
    assert "正确性=2" in text.stdout and "Judge差值 correctness：2.0 → 2.0" in text.stdout
    old = tmp_path / "old.json"
    assert cli("score", "--output", old).returncode == 0
    mismatch = cli("compare", "--observations", path, "--baseline", old)
    assert mismatch.returncode == 2
    assert json.loads(mismatch.stderr)["code"] == "BASELINE_SCHEMA_MISMATCH"


@pytest.mark.parametrize("version", [3, True, "2"])
def test_unsupported_schema_preserves_stable_error(version, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"schema_version": version}), encoding="utf-8")
    with pytest.raises(EvaluationInputError) as error:
        load_observations(path)
    assert error.value.code == "EVALUATION_SCHEMA_UNSUPPORTED"


def test_comparison_policy_mismatch_is_incomparable_and_arithmetic_is_checked():
    dataset, corpus, obs = inputs()
    baseline = score_v2(dataset, corpus, obs)
    candidate = score_v2(dataset, corpus, obs, minimum=1)
    result = compare_v2(candidate, baseline, GatePolicy(allowed_modes=("controlled",)))
    assert result.decision == "incomparable" and not result.judge_deltas
    with pytest.raises(ValidationError):
        JudgeDelta(metric="correctness", baseline=2.0, candidate=1.0, change=0.0)
