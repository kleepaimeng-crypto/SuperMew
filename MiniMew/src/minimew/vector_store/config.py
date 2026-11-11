from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class VectorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MINIMEW_VECTOR_", frozen=True)

    port: int = Field(default=59530, ge=1, le=65535)
    token: SecretStr = SecretStr("")
    timeout_seconds: float = Field(default=15, gt=0, le=120)
    batch_size: int = Field(default=4, ge=1, le=64)

    @property
    def uri(self) -> str:
        return f"http://127.0.0.1:{self.port}"
