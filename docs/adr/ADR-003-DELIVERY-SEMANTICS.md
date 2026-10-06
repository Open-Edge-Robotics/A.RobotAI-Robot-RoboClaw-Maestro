# ADR-003: 명령 전달 의미론

- 상태: 승인
- 결정: at-least-once 전달과 idempotency key를 사용합니다.
- 이유: 네트워크 단절 시 exactly-once 실행을 보장할 수 없습니다.
- 결과: timeout은 실패가 아닌 `UNKNOWN`으로 기록하고 reconciliation 전에는 물리 명령을 재전송하지 않습니다.
