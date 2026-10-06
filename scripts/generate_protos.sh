#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/src/robo_claw_maestro/adapters/grpc/generated"
mkdir -p "$OUT"
touch "$OUT/__init__.py"
for proto in robo fleet_control; do
  "$ROOT/.venv/bin/python" -m grpc_tools.protoc \
    -I "$ROOT/proto" \
    --python_out="$OUT" \
    --grpc_python_out="$OUT" \
    "$ROOT/proto/${proto}.proto"
done
python3 - "$OUT" <<'PY'
from pathlib import Path
import sys

out = Path(sys.argv[1])
for name in ("robo", "fleet_control"):
    path = out / f"{name}_pb2_grpc.py"
    text = path.read_text()
    text = text.replace(
        f"import {name}_pb2 as {name.replace('_', '__')}__pb2",
        f"from . import {name}_pb2 as {name.replace('_', '__')}__pb2",
    )
    path.write_text(text)
PY
