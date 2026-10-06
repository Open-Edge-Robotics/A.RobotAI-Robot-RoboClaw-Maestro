# 추가 구현 및 사용자 검증 시점

이 문서는 `PLAN.md` 기준으로 앞으로 추가 구현해야 할 항목과 사용자가 개입해 검증해야 하는 시점을 2026-09-22 기준으로 정리합니다.

## 1. 실제 Robo-Claw FleetControl Connector

**우선순위: 최상**

현재 상태:

- Maestro FleetControl server 구현 완료
- fake outbound robot 연결 검증 완료
- 실제 `robo_claw_grpc` outbound connector 구현 완료
- `rclaw launch --docker --use-grpc`의 `.env` 전달 경로 사용
- `MAESTRO_IP`, `ROBOT_PORT`, `ROBOT_ID` 설정 parser 구현
- registration/session/heartbeat/telemetry 구현
- `SKILLS.md` capability manifest builder 구현
- Robot별 site/map/map version/map frame 전송 구현
- Task/Skill/EmergencyStop/Snapshot command handler 구현
- insecure 및 TLS/mTLS client 구현
- disconnect `complete`, `stop`, `finish_atomic` 정책 구현
- 실제 gRPC fake Maestro 통합 테스트 완료

남은 구현 항목:

- 실제 AgentStatus의 current task/current skill/queue depth 상세 연동
- 실제 ROS action cancel handler
- runtime SkillManager 목록을 connector에 직접 주입하는 wiring
- 실제 Robot 환경의 certificate/mount 검증

### 사용자 개입 시점 1

Connector 구현과 fake 기반 테스트가 끝났으므로, 현재가 사용자 확인이 필요한 시점입니다.

필요 정보:

- Maestro 실제 IP와 포트
- 각 Robot의 고유 ID
- 실제 Robo-Claw 실행 방식
  - Docker
  - `rclaw launch`
  - 직접 ROS launch
- Robot별 capability
- site ID, map ID
- mTLS를 즉시 적용할지 개발망 insecure로 먼저 검증할지

첫 검증은 실제 동작 명령 없이 다음 항목만 확인합니다.

```text
Robot 등록
Heartbeat
배터리
위치
Capability
ONLINE/OFFLINE
```

## 2. Robot-side 영속 Command Dedup

**우선순위: 최상**

현재 구현 상태:

- Robo-Claw local SQLite command journal 구현
- `idempotency_key` primary key 제약 구현
- 완료 결과와 미전송 상태 저장
- 중복 Command 수신 시 이전 결과 replay
- process 재시작 후 중복 차단 테스트 완료
- reconnect 후 완료 결과 replay 구현

남은 항목:

- 운영 retention/cleanup 정책
- Maestro의 result receipt를 이용한 delivered 확정 protocol
- 실제 Robot motion을 이용한 물리 중복 방지 인수 테스트

### 사용자 개입 시점 2

구현 후 실제 Robot 또는 simulation에서 다음 검증이 필요합니다.

1. 이동 명령 1회 전송
2. 같은 idempotency key로 재전송
3. Robot이 실제로 한 번만 움직였는지 확인
4. Robot 재시작
5. 같은 명령 재전송
6. 재시작 후에도 다시 움직이지 않는지 확인

이 테스트는 실제 Robot 전에 simulation에서 먼저 수행해야 합니다.

## 3. 실행 중 Task Cancel/Pause/Resume

**우선순위: 높음**

현재 상태:

- Maestro 상태에는 cancel 요청이 있음
- 기존 RosGrpc에는 실행 중 Task cancel 계약이 없음
- FleetControl proto에는 cancel 메시지가 있지만 실제 Robot handler가 없음

추가 구현 항목:

- ROS action cancel 연결
- 현재 Task ID와 action goal mapping
- `CANCEL_REQUESTED → CANCELLED`
- cancel timeout
- cancel 결과를 Maestro에 보고
- pause가 불가능한 atomic skill 구분
- resume 가능 Task와 재시작 Task 구분
- emergency stop이 cancel보다 우선

