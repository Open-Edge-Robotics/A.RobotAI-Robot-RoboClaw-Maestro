# Maestro LLM Provider 설정

이 문서는 자연어 goal을 Mission DAG로 변환하는 Maestro LLM provider의 설정과 검증 방법을 설명합니다.

## 지원 Provider

```text
ollama
openai
azure_openai
anthropic
```

`MAESTRO_LLM_PROVIDER`가 비어 있으면 LLM planning을 비활성화하며 `/api/v1/goals`는 HTTP 503을 반환합니다. Mission을 직접 JSON으로 생성하거나 기존 Mission을 실행하는 기능은 LLM 없이 계속 동작합니다.

공통 설정:

```dotenv
MAESTRO_LLM_PROVIDER=<provider>
MAESTRO_LLM_MODEL=<model-or-deployment>
MAESTRO_LLM_TIMEOUT_SEC=30
```

## Ollama

```dotenv
MAESTRO_LLM_PROVIDER=ollama
MAESTRO_LLM_MODEL=qwen3:8b
MAESTRO_OLLAMA_BASE_URL=http://127.0.0.1:11434
```

Docker Compose에서 host Ollama를 사용할 때:

```dotenv
MAESTRO_OLLAMA_BASE_URL=http://host.docker.internal:11434
```

기존 `MAESTRO_OLLAMA_MODEL`도 하위 호환으로 지원하지만 신규 설정은 `MAESTRO_LLM_MODEL`을 사용합니다.

## OpenAI

```dotenv
MAESTRO_LLM_PROVIDER=openai
MAESTRO_LLM_MODEL=gpt-4.1
OPENAI_API_KEY=replace-openai-key
MAESTRO_OPENAI_BASE_URL=https://api.openai.com/v1
```

OpenAI-compatible gateway를 사용하는 경우 `MAESTRO_OPENAI_BASE_URL`만 변경합니다. 해당 gateway가 JSON object response format을 지원해야 합니다.

## Azure OpenAI

`MAESTRO_LLM_MODEL`에는 일반 model 이름이 아니라 Azure Portal에서 생성한 **deployment 이름**을 입력합니다.

```dotenv
MAESTRO_LLM_PROVIDER=azure_openai
MAESTRO_LLM_MODEL=maestro-gpt41-deployment
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_API_KEY=replace-azure-key
AZURE_OPENAI_API_VERSION=2024-10-21
```

다음 alias도 `azure_openai`로 정규화됩니다.

```text
azure
azureopenai
```

## Anthropic

```dotenv
MAESTRO_LLM_PROVIDER=anthropic
MAESTRO_LLM_MODEL=claude-sonnet-4-6
ANTHROPIC_API_KEY=replace-anthropic-key
MAESTRO_ANTHROPIC_BASE_URL=https://api.anthropic.com/v1
```

## 로컬 실행

`.env`를 uvicorn에 명시적으로 전달합니다.

```bash
.venv/bin/pip install -e '.[dev,llm,tracing]'
.venv/bin/uvicorn robo_claw_maestro.main:app \
  --env-file .env \
  --host 127.0.0.1 \
  --port 8000
```

또는 현재 shell에 환경 변수를 export한 후 실행합니다.

## Docker Compose 실행

저장소 루트의 `.env`에 provider 설정을 작성합니다.

```bash
docker compose up --build maestro-api web
```

API container에는 선택한 provider의 endpoint와 credential 환경 변수만 설정하는 것을 권장합니다.

## Goal API 검증

인증을 사용하는 경우:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/goals \
  -H "Authorization: Bearer ${MAESTRO_CONTROL_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{
    "goal":"연결된 로봇들의 상태를 점검하는 계획을 만들어줘",
    "created_by":"operator",
    "auto_start":false
  }'
```

정상 결과는 검증된 Mission과 Task 목록을 반환합니다. 다음 항목에 위반되는 provider 응답은 실행되지 않습니다.

- 잘못된 JSON
- 최대 Task 수 초과
- 알 수 없는 capability
- 알 수 없는 Robot ID
- dependency cycle
- timeout/retry budget 초과

## LangSmith 연동

모든 provider 호출은 공통 `llm_provider.complete` trace 아래에 기록됩니다. Provider별 child run 이름은 다음과 같습니다.

```text
OllamaProvider.complete
OpenAIProvider.complete
AzureOpenAIProvider.complete
AnthropicProvider.complete
```

설정은 [Maestro LangSmith 연동](LANGSMITH_INTEGRATION.md)을 참고합니다.

## Credential 및 로그 정책

- API key를 저장소에 commit하지 않습니다.
- API key를 command line argument로 전달하지 않습니다.
- structured log와 LangSmith metadata에 credential을 기록하지 않습니다.
- `.env`는 Git ignore 상태를 유지합니다.
- production에서는 Docker secret 또는 별도 secret manager 사용을 권장합니다.
- provider 오류 응답 전체에 민감 정보가 포함될 수 있으므로 사용자 API 응답에는 구조화된 오류만 노출합니다.

## 장애 동작

- provider timeout은 HTTP 504로 변환됩니다.
- 잘못된 plan은 HTTP 422이며 Mission으로 저장되지 않습니다.
- provider 장애가 실행 중인 Mission 상태를 변경하지 않습니다.
- LLM provider 설정이 없어도 Robot 상태, 직접 Mission, emergency stop은 동작합니다.
