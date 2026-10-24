from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from langchain.chat_models import init_chat_model

from backend.agent.models import ModelRegistry, ModelRole
from backend.core.settings import ModelSettings
from backend.model_control import ModelRuntimeSpec, build_model_catalog_snapshot


@pytest.mark.parametrize("role", list(ModelRole))
@pytest.mark.parametrize("use_snapshot", [False, True])
def test_all_model_roles_send_thinking_disabled_on_the_wire(role, use_snapshot):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    settings = SimpleNamespace(
        models=ModelSettings(
            _env_file=None,
            ARK_API_KEY="offline-test-key",
            BASE_URL="https://models.example.test/v1",
            MODEL="answer-test",
            FAST_MODEL="fast-test",
            GRADE_MODEL="grader-test",
            EVALUATION_MODEL="evaluator-test",
        )
    )
    snapshot = (
        build_model_catalog_snapshot(
            {
                role: ModelRuntimeSpec(
                    profile_id="snapshot-profile",
                    profile_version=2,
                    display_name="Snapshot model",
                    provider="openai",
                    model_name="snapshot-test",
                    base_url="https://snapshot.example.test/v1",
                    timeout_seconds=10,
                    supports_stream=True,
                    supports_structured_output=True,
                )
            }
        )
        if use_snapshot
        else None
    )
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        registry = ModelRegistry(
            settings=settings,
            initializer=lambda **kwargs: init_chat_model(**kwargs, http_client=client),
        )
        model = registry.get(role, snapshot=snapshot)
        # Tool binding is used by the Agent and must retain the transport policy.
        bound = model.bind_tools(
            [
                {
                    "name": "probe",
                    "description": "Test tool",
                    "parameters": {"type": "object", "properties": {}},
                }
            ],
            tool_choice="none",
        )
        assert bound.invoke("test").content == "ok"

    assert len(requests) == 1
    assert requests[0]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in requests[0]
    assert requests[0]["model"] == (
        "snapshot-test" if use_snapshot else f"{role.value}-test"
    )
    assert model.root_client.max_retries == 0
