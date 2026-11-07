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

# 2026.11.06

fix(web): Behavior Tree 새 노드에 고유 이름을 붙이고 선택 노드 표시에서 종류와 이름을 구분

Behavior Tree 편집기에서 자식 추가로 만든 노드의 기본 이름이 노드 종류와 같아,
선택 노드 제목이 "Parallel Parallel"처럼 반복되어 어떤 노드인지 알기 어려웠다.

주요 변경사항:
- 새 노드 기본 이름을 트리 안에서 겹치지 않는 번호 이름으로 생성한다.
  (parallel-1, sequence-1, fallback-1, action-1, condition-1, sleep-1, success-1)
- 선택 노드 제목을 "[종류] · 이름: <이름>" 형식으로 표시하고, 이름이 비어 있으면 (없음)으로 표시한다.
- 노드 종류 배지 스타일(.bt-type)을 추가한다.
- 기존 트리에 저장된 노드 이름은 변경하지 않는다.

검증:
- web: tsc -b 통과, vite build 성공
- 브라우저 수동 UI 확인은 수행하지 않음

변경 파일:
- web/src/BehaviorTree.tsx
- web/src/styles.css
- commit.md

🤖 Generated with Claude Code

# 2026.11.07

feat(mission): Mission Behavior Tree 실행, ExecuteSkill 전송, 승인·재실행·동료 작업 처리와 Console 개선

계획 생성 시 Mission에 Behavior Tree를 함께 저장하고 Maestro가 그 트리를 실행한다.
각 Action은 지정 로봇에 ExecuteSkill(skill + params)로 보내므로 로봇이 자연어 지시를 다시
해석하지 않고 계획한 스킬을 그대로 실행한다. 로봇 사이의 순서, 승인, 재시도, 반복은 Maestro가 관리한다.
로봇이 보고한 동료 작업도 Maestro를 거쳐 실행한다(Robo-Claw 2026.11.07 변경과 함께 동작).

주요 변경사항:

Mission Behavior Tree 실행
- 계획 생성(/goals, replan)에서 plan의 repeat·on_failure와 로봇 배정(ONLINE 로봇 우선)으로 BT를 만들어
  Mission constraints(behavior_tree, repeat, on_failure, planned_robots)에 저장한다.
  전체 배터리 Condition은 두지 않고 Action마다 실행 직전에 safety policy로 검사한다.
- 트리 구조: depends_on으로 이어진 체인마다 Sequence(단계 내 Parallel)로 만들고, 독립 체인은
  Parallel(Independent chains)로 나란히 둔다. 한 로봇(체인)의 실패가 의존 관계 없는 다른 로봇을 취소하지 않는다.
- application/bt_runner.py(MissionTreeRunner): Sequence/Fallback/Parallel/Action/Condition/AlwaysSuccess/
  SleepBetweenCycles를 실행하고 노드별 상태(RUNNING/SUCCESS/FAILURE/WAITING_APPROVAL)를 기록한다.
  - Action은 같은 이름의 Mission Task를 TaskDispatcher로 실행하고, 로봇별로 한 번에 하나씩 실행한다.
  - 지정 로봇이 safety policy를 통과하지 못하면 안전한 로봇으로 대체한다. preferred_robot_id가 있으면 대체하지 않는다.
  - max_attempts만큼 재시도하고 UNKNOWN은 재전송하지 않는다.
  - 결과: 모두 성공 SUCCEEDED, 일부 성공 PARTIALLY_SUCCEEDED, 성공 없음 FAILED. 도달하지 못한 Task는 CANCELLED.
  - repeat은 사이클마다 Task를 CREATED로 돌리고(Task.reset_for_cycle) 사이클별 idempotency key(:c<n>)로 보낸다.
  - 실행 중 일시정지·재개·취소는 runner를 거쳐 Mission을 한 곳에서만 갱신한다. 1회 실행과 replan은 409로 막는다.
- 끝난 Mission 재실행: Mission.reopen, Task.reset_for_rerun, POST /api/v1/missions/{id}/rerun.
  실패부터(성공한 Task는 건너뜀) 또는 처음부터 실행하고, 재실행 Task는 :r<n> idempotency key를 쓴다.
  재실행 시 저장된 트리를 다시 만든다.
- behavior_tree.py: split_by_robot(로봇별 보기), with_paths(노드 경로), tree_robots, 체인 단위 plan_to_tree.
- API: GET /api/v1/missions/{id}/behavior-tree, POST /api/v1/missions/{id}/behavior-tree/run,
  POST /api/v1/missions/{id}/rerun. Mission 응답에 behavior_tree_run 요약을 추가하고 /missions를 최신순으로 정렬한다.

