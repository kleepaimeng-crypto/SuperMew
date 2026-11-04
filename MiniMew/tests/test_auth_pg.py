"""由 check_models.py 显式指定随机临时库，默认 pytest 不联网。"""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from minimew.app import create_app
from minimew.database import create_engine
from minimew.models import User
from minimew.security import verify_password
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


async def stored_credentials(username):
    engine = create_engine(Settings())
    try:
        async with engine.connect() as connection:
            return (
                await connection.execute(
                    select(User.id, User.password_hash).where(User.username == username)
                )
            ).one()
    finally:
        await engine.dispose()


def test_register_login_me_and_deleted_user(client):
    password = "integration-password-1234"
    registered = client.post("/auth/register", json={"username": " Alice ", "password": password})
    assert registered.status_code == 201, registered.text
    assert set(registered.json()) == {"id", "username", "created_at"}
    assert registered.json()["username"] == "alice"
    user_id, encoded = asyncio.run(stored_credentials("alice"))
    assert encoded.startswith("$argon2id$") and password not in encoded
    assert verify_password(password, encoded)
    logged_in = client.post("/auth/login", json={"username": "ALICE", "password": password})
    assert logged_in.status_code == 200, logged_in.text
    assert logged_in.headers["cache-control"] == "no-store"
    assert "minimew_refresh" in logged_in.cookies
    assert logged_in.json()["expires_in"] == 900
    headers = {"Authorization": "Bearer " + logged_in.json()["access_token"]}
    assert client.get("/auth/me", headers=headers).json() == registered.json()

    async def remove_user():
        engine = create_engine(Settings())
        try:
            async with engine.begin() as connection:
                await connection.execute(delete(User).where(User.id == user_id))
        finally:
            await engine.dispose()

    asyncio.run(remove_user())
    assert client.get("/auth/me", headers=headers).status_code == 401
    assert client.post("/auth/refresh").status_code == 401


def test_wrong_password_and_unknown_username_have_same_response(client):
    assert (
        client.post(
            "/auth/register", json={"username": "login_test", "password": "correct-password-1234"}
        ).status_code
        == 201
    )
    wrong = client.post("/auth/login", json={"username": "login_test", "password": "wrong"})
    unknown = client.post("/auth/login", json={"username": "unknown", "password": "wrong"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Invalid credentials"}


def test_concurrent_duplicate_registration_uses_database_constraint(client):
    def register(username):
        return client.post(
            "/auth/register", json={"username": username, "password": "concurrent-password-1234"}
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(register, ["DuPlicate", " duplicate "]))
    assert sorted(r.status_code for r in results) == [201, 409]
    conflict = next(r for r in results if r.status_code == 409)
    assert conflict.json() == {"detail": "Username unavailable"}
    asyncio.run(stored_credentials("duplicate"))  # .one() 同时验证只有一条记录。
