"""项目与用户显式记忆；项目锁串行化写入和Run快照。"""

import json
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select

from minimew.api.auth import CurrentUser
from minimew.api.threads import no_store
from minimew.database import SessionDependency
from minimew.models import Project, ProjectMemory


class TextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="after", check_fields=False)
    @classmethod
    def text(cls, value):
        if isinstance(value, str) and (not value.strip() or "\x00" in value):
            raise ValueError("Nonblank text required")
        return value


class ProjectInput(TextInput):
    name: str = Field(min_length=1, max_length=100)


class ProjectView(ProjectInput):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    created_at: datetime


class ProjectPage(BaseModel):
    items: list[ProjectView]
    next_offset: int | None


class MemoryInput(TextInput):
    content: str = Field(min_length=1, max_length=500)
    enabled: bool = True


class MemoryChange(MemoryInput):
    revision: int = Field(ge=1)


class MemoryView(MemoryInput):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    revision: int
    project_id: UUID


class MemoryPage(BaseModel):
    items: list[MemoryView]
    max_active_items: int = 20
    max_content_chars: int = 500
    snapshot_max_chars: int = 12000


async def owned_project(session, user_id, project_id, *, lock=False):
    query = select(Project).where(Project.id == project_id, Project.user_id == user_id)
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise HTTPException(404, "Project not found")
    return row


async def memory_snapshot(session, user_id, project_id):
    if project_id is None:
        return []
    await owned_project(session, user_id, project_id, lock=True)
    rows = (
        await session.scalars(
            select(ProjectMemory)
            .where(
                ProjectMemory.project_id == project_id,
                ProjectMemory.enabled.is_(True),
                ProjectMemory.deleted_at.is_(None),
            )
            .order_by(ProjectMemory.created_at, ProjectMemory.id)
        )
    ).all()
    result = [{"id": str(r.id), "revision": r.revision, "content": r.content} for r in rows]
    if len(result) > 20 or len(json.dumps(result, ensure_ascii=False)) > 12000:
        raise HTTPException(409, "Project memory budget exceeded")
    return result


router = APIRouter(prefix="/v1/projects", tags=["projects"], dependencies=[Depends(no_store)])


@router.post("", response_model=ProjectView, status_code=201)
async def create(data: ProjectInput, user: CurrentUser, session: SessionDependency):
    async with session.begin():
        row = Project(user_id=user.id, name=data.name)
        session.add(row)
        await session.flush()
        return ProjectView.model_validate(row)


@router.get("", response_model=ProjectPage)
async def list_projects(
    user: CurrentUser,
    session: SessionDependency,
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
):
    rows = (
        await session.scalars(
            select(Project)
            .where(Project.user_id == user.id)
            .order_by(Project.created_at.desc(), Project.id.desc())
            .offset(offset)
            .limit(limit + 1)
        )
    ).all()
    return ProjectPage(
        items=[ProjectView.model_validate(r) for r in rows[:limit]],
        next_offset=offset + limit if len(rows) > limit else None,
    )


@router.get("/{project_id}", response_model=ProjectView)
async def get(project_id: UUID, user: CurrentUser, session: SessionDependency):
    return ProjectView.model_validate(await owned_project(session, user.id, project_id))


@router.get("/{project_id}/memories", response_model=MemoryPage)
async def list_memories(project_id: UUID, user: CurrentUser, session: SessionDependency):
    await owned_project(session, user.id, project_id)
    rows = (
        await session.scalars(
            select(ProjectMemory)
            .where(ProjectMemory.project_id == project_id, ProjectMemory.deleted_at.is_(None))
            .order_by(ProjectMemory.created_at, ProjectMemory.id)
        )
    ).all()
    return MemoryPage(items=[MemoryView.model_validate(r) for r in rows])


@router.post("/{project_id}/memories", response_model=MemoryView, status_code=201)
async def save(project_id: UUID, data: MemoryInput, user: CurrentUser, session: SessionDependency):
    async with session.begin():
        await owned_project(session, user.id, project_id, lock=True)
        row = ProjectMemory(user_id=user.id, project_id=project_id, **data.model_dump())
        session.add(row)
        await session.flush()
        await memory_snapshot(session, user.id, project_id)
        return MemoryView.model_validate(row)


@router.patch("/{project_id}/memories/{memory_id}", response_model=MemoryView)
async def edit(
    project_id: UUID,
    memory_id: UUID,
    data: MemoryChange,
    user: CurrentUser,
    session: SessionDependency,
):
    async with session.begin():
        await owned_project(session, user.id, project_id, lock=True)
        row = await session.scalar(
            select(ProjectMemory)
            .where(
                ProjectMemory.id == memory_id,
                ProjectMemory.project_id == project_id,
                ProjectMemory.deleted_at.is_(None),
            )
            .with_for_update()
        )
        if row is None:
            raise HTTPException(404, "Memory not found")
        if row.revision != data.revision:
            raise HTTPException(409, "Memory revision changed")
        row.content, row.enabled, row.revision = data.content, data.enabled, row.revision + 1
        await session.flush()
        await memory_snapshot(session, user.id, project_id)
        return MemoryView.model_validate(row)


@router.delete("/{project_id}/memories/{memory_id}", status_code=204)
async def remove(project_id: UUID, memory_id: UUID, user: CurrentUser, session: SessionDependency):
    async with session.begin():
        await owned_project(session, user.id, project_id, lock=True)
        row = await session.scalar(
            select(ProjectMemory)
            .where(ProjectMemory.id == memory_id, ProjectMemory.project_id == project_id)
            .with_for_update()
        )
        if row is None:
            raise HTTPException(404, "Memory not found")
        if row.deleted_at is None:
            row.deleted_at = await session.scalar(select(func.clock_timestamp()))
            row.enabled = False
            row.revision += 1
