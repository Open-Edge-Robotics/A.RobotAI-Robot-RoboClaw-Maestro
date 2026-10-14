Add public LLM providers and host bootstrap tooling

- configure Ollama, OpenAI, Azure OpenAI, or Anthropic from environment variables
- add HTTP adapters and a provider factory with legacy Ollama compatibility
- pass provider credentials and endpoints through Docker Compose
- integrate every provider with the existing optional LangSmith trace tree
- add secure new-host environment generation, Compose startup, and readiness checks
- add non-destructive API, metrics, auth, OpenAPI, Robot, and WebSocket verification
- document provider setup and fresh-host deployment; verify tests, build, and lint
