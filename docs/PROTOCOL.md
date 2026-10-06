# FleetControl 프로토콜

이 문서는 Robo-Claw robot이 Maestro로 outbound 연결하는 FleetControl v1 계약과 개발 환경 검증 방법을 설명합니다.

## 연결 방식

```protobuf
service FleetControl {
  rpc Connect(stream RobotToMaestro) returns (stream MaestroToRobot);
}
```

Robot은 연결 직후 반드시 첫 메시지로 `Register`를 보냅니다. Maestro는 protocol version과 robot identity를 검증한 뒤 `RegistrationResult`를 반환합니다.

현재 지원하는 robot 메시지는 다음과 같습니다.

- `Register`
- `Heartbeat`
- `Telemetry`
- `CommandAck`
- `TaskUpdate`

현재 지원하는 Maestro 명령은 다음과 같습니다.

- `ExecuteTaskCommand`
- `ExecuteSkillCommand`
- `CancelTaskCommand`
- `EmergencyStopCommand`

ACK는 명령 수락만 의미합니다. 완료 여부는 별도 `TaskUpdate`로 전달합니다. stream 단절 전송은 성공이나 실패로 단정하지 않고 호출 측에서 `UNKNOWN`으로 처리합니다.

## 개발 서버 활성화

```bash
export MAESTRO_FLEET_CONTROL_ENABLED=true
export MAESTRO_FLEET_CONTROL_HOST=0.0.0.0
export MAESTRO_FLEET_CONTROL_PORT=50053
.venv/bin/uvicorn robo_claw_maestro.main:app --port 8000
```

별도 터미널에서 outbound fake robot 두 대를 실행합니다.

```bash
.venv/bin/python scripts/run_fake_fleet_control_robot.py --robot-id fleet-robot-1
.venv/bin/python scripts/run_fake_fleet_control_robot.py --robot-id fleet-robot-2
```

Robot은 `MAESTRO_ROBOTS_JSON`에 미리 등록하지 않아도 Registry에 나타납니다.

```bash
curl -s http://127.0.0.1:8000/api/v1/robots
```

## mTLS

Maestro server 설정은 다음 환경 변수를 사용합니다.

```bash
export MAESTRO_FLEET_TLS_CERTIFICATE=/run/secrets/maestro.crt
export MAESTRO_FLEET_TLS_PRIVATE_KEY=/run/secrets/maestro.key
export MAESTRO_FLEET_TLS_CLIENT_CA=/run/secrets/robot-ca.crt
```

세 값 중 server certificate와 private key를 지정하면 TLS를 사용합니다. Client CA도 지정하면 client certificate를 필수로 검증합니다. 운영 환경에서는 세 값을 모두 설정합니다. mTLS 사용 시 client certificate의 peer identity(CN)가 등록하는 `robot_id`와 정확히 일치해야 합니다.

Fake robot client는 다음 옵션을 지원합니다.

```bash
.venv/bin/python scripts/run_fake_fleet_control_robot.py \
  --target maestro.example:50053 \
  --robot-id robot-1 \
  --ca /run/secrets/ca.crt \
  --certificate /run/secrets/robot-1.crt \
  --key /run/secrets/robot-1.key
```

## 세션과 중복 방지

- robot 재부팅마다 새로운 `session_id`를 생성합니다.
- 같은 robot ID의 새 session이 연결되면 이전 session pending command를 실패 처리합니다.
- 모든 명령은 `command_id`와 `idempotency_key`를 포함합니다.
- robot은 완료 결과를 idempotency key로 영속 보관하고 중복 물리 동작 없이 동일 결과를 반환해야 합니다.
- outbound queue가 가득 차면 새 명령을 거부합니다.

## Proto 갱신

`proto/fleet_control.proto` 또는 `proto/robo.proto` 변경 후 다음을 실행합니다.

```bash
./scripts/generate_protos.sh
.venv/bin/pytest -m contract
```
