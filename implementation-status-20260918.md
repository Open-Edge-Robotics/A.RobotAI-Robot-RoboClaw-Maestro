# PLAN 기준 구현 현황

이 문서는 `PLAN.md`의 단계별 작업 및 완료 조건과 현재 구현을 비교한 2026-09-18 기준 상태입니다.

## Phase 0 — 프로젝트 기반

**판정: 구현 완료**

구현된 항목:

- Python package/test 구조
- Ruff, pytest, coverage 설정
- `.env.example`
- Docker Compose
- PostgreSQL
- API Dockerfile
- Alembic
- ADR 5종
- `/health`, `/ready`
- SQLite 자동 migration
- PostgreSQL 명시적 migration

남은 항목:

- CI workflow 파일은 아직 없습니다.

## Phase 1 — Domain Model과 상태 머신

**판정: 대부분 완료**

구현된 항목:

- Robot, Mission, Task, Command, Event
- Mission/Task 상태 머신
- 잘못된 상태 전이 차단
- dependency DAG 및 cycle 검사
- Command idempotency key
- DB unique idempotency 제약
- repository port
- in-memory repository
- SQLModel SQLite/PostgreSQL adapter
- Alembic migration
- 상태 전이 event
- Command journal
- 재시작 후 aggregate 복구
- duplicate/rollback 테스트

현재 migration:

```text
0001_initial
0002_commands
0003_resource_leases
0004_approvals
```

남은 항목:

- aggregate 변경과 event append를 완전히 하나의 DB transaction으로 묶는 Unit of Work
- Command 자체의 ACK/SENT 상태 변경 영속화
- 모든 API 호출에 대한 별도 AuditLog 모델

따라서 Phase 1은 기능적으로 대부분 구현됐지만 엄격한 transactional outbox 수준은 아닙니다.

## Phase 2 — 기존 Robo-Claw 연결 PoC

**판정: MVP 완료**

구현된 항목:

- `RosGrpcRobotGateway`
- Robot별 정적 endpoint
- `Ping`
- `GetRobotInfo`
- `GetRobotStatus`
- `GetRobotStatusStream`
- `ExecuteTask`
- `ExecuteSkill`
- `EmergencyStop`
- status stream 자동 monitor
- exponential reconnect backoff
- stale/offline 판정
- reconnect session 갱신
- 실제 protobuf/gRPC fake robot server
- fake gRPC robot 3대 동시 연결
- 실제 stream disconnect/restart 테스트
- Fleet emergency stop 부분 실패 처리

남은 제약:

- 기존 RosGrpc에는 구조화 capability RPC가 없습니다.
- 기존 RosGrpc에는 일반 Task cancel contract가 없습니다.
- 실제 boot/session ID가 없어 RosGrpc 경로에서는 Maestro가 session ID를 생성합니다.

## Phase 3 — Robot Registry와 Telemetry API

**판정: 대부분 완료**

구현된 항목:

- Robot Registry
- snapshot DB 저장
- telemetry event
- event retention
- retention background job
- Robot 목록/상세
- Robot event
- 직접 Task
- 직접 Skill
- Robot emergency stop
- Fleet emergency stop
- Mission API
- WebSocket event
- read/control/admin token 역할 분리
- camera snapshot API

현재 주요 API:

```text
GET  /api/v1/robots
GET  /api/v1/robots/{robot_id}
GET  /api/v1/robots/{robot_id}/events
GET  /api/v1/robots/{robot_id}/snapshot
POST /api/v1/robots/{robot_id}/tasks
POST /api/v1/robots/{robot_id}/skills/{skill_name}
POST /api/v1/robots/{robot_id}/emergency-stop
POST /api/v1/fleet/emergency-stop
WS   /api/v1/events/stream
```

남은 항목:

- 외부 IdP/OIDC 기반 인증
- 분산 환경용 공유 WebSocket event bus
- telemetry 장기 집계/downsampling

## Phase 4 — Mission Orchestrator와 Scheduler

**판정: 핵심 구현, 일부 완료 조건 미충족**

구현된 항목:

- Mission create/start/pause/resume/cancel
- ready Task 계산
- capability filtering
- map 검사
- battery safety
- sensor health
- stale pose 검사
- queue/battery/distance scoring
- 선택 이유 event 기록
- 독립 Task 병렬 실행
- dependency 순차 실행
- deadline
- timeout의 `UNKNOWN` 처리
- retry budget
- 다른 Robot 재배정
- 실행 Robot 이력
- DB 영속 resource lease
- startup reconciliation

남은 항목:

- RosGrpc ACK와 실행 결과 분리
- 실행 중 원격 Task cancel
- 원격 pause/resume
- 자동 scheduling worker loop
- 다중 Maestro instance 사이의 scheduler leader election
- 운영 규모 lock 경합 테스트

## Phase 5 — Maestro LLM Agent

**판정: 대부분 완료**

구현된 항목:

- LLM provider port
- Fake provider
- Ollama adapter
- Mission plan parser
- Markdown fence 처리
- 잘못된 JSON 거부
- 최대 Task 수
- capability 검증
- Robot ID 검증
- DAG cycle 검증
- timeout/retry budget
- prompt injection 검증
- provider timeout 처리
- application tool facade
- bounded replanning
- `plan_version` 증가
- planning metric
- 자연어 goal API

