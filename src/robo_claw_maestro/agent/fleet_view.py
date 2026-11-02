"""Fleet capability view shared by the planner prompt, validator, and dry-run.

Robots report capabilities when they register with FleetControl, but a client
that registers without a capability payload leaves the fleet catalog empty for
that robot. ``knowledge/robots.md`` already documents those robots for the
planner, so the profile capabilities are used as a fallback. Prompt, validation,
and dry-run then agree on what a robot can do, instead of the prompt advertising
skills that the validator rejects.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping

from robo_claw_maestro.agent.knowledge import RobotProfile
from robo_claw_maestro.domain.robot import Robot

ProfileCapabilities = Mapping[str, Collection[str]]


def profile_capabilities(profiles: Iterable[RobotProfile]) -> dict[str, set[str]]:
    """Map ``robot_id`` to the capabilities declared in robots.md."""
    return {
        profile.robot_id: {str(name) for name in profile.capabilities if str(name).strip()}
        for profile in profiles
        if profile.capabilities
    }


def robot_capabilities(robot: Robot, profiles: ProfileCapabilities) -> set[str]:
    """Return reported capabilities, falling back to the robots.md profile.

    A robot that reports at least one capability is authoritative; the profile is
    only a fallback for registration payloads that carry none.
    """
    reported = {str(name) for name in robot.capabilities if str(name).strip()}
    if reported:
        return reported
    return {str(name) for name in profiles.get(robot.robot_id, ()) if str(name).strip()}


def fleet_capabilities(
    robots: Iterable[Robot],
    profiles: ProfileCapabilities,
    *,
    static: Iterable[str] = (),
) -> set[str]:
    """Return every capability name the planner is allowed to use."""
    names = {str(name) for name in static if str(name).strip()}
    for robot in robots:
        names |= robot_capabilities(robot, profiles)
    return names


def fleet_catalog(
    robots: Iterable[Robot], profiles: ProfileCapabilities
) -> list[dict[str, object]]:
    """Describe the connected fleet for the planner prompt."""
    return [
        {
            "robot_id": robot.robot_id,
            "robot_type": robot.robot_type,
            "connection_state": str(robot.connection_state),
            "site_id": robot.site_id,
            "map_id": robot.map_id,
            "capabilities": sorted(robot_capabilities(robot, profiles)),
        }
        for robot in robots
    ]
