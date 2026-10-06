# Maestro 운영 가이드

이 문서는 Robo-Claw Maestro의 상태 확인, metric, 로그, backup, 장애 대응 절차를 설명합니다.

## 상태 확인

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/ready
```

`/health`는 process와 DB 상태를 함께 표시합니다. `/ready`는 DB 연결 실패 시 HTTP 503을 반환합니다.

## Prometheus metric

```bash
curl -s http://127.0.0.1:8000/metrics
```

현재 제공하는 주요 metric은 다음과 같습니다.

- `maestro_connected_robots`
- `maestro_websocket_clients`
- `maestro_grpc_reconnect_total`
- `maestro_task_result_total`
- `maestro_emergency_stop_seconds`
- `maestro_llm_requests_total_snapshot`
- `maestro_llm_validation_errors_total_snapshot`

## 구조화 로그

`MAESTRO_LOG_LEVEL`의 기본값은 `INFO`입니다. Maestro 자체 로그는 JSON으로 출력하며 robot, mission, task, command, correlation ID를 structured field로 사용할 수 있습니다. credential과 사용자 원문 전체는 로그에 기록하지 않습니다.

## Event retention

Maestro는 기동 시와 실행 중 매시간 오래된 event를 정리합니다.

```bash
MAESTRO_EVENT_RETENTION_DAYS=30
```

Mission/Task 복구에 필요한 최신 event와 현재 aggregate snapshot은 별도로 유지됩니다.

## 위험 Skill 승인

`dangerous` capability로 선언된 skill은 control 요청만으로 실행되지 않고 `PENDING` approval을 생성합니다. 승인 실행과 거부에는 별도의 `MAESTRO_ADMIN_TOKEN`이 필요합니다.

```bash
MAESTRO_READ_TOKEN=replace-read-token
MAESTRO_CONTROL_TOKEN=replace-control-token
MAESTRO_ADMIN_TOKEN=replace-admin-token
MAESTRO_ALLOWED_SKILLS_JSON='["get_status","navigate_to","power_tool"]'
```

`MAESTRO_ALLOWED_SKILLS_JSON`을 설정하면 목록 밖의 skill은 승인 생성 없이 거부합니다. 승인 요청은 DB에 저장되므로 Maestro 재시작 후에도 유지됩니다.

## Rate limit

제어 API에는 token 또는 client IP별 process-local rate limit이 적용됩니다.

```bash
MAESTRO_CONTROL_RATE_LIMIT_PER_MINUTE=120
```

한도를 넘으면 HTTP 429와 `Retry-After: 60`을 반환합니다. 다중 API replica를 운영할 때는 gateway 또는 공유 rate-limit 저장소를 추가해야 합니다.

## PostgreSQL backup과 restore

Backup:

```bash
docker compose exec -T postgres \
  pg_dump -U maestro -d maestro -Fc > maestro.dump
```

Restore 전에는 API와 worker를 중지합니다.

```bash
docker compose stop maestro-api
docker compose exec -T postgres dropdb -U maestro --if-exists maestro
docker compose exec -T postgres createdb -U maestro maestro
docker compose exec -T postgres pg_restore -U maestro -d maestro < maestro.dump
docker compose start maestro-api
curl -fsS http://127.0.0.1:8000/ready
```

## 장애 대응

### Robot stream 단절

1. `maestro_grpc_reconnect_total` 증가를 확인합니다.
2. Robot 상태가 `OFFLINE`인지 확인합니다.
3. 진행 Task가 `UNKNOWN`이면 동일 물리 명령을 수동 재전송하지 않습니다.
4. Robot을 재기동하고 새 session ID로 연결되는지 확인합니다.
5. reconciliation event를 확인합니다.

### Emergency stop 일부 실패

Fleet emergency stop 응답의 `failed` map을 확인합니다. 일부 Robot이 응답하지 않으면 전체 성공으로 취급하지 않습니다. 현장 안전 절차로 응답하지 않은 Robot을 직접 정지합니다.

### DB 장애

1. `/ready`의 503을 확인합니다.
2. PostgreSQL container와 volume 상태를 확인합니다.
3. 최근 backup으로 restore합니다.
4. `alembic current`가 head인지 확인합니다.
5. API를 다시 시작하고 Mission/Task reconciliation 결과를 확인합니다.

## 운영 전 필수 항목

- read/control token 설정
- FleetControl mTLS 설정
- PostgreSQL backup 주기 설정
- reverse proxy의 request size와 connection limit 설정
- Prometheus scrape와 alert 설정
- event retention 기간 검토
- 실제 로봇 emergency stop 인수 테스트