### 사용자 개입 시점 3

다음 시나리오를 실제 Robot 또는 simulation에서 확인해야 합니다.

1. 장시간 이동 시작
2. 이동 중 cancel 요청
3. Robot이 안전하게 정지
4. Task가 `CANCELLED`
5. 재실행 시 처음부터 또는 정의된 checkpoint부터 실행
6. cancel 중 emergency stop 우선 동작

실제 Robot에서는 저속 설정과 안전 담당자가 필요합니다.

## 4. 자동 Scheduler Worker

**우선순위: 높음**

현재는 API의 `/run` 요청으로 scheduling round를 명시적으로 실행합니다.

추가 구현 항목:

- background scheduler loop
- READY Mission 자동 실행
- deadline/timeout 감시
- retry 예약
- resource lease 갱신
- offline Robot Task reconciliation
- graceful shutdown
- PostgreSQL advisory lock 또는 worker ownership
- 같은 Mission의 중복 scheduling 방지

### 사용자 개입 시점 4

자동 테스트 후 사용자 검증 항목:

1. Mission 생성만 수행
2. `/run` 호출 없이 자동 실행되는지 확인
3. Maestro를 실행 중 재시작
4. 같은 Task가 중복 실행되지 않는지 확인
5. Robot 한 대를 중간에 종료
6. 다른 Robot으로 재배정되는지 확인

이 단계부터 실제 운용 형태에 가까워집니다.

## 5. Aggregate/Event Atomic Transaction

**우선순위: 높음**

현재 상태 변경과 Event 저장이 별도 DB transaction이 될 수 있습니다.

추가 구현 항목:

- Unit of Work
- Mission/Task/Command/Event atomic commit
- transaction rollback
- optimistic locking 또는 version field
- concurrent update 충돌 처리
- outbox 또는 event publish 상태

### 사용자 개입 시점

일반적으로 사용자 개입은 필요하지 않습니다. 자동 DB 테스트로 검증할 수 있습니다.

다만 PostgreSQL container를 사용한 장애 테스트 시 다음 확인을 요청할 수 있습니다.

- transaction 중 DB 강제 종료
- 재시작 후 aggregate/event 불일치 여부
- 동일 Command 중복 여부

## 6. 실제 Robot 1대 읽기 전용 인수 테스트

**사용자 개입 필수**

Connector와 telemetry가 준비되면 가장 먼저 수행할 실제 Robot 테스트입니다.

허용 항목:

- 등록
- heartbeat
- 상태 조회
- battery
- pose
- capabilities
- camera snapshot
- `get_status`
- `analyze_scene`

금지 항목:

- navigation
- manipulation
- dangerous skill
- 자동 retry
- 병렬 동작

완료 기준:

```text
Robot ONLINE
고유 session ID
pose/map 정상
battery 정상
camera 정상
재시작 후 새 session
연결 종료 후 OFFLINE
```

## 7. 실제 Robot 2대 Fleet 인수 테스트

**사용자 개입 필수**

1대 읽기 전용 검증 후 진행합니다.

테스트 순서:

1. Robot 2대 등록
2. 서로 다른 Robot ID 확인
3. capability 차이 확인
4. 읽기 전용 Task를 각각 배정
5. 독립 Task 병렬 실행
6. dependency Task 순차 실행
7. 한 대 연결 종료
8. 나머지 Robot 상태 유지
9. 재연결 session 갱신
10. Fleet emergency stop 결과 확인

이 단계에서는 아직 motion을 실행하지 않는 것이 안전합니다.

## 8. 실제 Motion 테스트

**사용자와 안전 담당자 개입 필수**

읽기 전용 2대 테스트가 통과한 뒤 수행합니다.

필수 조건:

- 장애물이 없는 구역
- 저속 제한
- 물리 emergency stop 확인
- map/frame 일치
- stale pose 아님
- battery 임계치 이상
- Robot별 작업 구역 분리
- 좁은 통로 resource lock

