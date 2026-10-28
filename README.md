# Robo-Claw Maestro

Robo-Claw Maestro는 여러 Robo-Claw 로봇을 중앙에서 모니터링하고 Mission과 Task를 배정하는 fleet orchestration 서비스입니다.

## 구현 상태

| PLAN 단계 | 판정 | 구현된 범위 | 완료 조건 대비 남은 항목 |
| --- | --- | --- | --- |
| Phase 0 | 완료 | 패키지, Ruff/pytest, Docker Compose, PostgreSQL, Alembic, SQLite 자동 migration, health/readiness, ADR | CI workflow는 아직 없음 |
| Phase 1 | 대부분 완료 | Domain 상태 머신, DAG, Command idempotency, SQLModel 저장소, 상태 event, 재시작 복구 | aggregate 저장과 event append를 하나의 DB transaction으로 묶는 unit of work 보강 필요 |
| Phase 2 | MVP 완료 | RosGrpc unary/stream adapter, 정적 endpoint, 자동 monitor, exponential backoff, fake gRPC 3대, disconnect/reconnect/session test | 기존 RosGrpc 자체의 구조화 capability와 cancel contract는 없음 |
| Phase 3 | 대부분 완료 | Registry, snapshot 저장, telemetry event/retention, REST API, WebSocket event, read/control/admin token | 장기 telemetry 집계 정책과 외부 IdP 기반 RBAC는 없음 |
| Phase 4 | 핵심 구현 | Mission lifecycle, dependency/병렬 dispatch, retry/reassignment, DB resource lease, startup reconciliation, safety policy, deadline | RosGrpc ACK 분리, 실행 중 원격 cancel/pause, distributed worker 자동 scheduling loop 보강 필요 |
| Phase 5 | 대부분 완료 | Plan parser, Robot registry 기반 실시간 capability catalog, tool facade, Ollama/OpenAI/Azure OpenAI/Anthropic, goal API, bounded replanning, planning metric, LangSmith plan trace | provider 실제 token usage와 자동 replanning trigger는 없음 |
| Phase 6 | 부분 구현 | React fleet/mission/event Console, planning 문서 편집과 Dry-run, goal 입력, 승인 대기, snapshot, 직접 Task, emergency controls | 실제 지도 component, 별도 robot 상세 화면, camera polling/stream 최적화 필요 |
| Phase 7 | Connector 구현·실기기 검증 대기 | FleetControl proto/server, 실제 `robo_claw_grpc` outbound connector, registration/telemetry, SQLite dedup/result replay, TLS/mTLS | 실제 Robot 읽기 전용 인수, AgentStatus 통합, 실제 cancel/pause/resume handler 필요 |
| Phase 8 | 부분 구현 | health/RBAC, Prometheus metric, optional LangSmith trace, JSON log, rate limit, event retention, backup/장애 runbook, reconnect fault test | OpenTelemetry exporter, 인증서 교체 자동화, 부하 테스트, DB restore 자동 검증 필요 |

정적 RosGrpc 방식과 Robo-Claw outbound FleetControl connector가 모두 구현되어 있습니다. FleetControl connector는 fake Maestro와 실제 gRPC stream 통합 테스트까지 완료됐으며, 다음 단계는 실제 Robot 1대의 읽기 전용 인수 테스트입니다. 전체 요구사항은 [PLAN.md](PLAN.md), 최신 상세 구현 판정은 [implementation-status-20260923.md](implementation-status-20260923.md)를 참고합니다. 이전 기록은 [2026-09-18](implementation-status-20260918.md), [2026-09-22](implementation-status-20260922.md)에 있습니다.

## MVP 완료 정의 대비 현황

