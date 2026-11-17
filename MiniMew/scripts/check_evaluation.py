"""显式真实PG/Milvus/缓存CPU模型验收；三个进程同Case澄清续接，收费0。"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

from check_knowledge import build_once, offline_notifications
from check_runtime import temporary_database
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

import minimew.app as app_module
from minimew.database import create_engine
from minimew.documents.artifacts import read_artifact
from minimew.documents.storage import SourceStore
from minimew.evaluation.corpus import cleanup, prepare
from minimew.evaluation.protocol import Dataset, fingerprint
from minimew.evaluation.result_protocol import CorpusV2, SourceTextV2
from minimew.evaluation.runtime_protocol import JobRequest
from minimew.evaluation.store import EvaluationStore
from minimew.models import Document, EvaluationCase, EvaluationJob, IndexArtifact
from minimew.settings import Settings
from minimew.vector_store.adapter import CandidateIndex
from minimew.vector_store.config import VectorSettings
from minimew.vector_store.milvus import MilvusBackend

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "# 差旅制度\n\n## 餐费\n出差餐费每人每天最高报销150元，须提供票据。\n"


def process(phase):
    result = subprocess.run(
        [sys.executable, "scripts/evaluation_process.py", phase],
        cwd=ROOT,
        env=dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1"),
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode:
        (ROOT / ".artifacts" / ("evaluation-" + phase + ".log")).write_text(
            result.stderr, encoding="utf-8"
        )
        raise AssertionError("Evaluation subprocess failed: " + phase)


async def main(browser=False):
    report = {
        "passed": False,
        "paid_model_calls": 0,
        "source": "local-retrieval/controlled-generation/controlled-judge",
    }
    old_notify = app_module.notification_lifespan
    old_env = dict(os.environ)
    app_module.notification_lifespan = offline_notifications
    try:
        async with temporary_database() as database:
            with tempfile.TemporaryDirectory(prefix="minimew-evaluation-") as directory:
                os.environ.update(
                    MINIMEW_DB_NAME=database,
                    MINIMEW_DOCUMENT_UPLOAD_DIR=directory,
                    MINIMEW_KNOWLEDGE_ENABLED="false",
                    HF_HUB_OFFLINE="1",
                    TRANSFORMERS_OFFLINE="1",
                )
                engine = create_engine(Settings())
                sessions = async_sessionmaker(engine, expire_on_commit=False)
                corpus_id = None
                user = None
                job_id = None
                try:
                    with TestClient(app_module.create_app()) as client:
                        credentials = {
                            "username": "evaluation_" + uuid4().hex[:16],
                            "password": "owned-evaluation-test-1234",
                        }
                        assert client.post("/auth/register", json=credentials).status_code == 201
                        login = client.post("/auth/login", json=credentials)
                        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
                        user = UUID(client.get("/auth/me", headers=headers).json()["id"])
                        response = client.post(
                            "/v1/documents",
                            headers=headers,
                            data={"idempotency_key": "owned-evaluation"},
                            files={"file": ("policy.md", SOURCE.encode())},
                        )
                        assert response.status_code == 202, response.text
                        version = UUID(response.json()["version"]["id"])
                        document = UUID(response.json()["document"]["id"])
                    await asyncio.to_thread(build_once)
                    corpus = CorpusV2(sources=(SourceTextV2(source_id="policy", text=SOURCE),))
                    start = SOURCE.index("出差餐费")
                    dataset = Dataset.model_validate_json(
                        json.dumps(
                            {
                                "dataset_id": "local-evaluation-check",
                                "sources": [
                                    s.identity().model_dump(mode="json") for s in corpus.sources
                                ],
                                "cases": [
                                    {
                                        "case_id": "meal",
                                        "question": "出差餐费每人每天最高报销多少？",
                                        "allowed_outcomes": ["answered"],
                                        "critical": True,
                                        "requires_citations": True,
                                        "evidence_gold": [
                                            {
                                                "source_id": "policy",
                                                "start": start,
                                                "end": SOURCE.index("。") + 1,
                                            }
                                        ],
                                    }
                                ],
                            }
                        )
                    )
                    corpus_id = await prepare(
                        sessions,
                        user,
                        dataset,
                        {"policy": version},
                        SourceStore(Path(directory)),
                        VectorSettings(),
                    )
                    request = JobRequest.model_validate_json(
                        json.dumps(
                            {
                                "corpus_id": str(corpus_id),
                                "dataset": dataset.model_dump(mode="json"),
                                "case_seconds": 300,
                                "job_seconds": 1800,
                                "scenarios": [
                                    {
                                        "case_id": "meal",
                                        "roles": [
                                            {
                                                "purpose": "grade",
                                                "payload": {
                                                    "status": "ambiguous",
                                                    "supported": ["E1"],
                                                    "missing": ["培训地区"],
                                                },
                                            },
                                            {
                                                "purpose": "grade",
                                                "payload": {
                                                    "status": "sufficient",
                                                    "supported": ["E1"],
                                                    "missing": [],
                                                },
                                            },
                                        ],
                                        "expected_prompt": "请补充：培训地区",
                                        "supplement": "国内培训",
                                        "answer": (
                                            "## 文档依据\n每人每天最高报销150元，须提供票据。[E1]"
                                        ),
                                        "judge": {
                                            "correctness": 2,
                                            "completeness": 2,
                                            "grounding": 2,
                                            "reason": "受控流程验收",
                                        },
                                    }
                                ],
                            }
                        )
                    )
                    if browser:
                        from evaluation_browser import run_browser

                        evidence = await run_browser(request, credentials, database, directory)
                        async with sessions() as session:
                            finished = (
                                await session.scalars(
                                    select(EvaluationJob).where(EvaluationJob.user_id == user)
                                )
                            ).all()
                            assert len(finished) == 3
                            assert sorted(j.status for j in finished) == [
                                "cancelled",
                                "completed",
                                "completed",
                            ]
                            assert all(
                                j.report["decision"] == "pass"
                                for j in finished
                                if j.status == "completed"
                            )
                        report.update(
                            passed=True, browser="passed", jobs=3, browser_evidence=evidence
                        )
                    else:
                        store = EvaluationStore(sessions)
                        job_id = await store.create(user, "local-evaluation", request)
                        case_id = None
                        saved_graph = None
                        phases = []
                        for phase in ("waiting", "accepted", "completed"):
                            await asyncio.to_thread(process, phase)
                            job, cases = await store.read(user, job_id)
                            case = cases[0]
                            assert case.status == phase, (
                                phase,
                                job.status,
                                job.error_code,
                                case.error_code,
                            )
                            if case_id is None:
                                case_id = case.id
                                saved_graph = fingerprint(case.checkpoint["graph"])
                            assert case.id == case_id
                            assert fingerprint(case.checkpoint["graph"]) == saved_graph
                            phases.append(
                                {
                                    "phase": phase,
                                    "case_id": str(case.id),
                                    "model_attempts": (case.progress or {}).get("model_attempts"),
                                }
                            )
                            if phase != "completed":
                                async with sessions() as session, session.begin():
                                    await session.execute(
                                        text(
                                            "UPDATE evaluation_jobs "
                                            "SET lease_until=clock_timestamp()-interval '1 second' "
                                            "WHERE id=:id"
                                        ),
                                        {"id": job_id},
                                    )
                        assert job.status == "completed" and job.report["decision"] == "pass", (
                            job.report
                        )
                        assert job.report["total_model_attempts"] == 4
                        assert job.report["evaluations"][0]["resumed"]
                        # 正式图在补充后会重新检索：初次一次＋澄清后一次。
                        assert case.generation["observation"]["counts"]["retrieval_calls"] == 2
                        async with sessions() as session:
                            original = await session.get(Document, document)
                            assert original.current_version_id == version
                        report.update(
                            passed=True,
                            phases=phases,
                            total_model_attempts=4,
                            original_version_unchanged=True,
                        )
                finally:
                    if corpus_id:
                        async with sessions() as session, session.begin():
                            owned = select(EvaluationJob.id).where(
                                EvaluationJob.user_id == user, EvaluationJob.corpus_id == corpus_id
                            )
                            await session.execute(
                                delete(EvaluationCase).where(EvaluationCase.job_id.in_(owned))
                            )
                            await session.execute(
                                delete(EvaluationJob).where(
                                    EvaluationJob.user_id == user,
                                    EvaluationJob.corpus_id == corpus_id,
                                )
                            )
                    if corpus_id:
                        await cleanup(sessions, user, corpus_id, VectorSettings())
                        report["dedicated_collection_removed"] = True
                    backend = await asyncio.to_thread(MilvusBackend.connect, VectorSettings())
                    try:
                        async with sessions() as session:
                            artifacts = (await session.scalars(select(IndexArtifact))).all()
                        for artifact in artifacts:
                            manifest = read_artifact(artifact)
                            await asyncio.to_thread(
                                CandidateIndex(backend).delete_candidate, manifest.scope
                            )
                        report["cleaned_exact_scopes"] = len(artifacts)
                    finally:
                        await asyncio.to_thread(backend.close)
                        await engine.dispose()
    finally:
        app_module.notification_lifespan = old_notify
        os.environ.clear()
        os.environ.update(old_env)
        target = ROOT / (
            ".artifacts/evaluation-browser-check.json"
            if browser
            else ".artifacts/evaluation-check.json"
        )
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true")
    asyncio.run(main(parser.parse_args().browser))