Task 전송과 안전
- MAESTRO_DISPATCH_MODE(skill | instruction, 기본 skill): skill_name이 있는 Task를 ExecuteSkill로 보낸다.
  preferred_robot_id, resources, followup_of 등 Maestro 라우팅 값은 스킬 params에서 제외한다.
- execute_skill gateway(port, FleetControl, RosGrpc, multi-robot, fake)에 task_id, mission_id,
  idempotency_key, attempt_id, correlation_id를 전달한다.
- MAESTRO_ALLOWED_SKILLS_JSON에 없는 스킬은 보내지 않고 SKILL_NOT_DISPATCHABLE로 실패 처리한다.
- dangerous 스킬과 requires_approval Task: BT 실행 중에는 WAITING_APPROVAL로 기다리며 mission_task 승인 요청을
  만든다(ApprovalService.request_mission_task/current/withdraw/complete). 승인하면 실행하고, 거부·만료되면
  그 Action이 실패한다. 1회 실행에서는 승인 없이 보내지 않는다.
- MAESTRO_BATTERY_EXEMPT_ROBOTS: 배터리 센서가 고장 난 로봇의 이동 스킬 배터리 기준 검사만 건너뛴다.
- Mission 취소 중 로봇 결과가 도착하면 Task를 CANCELLED(성공) 또는 FAILED로 마무리한다.

동료 작업(peer_requests) 처리
- 로봇 결과의 peer_requests마다 후속 Task(<task>-peer-<n>)를 만들어 제안 동료에게 ExecuteTask로 보낸다.
- 항상 사람 승인 후 실행하고 motion으로 취급(배터리 정책 적용)하며 다른 로봇으로 대체하지 않는다.
  제안 동료가 없거나 보고한 로봇 자신이면 실행하지 않는다.
- 후속 작업이 실패해도 원래 Action은 성공으로 두고 Mission은 PARTIALLY_SUCCEEDED가 된다.
- 반복 사이클과 처음부터 재실행 때 후속 Task를 지우고 다시 만든다.

Web Console
- Mission 목록 최신순 정렬과 생성 시각 표시.
- 계획 생성 중 버튼 잠금, 진행 바와 경과 시간, 완료(Task 수, 소요 시간)·실패 결과 표시.
- "검증된 계획 생성"을 "계획 생성"으로 변경, 버튼별 동작 상태 설명(접힘)과 tooltip.
- Mission 카드: "BT 실행", "실패부터 재실행", "처음부터 재실행" 버튼과 "Behavior Tree (로봇별)" 보기
  (노드별 상태, 승인 대기 표시, 동료 작업 표시, 실행 중 2초 갱신).
- Task 행에 skill과 지정 로봇 표시.
- Event Timeline을 접힘으로 옮기고 telemetry·heartbeat 제외. 화면 갱신을 10초 주기로 통일.

설정 / 문서
- .env.example, docker-compose.yml: MAESTRO_DISPATCH_MODE, MAESTRO_BATTERY_EXEMPT_ROBOTS 추가.
- role.md: on_failure abort가 의존 체인만 멈춘다는 설명과 불필요한 로봇 간 의존을 만들지 말라는 지침.
- README: Task 전송 방식, 승인, Mission Behavior Tree 실행, 재실행, 동료 작업, 신규 API, 제한 사항.

알려진 제한:
- BT 실행 상태는 Maestro 메모리에만 있다. 실행 중 재시작하면 트리는 다시 시작되지 않는다.
- 로봇이 수행 중인 Action은 원격 취소되지 않는다.
- 로봇 capability input schema가 비어 있어 스킬 params 값 자체는 검증하지 않는다.
- 동료 작업 처리는 Robo-Claw가 peer_requests를 보고할 때만 동작한다(이전 Robo-Claw는 동료를 직접 호출).

변경 파일:
- 추가: src/robo_claw_maestro/application/bt_runner.py, tests/component/test_bt_runner.py, web/src/MissionTree.tsx
- application: approval_service.py, mission_orchestrator.py, scheduler.py, task_dispatcher.py
- domain: command.py, mission.py, policies.py, task.py
- agent: behavior_tree.py, maestro_agent.py, knowledge_defaults/role.md
- adapters/grpc: fleet_control_server.py, multi_robot.py, ros_grpc_client.py
- api/app.py, config.py, main.py, ports/robot_gateway.py
- tests/integration/test_api.py
- web/src/main.tsx, web/src/styles.css
- .env.example, docker-compose.yml, README.md, commit.md

검증:
- pytest: 165 passed
- ruff check / ruff format --check 통과
- web: tsc -b 통과, vite build 성공
- 실제 로봇 연동과 브라우저 수동 UI 확인은 수행하지 않음

🤖 Generated with Claude Code
