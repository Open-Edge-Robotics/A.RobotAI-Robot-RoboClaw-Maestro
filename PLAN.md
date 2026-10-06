# Robo-Claw Maestro 개발 계획

## 1. 문서 목적

이 문서는 여러 대의 Robo-Claw 로봇을 중앙에서 모니터링하고, 전체 목표를 여러 작업으로 분해하여 각 로봇에 배정·추적·재계획하는 **Robo-Claw Maestro**의 개발 기준과 단계별 실행 계획을 정의한다.

다른 개발 에이전트는 구현을 시작하기 전에 이 문서와 다음 Robo-Claw 파일을 함께 확인해야 한다.

- `../robo-claw/AGENTS.md`
- `../robo-claw/README.md`
- `../robo-claw/docs/PEER_COLLABORATION.md`
- `../robo-claw/docs/OPERATIONS.md`
- `../robo-claw/src/robo_claw_msgs/proto/messenger.proto`
- `../robo-claw/src/robo_claw_msgs/proto/robo.proto`
- `../robo-claw/src/robo_claw_channel/robo_claw_channel/grpc_server.py`
- `../robo-claw/src/robo_claw_channel/robo_claw_channel/client_node.py`
- `../robo-claw/src/robo_claw_grpc/robo_claw_grpc/services/robot_status_service.py`
- `../robo-claw/src/robo_claw_grpc/robo_claw_grpc/services/robot_task_service.py`
- `../robo-claw/src/robo_claw_agent/robo_claw_agent/skills/cooperate_skill.py`
- `../robo-claw/src/robo_claw_agent/robo_claw_agent/skills/cooperation_skill/`

---

## 2. 제품 정의

Robo-Claw Maestro는 개별 로봇에 설치된 `robo-claw` 에이전트들을 하나의 호스트에서 관리하는 fleet orchestration 시스템이다.

사용자는 Maestro에 자연어로 전체 목표를 전달한다. Maestro Agent는 목표를 구조화된 Mission과 Task로 분해하며, 결정론적인 Scheduler가 각 로봇의 상태·역량·위치·배터리·작업량을 바탕으로 작업을 배정한다. 각 Robo-Claw Agent는 배정된 작업을 자신의 LLM과 스킬을 이용해 수행하고 진행 상태와 결과를 Maestro에 보고한다.

```text
사용자 / 운영자
      │
      ▼
Robo-Claw Maestro
  ├─ API 및 관제 UI
  ├─ Maestro LLM Agent
  ├─ Mission Orchestrator
  ├─ Fleet Scheduler
  ├─ Robot Registry
  ├─ Safety Policy
  └─ Telemetry / Event Store
      │
      ├──────── gRPC ──────── Robo-Claw Agent A ── ROS 2 ── Robot A
      ├──────── gRPC ──────── Robo-Claw Agent B ── ROS 2 ── Robot B
      └──────── gRPC ──────── Robo-Claw Agent C ── ROS 2 ── Robot C
```

### 핵심 원칙

1. **LLM은 목표 해석·계획·재계획을 담당한다.**
2. **상태 전이·검증·재시도·중복 방지·자원 잠금은 결정론적인 코드가 담당한다.**
3. **자연어 메시지는 제어 프로토콜이 아니라 작업 의미를 전달하는 payload다.**
4. **개별 Robo-Claw의 로컬 안전 정책이 최종 제어 권한을 갖는다.**
5. **네트워크 timeout은 작업 실패로 단정하지 않고 `UNKNOWN` 상태로 재조정한다.**
6. **모든 제어 명령과 상태 변경은 추적 가능한 ID와 감사 로그를 갖는다.**
7. **초기에는 기존 Robo-Claw API를 재사용하고, 이후 전용 Fleet 프로토콜로 확장한다.**

---

## 3. 현재 Robo-Claw 구현 분석

### 3.1 재사용 가능한 기능

| 기능 | 기존 구현 | Maestro 활용 |
| --- | --- | --- |
| 자연어 작업 실행 | `ExecuteTask` ROS action 및 `RosGrpc.ExecuteTask` | 로봇에 고수준 작업 위임 |
| 직접 스킬 실행 | `ExecuteSkill` | 결정론적 단일 작업 실행 |
| 로봇 정보 | `GetRobotInfo` | 초기 Registry 구성 |
| 상태 조회 | `GetRobotStatus`, `GetRobotStatusStream` | Telemetry 수집 |
| 비상 정지 | `EmergencyStop` | 로봇별·전체 정지 |
| 카메라·지도 | `GetRobotCameraImage`, `GetRobotMap` | 관제 UI 및 상황 확인 |
| 로컬 작업 직렬화 | `TaskQueue` | 로봇 내부 동시 명령 보호 |
| 로컬 안전 | limits, sensor health, emergency skills | 최종 실행 안전성 보장 |
| peer 통신 | `RoboMessenger`, peer skills | PoC 및 대화 기능 참고 |
| 중복 메시지 방지 | peer SQLite dedup | Fleet command idempotency 설계 참고 |

### 3.2 현재 구조를 그대로 사용하기 어려운 부분

