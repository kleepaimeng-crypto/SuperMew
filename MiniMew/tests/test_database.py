import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from minimew.app import create_app
from minimew.database import database_url, get_session
from minimew.settings import Settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    monkeypatch.setenv("MINIMEW_DB_PASSWORD", "offline-secret-@%")
    monkeypatch.setenv("MINIMEW_DB_TIMEOUT_SECONDS", "0.05")


def test_password_required_and_redacted(monkeypatch):
    settings = Settings()
    assert "offline-secret" not in repr(settings)
    assert "offline-secret" not in str(database_url(settings))
    assert database_url(settings).password == "offline-secret-@%"
    monkeypatch.delenv("MINIMEW_DB_PASSWORD")
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize(
    "failure",
    [
        None,
        OSError("private host secret"),
        OperationalError("SELECT 1", {}, Exception("private password")),
        TimeoutError(),
    ],
)
def test_readiness_maps_database_failures_without_leaking(failure):
    app = create_app()
    session = AsyncMock()
    session.execute.side_effect = failure

    async def override():
        yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as client:
        response = client.get("/health/ready")
        assert client.get("/health/live").status_code == 200
    assert response.status_code == (503 if failure else 200)
    status = "unavailable" if failure else "ok"
    assert response.json() == {"status": status, "database": status}


def test_readiness_has_deadline():
    app = create_app()
    session = AsyncMock()

    async def slow_execute(*args):
        await asyncio.sleep(10)

    session.execute.side_effect = slow_execute

    async def override():
        yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 503


def test_sessions_are_request_local_and_closed():
    app = create_app()
    from minimew.database import SessionDependency

    seen = []

    @app.get("/test/session")
    async def session_probe(session: SessionDependency):
        seen.append(session)
        return {"ok": True}

    with TestClient(app) as client:
        factory = app.state.session_factory
        sessions = []

        def tracked_factory():
            session = factory()
            session.close = AsyncMock(wraps=session.close)
            sessions.append(session)
            return session

        app.state.session_factory = tracked_factory
        client.get("/test/session")
        client.get("/test/session")
    assert len(seen) == 2
    assert seen[0] is not seen[1]
    for session in sessions:
        session.close.assert_awaited_once()
