"""原RunExecutor的工具循环、预算、重试与执行资格。"""

import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import pytest
from execution_helpers import FakeProvider
from sqlalchemy.exc import SQLAlchemyError
from test_deepseek import request
from test_execution_retry import Repository
from test_tools import Result, call, spec

from minimew.execution.messages import ModelTurn
from minimew.execution.provider import ProviderCompleted, ProviderDelta, ProviderFailure
from minimew.execution.repository import CancellationRequested, OwnershipLost
from minimew.execution.service import RunExecutor
from minimew.execution.tools import ToolRegistry

CAPABILITIES = frozenset({"documents.read"})


def completed(*calls):
    return ProviderCompleted(ModelTurn("tool_calls", "先查询", tuple(calls)))


def executor(repo, provider, handler, *, authorize=lambda c, a: True):
    return RunExecutor(
        repo,
        provider,
        registry=ToolRegistry((spec(handler, authorize=authorize),)),
        capabilities=CAPABILITIES,
        cancellation_poll=0.01,
    )


async def run(executor, repo, user=None):
    return await executor.execute(user or uuid4(), repo.data.thread_id, repo.data.run_id)


def test_two_calls_feedback_same_run_snapshot_and_no_intermediate_public_text(model_configuration):
    async def scenario():
        repo = Repository(request(model_configuration))
        seen, contexts = [], []
        user = uuid4()

        async def handler(context, arguments):
            seen.append(arguments.query)
            contexts.append(context)
            assert context.user_id == user
            return Result(text="result:" + arguments.query)

        async def answer(data):
            assert data.run_id == repo.data.run_id and data.snapshot is repo.data.snapshot
            if not seen:
                assert data.tools[0].name == "search"
                yield completed(call("meal"), call("hotel", '{"query":"住宿"}'))
            else:
                assert [m.tool_call_id for m in data.messages[-2:]] == ["meal", "hotel"]
                assert [json.loads(m.content)["text"] for m in data.messages[-2:]] == [
                    "result:餐费",
                    "result:住宿",
                ]
                assert [c.id for c in data.messages[-3].tool_calls] == ["meal", "hotel"]
                yield ProviderCompleted(ModelTurn("text", "最终答案"))

        provider = FakeProvider(answer)
        assert await run(executor(repo, provider, handler), repo, user) == "succeeded"
        assert seen == ["餐费", "住宿"] and contexts[0] is contexts[1]
        assert len(provider.requests) == 2
        repo.claim.assert_awaited_once()
        repo.append_delta.assert_not_awaited()
        repo.finish.assert_awaited_once_with(repo.claimed, content="最终答案", error_code=None)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mode,code",
    [
        ("unknown", "TOOL_NOT_AVAILABLE"),
        ("type", "TOOL_INVALID_ARGUMENTS"),
        ("denied", "TOOL_PERMISSION_DENIED"),
    ],
)
def test_whole_batch_is_checked_before_any_handler_runs(model_configuration, mode, code):
    async def scenario():
        repo = Repository(request(model_configuration))
        invoked = []

        async def handler(c, a):
            invoked.append(a)
            return Result(text="data")

        async def answer(data):
            second = call(
                "second",
                '{"query":123}' if mode == "type" else '{"query":"拒绝"}',
                "unknown" if mode == "unknown" else "search",
            )
            yield completed(call("first"), second)

        provider = FakeProvider(answer)

        def auth(c, a):
            return not (mode == "denied" and a.query == "拒绝")

        assert await run(executor(repo, provider, handler, authorize=auth), repo) == "failed"
        assert invoked == [] and len(provider.requests) == 1
        assert repo.finish.await_args.kwargs["error_code"] == code

    asyncio.run(scenario())