| MVP 조건 | 판정 | 근거 또는 남은 작업 |
| --- | --- | --- |
| fake/simulated robot 3대 동시 관리 | 충족 | in-memory 및 실제 loopback gRPC 3대 통합 테스트 |
| Robot 연결·상태 실시간 표시 | 충족 | RosGrpc stream, FleetControl telemetry, WebSocket, Console |
| 자연어 목표를 검증된 Mission/Task로 변환 | 조건부 충족 | Ollama/OpenAI/Azure OpenAI/Anthropic 설정 시 동작하며 fake provider 테스트 완료 |
| capability 기반 Task 배정 | 충족 | capability·health·battery·map·pose safety policy 적용 |
| dependency 순차 및 독립 Task 병렬 실행 | 충족 | component/E2E 테스트 포함 |
| 성공·실패·timeout·unknown·cancel 추적 | 부분 충족 | 로컬 상태는 지원하지만 RosGrpc 원격 cancel contract가 없음 |
| retry budget 및 다른 Robot 재배정 | 충족 | 이전 실행 Robot 제외와 budget 테스트 포함 |
| 중복 command 방지 | 구현·실기기 검증 대기 | Maestro DB unique key와 Robo-Claw SQLite journal/restart 테스트 완료 |
| Robot별/Fleet emergency stop | 충족 | 병렬 실행과 부분 실패 응답 지원 |
| 재시작 후 복구 및 reconciliation | 충족 | DB 재개방 및 startup reconciliation 통합 테스트 |
| 모든 명령과 상태 변경 audit | 부분 충족 | 주요 상태·승인·dispatch event는 기록하지만 단일 DB transaction unit of work 보강 필요 |
| 단위·컴포넌트·계약·통합 테스트 | 충족 | 기본 suite에서 hardware/E2E 외부 장비 의존 없음 |
| 실제 Robot 2대 인수 시나리오 | 미충족 | fake robot 2~3대는 검증됐지만 실제 하드웨어 인수는 별도 수행 필요 |
| 운영·장애 대응 문서 | 충족 | `docs/OPERATIONS.md`, `docs/ROBOT_TEST_MANUAL.md`, `docs/PROTOCOL.md` |

따라서 현재 상태는 **fake fleet 기준 backend MVP와 초기 Console, 실제 Robo-Claw connector 코드까지 구현됐지만 PLAN의 최종 MVP 완료 상태는 아닙니다.** 원격 cancel, AgentStatus 통합, 실기기 인수 테스트가 완료되어야 최종 MVP로 판정할 수 있습니다.

## 개발 환경

로컬에서 직접 실행하거나 테스트할 때 사용합니다. Docker Compose로만 운영한다면 이 단계는 필요 없습니다.

```bash
cd A.RobotAI-Robot-RoboClaw-Maestro
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

`-e`(editable) 설치이므로 소스 수정 후 재설치 없이 프로세스만 재시작하면 반영됩니다. 반면 Docker Compose 배포는 소스가 image에 복사되므로 코드 변경 후 반드시 image를 다시 build해야 합니다([신규 호스트와 Docker Compose](#신규-호스트와-docker-compose) 참고).

기본 `sqlite:///maestro.db`는 개발 편의를 위해 최초 실행 시 schema를 자동 생성합니다. PostgreSQL은 `MAESTRO_AUTO_CREATE_SCHEMA=false`로 두고 반드시 Alembic migration을 먼저 적용합니다.

```bash
# 기본 SQLite로 바로 실행할 수 있습니다.
.venv/bin/uvicorn robo_claw_maestro.main:app --host 127.0.0.1 --port 8000
```

`no such table: missions` 오류가 발생했던 기존 빈 SQLite 파일도 현재 버전에서는 자동 bootstrap됩니다. 명시적으로 migration 방식만 사용하려면 다음처럼 실행합니다.

```bash
export MAESTRO_AUTO_CREATE_SCHEMA=false
.venv/bin/alembic upgrade head
.venv/bin/uvicorn robo_claw_maestro.main:app --host 127.0.0.1 --port 8000
```

## LLM Provider 설정

자연어 goal planning은 Ollama, OpenAI, Azure OpenAI와 Anthropic을 지원합니다.

```bash
export MAESTRO_LLM_PROVIDER=azure_openai
export MAESTRO_LLM_MODEL=<azure-deployment-name>
export AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com
export AZURE_OPENAI_API_KEY=<api-key>
export AZURE_OPENAI_API_VERSION=2024-10-21
```

