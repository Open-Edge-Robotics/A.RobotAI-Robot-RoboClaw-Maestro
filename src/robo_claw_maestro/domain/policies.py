"""Deterministic fleet scheduling safety policies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .robot import Robot, RobotConnectionState
from .task import Task


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    reason: str


class SchedulingSafetyPolicy:
    def __init__(
        self,
        *,
        motion_battery_threshold: float = 20.0,
        pose_max_age_sec: float = 5.0,
    ) -> None:
        if not 0 <= motion_battery_threshold <= 100:
            raise ValueError("motion battery threshold must be between 0 and 100")
        if pose_max_age_sec <= 0:
            raise ValueError("pose max age must be positive")
        self._battery_threshold = motion_battery_threshold
        self._pose_max_age = timedelta(seconds=pose_max_age_sec)

    def evaluate(self, robot: Robot, task: Task, *, now: datetime | None = None) -> PolicyDecision:
        if robot.connection_state is not RobotConnectionState.ONLINE:
            return PolicyDecision(False, f"connection_state={robot.connection_state.value}")
        if not robot.has_capabilities(task.required_capabilities):
            missing = sorted(task.required_capabilities - set(robot.capabilities))
            return PolicyDecision(False, f"missing_capabilities={missing}")
        requested_map = str(task.params.get("map_id", ""))
        if requested_map and robot.map_id != requested_map:
            return PolicyDecision(False, f"map_mismatch={robot.map_id or 'unknown'}")
        requested_version = str(task.params.get("map_version", ""))
        if requested_version and robot.map_version != requested_version:
            return PolicyDecision(False, f"map_version_mismatch={robot.map_version or 'unknown'}")
        requested_frame = str(task.params.get("frame_id", ""))
        if requested_frame and robot.frame_id != requested_frame:
            return PolicyDecision(False, f"frame_mismatch={robot.frame_id or 'unknown'}")

        capabilities = [robot.capabilities[name] for name in task.required_capabilities]
        motion_names = {"navigate", "navigate_to", "follow_waypoints", "move"}
        motion = (
            bool(task.params.get("motion"))
            or task.skill_name in motion_names
            or bool(task.required_capabilities & motion_names)
            or any(capability.risk_level in {"motion", "dangerous"} for capability in capabilities)
        )
        if motion and (
            robot.battery_percentage is None or robot.battery_percentage < self._battery_threshold
        ):
            return PolicyDecision(False, "battery_below_motion_threshold")

        unhealthy = set(robot.health_issues)
        required_sensors = {
            requirement for capability in capabilities for requirement in capability.requirements
        }
        failed_requirements = sorted(required_sensors & unhealthy)
        if failed_requirements:
            return PolicyDecision(False, f"unhealthy_requirements={failed_requirements}")

        location_task = all(key in task.params for key in ("x", "y"))
        if location_task:
            if robot.pose is None or robot.last_seen_at is None:
                return PolicyDecision(False, "pose_unavailable")
            observed = robot.last_seen_at
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=UTC)
            if (now or datetime.now(UTC)) - observed > self._pose_max_age:
                return PolicyDecision(False, "pose_stale")
        return PolicyDecision(True, "safety_policy_allowed")
