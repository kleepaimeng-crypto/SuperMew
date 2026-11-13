"""观测保存重试与工具执行分离；模型/存储均为显式测试替身。"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from execution_helpers import FakeProvider
from sqlalchemy.exc import SQLAlchemyError
from test_agent import completed, executor, run
from test_deepseek import request
from test_execution_retry import Repository
from test_tools import Result, call

from minimew.execution.messages import ModelTurn
from minimew.execution.provider import ProviderCompleted
from minimew.execution.tools import ToolFailure


@pytest.mark.parametrize(
    "mode", ["success", "retry_result", "start_unavailable", "lost", "tool_failure"]
)
def test_observation_lifecycle_does_not_reexecute_tool(model_configuration, mode):
    async def scenario():
        repo = Repository(request(model_configuration))
        invoked, saved, failed_once = [], [], False

        async def observe(claim, kind, data):
            nonlocal failed_once
            assert set(data) == (
                {"tool_call_id", "tool_name", "error_code"}
                if kind == "tool.failed"
                else {"tool_call_id", "tool_name"}
            )
            if mode == "start_unavailable":
                raise SQLAlchemyError("private storage failure")
            if mode == "retry_result" and kind == "tool.completed" and not failed_once:
                failed_once = True
                raise SQLAlchemyError("lost commit acknowledgement")
            if mode == "lost" and kind == "tool.completed":
                return False
            saved.append(kind)
            return True

        repo.record_tool = AsyncMock(side_effect=observe)

        async def handler(context, arguments):
            invoked.append(arguments.query)
            if mode == "tool_failure":
                raise ToolFailure("KNOWLEDGE_RETRIEVAL_FAILED")
            return Result(text="private tool payload")

        async def answer(data):
            if not invoked:
                yield completed(call())
            else:
                yield ProviderCompleted(ModelTurn("text", "最终答案"))

        provider = FakeProvider(answer)
        result = await run(executor(repo, provider, handler), repo)
        assert result == (
            {"lost": "ownership_lost", "start_unavailable": "failed", "tool_failure": "failed"}.get(
                mode, "succeeded"
            )
        )
        assert len(invoked) == (0 if mode == "start_unavailable" else 1)
        assert len(provider.requests) == (2 if mode in {"success", "retry_result"} else 1)
        if mode in {"success", "retry_result"}:
            assert saved == ["tool.started", "tool.completed"]
        if mode == "tool_failure":
            assert saved == ["tool.started", "tool.failed"]
            assert repo.finish.await_args.kwargs["error_code"] == "KNOWLEDGE_RETRIEVAL_FAILED"
        if mode == "start_unavailable":
            assert repo.record_tool.await_count == 2
            assert repo.finish.await_args.kwargs["error_code"] == "EXECUTION_STORAGE_UNAVAILABLE"

    asyncio.run(scenario())
