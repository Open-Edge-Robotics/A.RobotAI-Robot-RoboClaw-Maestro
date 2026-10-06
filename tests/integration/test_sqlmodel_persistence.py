from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from robo_claw_maestro.adapters.database.sqlmodel import (
    SqlModelApprovalRepository,
    SqlModelCommandRepository,
    SqlModelEventRepository,
    SqlModelMissionRepository,
    SqlModelRobotRepository,
    SqlModelStore,
    SqlResourceLockManager,
)
from robo_claw_maestro.domain.approval import ApprovalRequest, ApprovalStatus
from robo_claw_maestro.domain.command import Command
from robo_claw_maestro.domain.event import DomainEvent
from robo_claw_maestro.domain.mission import Mission
from robo_claw_maestro.domain.robot import Capability, Robot, RobotConnectionState
from robo_claw_maestro.domain.task import Task, TaskStatus

pytestmark = pytest.mark.integration


def make_store(path: Path) -> SqlModelStore:
    return SqlModelStore(f"sqlite:///{path}", create_tables=True)


def test_aggregates_and_events_survive_store_restart(tmp_path):
    path = tmp_path / "maestro.db"
    store = make_store(path)
    missions = SqlModelMissionRepository(store)
    robots = SqlModelRobotRepository(store)
    events = SqlModelEventRepository(store)

    robot = Robot("r1", "Robot One", connection_state=RobotConnectionState.ONLINE)
    robot.add_capability(Capability("inspect", requirements=("camera",)))
    robots.add(robot)
    mission = Mission("m1", "inspect")
    task = Task("t1", "m1", "inspect", required_capabilities={"inspect"})
    mission.add_task(task)
    task.transition_to(TaskStatus.READY)
    missions.add(mission)
    event = DomainEvent.create(
        event_type="mission.created",
        aggregate_type="mission",
        aggregate_id="m1",
    )
    events.append(event)

    reopened = make_store(path)

    loaded_robot = SqlModelRobotRepository(reopened).get("r1")
    loaded_mission = SqlModelMissionRepository(reopened).get("m1")
    assert reopened.healthcheck()
    assert loaded_robot is not None
    assert loaded_robot.capabilities["inspect"].requirements == ("camera",)
    assert loaded_mission is not None
    assert loaded_mission.tasks["t1"].status is TaskStatus.READY
    assert SqlModelEventRepository(reopened).list("m1") == [event]


def test_command_idempotency_survives_restart(tmp_path):
    path = tmp_path / "maestro.db"
    repository = SqlModelCommandRepository(make_store(path))
    command = Command.create(
        mission_id="m1", task_id="t1", robot_id="r1", instruction="inspect", attempt=1
    )
    repository.add(command)

    reopened = SqlModelCommandRepository(make_store(path))
    duplicate = Command.create(
        mission_id="m1", task_id="t1", robot_id="r1", instruction="inspect", attempt=1
    )

    assert reopened.get_by_idempotency_key(command.idempotency_key) == command
    with pytest.raises(IntegrityError):
        reopened.add(duplicate)


def test_pending_approval_survives_restart(tmp_path):
    path = tmp_path / "maestro.db"
    repository = SqlModelApprovalRepository(make_store(path))
    approval = ApprovalRequest.create(
        robot_id="r1",
        action="skill:power_tool",
        payload={"params": {}},
        requested_by="operator",
        reason="dangerous skill",
    )
    repository.add(approval)

    reopened = SqlModelApprovalRepository(make_store(path))
    restored = reopened.get(approval.approval_id)

    assert restored is not None
    assert restored.status is ApprovalStatus.PENDING
    assert restored.action == "skill:power_tool"


def test_resource_lease_survives_restart_and_can_be_released(tmp_path):
    path = tmp_path / "maestro.db"
    first = SqlResourceLockManager(make_store(path))
    assert first.acquire_many({"corridor-a", "door-a"}, "task-1", 60)

    reopened = SqlResourceLockManager(make_store(path))
    assert not reopened.acquire_many({"corridor-a"}, "task-2", 60)
    assert {lease.resource_id for lease in reopened.list()} == {
        "corridor-a",
        "door-a",
    }

    reopened.release_owner("task-1")
    assert reopened.acquire_many({"corridor-a"}, "task-2", 60)


def test_duplicate_aggregate_rolls_back(tmp_path):
    repository = SqlModelRobotRepository(make_store(tmp_path / "maestro.db"))
    repository.add(Robot("r1", "Robot One"))

    with pytest.raises(IntegrityError):
        repository.add(Robot("r1", "Duplicate"))

    assert repository.get("r1").display_name == "Robot One"
