# ADR-001: MVP 전송 계층으로 RosGrpc 사용

- 상태: 승인
- 결정: MVP는 기존 Robo-Claw `RosGrpc` API를 adapter로 사용합니다.
- 이유: 로봇 변경 없이 중앙 관제와 작업 실행을 먼저 검증할 수 있습니다.
- 제약: 인증이 없으므로 개발망에서만 사용하며 운영 단계에서는 FleetControl과 mTLS로 교체합니다.