1. `messenger.proto`의 `ChatMessage.content` 중심 통신은 mission/task 상태를 안정적으로 관리하기 어렵다.
2. `GRPC_TARGET_PEERS_JSON`은 정적 설정이며, 로봇 수가 늘면 peer 간 연결이 복잡해진다.
3. 중앙 Mission 저장소와 Task DAG가 없다.
4. `RobotStatus.current_task`는 실제 Agent 상태보다 단순하며 `IDLE`/`NAVIGATING` 중심이다.
5. Agent에는 `THINKING`, `EXECUTING`, `WAITING`, `ERROR`, 현재 스킬, 큐 메트릭이 있지만 Fleet telemetry로 통합되어 있지 않다.
6. 실행 중인 일반 작업에 대한 일관된 cancel contract가 없다.
7. `robo_claw_grpc`는 insecure port를 사용하며 RPC 수준 인증·권한 분리가 부족하다.
8. capability 조회가 일부 경로에서 LLM 자연어 응답 파싱에 의존한다.
9. 카메라 snapshot을 일반 상태 스트림에 포함하면 다수 로봇 환경에서 네트워크 부하가 커질 수 있다.
10. Maestro나 로봇 재시작 후 진행 중 작업을 재조정하는 reconciliation 절차가 없다.

### 3.3 결론

- **초기 PoC:** Maestro가 기존 `RosGrpc` 서버에 접속하는 client 구조로 빠르게 구현한다.
- **운영 프로토콜:** 로봇이 Maestro로 outbound 양방향 스트림을 생성하는 전용 `FleetControl` 프로토콜을 추가한다.
- 기존 peer 자연어 협업은 제거하지 않지만, Maestro의 핵심 제어 plane으로 사용하지 않는다.

---

## 4. 범위

### 4.1 MVP 포함 범위

- 최대 5대 로봇 정적 등록
- 로봇 online/degraded/offline 상태 관리
- 위치, 배터리, 시스템 상태, 현재 작업 모니터링
- 사용자 자연어 목표 입력
- 구조화된 Mission/Task 생성
- capability 기반 로봇 선택
- 독립 Task 병렬 실행 및 dependency 기반 순차 실행
- `ExecuteTask`와 `ExecuteSkill`을 통한 작업 전달
- 작업 결과·실패·timeout 기록
- 제한된 retry 및 다른 로봇으로 재배정
- 로봇별 stop 및 전체 emergency stop
- PostgreSQL 기반 상태·이벤트·감사 로그 저장
- WebSocket 기반 관제 UI 갱신
- fake robot을 이용한 무하드웨어 통합 테스트

### 4.2 MVP 제외 범위

- 다중 로봇 실시간 궤적 최적화
- 로봇 간 직접 충돌 회피 알고리즘
- 엘리베이터 전체 제어 시스템
- 다중 사이트 및 다중 테넌트
- 고가용성 Maestro cluster
- Kafka 기반 대규모 이벤트 처리
- 모든 카메라의 상시 고화질 영상 스트리밍
- LLM이 직접 속도·관절 제어값을 생성하는 기능

---

## 5. 목표 아키텍처

### 5.1 컴포넌트

#### API Server

- REST API 제공
- WebSocket 상태/event 전달
- 인증·권한 검사
- 사용자 목표 및 직접 명령 접수
- FastAPI 사용 권장

#### Maestro Agent

- 자연어 목표 해석
- Mission Plan JSON 생성
- 실행 실패에 대한 재계획 제안
- 사용자 질의에 Fleet 상태 요약
- 직접 DB나 gRPC를 조작하지 않고 application tool만 호출

#### Mission Orchestrator

- Mission과 Task 상태 머신 관리
- dependency 충족 여부 판단
- deadline·timeout·retry 관리
- 실패와 부분 성공 처리
- 재시작 후 reconciliation

#### Fleet Scheduler

- 실행 가능한 로봇 후보 계산
- capability, health, battery, distance, queue depth 기반 점수화
- 공유 자원과 구역 lock 확인
- 작업 할당 및 재할당

#### Robot Registry

- 로봇 identity와 endpoint 관리
- heartbeat와 session 관리
- capability 및 software/protocol version 관리
- online/degraded/offline 판정

#### Robot Gateway

- Maestro domain model과 실제 gRPC 메시지 사이 adapter
- 초기에는 기존 `RosGrpc` client 제공
- 이후 전용 `FleetControl` 양방향 stream adapter 추가
- timeout, reconnect, backoff, circuit breaker 담당

#### Safety Policy

- 위험 작업 승인 여부
- 배터리·센서·지도·지오펜스 조건 확인
- emergency stop 우선 처리
- 로봇의 로컬 safety rejection을 최종 결과로 존중

#### Persistence / Event Store

- Robot, Mission, Task, Command, Event, Audit Log 저장
- 상태 변경과 명령 발행을 transaction으로 처리
- 재시작 후 복구에 사용

### 5.2 권장 의존 방향

```text
api/adapters ──> application ──> domain
                       │
                       ▼
                 ports/interfaces
                       ▲
                       │
infrastructure/adapters
```

`domain/`은 FastAPI, gRPC, SQLAlchemy, ROS 2, LLM SDK를 import하지 않는다.

---

## 6. 도메인 모델

### 6.1 Robot

필수 필드:

- `robot_id`: 배포 동안 변하지 않는 고유 ID
- `display_name`
- `robot_type`
- `site_id`
- `map_id`
- `protocol_version`
- `software_version`
- `session_id`: 재부팅마다 변경
- `connection_state`
- `operational_state`
- `last_seen_at`
- `capabilities`
- `pose`
- `battery`
- `health_issues`
- `current_task_id`
- `current_skill`
- `queue_depth`

