"""取消控制流、受控故障与契约；无外部网络/数据库。"""

import asyncio
import json
from datetime import UTC, datetime

import pytest
from execution_helpers import FakeProvider
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from test_deepseek import request
from test_execution_retry import Repository, execute

from minimew.execution.messages import ModelTurn
from minimew.execution.provider import ProviderCompleted
from minimew.execution.repository import CancellationRequested, OwnershipLost
from minimew.generated.run_event_v1 import EVENT_ADAPTER


@pytest.mark.parametrize("kind", ["run.cancel_requested", "run.cancelled", "message.completed"])
def test_cancel_contract_round_trip_and_rejects_extra_fields(kind):
    from uuid import uuid4

    data = {
        "run.cancel_requested": {"cancel_requested_at": datetime.now(UTC).isoformat()},
        "run.cancelled": {"status": "cancelled"},
        "message.completed": {
            "status": "cancelled",
            "message_id": str(uuid4()),
            "content": "prefix",
        },
    }[kind]
    event = dict(
        schema_version=1,
        event_id=str(uuid4()),
        sequence=1,
        run_id=str(uuid4()),
        thread_id=str(uuid4()),
        type=kind,
        timestamp=datetime.now(UTC).isoformat(),
        data=data,
    )
    parsed = EVENT_ADAPTER.validate_json(json.dumps(event))
    assert EVENT_ADAPTER.validate_json(EVENT_ADAPTER.dump_json(parsed)) == parsed
    event["data"]["private"] = "secret"
    with pytest.raises(ValidationError):
        EVENT_ADAPTER.validate_json(json.dumps(event))


@pytest.mark.parametrize("failure", [CancellationRequested, OwnershipLost, SQLAlchemyError])
def test_monitor_failure_stops_model_and_keeps_failure_kind(model_configuration, failure):
    async def scenario():
        repo = Repository(request(model_configuration))
        closed = asyncio.Event()

        async def control(claim):
            if asyncio.current_task().get_name().startswith("minimew-cancel-monitor-"):
                raise failure()

        repo.check_control.side_effect = control

        async def answer(data):
            try:
                await asyncio.Event().wait()
                yield ProviderCompleted(ModelTurn("text", "never"))
            finally:
                closed.set()

        if failure is CancellationRequested:
            repo.finish.side_effect = None
            repo.finish.return_value = "cancelled"
        provider = FakeProvider(answer)
        result = await execute(repo, provider)
        assert closed.is_set() and len(provider.requests) == 1
        if failure is OwnershipLost:
            assert result == "ownership_lost"
            repo.finish.assert_not_awaited()
        elif failure is CancellationRequested:
            assert result == "cancelled"
        else:
            assert result == "failed"
            assert repo.finish.await_args.kwargs["error_code"] == "EXECUTION_STORAGE_UNAVAILABLE"
        assert not [
            t
            for t in asyncio.all_tasks()
            if not t.done()
            and t.get_name().startswith(("minimew-model-", "minimew-cancel-monitor-"))
        ]

    asyncio.run(scenario())


def test_monitor_and_save_storage_failure_does_not_invent_terminal(model_configuration):
    async def scenario():
        repo = Repository(request(model_configuration))

        async def control(claim):
            if asyncio.current_task().get_name().startswith("minimew-cancel-monitor-"):
                raise SQLAlchemyError("private read failure")

        repo.check_control.side_effect = control
        repo.finish.side_effect = SQLAlchemyError("private save failure")

        async def answer(data):
            await asyncio.Event().wait()
            yield ProviderCompleted(ModelTurn("text", "never"))

        provider = FakeProvider(answer)
        with pytest.raises(SQLAlchemyError, match="private save failure"):
            await execute(repo, provider)
        assert len(provider.requests) == 1 and repo.finish.await_count == 1

    asyncio.run(scenario())


def test_model_wait_cannot_extend_absolute_deadline_after_swallowed_cancel(model_configuration):
    from dataclasses import replace

    async def scenario():
        data = request(model_configuration)
        config = data.snapshot.answer.configuration.model_copy(update={"timeout_seconds": 0.03})
        data = replace(
            data,
            snapshot=data.snapshot.model_copy(
                update={
                    "answer": data.snapshot.answer.model_copy(update={"configuration": config}),
                }
            ),
        )
        repo = Repository(data)
        closed = asyncio.Event()

        async def answer(data):
            try:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    await asyncio.Event().wait()
                yield ProviderCompleted(ModelTurn("text", "late"))
            finally:
                closed.set()

        async with asyncio.timeout(5):
            assert await execute(repo, FakeProvider(answer)) == "failed"
        assert repo.finish.await_args.kwargs["error_code"] == "PROVIDER_TIMEOUT"
        assert closed.is_set()

    asyncio.run(scenario())
