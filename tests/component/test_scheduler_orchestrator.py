import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from robo_claw_maestro.application.mission_orchestrator import MissionOrchestrator
from robo_claw_maestro.application.robot_registry import RobotRegistry
from robo_claw_maestro.application.scheduler import FleetScheduler
from robo_claw_maestro.application.task_dispatcher import TaskDispatcher
from robo_claw_maestro.domain.mission import Mission, MissionStatus
from robo_claw_maestro.domain.robot import Capability, Robot, RobotConnectionState
from robo_claw_maestro.domain.task import Task, TaskStatus
from robo_claw_maestro.ports.robot_gateway import FakeRobotGateway, RobotStatusSnapshot

pytestmark = pytest.mark.component


def make_orchestrator():
    gateway = FakeRobotGateway(
        [
            RobotStatusSnapshot(robot_id="robot-a"),
            RobotStatusSnapshot(robot_id="robot-b"),
        ]
    )
    registry = RobotRegistry(gateway)
    for robot_id, queue_depth in (("robot-a", 2), ("robot-b", 0)):
        robot = Robot(
            robot_id,
            robot_id,
            connection_state=RobotConnectionState.ONLINE,
            queue_depth=queue_depth,
        )
        robot.add_capability(Capability("inspect"))
        registry.register(robot)
    scheduler = FleetScheduler(registry)
    return MissionOrchestrator(scheduler, TaskDispatcher(registry, gateway)), gateway


def test_scheduler_prefers_least_loaded_robot():
    orchestrator, _ = make_orchestrator()
    mission = Mission("m1", "inspect")
    task = Task("t1", "m1", "inspect room", required_capabilities={"inspect"})
    mission.add_task(task)
    orchestrator.create(mission)

    round_result = asyncio.run(orchestrator.run_round("m1"))

    assert round_result.assignments[0].robot_id == "robot-b"
    assert task.status is TaskStatus.SUCCEEDED


def test_orchestrator_runs_dependency_rounds():
    orchestrator, gateway = make_orchestrator()
    mission = Mission("m1", "inspect two steps")
    first = Task("first", "m1", "open door", required_capabilities={"inspect"})
    second = Task(
        "second", "m1", "inspect room", depends_on={"first"}, required_capabilities={"inspect"}
    )
    mission.add_tasks([first, second])
    orchestrator.create(mission)

    completed = asyncio.run(orchestrator.run_until_settled("m1"))

    assert completed.status is MissionStatus.SUCCEEDED
    assert first.status is TaskStatus.SUCCEEDED
    assert second.status is TaskStatus.SUCCEEDED
    assert [item["task_id"] for item in gateway.executed_tasks] == ["first", "second"]


def test_failed_task_retries_on_a_different_robot_within_budget():
    orchestrator, gateway = make_orchestrator()
    gateway.fail_commands_for.add("robot-b")
    mission = Mission("m1", "inspect")
    task = Task("t1", "m1", "inspect", required_capabilities={"inspect"}, max_attempts=2)
    mission.add_task(task)
    orchestrator.create(mission)

    first = asyncio.run(orchestrator.run_round("m1"))
    gateway.fail_commands_for.clear()
    second = asyncio.run(orchestrator.run_round("m1"))

    assert first.assignments[0].robot_id == "robot-b"
    assert second.assignments[0].robot_id == "robot-a"
    assert task.status is TaskStatus.SUCCEEDED
    assert task.attempt_count == 2


def test_mission_lifecycle_pause_resume_and_cancel():
    orchestrator, _ = make_orchestrator()
    mission = Mission("m1", "inspect")
    mission.add_task(Task("t1", "m1", "inspect"))
    orchestrator.create(mission)

    assert orchestrator.start("m1").status is MissionStatus.READY
    assert orchestrator.pause("m1").status is MissionStatus.PAUSED
    assert orchestrator.resume("m1").status is MissionStatus.RUNNING
    assert orchestrator.cancel("m1").status is MissionStatus.CANCELLED
    assert mission.tasks["t1"].status is TaskStatus.CANCELLED


def test_elapsed_deadline_prevents_dispatch():
    orchestrator, gateway = make_orchestrator()
    mission = Mission("m1", "inspect")
    task = Task(
        "t1",
        "m1",
        "inspect",
        required_capabilities={"inspect"},
        deadline=datetime.now(UTC) - timedelta(seconds=1),
    )
    mission.add_task(task)
    orchestrator.create(mission)

    result = asyncio.run(orchestrator.run_round("m1"))

    assert result.assignments == ()
    assert task.status is TaskStatus.TIMED_OUT
    assert task.error_code == "DEADLINE_EXCEEDED"
    assert mission.status is MissionStatus.FAILED
    assert gateway.executed_tasks == []


def test_independent_tasks_are_dispatched_in_parallel_round():
    orchestrator, gateway = make_orchestrator()
    mission = Mission("m1", "inspect two rooms")
    mission.add_tasks(
        [
            Task("a", "m1", "inspect A", required_capabilities={"inspect"}),
            Task("b", "m1", "inspect B", required_capabilities={"inspect"}),
        ]
    )
    orchestrator.create(mission)

    result = asyncio.run(orchestrator.run_round("m1"))

    assert len(result.assignments) == 2
    assert {item["task_id"] for item in gateway.executed_tasks} == {"a", "b"}
