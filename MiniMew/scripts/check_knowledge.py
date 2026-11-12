"""临时PG + 真实轻量CPU/本机Milvus + 受控回答Provider；不产生收费调用。"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from check_runtime import temporary_database
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import minimew.app as app_module
import minimew.execution.deepseek as answer_module
from minimew.database import create_engine
from minimew.documents.artifacts import read_artifact
from minimew.execution.messages import ModelTurn, ToolCall
from minimew.execution.provider import ProviderCompleted
from minimew.knowledge_runtime import KnowledgeRuntime
from minimew.model_control import ModelConfiguration, set_current_model
from minimew.models import IndexArtifact
from minimew.settings import Settings
from minimew.vector_store.adapter import CandidateIndex
from minimew.vector_store.config import VectorSettings
from minimew.vector_store.milvus import MilvusBackend

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / ".artifacts/knowledge-check.json"


class ControlledAnswer:
    """只替换模型决策；知识Tool、编码、检索、Run/Journal均走正式实现。"""

    def __init__(self):
        self.requests = 0
        self.tool_result = None

    async def stream(self, request):
        self.requests += 1
        if request.messages[-1].role != "tool":
            assert request.tools[0].name == "knowledge_search"
            yield ProviderCompleted(
                ModelTurn(
                    "tool_calls",
                    "",
                    (
                        ToolCall(
                            "call_meal",
                            "knowledge_search",
                            '{"query":"出差餐费每天最多报销多少？"}',
                        ),
                    ),
                )
            )
        else:
            self.tool_result = json.loads(request.messages[-1].content)
            assert self.tool_result["status"] == "ok"
            yield ProviderCompleted(
                ModelTurn("text", "检索原文：" + self.tool_result["matches"][0]["text"])
            )


@asynccontextmanager
async def offline_notifications(sessions, settings):
    # 本检查只验收知识链，不创建Redis通知key。
    yield None


def build_once():
    result = subprocess.run(
        [sys.executable, "-m", "minimew.documents.worker", "--once"],
        cwd=ROOT,
        env=dict(os.environ, HF_HUB_OFFLINE="1", MINIMEW_KNOWLEDGE_ENABLED="false"),
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode:
        raise AssertionError(f"Owned indexing Worker failed: exit {result.returncode}")


async def main():
    report = {"mode": "real_cpu_milvus_controlled_answer", "paid_model_calls": 0, "passed": False}
    names = (
        "MINIMEW_DB_NAME",
        "MINIMEW_DOCUMENT_UPLOAD_DIR",
        "MINIMEW_KNOWLEDGE_ENABLED",
        "MINIMEW_DEEPSEEK_API_KEY",
        "MINIMEW_JWT_SECRET",
        "MINIMEW_EXECUTION_SCAN_SECONDS",
    )
    before = {name: os.environ.get(name) for name in names}
    old_provider, old_notifications = (
        answer_module.DeepSeekProvider,
        app_module.notification_lifespan,
    )
    controlled = ControlledAnswer()
    answer_module.DeepSeekProvider = lambda key: controlled
    app_module.notification_lifespan = offline_notifications
    try:
        async with temporary_database() as database:
            with tempfile.TemporaryDirectory(prefix="minimew-knowledge-check-") as directory:
                os.environ.update(
                    MINIMEW_DB_NAME=database,
                    MINIMEW_DOCUMENT_UPLOAD_DIR=directory,
                    MINIMEW_KNOWLEDGE_ENABLED="false",
                    MINIMEW_DEEPSEEK_API_KEY="owned-test-no-network",
                    MINIMEW_JWT_SECRET="owned-knowledge-check-signing-key-123456789",
                    MINIMEW_EXECUTION_SCAN_SECONDS="0.02",
                )
                engine = create_engine(Settings())
                sessions = async_sessionmaker(engine, expire_on_commit=False)
                try:
                    async with sessions() as session:
                        await set_current_model(
                            session,
                            "owned-knowledge-check",
                            ModelConfiguration(
                                provider="openai_compatible",
                                model_name="controlled-answer",
                                base_url="https://models.example.invalid/v1",
                                temperature=0.2,
                                max_output_tokens=1024,
                                timeout_seconds=60.0,
                                thinking={"type": "disabled"},
                            ),
                        )
                    with TestClient(app_module.create_app()) as client:
                        credentials = {
                            "username": "knowledge_" + uuid4().hex[:16],
                            "password": "owned-knowledge-check-password-1234",
                        }
                        assert client.post("/auth/register", json=credentials).status_code == 201
                        login = client.post("/auth/login", json=credentials)
                        assert login.status_code == 200
                        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
                        uploaded = client.post(
                            "/v1/documents",
                            headers=headers,
                            data={"idempotency_key": "owned-document"},
                            files={
                                "file": (
                                    "policy.md",
                                    (
                                        "# 差旅制度\n\n## 餐费\n"
                                        "出差餐费每人每天最高报销150元，须提供票据。\n\n"
                                        "## 住宿\n住宿每晚最高报销350元。"
                                    ).encode(),
                                )
                            },
                        )
                        assert uploaded.status_code == 202
                        version = uploaded.json()["version"]["id"]
                        doc = uploaded.json()["document"]["id"]
                    await asyncio.to_thread(build_once)
                    os.environ["MINIMEW_KNOWLEDGE_ENABLED"] = "true"
                    with TestClient(app_module.create_app()) as client:
                        assert (
                            client.get(f"/v1/documents/{doc}", headers=headers).json()[
                                "current_version_id"
                            ]
                            == version
                        )
                        thread = client.post("/v1/threads", headers=headers, json={}).json()["id"]
                        response = client.post(
                            f"/v1/threads/{thread}/runs",
                            headers=headers,
                            json={
                                "idempotency_key": "owned-question",
                                "content": "出差餐费每天最多报销多少？",
                            },
                        )
                        assert response.status_code == 201
                        run_id = response.json()["id"]
                        path = f"/v1/threads/{thread}/runs/{run_id}"
                        deadline = time.monotonic() + 65
                        while True:
                            state = client.get(path, headers=headers).json()
                            if state["status"] in ("succeeded", "failed"):
                                break
                            assert time.monotonic() < deadline, "Knowledge Run timeout"
                            await asyncio.sleep(0.05)
                        assert state["status"] == "succeeded", state.get("error_code")
                        result = controlled.tool_result
                        assert result and "150" in result["matches"][0]["text"]
                        assert result["matches"][0]["version_id"] == version
                        events = client.get(path + "/events", headers=headers).json()["items"]
                        assert [e["type"] for e in events] == [
                            "run.created",
                            "run.started",
                            "message.completed",
                            "run.completed",
                        ]
                        assert "150" in events[-2]["data"]["content"]
                        assert controlled.requests == 2
                        report.update(
                            passed=True,
                            model_requests=controlled.requests,
                            version_id=version,
                            top_chunk_id=result["matches"][0]["chunk_id"],
                            top_score=result["matches"][0]["score"],
                            terminal=state["status"],
                            journal_events=len(events),
                        )
                finally:
                    cleanup = KnowledgeRuntime()
                    try:
                        cleanup.backend = await cleanup.sync(
                            MilvusBackend.connect, VectorSettings()
                        )
                        index = CandidateIndex(cleanup.backend)
                        async with sessions() as session:
                            artifacts = (await session.scalars(select(IndexArtifact))).all()
                        manifests = [read_artifact(artifact) for artifact in artifacts]
                        report["owned_scopes"] = [asdict(manifest.scope) for manifest in manifests]
                        for manifest in manifests:
                            await cleanup.sync(index.delete_candidate, manifest.scope)
                        report["cleaned_exact_scopes"] = len(manifests)
                    finally:
                        await cleanup.aclose()
                        await engine.dispose()
    finally:
        answer_module.DeepSeekProvider = old_provider
        app_module.notification_lifespan = old_notifications
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        REPORT.parent.mkdir(exist_ok=True)
        REPORT.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "owned_scopes"},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
