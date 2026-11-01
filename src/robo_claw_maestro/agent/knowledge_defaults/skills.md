# Maestro 스킬

Maestro가 계획을 세울 때 쓰는 작업 패턴과 로봇 스킬 인자입니다. 각 단계는 스킬 하나입니다. 스킬 이름은 fleet catalog 또는 로봇 프로필에 있는 것만 사용합니다.

## 주요 스킬 인자

| 스킬 | 주요 params | 설명 |
| --- | --- | --- |
| `get_status` | (없음) | 배터리·위치 조회 |
| `navigate_to` | `target_name`(장소명) 또는 `x`,`y` | 목적지 이동 |
| `describe_surroundings` | `use_vlm`(bool), `return_to_start`(bool) | 주변 관찰·설명 |
| `scan_room` | `max_turn_deg`, `step_deg` | 360도 스캔 후 객체 등록 |
| `analyze_scene` | `prompt` | 현재 카메라 장면 VLM 분석 |
| `find_object` | `target_object` | 특정 물체 탐지 |
| `tidy_home` | `scope`(`room`/`home`), `target_location`, `max_places` | 관찰 후 정리 대상 확인·정리 |
| `patrol` | `waypoints`(장소명 배열), `rounds` | 장소 순찰 |
| `stop` | (없음) | 현재 동작 정지 |

## 패턴: 구역 정리 (한 사이클)

로봇 수와 구역 수에 관계없이 같은 규칙으로 만듭니다.

1. 로봇마다 `get_status` 하나씩 병렬로 둡니다.
2. 구역 하나마다 `navigate_to` → `scan_room` → `tidy_home` 세 단계 체인을 만듭니다.
   - `navigate_to` `{"target_name": "<구역>"}`
   - `scan_room` — 정리 대상 파악. 실패해도 정리는 시도하도록 `on_failure: "continue"`
   - `tidy_home` `{"scope": "room", "target_location": "<구역>"}`
3. 한 로봇이 구역 여러 곳을 맡으면 체인을 **차례로 이어 붙입니다.** 다음 구역의 `navigate_to`는 앞 구역의 `tidy_home`에 `depends_on`으로 연결합니다. 첫 구역의 `navigate_to`는 그 로봇의 `get_status`에 연결합니다.
4. 서로 다른 로봇의 체인은 연결하지 않아 병렬로 실행되게 합니다.
5. 한 구역의 정리가 실패해도 그 로봇의 다음 구역은 계속하도록 `tidy_home`은 `on_failure: "continue"`로 둡니다.
6. `task_id`는 `<스킬>-<구역>` 형식으로 씁니다. 예: `go-kitchen`, `scan-kitchen`, `tidy-kitchen`

예) 로봇 A(거실, 주방), 로봇 B(침실, 현관):

```
A: status-a → go-livingroom → scan-livingroom → tidy-livingroom → go-kitchen → scan-kitchen → tidy-kitchen
B: status-b → go-bedroom → scan-bedroom → tidy-bedroom → go-entrance → scan-entrance → tidy-entrance
```

주기적으로 반복하라는 요청이면 최상위 `repeat`을 추가합니다.

## 패턴: 상태 확인

- 각 로봇에 `get_status` 하나씩, `depends_on` 없이 병렬로 둡니다.

## 패턴: 물건 찾기

- 구역별로 `navigate_to` → `find_object` `{"target_object": "열쇠"}` 순서를 로봇마다 병렬로 둡니다. 찾지 못해도 다른 구역은 계속하도록 `find_object`는 `on_failure: "continue"`로 둡니다.
