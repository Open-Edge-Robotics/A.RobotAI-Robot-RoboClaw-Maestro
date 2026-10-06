#!/usr/bin/env python3
"""Run Maestro with an in-memory fleet for local acceptance testing."""

from __future__ import annotations

import argparse

import uvicorn

from robo_claw_maestro.testing import build_fake_app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--robots", type=int, default=3)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(build_fake_app(args.robots), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
