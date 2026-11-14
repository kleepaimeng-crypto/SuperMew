import asyncio
from unittest.mock import AsyncMock

import pytest
from execution_helpers import FakeProvider
from pydantic import ValidationError
from test_deepseek import request
from test_execution_retry import Repository, execute

from minimew.execution.provider import ProviderFailure
from minimew.settings import Settings


@pytest.mark.parametrize("lease,heartbeat", [(1, 1), (0.2, 0.3), (20, 0)])
def test_runtime_settings_reject_invalid_heartbeat(lease, heartbeat):
    with pytest.raises(ValidationError):
        Settings(
            db_password="test-only",
            execution_lease_seconds=lease,
            execution_heartbeat_seconds=heartbeat,
        )


def test_activity_activation_failure_never_calls_provider(model_configuration):
    async def scenario():
        repo = Repository(request(model_configuration))
        repo.activate = AsyncMock(side_effect=OSError("controlled activation acknowledgement loss"))
        provider = FakeProvider()
        with pytest.raises(OSError):
            await execute(repo, provider)
        assert not provider.requests
        repo.finish.assert_not_awaited()

    asyncio.run(scenario())


def test_short_activity_budget_is_not_reset_by_retry(model_configuration):
    async def scenario():
        repo = Repository(request(model_configuration))
        repo.activate = AsyncMock(return_value=0.1)

        async def unavailable(request):
            raise ProviderFailure(retryable=True)
            yield

        provider = FakeProvider(unavailable)
        assert await execute(repo, provider) == "failed"
        assert len(provider.requests) == 1
        assert repo.finish.await_args.kwargs["error_code"] == "PROVIDER_TIMEOUT"
        repo.activate.assert_awaited_once()

    asyncio.run(scenario())
