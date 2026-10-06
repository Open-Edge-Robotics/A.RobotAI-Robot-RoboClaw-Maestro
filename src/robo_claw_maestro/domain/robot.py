"""Robot identity and runtime state.

This module intentionally has no transport, database, ROS 2, or API dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class RobotConnectionState(StrEnum):
    UNKNOWN = "UNKNOWN"
    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"
    ESTOPPED = "ESTOPPED"


class RobotOperationalState(StrEnum):
    UNKNOWN = "UNKNOWN"
    IDLE = "IDLE"
    THINKING = "THINKING"
    EXECUTING = "EXECUTING"
    WAITING = "WAITING"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class Capability:
    """A versioned robot capability exposed to the scheduler."""

    name: str
    version: str = "1.0"
    risk_level: str = "normal"
    requirements: tuple[str, ...] = ()
    input_schema: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("capability name must not be empty")
        if not self.version.strip():
            raise ValueError("capability version must not be empty")


@dataclass(slots=True)
class Robot:
    """Fleet-level representation of one Robo-Claw installation."""

    robot_id: str
    display_name: str
    robot_type: str = "unknown"
    site_id: str = ""
    map_id: str = ""
    map_version: str = ""
    frame_id: str = "map"
    protocol_version: str = "1"
    software_version: str = ""
    session_id: str = ""
    connection_state: RobotConnectionState = RobotConnectionState.UNKNOWN
    operational_state: RobotOperationalState = RobotOperationalState.UNKNOWN
    last_seen_at: datetime | None = None
    capabilities: dict[str, Capability] = field(default_factory=dict)
    battery_percentage: float | None = None
    pose: dict[str, float | str] | None = None
    health_issues: list[str] = field(default_factory=list)
    current_task_id: str | None = None
    current_skill: str = ""
    queue_depth: int = 0

    def __post_init__(self) -> None:
        if not self.robot_id.strip():
            raise ValueError("robot_id must not be empty")
        if not self.display_name.strip():
            raise ValueError("display_name must not be empty")
        if not 0 <= self.queue_depth:
            raise ValueError("queue_depth must not be negative")
        if self.battery_percentage is not None and not 0 <= self.battery_percentage <= 100:
            raise ValueError("battery_percentage must be between 0 and 100")

    def has_capabilities(self, required: set[str] | frozenset[str]) -> bool:
        return set(required).issubset(self.capabilities)

    def is_eligible(self, required_capabilities: set[str] | frozenset[str]) -> bool:
        """Return whether this robot is a safe candidate for a new task."""
        if self.connection_state not in (
            RobotConnectionState.ONLINE,
            RobotConnectionState.DEGRADED,
        ):
            return False
        if self.connection_state == RobotConnectionState.DEGRADED and self.health_issues:
            return False
        return self.has_capabilities(required_capabilities)

    def update_seen(self, seen_at: datetime | None = None) -> None:
        self.last_seen_at = seen_at or datetime.now(UTC)
        if self.connection_state == RobotConnectionState.OFFLINE:
            self.connection_state = RobotConnectionState.ONLINE

    def add_capability(self, capability: Capability) -> None:
        self.capabilities[capability.name] = capability
