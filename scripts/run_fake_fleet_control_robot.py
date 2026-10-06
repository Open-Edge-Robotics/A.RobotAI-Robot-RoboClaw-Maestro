#!/usr/bin/env python3
"""Run an outbound fake robot against the FleetControl stream server."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from uuid import uuid4

import grpc

from robo_claw_maestro.adapters.grpc.generated import fleet_control_pb2 as pb2
from robo_claw_maestro.adapters.grpc.generated import fleet_control_pb2_grpc as pb2_grpc


async def run(args) -> None:
    if args.ca:
        credentials = grpc.ssl_channel_credentials(
            root_certificates=Path(args.ca).read_bytes(),
            private_key=Path(args.key).read_bytes() if args.key else None,
            certificate_chain=Path(args.certificate).read_bytes() if args.certificate else None,
        )
        channel = grpc.aio.secure_channel(args.target, credentials)
    else:
        channel = grpc.aio.insecure_channel(args.target)
    stub = pb2_grpc.FleetControlStub(channel)
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    session_id = uuid4().hex
    completed: dict[str, tuple[bool, str, str]] = {}

    async def requests():
        yield pb2.RobotToMaestro(
            protocol_version="1",
            robot_id=args.robot_id,
            session_id=session_id,
            register=pb2.Register(
                session_id=session_id,
                descriptor=pb2.RobotDescriptor(
                    robot_id=args.robot_id,
                    display_name=args.robot_id,
                    robot_type="fake",
                    software_version="0.1.0",
                    protocol_version="1",
                    site_id=args.site,
                    map_id=args.map,
                    capabilities=[
                        pb2.Capability(name="inspect", version="1.0", risk_level="read"),
                        pb2.Capability(name="navigate_to", version="1.0", risk_level="motion"),
                        pb2.Capability(name="power_tool", version="1.0", risk_level="dangerous"),
                    ],
                ),
            ),
        )
        while True:
            yield await queue.get()

    async def telemetry_loop():
        while True:
            await queue.put(
                pb2.RobotToMaestro(
                    protocol_version="1",
                    robot_id=args.robot_id,
                    session_id=session_id,
                    telemetry=pb2.Telemetry(
                        battery_percentage=args.battery,
                        frame_id="map",
                        map_id=args.map,
                        operational_state="IDLE",
                        observed_at_ms=int(time.time() * 1000),
                    ),
                )
            )
            await asyncio.sleep(1)

    telemetry = asyncio.create_task(telemetry_loop())
    call = stub.Connect(requests())
    try:
        async for message in call:
            payload = message.WhichOneof("payload")
            if payload == "registration_result":
                print(
                    f"registration accepted={message.registration_result.accepted} "
                    f"message={message.registration_result.message}",
                    flush=True,
                )
                if not message.registration_result.accepted:
                    return
                continue
            if payload not in {
                "execute_task",
                "execute_skill",
                "emergency_stop",
                "request_snapshot",
            }:
                continue
            if payload == "execute_task":
                task_id = message.execute_task.task_id
                key = message.execute_task.idempotency_key
            elif payload == "execute_skill":
                task_id = message.execute_skill.task_id
                key = message.execute_skill.idempotency_key
            elif payload == "emergency_stop":
                task_id = "emergency-stop"
                key = message.command_id
            else:
                task_id = "snapshot"
                key = message.command_id
            await queue.put(
                pb2.RobotToMaestro(
                    protocol_version="1",
                    robot_id=args.robot_id,
                    session_id=session_id,
                    command_ack=pb2.CommandAck(command_id=message.command_id, accepted=True),
                )
            )
            result = completed.get(key)
            if result is None:
                result_data = {"payload": payload}
                if payload == "request_snapshot":
                    result_data.update(
                        {
                            "image_base64": "R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==",
                            "image_format": "image/gif",
                            "timestamp_ms": int(time.time() * 1000),
                        }
                    )
                result = (True, "fake command completed", json.dumps(result_data))
                completed[key] = result
                print(f"executed {payload} idempotency_key={key}", flush=True)
            await queue.put(
                pb2.RobotToMaestro(
                    protocol_version="1",
                    robot_id=args.robot_id,
                    session_id=session_id,
                    task_update=pb2.TaskUpdate(
                        command_id=message.command_id,
                        task_id=task_id,
                        status="SUCCEEDED" if result[0] else "FAILED",
                        progress=100,
                        message=result[1],
                        result_json=result[2],
                    ),
                )
            )
    finally:
        telemetry.cancel()
        await channel.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="127.0.0.1:50053")
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--site", default="test-site")
    parser.add_argument("--map", default="test-map")
    parser.add_argument("--battery", type=float, default=80)
    parser.add_argument("--ca", default="")
    parser.add_argument("--certificate", default="")
    parser.add_argument("--key", default="")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
