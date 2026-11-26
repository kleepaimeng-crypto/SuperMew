from contextlib import contextmanager
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse

from minimew.api.auth import bearer, get_current_access
from minimew.api.threads import no_store
from minimew.database import SessionDependency
from minimew.documents import catalog, rebuilds, rerank_settings
from minimew.documents.profiles import BuildConfiguration, preset
from minimew.documents.schemas import (
    DocumentPage,
    DocumentResponse,
    EmbeddingChange,
    EmbeddingSettingsView,
    IndexJobResponse,
    RerankChange,
    RerankSettingsView,
    UploadKey,
    UploadReceipt,
    VersionResponse,
)
from minimew.documents.storage import InvalidSource, SourceStore, SourceTooLarge, validate_source
from minimew.model_control import ModelConfigurationUnavailable
from minimew.models import User
from minimew.settings import SettingsDependency

router = APIRouter(prefix="/v1", tags=["documents"], dependencies=[Depends(no_store)])


@contextmanager
def public_errors():
    try:
        yield
    except catalog.DocumentNotFound:
        raise HTTPException(404, "Document or job not found") from None
    except catalog.SubmissionConflict:
        raise HTTPException(409, "Idempotency key belongs to another submission") from None
    except catalog.EmbeddingRebuildInProgress:
        raise HTTPException(409, detail={"code": "EMBEDDING_REBUILD_IN_PROGRESS"}) from None
    except catalog.LocalModelsDisabled:
        raise HTTPException(409, detail={"code": "LOCAL_MODELS_DISABLED"}) from None
    except SourceTooLarge:
        raise HTTPException(413, "Upload too large") from None
    except InvalidSource:
        raise HTTPException(
            422, "Expected a valid UTF-8 Markdown/TXT within the upload limit"
        ) from None
    except (SQLAlchemyError, TimeoutError, OSError, catalog.BuildUnavailable):
        raise HTTPException(
            503,
            detail={"code": "DOCUMENT_STORAGE_UNAVAILABLE"},
            headers={"Cache-Control": "no-store"},
        ) from None


async def document_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: SettingsDependency,
    session: SessionDependency,
) -> User:
    with public_errors():
        try:
            return (await get_current_access(credentials, settings, session)).user
        except HTTPException as failure:
            failure.headers = {**(failure.headers or {}), "Cache-Control": "no-store"}
            raise


DocumentUser = Annotated[User, Depends(document_user)]


@router.get("/rerank-settings", response_model=RerankSettingsView)
async def read_rerank_settings(
    user: DocumentUser,
    session: SessionDependency,
    settings: SettingsDependency,
    project_id: UUID | None = None,
):
    with public_errors():
        return await rerank_settings.read_settings(session, user.id, project_id, settings)


@router.put("/rerank-settings", response_model=RerankSettingsView)
async def save_rerank_settings(
    data: RerankChange,
    user: DocumentUser,
    session: SessionDependency,
    settings: SettingsDependency,
    project_id: UUID | None = None,
):
    with public_errors():
        try:
            return await rerank_settings.save_settings(session, user.id, project_id, data, settings)
        except rerank_settings.RerankKeyRequired:
            raise HTTPException(409, detail={"code": "RERANK_KEY_REQUIRED"}) from None
        except ModelConfigurationUnavailable:
            raise HTTPException(503, detail={"code": "RERANK_KEY_STORAGE_UNAVAILABLE"}) from None


@router.get("/embedding-profiles/selection", response_model=EmbeddingSettingsView)
async def embedding_selection(
    user: DocumentUser,
    session: SessionDependency,
    settings: SettingsDependency,
    project_id: UUID | None = None,
):
    with public_errors():
        return await rebuilds.settings_view(session, user.id, project_id, settings)


@router.post("/embedding-profiles/selection", response_model=EmbeddingSettingsView, status_code=202)
async def request_embedding_rebuild(
    data: EmbeddingChange,
    user: DocumentUser,
    session: SessionDependency,
    settings: SettingsDependency,
    project_id: UUID | None = None,
):
    with public_errors():
        try:
            return await rebuilds.start_rebuild(session, user.id, project_id, data, settings)
        except rebuilds.RebuildConflict as exc:
            raise HTTPException(409, detail={"code": str(exc)}) from None
        except ModelConfigurationUnavailable:
            raise HTTPException(503, detail={"code": "EMBEDDING_KEY_STORAGE_UNAVAILABLE"}) from None


