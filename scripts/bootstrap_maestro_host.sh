#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
BUILD=true
START=true
FORCE_ENV=false

usage() {
  cat <<'EOF'
Usage: scripts/bootstrap_maestro_host.sh [options]
  --env-file PATH  Environment file to create/use (default: .env)
  --no-build       Start existing images without rebuilding
  --no-start       Prepare and validate configuration only
  --force-env      Replace the selected environment file
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) ENV_FILE="$(realpath -m "$2")"; shift 2 ;;
    --no-build) BUILD=false; shift ;;
    --no-start) START=false; shift ;;
    --force-env) FORCE_ENV=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

for command in docker python3 curl; do
  command -v "$command" >/dev/null || {
    echo "Required command is missing: $command" >&2
    exit 1
  }
done
docker compose version >/dev/null 2>&1 || {
  echo "Docker Compose v2 is required (docker compose)." >&2
  exit 1
}
docker info >/dev/null 2>&1 || {
  echo "Docker daemon is unavailable or the current user lacks permission." >&2
  exit 1
}

if [[ -e "$ENV_FILE" && "$FORCE_ENV" != true ]]; then
  echo "Using existing environment file: $ENV_FILE"
else
  mkdir -p "$(dirname "$ENV_FILE")"
  cp "$ROOT/.env.example" "$ENV_FILE"
  python3 - "$ENV_FILE" <<'PY'
from pathlib import Path
import secrets
import sys

path = Path(sys.argv[1])
password = secrets.token_hex(24)
values = {
    "POSTGRES_PASSWORD": password,
    "MAESTRO_DATABASE_URL": f"postgresql+psycopg://maestro:{password}@postgres:5432/maestro",
    "MAESTRO_READ_TOKEN": secrets.token_urlsafe(32),
    "MAESTRO_CONTROL_TOKEN": secrets.token_urlsafe(32),
    "MAESTRO_ADMIN_TOKEN": secrets.token_urlsafe(32),
}
lines = path.read_text(encoding="utf-8").splitlines()
seen = set()
result = []
for line in lines:
    key = line.split("=", 1)[0] if "=" in line and not line.lstrip().startswith("#") else ""
    if key in values:
        result.append(f"{key}={values[key]}")
        seen.add(key)
    else:
        result.append(line)
for key, value in values.items():
    if key not in seen:
        result.append(f"{key}={value}")
path.write_text("\n".join(result) + "\n", encoding="utf-8")
PY
  chmod 600 "$ENV_FILE"
  echo "Created environment file with generated credentials: $ENV_FILE"
fi

cd "$ROOT"
docker compose --env-file "$ENV_FILE" config >/dev/null
echo "Docker Compose configuration is valid."

if [[ "$START" != true ]]; then
  echo "Preparation complete; services were not started."
  exit 0
fi

args=(up -d)
if [[ "$BUILD" == true ]]; then
  args+=(--build)
fi
docker compose --env-file "$ENV_FILE" "${args[@]}"

ports="$(python3 - "$ENV_FILE" <<'PY'
from pathlib import Path
import sys
values = {}
for line in Path(sys.argv[1]).read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
print(values.get("MAESTRO_PORT", "8000"), values.get("MAESTRO_WEB_PORT", "3000"))
PY
)"
read -r api_port web_port <<<"$ports"
base_url="http://127.0.0.1:${api_port}"
for _ in $(seq 1 60); do
  if curl -fsS "${base_url}/ready" >/dev/null 2>&1; then
    echo "Maestro is ready: $base_url"
    echo "Console: http://127.0.0.1:${web_port}"
    echo "Run verification: scripts/verify_maestro_host.py --env-file '$ENV_FILE'"
    exit 0
  fi
  sleep 2
done

echo "Maestro did not become ready within 120 seconds." >&2
docker compose --env-file "$ENV_FILE" ps >&2
docker compose --env-file "$ENV_FILE" logs --tail=100 maestro-api >&2
exit 1
