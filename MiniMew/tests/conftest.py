from contextlib import asynccontextmanager

import pytest


@pytest.fixture(autouse=True)
def test_jwt_settings(monkeypatch, tmp_path):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("MINIMEW_CREDENTIAL_MASTER_KEY", Fernet.generate_key().decode())
    # 新部署默认来自环境；保持原测试模型/预算，旧Profile管理另有事务测试。
    monkeypatch.setenv("MINIMEW_DEFAULT_MODEL_NAME", "test-model-v1")
    monkeypatch.setenv("MINIMEW_DEFAULT_MODEL_BASE_URL", "https://models.example.invalid/v1")
    monkeypatch.setenv("MINIMEW_DEFAULT_MODEL_ADAPTER", "generic")
    monkeypatch.setenv("MINIMEW_DEFAULT_MODEL_MAX_TOKENS", "1024")
    monkeypatch.setenv("MINIMEW_DEFAULT_MODEL_TIMEOUT", "30")
    monkeypatch.setenv("MINIMEW_DIAGNOSTIC_LOG_DIR", str(tmp_path / "logs"))
    # 原生命周期用例保持原事件断言；过程专项显式恢复正式报告器。
    monkeypatch.setattr("minimew.execution.service.ProcessReporter", lambda *args: None)
    monkeypatch.setenv("MINIMEW_JWT_SECRET", "test-only-signing-key-not-for-production-12345")
    monkeypatch.setenv("MINIMEW_REDIS_PASSWORD", "test-only-redis-not-for-production")
    monkeypatch.setenv("MINIMEW_DEEPSEEK_API_KEY", "test-only-never-call-network")
    monkeypatch.setenv("MINIMEW_KNOWLEDGE_ENABLED", "false")
    monkeypatch.setenv("MINIMEW_REASONING_ENABLED", "false")  # legacy v2 regression fixtures
    monkeypatch.setenv("MINIMEW_REPAIRS_ENABLED", "false")  # v1-v3 behavior remains explicit
    monkeypatch.setenv("MINIMEW_HITL_ENABLED", "false")  # HITL tests explicitly freeze v5
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
