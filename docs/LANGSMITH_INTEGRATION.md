# Maestro LangSmith 연동

이 문서는 Robo-Claw Maestro에 구현된 선택적 LangSmith tracing의 범위, 설정, 보안 규칙과 검증 방법을 설명합니다.

## 결론

**선택적 연동이 구현되어 있으며 기본값은 비활성화입니다.**

Maestro는 자연어 목표를 Mission DAG로 변환하고 plan validation, scheduling, dispatch, retry, reconciliation과 replanning을 수행합니다. LangSmith를 연동하면 다음 질문을 하나의 실행 tree에서 분석할 수 있습니다.

- 사용자 목표가 어떤 LLM 입력과 출력으로 변환됐는가
- LLM이 생성한 plan이 왜 validation에서 거부됐는가
- 어떤 capability와 Robot 상태를 바탕으로 Task가 배정됐는가
- 실패 후 replanning이 어떤 변경을 만들었는가
- provider latency, parse 오류, retry budget이 어디에서 발생했는가
- 특정 Mission과 Robot command/event를 어떻게 연결할 수 있는가

다만 LangSmith는 Robot heartbeat, pose stream, camera stream, Prometheus metric을 저장하는 용도로 사용하지 않습니다. 실시간 fleet 상태는 기존 PostgreSQL event, WebSocket, Prometheus를 사용하고 LangSmith는 **LLM과 Mission orchestration의 저빈도 의사결정 trace**에만 사용합니다.

## 연동 원칙

1. 완전한 optional 기능으로 구현합니다.
2. 기본값은 비활성화입니다.
3. 비활성 상태에서는 원래 함수, 반환값, 예외와 실행 순서를 그대로 유지합니다.
4. LangSmith 장애가 Mission 실행이나 emergency stop을 중단하면 안 됩니다.
5. telemetry raw stream, camera base64, map binary는 전송하지 않습니다.
6. token, credential, authorization header, 사용자 원문 전체를 기본 trace에 저장하지 않습니다.
7. LLM은 계속 application tool만 사용하며 LangSmith client를 직접 호출하지 않습니다.
8. emergency stop은 trace upload 또는 flush를 기다리지 않습니다.
9. deterministic event/audit log를 LangSmith로 대체하지 않습니다.
10. Prometheus와 OpenTelemetry 도입 계획을 LangSmith로 대체하지 않습니다.

## 권장 환경 변수

Maestro와 Robot Robo-Claw tracing을 독립적으로 켜기 위해 Maestro 전용 gate를 둡니다.

```dotenv
# Maestro tracing 자체 활성화 여부
MAESTRO_LANGSMITH_TRACING=false
# prompt/응답 원문 저장은 별도 opt-in입니다.
MAESTRO_LANGSMITH_CAPTURE_CONTENT=false

# LangSmith SDK 표준 설정
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_PROJECT=robo-claw-maestro
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_TRACING_SAMPLING_RATE=1.0
LANGSMITH_HIDE_INPUTS=false
LANGSMITH_HIDE_OUTPUTS=false
```

동작 규칙:

- `MAESTRO_LANGSMITH_TRACING=false`: 모든 Maestro trace wrapper는 no-op입니다.
- `MAESTRO_LANGSMITH_TRACING=true`: LangSmith package와 API key가 있을 때만 trace를 전송합니다.
- `MAESTRO_LANGSMITH_CAPTURE_CONTENT=false`: 사용자 목표, instruction과 provider 문자열은 hash/길이만 저장합니다.
- 개발 환경에서 원문 확인이 꼭 필요할 때만 `MAESTRO_LANGSMITH_CAPTURE_CONTENT=true`로 설정합니다.
- `LANGSMITH_PROJECT` 기본값은 `robo-claw-maestro`로 둡니다.
- Robot의 Robo-Claw trace와 같은 project를 사용할 경우 `component:maestro`, `component:robot` tag로 구분합니다.
- 운영에서는 sampling rate를 낮출 수 있지만 개발/인수 단계에서는 `1.0`을 권장합니다.

## 의존성 구성

기본 domain/application 실행에 LangSmith를 강제하지 않도록 optional dependency를 추가합니다.

```toml
[project.optional-dependencies]
tracing = [
  "langsmith>=0.1.100"
]
```

개발 환경에서는 다음처럼 설치합니다.

```bash
.venv/bin/pip install -e '.[dev,tracing]'
```

`domain/`은 LangSmith를 import하지 않습니다. tracing module은 `telemetry/`에 구현되어 있습니다.

구현 파일:

```text
src/robo_claw_maestro/telemetry/tracing.py
```

공개 API:

```text
is_enabled()
traceable(...)
process_inputs(...)
process_outputs(...)
current_trace_id()
set_current_metadata(...)
init_tracing(...)
```