연결 상태:

```text
UNKNOWN → ONLINE → DEGRADED → OFFLINE
                 ↘ ESTOPPED
```

권장 판정:

- `ONLINE`: heartbeat가 정상 주기 내 도착
- `DEGRADED`: heartbeat는 있으나 센서·배터리·Agent 오류 존재
- `OFFLINE`: 설정된 heartbeat timeout 초과
- `ESTOPPED`: 비상 정지 활성 상태

### 6.2 Mission

필수 필드:

- `mission_id`
- `goal`
- `status`
- `priority`
- `created_by`
- `created_at`, `started_at`, `finished_at`
- `plan_version`
- `constraints`
- `summary`

상태:

```text
DRAFT
PLANNING
READY
RUNNING
PAUSED
SUCCEEDED
PARTIALLY_SUCCEEDED
FAILED
CANCEL_REQUESTED
CANCELLED
```

### 6.3 Task

필수 필드:

- `task_id`
- `mission_id`
- `task_type`
- `instruction`
- `skill_name`, `params`
- `required_capabilities`
- `depends_on`
- `assigned_robot_id`
- `priority`
- `deadline`
- `timeout_sec`
- `max_attempts`
- `attempt_count`
- `idempotency_key`
- `status`
- `progress`
- `result`
- `error_code`, `error_message`

상태:

```text
CREATED
BLOCKED
READY
ASSIGNED
ACCEPTED
QUEUED
RUNNING
SUCCEEDED
FAILED
CANCEL_REQUESTED
CANCELLED
TIMED_OUT
UNKNOWN
```

허용 상태 전이는 domain 함수로만 수행한다. API handler나 gRPC callback이 DB 상태를 임의로 변경하지 않는다.

### 6.4 Command와 Event

각 원격 명령은 다음 ID를 포함해야 한다.

- `command_id`: 개별 전송 식별
- `mission_id`
- `task_id`
- `attempt_id`
- `correlation_id`
- `idempotency_key`
- `robot_id`
- `issued_at`
- `deadline`

전송 보장은 **at-least-once + robot-side idempotency**를 목표로 한다. 분산 환경에서 exactly-once 실행을 가정하지 않는다.

---

## 7. 통신 계약

### 7.1 1차 구현: 기존 RosGrpc Adapter

MVP에서 다음 RPC를 사용한다.

- `Ping`
- `GetRobotInfo`
- `GetRobotStatusStream`
- `ExecuteTask`
- `ExecuteSkill`
- `EmergencyStop`
- 필요 시 `GetRobotCameraImage`

기존 API의 정보 부족은 adapter에서 보정하되, 결과 문자열 파싱을 핵심 로직으로 만들지 않는다. 구조화 정보가 없으면 명시적으로 `unknown`을 반환한다.

### 7.2 2차 구현: FleetControl Protocol

권장 형태:

```protobuf
service FleetControl {
  rpc Connect(stream RobotToMaestro) returns (stream MaestroToRobot);
}
```

`RobotToMaestro` oneof 후보:

- register
- heartbeat
- telemetry
- capability_manifest
- command_ack
- task_update
- robot_event
- log_event

`MaestroToRobot` oneof 후보:

- registration_result
- execute_task
- execute_skill
- cancel_task
- pause_task
- resume_task
- emergency_stop
- request_snapshot
- config_update

### 7.3 프로토콜 규칙

1. 모든 메시지에 `protocol_version`을 둔다.
2. 시간은 UTC epoch milliseconds를 사용한다.
3. 모든 제어 명령에 deadline을 둔다.
4. 로봇은 수신 직후 `ACK` 또는 명시적인 rejection을 반환한다.
5. ACK는 실행 성공을 뜻하지 않는다.
6. Task 결과는 별도 `TaskUpdate`로 전달한다.
7. 재연결 시 로봇은 현재 실행 중인 Task와 최근 command ID를 보고한다.
8. Maestro는 DB 상태와 로봇 상태를 reconciliation한다.
9. 알 수 없는 enum/field를 받을 때 전체 연결을 종료하지 않는다.
10. free-form 자연어와 machine-readable result를 별도 필드로 둔다.

---

## 8. Capability 모델

LLM에게 capability 목록을 질문해 문자열을 파싱하지 않는다. 로봇이 구조화된 manifest를 제공해야 한다.

```json
{
  "name": "navigate_to",
  "version": "1.0",
  "risk_level": "motion",
  "input_schema": {
    "type": "object",
    "properties": {
      "x": {"type": "number"},
      "y": {"type": "number"},
      "yaw": {"type": "number"},
      "frame_id": {"type": "string"}
    },
    "required": ["x", "y", "frame_id"]
  },
  "requirements": ["nav2", "localization", "lidar"]
}
```

Scheduler는 manifest와 현재 health를 함께 사용한다.

```text
configured capability
  + runtime availability
  + required sensor health
  + safety policy
  = 현재 실행 가능 여부
```

---

## 9. 좌표계와 공유 자원

모든 위치에는 최소한 다음 정보를 포함한다.

- `site_id`
- `map_id`
- `map_version`
- `frame_id`
- `x`, `y`, `z`
- `yaw` 또는 quaternion
- 측정 timestamp

서로 다른 `map_id`의 좌표를 직접 비교하거나 전달하지 않는다.

