"""隔离 PG + 独立真实 CPU/Milvus Worker 进程；只清理本检查创建的 exact scope。"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

from check_runtime import temporary_database
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from minimew.app import create_app
from minimew.database import create_engine
from minimew.documents.artifacts import read_artifact
from minimew.documents.retrieval import published_scope, search_published
from minimew.embeddings.adapter import LocalEmbeddingAdapter
from minimew.embeddings.config import EmbeddingRuntimeSettings
from minimew.models import IndexArtifact, IndexJob
from minimew.settings import Settings
from minimew.vector_store.adapter import CandidateIndex
from minimew.vector_store.config import VectorSettings
from minimew.vector_store.manifest import PROFILE
from minimew.vector_store.milvus import MilvusBackend

ROOT = Path(__file__).resolve().parents[1]


def run_worker():
    # 标准正式入口；不是测试专用构建 handler，不使用线程代替进程。
    result = subprocess.run(
        [sys.executable, "-m", "minimew.documents.worker", "--once"],
        cwd=ROOT,
        env=dict(os.environ, HF_HUB_OFFLINE="1"),
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode:
        # 不打印可能含环境/参数的完整异常，检查失败有明确退出码。
        raise AssertionError(f"Worker process failed: exit {result.returncode}")


async def main():
    report = {}
    async with temporary_database() as database:
        before = {
            name: os.environ.get(name)
            for name in (
                "MINIMEW_DB_NAME",
                "MINIMEW_DOCUMENT_UPLOAD_DIR",
                "MINIMEW_REDIS_KEY_PREFIX",
            )
        }
        backend = engine = None
        try:
            with tempfile.TemporaryDirectory(prefix="minimew-index-check-") as directory:
                os.environ.update(
                    MINIMEW_DB_NAME=database,
                    MINIMEW_DOCUMENT_UPLOAD_DIR=directory,
                    MINIMEW_REDIS_KEY_PREFIX="minimew:index-check:" + uuid4().hex,
                )
                engine = create_engine(Settings())
                sessions = async_sessionmaker(engine, expire_on_commit=False)
                backend = await asyncio.to_thread(MilvusBackend.connect, VectorSettings())
                index = CandidateIndex(backend)

                async def validate():
                    # API 独立 lifespan/连接，上传真实 sample；不使用测试 fixtures。
                    with TestClient(create_app()) as client:
                        credentials = {
                            "username": "index_" + uuid4().hex[:16],
                            "password": "owned-process-check-1234",
                        }
                        assert client.post("/auth/register", json=credentials).status_code == 201
                        login = client.post("/auth/login", json=credentials)
                        assert login.status_code == 200
                        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
                        owner = UUID(client.get("/auth/me", headers=headers).json()["id"])
                        source = (ROOT / "examples/rag/expense-policy.md").read_bytes()
                        first = client.post(
                            "/v1/documents",
                            headers=headers,
                            data={"idempotency_key": "process-upload"},
                            files={"file": ("expense.md", source)},
                        )
                        assert first.status_code == 202, first.status_code
                        value = first.json()
                        assert value["job"]["status"] == "pending"
                        assert not (await published_scope(sessions, owner)).targets
                        await asyncio.to_thread(run_worker)
                        job = client.get(
                            "/v1/index-jobs/" + value["job"]["id"], headers=headers
                        ).json()
                        assert job["status"] == "succeeded", job["status"]
                        scope = await published_scope(sessions, owner)
                        assert len(scope.targets) == 1
                        manifest = scope.targets[0]
                        await asyncio.to_thread(index.verify, manifest)
                        encoder = await asyncio.to_thread(
                            LocalEmbeddingAdapter.load_cpu, PROFILE, EmbeddingRuntimeSettings()
                        )
                        vector = await asyncio.to_thread(
                            encoder.encode_query, "出差餐费每天最多报销多少？"
                        )
                        hits = await search_published(
                            sessions, index, scope, owner, vector, PROFILE
                        )
                        assert hits and "150" in hits[0]["payload"]["source_text"]
                        assert (
                            hits[0]["payload"]["identity"]["version_id"] == value["version"]["id"]
                        )
                        assert not (await published_scope(sessions, uuid4())).targets
                        second = client.post(
                            "/v1/documents/" + value["document"]["id"] + "/versions",
                            headers=headers,
                            data={"idempotency_key": "process-invalid-v2"},
                            files={"file": ("bad.md", b"```\n" + b"long " * 900 + b"\n```\n")},
                        )
                        assert second.status_code == 202
                        await asyncio.to_thread(run_worker)
                        failed = client.get(
                            "/v1/index-jobs/" + second.json()["job"]["id"], headers=headers
                        ).json()
                        assert (
                            failed["status"] == "failed"
                            and failed["error_code"] == "INVALID_SOURCE"
                        )
                        unchanged = await published_scope(sessions, owner)
                        assert unchanged.targets == scope.targets
                        await asyncio.to_thread(index.verify, manifest)
                        report.update(
                            published=True,
                            version_id=value["version"]["id"],
                            expected_chunks=len(manifest.entries),
                            top_score=hits[0]["score"],
                            top_text=hits[0]["payload"]["source_text"],
                            independent_worker_processes=2,
                            failed_new_version_preserves_current=True,
                            candidate_not_visible_before_publish=True,
                            other_owner_scope_empty=True,
                        )

                try:
                    await validate()
                finally:
                    # 临时库中只有本检查创建的产物。即使断言失败也按各自 scope 清理。
                    async with sessions() as session:
                        artifacts = (await session.scalars(select(IndexArtifact))).all()
                        jobs = (await session.scalars(select(IndexJob))).all()
                        assert all(job.user_id for job in jobs)
                    for artifact in artifacts:
                        await asyncio.to_thread(
                            index.delete_candidate, read_artifact(artifact).scope
                        )
                    report["owned_scopes_cleaned"] = len(artifacts)
        finally:
            if backend:
                await asyncio.to_thread(backend.close)
            if engine:
                await engine.dispose()
            for name, value in before.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
    path = ROOT / ".artifacts/indexing-process-check.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
