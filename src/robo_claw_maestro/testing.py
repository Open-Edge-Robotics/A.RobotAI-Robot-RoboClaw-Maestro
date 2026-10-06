"""Test application factory for a deterministic in-memory robot fleet."""

from __future__ import annotations

from robo_claw_maestro.api.app import create_app
from robo_claw_maestro.application.mission_orchestrator import MissionOrchestrator
from robo_claw_maestro.application.robot_registry import RobotRegistry
from robo_claw_maestro.application.scheduler import FleetScheduler
from robo_claw_maestro.application.task_dispatcher import TaskDispatcher
from robo_claw_maestro.domain.robot import Capability, Robot, RobotConnectionState
from robo_claw_maestro.ports.robot_gateway import FakeRobotGateway, RobotStatusSnapshot


def build_fake_app(robot_count: int = 3):
    if robot_count < 2:
        raise ValueError("acceptance fleet must contain at least two robots")
    snapshots = [
        RobotStatusSnapshot(
            robot_id=f"robot-{index}",
            battery_percentage=90 - index * 5,
            pose={
                "map_id": "test-map",
                "frame_id": "map",
                "x": float(index),
                "y": 0.0,
            },
        )
        for index in range(1, robot_count + 1)
    ]
    gateway = FakeRobotGateway(snapshots)
    registry = RobotRegistry(gateway)
    for snapshot in snapshots:
        robot = Robot(
            snapshot.robot_id,
            snapshot.robot_id.title(),
            map_id="test-map",
            connection_state=RobotConnectionState.ONLINE,
        )
        robot.add_capability(Capability("inspect"))
        robot.add_capability(Capability("navigate_to", risk_level="motion"))
        registry.register(robot)
    orchestrator = MissionOrchestrator(FleetScheduler(registry), TaskDispatcher(registry, gateway))
    return create_app(registry, orchestrator, readiness_check=lambda: True)
