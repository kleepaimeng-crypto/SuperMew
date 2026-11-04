import base64
import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from jwt import InvalidTokenError
from pydantic import ValidationError

from minimew.api import auth
from minimew.api.auth import CurrentAccess, CurrentUser
from minimew.app import create_app
from minimew.database import get_session
from minimew.models import User
from minimew.security import hash_password, issue_access_token, verify_access_token, verify_password
from minimew.settings import Settings


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("MINIMEW_DB_PASSWORD", "offline-test-only")
    return Settings()


def test_password_hash_is_salted_and_verifiable():
    password = "test-password-12345"
    first, second = hash_password(password), hash_password(password)
    assert first != second
    assert first.startswith("$argon2id$")
    assert password not in first
    assert verify_password(password, first)
    assert not verify_password("wrong-password", first)


def test_jwt_secret_required_and_minimum_length(settings, monkeypatch):
    for secret in [None, "too-short"]:
        if secret is None:
            monkeypatch.delenv("MINIMEW_JWT_SECRET")
        else:
            monkeypatch.setenv("MINIMEW_JWT_SECRET", secret)
        with pytest.raises(ValidationError):
            Settings()
    assert settings.jwt_secret.get_secret_value() not in repr(settings)


def test_access_token_round_trip(settings):
    user_id = uuid4()
    access = verify_access_token(issue_access_token(user_id, settings), settings)
    assert access.user_id == user_id
    assert access.expires_at.tzinfo is UTC


