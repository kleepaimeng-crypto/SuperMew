"""固定案例复用正式KnowledgeService/RunExecutor，不联网，不冒充语义评测。"""

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from execution_helpers import FakeProvider
from test_deepseek import request
from test_evidence import mixed
from test_execution_retry import Repository
from test_knowledge import setup_service

from minimew.evidence import SUPPLEMENT_HEADING
from minimew.execution.messages import InputMessage, ModelTurn, ToolCall
from minimew.execution.provider import ProviderCompleted, ProviderFailure
from minimew.execution.service import RunExecutor
from minimew.knowledge import NAME
from minimew.vector_store.manifest import VectorError

cases = json.loads(
    (Path(__file__).resolve().parents[1] / "examples/rag/chapter05-cases.json").read_text(
        encoding="utf-8"
    )
)["cases"]


@pytest.mark.parametrize("case", cases, ids=[c["id"] for c in cases])
def test_fixed_knowledge_case(monkeypatch, model_configuration, case):
    _, registry, context, _, scoped, search, _ = setup_service(monkeypatch)
    mode = case["mode"]
    if mode == "empty":
        scoped.return_value = replace(scoped.return_value, targets=())
    if mode == "tool_failure":
        search.side_effect = VectorError("VECTOR_UNAVAILABLE")

    async def scenario():
        data = replace(
            request(model_configuration), messages=(InputMessage("user", case["question"]),)
        )
        repo = Repository(data)

        async def respond(data):
            if data.messages[-1].role != "tool":
                assert data.messages[-1].content == case["question"]
                yield ProviderCompleted(
                    ModelTurn(
                        "tool_calls",
                        "",
                        (ToolCall(case["id"], NAME, json.dumps({"query": case["question"]})),),
                    )
                )
            elif mode == "provider_failure":
                raise ProviderFailure("PROVIDER_ERROR")
            elif mode in {"empty", "insufficient"}:
                yield ProviderCompleted(
                    ModelTurn("text", SUPPLEMENT_HEADING + "\n本次资料不足，仅可给出一般建议。")
                )
            else:
                content = mixed(citation_id="E99" if mode == "forged_citation" else "E1")
                if mode == "grounded":
                    content = content.split("\n\n" + SUPPLEMENT_HEADING)[0]
                yield ProviderCompleted(ModelTurn("text", content))

        provider = FakeProvider(respond)
        executor = RunExecutor(repo, provider, registry=registry, capabilities=context.capabilities)
        assert (
            await executor.execute(context.user_id, repo.data.thread_id, repo.data.run_id)
            == case["expected_status"]
        )
        saved = repo.finish.await_args.kwargs
        assert saved["error_code"] == case["expected_error"]
        materials = saved.get("materials")
        assert (len(materials.citations) if materials else 0) == case["expected_citations"]
        assert len(provider.requests) == (1 if mode == "tool_failure" else 2)
        phases = [call.args[1] for call in repo.record_tool.await_args_list]
        assert phases == [
            "tool.started",
            "tool.failed" if mode == "tool_failure" else "tool.completed",
        ]

    asyncio.run(scenario())
