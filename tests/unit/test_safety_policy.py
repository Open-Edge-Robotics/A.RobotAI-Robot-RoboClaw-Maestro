from datetime import UTC, datetime, timedelta

import pytest

from robo_claw_maestro.domain.policies import SchedulingSafetyPolicy
from robo_claw_maestro.domain.robot import Capability, Robot, RobotConnectionState
from robo_claw_maestro.domain.task import Task

pytestmark = pytest.mark.unit


def robot(**kwargs):
    item = Robot(
        "r1",
        "Robot One",
        connection_state=RobotConnectionState.ONLINE,
        battery_percentage=80,
        **kwargs,
    )
    item.add_capability(Capability("navigate_to", risk_level="motion", requirements=("lidar",)))
    return item


def motion_task(**kwargs):
    return Task(
        "t1",
        "m1",
        "navigate",
        required_capabilities={"navigate_to"},
        params={"map_id": "floor-1", "x": 1.0, "y": 2.0},
        **kwargs,
    )


def test_motion_requires_battery_matching_map_and_fresh_pose():
    now = datetime.now(UTC)
    policy = SchedulingSafetyPolicy(motion_battery_threshold=20, pose_max_age_sec=5)
    item = robot(map_id="floor-1")
    item.pose = {"x": 0.0, "y": 0.0}
    item.last_seen_at = now

    assert policy.evaluate(item, motion_task(), now=now).allowed

    item.battery_percentage = 10
    assert policy.evaluate(item, motion_task(), now=now).reason == "battery_below_motion_threshold"
    item.battery_percentage = 80
    item.last_seen_at = now - timedelta(seconds=6)
    assert policy.evaluate(item, motion_task(), now=now).reason == "pose_stale"


def test_unhealthy_required_sensor_and_map_mismatch_are_rejected():
    now = datetime.now(UTC)
    policy = SchedulingSafetyPolicy()
    item = robot(map_id="wrong-map")
    item.pose = {"x": 0.0, "y": 0.0}
    item.last_seen_at = now

    assert policy.evaluate(item, motion_task(), now=now).reason.startswith("map_mismatch")
    item.map_id = "floor-1"
    item.health_issues = ["lidar"]
    assert policy.evaluate(item, motion_task(), now=now).reason.startswith("unhealthy_requirements")
