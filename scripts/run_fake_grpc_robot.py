#!/usr/bin/env python3
"""Run one fake Robo-Claw RosGrpc server."""

from __future__ import annotations

import argparse
import asyncio

from robo_claw_maestro.testing_grpc import start_fake_robot


async def serve(robot_id: str, host: str, port: int, battery: float) -> None:
    robot = await start_fake_robot(robot_id, port, host=host, battery_percentage=battery)
    print(f"fake robot {robot.robot_id} listening on {robot.endpoint}", flush=True)
    try:
        await robot.server.wait_for_termination()
    finally:
        await robot.stop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--battery", type=float, default=80.0)
    args = parser.parse_args()
    asyncio.run(serve(args.robot_id, args.host, args.port, args.battery))


if __name__ == "__main__":
    main()
