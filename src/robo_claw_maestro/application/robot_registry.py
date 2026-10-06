"""Robot registration, heartbeat, status synchronization, and fleet stop."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from robo_claw_maestro.adapters.database.in_memory import (
    InMemoryEventRepository,
    InMemoryRobotRepository,
)
from robo_claw_maestro.domain.event import DomainEvent
from robo_claw_maestro.domain.robot import Robot, RobotConnectionState
from robo_claw_maestro.ports.repositories import EventRepository, RobotRepository
from robo_claw_maestro.ports.robot_gateway import (
    RemoteTaskResult,
    RobotGateway,
    RobotSnapshot,
    RobotStatusSnapshot,
    RobotUnavailable,
)


@dataclass(frozen=True, slots=True)
class FleetStopResult:
    requested: int
    stopped: tuple[str, ...]
    failed: dict[str, str]


class RobotRegistry:
    """In-memory registry for the first vertical slice.

    Persistence will be added behind a repository port in the next phase. The
    synchronization rules are kept here so the future database adapter does not
    need to know about transport details.
    """

    def __init__(
        self,
        gateway: RobotGateway,
        heartbeat_timeout_sec: float = 15.0,
        repository: RobotRepository | None = None,
        event_repository: EventRepository | None = None,
    ) -> None:
        if heartbeat_timeout_sec <= 0:
            raise ValueError("heartbeat_timeout_sec must be positive")
        self._gateway = gateway
        self._heartbeat_timeout = timedelta(seconds=heartbeat_timeout_sec)
        self._robots = repository or InMemoryRobotRepository()
        self._events = event_repository or InMemoryEventRepository()

    @property
    def events(self) -> EventRepository:
        return self._events

    def register(self, robot: Robot) -> None:
        if self._robots.get(robot.robot_id) is not None:
            raise ValueError(f"robot already registered: {robot.robot_id}")
        self._robots.add(robot)
        self._emit("robot.registered", robot.robot_id, {"display_name": robot.display_name})

    def save(self, robot: Robot) -> None:
        if self._robots.get(robot.robot_id) is None:
            raise KeyError(f"robot not registered: {robot.robot_id}")
        self._robots.save(robot)

    def get(self, robot_id: str) -> Robot:
        robot = self._robots.get(robot_id)
        if robot is None:
            raise KeyError(f"robot not registered: {robot_id}")
        return robot

    def list(self) -> list[Robot]:
        return self._robots.list()

    async def synchronize(self, robot_id: str) -> Robot:
        robot = self.get(robot_id)
        try:
            snapshot = await self._gateway.get_status(robot_id)
        except RobotUnavailable:
            previous = robot.connection_state
            robot.connection_state = RobotConnectionState.OFFLINE
            if previous is not RobotConnectionState.OFFLINE:
                self._emit(
                    "robot.connection_changed",
                    robot_id,
                    {"from": previous.value, "to": RobotConnectionState.OFFLINE.value},
                )
            save = getattr(self._robots, "save", None)
            if save is not None:
                save(robot)
            return robot
        return self.apply_snapshot(snapshot)

    async def synchronize_all(self) -> list[Robot]:
        robot_ids = sorted(robot.robot_id for robot in self._robots.list())
        return list(await asyncio.gather(*(self.synchronize(robot_id) for robot_id in robot_ids)))

    def heartbeat(self, robot_id: str, observed_at: datetime | None = None) -> Robot:
        robot = self.get(robot_id)
        previous = robot.connection_state
        robot.connection_state = RobotConnectionState.ONLINE
        robot.update_seen(observed_at)
        save = getattr(self._robots, "save", None)
        if save is not None:
            save(robot)
        if previous is not RobotConnectionState.ONLINE:
            self._emit(
                "robot.connection_changed",
                robot_id,
                {"from": previous.value, "to": RobotConnectionState.ONLINE.value},
            )
        return robot

    def mark_offline(self, robot_id: str) -> Robot:
        robot = self.get(robot_id)
        previous = robot.connection_state
        robot.connection_state = RobotConnectionState.OFFLINE
        if previous is not RobotConnectionState.OFFLINE:
            self._emit(
                "robot.connection_changed",
                robot_id,
                {"from": previous.value, "to": RobotConnectionState.OFFLINE.value},
            )
        save = getattr(self._robots, "save", None)
        if save is not None:
            save(robot)
        return robot

    def mark_stale(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(UTC)
        stale: list[str] = []
        for robot in self.list():
            if robot.last_seen_at is None:
                continue
            last_seen = robot.last_seen_at
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=UTC)
            if (
                now - last_seen > self._heartbeat_timeout
                and robot.connection_state is not RobotConnectionState.OFFLINE
            ):
                previous = robot.connection_state
                robot.connection_state = RobotConnectionState.OFFLINE
                self._emit(
                    "robot.connection_changed",
                    robot.robot_id,
                    {"from": previous.value, "to": RobotConnectionState.OFFLINE.value},
                )
                save = getattr(self._robots, "save", None)
                if save is not None:
                    save(robot)
                stale.append(robot.robot_id)
        return sorted(stale)

    def cleanup_events(self, retention_days: int, now: datetime | None = None) -> int:
        if retention_days < 1:
            raise ValueError("retention_days must be positive")
        before = (now or datetime.now(UTC)) - timedelta(days=retention_days)
        return self._events.delete_before(before)

    async def execute_skill(
        self,
        robot_id: str,
        skill_name: str,
        params: dict[str, object],
        timeout_sec: float = 30.0,
    ) -> RemoteTaskResult:
        self.get(robot_id)
        return await self._gateway.execute_skill(
            robot_id,
            skill_name=skill_name,
            params=params,
            timeout_sec=timeout_sec,
        )

    async def request_snapshot(self, robot_id: str) -> RobotSnapshot:
        self.get(robot_id)
        return await self._gateway.request_snapshot(robot_id)

    async def emergency_stop(self, robot_id: str) -> RemoteTaskResult:
        robot = self.get(robot_id)
        result = await self._gateway.emergency_stop(robot_id)
        if result.success:
            previous = robot.connection_state
            robot.connection_state = RobotConnectionState.ESTOPPED
            self._emit(
                "robot.emergency_stopped",
                robot_id,
                {"from": previous.value, "to": RobotConnectionState.ESTOPPED.value},
            )
            save = getattr(self._robots, "save", None)
            if save is not None:
                save(robot)
        return result

    async def emergency_stop_all(self) -> FleetStopResult:
        robot_ids = [robot.robot_id for robot in self.list()]

        async def stop_one(robot_id: str) -> tuple[str, bool, str]:
            try:
                result = await self.emergency_stop(robot_id)
                return robot_id, result.success, result.message
            except Exception as exc:  # transport failures must be reported per robot
                return robot_id, False, str(exc)

        outcomes = await asyncio.gather(*(stop_one(robot_id) for robot_id in robot_ids))
        stopped: list[str] = []
        failed: dict[str, str] = {}
        for robot_id, success, message in outcomes:
            if success:
                stopped.append(robot_id)
            else:
                failed[robot_id] = message
        return FleetStopResult(len(robot_ids), tuple(stopped), failed)

    def apply_snapshot(self, snapshot: RobotStatusSnapshot) -> Robot:
        robot = self.get(snapshot.robot_id)
        previous_connection = robot.connection_state
        robot.connection_state = snapshot.connection_state
        robot.operational_state = snapshot.operational_state
        robot.battery_percentage = snapshot.battery_percentage
        robot.pose = snapshot.pose
        if snapshot.pose:
            robot.map_id = str(snapshot.pose.get("map_id") or robot.map_id)
            robot.frame_id = str(snapshot.pose.get("frame_id") or robot.frame_id)
        robot.current_task_id = snapshot.current_task_id
        robot.current_skill = snapshot.current_skill
        robot.queue_depth = snapshot.queue_depth
        robot.health_issues = list(snapshot.health_issues)
        robot.update_seen(snapshot.observed_at)
        if not robot.session_id or previous_connection in {
            RobotConnectionState.UNKNOWN,
            RobotConnectionState.OFFLINE,
        }:
            robot.session_id = uuid4().hex
        save = getattr(self._robots, "save", None)
        if save is not None:
            save(robot)
        if previous_connection != robot.connection_state:
            self._emit(
                "robot.connection_changed",
                robot.robot_id,
                {"from": previous_connection.value, "to": robot.connection_state.value},
            )
        self._emit(
            "robot.telemetry_updated",
            robot.robot_id,
            {
                "battery_percentage": robot.battery_percentage,
                "operational_state": robot.operational_state.value,
                "current_task_id": robot.current_task_id,
                "queue_depth": robot.queue_depth,
            },
        )
        return robot

    def _emit(self, event_type: str, robot_id: str, payload: dict[str, object]) -> None:
        self._events.append(
            DomainEvent.create(
                event_type=event_type,
                aggregate_type="robot",
                aggregate_id=robot_id,
                payload=payload,
                correlation_id=robot_id,
            )
        )
