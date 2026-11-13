"""真实浏览器/API/PG + 独立CPU/Milvus Worker；只使用自有临时库/源目录/scope。"""

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from uuid import uuid4

import httpx
from check_models import ROOT
from check_runtime import temporary_database
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from minimew.database import create_engine
from minimew.documents.artifacts import read_artifact
from minimew.model_control import ModelConfiguration, set_current_model
from minimew.models import Document, IndexArtifact, IndexJob, IndexWorkerHeartbeat
from minimew.settings import Settings
from minimew.vector_store.adapter import CandidateIndex
from minimew.vector_store.config import VectorSettings
from minimew.vector_store.milvus import MilvusBackend


async def stop(process):
    if process.poll() is None:
        process.stdin.write("stop\n")
        process.stdin.flush()
        try:
            await asyncio.to_thread(process.wait, timeout=25)
        except subprocess.TimeoutExpired:
            process.kill()
            await asyncio.to_thread(process.wait, timeout=10)
    process.stdin.close()
    if process.returncode:
        raise RuntimeError("Owned browser process did not stop cleanly; inspect local log")


async def main():
    knowledge = os.environ.get("MINIMEW_KNOWLEDGE_BROWSER_TESTS") == "1"
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", 5175))
        except OSError:
            raise SystemExit("Port 5175 is occupied; no existing process will be stopped") from None
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        raise SystemExit("npm is required")
    folder = ROOT / ".artifacts" / ("document-browser-" + uuid4().hex)
    folder.mkdir(parents=True)
    async with temporary_database() as database:
        with tempfile.TemporaryDirectory(prefix="minimew-document-browser-") as source_dir:
            environment = dict(
                os.environ,
                MINIMEW_DB_NAME=database,
                MINIMEW_INTEGRATION_DB=database,
                MINIMEW_DOCUMENT_UPLOAD_DIR=source_dir,
                MINIMEW_DOCUMENT_BROWSER_TESTS="1",
                MINIMEW_CHAT_BROWSER_TESTS="1",
                MINIMEW_DEEPSEEK_API_KEY="test-only-no-chat-network",
                MINIMEW_KNOWLEDGE_ENABLED="true" if knowledge else "false",
                MINIMEW_EXECUTION_SCAN_SECONDS="0.05",
                MINIMEW_EVENT_POLL_SECONDS="0.05",
                MINIMEW_CANCELLATION_POLL_SECONDS="0.05",
                MINIMEW_INDEX_POLL_SECONDS="0.1",
                HF_HUB_OFFLINE="1",
            )
            engine = create_engine(Settings().model_copy(update={"db_name": database}))
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            if knowledge:
                async with sessions() as session:
                    await set_current_model(
                        session,
                        "knowledge-browser",
                        ModelConfiguration(
                            provider="openai_compatible",
                            model_name="controlled-answer",
                            base_url="https://api.deepseek.com",
                            temperature=0.2,
                            max_output_tokens=1024,
                            timeout_seconds=30.0,
                            thinking={"type": "disabled"},
                        ),
                    )
            api = worker = backend = None
            logs = []
            try:

                def launch(name, args):
                    log = (folder / f"{name}.log").open("w", encoding="utf-8")
                    logs.append(log)
                    return subprocess.Popen(
                        [sys.executable, *args],
                        cwd=ROOT,
                        env=environment,
                        stdin=subprocess.PIPE,
                        text=True,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )

                info = folder / "address.json"
                api = launch("api", [str(ROOT / "tests/frontend_process.py"), str(info)])
                async with httpx.AsyncClient(trust_env=False) as client:
                    for _ in range(200):
                        if api.poll() is not None:
                            raise RuntimeError("Owned API exited; inspect local log")
                        if info.exists():
                            base = f"http://127.0.0.1:{json.loads(info.read_text())['port']}"
                            try:
                                if (
                                    await client.get(base + "/health/ready", timeout=1)
                                ).status_code == 200:
                                    break
                            except httpx.HTTPError:
                                pass
                        await asyncio.sleep(0.1)
                    else:
                        raise RuntimeError("Owned API readiness timed out")
                worker = launch("worker", [str(ROOT / "tests/indexing_process.py")])
                for _ in range(600):
                    if worker.poll() is not None:
                        raise RuntimeError("Owned indexing worker exited; inspect local log")
                    async with sessions() as session:
                        ready = await session.scalar(
                            select(IndexWorkerHeartbeat).where(
                                IndexWorkerHeartbeat.status == "running"
                            )
                        )
                    if ready:
                        break
                    await asyncio.sleep(0.2)
                else:
                    raise RuntimeError("Owned indexing worker readiness timed out")
                await asyncio.to_thread(
                    subprocess.run,
                    [
                        npm,
                        "run",
                        "test:e2e",
                        "--",
                        "tests/knowledge-live.spec.ts"
                        if knowledge
                        else "tests/documents-live.spec.ts",
                    ],
                    cwd=ROOT / "frontend",
                    check=True,
                    timeout=180,
                    env=dict(environment, MINIMEW_DOCUMENT_LIVE="1", MINIMEW_API_TARGET=base),
                )
                async with sessions() as session:
                    jobs = (await session.scalars(select(IndexJob))).all()
                    documents = (await session.scalars(select(Document))).all()
                    artifacts = (await session.scalars(select(IndexArtifact))).all()
                assert len(jobs) == 2 and sorted(job.status for job in jobs) == (
                    ["succeeded", "succeeded"] if knowledge else ["failed", "succeeded"]
                )
                assert len(documents) == 1 and documents[0].current_version_id is not None
                backend = await asyncio.to_thread(MilvusBackend.connect, VectorSettings())
                for artifact in artifacts:
                    await asyncio.to_thread(CandidateIndex(backend).verify, read_artifact(artifact))
                report = {
                    "browser": "passed",
                    "real_cpu_milvus": True,
                    "job_statuses": sorted(job.status for job in jobs),
                    "manifest_counts": [len(read_artifact(a).entries) for a in artifacts],
                    "owned_processes": 2,
                    "dev_database_consumed": False,
                    "paid_model_calls": 0,
                    "knowledge_ui": knowledge,
                }
                (folder / "report.json").write_text(json.dumps(report, indent=2) + "\n")
                print(json.dumps(report))
            finally:
                body_failed = sys.exc_info()[0] is not None
                errors = []
                for process in (worker, api):
                    if process:
                        try:
                            await stop(process)
                        except Exception as exc:
                            errors.append(exc)
                try:
                    async with sessions() as session:
                        artifacts = (await session.scalars(select(IndexArtifact))).all()
                    if artifacts and backend is None:
                        backend = await asyncio.to_thread(MilvusBackend.connect, VectorSettings())
                    for artifact in artifacts:
                        await asyncio.to_thread(
                            CandidateIndex(backend).delete_candidate, read_artifact(artifact).scope
                        )
                finally:
                    if backend:
                        await asyncio.to_thread(backend.close)
                    for log in logs:
                        log.close()
                    await engine.dispose()
                if errors and not body_failed:
                    raise errors[0]
    print(f"Owned document browser data/processes cleaned; report: {folder.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
