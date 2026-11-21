"""显式临时 PostgreSQL 中验证归属、持久化与事务回滚。"""

import asyncio
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from minimew.app import create_app
from minimew.database import create_engine
from minimew.models import Thread
from minimew.settings import Settings

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Explicit temporary PostgreSQL required"
)


@pytest.fixture
def client(monkeypatch):
    database = os.environ["MINIMEW_INTEGRATION_DB"]
    assert database.startswith("minimew_test_")
    assert len(database.removeprefix("minimew_test_")) == 32
    monkeypatch.setenv("MINIMEW_DB_NAME", database)
    with TestClient(create_app()) as client:
        yield client


def account(client):
    credentials = {"username": "thr_" + uuid4().hex[:20], "password": "thread-test-password-1234"}
    registered = client.post("/auth/register", json=credentials)
    assert registered.status_code == 201
    logged_in = client.post("/auth/login", json=credentials)
    assert logged_in.status_code == 200
    return registered.json()["id"], {"Authorization": "Bearer " + logged_in.json()["access_token"]}


async def saved_threads(user_id):
    from uuid import UUID

    engine = create_engine(Settings())
    try:
        async with engine.connect() as connection:
            return (
                await connection.execute(
                    select(Thread.id, Thread.title).where(Thread.user_id == UUID(user_id))
                )
            ).all()
    finally:
        await engine.dispose()


def test_create_persists_default_title_and_cannot_read_other_users_thread(client):
    owner_id, owner = account(client)
    _, stranger = account(client)
    response = client.post("/v1/threads", json={}, headers=owner)
    assert response.status_code == 201, response.text
    thread = response.json()
    assert set(thread) == {"id", "title", "created_at", "project_id", "archived"}
    assert thread["archived"] is False and thread["project_id"] is None
    assert thread["title"] == "新对话"
    assert response.headers["cache-control"] == "no-store"
    rows = asyncio.run(saved_threads(owner_id))
    assert len(rows) == 1 and str(rows[0].id) == thread["id"] and rows[0].title == "新对话"
    # 新 app 的新连接也能读到；不是进程内保存的对象。
    with TestClient(create_app()) as second:
        assert second.get(f"/v1/threads/{thread['id']}", headers=owner).json() == thread
    denied = client.get(f"/v1/threads/{thread['id']}", headers=stranger)
    absent = client.get(f"/v1/threads/{uuid4()}", headers=stranger)
    assert denied.status_code == absent.status_code == 404
    assert denied.json() == absent.json() == {"detail": "Thread not found"}
    assert client.get("/v1/threads", headers=stranger).json() == {"items": [], "next_offset": None}


def test_owner_scoped_pagination_and_validation(client):
    owner_id, owner = account(client)
    _, stranger = account(client)
    own = [client.post("/v1/threads", json={}, headers=owner).json() for _ in range(3)]
    assert client.post("/v1/threads", json={}, headers=stranger).status_code == 201
    first = client.get("/v1/threads?limit=2", headers=owner).json()
    last = client.get(f"/v1/threads?limit=2&offset={first['next_offset']}", headers=owner).json()
    assert first["next_offset"] == 2 and last["next_offset"] is None
    expected = sorted(own, key=lambda t: (t["created_at"], t["id"]), reverse=True)
    assert first["items"] + last["items"] == expected
    assert client.get("/v1/threads?offset=50", headers=owner).json()["items"] == []
    for query in ["limit=0", "limit=101", "offset=-1", "limit=abc"]:
        assert client.get("/v1/threads?" + query, headers=owner).status_code == 422
    assert client.get("/v1/threads/not-a-uuid", headers=owner).status_code == 422
    for body in [{"user_id": owner_id}, {"id": str(uuid4())}, {"title": "自定义"}]:
        assert client.post("/v1/threads", json=body, headers=owner).status_code == 422
    assert len(asyncio.run(saved_threads(owner_id))) == 3


def test_equal_creation_times_have_stable_order(client):
    from uuid import UUID

    from sqlalchemy import update

    user_id, headers = account(client)
    for _ in range(3):
        assert client.post("/v1/threads", json={}, headers=headers).status_code == 201

    async def same_time():
        engine = create_engine(Settings())
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    update(Thread)
                    .where(Thread.user_id == UUID(user_id))
                    .values(created_at=func.now())
                )
        finally:
            await engine.dispose()

    asyncio.run(same_time())
    pages = [
        client.get(f"/v1/threads?limit=1&offset={i}", headers=headers).json() for i in range(3)
    ]
    ids = [page["items"][0]["id"] for page in pages]
    assert len(set(ids)) == 3 and ids == sorted(ids, reverse=True)


def test_failure_after_flush_rolls_back_thread(client, monkeypatch):
    user_id, headers = account(client)
    original = AsyncSession.flush

    async def fail_after_flush(self, *args, **kwargs):
        await original(self, *args, **kwargs)
        raise RuntimeError("injected failure after flush")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "flush", fail_after_flush)
        with pytest.raises(RuntimeError, match="injected failure"):
            client.post("/v1/threads", json={}, headers=headers)
    assert asyncio.run(saved_threads(user_id)) == []
    assert client.post("/v1/threads", json={}, headers=headers).status_code == 201