def test_verified_access_preserves_signed_expiry_and_is_immutable(settings):
    user_id = uuid4()
    now = int(datetime.now(UTC).timestamp())
    expires_at = now + 37
    token = jwt.encode(
        {
            "sub": str(user_id),
            "iat": now - 60,
            "exp": expires_at,
            "iss": "minimew",
            "aud": "minimew-api",
            "token_type": "access",
            "jti": str(uuid4()),
        },
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    access = verify_access_token(token, settings)
    assert access.user_id == user_id
    assert access.expires_at == datetime.fromtimestamp(expires_at, UTC)
    for name, value in [("user_id", uuid4()), ("expires_at", datetime.now(UTC))]:
        with pytest.raises(FrozenInstanceError):
            setattr(access, name, value)


def test_current_access_and_user_share_validation_and_lookup(settings, monkeypatch):
    user = User(id=uuid4(), username="alice", created_at=datetime.now(UTC))
    token = issue_access_token(user.id, settings)
    expected_access = verify_access_token(token, settings)
    verifier = MagicMock(wraps=verify_access_token)
    monkeypatch.setattr(auth, "verify_access_token", verifier)
    session = MagicMock()
    session.get = AsyncMock(return_value=user)
    app = create_app()
    seen_access = []

    async def override():
        yield session

    @app.get("/test-current-access")
    async def both_dependencies(access: CurrentAccess, current_user: CurrentUser):
        assert access.user is current_user
        seen_access.append(access)
        return {"user_id": str(current_user.id), "expires_at": access.expires_at.isoformat()}

    app.dependency_overrides[get_session] = override
    with TestClient(app) as client:
        response = client.get("/test-current-access", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == {
        "user_id": str(user.id),
        "expires_at": expected_access.expires_at.isoformat(),
    }
    verifier.assert_called_once()
    session.get.assert_awaited_once_with(User, user.id)
    assert seen_access[0].user is user
    for name, value in [("user", User()), ("expires_at", datetime.now(UTC))]:
        with pytest.raises(FrozenInstanceError):
            setattr(seen_access[0], name, value)


def test_me_retains_public_user_response(settings):
    user = User(id=uuid4(), username="alice", created_at=datetime.now(UTC))
    token = issue_access_token(user.id, settings)
    session = MagicMock()
    session.get = AsyncMock(return_value=user)
    app = create_app()

    async def override():
        yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as client:
        response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == {
        "id": str(user.id),
        "username": user.username,
        "created_at": user.created_at.isoformat().replace("+00:00", "Z"),
    }
    assert response.headers["cache-control"] == "no-store"
    session.get.assert_awaited_once_with(User, user.id)


def tampered_token(settings):
    parts = issue_access_token(uuid4(), settings).split(".")
    claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
    claims["sub"] = str(uuid4())
    parts[1] = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return ".".join(parts)


@pytest.mark.parametrize(
    "case",
    [
        "tampered",
        "expired",
        "wrong-key",
        "wrong-algorithm",
        "unsigned",
        "missing-exp",
        "wrong-audience",
        "wrong-issuer",
        "wrong-type",
        "invalid-sub",
        "future-iat",
        "float-iat",
        "float-exp",
        "boolean-iat",
        "boolean-exp",
        "numeric-string-iat",
        "numeric-string-exp",
        "non-numeric-iat",
        "non-numeric-exp",
        "out-of-range-exp",
        "malformed",
    ],
)
def test_invalid_tokens_rejected_before_user_lookup(settings, case):
    now = datetime.now(UTC)
    claims = {
        "sub": str(uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=15),
        "iss": "minimew",
        "aud": "minimew-api",
        "token_type": "access",
        "jti": str(uuid4()),
    }
    key, algorithm = settings.jwt_secret.get_secret_value(), "HS256"
    if case == "expired":
        claims.update(iat=now - timedelta(hours=1), exp=now - timedelta(minutes=1))
    elif case == "wrong-key":
        key = "other-test-signing-key-12345678901234567890"
    elif case == "wrong-algorithm":
        algorithm = "HS384"
        key = "test-key-with-at-least-forty-eight-bytes-for-hs384-test"
    elif case == "unsigned":
        key, algorithm = "", "none"
    elif case == "missing-exp":
        del claims["exp"]
    elif case == "wrong-audience":
        claims["aud"] = "other-app"
    elif case == "wrong-issuer":
        claims["iss"] = "other-issuer"
    elif case == "wrong-type":
        claims["token_type"] = "refresh"
    elif case == "invalid-sub":
        claims["sub"] = "not-a-uuid"
    elif case == "future-iat":
        claims["iat"] = now + timedelta(minutes=5)
    elif case == "float-iat":
        claims["iat"] = now.timestamp() - 60
    elif case == "float-exp":
        claims["exp"] = now.timestamp() + 60
    elif case == "boolean-iat":
        claims["iat"] = True
    elif case == "boolean-exp":
        claims["exp"] = True
    elif case == "numeric-string-iat":
        claims["iat"] = str(int(now.timestamp()) - 60)
    elif case == "numeric-string-exp":
        claims["exp"] = str(int(now.timestamp()) + 60)
    elif case == "non-numeric-iat":
        claims["iat"] = "not-a-number"
    elif case == "non-numeric-exp":
        claims["exp"] = "not-a-number"
    elif case == "out-of-range-exp":
        claims["exp"] = 2**63
    token = jwt.encode(claims, key, algorithm=algorithm)
    if case == "tampered":
        token = tampered_token(settings)
    elif case == "malformed":
        token = "invalid-token"
    with pytest.raises(InvalidTokenError):
        verify_access_token(token, settings)
    app = create_app()
    session = MagicMock()
    session.get = AsyncMock()

    async def override():
        yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as client:
        response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    session.get.assert_not_awaited()


def test_missing_user_and_missing_bearer_are_rejected(settings):
    app = create_app()
    session = MagicMock()
    session.get = AsyncMock(return_value=None)

    async def override():
        yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as client:
        assert client.get("/auth/me").status_code == 401
        token = issue_access_token(uuid4(), settings)
        assert (
            client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
        )
    session.get.assert_awaited_once()


def test_validation_never_echoes_secret_input(settings):
    password = "private-password"
    with TestClient(create_app()) as client:
        responses = [
            client.post("/auth/register", json={"username": "bad!", "password": password}),
            client.post("/auth/register", json={"username": "valid", "password": "short"}),
            client.post("/auth/login", content='{"password":"private-password",'),
        ]
    for response in responses:
        assert response.status_code == 422
        assert response.json() == {"detail": "Invalid request"}