MVP의 공간 충돌 완화 정책:

1. 작업 구역을 가능한 한 로봇별로 분리한다.
2. 좁은 통로, 문, 충전소는 resource lock으로 관리한다.
3. lock lease에는 만료 시간이 있어야 한다.
4. Maestro가 offline이면 새 lock을 획득하지 못하지만, 로컬 로봇은 안전하게 정지하거나 현재 atomic action을 마무리한다.
5. 실제 근접 충돌 회피는 각 로봇의 Nav2와 로컬 safety가 담당한다.

---

## 10. LLM Agent 설계

### 10.1 Maestro Agent가 사용할 Tool

초기 tool 목록:

- `list_robots`
- `get_robot_status`
- `get_robot_capabilities`
- `create_mission_plan`
- `submit_mission`
- `get_mission_status`
- `cancel_mission`
- `request_robot_observation`
- `emergency_stop_robot`
- `emergency_stop_all`

LLM이 low-level repository나 raw gRPC client를 직접 호출하지 않게 한다.

### 10.2 계획 생성 흐름

```text
사용자 목표
  → LLM Plan JSON
  → JSON Schema 검증
  → capability 검증
  → dependency cycle 검증
  → safety/approval 검증
  → 실행 가능성 검증
  → Mission 저장
  → Scheduler 실행
```

### 10.3 Plan 제약

- 한 번에 생성 가능한 최대 Task 수 제한
- DAG cycle 금지
- 존재하지 않는 capability 금지
- timeout과 retry budget 필수
- 위험 작업은 승인 단계 추가
- 전체 재계획 횟수 제한
- LLM 응답의 설명문을 실행하지 않고 구조화된 필드만 사용
- LLM 실패 시 기존 실행 상태를 변경하지 않음

### 10.4 로컬 Agent와 책임 분리

- Maestro Agent: 여러 로봇에 걸친 목적과 역할 분담
- Robo-Claw Agent: 한 로봇 내부의 상황 판단과 스킬 선택
- Maestro가 `ExecuteTask`를 호출할 때는 로봇이 해석 가능한 단일 책임 작업을 전달한다.
- 정확한 스킬과 인자가 이미 결정된 경우 `ExecuteSkill`을 사용해 불필요한 2차 LLM 추론을 피한다.

---

## 11. 안전 및 보안

### 11.1 안전

- emergency stop은 일반 작업 큐를 우회한다.
- 전체 정지는 각 로봇에 병렬 전송하고 개별 ACK를 표시한다.
- 일부 로봇이 응답하지 않아도 전체 요청을 성공으로 표시하지 않는다.
- timeout 발생 후 해당 로봇 상태는 `UNKNOWN`으로 두고 상태를 재조회한다.
- 위험 작업은 별도 allowlist와 사용자 승인 정책을 갖는다.
- 로봇은 Maestro 명령을 로컬 limits와 sensor health로 다시 검증한다.
- 배터리 임계치 미만 로봇에는 새 motion task를 배정하지 않는다.
- stale pose를 기반으로 거리 계산이나 위치 작업을 배정하지 않는다.

### 11.2 보안

운영 전 필수:

- TLS, 가능하면 mTLS
- 로봇별 identity와 credential
- read/control/admin 역할 분리
- credential을 저장소와 로그에 기록하지 않음
- 명령 발행자와 승인자 audit log
- RPC 크기 제한과 rate limit
- 파일 업로드 확장자·크기·저장 경로 검증
- protobuf/JSON 입력 크기와 깊이 제한
- LLM prompt injection이 안전 정책이나 권한 검사를 우회하지 못하게 함

---

## 12. 저장소 구조

초기 권장 구조:

```text
robo-claw-maestro/
├── AGENT.md
├── PLAN.md
├── README.md
├── pyproject.toml
├── .env.example
├── docker-compose.yml
├── proto/
│   └── fleet_control.proto
├── migrations/
├── src/
│   └── robo_claw_maestro/
│       ├── domain/
│       │   ├── robot.py
│       │   ├── mission.py
│       │   ├── task.py
│       │   ├── command.py
│       │   ├── event.py
│       │   └── policies.py
│       ├── application/
│       │   ├── robot_registry.py
│       │   ├── mission_service.py
│       │   ├── scheduler.py
│       │   ├── dispatcher.py
│       │   └── reconciliation.py
│       ├── agent/
│       │   ├── maestro_agent.py
│       │   ├── planner.py
│       │   ├── prompts.py
│       │   └── tools.py
│       ├── ports/
│       │   ├── robot_gateway.py
│       │   ├── repositories.py
│       │   └── event_publisher.py
│       ├── adapters/
│       │   ├── grpc/
│       │   │   ├── ros_grpc_client.py
│       │   │   └── fleet_control_server.py
│       │   ├── database/
│       │   └── llm/
│       ├── api/
│       │   ├── robots.py
│       │   ├── missions.py
│       │   ├── commands.py
│       │   └── websocket.py
│       ├── telemetry/
│       ├── config.py
│       └── main.py
├── web/
├── tests/
│   ├── unit/
│   ├── component/
│   ├── integration/
│   ├── contract/
│   └── e2e/
└── docs/
    ├── architecture.md
    ├── protocol.md
    ├── safety.md
    └── operations.md
```

---

## 13. 기술 스택

### Backend

