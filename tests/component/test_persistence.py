import asyncio

import pytest

from robo_claw_maestro.adapters.database.in_memory import (
    InMemoryCommandRepository,
    InMemoryEventRepository,
    InMemoryMissionRepository,
)
from robo_claw_maestro.application.mission_orchestrator import MissionOrchestrator
from robo_claw_maestro.application.robot_registry import RobotRegistry
from robo_claw_maestro.application.scheduler import FleetScheduler
from robo_claw_maestro.application.task_dispatcher import TaskDispatcher
from robo_claw_maestro.domain.command import Command
from robo_claw_maestro.domain.mission import Mission
from robo_claw_maestro.domain.robot import Robot, RobotConnectionState
from robo_claw_maestro.domain.task import Task
from robo_claw_maestro.ports.robot_gateway import FakeRobotGateway, RobotStatusSnapshot

pytestmark = pytest.mark.component


def test_command_repository_rejects_duplicate_idempotency_key():
    repository = InMemoryCommandRepository()
    first = Command.create(
        mission_id="m1", task_id="t1", robot_id="r1", instruction="inspect", attempt=1
    )
    duplicate = Command.create(
        mission_id="m1", task_id="t1", robot_id="r1", instruction="inspect", attempt=1
    )

    repository.add(first)
    with pytest.raises(ValueError, match="idempotency"):
        repository.add(duplicate)

    assert repository.get_by_idempotency_key(first.idempotency_key) == first


def test_mission_repository_and_event_repository_are_reusable():
    missions = InMemoryMissionRepository()
    events = InMemoryEventRepository()
    gateway = FakeRobotGateway([RobotStatusSnapshot(robot_id="robot-a")])
    registry = RobotRegistry(gateway)
    registry.register(Robot("robot-a", "Alpha", connection_state=RobotConnectionState.ONLINE))
    orchestrator = MissionOrchestrator(
        FleetScheduler(registry),
        TaskDispatcher(registry, gateway),
        mission_repository=missions,
        event_repository=events,
    )
    mission = Mission("m1", "inspect")
    mission.add_task(Task("t1", "m1", "inspect"))

    orchestrator.create(mission)
    asyncio.run(orchestrator.run_round("m1"))

    assert missions.get("m1") is mission
    assert [event.event_type for event in events.list("m1")] == [
        "mission.created",
        "mission.status_changed",
        "mission.status_changed",
        "mission.status_changed",
    ]
    assert [event.event_type for event in events.list("t1")] == [
        "task.status_changed",
        "task.status_changed",
        "task.status_changed",
        "task.status_changed",
        "task.status_changed",
        "task.dispatched",
    ]
