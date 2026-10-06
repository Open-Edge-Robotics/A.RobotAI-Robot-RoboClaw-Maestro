"""Optional LangSmith tracing with safe no-op behavior when disabled."""

from __future__ import annotations

import functools
import hashlib
import os
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])
_TRUE = {"1", "true", "yes", "on"}
_USER_TEXT_KEYS = {
    "goal",
    "instruction",
    "message",
    "result_message",
    "summary",
    "user_prompt",
}
_SENSITIVE = (
    "api_key",
    "authorization",
    "base64",
    "certificate",
    "credential",
    "file_content",
    "image",
    "map_data",
    "password",
    "private_key",
    "secret",
    "token",
)


def is_tracing_requested() -> bool:
    return os.getenv("MAESTRO_LANGSMITH_TRACING", "false").strip().lower() in _TRUE


def _package_available() -> bool:
    try:
        import langsmith  # noqa: F401

        return True
    except Exception:
        return False


def is_enabled() -> bool:
    return is_tracing_requested() and _package_available() and bool(os.getenv("LANGSMITH_API_KEY"))


def redact(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return "<MAX_DEPTH>"
    if isinstance(value, dict):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 100:
                result["<truncated>"] = f"{len(value) - 100} more items"
                break
            name = str(key)
            lowered = name.lower()
            if any(part in lowered for part in _SENSITIVE):
                result[name] = "<REDACTED>"
            elif (
                lowered in _USER_TEXT_KEYS
                and isinstance(item, str)
                and not capture_content_enabled()
            ):
                result[name] = {"hash": stable_hash(item), "length": len(item)}
            else:
                result[name] = redact(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        result = [redact(item, depth=depth + 1) for item in items[:100]]
        if len(items) > 100:
            result.append(f"<{len(items) - 100} more items>")
        return result
    if isinstance(value, bytes):
        return f"<BYTES:{len(value)}>"
    if is_dataclass(value) and not isinstance(value, type):
        return redact(asdict(value), depth=depth + 1)
    if isinstance(value, str):
        return value if len(value) <= 8192 else value[:8192] + f"<TRUNCATED:{len(value)}>"
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return f"<{type(value).__name__}>"


def process_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    safe = {
        key: value
        for key, value in inputs.items()
        if key not in {"self", "call_next", "provider", "gateway"}
    }
    for key in ("goal", "instruction", "user_prompt"):
        value = safe.get(key)
        if isinstance(value, str) and not capture_content_enabled():
            safe[key] = {"hash": stable_hash(value), "length": len(value)}
    return redact(safe)


def process_outputs(outputs: Any) -> Any:
    if isinstance(outputs, str) and not capture_content_enabled():
        return {"hash": stable_hash(outputs), "length": len(outputs)}
    return redact(outputs)


def traceable(*args: Any, **kwargs: Any) -> Callable[[F], F]:
    """Lazy LangSmith decorator; disabled calls execute the original function directly."""

    def decorator(func: F) -> F:
        cache: dict[str, Callable[..., Any]] = {}

        @functools.wraps(func)
        def wrapper(*func_args: Any, **func_kwargs: Any) -> Any:
            if not is_enabled():
                return func(*func_args, **func_kwargs)
            traced = cache.get("function")
            if traced is None:
                try:
                    from langsmith import traceable as langsmith_traceable

                    traced = langsmith_traceable(*args, **kwargs)(func)
                except Exception:
                    traced = func
                cache["function"] = traced
            return traced(*func_args, **func_kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def set_current_metadata(metadata: dict[str, Any], tags: list[str] | None = None) -> None:
    if not is_enabled():
        return
    try:
        from langsmith.run_helpers import get_current_run_tree

        run = get_current_run_tree()
        if run is None:
            return
        run.extra.setdefault("metadata", {}).update(redact(metadata))
        if tags:
            run.tags = list(dict.fromkeys([*(run.tags or []), *tags]))
    except Exception:
        return


def current_trace_id() -> str:
    if not is_enabled():
        return ""
    try:
        from langsmith.run_helpers import get_current_run_tree

        run = get_current_run_tree()
        return str(getattr(run, "trace_id", "") or getattr(run, "id", "")) if run else ""
    except Exception:
        return ""


def capture_content_enabled() -> bool:
    return os.getenv("MAESTRO_LANGSMITH_CAPTURE_CONTENT", "false").strip().lower() in _TRUE


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def init_tracing() -> str:
    if not is_tracing_requested():
        return "Maestro LangSmith tracing disabled"
    if not _package_available():
        return "Maestro LangSmith requested but langsmith package is unavailable"
    if not os.getenv("LANGSMITH_API_KEY"):
        return "Maestro LangSmith requested but LANGSMITH_API_KEY is missing"
    os.environ.setdefault("LANGSMITH_PROJECT", "robo-claw-maestro")
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    return f"Maestro LangSmith tracing enabled (project={os.environ['LANGSMITH_PROJECT']})"
