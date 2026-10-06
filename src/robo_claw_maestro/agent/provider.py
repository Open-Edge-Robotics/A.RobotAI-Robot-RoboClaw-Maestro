"""LLM provider port and deterministic test adapter."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol


class LlmProvider(Protocol):
    async def complete(self, system_prompt: str, user_prompt: str, timeout_sec: float) -> str: ...


class FakeLlmProvider:
    def __init__(self, responses: Iterable[str]) -> None:
        self._responses = iter(responses)
        self.requests: list[tuple[str, str, float]] = []

    async def complete(self, system_prompt: str, user_prompt: str, timeout_sec: float) -> str:
        self.requests.append((system_prompt, user_prompt, timeout_sec))
        try:
            return next(self._responses)
        except StopIteration as exc:
            raise RuntimeError("fake LLM has no response") from exc