테스트 순서:

1. Robot 1대 단일 이동
2. 이동 중 cancel
3. 이동 중 Maestro 연결 종료
4. emergency stop
5. Robot 2대 서로 다른 구역 이동
6. 같은 resource lock 경쟁
7. 한 대 실패 후 재배정

자동화로 실제 Robot을 바로 움직이지 않고 각 단계마다 사용자 확인 후 진행해야 합니다.

## 9. Console 추가 구현

**우선순위: 중간**

남은 항목:

- MapLibre/Leaflet 실제 지도
- Robot 상세 화면
- React Flow Mission DAG
- stale/offline 시각적 분리
- camera refresh 제한
- UI 자동 테스트
- 위험 명령 확인 dialog 강화
- 승인자 identity 표시

### 사용자 개입 시점

지도 구현 전에 다음 정보가 필요합니다.

- 사용하는 map 이미지 또는 tile
- 좌표 원점
- resolution
- map ID/version
- Robot pose frame
- 층별 지도 여부

## 10. 운영 안정화

**우선순위: 실제 운영 전 필수**

남은 항목:

- OpenTelemetry exporter
- 인증서 발급·교체 자동화
- Prometheus alert rule
- Grafana dashboard
- PostgreSQL restore 자동 테스트
- network fault injection
- 부하 테스트
- 장시간 soak test
- 다중 API worker
- shared rate limit
- WebSocket shared event bus

### 사용자 개입 시점

운영 환경 정보가 필요합니다.

- 배포 서버 OS
- Docker/Kubernetes 여부
- Prometheus/Grafana 주소
- 인증서 발급 방식
- DB backup 저장 위치
- telemetry 보관 기간
- 목표 Robot 수
- 최대 동시 사용자 수

## 권장 진행 순서

```text
1. 실제 Robo-Claw outbound connector
2. Robot-side persistent dedup
3. 실제 Robot 1대 읽기 전용 테스트        ← 사용자 개입
4. 실제 Robot 2대 읽기 전용 테스트        ← 사용자 개입
5. cancel/pause/resume
6. 자동 Scheduler worker
7. simulation motion/cancel 테스트         ← 사용자 확인
8. 실제 Robot 1대 motion 테스트            ← 사용자·안전 담당자 개입
9. 실제 Robot 2대 병렬/장애 테스트         ← 사용자·안전 담당자 개입
10. Console 지도/DAG
11. 운영 안정화 및 부하 테스트
```

## 실제 Robo-Claw Connector 구현을 위한 확정 사항

다음 항목은 사용자 확인을 통해 구현 기준으로 확정했습니다.

### 1. Robo-Claw 실행 방식

실제 Robot은 다음 명령 형태로 실행합니다.

```bash
./rclaw launch <robot-profile> <environment> --docker --use-grpc
```

FleetControl connector는 이 Docker 실행 경로에서 선택적으로 활성화할 수 있어야 합니다. 기존 RosGrpc server의 `--use-grpc` 동작을 유지하면서 outbound FleetControl client를 함께 실행하도록 launch/Docker 환경 변수 전달 경로를 추가해야 합니다.

### 2. Maestro 주소와 포트

Robot은 다음 환경 변수를 사용해 Maestro endpoint를 구성합니다.

```bash
MAESTRO_IP=<maestro-host-or-ip>
ROBOT_PORT=<fleet-control-port>
```

Connector의 접속 대상은 다음과 같이 계산합니다.

```text
${MAESTRO_IP}:${ROBOT_PORT}
```

개발 단계의 기본 예시는 다음과 같습니다.

```bash
MAESTRO_IP=192.168.10.10
ROBOT_PORT=50053
```

`MAESTRO_IP`가 비어 있으면 outbound connector를 시작하지 않습니다. `ROBOT_PORT`는 1~65535 범위를 검증하고 잘못된 값이면 안전하게 기동을 거부해야 합니다.

### 3. Robot identity

각 Robot은 다음 환경 변수의 값을 영구 identity로 사용합니다.

