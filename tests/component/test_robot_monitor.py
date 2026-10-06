import asyncio

import pytest

from robo_claw_maestro.application.robot_monitor import RobotMonitor
from robo_claw_maestro.application.robot_registry import RobotRegistry
from robo_claw_maestro.domain.robot import Robot, RobotConnectionState
from robo_claw_maestro.ports.robot_gateway import FakeRobotGateway, RobotUnavailable

pytestmark = pytest.mark.component


class FailingStreamGateway(FakeRobotGateway):
    def __init__(self):
        super().__init__()
        self.stream_attempts = 0

    async def stream_status(self, robot_id):
        self.stream_attempts += 1
        if False:
            yield None
        raise RobotUnavailable(robot_id)


def test_monitor_uses_bounded_exponential_backoff():
    gateway = FailingStreamGateway()
    registry = RobotRegistry(gateway)
    registry.register(Robot("r1", "Robot One"))
    delays = []
    stop = asyncio.Event()

    async def fake_sleep(delay):
        delays.append(delay)
        if len(delays) == 4:
            stop.set()

    monitor = RobotMonitor(
        registry,
        gateway,
        initial_backoff_sec=1,
        max_backoff_sec=4,
        sleep=fake_sleep,
    )
    asyncio.run(monitor.run("r1", stop))

    assert delays == [1, 2, 4, 4]
    assert registry.get("r1").connection_state is RobotConnectionState.OFFLINE
