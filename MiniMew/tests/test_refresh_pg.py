"""显式临时 PostgreSQL 测试：轮换、重放提交、退出、故障回滚及跨应用并发。"""

import asyncio
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

import minimew.refresh as refresh_service
from minimew.app import create_app
from minimew.auth_http import REFRESH_COOKIE
from minimew.database import create_engine
from minimew.models import RefreshToken
from minimew.refresh import refresh_digest
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
    monkeypatch.setenv("MINIMEW_REFRESH_COOKIE_SECURE", "false")
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client


def account(client):
    data = {"username": "r_" + uuid4().hex[:20], "password": "test-refresh-password-1234"}
    registered = client.post("/auth/register", json=data)
    assert registered.status_code == 201
    return data


def login(client, data):
    response = client.post("/auth/login", json=data)
    assert response.status_code == 200
    return response.cookies[REFRESH_COOKIE], response


def use_cookie(client, path, raw=None):
    # 显式 Header 避免并发测试中客户端共享 cookie jar 决定发送哪一代凭据。
    return client.post(path, headers={"Cookie": f"{REFRESH_COOKIE}={raw}" if raw else ""})


async def rows_for(raw):
    engine = create_engine(Settings())
    try:
        async with engine.connect() as connection:
            user_id = await connection.scalar(
                select(RefreshToken.user_id).where(RefreshToken.token_hash == refresh_digest(raw))
            )
            return (
                (
                    await connection.execute(
                        select(RefreshToken.__table__).where(RefreshToken.user_id == user_id)
                    )
                )
                .mappings()
                .all()
            )
    finally:
        await engine.dispose()


def rows(raw):
    return asyncio.run(rows_for(raw))


def test_rotation_cookie_and_only_hash_persisted(client):
    raw, logged_in = login(client, account(client))
    cookie = SimpleCookie(logged_in.headers["set-cookie"])[REFRESH_COOKIE]
    assert cookie["httponly"] and cookie["path"] == "/auth"
    assert cookie["samesite"] == "lax" and not cookie["secure"]
    assert int(cookie["max-age"]) == 7 * 86400
    assert set(logged_in.json()) == {"access_token", "token_type", "expires_in"}
    assert raw not in logged_in.text
    stored = rows(raw)
    assert len(stored) == 1 and stored[0]["token_hash"] == refresh_digest(raw)
    assert raw not in repr(stored)
    refreshed = use_cookie(client, "/auth/refresh", raw)
    assert refreshed.status_code == 200
    replacement = refreshed.cookies[REFRESH_COOKIE]
    assert replacement != raw and replacement not in refreshed.text
    assert refreshed.json()["access_token"] != logged_in.json()["access_token"]
    stored = {r["token_hash"]: r for r in rows(raw)}
    assert len(stored) == 2
    assert stored[refresh_digest(raw)]["revoked_at"] is not None
    assert stored[refresh_digest(replacement)]["revoked_at"] is None
    assert (
        client.get(
            "/auth/me", headers={"Authorization": "Bearer " + refreshed.json()["access_token"]}
        ).status_code
        == 200
    )


def test_unknown_refresh_and_access_token_as_cookie_are_rejected(client):
    active, logged_in = login(client, account(client))
    for raw in [secrets.token_urlsafe(32), logged_in.json()["access_token"]]:
        response = use_cookie(client, "/auth/refresh", raw)
        assert response.status_code == 401
        assert "Max-Age=0" in response.headers["set-cookie"]
    assert rows(active)[0]["revoked_at"] is None


def test_replay_commits_user_wide_revocation_before_401(client):
    data = account(client)
    raw, _ = login(client, data)
    another_device, _ = login(client, data)
    other_user, _ = login(client, account(client))
    refreshed = use_cookie(client, "/auth/refresh", raw)
    successor = refreshed.cookies[REFRESH_COOKIE]
    replay = use_cookie(client, "/auth/refresh", raw)
    assert replay.status_code == 401
    assert "Max-Age=0" in replay.headers["set-cookie"]
    assert replay.headers["cache-control"] == "no-store"
    assert all(r["revoked_at"] is not None for r in rows(raw))
    # 新连接读取证明撤销不是仅在失败请求事务内的未提交修改。
    assert use_cookie(client, "/auth/refresh", successor).status_code == 401
    assert use_cookie(client, "/auth/refresh", another_device).status_code == 401
    assert use_cookie(client, "/auth/refresh", other_user).status_code == 200
    assert (
        client.get(
            "/auth/me", headers={"Authorization": "Bearer " + refreshed.json()["access_token"]}
        ).status_code
        == 200
    )


