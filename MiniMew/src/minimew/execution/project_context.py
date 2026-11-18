"""项目聊天的有界目录提示；不是Evidence，也不授予工具权限。"""

import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from minimew.execution.messages import valid_text
from minimew.models import Document, DocumentVersion, KnowledgeBase, Project, Thread

MAX_CATALOG_TITLES = 8
MAX_TITLE_CHARS = 80
MAX_CATALOG_PROMPT_CHARS = 1000


def catalog_text(value: str, maximum: int) -> str:
    # 名称只是提示数据；规范控制字符并限制JSON转义后的上下文开销。
    return " ".join("".join(c if c.isprintable() else " " for c in value).split())[:maximum]


@dataclass(frozen=True)
class ProjectContext:
    project_id: UUID
    name: str
    document_titles: tuple[str, ...]
    catalog_limited: bool = False

    def __post_init__(self):
        if (
            not isinstance(self.project_id, UUID)
            or not valid_text(self.name, 100, nonblank=True)
            or not isinstance(self.document_titles, tuple)
            or len(self.document_titles) > MAX_CATALOG_TITLES
            or any(not valid_text(t, MAX_TITLE_CHARS, nonblank=True) for t in self.document_titles)
            or type(self.catalog_limited) is not bool
        ):
            raise ValueError("Invalid project chat context")

    def prompt_data(self):
        data = {
            "name": self.name,
            "published_document_titles": list(self.document_titles),
            "catalog_limited": self.catalog_limited,
        }
        while len(json.dumps(data, ensure_ascii=False)) > MAX_CATALOG_PROMPT_CHARS:
            data["published_document_titles"].pop()
            data["catalog_limited"] = True
        return data


async def load_project_context(session, user_id: UUID, thread_id: UUID) -> ProjectContext | None:
    thread = await session.scalar(
        select(Thread).where(Thread.id == thread_id, Thread.user_id == user_id)
    )
    if thread is None:
        raise ValueError("Thread not owned")
    if thread.project_id is None:
        return None
    project = await session.scalar(
        select(Project).where(Project.id == thread.project_id, Project.user_id == user_id)
    )
    if project is None:
        raise ValueError("Project not owned")
    titles = (
        await session.scalars(
            select(Document.title)
            .join(KnowledgeBase, Document.knowledge_base_id == KnowledgeBase.id)
            .join(DocumentVersion, Document.current_version_id == DocumentVersion.id)
            .where(
                KnowledgeBase.user_id == user_id,
                KnowledgeBase.project_id == project.id,
                Document.user_id == user_id,
                Document.deleted_at.is_(None),
                DocumentVersion.user_id == user_id,
                DocumentVersion.status == "published",
            )
            .order_by(Document.created_at.desc(), Document.id)
            .limit(MAX_CATALOG_TITLES + 1)
        )
    ).all()
    return ProjectContext(
        project.id,
        catalog_text(project.name, 100),
        tuple(catalog_text(title, MAX_TITLE_CHARS) for title in titles[:MAX_CATALOG_TITLES]),
        len(titles) > MAX_CATALOG_TITLES,
    )
