import secrets
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.responses import Response

from minimew.app import create_app
from minimew.auth_http import clear_refresh_cookie, set_refresh_cookie
from minimew.refresh import TokenPair, refresh_digest
from minimew.settings import Settings


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("MINIMEW_DB_PASSWORD", "offline-only")
    monkeypatch.setenv("MINIMEW_REFRESH_COOKIE_SECURE", "false")
    return Settings()


def test_refresh_digest_and_cookie_flags(settings):
    raw = secrets.token_urlsafe(32)
    digest = refresh_digest(raw)
    assert len(digest) == 64 and digest != raw
    assert refresh_digest(raw) == digest
    assert all(refresh_digest(value) is None for value in [None, "", "a" * 44, "bad!", "é" * 43])
    pair = TokenPair("access-test", raw, datetime.now(UTC) + timedelta(days=7))
    assert raw not in repr(pair) and "access-test" not in repr(pair)
    secure = settings.model_copy(update={"refresh_cookie_secure": True})
    response = Response()
    set_refresh_cookie(response, pair, secure)
    assert all(
        flag in response.headers["set-cookie"]
        for flag in ["HttpOnly", "Secure", "Path=/auth", "SameSite=lax", "Max-Age=604800"]
    )
    cleared = Response()
    clear_refresh_cookie(cleared, secure)
    assert all(
        flag in cleared.headers["set-cookie"]
        for flag in ["HttpOnly", "Secure", "Path=/auth", "SameSite=lax", "Max-Age=0"]
    )


def test_production_requires_secure_cookie(settings, monkeypatch):
    monkeypatch.setenv("MINIMEW_ENVIRONMENT", "production")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("MINIMEW_REFRESH_COOKIE_SECURE", "true")
    assert Settings().refresh_cookie_secure


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://other.example"},
        {"Origin": "null"},
        {"Referer": "https://other.example/page"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Origin": "http://testserver:81"},
        {"Origin": "http://user@testserver"},
    ],
)
def test_foreign_auth_posts_rejected_before_database(settings, headers):
    with TestClient(create_app()) as client:
        for path in ["/auth/login", "/auth/register", "/auth/refresh", "/auth/logout"]:
            response = client.post(path, headers=headers)
            assert response.status_code == 403
            assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "headers", [{}, {"Origin": "http://testserver"}, {"Referer": "http://testserver/docs"}]
)
def test_missing_or_malformed_refresh_clears_cookie_without_database(settings, headers):
    with TestClient(create_app()) as client:
        response = client.post("/auth/refresh", headers=headers)
        assert response.status_code == 401
        assert "Max-Age=0" in response.headers["set-cookie"]
        assert response.headers["cache-control"] == "no-store"
        assert client.post("/auth/logout", headers=headers).status_code == 204