- Python 3.11 또는 프로젝트 호환성을 고려한 3.10+
- FastAPI
- Pydantic
- grpcio / grpcio-tools
- SQLAlchemy 또는 SQLModel
- PostgreSQL
- Alembic
- asyncio
- httpx
- OpenTelemetry
- Prometheus client
- pytest / pytest-asyncio
- Ruff

### Frontend

- React + TypeScript
- TanStack Query
- WebSocket
- MapLibre 또는 Leaflet
- React Flow: Mission DAG 표시

### 초기 배포

```text
Docker Compose
  ├─ maestro-api
  ├─ maestro-worker
  ├─ postgres
  ├─ prometheus   # 선택
  └─ web          # UI 단계에서 추가
```

초기에는 별도 메시지 브로커 없이 PostgreSQL과 process-local async queue로 시작한다. 다중 worker 또는 고가용성 요구가 생기면 NATS/Redis Streams/Kafka를 ADR을 통해 도입한다.

---

## 14. 구현 단계

## Phase 0 — 프로젝트 기반과 Architecture Decision Records

### 작업

- [ ] `pyproject.toml` 생성
- [ ] 기본 package와 test 구조 생성
- [ ] Ruff, pytest, coverage 설정
- [ ] `.env.example` 작성
- [ ] Docker Compose와 PostgreSQL 개발 환경 작성
- [ ] ADR 작성
  - [ ] ADR-001: 기존 RosGrpc를 MVP transport로 사용
  - [ ] ADR-002: Mission/Task 상태 머신
  - [ ] ADR-003: at-least-once + idempotency
  - [ ] ADR-004: LLM과 결정론적 orchestrator 책임 분리
  - [ ] ADR-005: 좌표계와 resource lock
- [ ] API와 DB migration 기본 구성

### 완료 기준

- `pytest`가 빈 기본 프로젝트에서 성공한다.
- `ruff check`가 성공한다.
- Docker Compose로 API와 PostgreSQL이 기동한다.
- `/health`가 DB 연결 상태를 반환한다.

---

## Phase 1 — Domain Model과 상태 머신

### 작업

- [ ] Robot, Mission, Task, Command, Event entity 구현
- [ ] Mission/Task enum과 허용 전이 함수 구현
- [ ] dependency DAG validation 구현
- [ ] idempotency key 규칙 구현
- [ ] repository port 정의
- [ ] SQLAlchemy/SQLModel adapter와 migration 구현
- [ ] event/audit 기록 구현

### 필수 테스트

- [ ] 정상 상태 전이
- [ ] 허용되지 않은 전이 거부
- [ ] dependency cycle 거부
- [ ] 동일 idempotency key 중복 생성 방지
- [ ] transaction rollback
- [ ] 재시작 후 상태 복구

### 완료 기준

- ROS, gRPC, LLM 없이 domain 테스트가 모두 동작한다.
- 모든 상태 변경에 Event가 생성된다.

---

## Phase 2 — 기존 Robo-Claw 연결 PoC

### 작업

- [ ] `RosGrpcRobotGateway` 구현
- [ ] YAML 또는 DB 기반 정적 robot endpoint 등록
- [ ] `Ping`, `GetRobotInfo` 연동
- [ ] `GetRobotStatusStream` 수집
- [ ] `ExecuteTask`, `ExecuteSkill` 연동
- [ ] `EmergencyStop` 연동
- [ ] 지수 backoff 기반 reconnect
- [ ] 연결 상태와 stale heartbeat 판정
- [ ] fake RosGrpc robot server 작성

### 주의

- 현재 `RosGrpc`는 인증이 없으므로 개발망에서만 사용한다.
- `GetRobotStatusStream`의 image snapshot은 기본 수집에서 제외하거나 빈 값으로 처리하는 변경을 Robo-Claw 측에 제안한다.
- `ExecuteTaskResponse`에 task ID와 상세 결과가 없으므로 Maestro의 command/task mapping을 별도로 유지한다.

### 완료 기준

- fake robot 3대를 동시에 등록·모니터링한다.
- 한 로봇 연결 종료 후 제한 시간 내 `OFFLINE`으로 변경한다.
- 재연결 후 `ONLINE`과 새 session으로 복구한다.
- 특정 로봇에 task/skill을 보내 결과를 기록한다.
- 전체 emergency stop 결과를 로봇별로 반환한다.

---

## Phase 3 — Robot Registry와 Telemetry API

### 작업

- [ ] Registry application service 구현
- [ ] Robot 상태 snapshot 저장
- [ ] telemetry event 저장 정책 구현
- [ ] 오래된 telemetry cleanup/retention 정책
- [ ] REST API 구현
  - [ ] `GET /api/v1/robots`
  - [ ] `GET /api/v1/robots/{robot_id}`
  - [ ] `GET /api/v1/robots/{robot_id}/events`
  - [ ] `POST /api/v1/robots/{robot_id}/skills/{skill_name}`
  - [ ] `POST /api/v1/robots/{robot_id}/tasks`
  - [ ] `POST /api/v1/robots/{robot_id}/emergency-stop`
  - [ ] `POST /api/v1/fleet/emergency-stop`
- [ ] Fleet WebSocket event endpoint 구현

### 완료 기준

- API에서 전체 robot 상태를 조회할 수 있다.
- WebSocket으로 online/offline/status/task event를 받을 수 있다.
- read/control 역할을 구분한다.

---

