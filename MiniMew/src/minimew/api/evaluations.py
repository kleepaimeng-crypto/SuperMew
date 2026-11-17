"""评测Job的唯一HTTP生命周期入口；不在请求内执行模型。"""

import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BeforeValidator
from sqlalchemy.exc import SQLAlchemyError

from minimew.api.auth import CurrentUser
from minimew.api.threads import no_store
from minimew.evaluation.protocol import Code, FrozenModel, Identifier
from minimew.evaluation.runtime_protocol import JobRequest
from minimew.evaluation.runtime_result import ArtifactV2
from minimew.evaluation.store import EvaluationConflict, EvaluationNotFound, EvaluationStore
from minimew.evaluation.workbench import (
    CaseDetail,
    CompareRequest,
    CorpusPage,
    CorpusView,
    JobPage,
    JobSummary,
    ValidatedJob,
)

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


def json_request(model):
    return BeforeValidator(
        lambda value: (
            model.model_validate_json(json.dumps(value)) if isinstance(value, dict) else value
        )
    )


JsonComparison = Annotated[CompareRequest, json_request(CompareRequest)]


async def workbench_call(operation):
    try:
        return await operation
    except EvaluationNotFound:
        raise HTTPException(404, "Evaluation resource not found") from None
    except EvaluationConflict:
        raise HTTPException(409, "Evaluation inputs conflict with persisted state") from None
    except (SQLAlchemyError, OSError, TimeoutError):
        raise HTTPException(503, "Evaluation storage unavailable") from None


@router.get("", response_model=JobPage)
async def history(
    request: Request,
    user: CurrentUser,
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
):
    jobs = await workbench_call(store(request).history(user.id, offset, limit))
    return JobPage(
        items=tuple(
            JobSummary(
                job_id=j.id,
                dataset_id=j.snapshot["request"]["dataset"]["dataset_id"],
                status=j.status,
                created_at=j.created_at,
                decision=j.report["decision"] if j.report else None,
            )
            for j in jobs[:limit]
        ),
        next_offset=offset + limit if len(jobs) > limit else None,
    )


@router.get("/corpora", response_model=CorpusPage)
async def corpora(
    request: Request,
    user: CurrentUser,
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
):
    rows = await workbench_call(store(request).corpora(user.id, offset, limit))
    return CorpusPage(
        items=tuple(
            CorpusView.model_validate_json(
                json.dumps(
                    {
                        "corpus_id": str(c.id),
                        "source_ids": [b["source_id"] for b in c.payload["bindings"]],
                        "corpus_fingerprint": c.payload["corpus_fingerprint"],
                        "expires_at": c.payload["scope"]["expires_at"],
                    }
                )
            )
            for c in rows[:limit]
        ),
        next_offset=offset + limit if len(rows) > limit else None,
    )


@router.post("/validate", response_model=ValidatedJob)
async def validate(data: JsonJobRequest, request: Request, user: CurrentUser):
    await workbench_call(store(request).validate(user.id, data))
    return ValidatedJob(
        case_count=len(data.dataset.cases), total_model_limit=data.total_model_limit
    )


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


@router.get("/{job_id}/cases/{case_id}", response_model=CaseDetail)
async def case_detail(job_id: UUID, case_id: str, request: Request, user: CurrentUser):
    return await workbench_call(store(request).case_detail(user.id, job_id, case_id))


@router.post("/{job_id}/compare", response_model=ArtifactV2)
async def compare(job_id: UUID, data: JsonComparison, request: Request, user: CurrentUser):
    return await workbench_call(store(request).compare(user.id, job_id, data))
