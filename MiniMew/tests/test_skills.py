"""Skill包、冻结内容、权限与真实执行器接线；不访问外部网络。"""

import asyncio
import json
from dataclasses import replace

import pytest
from execution_helpers import FakeProvider
from pydantic import ValidationError
from test_deepseek import request
from test_execution_retry import Repository, execute
from test_tools import call, context, spec

from minimew.execution.messages import ModelTurn, ToolCall
from minimew.execution.provider import ProviderCompleted
from minimew.execution.tools import Guardrail, ToolFailure, ToolRegistry
from minimew.skills.catalog import SkillUnavailable, catalog, load_skill
from minimew.skills.runtime import RESOURCE_CAPABILITY, resource_spec
from minimew.skills.schemas import SkillSelection, canonical, parse_snapshot


def document_skill():
    return load_skill(SkillSelection(id="document-qa"))


def test_catalog_is_versioned_bounded_and_snapshot_is_deeply_immutable():
    assert [s.id for s in catalog()] == ["default", "document-qa"]
    skill = document_skill()
    assert parse_snapshot(skill.model_dump(mode="json")) == skill
    assert skill.resources[0].id == "answer-guide"
    with pytest.raises(ValidationError):
        skill.resources[0].content = "changed"
    payload = skill.model_dump(mode="json")
    payload["resources"][0]["content"] = "changed"
    with pytest.raises(ValidationError, match="digest mismatch"):
        parse_snapshot(payload)
    assert load_skill().instructions == ""  # 默认沿用固定现有规则。


@pytest.mark.parametrize(
    "selection", [SkillSelection(id="missing"), SkillSelection(id="default", version="9.0.0")]
)
def test_unknown_skill_is_not_silently_replaced_with_default(selection):
    with pytest.raises(SkillUnavailable):
        load_skill(selection)


def test_same_version_tampering_is_detected_before_new_run(monkeypatch):
    import minimew.skills.catalog as catalog_module

    release = catalog_module.RELEASES[0]
    monkeypatch.setattr(catalog_module, "RELEASES", ((*release[:3], "0" * 64),))
    with pytest.raises(SkillUnavailable):
        load_skill()


def test_tool_name_visibility_and_execution_both_enforce_skill_intersection():
    registry = ToolRegistry((spec(lambda *args: None), resource_spec()))
    ctx = replace(
        context(),
        skill=document_skill(),
        capabilities=frozenset({"documents.read", RESOURCE_CAPABILITY}),
    )
    assert [t.name for t in registry.visible(ctx)] == ["read_skill_resource"]
    assert (
        Guardrail.decide(ctx, registry.specs[0], registry.specs[0].parameters(query="q")).outcome
        == "DENY"
    )
    with pytest.raises(ToolFailure, match="TOOL_PERMISSION_DENIED"):
        registry.prepare((call(),), ctx)
    restricted = replace(ctx, capabilities=frozenset())
    assert registry.visible(restricted) == ()
    assert registry.visible(context())[0].name == "search"  # 评测/离线宿主未选Skill的既有目录。


def test_resource_reads_only_selected_snapshot_and_parallel_runs_are_isolated():
    async def scenario():
        registry = ToolRegistry((resource_spec(),))
        ctx = replace(
            context(), skill=document_skill(), capabilities=frozenset({RESOURCE_CAPABILITY})
        )
        requested = ToolCall("r1", "read_skill_resource", '{"resource_id":"answer-guide"}')
        prepared = registry.prepare((requested,), ctx)[0]
        content = json.loads(await registry.invoke(prepared, ctx))
        assert content["content"] == ctx.skill.resources[0].content
        assert content["skill_version"] == ctx.skill.version
        assert ctx.evidence.items == ()
        other = replace(context(), skill=load_skill(), capabilities=ctx.capabilities)
        with pytest.raises(ToolFailure, match="TOOL_PERMISSION_DENIED"):
            await registry.invoke(prepared, other)
        with pytest.raises(ToolFailure, match="TOOL_PERMISSION_DENIED"):
            registry.prepare((replace(requested, arguments_json='{"resource_id":"private"}'),), ctx)
        with pytest.raises(ToolFailure, match="TOOL_INVALID_ARGUMENTS"):
            registry.prepare(
                (replace(requested, arguments_json='{"resource_id":"../../secret"}'),), ctx
            )

    asyncio.run(scenario())


def test_executor_uses_selected_instruction_resource_and_same_loop_budget(model_configuration):
    async def scenario():
        repo = Repository(replace(request(model_configuration), skill=document_skill()))
        calls = []

        async def model(data):
            calls.append(data)
            assert data.skill is repo.data.skill
            assert "本次任务采用文档问答模式" in data.instructions
            assert [t.name for t in data.tools] == ["read_skill_resource"]
            if len(calls) == 1:
                yield ProviderCompleted(
                    ModelTurn(
                        "tool_calls",
                        "",
                        (ToolCall("r1", "read_skill_resource", '{"resource_id":"answer-guide"}'),),
                    )
                )
            else:
                assert "文档问答参考规范" in data.messages[-1].content
                yield ProviderCompleted(ModelTurn("text", "任务已完成"))

        assert await execute(repo, FakeProvider(model)) == "succeeded"
        assert len(calls) == 2
        assert repo.record_tool.await_count == 2
        repo.finish.assert_awaited_once_with(repo.claimed, content="任务已完成", error_code=None)

    asyncio.run(scenario())


def test_bundle_constraints_reject_arbitrary_paths_excess_content_and_private_fields():
    from minimew.skills.schemas import SkillBundle

    base = load_skill().model_dump(mode="json", exclude={"digest"})
    for patch in (
        {"tools": ["a"] * 9},
        {"instructions": "x" * 2001},
        {"api_key": "secret"},
        {"resources": [{"id": "../../etc", "name": "x", "content": "x"}]},
    ):
        with pytest.raises(ValidationError):
            SkillBundle.model_validate_json(canonical({**base, **patch}))
