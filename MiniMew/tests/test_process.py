"""公开报告的并行父子隔离与安全输出，不调用模型。"""

import asyncio
from uuid import uuid4

import pytest
from pydantic import ValidationError

from minimew.execution.process import ProcessReporter
from minimew.generated.run_event_v1 import ProcessStepData


def test_parallel_steps_and_recreated_reporter_use_stable_owned_identity():
    async def scenario():
        events = []

        async def sink(data):
            ProcessStepData.model_validate(data)
            events.append(data)

        rid = uuid4()

        async def branch(i):
            reporter = ProcessReporter(rid, sink).tool("call")
            async with reporter.step("subquestion", str(i), query=str(i)) as parent:
                async with parent.reporter.step("dense", "dense") as step:
                    await asyncio.sleep(0)
                    step.finish(count=i)

        await asyncio.gather(branch(1), branch(2))
        starts = [x for x in events if x["status"] == "running"]
        assert len({x["step_id"] for x in starts}) == 4
        for event in events:
            assert event["tool_call_id"] == "call"
            if event["kind"] == "dense":
                parent = next(p for p in starts if p["step_id"] == event["parent_step_id"])
                assert parent["query"] in {"1", "2"}
        await branch(1)
        assert events[-4]["step_id"] == starts[0]["step_id"]

    asyncio.run(scenario())


def test_failure_does_not_serialize_exception_or_reasoning():
    async def scenario():
        events = []

        async def sink(data):
            events.append(data)

        with pytest.raises(ValueError):
            async with ProcessReporter(uuid4(), sink).step("model", "one"):
                raise ValueError("private secret")
        assert events[-1]["status"] == "failed"
        assert events[-1]["outcome"] == "PROCESS_STAGE_FAILED"
        assert "private secret" not in str(events)
        with pytest.raises(ValidationError):
            ProcessStepData.model_validate({**events[0], "sources": [{}] * 7})

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["sufficient", "rewrite", "plan", "partial", "failure"])
def test_formal_workflow_observation_preserves_original_routes(monkeypatch, mode):
    from dataclasses import replace

    import test_reasoning_graph as cases

    original = cases.setup_service
    events = []

    async def sink(data):
        ProcessStepData.model_validate(data)
        events.append(data)

    def setup(patch):
        values = list(original(patch))
        values[2] = replace(
            values[2], process=ProcessReporter(values[2].run_id, sink).tool("call_1")
        )
        return values

    monkeypatch.setattr(cases, "setup_service", setup)
    cases.test_graph_uses_actual_draft_keeps_original_and_bounded_private_state(monkeypatch, mode)
    assert any(e["kind"] == "assessment" for e in events)
    if mode in {"plan", "partial", "failure"}:
        children = {e["step_id"] for e in events if e["kind"] == "subquestion"}
        assert len(children) == 4
        assert any(e["parent_step_id"] in children for e in events)
