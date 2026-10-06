import asyncio
from pathlib import Path

import pytest

from robo_claw_maestro.telemetry.tracing import (
    current_trace_id,
    init_tracing,
    is_enabled,
    process_inputs,
    process_outputs,
    redact,
    traceable,
)

pytestmark = pytest.mark.unit


def test_disabled_tracing_preserves_sync_result_identity(monkeypatch):
    monkeypatch.setenv("MAESTRO_LANGSMITH_TRACING", "false")
    expected = {"ok": True}
    calls = []

    @traceable(run_type="chain", name="disabled_sync")
    def operation(value):
        calls.append(value)
        return expected

    assert operation(1) is expected
    assert calls == [1]
    assert not is_enabled()
    assert current_trace_id() == ""


def test_disabled_tracing_preserves_async_result_and_exception(monkeypatch):
    monkeypatch.setenv("MAESTRO_LANGSMITH_TRACING", "false")

    @traceable(run_type="chain", name="disabled_async")
    async def operation(fail=False):
        if fail:
            raise RuntimeError("original failure")
        return "ok"

    assert asyncio.run(operation()) == "ok"
    with pytest.raises(RuntimeError, match="original failure"):
        asyncio.run(operation(fail=True))


def test_enabled_tracing_uses_langsmith_decorator_without_changing_result(monkeypatch):
    import langsmith

    monkeypatch.setenv("MAESTRO_LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    traced_calls = []

    def fake_traceable(*decorator_args, **decorator_kwargs):
        def decorate(function):
            def wrapped(*args, **kwargs):
                traced_calls.append((decorator_args, decorator_kwargs))
                return function(*args, **kwargs)

            return wrapped

        return decorate

    monkeypatch.setattr(langsmith, "traceable", fake_traceable)

    @traceable(run_type="chain", name="enabled_test")
    def operation():
        return {"ok": True}

    assert operation() == {"ok": True}
    assert traced_calls[0][1]["name"] == "enabled_test"


def test_redaction_hides_credentials_binary_and_goal_text():
    value = redact(
        {
            "authorization": "Bearer secret",
            "image_base64": "abc",
            "nested": {"token": "secret", "safe": "value"},
            "binary": b"123",
        }
    )
    inputs = process_inputs({"goal": "inspect secret room", "mission_id": "m1"})

    assert value == {
        "authorization": "<REDACTED>",
        "image_base64": "<REDACTED>",
        "nested": {"token": "<REDACTED>", "safe": "value"},
        "binary": "<BYTES:3>",
    }
    assert inputs["goal"]["length"] == len("inspect secret room")
    assert "inspect secret room" not in str(inputs)


def test_content_capture_is_opt_in(monkeypatch):
    monkeypatch.setenv("MAESTRO_LANGSMITH_CAPTURE_CONTENT", "false")
    hidden = process_outputs("sensitive provider response")
    assert hidden["length"] == len("sensitive provider response")
    assert "sensitive" not in str(hidden)

    monkeypatch.setenv("MAESTRO_LANGSMITH_CAPTURE_CONTENT", "true")
    assert process_outputs("visible development response") == "visible development response"


def test_missing_key_keeps_requested_tracing_inactive(monkeypatch):
    monkeypatch.setenv("MAESTRO_LANGSMITH_TRACING", "true")
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)

    assert not is_enabled()
    assert "API_KEY is missing" in init_tracing()


def test_domain_layer_does_not_import_langsmith():
    domain = Path(__file__).parents[2] / "src" / "robo_claw_maestro" / "domain"
    for path in domain.glob("*.py"):
        assert "langsmith" not in path.read_text(encoding="utf-8").lower()
