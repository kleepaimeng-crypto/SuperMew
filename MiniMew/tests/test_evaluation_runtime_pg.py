"""真实PG+正式计算图的评测状态测试；向量/权重为显式受控依赖。"""

import asyncio
import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import TypeAdapter
from sqlalchemy import text
from test_documents_pg import account, database_call
from test_documents_pg import client as client
from test_knowledge import Runtime
from test_vector_store import IDENTITY, FakeBackend, candidate

from minimew.documents.chunking import ChunkBuild
from minimew.documents.retrieval import RetrievalScope
from minimew.evaluation.protocol import Dataset
from minimew.evaluation.runtime_protocol import CorpusV2 as Corpus
from minimew.evaluation.runtime_protocol import JobRequest
from minimew.evaluation.runtime_protocol import SourceTextV2 as SourceText
from minimew.evaluation.store import EvaluationControl, EvaluationStore
from minimew.evaluation.worker import EvaluationWorker
from minimew.models import EvaluationCorpus
from minimew.vector_store.adapter import CandidateIndex

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned PG required"
)
SOURCE = "# 制度\n## 餐费\n每日150元。\n## 住宿\n住宿350元。\n## 材料\n申请需要票据。"


@pytest.fixture(autouse=True)
def owned_evaluation_catalog(client):
    async def reset(sessions):
        async with sessions() as session, session.begin():
            await session.execute(
                text("TRUNCATE evaluation_cases,evaluation_jobs,evaluation_corpora")
            )

    asyncio.run(database_call(reset))


async def seeded(sessions, user, clarification=False):
    target = candidate(identity=replace(IDENTITY, user_id=user))
    backend = FakeBackend()
    backend.close = lambda: None
    CandidateIndex(backend).stage(target)
    directory = ChunkBuild(
        target.manifest.scope.identity,
        target.manifest.scope.build_fingerprint,
        target.manifest.scope.source_sha256,
        (),
        tuple(),
    )
    # 本测试不做材料合并；真实语料准备/目录的集成另行覆盖。
    scope = RetrievalScope(
        user, (target.manifest,), datetime.now(UTC) + timedelta(hours=1), (directory,)
    )
    corpus = Corpus(sources=(SourceText(source_id="policy", text=SOURCE),))
    start = SOURCE.index("每日150元。")
    dataset = Dataset.model_validate_json(
        json.dumps(
            {
                "dataset_id": "runtime-test",
                "sources": [corpus.sources[0].identity().model_dump(mode="json")],
                "cases": [
                    {
                        "case_id": "meal",
                        "question": "餐费是多少？",
                        "allowed_outcomes": ["answered"],
                        "critical": True,
                        "requires_citations": True,
                        "required_claims": ["gold-sentinel-never-visible-to-generation"],
                        "evidence_gold": [
                            {"source_id": "policy", "start": start, "end": start + 7}
                        ],
                    }
                ],
            }
        )
    )
    corpus_id = uuid4()
    async with sessions() as session, session.begin():
        session.add(
            EvaluationCorpus(
                id=corpus_id,
                user_id=user,
                collection="minimew_eval_" + corpus_id.hex,
                payload={
                    "ready": True,
                    "corpus": corpus.model_dump(mode="json"),
                    "corpus_fingerprint": dataset.corpus_fingerprint(),
                    "scope": json.loads(TypeAdapter(RetrievalScope).dump_json(scope)),
                    "bindings": [
                        {
                            "source_id": "policy",
                            "version_id": str(target.manifest.scope.identity.version_id),
                        }
                    ],
                },
            )
        )
    grade = {
        "purpose": "grade",
        "payload": {"status": "sufficient", "supported": ["E1"], "missing": []},
    }
    roles = (
        [
            {
                "purpose": "grade",
                "payload": {"status": "ambiguous", "supported": ["E1"], "missing": ["培训地区"]},
            },
            grade,
        ]
        if clarification
        else [grade]
    )
    scenario = {
        "case_id": "meal",
        "roles": roles,
        "answer": "## 文档依据\n每日150元。[E1]",
        "judge": {"correctness": 2, "completeness": 2, "grounding": 2, "reason": "受控接线测试"},
    }
    if clarification:
        scenario.update(expected_prompt="请补充：培训地区", supplement="国内培训")
    request = JobRequest.model_validate_json(
        json.dumps(
            {
                "corpus_id": str(corpus_id),
                "dataset": dataset.model_dump(mode="json"),
                "scenarios": [scenario],
            }
        )
    )
    return request, backend


