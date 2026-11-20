from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from minimew.account_models.catalog import preset
from minimew.model_protocol import ModelConfiguration


class AccountModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ModelWrite(AccountModel):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[^\x00]+$")
    adapter: Literal["deepseek", "kimi", "qwen", "generic"]
    model_name: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
    base_url: str | None = Field(default=None, max_length=2048)
    api_key: SecretStr | None = Field(default=None, max_length=4096)
    max_output_tokens: int = Field(default=4096, ge=1, le=65536)
    timeout_seconds: float = Field(default=120, gt=0, le=300)
    temperature: float = Field(default=0.2, ge=0, le=2)

    def configuration(self):
        item = preset(self.adapter)
        if item and self.model_name not in item["models"]:
            raise ValueError("Use generic configuration for custom model IDs")
        from minimew.account_models.network import public_url

        address = public_url(self.base_url or (item["base_url"] if item else ""))
        if item and self.adapter != "qwen" and address != item["base_url"]:
            raise ValueError("Preset endpoint cannot be changed")
        if item and self.adapter == "qwen":
            from urllib.parse import urlsplit

            host = urlsplit(address).hostname or ""
            if not (
                host in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"}
                or host.endswith(".maas.aliyuncs.com")
            ):
                raise ValueError("Qwen endpoint required")
        return ModelConfiguration(
            provider="openai_compatible",
            adapter=self.adapter,
            model_name=self.model_name,
            base_url=address,
            temperature=1.0 if self.adapter == "kimi" else self.temperature,
            max_output_tokens=self.max_output_tokens,
            timeout_seconds=self.timeout_seconds,
            thinking={"type": "native" if self.adapter in {"kimi", "generic"} else "disabled"},
        )

    @model_validator(mode="after")
    def valid_configuration(self):
        self.configuration()
        if self.api_key is not None:
            value = self.api_key.get_secret_value()
            if value and (not value.strip() or any(ord(c) < 33 or ord(c) > 126 for c in value)):
                raise ValueError("Invalid API Key")
        return self


class ModelView(AccountModel):
    id: UUID
    name: str
    revision: int
    adapter: str
    model_name: str
    base_url: str
    max_output_tokens: int
    timeout_seconds: float
    temperature: float
    key_configured: bool


class SelectionWrite(AccountModel):
    model_id: UUID | None
    knowledge_enabled: bool = True


class AccountSettingsView(AccountModel):
    models: list[ModelView]
    current_model_id: UUID | None
    knowledge_enabled: bool
    knowledge_available: bool
    credential_storage_ready: bool
    default_model_name: str
    default_configured: bool


class PresetView(AccountModel):
    id: str
    name: str
    base_url: str
    models: list[str]
    documentation: str
