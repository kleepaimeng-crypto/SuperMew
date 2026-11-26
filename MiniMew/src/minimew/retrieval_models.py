"""Reusable retrieval presets and project-owned immutable model selections."""

from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from sqlalchemy import select

from minimew.account_models.credentials import encrypt
from minimew.account_models.network import public_url
from minimew.api.auth import CurrentUser
from minimew.api.threads import no_store
from minimew.database import SessionDependency
from minimew.documents.profiles import (
    BAILIAN_ID,
    CURRENT_CHUNKER,
    hybrid_preset,
    preset,
)
from minimew.model_control import ModelConfigurationUnavailable
from minimew.models import KnowledgeBase, ModelCredential, RetrievalModelConfiguration
from minimew.rag.protocol import LOCAL_RERANKER
from minimew.settings import SettingsDependency

Purpose = Literal["embedding", "rerank"]


class PrivateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class CredentialWrite(PrivateInput):
    api_key: SecretStr = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def valid_key(self):
        if any(ord(c) < 33 or ord(c) > 126 for c in self.api_key.get_secret_value()):
            raise ValueError("Invalid API Key")
        return self


class CloudConfiguration(PrivateInput):
    purpose: Purpose
    provider: Literal["bailian"] = "bailian"
    model_name: str
    base_url: str = Field(max_length=2048)

    @model_validator(mode="after")
    def supported_protocol(self):
        expected = "text-embedding-v4" if self.purpose == "embedding" else "qwen3-rerank"
        if self.model_name != expected:
            raise ValueError("Unsupported retrieval model")
        self.base_url = public_url(self.base_url).rstrip("/")
        url = urlsplit(self.base_url)
        if url.hostname not in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"}:
            raise ValueError("Bailian endpoint required")
        path = (
            "/compatible-mode/v1" if self.purpose == "embedding" else "/compatible-api/v1/reranks"
        )
        if url.path != path or url.query or url.fragment:
            raise ValueError("Unsupported retrieval endpoint")
        return self


class TemplateWrite(CloudConfiguration):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[^\x00]+$")
    api_key: SecretStr | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def key_text(self):
        if self.api_key is not None:
            CredentialWrite(api_key=self.api_key)
        if not self.name.strip():
            raise ValueError("Name required")
        return self

    def selection(self):
        return CloudConfiguration.model_validate(
            self.model_dump(include={"purpose", "provider", "model_name", "base_url"})
        ).model_dump()


class TemplateView(CloudConfiguration):
    id: UUID
    name: str
    key_configured: bool


class SelectionView(BaseModel):
    source: Literal["local", "bailian"]
    model_name: str
    base_url: str | None = None
    key_configured: bool = False


class ProjectModelsView(BaseModel):
    embedding: SelectionView
    rerank: SelectionView


def local_selection(purpose, model_name=None):
    return dict(
        source="local",
        model_name=model_name
        or ("BAAI/bge-small-zh-v1.5" if purpose == "embedding" else LOCAL_RERANKER),
        base_url=None,
    )


def view(kb):
    if kb is None or kb.project_models is None:
        raise HTTPException(409, detail={"code": "PROJECT_MODELS_NOT_BOUND"})
    return ProjectModelsView(
        **{
            purpose: {**kb.project_models[purpose], "key_configured": credential is not None}
            for purpose, credential in (
                ("embedding", kb.embedding_credential_id),
                ("rerank", kb.rerank_credential_id),
            )
        }
    )


async def save_credential(session, settings, owner, model_id, endpoint, key):
    identity = uuid4()
    try:
        encrypted = encrypt(settings, owner, identity, endpoint, key.get_secret_value())
    except ModelConfigurationUnavailable:
        raise HTTPException(503, detail={"code": "CREDENTIAL_STORAGE_UNAVAILABLE"}) from None
    session.add(
        ModelCredential(
            id=identity,
            user_id=owner,
            model_id=model_id,
            encrypted_value=encrypted,
            retrieval_url=endpoint,
        )
    )
    await session.flush()
    return identity


async def owned_template(session, owner, identity, *, lock=False):
    query = select(RetrievalModelConfiguration).where(
        RetrievalModelConfiguration.id == identity, RetrievalModelConfiguration.user_id == owner
    )
    row = await session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise HTTPException(404, "Model configuration not found")
    return row


