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
            corpus = await session.get(EvaluationCorpus, request.corpus_id, with_for_update=True)
            if corpus is None or corpus.user_id != user_id:
                raise EvaluationNotFound
            if not corpus.payload.get("ready"):
                raise EvaluationConflict
            if corpus.payload["corpus_fingerprint"] != request.dataset.corpus_fingerprint():
                raise EvaluationConflict
            now = await session.scalar(select(func.clock_timestamp()))
            if datetime.fromisoformat(
                corpus.payload["scope"]["expires_at"].replace("Z", "+00:00")
            ) <= now + timedelta(seconds=request.job_seconds):
                raise EvaluationConflict
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
