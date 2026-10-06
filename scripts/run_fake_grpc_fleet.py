#!/usr/bin/env python3
"""Run two or more fake RosGrpc robots in one process."""

from __future__ import annotations

import argparse
import asyncio

from robo_claw_maestro.testing_grpc import start_fake_robot


async def serve(count: int, host: str, base_port: int) -> None:
    if count < 2:
        raise ValueError("fleet must contain at least two robots")
    robots = [
        await start_fake_robot(
            f"robot-{index}",
            base_port + index - 1,
            host=host,
            battery_percentage=90 - index * 5,
        )
        for index in range(1, count + 1)
    ]
    for robot in robots:
        print(f"{robot.robot_id}: {robot.endpoint}", flush=True)
    try:
        await asyncio.gather(*(robot.server.wait_for_termination() for robot in robots))
    finally:
        await asyncio.gather(*(robot.stop() for robot in robots))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--robots", type=int, default=3)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--base-port", type=int, default=55051)
    args = parser.parse_args()
    asyncio.run(serve(args.robots, args.host, args.base_port))


if __name__ == "__main__":
    main()