LangSmith가 설치되지 않았거나 비활성 상태이면 모두 no-op 또는 빈 값으로 동작합니다.

## 구현된 Trace Tree

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

현재 `fleet_control.execute_task`와 `ros_grpc.execute_task` run은 remote 결과를 기다리는 전체
시간을 포함합니다. FleetControl ACK와 TaskUpdate, resource lease 획득, DB event append는 아직
별도 child run으로 분리하지 않았습니다. Skill 호출은 `ros_grpc.execute_skill` 또는
`fleet_control.execute_skill`로 표시됩니다.

## 계측 적용 위치

### MaestroAgent

파일:

```text
src/robo_claw_maestro/agent/maestro_agent.py
```

계측 대상:

- `create_plan`
- `replan`
- `_request_plan`

기록 정보:

```text
goal_hash
created_by_role
mission_id
plan_version
provider
request_timeout_sec
replan_count
validation_result
latency
estimated_token_count
```

사용자 목표 원문은 기본 metadata에 넣지 않습니다. Input 표시를 명시적으로 허용한 개발 환경에서만 redaction된 길이 제한 문자열을 저장합니다.

### LLM Provider

파일:

```text
src/robo_claw_maestro/agent/provider.py
src/robo_claw_maestro/adapters/llm/ollama.py
```

계측 대상:

- provider request
- provider response
- timeout
- HTTP status
- model
- latency
- 실제 또는 추정 token usage

Provider 실제 token usage를 공통 metrics로 전달하는 기능은 후속 작업입니다. 현재 planning metrics는 문자열 길이 기반 추정치를 사용하며 LangSmith provider run에서 요청 지연과 성공/실패를 확인할 수 있습니다.

### MissionPlanParser

파일:

```text
src/robo_claw_maestro/agent/planner.py
```

계측 대상:

- JSON 추출
- task 수
- capability validation
- Robot ID validation
- cycle validation
- timeout/retry validation
- validation error code

Parser trace에는 전체 prompt를 중복 저장하지 않고 다음처럼 구조화된 결과만 기록합니다.

```json
{
  "valid": false,
  "error_code": "UNKNOWN_CAPABILITY",
  "task_count": 4,
  "unknown_capabilities": ["fly"]
}
```

### MissionOrchestrator

파일:

```text
src/robo_claw_maestro/application/mission_orchestrator.py
```

계측 대상:

- `create`
- `start`, `pause`, `resume`, `cancel`
- `run_round`
- `run_until_settled`
- `replace_plan`
- `reconcile_all`

Mission 상태 변경의 source of truth는 계속 DB event입니다. LangSmith에는 상태, duration, 선택 이유와 correlation ID만 복제합니다.

### FleetScheduler

파일:

```text
src/robo_claw_maestro/application/scheduler.py
```

계측 대상:

- candidate filtering
- safety policy decision
- battery/map/health/pose 제외 이유
- candidate score
- 최종 Robot 선택 이유

Robot별 raw telemetry 전체 대신 scheduling 시점의 작은 snapshot만 기록합니다.

```json
{
  "robot_id": "former-01",
  "eligible": false,
  "reason": "pose_stale",
  "battery_bucket": "50-75",
  "queue_depth": 1,
  "map_id": "floor-1"
}
```

### TaskDispatcher와 RobotGateway

파일:

```text
src/robo_claw_maestro/application/task_dispatcher.py
src/robo_claw_maestro/adapters/grpc/ros_grpc_client.py
src/robo_claw_maestro/adapters/grpc/fleet_control_server.py
```

기록 정보:

```text
command_id
mission_id
task_id
attempt_id
correlation_id
idempotency_key
robot_id
transport
command_type
deadline
ack latency
result latency
result status
error code
```

Skill parameter, 자연어 instruction, image payload는 기본 metadata에서 제외하거나 redaction합니다.

## Trace Correlation 규칙

모든 root trace에 다음 metadata를 사용합니다.

```json
{
  "component": "maestro",
  "mission_id": "...",
  "task_id": "...",
  "command_id": "...",
  "attempt_id": "...",
  "correlation_id": "...",
  "robot_id": "...",
  "transport": "ros_grpc|fleet_control",
  "plan_version": 1
}
```

권장 tag:

```text
component:maestro
mission:<mission-id>
robot:<robot-id>
transport:fleet-control
provider:ollama
status:failed
```

## Robot trace와 연결하는 방법

Maestro와 Robot은 서로 다른 process이므로 LangSmith context가 자동 전파되지 않습니다.

1차 구현에서는 다음 공통 ID로 trace를 검색해 연결합니다.

```text
mission_id
task_id
command_id
attempt_id
correlation_id
robot_id
```

2차 구현에서는 FleetControl command에 선택적 trace context 필드를 추가할 수 있습니다.

```protobuf
message TraceContext {
  string trace_id = 1;
  string parent_run_id = 2;
}
```

