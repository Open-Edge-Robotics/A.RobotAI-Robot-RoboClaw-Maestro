#!/usr/bin/env python3
"""Non-destructive verification for a newly deployed Maestro host."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def request(
    base_url: str,
    path: str,
    *,
    token: str = "",
    expect_json: bool = True,
) -> tuple[int, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    req = urllib.request.Request(base_url.rstrip("/") + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            body = response.read()
            value = json.loads(body) if expect_json else body.decode("utf-8", errors="replace")
            return response.status, value
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GET {path} returned HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GET {path} failed: {exc.reason}") from exc


async def verify_websocket(base_url: str, token: str) -> None:
    try:
        import websockets
    except ImportError as exc:
        raise RuntimeError("websockets is unavailable; run with the project virtualenv") from exc
    scheme = "wss" if base_url.startswith("https://") else "ws"
    host = base_url.split("://", 1)[1].rstrip("/")
    uri = f"{scheme}://{host}/api/v1/events/stream"
    if token:
        from urllib.parse import quote

        uri += f"?token={quote(token)}"
    async with websockets.connect(uri, open_timeout=10):
        return


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)
    print(f"PASS: {message}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--expect-robot", action="append", default=[])
    parser.add_argument("--websocket", action="store_true")
    parser.add_argument("--skip-docker", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    env_file = Path(args.env_file).expanduser().resolve()
    env = {**load_env(env_file), **os.environ}
    base_url = args.base_url or f"http://127.0.0.1:{env.get('MAESTRO_PORT', '8000')}"
    read_token = env.get("MAESTRO_READ_TOKEN", "")

    try:
        status, health = request(base_url, "/health")
        check(status == 200 and health.get("status") == "ok", "API liveness")
        check(health.get("database") == "ok", "database health")

        status, ready = request(base_url, "/ready")
        check(status == 200 and ready.get("status") == "ready", "API readiness")

        status, metrics = request(base_url, "/metrics", expect_json=False)
        check(status == 200 and "maestro_connected_robots" in metrics, "Prometheus metrics")

        status, robots = request(base_url, "/api/v1/robots", token=read_token)
        check(status == 200 and isinstance(robots, list), "authenticated Robot API")
        robot_ids = {str(item.get("robot_id")) for item in robots}
        for robot_id in args.expect_robot:
            check(robot_id in robot_ids, f"expected Robot registered: {robot_id}")

        status, approvals = request(
            base_url, "/api/v1/approvals?pending_only=true", token=read_token
        )
        check(status == 200 and isinstance(approvals, list), "approval API")

        status, schema = request(base_url, "/openapi.json")
        required_paths = {
            "/api/v1/robots",
            "/api/v1/goals",
            "/api/v1/missions",
            "/api/v1/fleet/emergency-stop",
        }
        check(required_paths.issubset(schema.get("paths", {})), "required OpenAPI routes")

        if args.websocket:
            asyncio.run(verify_websocket(base_url, read_token))
            check(True, "WebSocket connection")

        if not args.skip_docker and shutil.which("docker"):
            result = subprocess.run(
                ["docker", "compose", "--env-file", str(env_file), "ps", "--status", "running"],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
            )
            check(result.returncode == 0 and "maestro-api" in result.stdout, "Docker services")

        print(f"Verification completed successfully: {base_url}")
        return 0
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
