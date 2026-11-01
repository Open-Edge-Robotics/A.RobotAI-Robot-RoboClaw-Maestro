# 로봇 프로필

연결될 로봇의 특성과 역할을 적습니다. 실제 로봇이 연결되어 있지 않아도 Dry-run에서 이 프로필을 가상 로봇으로 사용해 계획을 검증합니다.

작성 규칙:

- 로봇마다 `## <robot_id>` 제목을 씁니다. robot_id는 로봇 설정의 `ROBOT_ID`와 같아야 합니다.
- `- capabilities:` 줄에 쉼표로 capability 이름을 적습니다. Robo-Claw `docs/SKILLS.md`의 스킬 이름을 그대로 씁니다.
- `- robot_type:`, `- site_id:`, `- map_id:` 는 선택입니다.
- `- 담당 구역:` 줄에 로봇이 맡는 장소를 쉼표로 적습니다. 장소 이름은 로봇 지도에 기억된 장소명(`navigate_to`의 `target_name`)과 같아야 합니다.
- `- 출입 제한:` 줄에는 그 로봇이 갈 수 없는 장소나 조건을 적습니다. 없으면 생략합니다.
- 그 밖의 문장은 로봇의 특징과 역할 설명으로 planner에 전달됩니다.

## cloid01

- robot_type: cloid
- site_id: lab
- map_id: floor-1
- capabilities: get_status, get_location, navigate_to, describe_surroundings, analyze_scene, scan_room, find_object, tidy_home, cloid_express, stop
- 담당 구역: 거실, 주방
- 출입 제한: 문턱이 있는 장소

바퀴형 가정용 로봇입니다. 사람과 대화하거나 표정으로 반응하는 역할을 우선 맡깁니다.

## cloid02

- robot_type: cloid
- site_id: lab
- map_id: floor-1
- capabilities: get_status, get_location, navigate_to, describe_surroundings, analyze_scene, scan_room, find_object, tidy_home, patrol, stop
- 담당 구역: 침실, 현관

순찰(patrol)과 물건 찾기도 주로 맡깁니다.
