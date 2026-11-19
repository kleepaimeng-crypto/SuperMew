"""独立受控评测Worker；真实本地检索，绝不构造付费Provider。"""

import asyncio
import json
import logging
from dataclasses import asdict, replace

from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import async_sessionmaker

from minimew.database import create_engine
from minimew.documents.retrieval import RetrievalScope
from minimew.evaluation.environment import execution_environment
from minimew.evaluation.judge import JudgeFailure, JudgeInput, controlled_judge
from minimew.evaluation.protocol import (
    Counts,
    Material,
    Observation,
    ObservationSet,
    Provenance,
    fingerprint,
    text_hash,
)
from minimew.evaluation.runtime_protocol import CorpusV2, JobRequest, JudgeResult
from minimew.evaluation.runtime_result import CaseEvaluation, ObservationV2, score_v2
from minimew.evaluation.store import EvaluationControl, EvaluationStore
from minimew.evidence import ANSWER_INSTRUCTIONS, EvidenceDirectory
from minimew.execution.budget import ExecutionBudget as RunBudget
from minimew.execution.diagnostics import capture
from minimew.execution.grounded import grounded_answer
from minimew.execution.identity import CaseIdentity
from minimew.execution.messages import InputMessage, ModelTurn
from minimew.execution.provider import ProviderCompleted, ProviderRequest
from minimew.execution.reasoning import ModelCaller
from minimew.hitl.checkpoint import decode, encode
from minimew.hitl.policy import PauseRequested
from minimew.knowledge import KnowledgeCalculation, KnowledgeQuery
from minimew.knowledge_runtime import KnowledgeRuntime
from minimew.models import EvaluationCorpus
from minimew.rag.context import RagContext
from minimew.settings import Settings
from minimew.vector_store.adapter import CandidateIndex
from minimew.vector_store.milvus import MilvusBackend

logger = logging.getLogger(__name__)


class ControlledProvider:
    def __init__(self, scenario, budget, before_call=None):
        self.scenario, self.budget = scenario, budget
        self.before_call = before_call

    async def stream(self, request):
        if self.before_call is not None:
            await self.before_call()
        if request.purpose == "answer":
            content = self.scenario.answer
        elif request.purpose == "repair":
            if self.scenario.repair_answer is None:
                raise ValueError("CONTROLLED_REPAIR_MISSING")
            content = self.scenario.repair_answer
        else:
            index = self.budget.internal_requests - 1
            if not 0 <= index < len(self.scenario.roles):
                raise ValueError("CONTROLLED_ROLE_EXHAUSTED")
            reply = self.scenario.roles[index]
            if reply.purpose != request.purpose:
                raise ValueError("CONTROLLED_ROLE_MISMATCH")
            content = json.dumps(reply.payload, ensure_ascii=False)
        yield ProviderCompleted(ModelTurn("text", content))


