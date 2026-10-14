import pytest

from robo_claw_maestro.config import Settings

pytestmark = pytest.mark.unit


def test_settings_parse_static_robots(monkeypatch):
    monkeypatch.setenv(
        "MAESTRO_ROBOTS_JSON",
        '[{"robot_id":"r1","endpoint":"127.0.0.1:50051"}]',
    )
    monkeypatch.setenv("MAESTRO_PORT", "9000")

    settings = Settings.from_env()

    assert settings.port == 9000
    assert settings.robots[0]["robot_id"] == "r1"


def test_settings_parse_azure_openai_provider(monkeypatch):
    monkeypatch.setenv("MAESTRO_LLM_PROVIDER", "azureopenai")
    monkeypatch.setenv("MAESTRO_LLM_MODEL", "maestro-deployment")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "secret")

    settings = Settings.from_env()

    assert settings.llm_provider == "azure_openai"
    assert settings.llm_model == "maestro-deployment"
    assert settings.azure_openai_api_version == "2024-10-21"


def test_legacy_ollama_model_enables_ollama_provider(monkeypatch):
    monkeypatch.delenv("MAESTRO_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("MAESTRO_OLLAMA_MODEL", "qwen3")

    settings = Settings.from_env()

    assert settings.llm_provider == "ollama"
    assert settings.llm_model == "qwen3"


def test_settings_reject_provider_without_model(monkeypatch):
    monkeypatch.setenv("MAESTRO_LLM_PROVIDER", "openai")
    monkeypatch.delenv("MAESTRO_LLM_MODEL", raising=False)
    monkeypatch.delenv("MAESTRO_OLLAMA_MODEL", raising=False)

    with pytest.raises(ValueError, match="MAESTRO_LLM_MODEL"):
        Settings.from_env()


def test_settings_reject_invalid_robot_json(monkeypatch):
    monkeypatch.setenv("MAESTRO_ROBOTS_JSON", "not-json")

    with pytest.raises(ValueError, match="valid JSON"):
        Settings.from_env()
