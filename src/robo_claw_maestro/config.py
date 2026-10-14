"""Environment based Maestro configuration."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = "sqlite:///maestro.db"
    host: str = "0.0.0.0"
    port: int = 8000
    heartbeat_timeout_sec: float = 15.0
    read_token: str = ""
    control_token: str = ""
    admin_token: str = ""
    allowed_skills: frozenset[str] | None = None
    llm_provider: str = ""
    llm_model: str = ""
    llm_timeout_sec: float = 30.0
    ollama_model: str = ""
    ollama_base_url: str = "http://127.0.0.1:11434"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = "2024-10-21"
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    fleet_control_enabled: bool = False
    fleet_control_host: str = "0.0.0.0"
    fleet_control_port: int = 50053
    fleet_tls_certificate: str = ""
    fleet_tls_private_key: str = ""
    fleet_tls_client_ca: str = ""
    control_rate_limit_per_minute: int = 120
    log_level: str = "INFO"
    event_retention_days: int = 30
    auto_create_schema: bool = True
    robots: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_env(cls) -> Settings:
        database_url = os.getenv("MAESTRO_DATABASE_URL", "sqlite:///maestro.db")
        raw_auto_create = os.getenv("MAESTRO_AUTO_CREATE_SCHEMA")
        auto_create_schema = (
            database_url.startswith("sqlite")
            if raw_auto_create is None
            else raw_auto_create.lower() in {"1", "true", "yes"}
        )
        raw_allowed_skills = os.getenv("MAESTRO_ALLOWED_SKILLS_JSON", "").strip()
        allowed_skills = None
        if raw_allowed_skills:
            try:
                parsed_skills = json.loads(raw_allowed_skills)
            except json.JSONDecodeError as exc:
                raise ValueError("MAESTRO_ALLOWED_SKILLS_JSON must be valid JSON") from exc
            if not isinstance(parsed_skills, list) or not all(
                isinstance(item, str) for item in parsed_skills
            ):
                raise ValueError("MAESTRO_ALLOWED_SKILLS_JSON must be a string array")
            allowed_skills = frozenset(parsed_skills)
        raw_robots = os.getenv("MAESTRO_ROBOTS_JSON", "[]")
        try:
            robots = json.loads(raw_robots)
        except json.JSONDecodeError as exc:
            raise ValueError("MAESTRO_ROBOTS_JSON must be valid JSON") from exc
        if not isinstance(robots, list) or not all(isinstance(item, dict) for item in robots):
            raise ValueError("MAESTRO_ROBOTS_JSON must be a JSON array of objects")
        timeout = float(os.getenv("MAESTRO_HEARTBEAT_TIMEOUT_SEC", "15"))
        llm_timeout = float(os.getenv("MAESTRO_LLM_TIMEOUT_SEC", "30"))
        if llm_timeout <= 0:
            raise ValueError("MAESTRO_LLM_TIMEOUT_SEC must be positive")
        legacy_ollama_model = os.getenv("MAESTRO_OLLAMA_MODEL", "").strip()
        llm_provider = os.getenv("MAESTRO_LLM_PROVIDER", "").strip().lower()
        aliases = {"azure": "azure_openai", "azureopenai": "azure_openai"}
        llm_provider = aliases.get(llm_provider, llm_provider)
        if not llm_provider and legacy_ollama_model:
            llm_provider = "ollama"
        if llm_provider not in {"", "ollama", "openai", "azure_openai", "anthropic"}:
            raise ValueError(f"unsupported MAESTRO_LLM_PROVIDER: {llm_provider}")
        llm_model = os.getenv("MAESTRO_LLM_MODEL", "").strip() or legacy_ollama_model
        if llm_provider and not llm_model:
            raise ValueError("MAESTRO_LLM_MODEL is required when a provider is configured")
        port = int(os.getenv("MAESTRO_PORT", "8000"))
        if timeout <= 0:
            raise ValueError("MAESTRO_HEARTBEAT_TIMEOUT_SEC must be positive")
        if not 1 <= port <= 65535:
            raise ValueError("MAESTRO_PORT must be between 1 and 65535")
        fleet_port = int(os.getenv("MAESTRO_FLEET_CONTROL_PORT", "50053"))
        if not 1 <= fleet_port <= 65535:
            raise ValueError("MAESTRO_FLEET_CONTROL_PORT must be between 1 and 65535")
        rate_limit = int(os.getenv("MAESTRO_CONTROL_RATE_LIMIT_PER_MINUTE", "120"))
        if rate_limit < 1:
            raise ValueError("MAESTRO_CONTROL_RATE_LIMIT_PER_MINUTE must be positive")
        retention_days = int(os.getenv("MAESTRO_EVENT_RETENTION_DAYS", "30"))
        if retention_days < 1:
            raise ValueError("MAESTRO_EVENT_RETENTION_DAYS must be positive")
        return cls(
            database_url=database_url,
            host=os.getenv("MAESTRO_HOST", "0.0.0.0"),
            port=port,
            heartbeat_timeout_sec=timeout,
            read_token=os.getenv("MAESTRO_READ_TOKEN", ""),
            control_token=os.getenv("MAESTRO_CONTROL_TOKEN", ""),
            admin_token=os.getenv("MAESTRO_ADMIN_TOKEN", ""),
            allowed_skills=allowed_skills,
            llm_provider=llm_provider,
            llm_model=llm_model,
            llm_timeout_sec=llm_timeout,
            ollama_model=legacy_ollama_model,
            ollama_base_url=os.getenv("MAESTRO_OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            openai_base_url=os.getenv("MAESTRO_OPENAI_BASE_URL", "https://api.openai.com/v1"),
            azure_openai_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
            azure_openai_api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),
            azure_openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            anthropic_base_url=os.getenv(
                "MAESTRO_ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1"
            ),
            fleet_control_enabled=os.getenv("MAESTRO_FLEET_CONTROL_ENABLED", "false").lower()
            in {"1", "true", "yes"},
            fleet_control_host=os.getenv("MAESTRO_FLEET_CONTROL_HOST", "0.0.0.0"),
            fleet_control_port=fleet_port,
            fleet_tls_certificate=os.getenv("MAESTRO_FLEET_TLS_CERTIFICATE", ""),
            fleet_tls_private_key=os.getenv("MAESTRO_FLEET_TLS_PRIVATE_KEY", ""),
            fleet_tls_client_ca=os.getenv("MAESTRO_FLEET_TLS_CLIENT_CA", ""),
            control_rate_limit_per_minute=rate_limit,
            log_level=os.getenv("MAESTRO_LOG_LEVEL", "INFO"),
            event_retention_days=retention_days,
            auto_create_schema=auto_create_schema,
            robots=tuple(robots),
        )
