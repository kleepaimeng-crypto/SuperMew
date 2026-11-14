from contextlib import asynccontextmanager

import pytest


@pytest.fixture(autouse=True)
def test_jwt_settings(monkeypatch):
    monkeypatch.setenv("MINIMEW_JWT_SECRET", "test-only-signing-key-not-for-production-12345")
    monkeypatch.setenv("MINIMEW_REDIS_PASSWORD", "test-only-redis-not-for-production")
    monkeypatch.setenv("MINIMEW_DEEPSEEK_API_KEY", "test-only-never-call-network")
    monkeypatch.setenv("MINIMEW_KNOWLEDGE_ENABLED", "false")
    monkeypatch.setenv("MINIMEW_REASONING_ENABLED", "false")  # legacy v2 regression fixtures
    monkeypatch.setenv("MINIMEW_REPAIRS_ENABLED", "false")  # v1-v3 behavior remains explicit
    # Existing document fixtures are frozen Dense v1; Hybrid tests opt into the new layout.
    monkeypatch.setenv("MINIMEW_DOCUMENT_INDEX_LAYOUT", "dense-cosine-v1")

    # 旧离线/PG 用例不隐式联网。真实通知验收在专用 fixture 显式恢复资源工厂。
    @asynccontextmanager
    async def offline_notifications(sessions, settings):
        yield None

    monkeypatch.setattr("minimew.app.notification_lifespan", offline_notifications)
    # 正式自动执行由专用 fixture 恢复并注入受控 Provider，旧测试不会启动模型。
    monkeypatch.setattr("minimew.app.execution_lifespan", offline_notifications)


@pytest.fixture
def model_configuration():
    from minimew.model_control import ModelConfiguration

    # 显式测试配置；不会请求此地址，也不写入开发库。
    return ModelConfiguration(
        provider="openai_compatible",
        model_name="test-model-v1",
        base_url="https://models.example.invalid/v1",
        temperature=0.2,
        max_output_tokens=1024,
        timeout_seconds=30.0,
        thinking={"type": "disabled"},
    )