class EvaluationWorker:
    def __init__(
        self, sessions, *, runtime_factory=KnowledgeRuntime, backend_factory=MilvusBackend.connect
    ):
        self.sessions, self.store = sessions, EvaluationStore(sessions)
        self.runtime_factory = runtime_factory
        self.backend_factory = backend_factory

    async def once(self):
        lease = await self.store.claim()
        if lease is None:
            return False
        operation = asyncio.create_task(self.execute(lease))

        async def heartbeat():
            while True:
                await asyncio.sleep(5)
                await self.store.heartbeat(lease)

        monitor = asyncio.create_task(heartbeat())
        try:
            done, _ = await asyncio.wait((operation, monitor), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                await task
        except EvaluationControl as error:
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            if error.code != "EVALUATION_OWNERSHIP_LOST":
                await self.store.terminate(lease, error.code)
        except Exception as error:
            logger.warning(
                "evaluation_worker_failed job_id=%s diagnostic=%s",
                lease.job_id,
                json.dumps(asdict(capture("evaluation", "worker_failed", error))),
            )
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            await self.store.terminate(lease, "EVALUATION_WORKER_FAILED")
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        return True

    async def execute(self, lease):
        async with self.sessions() as session:
            from minimew.models import EvaluationJob

            job = await session.get(EvaluationJob, lease.job_id)
            artifact = await session.get(EvaluationCorpus, job.corpus_id)
            request = JobRequest.model_validate_json(json.dumps(job.snapshot["request"]))
            if job.snapshot["environment"] != execution_environment():
                raise EvaluationControl("EVALUATION_ENVIRONMENT_CHANGED")
            corpus = CorpusV2.model_validate_json(json.dumps(artifact.payload["corpus"]))
            scope = TypeAdapter(RetrievalScope).validate_json(json.dumps(artifact.payload["scope"]))
            bindings = {b["version_id"]: b["source_id"] for b in artifact.payload["bindings"]}
            namespace = artifact.collection
        runtime = self.runtime_factory()
        isolated = None
        try:
            await runtime.start()
            isolated = await runtime.sync(
                self.backend_factory, runtime.vector_settings, namespace=namespace
            )
            runtime.index = CandidateIndex(isolated)
            for scenario in request.scenarios:
                await self.store.heartbeat(lease)
                _, cases = await self.store.read(job.user_id, job.id)
                case = next(c for c in cases if c.case_id == scenario.case_id)
                if case.status in {"completed", "failed"}:
                    continue
                try:
                    if case.status in {"pending", "accepted"}:
                        await self._generate(
                            lease, job, request, case, scenario, scope, bindings, runtime
                        )
                    elif case.status == "waiting":
                        if await self.store.accept(
                            lease, case.id, scenario.expected_prompt, scenario.supplement
                        ):
                            # 下一循环/进程可以续接；本次也走同一持久接收事实。
                            _, refreshed = await self.store.read(job.user_id, job.id)
                            case = next(c for c in refreshed if c.id == case.id)
                            await self._generate(
                                lease, job, request, case, scenario, scope, bindings, runtime
                            )
                        else:
                            await self.store.write_case(
                                lease,
                                case.id,
                                {"waiting"},
                                "failed",
                                error_code="EVALUATION_CLARIFICATION_UNMATCHED",
                            )
                    _, refreshed = await self.store.read(job.user_id, job.id)
                    case = next(c for c in refreshed if c.id == case.id)
                    if case.status == "waiting":
                        # 先释放当前进程内计算栈；从DB读接收结果后再进入同一Case。
                        if await self.store.accept(
                            lease, case.id, scenario.expected_prompt, scenario.supplement
                        ):
                            _, refreshed = await self.store.read(job.user_id, job.id)
                            case = next(c for c in refreshed if c.id == case.id)
                            await self._generate(
                                lease, job, request, case, scenario, scope, bindings, runtime
                            )
                        else:
                            await self.store.write_case(
                                lease,
                                case.id,
                                {"waiting"},
                                "failed",
                                error_code="EVALUATION_CLARIFICATION_UNMATCHED",
                            )
                    _, refreshed = await self.store.read(job.user_id, job.id)
                    case = next(c for c in refreshed if c.id == case.id)
                    if case.status == "generated":
                        await self.store.write_case(lease, case.id, {"generated"}, "judging")
                        standard = next(
                            c for c in request.dataset.cases if c.case_id == case.case_id
                        )
                        material_refs = tuple(
                            Material.model_validate_json(json.dumps(m))
                            for m in case.generation["observation"]["materials"]
                        )
                        sources = {s.source_id: s.text for s in corpus.sources}
                        inputs = JudgeInput(
                            standard=standard,
                            answer=case.generation["answer"],
                            materials=material_refs,
                            source_texts=tuple(
                                sources[m.source_id][m.start : m.end] for m in material_refs
                            ),
                        )
                        for attempt in (1, 2):
                            await self.store.write_case(
                                lease,
                                case.id,
                                {"judging"},
                                "judging",
                                judgment={"result": None, "error": None, "attempts": attempt},
                            )
                            try:
                                judged = await controlled_judge(inputs, scenario, attempt)
                                judgment = {
                                    "result": judged.model_dump(mode="json"),
                                    "error": None,
                                    "attempts": attempt,
                                }
                                break
                            except JudgeFailure as error:
                                judgment = {
                                    "result": None,
                                    "error": error.code,
                                    "attempts": attempt,
                                }
                                if not error.retryable or attempt == 2:
                                    break
                        await self.store.write_case(
                            lease, case.id, {"judging"}, "completed", judgment=judgment
                        )
                except EvaluationControl:
                    raise
                except Exception as error:
                    logger.warning(
                        "evaluation_case_failed job_id=%s case_id=%s diagnostic=%s",
                        lease.job_id,
                        case.id,
                        json.dumps(asdict(capture("evaluation", "case_failed", error))),
                    )
                    _, refreshed = await self.store.read(job.user_id, job.id)
                    current = next(c for c in refreshed if c.id == case.id)
                    await self.store.write_case(
                        lease,
                        case.id,
                        {current.status},
                        "failed",
                        error_code="EVALUATION_CASE_FAILED",
                    )
            _, cases = await self.store.read(job.user_id, job.id)
            observations, evaluations = [], []
            for case in cases:
                scenario = next(s for s in request.scenarios if s.case_id == case.case_id)
                if case.generation:
                    observations.append(
                        Observation.model_validate_json(json.dumps(case.generation["observation"]))
                    )
                    detail = case.generation["clarification"]
                else:
                    observations.append(
                        Observation(
                            case_id=case.case_id,
                            outcome="waiting"
                            if case.error_code == "EVALUATION_CLARIFICATION_UNMATCHED"
                            else "error",
                            error_code=None
                            if case.error_code == "EVALUATION_CLARIFICATION_UNMATCHED"
                            else (case.error_code or "EVALUATION_CASE_FAILED"),
                            counts=Counts(**case.progress)
                            if case.progress
                            else Counts(model_attempts=0, retrieval_calls=0, tool_calls=0),
                            budget=Counts(model_attempts=12, retrieval_calls=8, tool_calls=6),
                        )
                    )
                    detail = {"requested": case.checkpoint is not None}
                evaluations.append(
                    CaseEvaluation(
                        case_id=case.case_id,
                        answer=case.generation["answer"] if case.generation else None,
                        judge=JudgeResult.model_validate_json(json.dumps(case.judgment["result"]))
                        if case.judgment and case.judgment["result"]
                        else None,
                        judge_error=case.judgment["error"]
                        if case.judgment
                        else (case.error_code or "JUDGE_UNAVAILABLE"),
                        clarification_required=scenario.expected_prompt is not None,
                        judge_attempts=case.judgment["attempts"] if case.judgment else 0,
                        **detail,
                    )
                )
            structural = ObservationSet(
                dataset_fingerprint=fingerprint(request.dataset),
                corpus_fingerprint=request.dataset.corpus_fingerprint(),
                provenance=Provenance(
                    mode="controlled",
                    implementation=job.snapshot["environment"]["implementation"],
                    dependencies=job.snapshot["environment"]["dependencies"],
                    model=fingerprint(
                        {
                            "generation": request.snapshot.answer.model_dump(mode="json"),
                            "judge": "controlled-rubric-v1",
                            "local": "frozen-build-and-reranker-base",
                        }
                    ),
                    strategy=fingerprint(
                        {
                            "rag": request.snapshot.rag.model_dump(mode="json"),
                            "reasoning": request.snapshot.reasoning.model_dump(mode="json"),
                            "repairs": request.snapshot.repairs.model_dump(mode="json"),
                            "hitl": request.snapshot.hitl.model_dump(mode="json"),
                        }
                    ),
                    index=fingerprint(
                        [
                            {
                                "source": d.source_sha256,
                                "build": d.build_fingerprint,
                                "chunks": [
                                    {"hash": c.content_sha256, "span": asdict(c.span)}
                                    for c in d.chunks
                                ],
                            }
                            for d in scope.directory
                        ]
                    ),
                ),
                cases=tuple(observations),
            )
            result = score_v2(
                request.dataset,
                corpus,
                ObservationV2(structural=structural, evaluations=tuple(evaluations)),
                request.judge_minimum,
            )
            await self.store.finish(lease, result.model_dump(mode="json"))
        finally:
            if isolated:
                await runtime.sync(isolated.close)
            await runtime.aclose()

    async def _generate(self, lease, job, request, case, scenario, scope, bindings, runtime):
        await self.store.write_case(lease, case.id, {"pending", "accepted"}, "generating")
        saved = case.checkpoint
        budget = (
            RunBudget(**saved["budget"]) if saved else RunBudget(reasoning=True, finalization=True)
        )
        owner = CaseIdentity(job.user_id, job.id, case.id, lease.owner)
        evidence = EvidenceDirectory(owner)
        if saved:
            evidence.items = decode(saved["evidence"])

        async def persist_progress():
            await self.store.write_case(
                lease,
                case.id,
                {"generating"},
                "generating",
                progress={
                    "model_attempts": budget.model_requests + budget.internal_requests,
                    "retrieval_calls": budget.retrieval_queries,
                    "tool_calls": 1,
                },
            )

        provider = ControlledProvider(scenario, budget, persist_progress)
        model_request = ProviderRequest(
            None,
            None,
            request.snapshot,
            (
                InputMessage(
                    "user",
                    next(c.question for c in request.dataset.cases if c.case_id == case.case_id),
                ),
            ),
            case_identity=owner,
        )
        deadline = asyncio.get_running_loop().time() + (
            saved["remaining_seconds"] if saved else request.case_seconds
        )

        async def check():
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Evaluation case deadline")
            await self.store.heartbeat(lease)

        caller = ModelCaller(
            provider, model_request, budget, deadline, check, request.snapshot.reasoning
        )
        context = RagContext(
            execution_identity=owner,
            evidence=evidence,
            rag_policy=request.snapshot.rag,
            deadline=deadline,
            reasoning=request.snapshot.reasoning,
            repairs=request.snapshot.repairs,
            hitl=request.snapshot.hitl,
            question=model_request.messages[0].content,
            model_caller=caller,
            run_budget=budget,
            checkpoint={"graph": saved["graph"], "answer": saved["answer"]} if saved else None,
            progress=persist_progress,
        )
        try:
            async with asyncio.timeout_at(deadline):
                result = await KnowledgeCalculation(self.sessions, runtime).calculate(
                    context, KnowledgeQuery(query=context.question), scope
                )
        except PauseRequested as pause:
            await self.store.write_case(
                lease,
                case.id,
                {"generating"},
                "waiting",
                checkpoint={
                    "graph": pause.graph,
                    "remaining_seconds": max(0.0, deadline - asyncio.get_running_loop().time()),
                    "prompt": pause.prompt,
                    "budget": asdict(budget),
                    "evidence": encode(evidence.items),
                },
            )
            return

        async def model_turn(model_request):
            await check()
            budget.require_model()
            budget.model_requests += 1
            await persist_progress()
            completed = None
            async for event in provider.stream(model_request):
                if completed is not None or not isinstance(event, ProviderCompleted):
                    raise ValueError("Invalid controlled turn")
                completed = event.turn
            if completed is None:
                raise ValueError("Missing controlled turn")
            return completed

        # 正式工具计算的结果进入生成上下文；人工标准只在稍后的JudgeInput出现。
        model_request = replace(
            model_request,
            instructions=ANSWER_INSTRUCTIONS,
            messages=(
                InputMessage(
                    "user",
                    json.dumps(
                        {
                            "question": context.question,
                            "knowledge_result": result.model_dump(mode="json"),
                        },
                        ensure_ascii=False,
                    ),
                ),
            ),
        )
        turn = await model_turn(model_request)
        answer = await grounded_answer(model_request, turn, context, budget, check, model_turn)
        materials = tuple(
            Material(
                material_id=m.citation_id.lower(),
                source_id=bindings[str(m.version_id)],
                start=m.start,
                end=m.end,
                content_sha256=text_hash(m.text),
            )
            for m in result.matches
        )
        observation = Observation(
            case_id=case.case_id,
            outcome="answered" if result.status == "ok" else "no_knowledge",
            materials=materials,
            citation_sources=tuple(c.citation_id.lower() for c in answer.materials.evidence)
            if answer.materials
            else (),
            counts=Counts(
                model_attempts=budget.model_requests + budget.internal_requests,
                retrieval_calls=budget.retrieval_queries,
                tool_calls=1,
            ),
            budget=Counts(model_attempts=12, retrieval_calls=8, tool_calls=6),
        )
        await self.store.write_case(
            lease,
            case.id,
            {"generating"},
            "generated",
            generation={
                "answer": answer.content,
                "observation": observation.model_dump(mode="json"),
                "clarification": {
                    "requested": bool(saved),
                    "matched": bool(saved),
                    "accepted": bool(saved),
                    "resumed": bool(saved),
                },
            },
        )


async def serve():
    engine = create_engine(Settings())
    worker = EvaluationWorker(async_sessionmaker(engine, expire_on_commit=False))
    try:
        while True:
            if not await worker.once():
                await asyncio.sleep(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    from minimew.execution.diagnostics import diagnostic_log

    with diagnostic_log(Settings().diagnostic_log_dir):
        asyncio.run(serve())
