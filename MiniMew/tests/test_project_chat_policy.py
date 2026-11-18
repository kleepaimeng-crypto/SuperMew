"""项目模式进入首次模型请求；受控测试不冒充真实模型选择率验收。"""

import asyncio
from dataclasses import FrozenInstanceError, replace
from uuid import uuid4

import pytest
from execution_helpers import FakeProvider
from test_deepseek import request
from test_evidence import evidence, mixed
from test_execution_retry import Repository
from test_model_messages import search_tool
from test_tools import Result, call, spec

from minimew.execution.identity import CaseIdentity
from minimew.execution.messages import InputMessage, ModelTurn, ToolDefinition, validate_messages
from minimew.execution.project_context import ProjectContext, catalog_text
from minimew.execution.prompts import PERSONAL_INSTRUCTIONS, PROJECT_INSTRUCTIONS, chat_instructions
from minimew.execution.provider import ProviderCompleted
from minimew.execution.service import RunBudget, RunExecutor
from minimew.execution.tools import ToolContext, ToolRegistry
from minimew.rag.reasoning import ReasoningPolicy, RepairPolicy


def project(name="Java学习"):
    return ProjectContext(uuid4(), name, ("Java异常处理.md",))


def test_project_metadata_is_immutable_bounded_and_never_available_to_evaluation(
    model_configuration,
):
    p = project()
    with pytest.raises(FrozenInstanceError):
        p.name = "other"
    with pytest.raises(ValueError):
        replace(p, document_titles=tuple("x" for _ in range(9)))
    with pytest.raises(ValueError):
        replace(p, document_titles=["x"])
    data = request(model_configuration)
    with pytest.raises(ValueError):
        replace(data, project_context={"name": "forged"})
    with pytest.raises(ValueError):
        replace(
            data,
            run_id=None,
            thread_id=None,
            project_context=p,
            case_identity=CaseIdentity(uuid4(), uuid4(), uuid4(), uuid4()),
        )
    assert catalog_text("a\n\x01\t b", 80) == "a b"


@pytest.mark.parametrize("is_project", [False, True])
def test_identical_question_gets_distinct_first_turn_policy(model_configuration, is_project):
    async def scenario():
        data = replace(
            request(model_configuration),
            messages=(InputMessage("user", "Java异常处理有哪些？"),),
            project_context=project() if is_project else None,
        )
        repo = Repository(data)

        async def answer(req):
            if is_project:
                assert PROJECT_INSTRUCTIONS in req.instructions
                assert PERSONAL_INSTRUCTIONS not in req.instructions
                assert "Java异常处理.md" in req.instructions
                assert '"conversation_scope": "project"' in req.instructions
            else:
                assert PERSONAL_INSTRUCTIONS in req.instructions
                assert PROJECT_INSTRUCTIONS not in req.instructions
                assert "Java异常处理.md" not in req.instructions
            assert '"knowledge_status": "not_searched"' in req.instructions
            assert not req.force_answer
            assert req.messages[-1].content == "Java异常处理有哪些？"
            yield ProviderCompleted(ModelTurn("text", "这是一般说明，本次未检索。"))

        assert (
            await RunExecutor(repo, FakeProvider(answer)).execute(
                uuid4(), data.thread_id, data.run_id
            )
            == "succeeded"
        )

    asyncio.run(scenario())


def test_project_policy_survives_tool_feedback_without_treating_catalog_as_evidence(
    model_configuration,
):
    async def scenario():
        data = replace(request(model_configuration), project_context=project())
        repo = Repository(data)

        async def handler(ctx, args):
            ctx.evidence.queried = True
            ctx.evidence.commit((evidence(),))
            return Result(text="实际片段[E1]")

        async def answer(req):
            assert req.project_context is data.project_context
            assert PROJECT_INSTRUCTIONS in req.instructions
            if req.messages[-1].role != "tool":
                assert '"citation_ids": []' in req.instructions
                yield ProviderCompleted(ModelTurn("tool_calls", "", (call(),)))
            else:
                assert '"citation_ids": ["E1"]' in req.instructions
                yield ProviderCompleted(ModelTurn("text", mixed()))

        provider = FakeProvider(answer)
        assert (
            await RunExecutor(
                repo,
                provider,
                registry=ToolRegistry((spec(handler),)),
                capabilities=frozenset({"documents.read"}),
            ).execute(uuid4(), data.thread_id, data.run_id)
            == "succeeded"
        )
        assert len(provider.requests) == 2
        assert len(repo.finish.await_args.kwargs["materials"].evidence) == 1

    asyncio.run(scenario())