class TestRuntime(Runtime):
    __test__ = False

    def __init__(self, backend):
        super().__init__(backend)
        self.vector_settings = SimpleNamespace()

    async def start(self):
        pass

    async def aclose(self):
        pass


@pytest.mark.parametrize("clarification", [False, True])
def test_persistent_job_runs_shared_graph_and_reports_controlled_results(
    client, clarification, monkeypatch
):
    from minimew.evaluation.worker import ControlledProvider

    original = ControlledProvider.stream

    async def inspect_generation(self, request):
        assert request.run_id is request.thread_id is None
        assert request.case_identity is not None
        assert all(
            "gold-sentinel-never-visible-to-generation" not in m.content for m in request.messages
        )
        async for event in original(self, request):
            yield event

    monkeypatch.setattr(ControlledProvider, "stream", inspect_generation)
    _, user = account(client)

    async def scenario(sessions):
        request, backend = await seeded(sessions, user, clarification)
        store = EvaluationStore(sessions)
        job_id = await store.create(user, "job-key", request)
        assert await store.create(user, "job-key", request) == job_id
        worker = EvaluationWorker(
            sessions,
            runtime_factory=lambda: TestRuntime(backend),
            backend_factory=lambda *a, **k: backend,
        )
        assert await worker.once()
        job, cases = await store.read(user, job_id)
        assert job.status == "completed", (job.error_code, [c.error_code for c in cases])
        assert job.report["schema_version"] == 2
        assert job.report["decision"] == "pass", job.report
        result = job.report["evaluations"][0]
        assert result["resumed"] is clarification
        assert cases[0].status == "completed"
        assert cases[0].generation["observation"]["counts"]["model_attempts"] == (
            3 if clarification else 2
        )
        assert not await worker.once()

    asyncio.run(database_call(scenario))


def test_lease_cancel_owner_isolation_and_unknown_result_are_persistent(client):
    _, user = account(client)

    async def scenario(sessions):
        request, _ = await seeded(sessions, user)
        store = EvaluationStore(sessions)
        job_id = await store.create(user, "lease-key", request)
        lease = await store.claim()
        _, cases = await store.read(user, job_id)
        await store.write_case(lease, cases[0].id, {"pending"}, "generating")
        async with sessions() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE evaluation_jobs SET lease_until=clock_timestamp()-interval '1 second' "
                    "WHERE id=:id"
                ),
                {"id": job_id},
            )
        successor = await store.claim()
        assert successor.owner != lease.owner
        with pytest.raises(EvaluationControl):
            await store.write_case(lease, cases[0].id, {"failed"}, "completed")
        _, cases = await store.read(user, job_id)
        assert cases[0].error_code == "EVALUATION_RESULT_UNKNOWN"
        await store.cancel(user, job_id)
        with pytest.raises(EvaluationControl, match="EVALUATION_CANCELLED"):
            await store.heartbeat(successor)
        await store.terminate(successor, "EVALUATION_CANCELLED")
        job, _ = await store.read(user, job_id)
        assert job.status == "cancelled"

    asyncio.run(database_call(scenario))