현재 API:

```text
POST /api/v1/goals
POST /api/v1/missions/{mission_id}/replan
GET  /api/v1/planning/metrics
```

남은 항목:

- OpenAI adapter
- 명시적인 JSON Schema 파일
- 실제 provider token usage 연동
- Mission 실패에 따른 자동 replanning trigger
- 더 정교한 observation tool

## Phase 6 — Maestro Console

**판정: 부분 구현**

구현된 항목:

- React + TypeScript
- Fleet overview
- Robot 상태/배터리/위치/capability
- Mission 목록
- Task dependency 표시
- Event timeline
- 자연어 goal 입력
- 직접 Robot Task
- Mission lifecycle 제어
- Robot/Fleet emergency stop
- 승인 대기 목록
- 승인/거부
- camera snapshot modal
- API token 설정
- 반응형 UI
- Docker/Nginx reverse proxy

남은 항목:

- MapLibre/Leaflet 실제 지도
- React Flow 기반 DAG
- 독립 Robot 상세 페이지
- camera polling/stream 최적화
- offline과 stale의 시각적 세분화
- UI 자동 테스트

## Phase 7 — FleetControl Protocol

**판정: Maestro 측 PoC 완료, 전체 Phase는 미완료**

Maestro 구현 항목:

- `fleet_control.proto`
- 양방향 stream server
- outbound Robot registration
- protocol version 검사
- session 관리
- heartbeat/telemetry
- command ACK
- TaskUpdate
- Task/Skill/Emergency stop/Snapshot command
- queue limit/backpressure
- stream disconnect 처리
- TLS/mTLS
- 인증서 identity와 Robot ID 확인
- RosGrpc/FleetControl hybrid gateway
- fake outbound client
- 동적 Robot 등록 테스트

남은 핵심 항목:

- 실제 `robo-claw` runtime outbound connector
- Robo-Claw SkillManager 기반 capability manifest
- 실제 AgentStatus/health/pose/queue telemetry
- robot-side SQLite command dedup
- 실제 cancel/pause/resume handler
- reconnect 후 미전송 결과 복구
- 구버전 compatibility 정책 확장

따라서 FleetControl은 Maestro와 fake robot 사이에서는 동작하지만 실제 Robo-Claw에는 아직 통합되지 않았습니다.

## Phase 8 — 운영 안정화

**판정: 부분 구현**

구현된 항목:

- mTLS server 옵션
- read/control/admin 역할
- Prometheus metric
- JSON structured logging
- rate limit
- event retention
- DB backup/restore 문서
- reconnect fault test
- 운영 장애 대응 문서

현재 metric:

```text
maestro_connected_robots
maestro_websocket_clients
maestro_grpc_reconnect_total
maestro_task_result_total
maestro_emergency_stop_seconds
maestro_llm_requests_total_snapshot
maestro_llm_validation_errors_total_snapshot
```

남은 항목:

- OpenTelemetry exporter
- 인증서 발급/자동 교체
- 실제 restore 자동 테스트
- 부하 테스트
- 장시간 soak test
- network fault injection suite
- alert rule과 dashboard
- 다중 API worker 지원

## MVP 완료 정의 기준

| 조건 | 상태 |
| --- | --- |
| fake robot 3대 동시 관리 | 충족 |
| 실시간 Robot 상태 | 충족 |
| 자연어 목표를 검증된 Mission DAG로 변환 | 조건부 충족 |
| capability 기반 Task 배정 | 충족 |
| dependency 기반 순차 및 독립 Task 병렬 실행 | 충족 |
| 성공, 실패, timeout, unknown, cancel 추적 | 부분 충족 |
| retry budget과 재배정 | 충족 |
| 중복 Command 방지 | 부분 충족 |
| Robot별 및 Fleet emergency stop | 충족 |
| 재시작 후 복구 및 reconciliation | 충족 |
| 모든 명령과 상태 변경 audit | 부분 충족 |
| 단위·컴포넌트·계약·통합 테스트 | 충족 |
| 실제 Robot 2대 이상 인수 테스트 | 미충족 |
| 운영 및 장애 대응 문서 | 충족 |

현재 상태는 **fake fleet 기준 backend MVP와 초기 Console은 동작하지만, PLAN의 최종 MVP 완료 상태는 아닙니다.**

최종 MVP 판정을 위해 중요한 남은 항목은 다음과 같습니다.

1. 실제 Robo-Claw outbound FleetControl connector
2. 실제 Robot의 영속 command dedup
3. 실행 중 Task cancel contract
4. aggregate/event atomic transaction
5. 실제 Robot 2대 이상 인수 테스트

## 검증 상태

2026-09-18 기준 마지막 검증 결과:

```text
pytest: 85 passed
ruff check: passed
ruff format --check: passed
npm run typecheck: passed
npm run build: passed
Alembic head: 0004_approvals
Docker Compose config: passed
git diff --check: passed
```

pytest 경고 2개는 FastAPI TestClient가 사용하는 Starlette/httpx deprecation warning이며 기능 실패는 아닙니다.