## Phase 4 — Mission Orchestrator와 Scheduler

### 작업

- [ ] Mission 생성·시작·일시정지·취소 구현
- [ ] ready task 계산
- [ ] capability filtering
- [ ] robot scoring 구현
- [ ] task dispatch와 ACK 처리
- [ ] timeout과 `UNKNOWN` 처리
- [ ] 제한된 retry
- [ ] 다른 로봇으로 재배정
- [ ] resource lock 및 lease
- [ ] Maestro 시작 시 reconciliation

### 초기 Scheduler 점수 요소

```text
eligible = online
        && required capabilities 충족
        && required sensors 정상
        && battery >= threshold
        && map_id 일치
        && safety policy 허용

score = distance_cost
      + queue_depth_cost
      + battery_cost
      + capability_preference
      + current_load_cost
```

점수 계산은 설명 가능해야 하며 선택 이유를 Event에 기록한다.

### 완료 기준

- 독립 Task가 다른 로봇에서 병렬 실행된다.
- dependency 완료 전 Task는 실행되지 않는다.
- capability가 없는 로봇에는 배정되지 않는다.
- 실패한 Task가 retry budget 안에서만 재시도된다.
- timeout 뒤 로봇 상태 확인 없이 동일 물리 작업을 재전송하지 않는다.
- Maestro 재시작 뒤 DB와 로봇 상태를 reconciliation한다.

---

## Phase 5 — Maestro LLM Agent

### 작업

- [ ] LLM provider port 정의
- [ ] OpenAI/Ollama 중 최소 한 provider adapter 구현
- [ ] Mission Plan JSON schema 정의
- [ ] plan parser 및 validator 구현
- [ ] Agent tool 구현
- [ ] prompt와 safety instruction 작성
- [ ] plan 수정과 제한된 replanning 구현
- [ ] token, latency, parse error metric 기록

### 필수 테스트

- [ ] 정상 plan 파싱
- [ ] markdown fence가 포함된 응답
- [ ] 잘못된 JSON
- [ ] 존재하지 않는 robot/capability
- [ ] dependency cycle
- [ ] 과도한 task 수
- [ ] retry/replanning budget 소진
- [ ] prompt injection 입력
- [ ] provider timeout

### 완료 기준

- 자연어 목표가 검증된 Mission DAG로 변환된다.
- 검증 실패 plan은 실행되지 않는다.
- LLM 장애가 실행 중 Mission 상태를 손상시키지 않는다.
- 재계획 결과도 기존 Mission의 새 `plan_version`으로 기록된다.

---

## Phase 6 — Maestro Console

### 화면

- [ ] Fleet overview
- [ ] 지도 위 로봇 위치
- [ ] 로봇 상태 상세
- [ ] Mission 목록 및 상세
- [ ] Task DAG와 진행률
- [ ] 로봇·Mission 이벤트 타임라인
- [ ] 사용자 목표 입력
- [ ] 직접 task/skill 실행
- [ ] 승인 대기 목록
- [ ] 로봇별 정지 및 전체 emergency stop
- [ ] 요청 시 camera snapshot

### 완료 기준

- 상태 변경이 새로고침 없이 반영된다.
- offline과 stale 상태가 명확히 구분된다.
- emergency stop은 항상 접근 가능한 위치에 표시된다.
- 위험 명령은 확인 절차 없이 실행되지 않는다.

---

## Phase 7 — 전용 FleetControl 프로토콜

이 단계는 Robo-Claw 저장소 변경이 함께 필요하다.

### Maestro 작업

- [ ] `fleet_control.proto` 확정
- [ ] 양방향 stream server
- [ ] registration/session 관리
- [ ] command ACK 및 TaskUpdate 처리
- [ ] stream backpressure와 queue limit
- [ ] reconnect/reconciliation
- [ ] mTLS 인증

### Robo-Claw 작업

- [ ] Maestro outbound client package 추가
- [ ] RobotDescriptor 생성
- [ ] SkillManager 기반 capability manifest 생성
- [ ] AgentStatus, health, pose, queue 통합 telemetry 생성
- [ ] 구조화 TaskCommand 수신
- [ ] command idempotency 영속 저장
- [ ] task progress/result event 전달
- [ ] cancel task 지원
- [ ] 로컬 safety rejection error code 표준화

### 완료 기준

- 로봇 IP를 Maestro가 미리 알지 않아도 등록된다.
- 로봇 재부팅은 새 session으로 구분된다.
- 연결 단절 동안 발생한 결과가 재연결 후 조정된다.
- 중복 command가 중복 물리 동작을 일으키지 않는다.
- 구버전 protocol robot의 연결을 명시적으로 거절하거나 호환 모드로 처리한다.

---

## Phase 8 — 운영 안정화

### 작업

- [ ] mTLS 인증서 발급·교체 절차
- [ ] RBAC
- [ ] Prometheus metrics
- [ ] OpenTelemetry tracing
- [ ] 구조화 JSON log
- [ ] DB backup/restore
- [ ] event retention
- [ ] rate limit
- [ ] network fault injection
- [ ] 로봇 및 Maestro 재시작 테스트
- [ ] 부하 테스트
- [ ] 운영 runbook

### 핵심 metric

- 연결된 로봇 수
- heartbeat 지연
- command ACK 지연
- task 성공률/실패율
- task 실행 시간
- retry/reassignment 횟수
- LLM plan parse 실패율
- emergency stop ACK 시간
- gRPC reconnect 횟수
- WebSocket client 수