def test_evaluation_api_owns_jobs_and_idempotency_without_executing(client):
    headers, user = account(client)
    stranger, _ = account(client)

    async def setup(sessions):
        request, _ = await seeded(sessions, user)
        return request

    request = asyncio.run(database_call(setup))
    path = "/v1/evaluation-jobs"
    data = request.model_dump(mode="json")
    auth = {**headers, "Idempotency-Key": "http-job"}
    created = client.post(path, headers=auth, json=data)
    assert created.status_code == 201, created.text
    job_id = created.json()["job_id"]
    assert client.post(path, headers=auth, json=data).json()["job_id"] == job_id
    changed = {**data, "case_seconds": 121}
    assert client.post(path, headers=auth, json=changed).status_code == 409
    assert (
        client.post(path, headers={**stranger, "Idempotency-Key": "other"}, json=data).status_code
        == 404
    )
    assert client.get(path + "/" + job_id, headers=stranger).status_code == 404
    assert client.post(path + "/" + job_id + "/cancel", headers=stranger).status_code == 404
    assert client.get(path + "/" + job_id + "/report", headers=stranger).status_code == 404
    view = client.get(path + "/" + job_id, headers=headers)
    assert view.status_code == 200 and view.json()["status"] == "pending"
    assert view.headers["cache-control"] == "no-store"
    assert client.get(path + "/" + job_id + "/report", headers=headers).json()["report"] is None
    assert client.post(path + "/" + job_id + "/cancel", headers=headers).json()["cancel_requested"]
    assert client.get(path + "/" + job_id).status_code == 401


def test_joint_budget_rolls_back_stage_and_cancel_marks_unfinished_cases(client):
    _, user = account(client)

    async def scenario(sessions):
        request, _ = await seeded(sessions, user)
        request = request.model_copy(update={"total_model_limit": 1})
        store = EvaluationStore(sessions)
        job_id = await store.create(user, "budget", request)
        lease = await store.claim()
        _, cases = await store.read(user, job_id)
        case_id = cases[0].id
        await store.write_case(
            lease, case_id, {"pending"}, "generating", progress={"model_attempts": 1}
        )
        with pytest.raises(EvaluationControl, match="EVALUATION_MODEL_BUDGET"):
            await store.write_case(
                lease, case_id, {"generating"}, "judging", judgment={"attempts": 1}
            )
        _, cases = await store.read(user, job_id)
        assert cases[0].status == "generating" and cases[0].judgment is None
        await store.cancel(user, job_id)
        await store.terminate(lease, "EVALUATION_CANCELLED")
        _, cases = await store.read(user, job_id)
        assert cases[0].status == "cancelled"

    asyncio.run(database_call(scenario))


def test_persisted_clarification_accept_is_exact_and_idempotent(client):
    _, user = account(client)

    async def scenario(sessions):
        request, _ = await seeded(sessions, user, True)
        store = EvaluationStore(sessions)
        job_id = await store.create(user, "accept", request)
        lease = await store.claim()
        _, cases = await store.read(user, job_id)
        case_id = cases[0].id
        await store.write_case(
            lease, case_id, {"pending"}, "waiting", checkpoint={"prompt": "地区"}
        )
        assert not await store.accept(lease, case_id, "城市", "北京")
        assert await store.accept(lease, case_id, "地区", "北京")
        assert await store.accept(lease, case_id, "地区", "北京")
        from minimew.evaluation.store import EvaluationConflict

        with pytest.raises(EvaluationConflict):
            await store.accept(lease, case_id, "地区", "上海")
        _, cases = await store.read(user, job_id)
        assert cases[0].status == "accepted" and cases[0].checkpoint["answer"] == "北京"

    asyncio.run(database_call(scenario))


def test_competing_workers_claim_only_one_owner_and_expired_corpus_is_rejected(client):
    from minimew.evaluation.store import EvaluationConflict

    _, user = account(client)

    async def scenario(sessions):
        request, _ = await seeded(sessions, user)
        store = EvaluationStore(sessions)
        job_id = await store.create(user, "compete", request)
        leases = await asyncio.gather(store.claim(), store.claim())
        claimed = [lease for lease in leases if lease is not None]
        assert len(claimed) == 1 and claimed[0].job_id == job_id
        async with sessions() as session, session.begin():
            corpus = await session.get(EvaluationCorpus, request.corpus_id)
            scope = {**corpus.payload["scope"], "expires_at": datetime.now(UTC).isoformat()}
            corpus.payload = {**corpus.payload, "scope": scope}
        with pytest.raises(EvaluationConflict):
            await store.create(user, "expired", request)

    asyncio.run(database_call(scenario))
