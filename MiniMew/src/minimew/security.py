"""密码哈希与短期 Access Token；不记录 credential，不提供不验签解析路径。"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from jwt import InvalidTokenError

from minimew.settings import Settings

ISSUER = "minimew"
AUDIENCE = "minimew-api"


@dataclass(frozen=True, slots=True)
class VerifiedAccess:
    user_id: UUID
    expires_at: datetime


def hash_password(password: str) -> str:
    return PasswordHasher().hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return PasswordHasher().verify(password_hash, password)
    except VerifyMismatchError:
        return False


def issue_access_token(user_id: UUID, settings: Settings) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": now,
            "exp": now + timedelta(minutes=settings.access_token_minutes),
            "iss": ISSUER,
            "aud": AUDIENCE,
            "token_type": "access",
            "jti": str(uuid4()),
        },
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def verify_access_token(token: str, settings: Settings) -> VerifiedAccess:
    if len(token) > 8192:
        raise InvalidTokenError("Invalid access token")
    claims = jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=["HS256"],
        issuer=ISSUER,
        audience=AUDIENCE,
        options={"require": ["sub", "iat", "exp", "iss", "aud", "token_type", "jti"]},
    )
    if (
        claims["token_type"] != "access"
        or type(claims["iat"]) is not int
        or type(claims["exp"]) is not int
        or claims["exp"] <= claims["iat"]
    ):
        raise InvalidTokenError("Invalid access token claims")
    try:
        user_id = UUID(claims["sub"])
    except (ValueError, TypeError, AttributeError) as error:
        raise InvalidTokenError("Invalid subject") from error
    try:
        expires_at = datetime.fromtimestamp(claims["exp"], UTC)
    except (ValueError, OverflowError, OSError) as error:
        raise InvalidTokenError("Invalid access token expiry") from error
    return VerifiedAccess(user_id=user_id, expires_at=expires_at)
