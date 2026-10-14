"""Construct the configured Maestro LLM provider."""

from __future__ import annotations

from robo_claw_maestro.agent.provider import LlmProvider
from robo_claw_maestro.config import Settings

from .ollama import OllamaProvider
from .public import AnthropicProvider, AzureOpenAIProvider, OpenAIProvider


def build_llm_provider(settings: Settings) -> LlmProvider | None:
    provider = settings.llm_provider
    if not provider:
        return None
    if provider == "ollama":
        return OllamaProvider(settings.llm_model, base_url=settings.ollama_base_url)
    if provider == "openai":
        return OpenAIProvider(
            settings.llm_model,
            settings.openai_api_key,
            base_url=settings.openai_base_url,
        )
    if provider == "azure_openai":
        return AzureOpenAIProvider(
            settings.llm_model,
            settings.azure_openai_endpoint,
            settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
        )
    if provider == "anthropic":
        return AnthropicProvider(
            settings.llm_model,
            settings.anthropic_api_key,
            base_url=settings.anthropic_base_url,
        )
    raise ValueError(f"unsupported MAESTRO_LLM_PROVIDER: {provider}")