```bash
ROBOT_ID=<unique-robot-id>
```

요구사항:

- Robot마다 서로 다른 `ROBOT_ID`를 사용합니다.
- registration descriptor와 모든 telemetry/ACK/TaskUpdate에 같은 ID를 사용합니다.
- mTLS 적용 이후 certificate identity와 `ROBOT_ID`가 일치해야 합니다.
- Robot 재부팅 시 `ROBOT_ID`는 유지하고 `session_id`만 새로 생성합니다.
- 동일 `ROBOT_ID`의 새 session이 연결되면 Maestro는 이전 session을 종료된 것으로 처리합니다.

### 4. Capability negotiation

Capability는 Maestro 정적 설정에만 의존하지 않고 연결 registration 단계에서 Robot과 Maestro가 교환합니다.

Robot은 로컬 Robo-Claw의 `SKILLS.md` 및 실제 등록된 SkillManager 정보를 사용해 구조화 manifest를 생성합니다.

```json
{
  "name": "navigate_to",
  "version": "1.0",
  "risk_level": "motion",
  "input_schema": {},
  "requirements": ["nav2", "localization", "lidar"]
}
```

구현 규칙:

1. `SKILLS.md`에 문서화된 skill 목록을 읽습니다.
2. 가능하면 SkillManager의 실제 등록 목록과 교차 검증합니다.
3. 문서에는 있지만 runtime에 없는 skill은 현재 사용 불가로 보고합니다.
4. runtime에는 있지만 문서에 없는 skill은 원격 실행 기본 거부 대상으로 둡니다.
5. read/action/motion/dangerous 위험도를 구조화합니다.
6. Maestro는 registration 시 manifest를 저장하고 Scheduler와 승인 정책에 사용합니다.
7. reconnect 또는 skill 구성이 바뀌면 manifest를 다시 전송합니다.
8. 자연어 응답을 capability source로 사용하지 않습니다.

### 5. Robot별 Map 관리

Robot은 동일 map을 사용할 수도 있고 서로 다른 map에서 운영될 수도 있습니다. Maestro는 Robot별 map 정보를 관리합니다.

registration/telemetry에 포함할 필드:

```text
site_id
map_id
map_version
frame_id
```

운영 규칙:

- 같은 `map_id`의 Robot끼리만 좌표 거리를 직접 비교합니다.
- Task에 `map_id`가 있으면 동일 map의 Robot만 후보가 됩니다.
- Maestro가 해당 Robot의 map을 알지 못하면 위치 기반 Task를 배정하지 않습니다.
- map 변경 시 Robot은 새 descriptor/telemetry를 전송합니다.
- map version이 다르면 같은 map ID라도 좌표 호환 여부를 별도로 검증합니다.
- 층간 또는 서로 다른 map 이동은 좌표 비교가 아니라 명시적 전환 Task로 처리합니다.

### 6. 개발 단계 보안 모드

개발 단계에서는 insecure FleetControl 연결을 먼저 사용합니다.

```text
grpc://MAESTRO_IP:ROBOT_PORT
```

조건:

- 신뢰할 수 있는 개발 LAN에서만 사용합니다.
- public network에 노출하지 않습니다.
- 운영 전에는 TLS/mTLS로 전환합니다.
- insecure 사용 여부가 로그에 명확히 표시되어야 합니다.
- 운영 설정에서 insecure fallback을 자동 허용하지 않습니다.

권장 환경 변수:

```bash
FLEET_CONTROL_TLS=false
```

운영 전환 시:

```bash
FLEET_CONTROL_TLS=true
FLEET_CONTROL_CA_CERT=/path/to/ca.crt
FLEET_CONTROL_CLIENT_CERT=/path/to/robot.crt
FLEET_CONTROL_CLIENT_KEY=/path/to/robot.key
```

### 7. Disconnect 정책

Maestro 연결이 끊기면 현재 작업을 완료하는 것을 기본값으로 합니다. 정책은 Robot별 옵션으로 설정할 수 있어야 합니다.