`MAESTRO_LLM_PROVIDER`를 비워두면 LLM planning만 비활성화되고 Robot 관리, 직접 Mission과 emergency stop은 계속 동작합니다.

Planner는 goal 요청마다 Robot registry를 조회해 등록된 Robot의 `robot_id`, type, 연결 상태, site/map, capability 목록을 fleet catalog로 LLM prompt에 포함하고, 같은 목록으로 계획을 검증합니다. FleetControl Robot은 연결 시 `SKILLS.md` 기반 capability를 직접 보고하므로 Robot별 capability를 Maestro에 수동 등록할 필요가 없고, Maestro 재시작 없이 새 Robot이 planning에 반영됩니다. `MAESTRO_ROBOTS_JSON`의 `capabilities`는 정적 RosGrpc Robot용 보조 입력으로만 합산됩니다. Provider별 전체 설정은 [Maestro LLM Provider 설정](docs/LLM_PROVIDERS.md)을 참고합니다.

## 검증 지점 1: 정적 검사와 전체 자동 테스트

언제든 다음 명령으로 현재 구현을 검증할 수 있습니다.

```bash
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
.venv/bin/pytest -q
```

테스트에는 domain 상태 머신, DB 재시작 복구, idempotency, 실제 gRPC loopback 3대, REST API, RBAC, retry/reassignment, resource lock, reconciliation, LLM plan validation과 LangSmith 비활성 호환성/redaction이 포함됩니다. 현재 검증 기준은 backend `112 passed`, frontend typecheck/build 성공입니다.

Migration도 독립적으로 확인할 수 있습니다.

```bash
rm -f /tmp/maestro-check.db
MAESTRO_DATABASE_URL=sqlite:////tmp/maestro-check.db \
  .venv/bin/alembic upgrade head
MAESTRO_DATABASE_URL=sqlite:////tmp/maestro-check.db \
  .venv/bin/alembic current
```

기대 revision은 `0004_approvals (head)`입니다.

## Planning 문서와 Dry-run

Console 상단의 **설정** 탭에서 planner가 참고하는 Markdown 문서 5종을 편집하고, 로봇 없이 계획을 미리 검증할 수 있습니다.

| 문서 | 파일 | 용도 |
| --- | --- | --- |
| Maestro 역할 | `role.md` | planner의 원칙, 안전 기준, 출력 스타일 |
| Maestro 스킬 | `skills.md` | 자주 쓰는 작업 분해 패턴 |
| 외부 MCP 설정 | `mcp.md` | 외부 도구 정보. 현재 Maestro는 MCP를 직접 호출하지 않으며 planning 참고용으로만 사용합니다. |
| Troubleshooting | `troubleshooting.md` | 실패 유형별 대응과 replan 규칙 |
| 로봇 프로필 | `robots.md` | 연결될 로봇의 특성·역할·capability. Dry-run에서 가상 로봇으로 사용합니다. |

- 다섯 문서는 모두 planner system prompt에 포함됩니다. 실제 goal planning(`/api/v1/goals`)과 Dry-run에 같은 내용이 적용되며, 저장한 뒤 다음 요청부터 반영됩니다.
- 기본 safety 규칙보다 우선하지는 않습니다. 문서로 "검증을 건너뛰어라" 같은 지시를 넣어도 deterministic 검증은 그대로 동작합니다.
- 문서는 `MAESTRO_KNOWLEDGE_DIR/<name>.md`에 저장됩니다. Docker Compose에서는 호스트의 `./knowledge`(`MAESTRO_KNOWLEDGE_HOST_DIR`)가 mount되므로 image를 다시 build해도 유지됩니다. 파일이 없으면 내장 기본값을 사용하고, **기본값으로** 버튼은 파일을 삭제해 기본값으로 되돌립니다.

`robots.md` 작성 형식:

```markdown
## cloid01

- robot_type: cloid
- site_id: lab
- map_id: floor-1
- capabilities: get_status, navigate_to, describe_surroundings, tidy_home

거실과 주방을 담당합니다. 문턱을 넘지 못합니다.
```

