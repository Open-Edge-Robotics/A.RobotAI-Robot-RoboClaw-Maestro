import pytest

from robo_claw_maestro.agent.fleet_view import (
    fleet_capabilities,
    fleet_catalog,
    profile_capabilities,
    robot_capabilities,
)
from robo_claw_maestro.agent.knowledge import RobotProfile
from robo_claw_maestro.domain.robot import Capability, Robot

pytestmark = pytest.mark.unit


def robot(robot_id: str, capabilities: tuple[str, ...] = ()) -> Robot:
    instance = Robot(robot_id, robot_id)
    for name in capabilities:
        instance.add_capability(Capability(name))
    return instance


def test_profile_capabilities_are_keyed_by_robot_id():
    profiles = [
        RobotProfile("cloid01", capabilities=("get_status", "patrol")),
        RobotProfile("empty"),
    ]

    assert profile_capabilities(profiles) == {"cloid01": {"get_status", "patrol"}}


def test_robot_without_reported_capabilities_uses_profile():
    profiles = {"cloid01": {"get_status", "patrol"}}

    assert robot_capabilities(robot("cloid01"), profiles) == {"get_status", "patrol"}


def test_reported_capabilities_win_over_profile():
    profiles = {"Former0045": {"patrol"}}

    assert robot_capabilities(robot("Former0045", ("grasp",)), profiles) == {"grasp"}


def test_robot_missing_from_profiles_stays_empty():
    assert robot_capabilities(robot("unknown"), {}) == set()


def test_fleet_capabilities_merge_static_live_and_profile_names():
    robots = [robot("Former0045", ("grasp",)), robot("cloid01")]
    profiles = {"cloid01": {"patrol"}}

    names = fleet_capabilities(robots, profiles, static={"navigate_to"})

    assert names == {"navigate_to", "grasp", "patrol"}


def test_fleet_catalog_reports_effective_capabilities_per_robot():
    robots = [robot("Former0045", ("grasp",)), robot("cloid01")]
    profiles = {"cloid01": {"patrol", "get_status"}}

    catalog = fleet_catalog(robots, profiles)

    assert [entry["robot_id"] for entry in catalog] == ["Former0045", "cloid01"]
    assert catalog[0]["capabilities"] == ["grasp"]
    assert catalog[1]["capabilities"] == ["get_status", "patrol"]
