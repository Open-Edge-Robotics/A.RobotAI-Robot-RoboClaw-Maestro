# Git 변경 파일을 시간순으로 확인하기

`git status`에 표시되는 수정 파일(`M`)과 추적되지 않은 파일(`??`)을 파일 시스템의 마지막 수정 시간 기준으로 오래된 순서대로 출력합니다.

```bash
git status --porcelain=v1 -uall |
while read -r status path; do
  printf '%s\t%s\t%s\n' \
    "$(stat -c '%Y' "$path")" "$status" "$path"
done |
sort -n |
while IFS=$'\t' read -r timestamp status path; do
  printf '%s\t%s\t%s\n' \
    "$(date -d "@$timestamp" '+%Y-%m-%d %H:%M:%S %z')" \
    "$status" "$path"
done
```

## 상태 코드

- `M`: 기존 파일이 수정됨
- `??`: Git이 아직 추적하지 않는 새 파일

`-uall` 옵션을 사용하면 `git status`에서 디렉터리 하나로 묶여 표시되는 추적되지 않은 파일도 개별적으로 확인할 수 있습니다.

> 표시되는 시간은 Git의 변경 이력이 아니라 현재 파일 시스템의 마지막 수정 시간(mtime)입니다.

# 특정 파일만 커밋하기

지정한 파일만 스테이징하고 커밋합니다. 아래 명령에 포함되지 않은 다른 변경 파일은 커밋되지 않습니다.

```bash
git add -- \
  src/robo_claw_maestro/agent/knowledge_defaults/mcp.md \
  src/robo_claw_maestro/agent/knowledge_defaults/robots.md \
  src/robo_claw_maestro/agent/knowledge_defaults/troubleshooting.md \
  pyproject.toml \
  src/robo_claw_maestro/agent/knowledge.py \
  tests/unit/test_knowledge.py \
  docker-compose.yml \
  .env.example \
  web/src/styles.css \
  web/src/main.tsx \
  README.md

git commit -m "Add knowledge defaults and update configuration"
```

커밋 전에 스테이징된 파일을 확인하려면 다음 명령을 실행합니다.

```bash
git diff --cached --name-status
```
