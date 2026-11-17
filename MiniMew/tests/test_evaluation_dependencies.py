"""评测索引隔离与受控评分协议。"""

import asyncio
import copy
from unittest.mock import Mock
from uuid import uuid4

import pytest
from test_vector_store import FakeBackend, candidate

from minimew.evaluation.judge import JudgeFailure, JudgeInput, controlled_judge
from minimew.evaluation.protocol import Case
from minimew.evaluation.runtime_protocol import Scenario
from minimew.vector_store.adapter import CandidateIndex
from minimew.vector_store.config import VectorSettings
from minimew.vector_store.manifest import HYBRID_LAYOUT, VectorError, collection_for
from minimew.vector_store.milvus import MilvusBackend


@pytest.mark.parametrize("corrupt", [False, True])
def test_isolated_copy_verifies_source_before_writing_and_preserves_source(corrupt):
    source, target = FakeBackend(), FakeBackend()
    build = candidate()
    CandidateIndex(source).stage(build)
    if corrupt:
        source.rows.pop(next(iter(source.rows)))
    before = copy.deepcopy(source.rows)
    if corrupt:
        with pytest.raises(VectorError):
            CandidateIndex(source).copy_to(build.manifest, CandidateIndex(target))
        assert not target.rows
    else:
        CandidateIndex(source).copy_to(build.manifest, CandidateIndex(target))
        assert target.rows == source.rows
        CandidateIndex(target).delete_candidate(build.manifest.scope)
        assert not target.rows
    assert source.rows == before


@pytest.mark.parametrize(
    "namespace",
    ["normal", "minimew_eval_bad", "minimew_eval_" + ("a" * 32) + "_dense", "minimew_eval_../"],
)
def test_namespace_rejects_arbitrary_collection_names(namespace):
    with pytest.raises(ValueError):
        MilvusBackend(VectorSettings(), Mock(), namespace=namespace)


def test_eval_collection_uses_separate_dense_and_bm25_statistics():
    namespace = "minimew_eval_" + uuid4().hex
    backend = MilvusBackend(VectorSettings(), Mock(), namespace=namespace)
    assert backend.collection(HYBRID_LAYOUT) == namespace + "_hybrid"
    assert backend.collection("dense-cosine-v1") == namespace + "_dense"
    assert MilvusBackend(VectorSettings(), Mock()).collection(HYBRID_LAYOUT) == collection_for(
        HYBRID_LAYOUT
    )


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("timeout", "JUDGE_TIMEOUT"),
        ("invalid", "JUDGE_INVALID_RESPONSE"),
        ("transient", "JUDGE_RETRYABLE"),
    ],
)
def test_judge_failures_are_distinct_and_only_transient_retries(fault, expected):
    scenario = Scenario(
        case_id="meal",
        roles=(),
        answer="受控答卷",
        judge_fault=fault,
        judge={"correctness": 2, "completeness": 2, "grounding": 2, "reason": "流程"},
    )
    inputs = JudgeInput(
        standard=Case(case_id="meal", question="问题", allowed_outcomes=("answered",)),
        answer="答卷",
        materials=(),
        source_texts=(),
    )
    with pytest.raises(JudgeFailure) as error:
        asyncio.run(controlled_judge(inputs, scenario, 1))
    assert error.value.code == expected
    assert error.value.retryable is (fault == "transient")
    if fault == "transient":
        assert (asyncio.run(controlled_judge(inputs, scenario, 2))).correctness == 2