---

## 15. 테스트 전략

### Unit

외부 시스템 없이 다음을 검증한다.

- 상태 머신
- DAG
- scheduler scoring
- capability validation
- safety policy
- timeout/retry 계산
- idempotency
- resource lock
- LLM plan parser

### Component

fake repository, fake clock, fake robot gateway, fake LLM을 사용한다.

- MissionService
- RobotRegistry
- Scheduler
- Dispatcher
- Reconciliation

시간 기반 로직은 실제 `sleep` 대신 주입 가능한 clock을 사용한다.

### Contract

- 기존 `robo.proto` client/server 호환성
- 신규 `fleet_control.proto` 직렬화
- 필수 필드 및 enum
- 구버전 메시지
- 잘못된 payload
- 크기 제한

### Integration

- PostgreSQL
- API + application + DB
- Maestro + fake robot 3대
- reconnect
- timeout
- duplicate command

### E2E / Simulation

- Gazebo 또는 fake robot fleet
- 병렬 작업
- 로봇 한 대 장애
- 배터리 부족
- 센서 오류
- stale pose
- 응답 유실
- Maestro 재시작
- 전체 emergency stop

### 테스트 규칙

1. 요구사항을 테스트로 먼저 작성한다.
2. 실제 LLM, 로봇, 네트워크는 기본 테스트에서 사용하지 않는다.
3. 외부 시스템이 필요한 테스트에는 marker를 지정한다.
4. timeout·취소·중복·부분 성공을 정상 경로만큼 중요하게 테스트한다.
5. 테스트에서 실제 로봇 제어 endpoint를 기본값으로 사용하지 않는다.

---

## 16. API 초안

```text
GET    /health
GET    /ready

GET    /api/v1/robots
POST   /api/v1/robots
GET    /api/v1/robots/{robot_id}
GET    /api/v1/robots/{robot_id}/events
POST   /api/v1/robots/{robot_id}/tasks
POST   /api/v1/robots/{robot_id}/skills/{skill_name}
POST   /api/v1/robots/{robot_id}/cancel
POST   /api/v1/robots/{robot_id}/emergency-stop

GET    /api/v1/missions
POST   /api/v1/missions
GET    /api/v1/missions/{mission_id}
POST   /api/v1/missions/{mission_id}/start
POST   /api/v1/missions/{mission_id}/pause
POST   /api/v1/missions/{mission_id}/resume
POST   /api/v1/missions/{mission_id}/cancel
GET    /api/v1/missions/{mission_id}/events

POST   /api/v1/goals
POST   /api/v1/fleet/emergency-stop
GET    /api/v1/events/stream        # WebSocket
```

OpenAPI schema와 실제 domain schema는 별도로 두되 변환을 명시적으로 구현한다.

---

## 17. Robo-Claw 측 변경 요청 목록

Maestro 개발 중 `../robo-claw` 변경이 필요하면 한 번에 큰 변경을 하지 말고 아래 순서로 독립 PR을 만든다.

1. `GetCapabilities` 구조화 RPC
2. Agent 상태와 sensor health가 포함된 telemetry
3. `mission_id`, `task_id`, `command_id`, `attempt_id` 전달
4. Task progress event
5. 실행 중 Task cancel contract
6. command idempotency 영속 저장
7. `session_id`/boot ID
8. `site_id`, `map_id`, `map_version`, `frame_id`
9. 구조화 error code
10. `robo_claw_grpc` 인증/TLS
11. camera stream과 telemetry 분리
12. protocol/software version negotiation

Robo-Claw의 기존 public API를 변경할 때는 generated protobuf, 테스트, README와 관련 문서를 함께 갱신한다.

---

## 18. 개발 규칙

1. TDD를 기본으로 한다.
2. domain layer에는 FastAPI, gRPC, DB, LLM SDK를 import하지 않는다.
3. 모든 외부 의존성은 port와 adapter로 분리한다.
4. public API/proto 변경에는 contract test를 추가한다.
5. 상태 변경은 반드시 domain 상태 머신을 통한다.
6. 모든 명령은 audit event를 생성한다.
7. credential, token, 사용자 자연어 원문 전체를 기본 로그에 남기지 않는다.
8. 실패를 문자열 하나로 표현하지 말고 error code와 구조화 detail을 사용한다.
9. 실제 로봇을 움직이는 테스트는 명시적인 opt-in marker를 사용한다.
10. emergency stop 경로는 일반 큐나 LLM 응답을 기다리지 않는다.
11. 자동 retry 전에 remote side-effect 상태를 확인한다.
12. 다른 에이전트의 변경이나 미추적 파일을 임의로 삭제하지 않는다.

권장 CI 명령:

```bash
ruff check src tests
ruff format --check src tests
pytest -m "not hardware and not e2e"
```

---

## 19. 초기 이슈 분할안

병렬 개발 시 충돌을 줄이기 위해 다음 단위로 분할한다.

### Foundation Agent

- 프로젝트 skeleton
- config
- logging
- CI
- Docker Compose

### Domain Agent

- Robot/Mission/Task 모델
- 상태 머신
- DAG validation
- domain tests

### Persistence Agent

- DB models
- repositories
- migrations
- transaction/event store

### Robot Gateway Agent

