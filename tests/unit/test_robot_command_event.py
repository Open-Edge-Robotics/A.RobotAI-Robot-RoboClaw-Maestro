from datetime import UTC, datetime

import pytest

from robo_claw_maestro.domain.command import Command
from robo_claw_maestro.domain.event import DomainEvent
from robo_claw_maestro.domain.robot import (
    Capability,
    Robot,
    RobotConnectionState,
)

pytestmark = pytest.mark.unit


def test_robot_capability_and_eligibility():
    robot = Robot("robot-a", "Alpha", connection_state=RobotConnectionState.ONLINE)
    robot.add_capability(Capability("navigate_to", requirements=("nav2",)))
    assert robot.has_capabilities({"navigate_to"})
    assert robot.is_eligible({"navigate_to"})


def test_degraded_robot_with_health_issue_is_not_eligible():
    robot = Robot(
        "robot-a",
        "Alpha",
        connection_state=RobotConnectionState.DEGRADED,
        health_issues=["stale_pose"],
    )
    assert not robot.is_eligible(set())


def test_robot_battery_and_queue_are_validated():
    with pytest.raises(ValueError):
        Robot("robot-a", "Alpha", battery_percentage=101)
    with pytest.raises(ValueError):
        Robot("robot-a", "Alpha", queue_depth=-1)


def test_command_has_stable_idempotency_key_per_attempt():
    command = Command.create(
        mission_id="m1",
        task_id="t1",
        robot_id="r1",
        instruction="move",
        attempt=2,
    )
    assert command.idempotency_key == "m1:t1:r1:2"
    assert command.attempt_id == "t1-attempt-2"
    assert command.command_id


def test_event_has_utc_timestamp_and_structured_payload():
    event = DomainEvent.create(
        event_type="task.created",
        aggregate_type="task",
        aggregate_id="t1",
        payload={"mission_id": "m1"},
    )
    assert event.payload == {"mission_id": "m1"}
    assert event.occurred_at.tzinfo == UTC
    assert isinstance(event.occurred_at, datetime)
