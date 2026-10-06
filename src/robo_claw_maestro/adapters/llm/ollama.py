"""Ollama HTTP adapter for the LLM provider port."""

from __future__ import annotations

import httpx

from robo_claw_maestro.telemetry.tracing import process_inputs, process_outputs, traceable


class OllamaProvider:
    def __init__(
        self,
        model: str,
        *,
        base_url: str = "http://127.0.0.1:11434",
        max_response_bytes: int = 1_000_000,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._max_response_bytes = max_response_bytes

    @traceable(
        run_type="llm",
        name="OllamaProvider.complete",
        tags=["component:maestro", "provider:ollama"],
        process_inputs=process_inputs,
        process_outputs=process_outputs,
    )
    async def complete(self, system_prompt: str, user_prompt: str, timeout_sec: float) -> str:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            response = await client.post(
                f"{self._base_url}/api/chat",
                json={
                    "model": self._model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                },
            )
            response.raise_for_status()
            if len(response.content) > self._max_response_bytes:
                raise ValueError("LLM response exceeds size limit")
            data = response.json()
            return str(data.get("message", {}).get("content", ""))
