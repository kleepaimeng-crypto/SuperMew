"""真实 CPU/Milvus/PG + 暂停/接收/恢复三个 API 进程；模型回答受控，收费 0。"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
from check_knowledge import build_once, offline_notifications
from check_runtime import temporary_database
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import minimew.app as app_module
from minimew.database import create_engine
from minimew.documents.artifacts import read_artifact
from minimew.knowledge_runtime import KnowledgeRuntime
from minimew.model_control import ModelConfiguration, set_current_model
from minimew.models import IndexArtifact
from minimew.settings import Settings
from minimew.vector_store.adapter import CandidateIndex
from minimew.vector_store.config import VectorSettings
from minimew.vector_store.milvus import MilvusBackend

ROOT = Path(__file__).resolve().parents[1]


@asynccontextmanager
async def api_process(directory, phase):
    ready = Path(directory) / (phase + ".json")
    log = open(Path(directory) / (phase + ".log"), "w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts/hitl_process.py"), str(ready), phase],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
        env=dict(os.environ, MINIMEW_KNOWLEDGE_ENABLED="false" if phase == "accept" else "true"),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        async with asyncio.timeout(90):
            while not ready.exists():
                assert process.poll() is None, f"API {phase} exited before handshake"
                await asyncio.sleep(0.05)
            port = json.loads(ready.read_text())["port"]
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False
            ) as client:
                while True:
                    assert process.poll() is None, f"API {phase} exited during initialization"
                    try:
                        if (await client.get("/health/live")).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(0.1)
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", timeout=15, trust_env=False
        ) as client:
            yield client
    finally:
        try:
            await asyncio.to_thread(process.communicate, "\n", timeout=15)
        except subprocess.TimeoutExpired:
            process.terminate()
            await asyncio.to_thread(process.wait, timeout=10)
        finally:
            log.close()
        import shutil

        shutil.copyfile(
            Path(directory) / (phase + ".log"), ROOT / ".artifacts" / ("hitl-" + phase + ".log")
        )
        assert process.returncode == 0, f"API {phase} exited with {process.returncode}"


async def wait_status(client, path, headers, wanted):
    async with asyncio.timeout(65):
        while True:
            response = await client.get(path, headers=headers)
            assert response.status_code == 200
            state = response.json()
            if state["status"] == wanted:
                return state
            assert state["status"] not in {"failed", "cancelled"}, state.get("error_code")
            await asyncio.sleep(0.05)


async def main():
    report = {
        "passed": False,
        "paid_model_calls": 0,
        "mode": "real_pg_cpu_milvus_pause_accept_resume_processes",
    }
    names = [
        "MINIMEW_DB_NAME",
        "MINIMEW_DOCUMENT_UPLOAD_DIR",
        "MINIMEW_KNOWLEDGE_ENABLED",
        "MINIMEW_DEEPSEEK_API_KEY",
        "MINIMEW_JWT_SECRET",
        "MINIMEW_EXECUTION_SCAN_SECONDS",
        "MINIMEW_REASONING_ENABLED",
        "MINIMEW_REPAIRS_ENABLED",
        "MINIMEW_HITL_ENABLED",
        "MINIMEW_HITL_CHECK",
        "HF_HUB_OFFLINE",
        "HF_HUB_DISABLE_TELEMETRY",
        "TRANSFORMERS_OFFLINE",
    ]
    before = {k: os.environ.get(k) for k in names}
    old_exec, old_notify = app_module.execution_lifespan, app_module.notification_lifespan
    try:
        async with temporary_database() as database:
            with tempfile.TemporaryDirectory(prefix="minimew-hitl-") as directory:
                os.environ.update(
                    MINIMEW_DB_NAME=database,
                    MINIMEW_DOCUMENT_UPLOAD_DIR=directory,
                    MINIMEW_KNOWLEDGE_ENABLED="false",
                    MINIMEW_DEEPSEEK_API_KEY="controlled-no-network",
                    MINIMEW_JWT_SECRET="owned-hitl-check-signing-key-123456789",
                    MINIMEW_EXECUTION_SCAN_SECONDS="0.02",
                    MINIMEW_REASONING_ENABLED="true",
                    MINIMEW_REPAIRS_ENABLED="true",
                    MINIMEW_HITL_ENABLED="true",
                    MINIMEW_HITL_CHECK="1",
                    HF_HUB_OFFLINE="1",
                    HF_HUB_DISABLE_TELEMETRY="1",
                    TRANSFORMERS_OFFLINE="1",
                )
                engine = create_engine(Settings())
                sessions = async_sessionmaker(engine, expire_on_commit=False)
                try:
                    async with sessions() as session:
                        await set_current_model(
                            session,
                            "hitl-check",
                            ModelConfiguration(
                                provider="openai_compatible",
                                model_name="controlled-hitl",
                                base_url="https://models.example.invalid/v1",
                                temperature=0.2,
                                max_output_tokens=1024,
                                timeout_seconds=60.0,
                                thinking={"type": "disabled"},
                            ),
                        )
                    app_module.execution_lifespan = offline_notifications
                    app_module.notification_lifespan = offline_notifications
                    with TestClient(app_module.create_app()) as client:
                        credentials = {
                            "username": "hitl_" + uuid4().hex[:16],
                            "password": "owned-hitl-password-1234",
                        }
                        assert client.post("/auth/register", json=credentials).status_code == 201
                        login = client.post("/auth/login", json=credentials)
                        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
                        upload = client.post(
                            "/v1/documents",
                            headers=headers,
                            data={"idempotency_key": "owned"},
                            files={
                                "file": (
                                    "policy.md",
                                    (
                                        "# 差旅\n\n国内培训餐费每人每天150元，须提供票据。"
                                        "海外按当地标准。"
                                    ).encode(),
                                )
                            },
                        )
                        assert upload.status_code == 202
                        version = upload.json()["version"]["id"]
                    await asyncio.to_thread(build_once)
                    os.environ["MINIMEW_KNOWLEDGE_ENABLED"] = "true"
                    async with api_process(directory, "pause") as client:
                        tid = (await client.post("/v1/threads", headers=headers, json={})).json()[
                            "id"
                        ]
                        created = (
                            await client.post(
                                f"/v1/threads/{tid}/runs",
                                headers=headers,
                                json={
                                    "idempotency_key": "question",
                                    "content": "培训餐费能报多少？",
                                },
                            )
                        ).json()
                        rid = created["id"]
                        path = f"/v1/threads/{tid}/runs/{rid}"
                        await wait_status(client, path, headers, "waiting_input")
                        before_events = (
                            await client.get(path + "/events", headers=headers)
                        ).json()["items"]
                        assert before_events[-1]["type"] == "hitl.required"
                    # 第一进程已退出；接收进程只读 PG 等待事实，不领取或加载知识资源。
                    async with api_process(directory, "accept") as client:
                        assert (await client.get(path, headers=headers)).json()[
                            "status"
                        ] == "waiting_input"
                        credential = (
                            await client.post(path + "/resume-credential", headers=headers)
                        ).json()
                        resume = await client.post(
                            path + "/resume",
                            headers=headers,
                            json={
                                "checkpoint_id": credential["checkpoint_id"],
                                "token": credential["token"],
                                "idempotency_key": "resume",
                                "answer": "国内",
                            },
                        )
                        assert resume.status_code == 202
                        assert (await client.get(path, headers=headers)).json()[
                            "status"
                        ] == "resume_pending"
                    # 接收进程没有 Provider/知识运行资源，不能提前领取；退出后由第三进程领取。
                    async with api_process(directory, "resume") as client:
                        await wait_status(client, path, headers, "succeeded")
                        events = (await client.get(path + "/events", headers=headers)).json()[
                            "items"
                        ]
                        assert [e["type"] for e in events] == [
                            "run.created",
                            "run.started",
                            "tool.started",
                            "hitl.required",
                            "hitl.accepted",
                            "run.resumed",
                            "tool.completed",
                            "message.completed",
                            "run.completed",
                        ]
                        assert events[-2]["data"]["message_id"] == created["assistant_message_id"]
                        answer = (await client.get(path + "/answer", headers=headers)).json()
                        assert answer["materials"]["evidence"][0]["version_id"] == version
                        assert "150" in events[-2]["data"]["content"]
                        report.update(
                            journal_events=len(events),
                            api_processes=3,
                            evidence_citations=len(answer["materials"]["citations"]),
                        )
                    measured = [
                        json.loads((Path(directory) / (phase + ".calls.json")).read_text())
                        for phase in ("pause", "accept", "resume")
                    ]
                    assert measured == [["answer", "grade"], [], ["grade", "answer"]]
                    report["model_attempts"] = sum(len(calls) for calls in measured)
                finally:
                    cleanup = KnowledgeRuntime()
                    try:
                        async with sessions() as session:
                            artifacts = (await session.scalars(select(IndexArtifact))).all()
                        manifests = [read_artifact(a) for a in artifacts]
                        if manifests:
                            cleanup.backend = await cleanup.sync(
                                MilvusBackend.connect, VectorSettings()
                            )
                            index = CandidateIndex(cleanup.backend)
                            for manifest in manifests:
                                await cleanup.sync(index.delete_candidate, manifest.scope)
                        report["cleaned_exact_scopes"] = len(manifests)
                    finally:
                        await cleanup.aclose()
                        await engine.dispose()
        report["passed"] = True
    finally:
        app_module.execution_lifespan, app_module.notification_lifespan = old_exec, old_notify
        for k, v in before.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        target = ROOT / ".artifacts/hitl-check.json"
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