def test_project_policy_restored_with_same_run_after_hitl(model_configuration):
    async def scenario():
        data = replace(request(model_configuration), project_context=project())
        repo = Repository(data)

        async def handler(ctx, args):
            return Result(text="原工具恢复结果")

        registry = ToolRegistry((spec(handler),))
        ctx = registry.bind_context(
            ToolContext(uuid4(), data.thread_id, data.run_id, frozenset({"documents.read"}))
        )
        ctx.evidence.queried = True
        ctx.evidence.commit((evidence(),))
        ctx = replace(
            ctx,
            checkpoint={
                "budget": vars(RunBudget()),
                "outer": {
                    "messages": data.messages,
                    "instructions": "legacy",
                    "assistant": InputMessage("assistant", "", (call(),)),
                    "results": (),
                    "body_committed": False,
                },
            },
        )

        async def answer(req):
            assert req.run_id == data.run_id and req.project_context is data.project_context
            assert PROJECT_INSTRUCTIONS in req.instructions
            assert '"knowledge_status": "searched_with_evidence"' in req.instructions
            yield ProviderCompleted(ModelTurn("text", mixed()))

        answer = await RunExecutor(repo, FakeProvider(answer), registry=registry)._generate(
            repo.claimed,
            replace(data, tools=registry.visible(ctx)),
            asyncio.get_running_loop().time() + 5,
            ctx,
        )
        assert answer.materials.evidence == ctx.evidence.items
        assert repo.record_tool.await_count == 1

    asyncio.run(scenario())


def test_project_empty_catalog_is_not_a_claim_of_executed_retrieval(model_configuration):
    data = request(model_configuration)
    ctx = ToolContext(uuid4(), data.thread_id, data.run_id, frozenset())
    instructions = chat_instructions(ctx, (), project=replace(project(), document_titles=()))
    assert '"published_document_titles": []' in instructions
    assert '"knowledge_status": "not_searched"' in instructions
    assert '"citation_ids": []' in instructions
    assert "工具不可用" in instructions


def test_maximum_project_metadata_tool_names_and_repair_rules_fit_instruction_budget(
    model_configuration,
):
    data = request(model_configuration)
    ctx = ToolContext(
        uuid4(),
        data.thread_id,
        data.run_id,
        frozenset(),
        reasoning=ReasoningPolicy(),
        repairs=RepairPolicy(),
    )
    ctx.evidence.queried = True
    ctx.evidence.commit(tuple(evidence(citation_id=f"E{i}") for i in range(1, 19)))
    tools = tuple(
        ToolDefinition(f"t{i}" + "x" * 61, "d", search_tool().parameters_json) for i in range(16)
    )
    p = ProjectContext(uuid4(), '"' * 100, tuple('"' * 80 for _ in range(8)), True)
    instructions = chat_instructions(ctx, tools, project=p, force_answer=True)
    # 格式修复还会追加约250字符，不扩大既有4096/60000限制。
    validate_messages(data.messages, tools, instructions + "修" * 300)


def test_two_project_requests_keep_separate_metadata(model_configuration):
    first = replace(request(model_configuration), project_context=project("Java学习"))
    second = replace(first, run_id=uuid4(), project_context=project("财务制度"))
    ctx = ToolContext(uuid4(), first.thread_id, first.run_id, frozenset())
    one = RunExecutor._with_instructions(first, ctx)
    two = RunExecutor._with_instructions(second, ctx)
    assert "Java学习" in one.instructions and "财务制度" not in one.instructions
    assert "财务制度" in two.instructions and "Java学习" not in two.instructions
