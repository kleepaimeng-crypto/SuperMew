"""离线验证 Thread 接口的认证入口。"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from minimew.app import create_app
from minimew.database import get_session


@pytest.mark.parametrize("method,path", [("POST", ""), ("GET", ""), ("GET", f"/{uuid4()}")])
@pytest.mark.parametrize("token", [None, "invalid-token"])
def test_threads_require_valid_identity_before_database_access(monkeypatch, method, path, token):
    monkeypatch.setenv("MINIMEW_DB_PASSWORD", "offline-test-only")
    session = MagicMock()

    async def offline_session():
        yield session

    app = create_app()
    app.dependency_overrides[get_session] = offline_session
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with TestClient(app) as client:
        response = client.request(method, "/v1/threads" + path, json={}, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert session.mock_calls == []
