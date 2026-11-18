"""自动工具模式的输入/来源闭环；受控Provider不证明真实模型的路由准确率。"""

import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import pytest
from execution_helpers import FakeProvider
from test_deepseek import request
from test_evidence import evidence, mixed
from test_execution_retry import Repository
from test_knowledge import setup_service
from test_tools import Result, call, spec

from minimew.evidence import ANSWER_INSTRUCTIONS, SUPPLEMENT_HEADING
from minimew.execution.context import HISTORY_NOTICE, build_messages
from minimew.execution.grounded import direct_answer
from minimew.execution.messages import InputMessage, ModelTurn
from minimew.execution.prompts import CHAT_INSTRUCTIONS, chat_instructions
from minimew.execution.provider import ProviderCompleted, ProviderDelta
from minimew.execution.service import RunBudget, RunExecutor
from minimew.execution.tools import ToolContext, ToolRegistry
from minimew.knowledge import NAME
from minimew.rag.reasoning import ReasoningPolicy, RepairPolicy
from minimew.vector_store.manifest import VectorError


def test_history_projection_preserves_body_current_question_and_original_history():
    history = [("上个问题", mixed("本次检索到餐费150元。"))]
    original = history.copy()
    messages = build_messages("请解释[E1]，然后重新查证。", history)
    projected = messages[1].content
    assert projected.startswith(HISTORY_NOTICE)
    assert "本次检索到餐费150元。" in projected
    assert "[E1]" not in projected and "## 文档依据" not in projected
    assert "历史回答中的文档说明" in projected
    assert messages[-1].content == "请解释[E1]，然后重新查证。"
    assert history == original


def test_history_notice_counts_toward_budget_and_never_splits_pairs():
    size = len("now") + len("prior") + len(HISTORY_NOTICE + "answer")
    pairs = [("prior", "answer"), ("older", "other")]
    assert len(build_messages("now", pairs, max_chars=size)) == 3
    assert build_messages("now", pairs, max_chars=size - 1) == (InputMessage("user", "now"),)


def test_ordinary_english_markdown_link_is_not_a_document_citation():
    text = "示例链接：[Example](https://example.invalid)。"
    assert direct_answer(text).content == text


@pytest.mark.parametrize("visible", [False, True])
def test_first_request_has_policy_but_direct_answer_never_executes_a_tool(
    model_configuration, visible
):
    async def scenario():
        data = request(model_configuration)
        repo = Repository(data)

        async def forbidden(*args):
            raise AssertionError("Auto mode must not force tool execution")

        registry = ToolRegistry((spec(forbidden),)) if visible else ToolRegistry()

        async def answer(req):
            assert CHAT_INSTRUCTIONS in req.instructions
            assert '"knowledge_status": "not_searched"' in req.instructions
            assert '"citation_ids": []' in req.instructions
            assert not req.force_answer
            assert req.snapshot is data.snapshot
            yield ProviderCompleted(ModelTurn("text", "这是一般性的概念解释，未查询知识库。"))

        provider = FakeProvider(answer)
        outcome = await RunExecutor(
            repo, provider, registry=registry, capabilities=frozenset({"documents.read"})
        ).execute(uuid4(), data.thread_id, data.run_id)
        assert outcome == "succeeded" and len(provider.requests) == 1
        repo.record_tool.assert_not_awaited()
        assert "materials" not in repo.finish.await_args.kwargs

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "content",
    [
        "本次检索发现餐费150元。[E1]",
        "沿用上次的引用。[E999]",
        "无效引用[E0]",
        "未闭合引用[E1",
        "## 文档依据\n没有任何相关文档。",
        "### 文档依据\n旧回答中的内容。",
    ],
)
def test_no_tool_answer_cannot_succeed_with_document_markers_and_logs_reason(
    model_configuration, content, caplog
):
    async def scenario():
        data = request(model_configuration)
        repo = Repository(data)

        async def answer(req):
            yield ProviderCompleted(ModelTurn("text", content))

        provider = FakeProvider(answer)
        assert (
            await RunExecutor(repo, provider).execute(uuid4(), data.thread_id, data.run_id)
            == "failed"
        )
        repo.record_tool.assert_not_awaited()
        assert len(provider.requests) == 1  # 来源问题不冒充格式错误，不能增加重试。
        assert repo.finish.await_args.kwargs == {
            "content": "",
            "error_code": "PROVIDER_INVALID_RESPONSE",
        }

    asyncio.run(scenario())
    assert "citation_without_retrieval" in caplog.text
    assert content not in caplog.text


