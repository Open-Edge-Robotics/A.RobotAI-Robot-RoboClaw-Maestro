import asyncio

import pytest

from robo_claw_maestro.application.reconciliation import ReconciliationService
from robo_claw_maestro.application.robot_registry import RobotRegistry
from robo_claw_maestro.domain.mission import Mission, MissionStatus
from robo_claw_maestro.domain.robot import Robot, RobotConnectionState
from robo_claw_maestro.domain.task import Task, TaskStatus
from robo_claw_maestro.ports.robot_gateway import FakeRobotGateway, RobotStatusSnapshot

pytestmark = pytest.mark.component


def ambiguous_mission():
    mission = Mission("m1", "inspect")
    task = Task("t1", "m1", "inspect")
    mission.add_task(task)
    mission.transition_to(MissionStatus.READY)
    mission.transition_to(MissionStatus.RUNNING)
    task.transition_to(TaskStatus.READY)
    task.assign("r1")
    task.transition_to(TaskStatus.UNKNOWN)
    return mission, task


def test_reconciliation_restores_remotely_running_task():
    gateway = FakeRobotGateway([RobotStatusSnapshot(robot_id="r1", current_task_id="t1")])
    registry = RobotRegistry(gateway)
    registry.register(Robot("r1", "Robot One", connection_state=RobotConnectionState.ONLINE))
    mission, task = ambiguous_mission()

    result = asyncio.run(ReconciliationService(registry).reconcile(mission))

    assert result.running == ("t1",)
    assert task.status is TaskStatus.RUNNING


def test_reconciliation_keeps_task_unknown_while_robot_is_offline():
    gateway = FakeRobotGateway([RobotStatusSnapshot(robot_id="r1")])
    gateway.disconnect("r1")
    registry = RobotRegistry(gateway)
    registry.register(Robot("r1", "Robot One", connection_state=RobotConnectionState.ONLINE))
    mission, task = ambiguous_mission()

    result = asyncio.run(ReconciliationService(registry).reconcile(mission))

    assert result.unresolved == ("t1",)
    assert task.status is TaskStatus.UNKNOWN