def test_transient_model_retry_reuses_results_and_does_not_rerun_tools(model_configuration):
    async def scenario():
        repo = Repository(request(model_configuration))
        invoked = []
        requests = []

        async def handler(c, a):
            invoked.append(a.query)
            return Result(text="已取得结果")

        async def answer(data):
            requests.append(data)
            if len(requests) == 1:
                yield completed(call())
            elif len(requests) == 2:
                raise ProviderFailure(retryable=True)
            else:
                assert data is requests[1]
                assert json.loads(data.messages[-1].content) == {"text": "已取得结果"}
                yield ProviderCompleted(ModelTurn("text", "answer"))

        assert await run(executor(repo, FakeProvider(answer), handler), repo) == "succeeded"
        assert invoked == ["餐费"] and len(requests) == 3

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mode,expected_models,expected_tools", [("models", 4, 3), ("retry", 4, 1), ("tools", 2, 4)]
)
def test_budget_counts_actual_requests_and_rejects_entire_oversized_batch(
    model_configuration, mode, expected_models, expected_tools
):
    async def scenario():
        repo = Repository(request(model_configuration))
        invoked, requests = [], []

        async def handler(c, a):
            invoked.append(a)
            return Result(text="evidence")

        async def answer(data):
            requests.append(data)
            n = len(requests)
            if mode == "retry" and n in (1, 3):
                raise ProviderFailure(retryable=True)
            if mode == "tools":
                yield completed(*(call(f"{n}_{i}") for i in range(4 if n == 1 else 3)))
            else:
                yield completed(call(f"call_{n}"))

        assert await run(executor(repo, FakeProvider(answer), handler), repo) == "failed"
        assert len(requests) == expected_models and len(invoked) == expected_tools
        assert repo.finish.await_args.kwargs["error_code"] == "AGENT_BUDGET_EXCEEDED"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mode,code",
    [
        ("exception", "TOOL_EXECUTION_FAILED"),
        ("invalid", "TOOL_INVALID_RESULT"),
        ("delta", "PROVIDER_INVALID_RESPONSE"),
        ("reused_id", "CONTEXT_INVALID"),
    ],
)
def test_failures_never_publish_tool_text_or_repeat_completed_steps(
    model_configuration, mode, code
):
    async def scenario():
        repo = Repository(request(model_configuration))
        invoked = []

        async def handler(c, a):
            invoked.append(a)
            if mode == "exception":
                raise RuntimeError("private credential")
            return {"text": 123} if mode == "invalid" else Result(text="data")

        async def answer(data):
            if mode == "delta":
                yield ProviderDelta("工具中间文字")
            yield completed(call())

        provider = FakeProvider(answer)
        assert await run(executor(repo, provider, handler), repo) == "failed"
        assert len(invoked) == (0 if mode == "delta" else 1)
        assert len(provider.requests) == (2 if mode == "reused_id" else 1)
        repo.append_delta.assert_not_awaited()
        assert repo.finish.await_args.kwargs == {"content": "", "error_code": code}

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure,expected", [(CancellationRequested, "cancelled"), (OwnershipLost, "ownership_lost")]
)
def test_monitor_stops_waiting_tool_without_second_call(model_configuration, failure, expected):
    async def scenario():
        repo = Repository(request(model_configuration))
        entered, closed = asyncio.Event(), asyncio.Event()

        async def control(claim):
            if entered.is_set():
                raise failure()

        repo.check_control.side_effect = control
        if expected == "cancelled":
            repo.finish.side_effect = None
            repo.finish.return_value = "cancelled"

        async def handler(c, a):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

        async def answer(data):
            yield completed(call("one"), call("two"))

        provider = FakeProvider(answer)
        assert await run(executor(repo, provider, handler), repo) == expected
        assert closed.is_set() and len(provider.requests) == 1
        if failure is OwnershipLost:
            repo.finish.assert_not_awaited()

    asyncio.run(scenario())


def test_same_absolute_deadline_includes_tool_wait_and_rejects_late_result(model_configuration):
    async def scenario():
        data = request(model_configuration)
        configuration = data.snapshot.answer.configuration.model_copy(
            update={"timeout_seconds": 0.08}
        )
        data = replace(
            data,
            snapshot=data.snapshot.model_copy(
                update={
                    "answer": data.snapshot.answer.model_copy(
                        update={"configuration": configuration}
                    )
                }
            ),
        )
        repo = Repository(data)
        calls = []

        async def handler(c, a):
            calls.append(a)
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                return Result(text="迟到结果")

        async def answer(data):
            yield completed(call("one"), call("two"))

        provider = FakeProvider(answer)
        assert await run(executor(repo, provider, handler), repo) == "failed"
        assert len(calls) == 1 and len(provider.requests) == 1
        assert repo.finish.await_args.kwargs["error_code"] == "AGENT_DEADLINE_EXCEEDED"

    asyncio.run(scenario())


