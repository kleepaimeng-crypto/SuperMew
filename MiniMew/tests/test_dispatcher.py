import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

from pydantic import SecretStr

from minimew.execution.dispatcher import RunDispatcher, execution_lifespan
from minimew.settings import Settings


def test_shutdown_allows_active_execution_to_finish():
    async def scenario():
        dispatcher = RunDispatcher(None, None)
        run = uuid4()

        async def work():
            await asyncio.sleep(0.02)
            return "succeeded"

        task = asyncio.create_task(work())
        dispatcher.active[run] = task
        task.add_done_callback(lambda done: dispatcher._finished(run, done))
        await dispatcher.drain()
        assert task.result() == "succeeded" and not dispatcher.active

    asyncio.run(scenario())


def test_shutdown_cancels_remaining_execution_after_grace_period():
    async def scenario():
        dispatcher = RunDispatcher(None, None)
        run, closed = uuid4(), asyncio.Event()

        async def work():
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

        task = asyncio.create_task(work())
        dispatcher.active[run] = task
        task.add_done_callback(lambda done: dispatcher._finished(run, done))
        await dispatcher.drain()
        assert task.cancelled() and closed.is_set() and not dispatcher.active

    asyncio.run(scenario())


def test_missing_key_starts_recovery_without_claiming(monkeypatch):
    monkeypatch.setenv("MINIMEW_DB_PASSWORD", "offline-test")

    async def scenario():
        observed = asyncio.Event()
        repo = AsyncMock()

        async def recover(*, limit):
            assert limit == 100
            observed.set()
            return 0

        repo.recover_expired.side_effect = recover
        monkeypatch.setattr(
            "minimew.execution.dispatcher.ExecutionRepository", lambda sessions, **kwargs: repo
        )
        settings = Settings(deepseek_api_key=SecretStr(""))
        async with execution_lifespan(None, settings) as dispatcher:
            await asyncio.wait_for(observed.wait(), 1)
            assert dispatcher.executor is None
        repo.candidates.assert_not_awaited()
        repo.claim.assert_not_awaited()

    asyncio.run(scenario())