`## <robot_id>` 제목 하나가 로봇 하나이고, `- capabilities:` 줄의 이름은 Robo-Claw `docs/SKILLS.md`의 스킬 이름을 사용합니다. 나머지 문장은 로봇 특징으로 planner에 전달됩니다.

### Dry-run

**Plan Dry-run**에 목표를 입력하고 실행하면 Mission을 만들거나 로봇에 명령하지 않고 다음을 보여줍니다.

- 검증 통과 여부와 거부 사유 (`unknown capabilities` 등)
- 실행 단계(stage)별 Task와 병렬 여부
- Task별 배정 후보 로봇(연결된 로봇은 실제 상태, 프로필 로봇은 `PROFILE`)
- 경고: 배정 가능한 로봇 없음, `required_capabilities` 누락, 수행 불가능한 `preferred_robot_id`
- LLM에 보낸 system/user prompt와 원본 응답

연결된 로봇과 프로필 로봇의 `robot_id`가 같으면 연결된 로봇 정보를 우선합니다. **로봇 프로필 포함**을 끄면 실제 연결된 로봇만으로 검증하므로 실제 `/api/v1/goals` 결과와 같은 조건이 됩니다.

API로도 사용할 수 있습니다.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/planning/dry-run \
  -H "Authorization: Bearer ${MAESTRO_CONTROL_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"goal":"내가 없는 동안 집안을 정리해줘","include_profiles":true}'
```

LLM provider 연결 실패는 `502 LLM provider error: ...`로 원인과 함께 반환됩니다.

## 검증 지점 2: 간단한 in-memory fleet

한 프로세스에서 fake robot 3대를 즉시 실행합니다.

```bash
.venv/bin/python scripts/run_fake_fleet.py --robots 3 --port 8000
```

다른 터미널에서 확인합니다.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/robots/synchronize
curl -s http://127.0.0.1:8000/api/v1/robots
```

세 로봇 모두 `ONLINE`이어야 합니다.

## 검증 지점 3: 실제 RosGrpc transport를 사용하는 fake robot 3대

이 검증은 HTTP 내부 fake가 아니라 TCP loopback의 실제 protobuf/gRPC server와 channel을 사용합니다.

터미널 1에서 fake RosGrpc fleet을 실행합니다.

```bash
.venv/bin/python scripts/run_fake_grpc_fleet.py --robots 3 --base-port 55051
```

터미널 2에서 Maestro를 실행합니다.

```bash
export MAESTRO_DATABASE_URL=sqlite:////tmp/maestro-grpc.db
export MAESTRO_ROBOTS_JSON='[
  {"robot_id":"robot-1","endpoint":"127.0.0.1:55051","capabilities":["inspect","get_status"]},
  {"robot_id":"robot-2","endpoint":"127.0.0.1:55052","capabilities":["inspect","get_status"]},
  {"robot_id":"robot-3","endpoint":"127.0.0.1:55053","capabilities":["inspect","get_status"]}
]'
rm -f /tmp/maestro-grpc.db
.venv/bin/alembic upgrade head
.venv/bin/uvicorn robo_claw_maestro.main:app --host 127.0.0.1 --port 8000
```

터미널 3에서 상태와 명령을 확인합니다.

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/api/v1/robots

curl -s -X POST \
  http://127.0.0.1:8000/api/v1/robots/robot-1/skills/get_status \
  -H 'Content-Type: application/json' \
  -d '{"params":{},"timeout_sec":10}'
```

API lifespan에서 `RobotMonitor`가 자동으로 세 status stream을 연결합니다. 기동 직후에는 `UNKNOWN`일 수 있지만 첫 telemetry 이후 `ONLINE`으로 변경되어야 합니다.

## Mission 병렬 실행 검증

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/missions \
  -H 'Content-Type: application/json' \
  -d '{
    "mission_id":"verify-parallel",
    "goal":"두 구역 병렬 점검",
    "tasks":[
      {"task_id":"inspect-a","instruction":"A 구역 점검","required_capabilities":["inspect"]},
      {"task_id":"inspect-b","instruction":"B 구역 점검","required_capabilities":["inspect"]}
    ]
  }'

curl -s -X POST http://127.0.0.1:8000/api/v1/missions/verify-parallel/run
curl -s http://127.0.0.1:8000/api/v1/missions/verify-parallel/events
```

