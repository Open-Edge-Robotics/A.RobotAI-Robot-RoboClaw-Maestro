import asyncio
import json

import httpx
import pytest

from robo_claw_maestro.adapters.llm.factory import build_llm_provider
from robo_claw_maestro.adapters.llm.ollama import OllamaProvider
from robo_claw_maestro.adapters.llm.public import (
    AnthropicProvider,
    AzureOpenAIProvider,
    OpenAIProvider,
)
from robo_claw_maestro.config import Settings

pytestmark = pytest.mark.unit


def test_openai_provider_uses_chat_completions_contract():
    async def handler(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer openai-secret"
        body = json.loads(request.content)
        assert body["model"] == "gpt-4.1"
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"tasks":[]}'}}]})

    provider = OpenAIProvider(
        "gpt-4.1",
        "openai-secret",
        transport=httpx.MockTransport(handler),
    )

    assert asyncio.run(provider.complete("system", "goal", 10)) == '{"tasks":[]}'


def test_azure_openai_provider_uses_deployment_and_api_version():
    async def handler(request):
        assert request.url.path == "/openai/deployments/maestro-gpt/chat/completions"
        assert request.url.params["api-version"] == "2024-10-21"
        assert request.headers["api-key"] == "azure-secret"
        return httpx.Response(200, json={"choices": [{"message": {"content": "plan"}}]})

    provider = AzureOpenAIProvider(
        "maestro-gpt",
        "https://example.openai.azure.com",
        "azure-secret",
        transport=httpx.MockTransport(handler),
    )

    assert asyncio.run(provider.complete("system", "goal", 10)) == "plan"


def test_anthropic_provider_combines_text_blocks():
    async def handler(request):
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "anthropic-secret"
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "text", "text": "{"},
                    {"type": "text", "text": '"tasks":[]}'},
                ]
            },
        )

    provider = AnthropicProvider(
        "claude-sonnet-4-6",
        "anthropic-secret",
        transport=httpx.MockTransport(handler),
    )

    assert asyncio.run(provider.complete("system", "goal", 10)) == '{"tasks":[]}'


@pytest.mark.parametrize(
    ("settings", "expected_type"),
    [
        (
            Settings(llm_provider="ollama", llm_model="qwen3", ollama_model="qwen3"),
            OllamaProvider,
        ),
        (
            Settings(llm_provider="openai", llm_model="gpt-4.1", openai_api_key="key"),
            OpenAIProvider,
        ),
        (
            Settings(
                llm_provider="azure_openai",
                llm_model="deployment",
                azure_openai_endpoint="https://example.openai.azure.com",
                azure_openai_api_key="key",
            ),
            AzureOpenAIProvider,
        ),
        (
            Settings(
                llm_provider="anthropic",
                llm_model="claude-sonnet-4-6",
                anthropic_api_key="key",
            ),
            AnthropicProvider,
        ),
    ],
)
def test_provider_factory(settings, expected_type):
    assert isinstance(build_llm_provider(settings), expected_type)


def test_provider_factory_returns_none_when_disabled():
    assert build_llm_provider(Settings()) is None