class UploadLimitMiddleware:
    """在 multipart 解析前限制流量；Content-Length 与分块传输都受同一预算约束。"""

    def __init__(self, app, max_bytes):
        self.app, self.max_bytes = app, max_bytes + 64 * 1024

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not any(
            scope["path"] == prefix or scope["path"].startswith(prefix + "/")
            for prefix in (
                "/v1/documents",
                "/v1/index-jobs",
                "/v1/embedding-profiles",
                "/v1/rerank-settings",
            )
        ):
            return await self.app(scope, receive, send)

        async def private_send(message):
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        if scope["method"] != "POST":
            return await self.app(scope, receive, private_send)
        headers = dict(scope["headers"])
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = self.max_bytes + 1
        if length > self.max_bytes:
            return await JSONResponse(
                status_code=413,
                content={"detail": "Upload too large"},
                headers={"Cache-Control": "no-store"},
            )(scope, receive, private_send)
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > self.max_bytes:
                raise HTTPException(413, "Upload too large", headers={"Cache-Control": "no-store"})
            return message

        return await self.app(scope, limited_receive, private_send)


async def accept_upload(
    request, file, key, user, session, settings, document_id=None, project_id=None
):
    form = await request.form()
    if set(form.keys()) != (
        {"file", "idempotency_key", "project_id"}
        if project_id is not None
        else {"file", "idempotency_key"}
    ) or len(form.multi_items()) != (3 if project_id is not None else 2):
        raise HTTPException(422, "Invalid upload fields")
    with public_errors():
        source = validate_source(
            file.filename or "",
            await file.read(settings.document_max_upload_bytes + 1),
            settings.document_max_upload_bytes,
        )
        key = UploadKey(idempotency_key=key).idempotency_key
        return await catalog.reserve_document(
            session,
            SourceStore(settings.document_upload_dir),
            user.id,
            source,
            key,
            settings.embedding_profile,
            document_id,
            index_layout=settings.document_index_layout,
            project_id=project_id,
            local_models_enabled=settings.local_models_enabled,
        )


UploadFileField = Annotated[UploadFile, File()]
SubmissionKey = Annotated[str, Form(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]


@router.post(
    "/documents",
    status_code=202,
    response_model=UploadReceipt,
    responses={200: {"model": UploadReceipt}},
)
async def upload_document(
    request: Request,
    response: Response,
    user: DocumentUser,
    session: SessionDependency,
    settings: SettingsDependency,
    file: UploadFileField,
    idempotency_key: SubmissionKey,
    project_id: Annotated[UUID | None, Form()] = None,
) -> UploadReceipt:
    result, created = await accept_upload(
        request, file, idempotency_key, user, session, settings, project_id=project_id
    )
    response.status_code = 202 if created else 200
    return result


@router.post(
    "/documents/{document_id}/versions",
    status_code=202,
    response_model=UploadReceipt,
    responses={200: {"model": UploadReceipt}},
)
async def upload_version(
    document_id: UUID,
    request: Request,
    response: Response,
    user: DocumentUser,
    session: SessionDependency,
    settings: SettingsDependency,
    file: UploadFileField,
    idempotency_key: SubmissionKey,
) -> UploadReceipt:
    result, created = await accept_upload(
        request, file, idempotency_key, user, session, settings, document_id
    )
    response.status_code = 202 if created else 200
    return result


@router.get("/documents", response_model=DocumentPage)
async def documents(
    user: DocumentUser,
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    project_id: UUID | None = None,
    archived: bool = False,
) -> DocumentPage:
    with public_errors():
        return await catalog.list_documents(
            session, user.id, limit, offset, project_id=project_id, archived=archived
        )


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def document(
    document_id: UUID, user: DocumentUser, session: SessionDependency
) -> DocumentResponse:
    with public_errors():
        return await catalog.get_document(session, user.id, document_id)


@router.get("/documents/{document_id}/versions/{version_id}", response_model=VersionResponse)
async def version(
    document_id: UUID, version_id: UUID, user: DocumentUser, session: SessionDependency
) -> VersionResponse:
    with public_errors():
        return await catalog.get_version(session, user.id, document_id, version_id)


@router.get("/index-jobs/{job_id}", response_model=IndexJobResponse)
async def job(job_id: UUID, user: DocumentUser, session: SessionDependency) -> IndexJobResponse:
    with public_errors():
        return await catalog.get_job(session, user.id, job_id)


@router.get("/embedding-profiles", response_model=list[BuildConfiguration])
async def embedding_profiles(user: DocumentUser) -> list[BuildConfiguration]:
    return [preset("bge-small-zh-v1.5"), preset("bge-m3")]


@router.delete("/documents/{document_id}", response_model=DocumentResponse)
async def withdraw_document(document_id: UUID, user: DocumentUser, session: SessionDependency):
    with public_errors():
        return await catalog.withdraw(session, user.id, document_id)