권장 환경 변수:

```bash
MAESTRO_DISCONNECT_POLICY=complete
```

지원할 정책:

| 값 | 동작 |
| --- | --- |
| `complete` | 기본값. 현재 실행 중인 작업을 로컬 safety 범위 안에서 완료하고 새 작업은 시작하지 않습니다. |
| `stop` | 연결 단절을 감지하면 현재 작업을 안전하게 중단합니다. |
| `finish_atomic` | 현재 atomic action만 마친 뒤 다음 단계로 진행하지 않고 정지합니다. |

공통 규칙:

- 연결 단절 중 새 원격 작업을 시작하지 않습니다.
- emergency stop과 로컬 safety는 disconnect 정책보다 우선합니다.
- 완료/실패 결과는 Robot local journal에 저장합니다.
- reconnect 후 미전송 TaskUpdate를 Maestro에 다시 전달합니다.
- Maestro는 결과를 받기 전 Task를 `UNKNOWN`으로 유지합니다.

### 8. Read-only Skill 목록

Read-only skill 목록은 Robot 내부 Robo-Claw의 `SKILLS.md`에서 읽어 활용합니다.

구현 규칙:

1. Connector 시작 시 `SKILLS.md`를 읽습니다.
2. read-only로 표시된 skill을 capability manifest에 `risk_level=read`로 포함합니다.
3. 실제 SkillManager 등록 상태와 교차 검증합니다.
4. Maestro는 read-only skill을 관측 및 상태 조회에 우선 사용합니다.
5. read-only skill은 위험 승인 workflow 없이 실행할 수 있지만 control 권한은 계속 요구합니다.
6. Robot의 로컬 allow/block policy가 최종 권한을 갖습니다.
7. `SKILLS.md` 파싱 실패 시 임의 추론하지 않고 구조화 capability를 비어 있거나 unavailable 상태로 보고합니다.

초기 읽기 전용 인수 테스트 후보:

```text
get_status
analyze_scene
scan_room
describe_surroundings
get_detections
query_peer_status
rag_search
```

실제 목록은 각 Robot의 `SKILLS.md`와 SkillManager 등록 결과를 기준으로 확정합니다.

## Connector 구현에 반영할 설정 요약

```bash
# 필수 identity 및 endpoint
ROBOT_ID=robot-1
MAESTRO_IP=192.168.10.10
ROBOT_PORT=50053

# 개발 단계
FLEET_CONTROL_TLS=false
MAESTRO_DISCONNECT_POLICY=complete

# Robot별 공간 정보
ROBOT_SITE_ID=lab
ROBOT_MAP_ID=floor-1
ROBOT_MAP_VERSION=v1
ROBOT_MAP_FRAME_ID=map

# 운영 mTLS 전환 시
FLEET_CONTROL_TLS=true
FLEET_CONTROL_CA_CERT=/run/secrets/ca.crt
FLEET_CONTROL_CLIENT_CERT=/run/secrets/robot-1.crt
FLEET_CONTROL_CLIENT_KEY=/run/secrets/robot-1.key
```

## 확정 사항 반영 후 구현 순서

```text
1. Robo-Claw connector config/env parser                 완료
2. SKILLS.md capability manifest builder                 완료
3. FleetControl outbound registration/heartbeat          완료
4. Robot별 map descriptor/telemetry                      완료
5. disconnect policy complete/stop/finish_atomic         완료
6. local SQLite command/result journal                   완료
7. Task/Skill/Snapshot/EmergencyStop command handler     완료
8. reconnect 및 미전송 결과 replay                       완료
9. Docker/rclaw launch 환경 변수 전달                    완료(.env 전달 경로 사용)
10. fake dependency 기반 connector unit/integration test 완료
11. 실제 Robot 1대 읽기 전용 테스트                     현재 사용자 개입 필요
12. 실제 Robot 2대 읽기 전용 테스트                     1대 검증 후 사용자 개입
```

