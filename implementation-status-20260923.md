# PLAN 대비 구현 현황

이 문서는 `PLAN.md`의 Phase 0~8 및 MVP 완료 정의와 현재 `robo-claw-maestro`, `robo-claw` 구현을 2026-09-23 기준으로 비교한 결과입니다. 판정은 파일 존재 여부가 아니라 PLAN의 완료 조건 충족 여부를 기준으로 합니다.

## 요약

| Phase | 판정 | 요약 |
| --- | --- | --- |
| Phase 0 | 완료 | 프로젝트, Docker Compose, DB migration, health/readiness, ADR 구현 |
| Phase 1 | 대부분 완료 | Domain, 상태 머신, 저장소, idempotency, event, 복구 구현. 원자적 Unit of Work 보강 필요 |
| Phase 2 | MVP 완료 | 기존 RosGrpc 기반 다중 Robot 연결·stream·reconnect·fake fleet 검증 완료 |
| Phase 3 | 대부분 완료 | Registry, telemetry/event, REST, WebSocket, read/control/admin 역할 구현 |
| Phase 4 | 핵심 구현 | Scheduler, 병렬/dependency, retry/reassignment, lease, reconciliation 구현. 원격 cancel/자동 worker 미완료 |
| Phase 5 | 대부분 완료 | Ollama planning, validation, replanning, tools, metric, LangSmith trace 구현 |
| Phase 6 | 부분 구현 | React Console, 승인, snapshot, event, emergency UI 구현. 지도/DAG 상세 화면 미완료 |
| Phase 7 | 코드 구현·실기기 검증 대기 | Maestro FleetControl과 실제 Robo-Claw outbound connector 구현. 실제 Robot 인수와 cancel/AgentStatus 미완료 |
| Phase 8 | 부분 구현 | mTLS, 역할 분리, Prometheus, JSON log, rate limit, retention, LangSmith, runbook 구현 |

## Phase 0 — 프로젝트 기반과 ADR

**판정: 완료**

구현된 항목:

- `pyproject.toml` 및 package/test 구조
- Ruff, pytest, coverage 설정
- `.env.example`
- API/PostgreSQL/Web Docker Compose
- API 및 Web Dockerfile
- Alembic migration
- SQLite 개발 환경 자동 migration
- `/health`, `/ready`
- ADR-001~005
- README 검증 절차

현재 Alembic revision:

```text
0001_initial
0002_commands
0003_resource_leases
0004_approvals
```

남은 항목:

- 저장소 자체 CI workflow 파일

## Phase 1 — Domain Model과 상태 머신

**판정: 대부분 완료**

구현된 항목:

- Robot, Mission, Task, Command, Event
- Approval 및 ResourceLease 추가 domain
- Mission/Task 상태 머신
- 잘못된 전이 차단
- dependency DAG 및 cycle validation
- Command idempotency key
- DB unique idempotency 제약
- repository port
- in-memory adapter
- SQLModel SQLite/PostgreSQL adapter
- 상태 전이 event
- Command journal
- 재시작 후 Mission/Task/Command/Event 복구
- duplicate 및 rollback 테스트

남은 항목:

- aggregate 변경과 event append를 같은 transaction으로 처리하는 Unit of Work
- Command `CREATED/SENT/ACKNOWLEDGED/COMPLETED` 상태 영속 전이
- API 요청 전체를 포괄하는 별도 AuditLog entity
- transactional outbox

## Phase 2 — 기존 Robo-Claw 연결 PoC

**판정: MVP 완료**

구현된 항목:

- `RosGrpcRobotGateway`
- Robot별 정적 endpoint
- `Ping`, `GetRobotInfo`
- `GetRobotStatus`, `GetRobotStatusStream`
- `ExecuteTask`, `ExecuteSkill`
- `GetRobotCameraImage`
- `EmergencyStop`
- Robot별 lazy gRPC channel
- status stream monitor
- exponential reconnect backoff
- stale/offline 판정
- reconnect session 갱신
- 실제 protobuf/gRPC fake Robot server
- fake gRPC Robot 3대 동시 연결
- stream disconnect/restart 통합 테스트
- Fleet emergency stop 부분 실패 처리

남은 제약:

- 기존 RosGrpc에 구조화 capability RPC가 없음
- 기존 RosGrpc에 일반 Task cancel contract가 없음
- 실제 Robot boot/session ID가 없어 Maestro가 RosGrpc session을 생성

## Phase 3 — Robot Registry와 Telemetry API

**판정: 대부분 완료**

구현된 항목:

- Robot Registry
- Robot snapshot DB 저장
- telemetry/connection event
- event retention 및 background cleanup
- Robot 목록/상세/event API
- 직접 Task/Skill API
- Robot/Fleet emergency stop
- camera snapshot API
- Mission API
- Fleet WebSocket event
- read/control/admin bearer 역할
- 위험 Skill approval workflow
- Approval DB 영속화 및 재시작 복구

남은 항목:

- OIDC/외부 IdP 연동
- 다중 API process용 shared WebSocket event bus
- telemetry downsampling 및 장기 집계
- distributed rate limit

## Phase 4 — Mission Orchestrator와 Scheduler

**판정: 핵심 구현, 완료 조건 일부 미충족**

구현된 항목:

- Mission create/start/pause/resume/cancel
- ready Task 계산
- dependency 순차 실행
- 독립 Task 병렬 실행
- capability filtering
- battery/map/map version/frame/sensor/pose safety policy
- queue depth, battery, 거리 scoring
- Scheduler 선택 이유 event
- Task deadline
- 통신 timeout의 `UNKNOWN` 처리
- retry budget
- 이전 Robot 제외 및 재배정
- Robot 실행 이력
- DB 영속 resource lease
- startup reconciliation
- Maestro 재시작 후 `UNKNOWN` Task 조정

남은 항목:

- 기존 RosGrpc ACK와 최종 결과 분리
- 실행 중 실제 원격 Task cancel
- 실제 pause/resume handler
- READY Mission을 자동 처리하는 background scheduling worker
- 다중 worker ownership/leader election
- 운영 규모 resource lock 경합 및 lease renewal 검증

## Phase 5 — Maestro LLM Agent

**판정: 대부분 완료**

구현된 항목:

- LLM provider port
- Fake provider
- Ollama adapter
- Mission Plan JSON parser
- Markdown fence 처리
- 잘못된 JSON 거부
- 최대 Task 수
- capability/Robot ID validation
- dependency cycle validation
- timeout/retry budget validation
- application tool facade
- bounded replanning
- `plan_version` 증가
- provider/validation/latency/token 추정 metric
- prompt injection 및 provider timeout 테스트
- `/api/v1/goals`
- `/api/v1/missions/{id}/replan`
- `/api/v1/planning/metrics`
- optional LangSmith trace

LangSmith 계측 범위:

```text
maestro_api_request
├─ maestro_goal_request
│  └─ create_mission_plan
│     ├─ llm_provider.complete
│     │  └─ OllamaProvider.complete
│     └─ validate_mission_plan
│        ├─ decode_plan_json
│        └─ validate_plan_task
└─ persist_mission

mission_scheduling_round
├─ assign_ready_tasks
│  └─ score_robot_candidates
│     └─ evaluate_safety_policy
└─ dispatch_task
   ├─ ros_grpc.execute_task 또는
   └─ fleet_control.execute_task
```

LangSmith 안전 조건:

- `MAESTRO_LANGSMITH_TRACING=false` 기본값
- 비활성 시 원래 sync/async 반환값과 예외 유지
- credential/token/image/base64 redaction
- goal/instruction/provider 원문은 기본적으로 hash/길이만 기록
- emergency stop은 trace upload 대기 없음
- domain layer에서 LangSmith import 금지

남은 항목:

- OpenAI provider adapter
- provider 실제 token usage 통합
- 실패 event 기반 자동 replanning trigger
- 명시적인 외부 JSON Schema 파일
- 실제 LangSmith project 수동 검증

## Phase 6 — Maestro Console

**판정: 부분 구현**

구현된 항목:

- React + TypeScript
- Fleet overview
- Robot 상태, 배터리, 위치, capability
- Mission/Task dependency 표시
- Event timeline
- 자연어 goal 입력
- 직접 Robot Task
- Mission start/run/pause/resume/cancel
- Robot/Fleet emergency stop
- 위험 Skill 승인 대기/승인/거부
- camera snapshot modal
- API token 설정
- 반응형 UI
- Nginx API/WebSocket reverse proxy

남은 항목:

- MapLibre/Leaflet 실제 지도
- React Flow 기반 DAG
- 별도 Robot 상세 화면
- stale/offline 세분화 표시
- camera polling/stream 최적화
- UI unit/E2E 테스트

## Phase 7 — FleetControl Protocol

**판정: 코드 구현 완료, 실제 Robot 검증 대기**

Maestro 구현:

- `fleet_control.proto`
- 양방향 stream server
- outbound Robot registration
- protocol version 검사
- session 교체
- heartbeat/telemetry
- capability manifest 저장
- site/map/map version/frame 관리
- Command ACK/TaskUpdate
- Task/Skill/EmergencyStop/Snapshot command
- queue limit/backpressure
- stream disconnect 처리
- TLS/mTLS
- certificate identity와 Robot ID 검사
- RosGrpc/FleetControl hybrid gateway

Robo-Claw 구현:

- `robo_claw_grpc` outbound FleetControl connector
- `MAESTRO_IP`, `ROBOT_PORT`, `ROBOT_ID`
- `ROBOT_SITE_ID`, `ROBOT_MAP_ID`, `ROBOT_MAP_VERSION`, `ROBOT_MAP_FRAME_ID`
- `rclaw launch --docker --use-grpc` 환경 전달 경로
- `SKILLS.md` capability manifest builder
- heartbeat 및 battery/pose/map telemetry
- Task/Skill/Snapshot/EmergencyStop handler
- SQLite idempotency/result journal
- process 재시작 후 duplicate 차단
- reconnect 후 result replay
- `complete`, `stop`, `finish_atomic` disconnect 정책
- insecure 및 TLS/mTLS client
- 실제 loopback FleetControl 통합 테스트

