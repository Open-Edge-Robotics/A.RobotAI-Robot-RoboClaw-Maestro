# 다중 로봇 연동 테스트 매뉴얼

이 문서는 Robo-Claw Maestro를 fake robot 3대 또는 실제 Robo-Claw 2대 이상과 연결해 상태 조회, 병렬 작업, 장애 감지, 비상 정지를 검증하는 절차를 설명합니다.

## 1. 안전 조건

- 실제 로봇 테스트는 장애물이 없는 제한 구역에서 수행합니다.
- 각 로봇의 로컬 emergency stop과 물리 비상 정지 버튼을 먼저 확인합니다.
- 현재 RosGrpc 연결은 TLS와 인증을 제공하지 않으므로 신뢰할 수 있는 개발 LAN에서만 사용합니다.
- 첫 테스트에서는 `get_status`, `analyze_scene` 같은 읽기 전용 스킬만 허용합니다.
- motion 작업은 로봇별 지도와 초기 위치가 정상임을 확인한 후 수행합니다.

## 2. 신규 호스트 빠른 설치와 실행

### 사전 조건

신규 호스트에는 다음 도구가 필요합니다.

```text
git
Docker Engine
Docker Compose v2 (`docker compose`)
python3
curl
```

Docker daemon이 실행 중이고 현재 사용자가 Docker를 실행할 수 있어야 합니다.

### 저장소 준비

```bash
git clone <robo-claw-maestro-repository-url>
cd robo-claw-maestro
```

### 1단계: 안전한 환경 파일 생성

```bash
./scripts/bootstrap_maestro_host.sh --no-start
```

스크립트는 `.env.example`을 바탕으로 `.env`를 만들고 다음 값을 자동 생성합니다.

- PostgreSQL password
- read token
- control token
- admin token
- PostgreSQL connection URL

생성한 `.env`는 권한 `600`으로 저장합니다. 기존 `.env`가 있으면 덮어쓰지 않습니다.

기존 파일을 교체해야 할 때만 다음 옵션을 사용합니다.

```bash
./scripts/bootstrap_maestro_host.sh --no-start --force-env
```

### 2단계: 환경별 설정 수정

```bash
editor .env
```

최소 확인 항목:

```dotenv
MAESTRO_PORT=8000
MAESTRO_FLEET_CONTROL_ENABLED=false
MAESTRO_FLEET_CONTROL_PORT=50053
MAESTRO_LANGSMITH_TRACING=false
MAESTRO_LLM_PROVIDER=
MAESTRO_LLM_MODEL=
```

LLM planning을 사용할 경우 [LLM Provider 설정](LLM_PROVIDERS.md)에 따라 provider와 credential을 설정합니다. LLM을 비활성화해도 Robot monitoring, 직접 Mission, emergency stop은 동작합니다.

FleetControl outbound Robot을 받을 경우:

```dotenv
MAESTRO_FLEET_CONTROL_ENABLED=true
MAESTRO_FLEET_CONTROL_HOST=0.0.0.0
MAESTRO_FLEET_CONTROL_PORT=50053
```

### 3단계: Build 및 실행

```bash
./scripts/bootstrap_maestro_host.sh
```

스크립트가 수행하는 작업:

1. Docker/Docker Compose/Python/curl 확인
2. `.env` 생성 또는 기존 파일 사용
3. `docker compose config` 검증
4. API/Web image build
5. PostgreSQL/API/Web container 실행
6. `/ready`를 최대 120초 동안 확인
7. 실패 시 API log 출력

기존 image를 다시 build하지 않고 시작하려면:

```bash
./scripts/bootstrap_maestro_host.sh --no-build
```

### 4단계: 비파괴 자동 검증

```bash
python3 scripts/verify_maestro_host.py --env-file .env
```

검증 항목:

- `/health`
- DB health
- `/ready`
- Prometheus `/metrics`
- read token을 사용한 Robot API
- approval API
- 필수 OpenAPI route
- Docker service 실행 상태

등록돼야 하는 Robot ID가 있다면 반복해서 지정합니다.

```bash
python3 scripts/verify_maestro_host.py \
  --env-file .env \
  --expect-robot former-01 \
  --expect-robot stretch-01
```

WebSocket까지 확인하려면 프로젝트 가상환경을 준비합니다.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python scripts/verify_maestro_host.py --env-file .env --websocket
```

검증 스크립트는 Task, Skill, emergency stop 같은 제어 요청을 보내지 않습니다.

### 5단계: 상태 및 종료

```bash
docker compose --env-file .env ps
docker compose --env-file .env logs -f maestro-api
docker compose --env-file .env down
```

PostgreSQL volume까지 삭제하려면 데이터가 필요 없는지 확인한 후에만 사용합니다.

```bash
docker compose --env-file .env down -v
```

## 3. 개발 환경 설치와 기본 검증

```bash
cd robo-claw-maestro
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/pytest -m 'not hardware and not e2e'
```

PostgreSQL과 API 기동을 확인하려면 다음 명령을 사용합니다.

```bash
cp .env.example .env
docker compose up --build -d
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/ready
docker compose down
```

## 4. Fake robot 3대 테스트

별도 ROS 2 또는 로봇 없이 실행할 수 있습니다.

```bash
.venv/bin/python scripts/run_fake_fleet.py --robots 3
```

다른 터미널에서 상태를 확인합니다.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/robots/synchronize
curl -s http://127.0.0.1:8000/api/v1/robots
```

