import asyncio

import pytest

from robo_claw_maestro.adapters.grpc.multi_robot import MultiRobotGateway
from robo_claw_maestro.application.robot_monitor import RobotMonitor
from robo_claw_maestro.application.robot_registry import RobotRegistry
from robo_claw_maestro.domain.robot import Robot, RobotConnectionState
from robo_claw_maestro.testing_grpc import start_fake_robot

pytestmark = pytest.mark.integration


async def wait_until(predicate, timeout_sec=5.0):
    deadline = asyncio.get_running_loop().time() + timeout_sec
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("condition was not reached")
        await asyncio.sleep(0.05)


def test_status_stream_disconnect_and_restart_creates_new_session():
    async def scenario():
        first_server = await start_fake_robot("robot-1", 0)
        port = int(first_server.endpoint.rsplit(":", 1)[1])
        gateway = MultiRobotGateway.from_endpoints({"robot-1": first_server.endpoint})
        registry = RobotRegistry(gateway)
        registry.register(Robot("robot-1", "Robot One"))
        stop = asyncio.Event()
        monitor = RobotMonitor(
            registry,
            gateway,
            initial_backoff_sec=0.05,
            max_backoff_sec=0.2,
        )
        monitor_task = asyncio.create_task(monitor.run("robot-1", stop))
        second_server = None
        try:
            await wait_until(
                lambda: registry.get("robot-1").connection_state is RobotConnectionState.ONLINE
            )
            first_session = registry.get("robot-1").session_id

            await first_server.stop()
            await wait_until(
                lambda: registry.get("robot-1").connection_state is RobotConnectionState.OFFLINE
            )

            second_server = await start_fake_robot("robot-1", port)
            await wait_until(
                lambda: registry.get("robot-1").connection_state is RobotConnectionState.ONLINE
            )
            second_session = registry.get("robot-1").session_id

            assert first_session
            assert second_session
            assert first_session != second_session
        finally:
            stop.set()
            monitor_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await monitor_task
            await monitor.close()
            if second_server is not None:
                await second_server.stop()

    asyncio.run(scenario())
