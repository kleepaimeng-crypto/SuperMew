"""6.7：固定发布资料、本地真实检索及原Run受控路线；不调用收费模型。"""

import argparse
import asyncio
import hashlib
import json
import os
import tempfile
import time
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path
from uuid import UUID, uuid4

from check_knowledge import build_once, offline_notifications
from check_runtime import temporary_database
from fastapi.testclient import TestClient
from rag_cases import (
    ScriptedModel,
    coverage,
    fingerprint,
    load_cases,
    portable_observation,
    timings,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import minimew.app as app_module
from minimew.database import create_engine
from minimew.documents.artifacts import read_artifact
from minimew.documents.retrieval import published_scope
from minimew.execution.repository import ExecutionRepository
from minimew.execution.service import RunExecutor
from minimew.execution.tools import ToolContext, ToolRegistry
from minimew.knowledge import CAPABILITY, KnowledgeService
from minimew.knowledge_runtime import KnowledgeRuntime
from minimew.model_control import ModelConfiguration, set_current_model
from minimew.models import IndexArtifact, Run
from minimew.rag.pipeline import RagPipeline
from minimew.rag.protocol import RagPolicy
from minimew.rag.reasoning import ReasoningPolicy
from minimew.settings import Settings
from minimew.vector_store.adapter import CandidateIndex
from minimew.vector_store.manifest import PROFILE
from minimew.vector_store.milvus import MilvusBackend

ROOT = Path(__file__).resolve().parents[1]
ROUTES = (
    ("dense", dict(strategy="dense", merge=False, rerank=False)),
    ("hybrid", dict(strategy="hybrid", merge=False, rerank=False)),
    ("completion", dict(strategy="hybrid", merge=True, rerank=False)),
    ("reranked", dict(strategy="hybrid", merge=True, rerank=True)),
)


async def compare_materials(sessions, runtime, owner, suite, source, samples):
    scope = await published_scope(sessions, owner)
    service = KnowledgeService(sessions, runtime)
    observations = []
    for case in suite.cases:
        # Retrieval quality cases are distinct from injected Provider failures.
        if case.mode in {"partial", "failure", "retry", "budget"}:
            continue
        for route, options in ROUTES:
            policy = RagPolicy(**options)
            durations = []
            selection_hashes = []
            for _ in range(samples):
                context = ToolContext(
                    owner,
                    uuid4(),
                    uuid4(),
                    frozenset({CAPABILITY}),
                    rag_policy=policy,
                    reasoning=ReasoningPolicy(),
                )
                started = time.monotonic()
                result = await RagPipeline(sessions, runtime).search(
                    case.question,
                    scope,
                    owner,
                    policy,
                    deadline=time.monotonic() + 60,
                )
                draft, _ = service.prepare(context, result, scope)
                durations.append(time.monotonic() - started)
                matches = draft.model_dump(mode="json")["matches"]
                selection_hashes.append(
                    fingerprint(
                        [
                            (m["chunk_id"], m["start"], m["end"], m["content_sha256"])
                            for m in matches
                        ]
                    )
                )
            observations.append(
                {
                    "case": case.id,
                    "route": route,
                    "policy": policy.model_dump(mode="json"),
                    "timing": timings(durations),
                    "selection_stable": len(set(selection_hashes)) == 1 if samples > 1 else None,
                    "materials": [
                        {
                            k: m[k]
                            for k in (
                                "chunk_id",
                                "start",
                                "end",
                                "content_sha256",
                                "score_kind",
                                "stage_scores",
                            )
                        }
                        for m in matches
                    ],
                    "material_chars": len(draft.model_dump_json()),
                    "limited": draft.limited,
                    "coverage": coverage(case, source, matches),
                    "trace": asdict(result.trace),
                }
            )
    return scope, observations


async def exercise_runs(client, headers, owner, sessions, runtime, suite, source, version):
    observations = []
    for case in suite.cases:
        model = ScriptedModel(case)
        captured = {}
        service = KnowledgeService(sessions, runtime)

        async def observed(context, arguments):
            captured["context"] = context
            return await service.search(context, arguments)

        registry = ToolRegistry((replace(service.spec(), handler=observed),))
        thread = client.post("/v1/threads", headers=headers, json={})
        assert thread.status_code == 201
        tid = thread.json()["id"]
        response = client.post(
            f"/v1/threads/{tid}/runs",
            headers=headers,
            json={"content": case.question, "idempotency_key": case.id},
        )
        assert response.status_code == 201
        rid = response.json()["id"]
        started = time.monotonic()
        outcome = await RunExecutor(
            ExecutionRepository(sessions),
            model,
            registry=registry,
            capabilities=frozenset({CAPABILITY}),
        ).execute(owner, UUID(tid), UUID(rid))
        duration = time.monotonic() - started
        path = f"/v1/threads/{tid}/runs/{rid}"
        state = client.get(path, headers=headers).json()
        events = client.get(path + "/events", headers=headers).json()["items"]
        expected_error = {
            "failure": "TOOL_EXECUTION_FAILED",
            "budget": "AGENT_BUDGET_EXCEEDED",
        }.get(case.mode)
        assert outcome == state["status"] == ("failed" if expected_error else "succeeded"), state
        assert state["error_code"] == expected_error
        context = captured["context"]
        budget = context.run_budget
        counts = Counter(a["purpose"] for a in model.attempts)
        assert counts["answer"] == budget.model_requests
        assert len(model.attempts) == budget.model_requests + budget.internal_requests <= 12
        assert budget.retrieval_queries <= 8 and counts["plan"] <= 1 and counts["rewrite"] <= 1
        assert not any(e["type"] == "message.delta" for e in events)
        assert len([e for e in events if e["type"] in {"run.completed", "run.failed"}]) == 1
        entry = {
            "case": case.id,
            "mode": case.mode,
            "decision_source": "controlled_fixture",
            "terminal": outcome,
            "error": state["error_code"],
            "controlled_end_to_end_seconds": duration,
            "model_attempts": len(model.attempts),
            "roles": dict(counts),
            "retry_failures": model.retry_failures,
            "retrieval_queries": budget.retrieval_queries,
            "tool_calls": budget.tool_calls,
            "initial_trace": asdict(context.tool_sessions["knowledge_search"].last_trace),
            "journal_types": [e["type"] for e in events],
        }
        async with sessions() as session:
            run = await session.get(Run, UUID(rid))
            entry["frozen_policy"] = {
                k: run.model_snapshot_json[k] for k in ("schema_version", "rag", "reasoning")
            }
            if run.model_snapshot_json.get("repairs") is not None:
                entry["frozen_policy"]["repairs"] = run.model_snapshot_json["repairs"]
        if expected_error:
            assert context.evidence.items == ()
            assert model.tool_result is None
        else:
            result = model.tool_result
            assert result is not None
            expected_grade = {
                "insufficient": "insufficient",
                "partial": "insufficient",
                "ambiguous": "ambiguous",
                "conflicting": "conflicting",
            }.get(case.mode, "sufficient")
            assert result["assessment"]["status"] == expected_grade
            assert all(m["version_id"] == version for m in result["matches"])
            entry.update(
                assessment=result["assessment"],
                coverage=coverage(case, source, result["matches"]),
                material_chars=len(json.dumps(result, ensure_ascii=False)),
                limited=result["limited"],
            )
            answer = client.get(path + "/answer", headers=headers)
            assert answer.status_code == 200
            materials = answer.json()["materials"]
            assert materials and materials["citations"]
            assert all(e["version_id"] == version for e in materials["evidence"])
            coverage(case, source, materials["evidence"])
            assert {c["citation_id"] for c in materials["citations"]} <= {
                e["citation_id"] for e in materials["evidence"]
            }
            entry["persisted_citations"] = len(materials["citations"])
            entry["last_draft_coverage"] = coverage(case, source, model.drafts[-1]["materials"])
            assert model.drafts[-1]["materials"] == result["matches"]
            entry["initial_draft_coverage"] = coverage(case, source, model.drafts[0]["materials"])
            entry["sufficient_with_missing_anchors"] = expected_grade == "sufficient" and bool(
                entry["coverage"]["missing"]
            )
        observations.append(entry)
    return observations


async def main(samples, write_observation=False, write_repair_observation=False):
    suite, source, suite_hash = load_cases()
    report = {
        "mode": "chapter06_published_comparison_controlled_reasoning",
        "passed": False,
        "suite_fingerprint": suite_hash,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "canonical_source": "UTF-8/LF",
        "paid_calls": 0,
        "paid_cost": 0,
        "model_quality_evaluated": False,
        "production_latency_evaluated": False,
        "scope_note": "Owned scope; BM25 statistics also depend on the shared collection corpus",
    }
    destination = ROOT / ".artifacts/rag-suite-check.json"
    destination.parent.mkdir(exist_ok=True)
    names = (
        "MINIMEW_DB_NAME",
        "MINIMEW_DOCUMENT_UPLOAD_DIR",
        "MINIMEW_KNOWLEDGE_ENABLED",
        "MINIMEW_REASONING_ENABLED",
        "MINIMEW_DOCUMENT_INDEX_LAYOUT",
        "MINIMEW_DEEPSEEK_API_KEY",
        "MINIMEW_JWT_SECRET",
        "HF_HUB_OFFLINE",
    )
    before = {n: os.environ.get(n) for n in names}
    notifications, execution = app_module.notification_lifespan, app_module.execution_lifespan
    app_module.notification_lifespan = app_module.execution_lifespan = offline_notifications
    try:
        async with temporary_database() as database:
            with tempfile.TemporaryDirectory(prefix="minimew-rag-suite-") as directory:
                os.environ.update(
                    MINIMEW_DB_NAME=database,
                    MINIMEW_DOCUMENT_UPLOAD_DIR=directory,
                    MINIMEW_KNOWLEDGE_ENABLED="false",
                    MINIMEW_REASONING_ENABLED="true",
                    MINIMEW_DOCUMENT_INDEX_LAYOUT="hybrid-jieba-bm25-v1",
                    MINIMEW_DEEPSEEK_API_KEY="controlled-no-network",
                    MINIMEW_JWT_SECRET="owned-rag-suite-signing-key-123456789",
                    HF_HUB_OFFLINE="1",
                )
                engine = create_engine(Settings())
                sessions = async_sessionmaker(engine, expire_on_commit=False)
                runtime = KnowledgeRuntime()
                manifests = []
                try:
                    async with sessions() as session:
                        await set_current_model(
                            session,
                            "owned-rag-suite",
                            ModelConfiguration(
                                provider="openai_compatible",
                                model_name="controlled-no-network",
                                base_url="https://models.example.invalid/v1",
                                temperature=0.2,
                                max_output_tokens=1024,
                                timeout_seconds=60.0,
                                thinking={"type": "disabled"},
                            ),
                        )
                    with TestClient(app_module.create_app()) as client:
                        credentials = {
                            "username": "rag_" + uuid4().hex[:16],
                            "password": "owned-suite-password-1234",
                        }
                        assert client.post("/auth/register", json=credentials).status_code == 201
                        login = client.post("/auth/login", json=credentials)
                        assert login.status_code == 200
                        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
                        owner = UUID(client.get("/auth/me", headers=headers).json()["id"])
                        upload = client.post(
                            "/v1/documents",
                            headers=headers,
                            data={"idempotency_key": "suite-corpus"},
                            files={"file": (suite.source, source.encode(), "text/markdown")},
                        )
                        assert upload.status_code == 202
                        version = upload.json()["version"]["id"]
                        await asyncio.to_thread(build_once)
                        cold = time.monotonic()
                        await runtime.start()
                        report["cold_load_seconds"] = time.monotonic() - cold
                        scope, observations = await compare_materials(
                            sessions, runtime, owner, suite, source, samples
                        )
                        assert (
                            len(scope.targets) == 1
                            and str(scope.targets[0].scope.identity.version_id) == version
                        )
                        report.update(
                            published=True,
                            scope=[asdict(m.scope) for m in scope.targets],
                            embedding=PROFILE.model_dump(mode="json"),
                            reranker={
                                "model": RagPolicy().reranker,
                                "revision": RagPolicy().revision,
                            },
                            retrieval=observations,
                        )
                        report["runs"] = await exercise_runs(
                            client, headers, owner, sessions, runtime, suite, source, version
                        )
                finally:
                    try:
                        # Includes registered candidates after a Worker failure; exact scopes only.
                        async with sessions() as session:
                            manifests = [
                                read_artifact(a)
                                for a in (await session.scalars(select(IndexArtifact))).all()
                            ]
                        if manifests and runtime.backend is None:
                            runtime.backend = await runtime.sync(
                                MilvusBackend.connect, runtime.vector_settings
                            )
                        if manifests and runtime.index is None:
                            runtime.index = CandidateIndex(runtime.backend)
                        for manifest in manifests:
                            await runtime.sync(runtime.index.delete_candidate, manifest.scope)
                        report["cleaned_exact_scopes"] = len(manifests)
                    finally:
                        await runtime.aclose()
                        await engine.dispose()
        report["passed"] = True  # Includes exact vector cleanup and temporary database removal.
    finally:
        app_module.notification_lifespan, app_module.execution_lifespan = notifications, execution
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        destination.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )
    for enabled, name in (
        (write_observation, "chapter06-observation.json"),
        (write_repair_observation, "chapter06-repair-observation.json"),
    ):
        if enabled:
            (ROOT / "examples/rag" / name).write_text(
                json.dumps(
                    portable_observation(
                        report,
                        "scripts/check_rag_suite.py "
                        + (
                            "--write-repair-observation"
                            if name == "chapter06-repair-observation.json"
                            else "--write-observation"
                        ),
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "retrieval_observations": len(report["retrieval"]),
                "run_cases": len(report["runs"]),
                "paid_calls": report["paid_calls"],
                "cleaned_exact_scopes": report["cleaned_exact_scopes"],
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, choices=range(1, 21), default=3)
    parser.add_argument(
        "--write-observation",
        action="store_true",
        help="显式更新不含设备时延/身份的共同观察资产（不是质量baseline）",
    )
    parser.add_argument(
        "--write-repair-observation",
        action="store_true",
        help="将修复后观察写入独立资产，保留原C包观察",
    )
    args = parser.parse_args()
    asyncio.run(main(args.samples, args.write_observation, args.write_repair_observation))
