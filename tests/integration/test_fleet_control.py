import asyncio

import grpc
import pytest

from robo_claw_maestro.adapters.grpc.fleet_control_server import (
    FleetControlHub,
    FleetControlServer,
)
from robo_claw_maestro.adapters.grpc.generated import fleet_control_pb2 as pb2
from robo_claw_maestro.adapters.grpc.generated import fleet_control_pb2_grpc as pb2_grpc
from robo_claw_maestro.application.robot_registry import RobotRegistry
from robo_claw_maestro.ports.robot_gateway import FakeRobotGateway

pytestmark = pytest.mark.integration


async def wait_until(predicate, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("condition was not reached")
        await asyncio.sleep(0.01)


def test_fleet_control_registration_telemetry_ack_result_and_idempotency():
    async def scenario():
        registry = RobotRegistry(FakeRobotGateway())
        hub = FleetControlHub(registry, command_timeout_sec=2)
        server = FleetControlServer(hub, host="127.0.0.1", port=0)
        await server.start()
        channel = grpc.aio.insecure_channel(server.address)
        stub = pb2_grpc.FleetControlStub(channel)
        outbound = asyncio.Queue()

        async def robot_messages():
            while True:
                message = await outbound.get()
                if message is None:
                    return
                yield message

        await outbound.put(
            pb2.RobotToMaestro(
                protocol_version="1",
                robot_id="robot-1",
                session_id="boot-1",
                register=pb2.Register(
                    session_id="boot-1",
                    descriptor=pb2.RobotDescriptor(
                        robot_id="robot-1",
                        display_name="Robot One",
                        map_id="floor-1",
                        map_version="v2",
                        frame_id="map",
                        capabilities=[pb2.Capability(name="inspect", version="1.0")],
                    ),
                ),
            )
        )
        call = stub.Connect(robot_messages())
        registration = await call.read()
        assert registration.registration_result.accepted
        await outbound.put(
            pb2.RobotToMaestro(
                protocol_version="1",
                robot_id="robot-1",
                session_id="boot-1",
                telemetry=pb2.Telemetry(
                    battery_percentage=73,
                    operational_state="IDLE",
                    map_id="floor-1",
                    frame_id="map",
                ),
            )
        )
        await wait_until(lambda: registry.get("robot-1").battery_percentage == 73)

        physical_executions = set()

        async def robot_worker():
            for _ in range(2):
                command = await call.read()
                key = command.execute_task.idempotency_key
                physical_executions.add(key)
                await outbound.put(
                    pb2.RobotToMaestro(
                        protocol_version="1",
                        robot_id="robot-1",
                        session_id="boot-1",
                        command_ack=pb2.CommandAck(command_id=command.command_id, accepted=True),
                    )
                )
                await outbound.put(
                    pb2.RobotToMaestro(
                        protocol_version="1",
                        robot_id="robot-1",
                        session_id="boot-1",
                        task_update=pb2.TaskUpdate(
                            command_id=command.command_id,
                            task_id=command.execute_task.task_id,
                            status="SUCCEEDED",
                            progress=100,
                            message="done",
                            result_json='{"ok":true}',
                        ),
                    )
                )

        worker = asyncio.create_task(robot_worker())
        first = await hub.execute_task(
            "robot-1", instruction="inspect", task_id="task-1", timeout_sec=1
        )
        second = await hub.execute_task(
            "robot-1", instruction="inspect", task_id="task-1", timeout_sec=1
        )
        await worker

        assert first.success and second.success
        assert physical_executions == {"task-1"}
        assert registry.get("robot-1").session_id == "boot-1"
        assert registry.get("robot-1").map_version == "v2"
        assert registry.get("robot-1").frame_id == "map"
        assert "inspect" in registry.get("robot-1").capabilities

        await outbound.put(None)
        call.cancel()
        await channel.close()
        await server.stop()

    asyncio.run(scenario())


def test_fleet_control_rejects_unsupported_protocol():
    async def scenario():
        hub = FleetControlHub()
        server = FleetControlServer(hub, host="127.0.0.1", port=0)
        await server.start()
        channel = grpc.aio.insecure_channel(server.address)
        stub = pb2_grpc.FleetControlStub(channel)

        async def messages():
            yield pb2.RobotToMaestro(
                protocol_version="999",
                robot_id="old-robot",
                session_id="boot-1",
                register=pb2.Register(descriptor=pb2.RobotDescriptor(robot_id="old-robot")),
            )

        call = stub.Connect(messages())
        response = await call.read()
        assert not response.registration_result.accepted
        await channel.close()
        await server.stop()

    asyncio.run(scenario())
