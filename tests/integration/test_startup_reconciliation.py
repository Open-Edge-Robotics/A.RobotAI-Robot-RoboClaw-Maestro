import asyncio

import pytest

from robo_claw_maestro.adapters.database.sqlmodel import (
    SqlModelEventRepository,
    SqlModelMissionRepository,
    SqlModelStore,
)
from robo_claw_maestro.application.mission_orchestrator import MissionOrchestrator
from robo_claw_maestro.application.reconciliation import ReconciliationService
from robo_claw_maestro.application.robot_registry import RobotRegistry
from robo_claw_maestro.application.scheduler import FleetScheduler
from robo_claw_maestro.application.task_dispatcher import TaskDispatcher
from robo_claw_maestro.domain.mission import Mission, MissionStatus
from robo_claw_maestro.domain.robot import Robot, RobotConnectionState
from robo_claw_maestro.domain.task import Task, TaskStatus
from robo_claw_maestro.ports.robot_gateway import FakeRobotGateway, RobotStatusSnapshot

pytestmark = pytest.mark.integration


def test_restart_reconciles_persisted_unknown_task(tmp_path):
    path = tmp_path / "maestro.db"
    first_store = SqlModelStore(f"sqlite:///{path}", create_tables=True)
    first_repository = SqlModelMissionRepository(first_store)
    mission = Mission("m1", "inspect")
    task = Task("t1", "m1", "inspect")
    mission.add_task(task)
    mission.transition_to(MissionStatus.READY)
    mission.transition_to(MissionStatus.RUNNING)
    task.transition_to(TaskStatus.READY)
    task.assign("r1")
    task.transition_to(TaskStatus.UNKNOWN)
    mission.pull_events()
    task.pull_events()
    first_repository.add(mission)

    reopened_store = SqlModelStore(f"sqlite:///{path}", create_tables=True)
    missions = SqlModelMissionRepository(reopened_store)
    events = SqlModelEventRepository(reopened_store)
    gateway = FakeRobotGateway([RobotStatusSnapshot(robot_id="r1", current_task_id="t1")])
    registry = RobotRegistry(gateway)
    registry.register(Robot("r1", "Robot One", connection_state=RobotConnectionState.ONLINE))
    orchestrator = MissionOrchestrator(
        FleetScheduler(registry),
        TaskDispatcher(registry, gateway),
        mission_repository=missions,
        event_repository=events,
    )

    results = asyncio.run(orchestrator.reconcile_all(ReconciliationService(registry)))

    assert results["m1"].running == ("t1",)
    restored = missions.get("m1")
    assert restored.tasks["t1"].status is TaskStatus.RUNNING
    assert events.list("t1")[-1].payload["to"] == "RUNNING"