- 기존 RosGrpc client
- fake robot server
- reconnect/status stream
- contract tests

### Registry Agent

- robot registration
- heartbeat
- online/offline 판정
- telemetry snapshot

### Orchestrator Agent

- ready task 계산
- dispatcher
- retry/timeout
- reconciliation

### Scheduler Agent

- capability filtering
- scoring
- resource lock
- assignment explanation

### LLM Agent

- provider port
- plan schema/parser
- prompts/tools
- fake LLM tests

### API Agent

- REST endpoints
- auth/RBAC skeleton
- WebSocket events

### UI Agent

- fleet overview
- mission/task view
- emergency controls

각 작업은 다른 영역의 concrete implementation을 직접 import하지 말고 합의된 domain type과 port를 사용한다.

---

## 20. 첫 번째 Milestone 권장 작업 순서

첫 구현은 UI나 LLM보다 아래 vertical slice를 우선한다.

```text
1. 프로젝트 skeleton
2. Robot domain model
3. fake robot gateway
4. Robot Registry
5. status polling/stream
6. GET /robots
7. 단일 ExecuteTask 전송
8. Task 상태 저장
9. emergency stop
10. integration test
```

첫 Milestone 시연 시나리오:

1. fake robot 3대를 실행한다.
2. Maestro가 세 로봇을 등록하고 상태를 표시한다.
3. 한 로봇에 `회의실로 이동해` Task를 전달한다.
4. Task가 `ASSIGNED → ACCEPTED → RUNNING → SUCCEEDED`로 변경된다.
5. 두 번째 로봇 연결을 끊으면 `OFFLINE`으로 표시된다.
6. fleet emergency stop을 실행하고 각 로봇의 ACK 결과를 확인한다.

이 vertical slice가 안정화된 뒤 Mission DAG와 LLM 계획 생성을 추가한다.

---

## 21. 구현 전 확정이 필요한 질문

다음 항목은 Phase 0에서 사용자 또는 시스템 담당자와 확정한다.

1. 초기 목표 로봇 수와 장기 최대 로봇 수는 몇 대인가?
2. 로봇과 Maestro는 동일 LAN인가, 서로 다른 네트워크인가?
3. 로봇에서 Maestro로 outbound 연결이 가능한가?
4. 모든 로봇이 같은 map을 사용하는가?
5. 로봇 종류별 capability는 무엇인가?
6. Maestro LLM은 로컬 Ollama인가, 클라우드 provider인가?
7. 사람 승인 없이 허용할 motion/manipulation 범위는 어디까지인가?
8. Maestro 연결이 끊겼을 때 로봇은 현재 작업을 완료할지 즉시 정지할지?
9. telemetry 보관 기간은 얼마인가?
10. 카메라 영상은 snapshot이면 충분한가, 실시간 영상이 필요한가?
11. 사용자 인증과 조직 계정 연동이 필요한가?
12. 기존 `roboclaw_config_server`와 Maestro의 설정 책임을 어떻게 구분할 것인가?

확정 전 기본 정책은 보수적으로 선택한다.

- Maestro 연결 단절 시 새 작업을 시작하지 않는다.
- 실행 중인 장기 작업은 로컬 안전 정지 또는 명시된 offline policy를 따른다.
- 위험 스킬은 기본 거부한다.
- 원격 실행은 allowlist 방식으로 허용한다.
- telemetry가 stale하면 로봇을 새 작업 후보에서 제외한다.

---

## 22. MVP 완료 정의

아래 항목을 모두 만족하면 첫 MVP를 완료한 것으로 본다.

- [ ] 최소 3대의 fake/simulated robot 동시 관리
- [ ] robot 상태와 연결 상태 실시간 표시
- [ ] 자연어 목표를 검증된 Mission/Task로 변환
- [ ] capability 기반 Task 배정
- [ ] dependency 기반 순차 및 독립 Task 병렬 실행
- [ ] 성공, 실패, timeout, unknown, cancel 상태 추적
- [ ] retry budget과 재배정 동작
- [ ] 중복 command 방지
- [ ] 로봇별 및 fleet emergency stop
- [ ] Maestro 재시작 후 Mission/Task 복구 및 reconciliation
- [ ] 모든 명령과 상태 변경 audit log
- [ ] 단위·컴포넌트·계약·통합 테스트 통과
- [ ] 실제 로봇 2대 이상으로 제한된 인수 시나리오 통과
- [ ] 운영 및 장애 대응 문서 작성

---

## 23. 최우선 구현 방향

첫 단계에서는 새로운 Fleet 프로토콜부터 만들지 않는다. 다음 순서로 위험을 줄인다.

1. 기존 `robo_claw_grpc`를 사용하는 adapter로 중앙 모니터링과 단일 작업 실행을 검증한다.
2. Mission/Task 상태 머신과 Scheduler를 fake robot으로 충분히 검증한다.
3. 실제 로봇 2~3대로 네트워크 단절·timeout·중복 실행 문제를 확인한다.
4. 확인된 요구사항을 바탕으로 `FleetControl` protobuf를 확정한다.
5. 이후 각 Robo-Claw에 outbound Maestro connector를 추가한다.

이 순서를 따르면 기존 Robo-Claw의 동작을 크게 변경하지 않고도 Maestro의 핵심 가치를 먼저 검증할 수 있으며, 실사용에서 확인된 요구사항을 전용 프로토콜에 반영할 수 있다.