남은 항목:

- 실제 Robot 1대 읽기 전용 인수 테스트
- 실제 Robot 2대 Fleet 인수 테스트
- AgentStatus/current skill/queue depth 상세 연결
- runtime SkillManager 목록 직접 교차 검증 wiring
- ROS action 실제 cancel
- pause/resume/config update handler
- Maestro result receipt 기반 journal delivered 확정
- 실기기 certificate mount/mTLS 검증

## Phase 8 — 운영 안정화

**판정: 부분 구현**

구현된 항목:

- FleetControl TLS/mTLS server/client
- read/control/admin 역할
- Prometheus metric
- optional LangSmith trace
- JSON structured logging
- control API rate limit
- event retention
- PostgreSQL backup/restore 문서
- reconnect fault test
- 운영 장애 대응 runbook

남은 항목:

- OpenTelemetry exporter
- 인증서 발급/교체 자동화
- 실제 DB restore 자동 테스트
- network fault injection suite
- 부하/soak 테스트
- Grafana dashboard 및 alert rule
- 다중 API worker
- shared rate limit/event bus

## MVP 완료 정의 대비 현황

| MVP 조건 | 판정 | 근거 또는 남은 작업 |
| --- | --- | --- |
| fake/simulated Robot 3대 동시 관리 | 충족 | in-memory 및 실제 loopback gRPC 테스트 |
| Robot 상태와 연결 상태 실시간 표시 | 충족 | RosGrpc stream, FleetControl telemetry, WebSocket, Console |
| 자연어 목표를 검증된 Mission/Task로 변환 | 조건부 충족 | Ollama 설정 시 동작, fake provider 테스트 완료 |
| capability 기반 Task 배정 | 충족 | health/battery/map/pose safety 포함 |
| dependency 순차 및 독립 Task 병렬 실행 | 충족 | component/E2E 테스트 |
| 성공, 실패, timeout, unknown, cancel 추적 | 부분 충족 | 로컬 상태 지원, 실제 원격 cancel 미완료 |
| retry budget과 재배정 | 충족 | 이전 Robot 제외 및 budget 테스트 |
| 중복 Command 방지 | 구현·실기기 검증 대기 | Maestro unique key와 Robo-Claw SQLite restart dedup 테스트 |
| Robot별 및 Fleet emergency stop | 충족 | 병렬 전송 및 부분 실패 응답 |
| 재시작 후 Mission/Task 복구 | 충족 | DB 복구와 startup reconciliation |
| 모든 명령과 상태 변경 audit | 부분 충족 | 주요 event 기록, atomic Unit of Work 필요 |
| 단위·컴포넌트·계약·통합 테스트 | 충족 | 기본 suite에서 외부 Robot/LLM 미사용 |
| 실제 Robot 2대 인수 테스트 | 미충족 | connector 코드 완료, 실기기 검증 대기 |
| 운영 및 장애 대응 문서 | 충족 | operations/protocol/test manual 제공 |

따라서 현재 상태는 **fake fleet 기준 backend MVP, 초기 Console, 실제 Robo-Claw FleetControl connector 코드까지 구현됐지만 최종 MVP 완료 상태는 아닙니다.** 실제 Robot 읽기 전용 인수, 실제 cancel, AgentStatus 통합과 실기기 중복 방지 확인이 필요합니다.

## 현재 사용자 개입 시점

현재가 실제 Robot 1대 읽기 전용 인수 테스트 시점입니다.

확인 항목:

```text
Robot registration
connection_state=ONLINE
고유 session_id
heartbeat
battery
pose
site_id/map_id/map_version/frame_id
capability manifest
get_status
camera snapshot
process 종료 후 OFFLINE
재시작 후 새 session_id와 ONLINE 복구
```

이 단계에서는 다음을 실행하지 않습니다.

```text
navigate_to
move_relative
manipulation
dangerous skill
automatic retry
parallel motion
```

상세 절차는 `implementation-status-20260922.md`의 "현재 사용자 확인이 필요한 실제 Robot 1대 테스트"를 참고합니다.

## 검증 결과

2026-09-23 기준:

```text
Maestro backend: 92 passed
Maestro Ruff: passed
Maestro frontend typecheck/build: passed
Maestro Docker Compose config: passed
Robo-Claw Fleet connector: 6 passed
Robo-Claw detailed tracing related tests: 35 passed, 2 skipped
Robo-Claw connector/tracing Ruff: passed
Alembic head: 0004_approvals
```

알려진 검증 환경 제한:

- Robo-Claw 전체 legacy service test는 현재 별도 실행 환경의 `psutil`/ROS dependency가 필요합니다.
- `task contract-check`는 Go 실행 파일이 있는 환경에서 추가 확인해야 합니다.
- 실제 Robot motion과 hardware acceptance는 수행하지 않았습니다.
