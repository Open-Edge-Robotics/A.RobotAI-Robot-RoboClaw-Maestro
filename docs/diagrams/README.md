# Maestro 기술 문서 그림

[기술 문서](../TECHNICAL_DOCUMENT.md)에 삽입한 8개 그림의 원본과 출력 파일이다.

- `.dot`: 수정 가능한 Graphviz 텍스트 원본. 노드, 화살표, 설명을 편집한다.
- `.svg`: Markdown에 삽입하는 벡터 이미지. 확대해도 선명하며 브라우저에서 바로 열 수 있다.
- 그림은 기준 구현의 역할·흐름을 설명하는 개념도다. 코드·프로토콜 변경 시 문서와 함께 갱신한다.

| 번호 | 파일명 (확장자 제외) | 문서 위치 |
| --- | --- | --- |
| 1 | `01-system-architecture` | 2.1 전체 시스템 구성도 |
| 2 | `02-software-layers` | 2.2 구성 요소와 역할 |
| 3 | `03-task-dependency` | 3.3 Task 의존성과 병렬 실행 |
| 4 | `04-robot-connections` | 4.2 로봇 연결 방식 비교 |
| 5 | `05-planning-execution` | 5.1 계획 생성과 실행 |
| 6 | `06-behavior-tree` | 5.7 Behavior Tree |
| 7 | `07-safety-approval` | 6.3 안전 검사와 승인 |
| 8 | `08-restart-reconciliation` | 10.5 재시작 복구 |

## 수정과 재생성

Graphviz의 `dot` 명령과 한글 글꼴이 필요하다. 이 그림은 `Noto Sans CJK KR` 글꼴을 사용한다. SVG를 읽는 환경에도 해당 글꼴 또는 한글을 표시하는 대체 글꼴이 있어야 한다.

Maestro 저장소 루트에서 한 그림을 재생성한다.

```bash
dot -Tsvg docs/diagrams/01-system-architecture.dot \
  -o docs/diagrams/01-system-architecture.svg
```

전체 그림 재생성:

```bash
for source in docs/diagrams/*.dot; do
  dot -Tsvg "$source" -o "${source%.dot}.svg"
done
```

PNG가 필요한 문서 편집기로 가져가려면 다음처럼 출력한다. PNG는 기본 저장 파일이 아니며 필요한 경우 생성한다.

```bash
dot -Tpng -Gdpi=160 docs/diagrams/01-system-architecture.dot \
  -o /tmp/maestro-system-architecture.png
```

## 표현 규칙

- 파랑: API, 상태 관리, 인터페이스, 일반 처리.
- 보라: LLM 계획 또는 BT 제어·승인.
- 초록: 실행과 Robot.
- 노랑: 저장소·문서·판단 조건.
- 빨강: 거부·실패·복구 제약.
- 선의 의미는 그림별 캡션을 따른다. 연결 시작 방향, 데이터 교환, 의존성, 트리 부모·자식은 서로 다르다.
- 그림 2는 전체 import 그래프가 아니며, 그림 5는 명시적 실행 절차, 그림 7은 BT 승인 경로를 나타낸다.

SVG 링크가 정상적으로 표시되지 않는 Markdown 편집기에서는 브라우저로 SVG를 열거나 PNG로 변환한다. `.dot` 원본과 `.svg` 결과를 함께 관리하여 편집 가능성과 재현성을 유지한다.
