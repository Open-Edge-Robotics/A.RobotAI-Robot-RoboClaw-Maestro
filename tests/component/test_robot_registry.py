import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from robo_claw_maestro.application.robot_registry import RobotRegistry
from robo_claw_maestro.domain.robot import (
    Robot,
    RobotConnectionState,
    RobotOperationalState,
)
from robo_claw_maestro.ports.robot_gateway import FakeRobotGateway, RobotStatusSnapshot

pytestmark = pytest.mark.component


def make_robot(robot_id: str) -> Robot:
    return Robot(robot_id, robot_id.title())


def make_snapshot(robot_id: str) -> RobotStatusSnapshot:
    return RobotStatusSnapshot(
        robot_id=robot_id,
        operational_state=RobotOperationalState.EXECUTING,
        battery_percentage=82,
        pose={"site_id": "site-a", "map_id": "floor-1", "frame_id": "map", "x": 1.0, "y": 2.0},
        current_task_id="task-1",
        current_skill="navigate_to",
        queue_depth=1,
    )


def test_registry_synchronizes_status():
    gateway = FakeRobotGateway([make_snapshot("robot-a")])
    registry = RobotRegistry(gateway)
    registry.register(make_robot("robot-a"))

    robot = asyncio.run(registry.synchronize("robot-a"))

    assert robot.connection_state is RobotConnectionState.ONLINE
    assert robot.operational_state is RobotOperationalState.EXECUTING
    assert robot.battery_percentage == 82
    assert robot.current_task_id == "task-1"
    assert robot.last_seen_at is not None


def test_registry_synchronizes_multiple_robots():
    gateway = FakeRobotGateway([make_snapshot("robot-a"), make_snapshot("robot-b")])
    registry = RobotRegistry(gateway)
    registry.register(make_robot("robot-a"))
    registry.register(make_robot("robot-b"))

    robots = asyncio.run(registry.synchronize_all())

    assert [robot.robot_id for robot in robots] == ["robot-a", "robot-b"]
    assert all(robot.connection_state is RobotConnectionState.ONLINE for robot in robots)


def test_reconnect_creates_a_new_session():
    gateway = FakeRobotGateway([make_snapshot("robot-a")])
    registry = RobotRegistry(gateway)
    registry.register(make_robot("robot-a"))

    first = asyncio.run(registry.synchronize("robot-a")).session_id
    gateway.disconnect("robot-a")
    asyncio.run(registry.synchronize("robot-a"))
    gateway.reconnect("robot-a")
    second = asyncio.run(registry.synchronize("robot-a")).session_id

    assert first
    assert second
    assert first != second


def test_unavailable_robot_becomes_offline():
    gateway = FakeRobotGateway([make_snapshot("robot-a")])
    gateway.disconnect("robot-a")
    registry = RobotRegistry(gateway)
    registry.register(make_robot("robot-a"))

    robot = asyncio.run(registry.synchronize("robot-a"))

    assert robot.connection_state is RobotConnectionState.OFFLINE


def test_stale_robot_is_marked_offline():
    gateway = FakeRobotGateway([make_snapshot("robot-a")])
    registry = RobotRegistry(gateway, heartbeat_timeout_sec=5)
    robot = make_robot("robot-a")
    robot.connection_state = RobotConnectionState.ONLINE
    robot.last_seen_at = datetime.now(UTC) - timedelta(seconds=10)
    registry.register(robot)

    assert registry.mark_stale() == ["robot-a"]
    assert robot.connection_state is RobotConnectionState.OFFLINE


def test_telemetry_events_can_be_retained_by_age():
    gateway = FakeRobotGateway([make_snapshot("robot-a")])
    registry = RobotRegistry(gateway)
    registry.register(make_robot("robot-a"))
    asyncio.run(registry.synchronize("robot-a"))

    deleted = registry.cleanup_events(retention_days=1, now=datetime.now(UTC) + timedelta(days=2))

    assert deleted >= 2
    assert registry.events.list() == []


def test_emergency_stop_reports_partial_failure():
    gateway = FakeRobotGateway([make_snapshot("robot-a"), make_snapshot("robot-b")])
    gateway.disconnect("robot-b")
    registry = RobotRegistry(gateway)
    registry.register(make_robot("robot-a"))
    registry.register(make_robot("robot-b"))

    result = asyncio.run(registry.emergency_stop_all())

    assert result.requested == 2
    assert result.stopped == ("robot-a",)
    assert "robot-b" in result.failed
    assert registry.get("robot-a").connection_state is RobotConnectionState.ESTOPPED
