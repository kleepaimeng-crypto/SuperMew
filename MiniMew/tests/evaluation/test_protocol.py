"""8.1独立离线测试；使用--confcutdir=tests/evaluation隔离在线App夹具。"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from minimew.evaluation.protocol import (
    Corpus,
    Dataset,
    EvaluationInputError,
    ObservationSet,
    fingerprint,
    load,
    validate_inputs,
)

FIXTURES = Path(__file__).resolve().parents[2] / "evals/rag/fixtures"


def inputs():
    return (
        load(FIXTURES / "dataset.json", Dataset),
        load(FIXTURES / "corpus.json", Corpus),
        load(FIXTURES / "observation.json", ObservationSet),
    )


def test_authored_fixture_and_content_identity_are_portable(tmp_path):
    dataset, corpus, observations = inputs()
    validate_inputs(dataset, corpus, observations)
    assert len(dataset.cases) == 4
    assert dataset.cases[1].evidence_gold[0].start == 9
    assert dataset.cases[1].evidence_gold[1].end == 29
    path = tmp_path / "relocated.json"
    path.write_text(
        json.dumps(dataset.model_dump(mode="json"), ensure_ascii=True), encoding="utf-8"
    )
    assert fingerprint(load(path, Dataset)) == fingerprint(dataset)
    assert observations.provenance.mode == "contract_smoke"


@pytest.mark.parametrize("change", ["question", "claim", "source"])
def test_fingerprint_changes_with_standard_or_source_content(change):
    dataset, _, _ = inputs()
    value = dataset.model_dump(mode="json")
    if change == "question":
        value["cases"][0]["question"] += "？"
    elif change == "claim":
        value["cases"][0]["required_claims"] = ["另一个人工标准"]
    else:
        value["sources"][0]["content_sha256"] = "a" * 64
    changed = Dataset.model_validate_json(json.dumps(value))
    assert fingerprint(changed) != fingerprint(dataset)


@pytest.mark.parametrize(
    "change", ["unknown", "duplicate", "type", "bounds", "blank", "claims", "error", "gold"]
)
def test_invalid_standards_are_rejected(change):
    value = inputs()[0].model_dump(mode="json")
    case = value["cases"][0]
    if change == "unknown":
        case["mode"] = "rewrite"
    elif change == "duplicate":
        value["cases"].append(case.copy())
    elif change == "type":
        case["critical"] = 1
    elif change == "bounds":
        case["evidence_gold"][0]["end"] = 1000
    elif change == "blank":
        case["question"] = " "
    elif change == "claims":
        case["forbidden_claims"] = case["required_claims"]
    elif change == "error":
        case["allowed_outcomes"] = ["error"]
        case["expected_error"] = "FAULT"
    else:
        case["evidence_gold"] = []
    with pytest.raises(ValidationError):
        Dataset.model_validate_json(json.dumps(value))


@pytest.mark.parametrize(
    "change,code",
    [
        ("dataset", "DATASET_MISMATCH"),
        ("corpus", "CORPUS_MISMATCH"),
        ("missing", "MISSING_CASE"),
        ("unknown", "UNKNOWN_CASE"),
        ("hash", "INVALID_SOURCE"),
        ("bounds", "INVALID_SOURCE"),
    ],
)
def test_observations_must_match_declared_facts(change, code):
    dataset, corpus, observations = inputs()
    value = observations.model_dump(mode="json")
    if change in {"dataset", "corpus"}:
        value[change + "_fingerprint"] = "a" * 64
    elif change == "missing":
        value["cases"].pop()
    elif change == "unknown":
        value["cases"][0]["case_id"] = "foreign"
    elif change == "hash":
        value["cases"][0]["materials"][0]["content_sha256"] = "a" * 64
    else:
        value["cases"][0]["materials"][0]["end"] = 1000
    changed = ObservationSet.model_validate_json(json.dumps(value))
    with pytest.raises(EvaluationInputError, match=code):
        validate_inputs(dataset, corpus, changed)


def test_actual_corpus_change_cannot_keep_old_identity():
    dataset, corpus, observations = inputs()
    value = corpus.model_dump(mode="json")
    value["sources"][0]["text"] += "改动"
    with pytest.raises(EvaluationInputError, match="CORPUS_MISMATCH"):
        validate_inputs(dataset, Corpus.model_validate_json(json.dumps(value)), observations)


@pytest.mark.parametrize("change", ["duplicate", "error", "negative"])
def test_invalid_observation_structure(change):
    value = inputs()[2].model_dump(mode="json")
    if change == "duplicate":
        value["cases"].append(value["cases"][0].copy())
    elif change == "error":
        value["cases"][0]["error_code"] = "FAILURE"
    else:
        value["cases"][0]["counts"]["model_attempts"] = -1
    with pytest.raises(ValidationError):
        ObservationSet.model_validate_json(json.dumps(value))


def test_json_duplicate_keys_and_private_fields_are_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(EvaluationInputError, match="DUPLICATE_JSON_KEY"):
        load(path, Dataset)
    value = inputs()[2].model_dump(mode="json")
    value["provenance"]["api_key"] = "must-not-appear-in-error"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(EvaluationInputError) as error:
        load(path, ObservationSet)
    assert str(error.value) == "INVALID_EVALUATION_INPUT"