async def bind_project(session, owner, project_id, data, settings):
    selections, credentials = {}, {}
    # Stable lock ordering when a template is reused for project creation.
    for purpose in ("embedding", "rerank"):
        identity = getattr(data, f"{purpose}_configuration_id")
        if identity is None:
            if not settings.local_models_enabled:
                raise HTTPException(409, detail={"code": "LOCAL_MODELS_DISABLED"})
            selections[purpose], credentials[purpose] = local_selection(purpose), None
            continue
        template = await owned_template(session, owner, identity, lock=True)
        config = CloudConfiguration.model_validate(template.configuration)
        if config.purpose != purpose:
            raise HTTPException(422, "Model purpose mismatch")
        selections[purpose] = dict(
            source="bailian", model_name=config.model_name, base_url=config.base_url
        )
        credentials[purpose] = template.credential_id
    profile = BAILIAN_ID if selections["embedding"]["source"] == "bailian" else "bge-small-zh-v1.5"
    build = (hybrid_preset if "hybrid" in settings.document_index_layout else preset)(profile)
    build = build.model_copy(update={"parser": "markdown-txt-v2", "chunker": CURRENT_CHUNKER})
    kb = KnowledgeBase(
        user_id=owner,
        project_id=project_id,
        project_models=selections,
        build_configuration=build.model_dump(mode="json"),
        build_fingerprint=build.fingerprint(),
        embedding_credential_id=credentials["embedding"],
        rerank_use_bailian=selections["rerank"]["source"] == "bailian",
        rerank_credential_id=credentials["rerank"],
    )
    session.add(kb)
    await session.flush()


router = APIRouter(prefix="/v1", tags=["retrieval-models"], dependencies=[Depends(no_store)])


def template_view(row):
    return TemplateView(id=row.id, name=row.name, key_configured=True, **row.configuration)


@router.get("/retrieval-models", response_model=list[TemplateView])
async def list_templates(user: CurrentUser, session: SessionDependency):
    rows = await session.scalars(
        select(RetrievalModelConfiguration)
        .where(RetrievalModelConfiguration.user_id == user.id)
        .order_by(RetrievalModelConfiguration.name, RetrievalModelConfiguration.id)
    )
    return [template_view(row) for row in rows]


@router.post("/retrieval-models", response_model=TemplateView, status_code=201)
async def create_template(
    data: TemplateWrite, user: CurrentUser, session: SessionDependency, settings: SettingsDependency
):
    if data.api_key is None:
        raise HTTPException(422, "API Key required")
    async with session.begin():
        identity = uuid4()
        credential = await save_credential(
            session, settings, user.id, identity, data.base_url, data.api_key
        )
        row = RetrievalModelConfiguration(
            id=identity,
            user_id=user.id,
            name=data.name,
            configuration=data.selection(),
            credential_id=credential,
        )
        session.add(row)
        await session.flush()
        return template_view(row)


@router.put("/retrieval-models/{identity}", response_model=TemplateView)
async def update_template(
    identity: UUID,
    data: TemplateWrite,
    user: CurrentUser,
    session: SessionDependency,
    settings: SettingsDependency,
):
    async with session.begin():
        row = await owned_template(session, user.id, identity, lock=True)
        changed = data.selection() != row.configuration
        if changed and data.api_key is None:
            raise HTTPException(422, "API Key required when changing configuration")
        if data.api_key is not None:
            row.credential_id = await save_credential(
                session, settings, user.id, identity, data.base_url, data.api_key
            )
        row.name, row.configuration = data.name, data.selection()
        await session.flush()
        return template_view(row)


async def project_base(session, owner, project_id):
    from minimew.projects import owned_project

    await owned_project(session, owner, project_id, lock=True)
    return await session.scalar(
        select(KnowledgeBase)
        .where(KnowledgeBase.user_id == owner, KnowledgeBase.project_id == project_id)
        .with_for_update()
    )


@router.get("/projects/{project_id}/models", response_model=ProjectModelsView)
async def project_models(project_id: UUID, user: CurrentUser, session: SessionDependency):
    async with session.begin():
        return view(await project_base(session, user.id, project_id))


@router.put("/projects/{project_id}/models/{purpose}/credential", response_model=ProjectModelsView)
async def rotate_project_key(
    project_id: UUID,
    purpose: Purpose,
    data: CredentialWrite,
    user: CurrentUser,
    session: SessionDependency,
    settings: SettingsDependency,
):
    async with session.begin():
        kb = await project_base(session, user.id, project_id)
        selection = getattr(view(kb), purpose)
        if selection.source != "bailian":
            raise HTTPException(409, "Local model has no API credential")
        identity = await save_credential(
            session, settings, user.id, kb.id, selection.base_url, data.api_key
        )
        setattr(kb, f"{purpose}_credential_id", identity)
        await session.flush()
        return view(kb)
