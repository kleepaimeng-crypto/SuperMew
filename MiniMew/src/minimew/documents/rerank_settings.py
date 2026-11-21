"""A knowledge base owns its rerank selection; each Run freezes a private binding."""

from uuid import uuid4

from sqlalchemy import select

from minimew.account_models.credentials import encrypt
from minimew.documents.catalog import ensure_base
from minimew.documents.schemas import RerankSettingsView
from minimew.models import KnowledgeBase, ModelCredential
from minimew.rag.bailian_reranker import RERANK_URL
from minimew.rag.protocol import BAILIAN_RERANK_REVISION, BAILIAN_RERANKER, LOCAL_RERANKER


class RerankKeyRequired(Exception):
    pass


async def _base(session, owner, project):
    return await session.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.user_id == owner, KnowledgeBase.project_id == project
        )
    )


def _view(kb):
    remote = kb is not None and kb.rerank_use_bailian
    return RerankSettingsView(
        source="bailian" if remote else "local",
        model=BAILIAN_RERANKER if remote else LOCAL_RERANKER,
        bailian_key_configured=kb is not None and kb.rerank_credential_id is not None,
    )


async def read_settings(session, owner, project):
    async with session.begin():
        if project is not None:
            from minimew.projects import owned_project

            await owned_project(session, owner, project)
        return _view(await _base(session, owner, project))


async def save_settings(session, owner, project, data, settings):
    async with session.begin():
        if project is not None:
            from fastapi import HTTPException

            from minimew.projects import owned_project

            await owned_project(session, owner, project)
            raise HTTPException(409, detail={"code": "PROJECT_MODELS_IMMUTABLE"})
        kb = await ensure_base(
            session, owner, project, settings.embedding_profile, settings.document_index_layout
        )
        if data.api_key is not None:
            identity = uuid4()
            item = ModelCredential(
                id=identity,
                user_id=owner,
                model_id=kb.id,
                retrieval_url=RERANK_URL,
                encrypted_value=encrypt(
                    settings, owner, identity, RERANK_URL, data.api_key.get_secret_value()
                ),
            )
            session.add(item)
            await session.flush()
            kb.rerank_credential_id = identity
        if data.source == "bailian" and kb.rerank_credential_id is None:
            raise RerankKeyRequired
        kb.rerank_use_bailian = data.source == "bailian"
        return _view(kb)


async def freeze_rerank(session, owner, project, snapshot):
    if snapshot.rag is None or not snapshot.rag.rerank or snapshot.rag.strategy != "hybrid":
        return snapshot, None
    kb = await _base(session, owner, project)
    if kb is None or not kb.rerank_use_bailian:
        return snapshot, None
    policy = snapshot.rag.model_copy(
        update={"reranker": BAILIAN_RERANKER, "revision": BAILIAN_RERANK_REVISION}
    )
    return snapshot.model_copy(update={"rag": policy}), kb.rerank_credential_id
