# 2026.11.01

feat: Plan Dry-run, 단일 스킬 Plan, Behavior Tree 구성 및 planning 설정 추가

로봇 없이 Maestro의 planning 결과를 미리 검증하고, 단계별 단일 스킬 Plan을
Robo-Claw BT 노드 체계의 Behavior Tree로 변환·편집·검증·저장할 수 있게 한다.
이전 커밋(fe5de58)에서 빠진 설정 화면(web/src/Settings.tsx)과 기본 문서
(role.md, skills.md)를 함께 추가해 HEAD의 web 빌드 오류를 해소한다.

주요 변경사항:

Planning / Dry-run
- POST /api/v1/planning/dry-run을 추가한다. Mission을 만들거나 로봇에 명령하지 않고
  검증 결과, 단계(stage)별 Task, Task별 후보·배정 로봇, 경고, LLM prompt와 원본 응답을 반환한다.
- robots.md 로봇 프로필을 가상 로봇으로 사용해 연결된 로봇 없이도 검증한다.
  같은 robot_id는 연결된 로봇 정보를 우선한다.
- planning 문서(role, skills, mcp, troubleshooting, robots)를 system prompt에 포함한다.
  기본 safety 규칙보다 우선하지 않도록 operator guidance로 구분한다.
- 배정 로봇 제안: preferred_robot_id 우선, 의존 단계는 앞 단계를 실행한 로봇에 이어 배정,
  나머지는 같은 단계 안에서 부하가 적은 로봇에 분산한다.

단일 스킬 Plan
- MAESTRO_PLAN_SINGLE_SKILL(기본 true): Task 하나를 스킬 하나 호출(skill_name + params,
  required_capabilities=[skill_name])로 강제하고 system prompt에 규칙을 추가한다.
- Task별 on_failure(abort | continue)와 Plan 최상위 repeat(interval_sec, max_cycles)을 지원·검증한다.
- MAESTRO_PLAN_MAX_TASKS(기본 100, 1~500)로 Plan 최대 Task 수를 조정한다. (기존 고정값 20)
- LLM 응답의 숫자·params 형식 오류와 Task 생성 오류를 500 대신 422로 반환한다.

LLM provider 오류 처리
- provider 호출 실패(DNS, 인증, 네트워크 등)를 LlmProviderError로 감싸
  /goals, /replan, /dry-run에서 500 대신 502와 원인 메시지로 반환한다.

Behavior Tree
- agent/behavior_tree.py: Sequence/Fallback/Parallel, Action/Condition, AlwaysSuccess,
  SleepBetweenCycles 노드로 구성한다. (Robo-Claw skills/autonomous_skill BT 엔진과 동일 체계)
- Plan → BT 결정적 변환: 단계 내 Parallel, 단계 간 Sequence, on_failure=continue는
  Fallback[Action, AlwaysSuccess], 로봇별 배터리 Condition, repeat은 autonomous_act patrol
  구조(Fallback[CycleWork, CycleDone] + SleepBetweenCycles)로 만든다.
- 검증: 노드 구조, Parallel.success_count, 로봇 존재 여부, 로봇의 스킬 보유 여부(연결 로봇 + 프로필).
- 저장소: MAESTRO_KNOWLEDGE_DIR/behavior_trees/<name>.json
- API: GET /api/v1/behavior-trees, GET /catalog, POST /from-plan, POST /validate,
  GET·PUT·DELETE /api/v1/behavior-trees/{name}
- 실행 기능은 포함하지 않는다. (구성·검증·저장까지)

Web Console
- 설정 탭: planning 문서 편집, Plan Dry-run, 결과에서 "Behavior Tree로 변환".
- Behavior Tree 탭: 트리 보기, 노드 추가·삭제·이동, 속성 편집(로봇별 스킬 목록), JSON 편집,
  자동 검증과 오류 노드 표시, 저장·열기·삭제.
- 탭을 전환해도 Dry-run 결과와 편집 중인 트리가 유지되도록 화면을 mount 상태로 유지한다.

기본 planning 문서
- role.md: 단일 스킬 출력 형식, 여러 구역 누락 없이 계획, 부재 중 작업 기준표
  (조회·이동·정리는 수행, 고위험 조작은 제외하고 summary에 기록).
- skills.md: 주요 스킬 params 표, 로봇·구역 수와 무관한 구역 정리 체인 패턴.
- robots.md: 담당 구역·출입 제한 작성 규칙, 예시 프로필에 담당 구역 명시,
  cloid02에 tidy_home capability 추가.

설정 / 배포
- .env.example, docker-compose.yml: MAESTRO_PLAN_SINGLE_SKILL, MAESTRO_PLAN_MAX_TASKS 추가.
- README: 단일 스킬 Plan, Behavior Tree 구성, 신규 API, 테스트 기준 갱신.

알려진 제한:
- Mission dispatcher는 여전히 Task를 자연어 ExecuteTask로 전송하며 skill_name을 사용하지 않는다.
- 로봇 capability input schema가 비어 있어 params 값 자체는 검증하지 않는다.
- 외부 MCP는 planning 참고 문서로만 사용하며 실제 호출하지 않는다.

추가 파일:
- src/robo_claw_maestro/agent/behavior_tree.py
- src/robo_claw_maestro/agent/knowledge_defaults/role.md
- src/robo_claw_maestro/agent/knowledge_defaults/skills.md
- web/src/Settings.tsx
- web/src/BehaviorTree.tsx
- tests/unit/test_behavior_tree.py

검증:
- pytest: 120 passed
- ruff check / ruff format --check 통과
- web: tsc -b 통과, vite build 성공
- 브라우저 수동 UI 확인은 수행하지 않음

🤖 Generated with Claude Code
