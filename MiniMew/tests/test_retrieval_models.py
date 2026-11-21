import pytest
from pydantic import ValidationError

from minimew.retrieval_models import CloudConfiguration, CredentialWrite


@pytest.mark.parametrize(
    "changes",
    [
        {"model_name": "other"},
        {"provider": "other"},
        {"base_url": "https://127.0.0.1/compatible-mode/v1"},
        {"base_url": "https://other.example.com/compatible-mode/v1"},
        {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1?key=secret"},
    ],
)
def test_only_declared_protocol_and_provider_endpoints_are_accepted(changes):
    with pytest.raises(ValidationError):
        CloudConfiguration(
            **{
                **dict(
                    purpose="embedding",
                    model_name="text-embedding-v4",
                    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                ),
                **changes,
            }
        )


@pytest.mark.parametrize("key", ["", " ", "secret\nheader", "密钥"])
def test_credential_update_accepts_only_key_without_model_changes(key):
    with pytest.raises(ValidationError):
        CredentialWrite(api_key=key)
    with pytest.raises(ValidationError):
        CredentialWrite(api_key="valid-key", base_url="https://different.example.com")
