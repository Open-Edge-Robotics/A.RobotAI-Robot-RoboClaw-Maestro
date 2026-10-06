# ADR-004: LLM과 Orchestrator 책임 분리

- 상태: 승인
- 결정: LLM은 계획 제안만 하고 상태 전이, 검증, 배정, 재시도는 결정론적 application/domain 코드가 수행합니다.
- 결과: LLM 출력은 schema, capability, DAG, safety 검증을 통과하기 전 실행되지 않습니다.
