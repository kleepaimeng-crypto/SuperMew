"""评测Job的唯一HTTP生命周期入口；不在请求内执行模型。"""

import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BeforeValidator
from sqlalchemy.exc import SQLAlchemyError

from minimew.api.auth import CurrentUser
from minimew.api.threads import no_store
from minimew.evaluation.protocol import Code, FrozenModel, Identifier
from minimew.evaluation.runtime_protocol import JobRequest
from minimew.evaluation.runtime_result import ArtifactV2
from minimew.evaluation.store import EvaluationConflict, EvaluationNotFound, EvaluationStore

router = APIRouter(
    prefix="/v1/evaluation-jobs", tags=["evaluation"], dependencies=[Depends(no_store)]
)

# FastAPI已将HTTP JSON解码成Python对象；按JSON语义严格验证UUID/数组，
# 不放宽字段类型（例如query/次数仍不接受隐式类型转换）。
JsonJobRequest = Annotated[
    JobRequest,
    BeforeValidator(
        lambda value: (
            JobRequest.model_validate_json(json.dumps(value)) if isinstance(value, dict) else value
        )
    ),
]


class JobReceipt(FrozenModel):
    job_id: UUID


class CaseView(FrozenModel):
    case_id: Identifier
    status: Literal[
        "pending",
        "generating",
        "waiting",
        "accepted",
        "generated",
        "judging",
        "completed",
        "failed",
        "cancelled",
    ]
    error_code: Code | None


class JobView(FrozenModel):
    job_id: UUID
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    cancel_requested: bool
    error_code: Code | None
    cases: tuple[CaseView, ...]


class JobReport(FrozenModel):
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    report: ArtifactV2 | None
    error_code: Code | None


def store(request):
    return EvaluationStore(request.app.state.session_factory)


@router.post("", status_code=201, response_model=JobReceipt)
async def create(
    data: JsonJobRequest,
    request: Request,
    user: CurrentUser,
    idempotency_key: str = Header(min_length=1, max_length=128),
):
    try:
        job_id = await store(request).create(user.id, idempotency_key, data)
        return JobReceipt(job_id=job_id)
    except EvaluationNotFound:
        raise HTTPException(404, "Evaluation corpus not found") from None
    except EvaluationConflict:
        raise HTTPException(409, "Evaluation input conflicts with frozen state") from None
    except (SQLAlchemyError, OSError, TimeoutError):
        raise HTTPException(503, "Evaluation storage unavailable") from None


@router.get("/{job_id}", response_model=JobView)
async def read(job_id: UUID, request: Request, user: CurrentUser):
    try:
        job, cases = await store(request).read(user.id, job_id)
        return JobView.model_validate(
            {
                "job_id": job.id,
                "status": job.status,
                "cancel_requested": job.cancel_requested,
                "error_code": job.error_code,
                "cases": tuple(
                    {"case_id": c.case_id, "status": c.status, "error_code": c.error_code}
                    for c in cases
                ),
            }
        )
    except EvaluationNotFound:
        raise HTTPException(404, "Evaluation job not found") from None
    except (SQLAlchemyError, OSError, TimeoutError):
        raise HTTPException(503, "Evaluation storage unavailable") from None


@router.post("/{job_id}/cancel", status_code=202, response_model=JobView)
async def cancel(job_id: UUID, request: Request, user: CurrentUser):
    try:
        await store(request).cancel(user.id, job_id)
        return await read(job_id, request, user)
    except EvaluationNotFound:
        raise HTTPException(404, "Evaluation job not found") from None
    except (SQLAlchemyError, OSError, TimeoutError):
        raise HTTPException(503, "Evaluation storage unavailable") from None


@router.get("/{job_id}/report", response_model=JobReport)
async def report(job_id: UUID, request: Request, user: CurrentUser):
    try:
        job, _ = await store(request).read(user.id, job_id)
        return JobReport.model_validate_json(
            json.dumps({"status": job.status, "report": job.report, "error_code": job.error_code})
        )
    except EvaluationNotFound:
        raise HTTPException(404, "Evaluation job not found") from None
    except (SQLAlchemyError, OSError, TimeoutError):
        raise HTTPException(503, "Evaluation storage unavailable") from None