@pytest.mark.parametrize("mode", ["hits", "catalog", "empty", "failure"])
def test_actual_knowledge_result_controls_next_request_and_failure_is_not_empty(
    monkeypatch, model_configuration, mode
):
    _, registry, ctx, _, scoped, search, _ = setup_service(monkeypatch)
    if mode == "catalog":
        scoped.return_value = replace(scoped.return_value, targets=())
    elif mode == "empty":
        search.return_value = []
    elif mode == "failure":
        search.side_effect = VectorError("VECTOR_UNAVAILABLE")

    async def scenario():
        data = replace(
            request(model_configuration),
            messages=build_messages("请重新查证文档里的餐费。", [("旧问题", mixed())]),
        )
        repo = Repository(data)

        async def answer(req):
            assert CHAT_INSTRUCTIONS in req.instructions
            if req.messages[-1].role != "tool":
                assert "[E1]" not in req.messages[-2].content
                assert '"knowledge_status": "not_searched"' in req.instructions
                yield ProviderCompleted(ModelTurn("tool_calls", "", (call(name=NAME),)))
            else:
                assert ANSWER_INSTRUCTIONS in req.instructions
                value = json.loads(req.messages[-1].content)
                status = "searched_with_evidence" if mode == "hits" else "searched_without_evidence"
                assert f'"knowledge_status": "{status}"' in req.instructions
                assert (
                    value["status"]
                    == {"hits": "ok", "catalog": "no_documents", "empty": "no_matches"}[mode]
                )
                content = mixed() if mode == "hits" else SUPPLEMENT_HEADING + "\n本次没有足够资料。"
                yield ProviderCompleted(ModelTurn("text", content))

        provider = FakeProvider(answer)
        outcome = await RunExecutor(
            repo, provider, registry=registry, capabilities=ctx.capabilities
        ).execute(ctx.user_id, data.thread_id, data.run_id)
        assert len(provider.requests) == (1 if mode == "failure" else 2)
        assert [a.args[1] for a in repo.record_tool.await_args_list] == [
            "tool.started",
            "tool.failed" if mode == "failure" else "tool.completed",
        ]
        assert outcome == ("failed" if mode == "failure" else "succeeded")
        saved = repo.finish.await_args.kwargs
        if mode == "failure":
            assert saved["error_code"] == "KNOWLEDGE_RETRIEVAL_FAILED"
        else:
            assert bool(saved["materials"].citations) == (mode == "hits")

    asyncio.run(scenario())


