import asyncio

import pytest

from robo_claw_maestro.adapters.grpc.multi_robot import MultiRobotGateway
from robo_claw_maestro.application.robot_registry import RobotRegistry
from robo_claw_maestro.domain.robot import Robot
from robo_claw_maestro.testing_grpc import start_fake_robot

pytestmark = pytest.mark.integration


def test_three_real_grpc_connections_support_monitoring_commands_and_stop():
    async def scenario():
        servers = [
            await start_fake_robot(f"robot-{index}", 0, battery_percentage=90 - index)
            for index in range(1, 4)
        ]
        gateway = MultiRobotGateway.from_endpoints(
            {robot.robot_id: robot.endpoint for robot in servers}
        )
        registry = RobotRegistry(gateway)
        for robot in servers:
            registry.register(Robot(robot.robot_id, robot.robot_id))
        try:
            synchronized = await registry.synchronize_all()
            assert len(synchronized) == 3
            assert all(robot.connection_state.value == "ONLINE" for robot in synchronized)

            info = await gateway.get_info("robot-1")
            task = await gateway.execute_task(
                "robot-1", instruction="inspect", task_id="task-1", timeout_sec=2
            )
            skill = await gateway.execute_skill(
                "robot-2", skill_name="get_status", params={}, timeout_sec=2
            )
            stopped = await registry.emergency_stop_all()

            assert info.robot_id == "robot-1"
            assert task.success
            assert skill.success
            assert len(stopped.stopped) == 3
        finally:
            await gateway.close()
            await asyncio.gather(*(robot.stop() for robot in servers))

    asyncio.run(scenario())