현재가 **실제 Robot 1대 읽기 전용 등록 테스트 직전**입니다. 이 단계에서는 motion/manipulation 명령을 사용하지 않고 registration, heartbeat, battery, pose, map, capability, snapshot, read-only skill만 확인합니다.

## Connector 자동 검증 결과

실제 Robot 개입 전 다음 검증을 완료했습니다.

```text
Robo-Claw connector unit/integration: 6 passed
Maestro backend suite: 85 passed
Ruff connector/static check: passed
FleetControl 실제 loopback gRPC stream: passed
SQLite command journal restart dedup: passed
```

Robo-Claw 전체 legacy service test는 현재 사용한 Maestro 가상환경에 `psutil` 및 ROS 의존성이 없어 실행하지 않았습니다. Connector 전용 테스트와 기존 Maestro 전체 테스트는 통과했습니다.

## 현재 사용자 확인이 필요한 실제 Robot 1대 테스트

### Maestro 호스트

```bash
export MAESTRO_FLEET_CONTROL_ENABLED=true
export MAESTRO_FLEET_CONTROL_HOST=0.0.0.0
export MAESTRO_FLEET_CONTROL_PORT=50053
export MAESTRO_READ_TOKEN=read-secret
export MAESTRO_CONTROL_TOKEN=control-secret
.venv/bin/uvicorn robo_claw_maestro.main:app --host 0.0.0.0 --port 8000
```

### Robot 호스트 `.env`

```bash
MAESTRO_IP=<MAESTRO 실제 IP>
ROBOT_PORT=50053
ROBOT_ID=<고유 Robot ID>
ROBOT_SITE_ID=lab
ROBOT_MAP_ID=floor-1
ROBOT_MAP_VERSION=v1
ROBOT_MAP_FRAME_ID=map
MAESTRO_DISCONNECT_POLICY=complete
FLEET_CONTROL_TLS=false
FLEET_COMMAND_JOURNAL_PATH=/tmp/robo_claw_fleet_commands.sqlite3
RC_SKILLS_GUIDE_FILE=/ros2_ws/docs/SKILLS.md
```

Connector가 포함된 Docker image를 먼저 build한 뒤 실행합니다. Robo-Claw 저장소에서 로컬 검증용 image를 만들 경우 다음 순서를 사용합니다.

```bash
task docker-build-local
./rclaw launch <robot-profile> <environment> --docker --use-grpc
```

기존 registry image를 사용하는 환경에서는 connector 변경이 포함된 새 image tag를 배포하고 `--image-tag <tag> --pull`을 지정합니다.

### 확인 순서

1. Maestro에서 Robot 등록을 확인합니다.

   ```bash
   curl -H 'Authorization: Bearer read-secret' \
     http://127.0.0.1:8000/api/v1/robots
   ```

2. 다음 값이 실제 Robot과 일치하는지 확인합니다.

   ```text
   robot_id
   connection_state=ONLINE
   session_id
   site_id
   map_id
   map_version
   frame_id
   battery_percentage
   pose
   capabilities
   ```

3. 읽기 전용 skill만 호출합니다.

   ```bash
   curl -X POST \
     http://127.0.0.1:8000/api/v1/robots/<ROBOT_ID>/skills/get_status \
     -H 'Authorization: Bearer control-secret' \
     -H 'Content-Type: application/json' \
     -d '{"params":{},"requested_by":"operator","timeout_sec":10}'
   ```

4. camera가 있는 Robot만 snapshot을 확인합니다.

   ```bash
   curl -H 'Authorization: Bearer read-secret' \
     http://127.0.0.1:8000/api/v1/robots/<ROBOT_ID>/snapshot
   ```

5. Robot process를 종료하고 `OFFLINE` 전환을 확인합니다.
6. Robot을 재실행하고 이전과 다른 `session_id`로 `ONLINE` 복구되는지 확인합니다.
7. 이 단계에서는 `navigate_to`, manipulation, dangerous skill, 자동 retry를 실행하지 않습니다.