def test_resume_rebuilds_state_from_same_run_ledger_instead_of_old_prompt(model_configuration):
    async def scenario():
        data = request(model_configuration)
        repo = Repository(data)

        async def handler(context, args):
            return Result(text="已恢复工具结果")

        registry = ToolRegistry((spec(handler),))
        ctx = registry.bind_context(
            ToolContext(uuid4(), data.thread_id, data.run_id, frozenset({"documents.read"}))
        )
        ctx.evidence.queried = True
        ctx.evidence.commit((evidence(),))
        checkpoint = {
            "budget": vars(RunBudget()),
            "outer": {
                "messages": data.messages,
                "instructions": "obsolete system instructions",
                "assistant": InputMessage("assistant", "", (call(),)),
                "results": (),
                "body_committed": False,
            },
        }
        ctx = replace(ctx, checkpoint=checkpoint)

        async def answer(req):
            assert "obsolete system instructions" not in req.instructions
            assert '"knowledge_status": "searched_with_evidence"' in req.instructions
            assert '"citation_ids": ["E1"]' in req.instructions
            yield ProviderCompleted(ModelTurn("text", mixed()))

        executor = RunExecutor(repo, FakeProvider(answer), registry=registry)
        result = await executor._generate(
            repo.claimed,
            replace(data, tools=registry.visible(ctx)),
            asyncio.get_running_loop().time() + 5,
            ctx,
        )
        assert result.materials.evidence == ctx.evidence.items
        assert repo.record_tool.await_count == 1  # 恢复不会重复写tool.started。

    asyncio.run(scenario())


def test_concurrent_runs_never_share_source_status_or_ids(model_configuration):
    async def scenario():
        entered = asyncio.Event()
        first = request(model_configuration)
        second = replace(first, run_id=uuid4())
        queried = False

        async def handler(context, args):
            nonlocal queried
            context.evidence.queried = True
            context.evidence.commit((evidence(),))
            queried = True
            entered.set()
            return Result(text="本Run资料[E1]")

        async def answer(req):
            if req.run_id == first.run_id:
                if req.messages[-1].role != "tool":
                    yield ProviderCompleted(ModelTurn("tool_calls", "", (call(),)))
                else:
                    assert '"citation_ids": ["E1"]' in req.instructions
                    yield ProviderCompleted(ModelTurn("text", mixed()))
            else:
                await entered.wait()
                assert queried
                assert '"knowledge_status": "not_searched"' in req.instructions
                assert '"citation_ids": []' in req.instructions
                yield ProviderCompleted(ModelTurn("text", "普通回答，未查询知识库。"))

        provider = FakeProvider(answer)
        registry = ToolRegistry((spec(handler),))
        repos = [Repository(first), Repository(second)]
        outcomes = await asyncio.gather(
            *(
                RunExecutor(
                    repo, provider, registry=registry, capabilities=frozenset({"documents.read"})
                ).execute(uuid4(), repo.data.thread_id, repo.data.run_id)
                for repo in repos
            )
        )
        assert outcomes == ["succeeded", "succeeded"]
        assert "materials" not in repos[1].finish.await_args.kwargs

    asyncio.run(scenario())


def test_full_policy_and_force_answer_fit_original_instruction_limit(model_configuration):
    from minimew.execution.messages import validate_messages

    ctx = ToolContext(
        uuid4(), uuid4(), uuid4(), frozenset(), reasoning=ReasoningPolicy(), repairs=RepairPolicy()
    )
    ctx.evidence.queried = True
    ctx.evidence.commit(tuple(evidence(citation_id=f"E{i}") for i in range(1, 19)))
    instructions = chat_instructions(ctx, (), force_answer=True)
    validate_messages(request(model_configuration).messages, (), instructions)
    assert "预算已结束" in instructions and "E18" in instructions


def test_streamed_unverified_citation_fails_without_retry_or_rewriting_committed_text(
    model_configuration,
):
    async def scenario():
        data = request(model_configuration)
        repo = Repository(data)

        async def answer(req):
            yield ProviderDelta("未验证的引用[E1]")
            yield ProviderCompleted(ModelTurn("text", "未验证的引用[E1]"))

        provider = FakeProvider(answer)
        outcome = await RunExecutor(repo, provider).execute(uuid4(), data.thread_id, data.run_id)
        assert outcome == "failed" and len(provider.requests) == 1
        repo.append_delta.assert_awaited_once_with(repo.claimed, "未验证的引用[E1]")
        assert repo.finish.await_args.kwargs["error_code"] == "PROVIDER_INVALID_RESPONSE"

    asyncio.run(scenario())
