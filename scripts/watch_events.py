#!/usr/bin/env python3
"""Watch Maestro fleet events without requiring websocat."""

from __future__ import annotations

import argparse
import asyncio
import json
from urllib.parse import quote

import websockets


async def watch(url: str, token: str, max_events: int, timeout_sec: float) -> None:
    separator = "&" if "?" in url else "?"
    uri = f"{url}{separator}token={quote(token)}" if token else url
    received = 0
    async with websockets.connect(uri, open_timeout=timeout_sec) as socket:
        print(f"connected: {url}", flush=True)
        while max_events <= 0 or received < max_events:
            try:
                raw = await asyncio.wait_for(socket.recv(), timeout=timeout_sec)
            except TimeoutError:
                print(f"no event received for {timeout_sec:g} seconds", flush=True)
                return
            try:
                event = json.loads(raw)
                print(json.dumps(event, ensure_ascii=False, indent=2), flush=True)
            except json.JSONDecodeError:
                print(raw, flush=True)
            received += 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url",
        default="ws://127.0.0.1:8000/api/v1/events/stream",
    )
    parser.add_argument("--token", default="")
    parser.add_argument(
        "--max-events",
        type=int,
        default=0,
        help="0 means keep watching until interrupted",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    if args.max_events < 0 or args.timeout <= 0:
        parser.error("max-events must be >= 0 and timeout must be positive")
    try:
        asyncio.run(watch(args.url, args.token, args.max_events, args.timeout))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
