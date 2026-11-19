"""故障诊断不得记录异常文本、SQL参数、模型正文或Secret。"""

import asyncio
import json
import logging
from dataclasses import replace
from uuid import uuid4

import pytest
from execution_helpers import FakeProvider
from sqlalchemy.exc import SQLAlchemyError
from test_agent import completed, executor, run
from test_deepseek import request
from test_execution_retry import Repository
from test_tools import Result, call

from minimew.execution.diagnostics import capture, emit
from minimew.execution.messages import ModelTurn
from minimew.execution.provider import ProviderCompleted
from minimew.execution.service import RunExecutor

SECRET = "sk-private-token postgres://user:password@host/private private-document"


def records(caplog):
    return [
        json.loads(r.message.split("execution_diagnostic ", 1)[1])
        for r in caplog.records
        if "execution_diagnostic " in r.message
    ]


@pytest.mark.parametrize(
    "mode,stage,code",
    [
        ("provider", "answer_model", "PROVIDER_ERROR"),
        ("tool", "tool", "TOOL_EXECUTION_FAILED"),
        ("result", "tool_result", "TOOL_INVALID_RESULT"),
        ("answer", "answer_validation", "PROVIDER_INVALID_RESPONSE"),
    ],
)
def test_handled_failure_is_correlated_once_without_private_input(
    caplog, model_configuration, mode, stage, code
):
    caplog.set_level(logging.WARNING)

    async def scenario():
        repo = Repository(request(model_configuration))
        calls = 0

        async def handler(context, args):
            if mode == "tool":
                raise RuntimeError(SECRET)
            if mode == "result":
                return {"text": {"credential": SECRET}}
            context.evidence.queried = True
            return Result(text="ready")

        async def answer(data):
            nonlocal calls
            calls += 1
            if mode == "provider":
                raise RuntimeError(SECRET)
            if calls == 1:
                yield completed(call())
            else:
                yield ProviderCompleted(ModelTurn("text", f"## 文档依据\n{SECRET}\n\n正文[E1]"))

        assert await run(executor(repo, FakeProvider(answer), handler), repo) == "failed"
        assert repo.finish.await_args.kwargs["error_code"] == code

    asyncio.run(scenario())
    entries = records(caplog)
    handled = [r for r in entries if r["event"] == "execution_failure_handled"]
    assert len(handled) == 1
    entry = handled[0]
    assert entry["stage"] == stage and entry["code"] == code
    assert entry["run_id"] and entry["thread_id"] and entry["stack"]
    if mode in {"tool", "result"}:
        assert entry["tool_call_id"] == "call_1"
    if mode == "answer":
        assert entry["reason"] == "document_paragraph_missing_citation"
    assert SECRET not in caplog.text and "password@host" not in caplog.text


def test_internal_model_diagnostic_survives_tool_boundary(caplog, model_configuration, monkeypatch):
    from test_knowledge import setup_service

    from minimew.execution.messages import ToolCall
    from minimew.model_control import ModelSnapshot
    from minimew.rag.protocol import RagPolicy
    from minimew.rag.reasoning import ReasoningPolicy

    _, registry, context, _, _, _, _ = setup_service(monkeypatch)
    data = request(model_configuration)
    data = replace(
        data,
        snapshot=ModelSnapshot(
            schema_version=3,
            answer=data.snapshot.answer,
            rag=RagPolicy(),
            reasoning=ReasoningPolicy(),
        ),
    )

    async def answer(data):
        if data.purpose == "grade":
            raise RuntimeError(SECRET)
        yield completed(ToolCall("grade_call", "knowledge_search", '{"query":"餐费"}'))

    repo = Repository(data)
    assert (
        asyncio.run(
            RunExecutor(
                repo, FakeProvider(answer), registry=registry, capabilities=context.capabilities
            ).execute(context.user_id, data.thread_id, data.run_id)
        )
        == "failed"
    )
    entry = next(r for r in records(caplog) if r["event"] == "execution_failure_handled")
    assert entry["stage"] == "grade" and entry["tool_call_id"] == "grade_call"
    assert entry["code"] == "PROVIDER_ERROR"
    assert SECRET not in caplog.text


def test_terminal_storage_failure_is_logged_without_claiming_saved_completion(
    caplog, model_configuration
):
    repo = Repository(request(model_configuration))
    repo.finish.side_effect = SQLAlchemyError(SECRET)
    with pytest.raises(SQLAlchemyError):
        asyncio.run(
            RunExecutor(repo, FakeProvider()).execute(
                uuid4(), repo.data.thread_id, repo.data.run_id
            )
        )
    entries = records(caplog)
    assert [r["event"] for r in entries] == ["execution_terminal_write_failed"]
    assert entries[0]["stage"] == "terminal_write" and entries[0]["stack"]
    assert SECRET not in caplog.text


def test_sanitized_stack_excludes_source_lines_locals_and_exception_text(caplog):
    try:
        credential = SECRET
        raise ValueError(credential)
    except ValueError as error:
        diagnostic = capture("test", "injected_failure", error)
    emit(logging.getLogger("minimew.test"), "test_failure", uuid4(), "TEST", diagnostic)
    value = records(caplog)[0]
    assert value["exception_type"] == "ValueError"
    assert set(value["stack"][0]) == {"file", "function", "line"}
    assert SECRET not in caplog.text and "raise ValueError" not in caplog.text