두 Task가 서로 다른 robot에 배정되고 Mission이 `SUCCEEDED`가 되어야 합니다.

## 인증 검증

`MAESTRO_READ_TOKEN`과 `MAESTRO_CONTROL_TOKEN`이 비어 있으면 개발 모드로 인증을 생략합니다. 역할 분리를 확인하려면 다음처럼 실행합니다.

```bash
export MAESTRO_READ_TOKEN=read-secret
export MAESTRO_CONTROL_TOKEN=control-secret
export MAESTRO_ADMIN_TOKEN=admin-secret
# 설정하면 목록 밖의 skill은 모두 거부합니다.
export MAESTRO_ALLOWED_SKILLS_JSON='["get_status","inspect","navigate_to","power_tool"]'
```

```bash
# 조회 허용
curl -H 'Authorization: Bearer read-secret' \
  http://127.0.0.1:8000/api/v1/robots

# read token으로 제어 요청: HTTP 403 기대
curl -i -X POST -H 'Authorization: Bearer read-secret' \
  http://127.0.0.1:8000/api/v1/fleet/emergency-stop

# control token으로 제어 요청
curl -X POST -H 'Authorization: Bearer control-secret' \
  http://127.0.0.1:8000/api/v1/fleet/emergency-stop
```

WebSocket 인증은 query token을 사용합니다. 일반 `curl`은 환경에 따라 `ws://`를 지원하지 않으므로 포함된 Python client로 검증합니다.

```bash
.venv/bin/python scripts/watch_events.py --token read-secret

# 이벤트 하나만 받고 종료
.venv/bin/python scripts/watch_events.py \
  --token read-secret \
  --max-events 1 \
  --timeout 10
```

직접 사용하는 URL은 다음과 같습니다.

```text
ws://127.0.0.1:8000/api/v1/events/stream?token=read-secret
```

검증 지점 5에서 실행하는 FleetControl fake robot은 `power_tool`을 `dangerous` capability로 등록합니다. 승인 workflow는 다음처럼 확인합니다.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/robots/fleet-robot-1/skills/power_tool \
  -H 'Authorization: Bearer control-secret' \
  -H 'Content-Type: application/json' \
  -d '{"params":{},"requested_by":"operator"}'

curl -s http://127.0.0.1:8000/api/v1/approvals \
  -H 'Authorization: Bearer read-secret'

curl -s -X POST http://127.0.0.1:8000/api/v1/approvals/APPROVAL_ID/approve \
  -H 'Authorization: Bearer admin-secret' \
  -H 'Content-Type: application/json' \
  -d '{"actor":"safety-admin"}'
```

첫 요청은 HTTP 202를 반환하며 승인 전에는 skill이 실행되지 않습니다.

## 검증 지점 4: React 관제 Console

개발 서버는 API 요청과 WebSocket을 `127.0.0.1:8000`으로 proxy합니다.

```bash
cd web
npm install
npm run typecheck
npm run build
npm run dev
```

브라우저에서 `http://127.0.0.1:3000`을 엽니다. 화면에서 다음 항목을 확인할 수 있습니다.

- Robot online/offline, 배터리, 현재 Task와 capability
- Mission 목록, Task dependency와 진행 상태 및 start/pause/resume/cancel
- 로봇별 직접 Task, camera snapshot과 개별/fleet emergency stop
- 위험 skill 승인/거부 대기 목록
- WebSocket event timeline
- 자연어 goal 입력
- read/control token 저장
- 확인 대화상자가 있는 fleet emergency stop

LLM provider를 설정하지 않았다면 자연어 goal 입력은 `503 LLM planner is not configured`를 반환합니다. 나머지 fleet 화면과 emergency stop은 LLM 없이 동작합니다.

## 검증 지점 5: FleetControl outbound robot

Maestro의 전용 양방향 stream을 활성화합니다.

```bash
export MAESTRO_FLEET_CONTROL_ENABLED=true
export MAESTRO_FLEET_CONTROL_PORT=50053
.venv/bin/uvicorn robo_claw_maestro.main:app --host 127.0.0.1 --port 8000
```

