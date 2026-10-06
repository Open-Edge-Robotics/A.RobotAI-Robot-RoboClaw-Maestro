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


def test_settings_reject_invalid_robot_json(monkeypatch):
    monkeypatch.setenv("MAESTRO_ROBOTS_JSON", "not-json")

    with pytest.raises(ValueError, match="valid JSON"):
        Settings.from_env()
