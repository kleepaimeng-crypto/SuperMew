"""PG评测事实与fencing；不调用模型或读写向量库。"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select, update

from minimew.evaluation.environment import execution_environment
from minimew.evaluation.protocol import fingerprint
from minimew.models import EvaluationCase, EvaluationCorpus, EvaluationJob, User


class EvaluationConflict(Exception):
    pass


class EvaluationNotFound(Exception):
    pass


class EvaluationControl(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class JobLease:
    job_id: UUID
    owner: UUID


class EvaluationStore:
    def __init__(self, sessions):
        self.sessions = sessions

    async def create(self, user_id, key, request):
        async with self.sessions() as session, session.begin():
            await session.scalar(select(User).where(User.id == user_id).with_for_update())
            previous = await session.scalar(
                select(EvaluationJob).where(
                    EvaluationJob.user_id == user_id, EvaluationJob.idempotency_key == key
                )
            )
            if previous is not None:
                if previous.request_hash != fingerprint(request):
                    raise EvaluationConflict
                return previous.id
            corpus = await self._validate_corpus(session, user_id, request, lock=True)
            now = await session.scalar(select(func.clock_timestamp()))
            job = EvaluationJob(
                id=uuid4(),
                user_id=user_id,
                corpus_id=corpus.id,
                idempotency_key=key,
                request_hash=fingerprint(request),
                snapshot={
                    "request": request.model_dump(mode="json"),
                    "environment": execution_environment(),
                },
                status="pending",
                cancel_requested=False,
                deadline=now + timedelta(seconds=request.job_seconds),
            )
            session.add(job)
            await session.flush()
            session.add_all(
                [
                    EvaluationCase(id=uuid4(), job_id=job.id, case_id=c.case_id, status="pending")
                    for c in request.dataset.cases
                ]
            )
            return job.id

    async def read(self, user_id, job_id):
        async with self.sessions() as session:
            job = await session.get(EvaluationJob, job_id)
            if job is None or job.user_id != user_id:
                raise EvaluationNotFound
            cases = (
                await session.scalars(
                    select(EvaluationCase)
                    .where(EvaluationCase.job_id == job_id)
                    .order_by(EvaluationCase.case_id)
                )
            ).all()
            return job, cases

    async def cancel(self, user_id, job_id):
        async with self.sessions() as session, session.begin():
            job = await session.scalar(
                select(EvaluationJob)
                .where(EvaluationJob.id == job_id, EvaluationJob.user_id == user_id)
                .with_for_update()
            )
            if job is None:
                raise EvaluationNotFound
            if job.status in {"pending", "running"}:
                job.cancel_requested = True

    async def claim(self):
        async with self.sessions() as session, session.begin():
            now = await session.scalar(select(func.clock_timestamp()))
            job = await session.scalar(
                select(EvaluationJob)
                .where(
                    EvaluationJob.status.in_(("pending", "running")),
                    (EvaluationJob.owner_token.is_(None)) | (EvaluationJob.lease_until <= now),
                )
                .order_by(EvaluationJob.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            if job.cancel_requested or job.deadline <= now:
                job.status = "cancelled" if job.cancel_requested else "failed"
                job.error_code = (
                    "EVALUATION_CANCELLED" if job.cancel_requested else "EVALUATION_DEADLINE"
                )
                job.owner_token = job.lease_until = None
                await self._stop_cases(session, job)
                return None
            # 未知外部结果不重放；已保存阶段与未开始Case保持可续接。
            stale = (
                await session.scalars(
                    select(EvaluationCase).where(
                        EvaluationCase.job_id == job.id,
                        EvaluationCase.status.in_(("generating", "judging")),
                    )
                )
            ).all()
            for case in stale:
                case.status, case.error_code = "failed", "EVALUATION_RESULT_UNKNOWN"
            job.status, job.owner_token = "running", uuid4()
            job.lease_until = min(now + timedelta(seconds=20), job.deadline)
            return JobLease(job.id, job.owner_token)

    async def _require(self, session, lease):
        job = await session.scalar(
            select(EvaluationJob).where(EvaluationJob.id == lease.job_id).with_for_update()
        )
        now = await session.scalar(select(func.clock_timestamp()))
        if (
            job is None
            or job.status != "running"
            or job.owner_token != lease.owner
            or job.lease_until <= now
        ):
            raise EvaluationControl("EVALUATION_OWNERSHIP_LOST")
        if job.cancel_requested:
            raise EvaluationControl("EVALUATION_CANCELLED")
        if job.deadline <= now:
            raise EvaluationControl("EVALUATION_DEADLINE")
        return job, now

    async def heartbeat(self, lease):
        async with self.sessions() as session, session.begin():
            job, now = await self._require(session, lease)
            job.lease_until = min(now + timedelta(seconds=20), job.deadline)

    async def write_case(self, lease, case_id, expected, status, **values):
        async with self.sessions() as session, session.begin():
            await self._require(session, lease)
            case = await session.scalar(
                select(EvaluationCase)
                .where(EvaluationCase.id == case_id, EvaluationCase.job_id == lease.job_id)
                .with_for_update()
            )
            if case is None or case.status not in expected:
                raise EvaluationControl("EVALUATION_STAGE_CONFLICT")
            case.status = status
            for key, value in values.items():
                if key not in {"checkpoint", "generation", "judgment", "error_code", "progress"}:
                    raise ValueError("Unsupported case field")
                setattr(case, key, value)
            await session.flush()
            job = await session.get(EvaluationJob, lease.job_id)
            all_cases = (
                await session.scalars(select(EvaluationCase).where(EvaluationCase.job_id == job.id))
            ).all()
            attempts = sum(
                (c.progress or {}).get("model_attempts", 0) + (c.judgment or {}).get("attempts", 0)
                for c in all_cases
            )
            if attempts > job.snapshot["request"]["total_model_limit"]:
                raise EvaluationControl("EVALUATION_MODEL_BUDGET")

    async def accept(self, lease, case_id, expected_prompt, supplement):
        async with self.sessions() as session, session.begin():
            await self._require(session, lease)
            case = await session.scalar(
                select(EvaluationCase)
                .where(EvaluationCase.id == case_id, EvaluationCase.job_id == lease.job_id)
                .with_for_update()
            )
            if case is None or case.checkpoint is None:
                raise EvaluationControl("EVALUATION_STAGE_CONFLICT")
            if case.status == "accepted":
                if case.checkpoint.get("answer") != supplement:
                    raise EvaluationConflict
                return True
            if case.status != "waiting":
                raise EvaluationControl("EVALUATION_STAGE_CONFLICT")
            if expected_prompt is None or case.checkpoint["prompt"] != expected_prompt:
                return False
            case.checkpoint = {**case.checkpoint, "answer": supplement}
            case.status = "accepted"
            return True

    async def finish(self, lease, report):
        async with self.sessions() as session, session.begin():
            job, _ = await self._require(session, lease)
            cases = (
                await session.scalars(select(EvaluationCase).where(EvaluationCase.job_id == job.id))
            ).all()
            if not cases or any(c.status not in {"completed", "failed"} for c in cases):
                raise EvaluationControl("EVALUATION_INCOMPLETE")
            job.report, job.status = report, "completed"
            job.owner_token = job.lease_until = None

    async def terminate(self, lease, code):
        async with self.sessions() as session, session.begin():
            job = await session.scalar(
                select(EvaluationJob).where(EvaluationJob.id == lease.job_id).with_for_update()
            )
            now = await session.scalar(select(func.clock_timestamp()))
            if (
                job is None
                or job.owner_token != lease.owner
                or job.status != "running"
                or job.lease_until <= now
            ):
                raise EvaluationControl("EVALUATION_OWNERSHIP_LOST")
            job.status = "cancelled" if code == "EVALUATION_CANCELLED" else "failed"
            job.error_code = code
            job.owner_token = job.lease_until = None
            await self._stop_cases(session, job)

    async def _stop_cases(self, session, job):
        await session.execute(
            update(EvaluationCase)
            .where(
                EvaluationCase.job_id == job.id,
                EvaluationCase.status.not_in(("completed", "failed", "cancelled")),
            )
            .values(
                status="cancelled" if job.status == "cancelled" else "failed",
                error_code=job.error_code,
            )
        )

    async def validate(self, user_id, request):
        async with self.sessions() as session:
            await self._validate_corpus(session, user_id, request)

    async def _validate_corpus(self, session, user_id, request, *, lock=False):
        corpus = await session.get(EvaluationCorpus, request.corpus_id, with_for_update=lock)
        if corpus is None or corpus.user_id != user_id:
            raise EvaluationNotFound
        now = await session.scalar(select(func.clock_timestamp()))
        if (
            not corpus.payload.get("ready")
            or corpus.payload["corpus_fingerprint"] != request.dataset.corpus_fingerprint()
            or datetime.fromisoformat(corpus.payload["scope"]["expires_at"].replace("Z", "+00:00"))
            <= now + timedelta(seconds=request.job_seconds)
        ):
            raise EvaluationConflict
        return corpus

    async def history(self, user_id, offset, limit):
        async with self.sessions() as session:
            return (
                await session.scalars(
                    select(EvaluationJob)
                    .where(EvaluationJob.user_id == user_id)
                    .order_by(EvaluationJob.created_at.desc(), EvaluationJob.id.desc())
                    .offset(offset)
                    .limit(limit + 1)
                )
            ).all()

    async def corpora(self, user_id, offset, limit):
        from sqlalchemy import DateTime, cast

        async with self.sessions() as session:
            return (
                await session.scalars(
                    select(EvaluationCorpus)
                    .where(
                        EvaluationCorpus.user_id == user_id,
                        EvaluationCorpus.payload["ready"].as_boolean().is_(True),
                        cast(
                            EvaluationCorpus.payload["scope"]["expires_at"].astext,
                            DateTime(timezone=True),
                        )
                        > func.clock_timestamp(),
                    )
                    .order_by(EvaluationCorpus.created_at.desc(), EvaluationCorpus.id.desc())
                    .offset(offset)
                    .limit(limit + 1)
                )
            ).all()

    async def case_detail(self, user_id, job_id, case_key):
        from minimew.evaluation.workbench import CaseDetail

        job, cases = await self.read(user_id, job_id)
        case = next((c for c in cases if c.case_id == case_key), None)
        if case is None:
            raise EvaluationNotFound
        standard = next(
            c for c in job.snapshot["request"]["dataset"]["cases"] if c["case_id"] == case_key
        )
        generation = case.generation or {}
        judgment = case.judgment or {}
        observation = generation.get("observation", {})
        checkpoint = case.checkpoint or {}
        detail = generation.get("clarification", {})
        import json

        return CaseDetail.model_validate_json(
            json.dumps(
                {
                    "case_id": case.case_id,
                    "question": standard["question"],
                    "status": case.status,
                    "answer": generation.get("answer"),
                    "error_code": case.error_code,
                    "judge_error": judgment.get("error"),
                    "judge": judgment.get("result"),
                    "counts": observation.get("counts") or case.progress,
                    "materials": observation.get("materials", []),
                    "requested": detail.get("requested", bool(checkpoint)),
                    "accepted": detail.get("accepted", "answer" in checkpoint),
                    "resumed": detail.get("resumed", False),
                }
            )
        )

    async def compare(self, user_id, job_id, inputs):
        import json

        from minimew.evaluation.runtime_result import ArtifactV2, compare_v2

        candidate, _ = await self.read(user_id, job_id)
        baseline, _ = await self.read(user_id, inputs.baseline_id)
        if (
            candidate.status != "completed"
            or baseline.status != "completed"
            or not candidate.report
            or not baseline.report
        ):
            raise EvaluationConflict
        return compare_v2(
            ArtifactV2.model_validate_json(json.dumps(candidate.report)),
            ArtifactV2.model_validate_json(json.dumps(baseline.report)),
            inputs.policy,
        )