별도 터미널 두 개에서 Robot이 Maestro로 outbound 연결하게 합니다.

```bash
.venv/bin/python scripts/run_fake_fleet_control_robot.py --robot-id fleet-robot-1
.venv/bin/python scripts/run_fake_fleet_control_robot.py --robot-id fleet-robot-2
```

두 Robot은 정적 endpoint 등록 없이 `/api/v1/robots`에 나타나야 합니다. 실제 프로세스 검증에서는 두 outbound Robot 모두 `ONLINE`과 서로 다른 session ID로 등록되는 것을 확인했습니다. 상세 protocol 및 mTLS 설정은 [FleetControl 프로토콜](docs/PROTOCOL.md)을 참고합니다.

## 검증 지점 6: 운영 metric과 rate limit

```bash
curl -s http://127.0.0.1:8000/metrics | grep '^maestro_'
```

Prometheus 형식으로 연결 Robot, WebSocket client, reconnect, Task 결과, emergency stop latency, LLM 요청/검증 실패 metric이 반환됩니다.

제어 요청 제한은 다음 환경 변수로 조정합니다.

```bash
export MAESTRO_CONTROL_RATE_LIMIT_PER_MINUTE=120
export MAESTRO_EVENT_RETENTION_DAYS=30
export MAESTRO_LOG_LEVEL=INFO
```

운영 절차는 [운영 가이드](docs/OPERATIONS.md)를 참고합니다.

## 검증 지점 7: Maestro LangSmith trace

기본값은 비활성입니다. 테스트 project에서만 먼저 활성화합니다.

```bash
export MAESTRO_LANGSMITH_TRACING=true
export MAESTRO_LANGSMITH_CAPTURE_CONTENT=false
export LANGSMITH_API_KEY=lsv2_...
export LANGSMITH_PROJECT=robo-claw-maestro-test
export LANGSMITH_TRACING_SAMPLING_RATE=1.0
# redaction된 구조화 입력/출력을 LangSmith 화면에서 표시합니다.
export LANGSMITH_HIDE_INPUTS=false
export LANGSMITH_HIDE_OUTPUTS=false
export MAESTRO_LLM_PROVIDER=ollama
export MAESTRO_LLM_MODEL=<installed-model>
.venv/bin/uvicorn robo_claw_maestro.main:app --host 127.0.0.1 --port 8000
```

다른 터미널에서 실제 planning 요청을 보냅니다. 인증 token을 설정했다면 control header를 함께 사용합니다.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/goals \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${MAESTRO_CONTROL_TOKEN}" \
  -d '{"goal":"연결된 로봇들의 상태를 확인하는 계획을 만들어줘","created_by":"langsmith-check"}'
```

Token을 설정하지 않은 개발 모드라면 `Authorization` header를 생략합니다. LangSmith의 `robo-claw-maestro-test` project에서 아래 구현된 Trace Tree를 확인합니다.

### 구현된 LangSmith Trace Tree

```text
maestro_api_request                              [chain, HTTP root]
├─ maestro_goal_request                          [chain]
│  └─ create_mission_plan                        [chain]
│     ├─ llm_provider.complete                   [llm]
│     │  └─ Ollama/OpenAI/AzureOpenAI/AnthropicProvider.complete
│     └─ validate_mission_plan                   [chain]
│        ├─ decode_plan_json                     [chain]
│        └─ validate_plan_task                   [chain, Task마다 반복]
└─ persist_mission                               [chain]

maestro_api_request                              [chain, Mission run/replan]
├─ mission_scheduling_round                      [chain]
│  ├─ assign_ready_tasks                         [chain]
│  │  └─ score_robot_candidates                  [chain, Task마다 반복]
│  │     └─ evaluate_safety_policy               [chain, Robot 후보마다 반복]
│  └─ dispatch_task                              [tool, 병렬 Task마다 생성]
│     ├─ ros_grpc.execute_task                   [tool, RosGrpc 경로] 또는
│     └─ fleet_control.execute_task              [tool, FleetControl 경로]
├─ mission_replan                                [chain, replan 요청 시]
│  └─ create_mission_plan                        [chain]
└─ persist_replacement_plan                      [chain]

