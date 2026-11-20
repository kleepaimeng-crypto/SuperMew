"""认证加密绑定账户、凭据ID及端点；公开接口不返回密文或原始Key。"""

import json
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from minimew.model_control import ModelConfigurationUnavailable


def cipher(settings):
    try:
        return Fernet(settings.credential_master_key.get_secret_value().encode("ascii"))
    except (ValueError, TypeError, UnicodeError):
        raise ModelConfigurationUnavailable from None


def encrypt(settings, owner, identity, base_url, value):
    payload = json.dumps(
        dict(owner=str(owner), identity=str(identity), base_url=base_url, key=value)
    )
    return cipher(settings).encrypt(payload.encode()).decode()


def decrypt(settings, record, base_url):
    try:
        body = json.loads(cipher(settings).decrypt(record.encrypted_value.encode()))
        if (body["owner"], body["identity"], body["base_url"]) != (
            str(record.user_id),
            str(record.id),
            base_url,
        ):
            raise ValueError("Credential binding mismatch")
        return SecretStr(body["key"])
    except (InvalidToken, ValueError, TypeError, KeyError, UnicodeError):
        raise ModelConfigurationUnavailable from None


class CredentialResolver:
    def __init__(self, sessions, settings):
        self.sessions, self.settings = sessions, settings

    async def __call__(self, owner: UUID, identity: UUID, base_url: str):
        from minimew.models import ModelCredential

        try:
            async with self.sessions() as session:
                item = await session.scalar(
                    select(ModelCredential).where(
                        ModelCredential.id == identity,
                        ModelCredential.user_id == owner,
                        ModelCredential.revoked_at.is_(None),
                    )
                )
                if item is None:
                    raise ModelConfigurationUnavailable
                return decrypt(self.settings, item, base_url)
        except SQLAlchemyError:
            from minimew.execution.control import ExecutionStorageUnavailable

            raise ExecutionStorageUnavailable from None
