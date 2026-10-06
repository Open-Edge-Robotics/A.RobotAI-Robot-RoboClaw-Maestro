"""Robot transport port.

The application layer depends on this interface rather than on gRPC or ROS.
The first production adapter will wrap Robo-Claw's existing ``RosGrpc`` API;
tests use :class:`FakeRobotGateway`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from robo_claw_maestro.domain.robot import RobotConnectionState, RobotOperationalState


@dataclass(frozen=True, slots=True)
class RobotStatusSnapshot:
    robot_id: str
    connection_state: RobotConnectionState = RobotConnectionState.ONLINE
    operational_state: RobotOperationalState = RobotOperationalState.IDLE
    battery_percentage: float | None = None
    pose: dict[str, float | str] | None = None
    current_task_id: str | None = None
    current_skill: str = ""
    queue_depth: int = 0
    health_issues: tuple[str, ...] = ()
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class RobotDescriptor:
    robot_id: str
    display_name: str
    robot_type: str = "unknown"


@dataclass(frozen=True, slots=True)
class RobotSnapshot:
    robot_id: str
    image_base64: str
    image_format: str
    timestamp_ms: int


@dataclass(frozen=True, slots=True)
class RemoteTaskResult:
    success: bool
    message: str
    result: dict[str, Any] = field(default_factory=dict)
    remote_task_id: str = ""


class RobotGatewayError(RuntimeError):
    """Base error for transport and remote robot failures."""


class RobotUnavailable(RobotGatewayError):
    """Raised when a robot cannot receive a command."""


class RobotGateway(Protocol):
    """Transport operations required by Maestro application services."""

    async def ping(self, robot_id: str) -> bool: ...

    async def get_info(self, robot_id: str) -> RobotDescriptor: ...

    async def get_status(self, robot_id: str) -> RobotStatusSnapshot: ...

    def stream_status(self, robot_id: str) -> AsyncIterator[RobotStatusSnapshot]: ...

    async def execute_task(
        self,
        robot_id: str,
        *,
        instruction: str,
        task_id: str,
        timeout_sec: float,
        idempotency_key: str = "",
        mission_id: str = "",
        attempt_id: str = "",
        correlation_id: str = "",
    ) -> RemoteTaskResult: ...

    async def execute_skill(
        self,
        robot_id: str,
        *,
        skill_name: str,
        params: dict[str, Any],
        timeout_sec: float,
    ) -> RemoteTaskResult: ...

    async def request_snapshot(self, robot_id: str) -> RobotSnapshot: ...

    async def emergency_stop(self, robot_id: str) -> RemoteTaskResult: ...


class FakeRobotGateway:
    """Deterministic in-memory gateway for component and integration tests."""

    def __init__(self, robots: list[RobotStatusSnapshot] | None = None) -> None:
        self._statuses = {item.robot_id: item for item in robots or []}
        self.executed_tasks: list[dict[str, Any]] = []
        self.emergency_stops: list[str] = []
        self.fail_commands_for: set[str] = set()
        self.unavailable: set[str] = set()

    def add_robot(self, snapshot: RobotStatusSnapshot) -> None:
        self._statuses[snapshot.robot_id] = snapshot

    def set_status(self, snapshot: RobotStatusSnapshot) -> None:
        self._statuses[snapshot.robot_id] = snapshot

    def disconnect(self, robot_id: str) -> None:
        self.unavailable.add(robot_id)

    def reconnect(self, robot_id: str) -> None:
        self.unavailable.discard(robot_id)

    async def ping(self, robot_id: str) -> bool:
        return robot_id in self._statuses and robot_id not in self.unavailable

    async def get_info(self, robot_id: str) -> RobotDescriptor:
        if not await self.ping(robot_id):
            raise RobotUnavailable(f"robot unavailable: {robot_id}")
        return RobotDescriptor(robot_id=robot_id, display_name=robot_id)

    async def get_status(self, robot_id: str) -> RobotStatusSnapshot:
        if not await self.ping(robot_id):
            raise RobotUnavailable(f"robot unavailable: {robot_id}")
        return self._statuses[robot_id]

    async def stream_status(self, robot_id: str) -> AsyncIterator[RobotStatusSnapshot]:
        yield await self.get_status(robot_id)

    async def execute_task(
        self,
        robot_id: str,
        *,
        instruction: str,
        task_id: str,
        timeout_sec: float,
        idempotency_key: str = "",
        mission_id: str = "",
        attempt_id: str = "",
        correlation_id: str = "",
    ) -> RemoteTaskResult:
        del timeout_sec
        if not await self.ping(robot_id):
            raise RobotUnavailable(f"robot unavailable: {robot_id}")
        record = {
            "robot_id": robot_id,
            "task_id": task_id,
            "instruction": instruction,
            "idempotency_key": idempotency_key,
            "mission_id": mission_id,
            "attempt_id": attempt_id,
            "correlation_id": correlation_id,
        }
        self.executed_tasks.append(record)
        if robot_id in self.fail_commands_for:
            return RemoteTaskResult(False, "fake remote failure", {"code": "FAKE_FAILURE"})
        return RemoteTaskResult(True, "fake task completed", {"task_id": task_id})

    async def execute_skill(
        self,
        robot_id: str,
        *,
        skill_name: str,
        params: dict[str, Any],
        timeout_sec: float,
    ) -> RemoteTaskResult:
        return await self.execute_task(
            robot_id,
            instruction=f"skill:{skill_name}:{params}",
            task_id=f"skill:{skill_name}",
            timeout_sec=timeout_sec,
        )

    async def request_snapshot(self, robot_id: str) -> RobotSnapshot:
        if not await self.ping(robot_id):
            raise RobotUnavailable(f"robot unavailable: {robot_id}")
        return RobotSnapshot(robot_id, "", "image/jpeg", int(datetime.now(UTC).timestamp() * 1000))

    async def emergency_stop(self, robot_id: str) -> RemoteTaskResult:
        if not await self.ping(robot_id):
            raise RobotUnavailable(f"robot unavailable: {robot_id}")
        self.emergency_stops.append(robot_id)
        current = self._statuses[robot_id]
        self._statuses[robot_id] = RobotStatusSnapshot(
            robot_id=robot_id,
            connection_state=RobotConnectionState.ESTOPPED,
            operational_state=RobotOperationalState.ERROR,
            battery_percentage=current.battery_percentage,
            pose=current.pose,
            health_issues=current.health_issues,
        )
        return RemoteTaskResult(True, "emergency stop accepted")