RosGrpc는 기존 public proto 호환성을 유지해야 하므로 우선 correlation ID 기반 연결만 사용합니다.

## 입력과 출력 Redaction

반드시 제거할 key 패턴:

```text
token
api_key
authorization
password
secret
credential
image_base64
file_content
map_data
certificate
private_key
```

크기 제한 권장값:

```text
문자열: 최대 8 KiB
리스트: 최대 100개
중첩 깊이: 최대 8
LLM response: 최대 32 KiB
```

기록하지 않을 데이터:

- camera base64
- occupancy grid raw bytes
- embedding vector 전체
- 사용자 업로드 파일 내용
- mTLS private key/certificate 원문
- authorization header
- Robot credential

## 비활성 상태 호환성 보장

다음 설정이 기본값입니다.

```dotenv
MAESTRO_LANGSMITH_TRACING=false
```

비활성 상태에서 다음을 테스트합니다.

1. LangSmith package가 없어도 import할 수 있습니다.
2. 원래 sync/async 함수가 한 번만 호출됩니다.
3. 원래 반환 객체 identity가 유지됩니다.
4. 원래 예외 type과 message가 유지됩니다.
5. Mission/Task 상태 전이가 동일합니다.
6. Scheduler 선택 결과가 동일합니다.
7. RobotGateway 호출 횟수가 동일합니다.
8. emergency stop 경로가 tracing client를 호출하지 않습니다.
9. tracing 환경변수 변경이 domain layer에 영향을 주지 않습니다.
10. redaction은 전송용 복사본만 변경합니다.

## 테스트 계획

### Unit

```text
test_disabled_tracing_preserves_sync_result
test_disabled_tracing_preserves_async_result
test_disabled_tracing_preserves_exception
test_redaction_removes_credentials_and_binary
test_trace_metadata_has_correlation_ids
test_domain_does_not_import_langsmith
```

### Component

```text
test_goal_trace_contains_provider_and_validation_children
test_invalid_plan_trace_records_validation_error
test_scheduler_trace_records_candidate_reasons
test_dispatch_trace_records_command_identity
test_replan_trace_increments_plan_version
test_emergency_stop_does_not_wait_for_trace_upload
```

### Integration

```text
test_ollama_timeout_trace
test_fleet_control_ack_result_correlation
test_ros_grpc_unknown_result_trace
test_startup_reconciliation_trace
test_langsmith_unavailable_does_not_fail_api
```

실제 LangSmith SaaS를 사용하는 테스트는 기본 suite에 넣지 않고 opt-in marker로 분리합니다.

```bash
pytest -m langsmith
```

## 구현 상태

```text
1. optional tracing dependency group                    완료
2. no-op tracing wrapper와 redaction                     완료
3. 비활성 호환성 unit test                               완료
4. MaestroAgent/Provider/Parser 계측                      완료
5. Orchestrator/Scheduler 계측                            완료
6. Dispatcher/RosGrpc/FleetControl 계측                  완료
7. goal/mission 제어 API request root trace              완료
8. Robot/mission/task/correlation metadata                완료
9. Prometheus planning metric 유지                       완료
10. 실제 LangSmith project 수동 검증                     사용자 확인 필요
```

## 사용자가 확인해야 하는 시점

코드 구현과 fake 기반 테스트 완료 후 다음 수동 검증이 필요합니다.

1. `.env`에서 `MAESTRO_LANGSMITH_TRACING=true`를 설정합니다.
2. 테스트 전용 `LANGSMITH_PROJECT=robo-claw-maestro-test`를 사용합니다.
3. `/api/v1/goals`로 정상 goal을 전송합니다.
4. LangSmith에서 provider → parser → mission 저장 child tree를 확인합니다.
5. 잘못된 capability plan을 전송해 validation error를 확인합니다.
6. Robot이 연결된 상태에서 scheduling/dispatch trace를 확인합니다.
7. `MAESTRO_LANGSMITH_TRACING=false`로 재시작합니다.
8. 동일 API 결과와 Robot command 횟수가 유지되는지 확인합니다.

## 구현하지 않을 범위

- heartbeat마다 LangSmith run 생성
- pose telemetry마다 LangSmith run 생성
- camera frame trace 업로드
- map raw data 업로드
- Prometheus metric 대체
- DB audit event 대체
- emergency stop 동기 upload

## 최종 판단

Maestro LangSmith tracing은 LLM plan validation과 deterministic Scheduler 사이의 경계를 중심으로 구현됐습니다. 저빈도 의사결정 trace로 범위를 제한하고 기본 비활성, 데이터 redaction, emergency stop 비차단 조건을 유지합니다. 다음 단계는 실제 LangSmith project에서 run tree와 비활성 동작 동일성을 사용자가 확인하는 것입니다.