세 로봇이 모두 `ONLINE`인지 확인합니다. 독립 작업 두 개를 생성합니다.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/missions \
  -H 'Content-Type: application/json' \
  -d '{
    "mission_id":"acceptance-1",
    "goal":"두 구역을 병렬 점검",
    "tasks":[
      {"task_id":"inspect-a","instruction":"A 구역 점검","required_capabilities":["inspect"]},
      {"task_id":"inspect-b","instruction":"B 구역 점검","required_capabilities":["inspect"]}
    ]
  }'

curl -s -X POST http://127.0.0.1:8000/api/v1/missions/acceptance-1/run
curl -s http://127.0.0.1:8000/api/v1/missions/acceptance-1
```

확인 기준은 두 task가 서로 다른 robot에 배정되고 모두 `SUCCEEDED`가 되는 것입니다.

Fleet emergency stop도 확인합니다.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/fleet/emergency-stop
```

응답의 `requested`와 `stopped` 수가 각각 3이어야 합니다.

## 5. 실제 Robo-Claw 2대 준비

각 로봇에서 서로 다른 robot ID와 RosGrpc endpoint를 사용합니다. 예시는 다음과 같습니다.

| 로봇 | robot ID | RosGrpc endpoint |
| --- | --- | --- |
| 로봇 A | `former-01` | `192.168.10.21:50051` |
| 로봇 B | `stretch-01` | `192.168.10.22:50051` |

각 로봇에서 RosGrpc를 활성화합니다.

```bash
./rclaw launch <robot-profile> <environment> --docker --use-grpc
```

Maestro 호스트에서 포트 연결과 Ping을 먼저 점검합니다.

```bash
nc -vz 192.168.10.21 50051
nc -vz 192.168.10.22 50051
```

Maestro에는 현재 Robo-Claw `robo.proto`와 호환되는 generated client가 포함되어 있으므로 Robo-Claw ROS workspace를 Maestro 호스트에서 source할 필요는 없습니다. Robo-Claw의 proto가 변경되면 Maestro의 `proto/robo.proto`, generated client, contract test를 함께 갱신해야 합니다.

## 6. 실제 로봇 endpoint 설정

```bash
export MAESTRO_DATABASE_URL='postgresql+psycopg://maestro:change-me@127.0.0.1:5432/maestro'
export MAESTRO_READ_TOKEN='replace-read-token'
export MAESTRO_CONTROL_TOKEN='replace-control-token'
export MAESTRO_ROBOTS_JSON='[
  {
    "robot_id":"former-01",
    "display_name":"Former 01",
    "robot_type":"former",
    "endpoint":"192.168.10.21:50051",
    "site_id":"lab",
    "map_id":"floor-1",
    "capabilities":["get_status","navigate_to","analyze_scene"]
  },
  {
    "robot_id":"stretch-01",
    "display_name":"Stretch 01",
    "robot_type":"stretch",
    "endpoint":"192.168.10.22:50051",
    "site_id":"lab",
    "map_id":"floor-1",
    "capabilities":["get_status","navigate_to","analyze_scene","grasp"]
  }
]'

.venv/bin/alembic upgrade head
.venv/bin/uvicorn robo_claw_maestro.main:app --host 0.0.0.0 --port 8000
```

## 7. 실제 로봇 상태와 읽기 전용 스킬 검증

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/robots/synchronize \
  -H 'Authorization: Bearer replace-control-token'
curl -s http://127.0.0.1:8000/api/v1/robots \
  -H 'Authorization: Bearer replace-read-token'

curl -s -X POST http://127.0.0.1:8000/api/v1/robots/former-01/skills/get_status \
  -H 'Authorization: Bearer replace-control-token' \
  -H 'Content-Type: application/json' -d '{"params":{},"timeout_sec":10}'
curl -s -X POST http://127.0.0.1:8000/api/v1/robots/stretch-01/skills/get_status \
  -H 'Authorization: Bearer replace-control-token' \
  -H 'Content-Type: application/json' -d '{"params":{},"timeout_sec":10}'
```

두 로봇의 `connection_state`가 `ONLINE`이고 robot ID가 endpoint 설정과 일치해야 합니다. ID가 다르면 Maestro는 해당 응답을 거부합니다.

## 8. 장애와 비상 정지 검증

1. 로봇 한 대의 RosGrpc 프로세스를 중지합니다.
2. `/api/v1/robots/synchronize`를 호출합니다.
3. 중지한 로봇만 `OFFLINE`으로 바뀌는지 확인합니다.
4. 프로세스를 재시작하고 다시 동기화하여 `ONLINE` 복구를 확인합니다.
5. 안전 담당자 확인 후 다음 요청을 실행합니다.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/fleet/emergency-stop
```

응답은 로봇별 성공과 실패를 분리합니다. 응답하지 않은 로봇이 있으면 전체 성공으로 판단하지 않습니다.

## 9. 현재 제한 사항

- RosGrpc transport는 개발망 전용이며 mTLS는 FleetControl 단계에서 제공합니다.
- 기존 `ExecuteTaskResponse`에는 command/task ID와 progress가 없어 Maestro가 로컬 mapping을 유지합니다.
- network timeout으로 실행 여부를 판단할 수 없으면 Task를 `UNKNOWN`으로 유지합니다. 상태 조정 전 동일 물리 작업을 임의로 재전송하지 않습니다.
- 실제 motion 및 manipulation 인수 테스트는 `hardware` opt-in 시나리오로 별도 수행합니다.