startup_reconciliation                           [chain, API startup]
```

`fleet_control.execute_task` span은 ACK와 최종 TaskUpdate를 기다리는 전체 시간을 포함하지만 ACK와 TaskUpdate가 현재 별도 child span으로 분리되지는 않습니다. Skill 호출은 각각 `ros_grpc.execute_skill`, `fleet_control.execute_skill`로 표시됩니다.

기본 `MAESTRO_LANGSMITH_CAPTURE_CONTENT=false`에서는 goal, instruction, provider 응답 원문 대신 hash와 길이를 기록합니다. 개발 환경에서 원문까지 확인할 때만 다음 값을 추가합니다.

```bash
export MAESTRO_LANGSMITH_CAPTURE_CONTENT=true
```

상세 설정과 redaction 범위는 [Maestro LangSmith 연동](docs/LANGSMITH_INTEGRATION.md)을 참고합니다.

비활성 동작 동일성은 다음처럼 확인합니다.

```bash
export MAESTRO_LANGSMITH_TRACING=false
.venv/bin/pytest tests/unit/test_tracing.py -q
```

## 신규 호스트와 Docker Compose

신규 호스트에서는 bootstrap script가 credential 생성, Compose 검증, build, 기동과 readiness 확인을 수행합니다.

```bash
git clone <robo-claw-maestro-repository-url>
cd A.RobotAI-Robot-RoboClaw-Maestro
./scripts/bootstrap_maestro_host.sh --no-start
editor .env
./scripts/bootstrap_maestro_host.sh
python3 scripts/verify_maestro_host.py --env-file .env
```

`.env`는 저장소 루트(`docker-compose.yml`과 같은 폴더)에 생성됩니다. 기존 `.env`를 덮어쓰지 않으며 read/control/admin token과 PostgreSQL password를 자동 생성합니다. Console 첫 접속 시 우측 상단 token 입력란에 `.env`의 `MAESTRO_CONTROL_TOKEN`을 입력해야 하며, 입력하지 않으면 `403 forbidden`이 표시됩니다.

코드를 수정했거나 새 버전을 받은 경우에는 `--no-build` 없이 실행해 image를 다시 build합니다. `--no-build`는 `.env` 값만 바꿨을 때 사용합니다.

```bash
./scripts/bootstrap_maestro_host.sh             # 코드 변경 반영 (rebuild + 재기동)
./scripts/bootstrap_maestro_host.sh --no-build  # 설정(.env)만 변경
```

호스트 포트가 다른 서비스와 충돌하면 `.env`에서 변경합니다. Container 내부 포트는 바뀌지 않으므로 다른 설정은 수정할 필요가 없습니다.

| 변수 | 기본값 | 용도 |
| --- | --- | --- |
| `MAESTRO_WEB_PORT` | `3000` | Web Console. `.env.example`에 없으므로 필요 시 추가합니다. |
| `MAESTRO_PORT` | `8000` | REST API/WebSocket 직접 호출(curl, script). Console은 내부 proxy를 사용하므로 이 포트를 거치지 않습니다. |
| `MAESTRO_FLEET_CONTROL_PORT` | `50053` | Robot outbound FleetControl 접속. 변경 시 Robot의 `ROBOT_PORT`도 같은 값으로 맞춥니다. |
| `POSTGRES_PORT` | `5432` | PostgreSQL. 호스트에 PostgreSQL이 이미 있으면 변경합니다. | 전체 절차와 옵션은 [다중 로봇 연동 테스트](docs/ROBOT_TEST_MANUAL.md#2-신규-호스트-빠른-설치와-실행)를 참고합니다.

수동 실행이 필요한 경우:

```bash
cp .env.example .env
# .env의 change-me와 token을 반드시 변경합니다.
docker compose --env-file .env up --build
```

Console은 `http://127.0.0.1:3000`, API는 `http://127.0.0.1:8000`에서 확인합니다.

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/ready
```

## 실제 Robo-Claw 2대 이상 연결

두 가지 방식을 지원하며, Robot 수가 늘어나는 환경에서는 FleetControl 방식을 권장합니다.

### 방식 A: FleetControl outbound (권장)

Robot이 Maestro로 접속하므로 Maestro에 Robot endpoint나 capability를 등록할 필요가 없습니다.

1. Maestro `.env`에서 `MAESTRO_FLEET_CONTROL_ENABLED=true`로 설정하고 재기동합니다. 방화벽에서 `50053/tcp`를 허용합니다.
2. AI Config Server의 Robot 프로필 → **Maestro FleetControl 연동**에서 Robot별로 설정합니다.

   | 항목 | 값 |
   | --- | --- |
   | `MAESTRO_IP` | Robot에서 접근 가능한 Maestro 호스트 IP (`127.0.0.1` 불가) |
   | `ROBOT_PORT` | Maestro의 `MAESTRO_FLEET_CONTROL_PORT` (기본 `50053`, API 포트 아님) |
   | `ROBOT_ID` | Robot마다 고유한 값 (예: `cloid01`, `cloid02`) |
   | `ROBOT_SITE_ID`, `ROBOT_MAP_ID` | 같은 공간·지도를 쓰는 Robot은 같은 값 |
   | `RC_SKILLS_GUIDE_FILE` | 비워두면 Robo-Claw의 `docs/SKILLS.md` 사용 |

3. 각 Robot을 `--use-grpc`로 실행합니다: `./rclaw launch <robot-profile> <environment> --docker --use-grpc`
4. Console 또는 `GET /api/v1/robots`에서 두 Robot이 `ONLINE`이고 capability가 표시되는지 확인합니다.

### 방식 B: 정적 RosGrpc endpoint

Maestro가 각 Robo-Claw의 `robo_claw_grpc` 포트 `50051`로 직접 접속합니다. Robot별 ID, endpoint, capability를 `MAESTRO_ROBOTS_JSON`에 등록합니다.

상세 안전 절차, offline/reconnect, emergency stop 검증은 [다중 로봇 연동 테스트 매뉴얼](docs/ROBOT_TEST_MANUAL.md)을 참고합니다.

## 주요 API

```text
GET  /health
GET  /ready
GET  /api/v1/robots
GET  /api/v1/robots/{robot_id}
GET  /api/v1/robots/{robot_id}/events
GET  /api/v1/robots/{robot_id}/snapshot
POST /api/v1/robots/{robot_id}/tasks
POST /api/v1/robots/{robot_id}/skills/{skill_name}
POST /api/v1/robots/{robot_id}/emergency-stop
POST /api/v1/fleet/emergency-stop
GET  /api/v1/approvals
POST /api/v1/approvals/{approval_id}/approve
POST /api/v1/approvals/{approval_id}/reject
POST /api/v1/goals
POST /api/v1/planning/dry-run
GET  /api/v1/planning/metrics
GET  /api/v1/knowledge
GET  /api/v1/knowledge/robots/profiles
GET  /api/v1/knowledge/{name}
PUT  /api/v1/knowledge/{name}
POST /api/v1/knowledge/{name}/reset
GET  /api/v1/missions
POST /api/v1/missions
POST /api/v1/missions/{mission_id}/start
POST /api/v1/missions/{mission_id}/pause
POST /api/v1/missions/{mission_id}/resume
POST /api/v1/missions/{mission_id}/cancel
POST /api/v1/missions/{mission_id}/replan
POST /api/v1/missions/{mission_id}/run
GET  /api/v1/missions/{mission_id}/events
WS   /api/v1/events/stream
```

## 운영 문서

- [다중 로봇 연동 테스트](docs/ROBOT_TEST_MANUAL.md)
- [FleetControl 프로토콜](docs/PROTOCOL.md)
- [운영·metric·backup·장애 대응](docs/OPERATIONS.md)
- [Maestro LLM Provider 설정](docs/LLM_PROVIDERS.md)
- [Maestro LangSmith 연동](docs/LANGSMITH_INTEGRATION.md)
- [Architecture Decision Records](docs/adr/)
