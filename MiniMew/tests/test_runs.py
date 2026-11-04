from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from minimew.app import create_app
from minimew.conversation_schemas import CreateRunRequest
from minimew.conversations import title_from_content
from minimew.database import get_session


@pytest.mark.parametrize("content", ["", "  \n\t", "a\x00b", "a" * 20001])
def test_invalid_content(content):
    with pytest.raises(ValidationError):
        CreateRunRequest(content=content, idempotency_key="key")


def test_title_normalization_does_not_change_message_content():
    content = "  如何\n 配置\t知识库  "
    assert title_from_content(content) == "如何 配置 知识库"
    assert CreateRunRequest(content=content, idempotency_key="key").content == content
    assert title_from_content("问" * 30) == "问" * 30
    assert title_from_content("问" * 31) == "问" * 30 + "…"


@pytest.mark.parametrize("key", ["", "a" * 129, "has space", "中文"])
def test_invalid_idempotency_key(key):
    with pytest.raises(ValidationError):
        CreateRunRequest(content="question", idempotency_key=key)


@pytest.mark.parametrize(
    "method,suffix",
    [
        ("POST", "runs"),
        ("GET", "runs/" + str(uuid4())),
        ("GET", "runs/" + str(uuid4()) + "/events"),
        ("GET", "runs/" + str(uuid4()) + "/events/stream"),
        ("GET", "messages"),
    ],
)
def test_conversation_routes_require_identity(monkeypatch, method, suffix):
    monkeypatch.setenv("MINIMEW_DB_PASSWORD", "offline-test")
    session = MagicMock()

    async def offline():
        yield session

    app = create_app()
    app.dependency_overrides[get_session] = offline
    with TestClient(app) as client:
        response = client.request(
            method,
            f"/v1/threads/{uuid4()}/{suffix}",
            json={"content": "test", "idempotency_key": "key"},
        )
    assert response.status_code == 401
    assert session.mock_calls == []
