"""执行器重试的资格、提交边界和同一绝对截止时间。"""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from execution_helpers import FakeProvider
from test_deepseek import request

from minimew.execution.provider import ProviderCompleted, ProviderDelta, ProviderFailure
from minimew.execution.service import RunExecutor


class Repository:
    def __init__(self, data):
        self.data, self.claimed = data, object()
        self.claim = AsyncMock(return_value=self.claimed)
        self.request = AsyncMock(return_value=data)
        self.remaining = AsyncMock(return_value=60)
        self.check_control = AsyncMock(return_value=None)
        self.append_delta = AsyncMock(return_value=True)

        async def finish(claim, *, content="", error_code=None):
            return "failed" if error_code else "succeeded"

        self.finish = AsyncMock(side_effect=finish)
        self.expire = AsyncMock(return_value=False)


def execute(repo, provider):
    return RunExecutor(repo, provider).execute(uuid4(), uuid4(), uuid4())


def test_retry_reuses_claim_request_and_snapshot(model_configuration):
    async def scenario():
        repo = Repository(request(model_configuration))
        calls = 0

        async def answer(data):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ProviderFailure(retryable=True)
            yield ProviderDelta("answer")
            yield ProviderCompleted("answer")

        provider = FakeProvider(answer)
        assert await execute(repo, provider) == "succeeded"
        assert len(provider.requests) == 2
        assert provider.requests[0] is provider.requests[1] is repo.data
        repo.claim.assert_awaited_once()
        repo.request.assert_awaited_once()
        repo.append_delta.assert_awaited_once_with(repo.claimed, "answer")
        repo.finish.assert_awaited_once_with(repo.claimed, content="answer", error_code=None)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "partial,retryable,expected",
    [
        (True, True, 1),
        (False, False, 1),
        (False, True, 2),
    ],
)
def test_no_retry_after_commit_or_permanent_error_and_at_most_two_attempts(
    model_configuration,
    partial,
    retryable,
    expected,
):
    async def scenario():
        repo = Repository(request(model_configuration))

        async def answer(data):
            if partial:
                yield ProviderDelta("prefix")
            raise ProviderFailure(retryable=retryable)

        provider = FakeProvider(answer)
        assert await execute(repo, provider) == "failed"
        assert len(provider.requests) == expected
        assert repo.append_delta.await_count == int(partial)
        repo.finish.assert_awaited_once_with(repo.claimed, content="", error_code="PROVIDER_ERROR")

    asyncio.run(scenario())


def test_storage_failure_is_not_model_retry(model_configuration):
    async def scenario():
        repo = Repository(request(model_configuration))
        repo.append_delta.side_effect = RuntimeError("database failed")

        async def answer(data):
            yield ProviderDelta("body")
            yield ProviderCompleted("body")

        provider = FakeProvider(answer)
        with pytest.raises(RuntimeError, match="database failed"):
            await execute(repo, provider)
        assert len(provider.requests) == 1
        repo.finish.assert_not_awaited()

    asyncio.run(scenario())


def test_retry_requires_original_lease(model_configuration):
    async def scenario():
        repo = Repository(request(model_configuration))
        repo.remaining.side_effect = [60, 0]

        async def answer(data):
            raise ProviderFailure(retryable=True)
            yield

        provider = FakeProvider(answer)
        assert await execute(repo, provider) == "ownership_lost"
        assert len(provider.requests) == 1
        repo.finish.assert_not_awaited()
        repo.expire.assert_awaited_once_with(repo.claimed)

    asyncio.run(scenario())


def test_retry_wait_and_second_attempt_share_original_deadline(model_configuration):
    async def scenario():
        data = request(model_configuration)
        configuration = data.snapshot.answer.configuration.model_copy(
            update={"timeout_seconds": 0.8}
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
        calls = 0

        async def answer(data):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ProviderFailure(retryable=True)
            await asyncio.sleep(0.6)
            yield ProviderCompleted("late")

        provider = FakeProvider(answer)
        assert await execute(repo, provider) == "failed"
        assert len(provider.requests) == 2
        assert repo.finish.await_args.kwargs["error_code"] == "PROVIDER_TIMEOUT"

    asyncio.run(scenario())


def test_long_retry_after_cannot_start_over_budget(model_configuration):
    async def scenario():
        repo = Repository(request(model_configuration))

        async def answer(data):
            raise ProviderFailure(retryable=True, retry_after=120)
            yield

        provider = FakeProvider(answer)
        assert await execute(repo, provider) == "failed"
        assert len(provider.requests) == 1
        assert repo.finish.await_args.kwargs["error_code"] == "PROVIDER_TIMEOUT"

    asyncio.run(scenario())
