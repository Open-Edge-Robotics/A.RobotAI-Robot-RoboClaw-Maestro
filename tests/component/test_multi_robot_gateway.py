import asyncio

import pytest

from robo_claw_maestro.adapters.grpc.multi_robot import MultiRobotGateway
from robo_claw_maestro.ports.robot_gateway import FakeRobotGateway, RobotStatusSnapshot

pytestmark = pytest.mark.component


def test_routes_each_robot_to_its_own_gateway():
    first = FakeRobotGateway([RobotStatusSnapshot(robot_id="robot-a")])
    second = FakeRobotGateway([RobotStatusSnapshot(robot_id="robot-b")])
    gateway = MultiRobotGateway({"robot-a": first, "robot-b": second})

    asyncio.run(
        gateway.execute_task("robot-b", instruction="inspect", task_id="task-1", timeout_sec=10)
    )

    assert first.executed_tasks == []
    assert second.executed_tasks[0]["robot_id"] == "robot-b"


def test_unknown_robot_is_not_reachable():
    gateway = MultiRobotGateway()

    assert asyncio.run(gateway.ping("missing")) is False