@pytest.mark.parametrize("already_revoked", [False, True])
def test_natural_expiry_does_not_revoke_other_credentials(client, already_revoked):
    data = account(client)
    old, _ = login(client, data)
    active, _ = login(client, data)

    async def expire():
        engine = create_engine(Settings())
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    update(RefreshToken)
                    .where(RefreshToken.token_hash == refresh_digest(old))
                    .values(
                        expires_at=datetime.now(UTC) - timedelta(seconds=1),
                        revoked_at=datetime.now(UTC) if already_revoked else None,
                    )
                )
        finally:
            await engine.dispose()

    asyncio.run(expire())
    response = use_cookie(client, "/auth/refresh", old)
    assert response.status_code == 401
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert use_cookie(client, "/auth/refresh", active).status_code == 200


def test_logout_only_revokes_current_cookie_and_is_idempotent(client):
    data = account(client)
    current, logged_in = login(client, data)
    other_device, _ = login(client, data)
    for raw in [current, current, None, "malformed"]:
        response = use_cookie(client, "/auth/logout", raw)
        assert response.status_code == 204 and not response.content
        assert "Max-Age=0" in response.headers["set-cookie"]
    stored = {r["token_hash"]: r for r in rows(current)}
    assert stored[refresh_digest(current)]["revoked_at"] is not None
    assert stored[refresh_digest(other_device)]["revoked_at"] is None
    assert use_cookie(client, "/auth/refresh", other_device).status_code == 200
    assert use_cookie(client, "/auth/refresh").status_code == 401
    assert (
        client.get(
            "/auth/me", headers={"Authorization": "Bearer " + logged_in.json()["access_token"]}
        ).status_code
        == 200
    )


def test_same_token_concurrent_refresh_across_apps(client):
    raw, _ = login(client, account(client))
    with TestClient(create_app()) as other_app, ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(use_cookie, app, "/auth/refresh", raw) for app in [client, other_app]
        ]
        responses = [f.result(timeout=15) for f in futures]
    assert sorted(r.status_code for r in responses) == [200, 401]
    stored = rows(raw)
    assert len(stored) == 2  # 只创建一个后继，随后重放处理撤销它。
    assert all(r["revoked_at"] is not None for r in stored)


def test_refresh_and_logout_use_consistent_locks(client):
    raw, _ = login(client, account(client))
    with TestClient(create_app()) as other_app, ThreadPoolExecutor(max_workers=2) as pool:
        refresh = pool.submit(use_cookie, client, "/auth/refresh", raw)
        logout = pool.submit(use_cookie, other_app, "/auth/logout", raw)
        refreshed, logged_out = refresh.result(timeout=15), logout.result(timeout=15)
    assert logged_out.status_code == 204
    assert refreshed.status_code in {200, 401}
    stored = rows(raw)
    assert next(r for r in stored if r["token_hash"] == refresh_digest(raw))["revoked_at"]
    # 若刷新先完成，旧 Cookie 的 logout 不会凭空指向新 Cookie；前端必须串行退出。
    assert sum(r["revoked_at"] is None for r in stored) == (
        1 if refreshed.status_code == 200 else 0
    )


@pytest.mark.parametrize("stage", ["signing", "after_flush"])
def test_failed_rotation_rolls_back_old_revocation_and_successor(client, monkeypatch, stage):
    raw, _ = login(client, account(client))
    with monkeypatch.context() as patch:
        if stage == "signing":

            def fail_signing(*args):
                raise RuntimeError("injected signing failure")

            patch.setattr(refresh_service, "issue_access_token", fail_signing)
        else:
            original = refresh_service.issue_pair

            async def fail_after_flush(*args):
                await original(*args)
                raise RuntimeError("injected failure after credential writes")

            patch.setattr(refresh_service, "issue_pair", fail_after_flush)
        response = use_cookie(client, "/auth/refresh", raw)
        assert response.status_code == 500
        assert "set-cookie" not in response.headers
    stored = rows(raw)
    assert len(stored) == 1 and stored[0]["revoked_at"] is None
    assert use_cookie(client, "/auth/refresh", raw).status_code == 200


def test_foreign_origin_cannot_rotate_or_logout(client):
    raw, _ = login(client, account(client))
    for path in ["/auth/refresh", "/auth/logout"]:
        response = client.post(
            path,
            headers={"Cookie": f"{REFRESH_COOKIE}={raw}", "Origin": "https://untrusted.example"},
        )
        assert response.status_code == 403 and "set-cookie" not in response.headers
    assert len(rows(raw)) == 1 and rows(raw)[0]["revoked_at"] is None
    assert use_cookie(client, "/auth/refresh", raw).status_code == 200