def test_concurrent_runs_share_no_context_messages_budget_or_results(model_configuration):
    async def scenario():
        entered = []
        release = asyncio.Event()

        async def handler(context, arguments):
            entered.append(context)
            if len(entered) == 2:
                release.set()
            await release.wait()
            return Result(text=str(context.run_id))

        async def answer(data):
            if data.messages[-1].role != "tool":
                yield completed(call())
            else:
                assert json.loads(data.messages[-1].content)["text"] == str(data.run_id)
                yield ProviderCompleted(ModelTurn("text", str(data.run_id)))

        provider = FakeProvider(answer)
        repos = [Repository(request(model_configuration)), Repository(request(model_configuration))]
        registry = ToolRegistry((spec(handler),))
        executors = [
            RunExecutor(r, provider, registry=registry, capabilities=CAPABILITIES) for r in repos
        ]
        assert await asyncio.gather(
            *(run(e, r) for e, r in zip(executors, repos, strict=True))
        ) == ["succeeded", "succeeded"]
        assert entered[0] is not entered[1] and entered[0].run_id != entered[1].run_id
        for repo in repos:
            assert repo.finish.await_args.kwargs["content"] == str(repo.data.run_id)

    asyncio.run(scenario())


def test_exactly_six_calls_can_complete_within_model_budget(model_configuration):
    async def scenario():
        repo = Repository(request(model_configuration))
        invoked, requests = [], []

        async def handler(c, a):
            invoked.append(a)
            return Result(text="data")

        async def answer(data):
            requests.append(data)
            if len(requests) < 3:
                yield completed(*(call(f"call_{len(requests)}_{i}") for i in range(3)))
            else:
                yield ProviderCompleted(ModelTurn("text", "answer"))

        assert await run(executor(repo, FakeProvider(answer), handler), repo) == "succeeded"
        assert len(invoked) == 6 and len(requests) == 3

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure,expected",
    [
        (CancellationRequested, "cancelled"),
        (OwnershipLost, "ownership_lost"),
        (SQLAlchemyError, "failed"),
    ],
)
def test_qualification_is_rechecked_after_model_before_any_handler(
    model_configuration, failure, expected
):
    async def scenario():
        repo = Repository(request(model_configuration))
        returned = False

        async def control(claim):
            if returned:
                raise failure("private control failure")

        repo.check_control.side_effect = control
        if expected == "cancelled":
            repo.finish.side_effect = None
            repo.finish.return_value = "cancelled"

        async def handler(c, a):
            raise AssertionError("No execution qualification")

        async def answer(data):
            nonlocal returned
            returned = True
            yield completed(call())

        provider = FakeProvider(answer)
        assert await run(executor(repo, provider, handler), repo) == expected
        assert len(provider.requests) == 1
        if failure is SQLAlchemyError:
            assert repo.finish.await_args.kwargs["error_code"] == "EXECUTION_STORAGE_UNAVAILABLE"
        elif failure is OwnershipLost:
            repo.finish.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["initial", "results"])
def test_context_budget_does_not_leak_or_retry_completed_tools(model_configuration, phase):
    from minimew.execution.messages import InputMessage

    async def scenario():
        data = request(model_configuration)
        if phase == "initial":
            data = replace(data, messages=(InputMessage("user", "x" * 60000),))
        repo = Repository(data)
        invoked, requests = [], []

        async def handler(c, a):
            invoked.append(a)
            return Result(text="x" * 11000)

        async def answer(data):
            requests.append(data)
            yield completed(*(call(f"c_{len(requests)}_{i}") for i in range(3)))

        assert await run(executor(repo, FakeProvider(answer), handler), repo) == "failed"
        assert repo.finish.await_args.kwargs["error_code"] == "CONTEXT_INVALID"
        assert len(invoked) == (0 if phase == "initial" else 6)
        assert len(requests) == (0 if phase == "initial" else 2)
        repo.append_delta.assert_not_awaited()

    asyncio.run(scenario())


def test_local_task_cancellation_cannot_be_swallowed_to_start_second_tool(model_configuration):
    async def scenario():
        repo = Repository(request(model_configuration))
        entered = asyncio.Event()
        invoked = []

        async def handler(c, a):
            invoked.append(a)
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return Result(text="late")

        async def answer(data):
            yield completed(call("one"), call("two"))

        provider = FakeProvider(answer)
        task = asyncio.create_task(run(executor(repo, provider, handler), repo))
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(invoked) == 1 and len(provider.requests) == 1
        assert repo.finish.await_args.kwargs["error_code"] == "EXECUTOR_INTERRUPTED"

    asyncio.run(scenario())
