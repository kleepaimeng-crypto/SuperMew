import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from minimew.app import create_app


@pytest.fixture(autouse=True)
def clean_settings_env(monkeypatch):
    monkeypatch.delenv("MINIMEW_APP_NAME", raising=False)
    monkeypatch.delenv("MINIMEW_ENVIRONMENT", raising=False)
    monkeypatch.setenv("MINIMEW_DB_PASSWORD", "offline-test-only")


def test_liveness_without_external_services():
    with TestClient(create_app()) as client:
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app_name": "MiniMew"}


def test_configuration_is_loaded_once_per_app(monkeypatch):
    monkeypatch.setenv("MINIMEW_APP_NAME", "First")
    monkeypatch.setenv("MINIMEW_ENVIRONMENT", "test")
    first = create_app()
    monkeypatch.setenv("MINIMEW_APP_NAME", "Second")
    second = create_app()
    with TestClient(first) as first_client, TestClient(second) as second_client:
        assert first_client.get("/health/live").json()["app_name"] == "First"
        assert second_client.get("/health/live").json()["app_name"] == "Second"
    assert first.state.settings.environment == "test"


@pytest.mark.parametrize(
    ("variable", "value", "field"),
    [("MINIMEW_ENVIRONMENT", "typo", "environment"), ("MINIMEW_APP_NAME", "", "app_name")],
)
def test_invalid_configuration_prevents_app_creation(monkeypatch, variable, value, field):
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValidationError) as error:
        create_app()
    assert error.value.errors()[0]["loc"] == (field,)


def test_dotenv_is_not_implicitly_loaded(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("MINIMEW_APP_NAME=UnrelatedProject\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert create_app().state.settings.app_name == "MiniMew"
