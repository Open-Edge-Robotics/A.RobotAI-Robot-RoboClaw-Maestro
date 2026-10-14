"""Public cloud LLM provider adapters using their documented HTTP APIs."""

from __future__ import annotations

from typing import Any

import httpx

from robo_claw_maestro.telemetry.tracing import process_inputs, process_outputs, traceable


class _HttpProvider:
    def __init__(
        self,
        *,
        max_response_bytes: int = 1_000_000,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._max_response_bytes = max_response_bytes
        self._transport = transport

    async def _post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_sec: float,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout_sec, transport=self._transport) as client:
            response = await client.post(url, headers=headers, json=payload, params=params)
            response.raise_for_status()
            if len(response.content) > self._max_response_bytes:
                raise ValueError("LLM response exceeds size limit")
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("LLM provider response must be a JSON object")
            return data


class OpenAIProvider(_HttpProvider):
    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        base_url: str = "https://api.openai.com/v1",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(transport=transport)
        if not model or not api_key:
            raise ValueError("OpenAI model and API key are required")
        self._model = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    @traceable(
        run_type="llm",
        name="OpenAIProvider.complete",
        tags=["component:maestro", "provider:openai"],
        process_inputs=process_inputs,
        process_outputs=process_outputs,
    )
    async def complete(self, system_prompt: str, user_prompt: str, timeout_sec: float) -> str:
        data = await self._post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            payload={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
            },
            timeout_sec=timeout_sec,
        )
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("OpenAI response does not contain message content") from exc


class AzureOpenAIProvider(_HttpProvider):
    def __init__(
        self,
        deployment: str,
        endpoint: str,
        api_key: str,
        *,
        api_version: str = "2024-10-21",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(transport=transport)
        if not deployment or not endpoint or not api_key or not api_version:
            raise ValueError("Azure OpenAI deployment, endpoint, API key, and version are required")
        self._deployment = deployment
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._api_version = api_version

    @traceable(
        run_type="llm",
        name="AzureOpenAIProvider.complete",
        tags=["component:maestro", "provider:azure-openai"],
        process_inputs=process_inputs,
        process_outputs=process_outputs,
    )
    async def complete(self, system_prompt: str, user_prompt: str, timeout_sec: float) -> str:
        data = await self._post(
            f"{self._endpoint}/openai/deployments/{self._deployment}/chat/completions",
            headers={"api-key": self._api_key},
            params={"api-version": self._api_version},
            payload={
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
            },
            timeout_sec=timeout_sec,
        )
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("Azure OpenAI response does not contain message content") from exc


class AnthropicProvider(_HttpProvider):
    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        base_url: str = "https://api.anthropic.com/v1",
        max_tokens: int = 4096,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(transport=transport)
        if not model or not api_key:
            raise ValueError("Anthropic model and API key are required")
        self._model = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._max_tokens = max_tokens

    @traceable(
        run_type="llm",
        name="AnthropicProvider.complete",
        tags=["component:maestro", "provider:anthropic"],
        process_inputs=process_inputs,
        process_outputs=process_outputs,
    )
    async def complete(self, system_prompt: str, user_prompt: str, timeout_sec: float) -> str:
        data = await self._post(
            f"{self._base_url}/messages",
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
            },
            payload={
                "model": self._model,
                "max_tokens": self._max_tokens,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            },
            timeout_sec=timeout_sec,
        )
        try:
            blocks = data["content"]
            return "".join(
                str(block.get("text", ""))
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("Anthropic response does not contain text content") from exc
