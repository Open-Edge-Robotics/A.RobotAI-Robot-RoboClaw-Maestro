"""In-memory repositories for development and deterministic tests."""

from __future__ import annotations

from datetime import datetime

from robo_claw_maestro.domain.approval import ApprovalRequest
from robo_claw_maestro.domain.command import Command
from robo_claw_maestro.domain.event import DomainEvent
from robo_claw_maestro.domain.mission import Mission
from robo_claw_maestro.domain.robot import Robot


class InMemoryMissionRepository:
    def __init__(self) -> None:
        self._items: dict[str, Mission] = {}

    def add(self, mission: Mission) -> None:
        if mission.mission_id in self._items:
            raise ValueError(f"mission already exists: {mission.mission_id}")
        self._items[mission.mission_id] = mission

    def save(self, mission: Mission) -> None:
        self._items[mission.mission_id] = mission

    def get(self, mission_id: str) -> Mission | None:
        return self._items.get(mission_id)

    def list(self) -> list[Mission]:
        return sorted(self._items.values(), key=lambda item: item.mission_id)


class InMemoryRobotRepository:
    def __init__(self) -> None:
        self._items: dict[str, Robot] = {}

    def add(self, robot: Robot) -> None:
        if robot.robot_id in self._items:
            raise ValueError(f"robot already exists: {robot.robot_id}")
        self._items[robot.robot_id] = robot

    def save(self, robot: Robot) -> None:
        self._items[robot.robot_id] = robot

    def get(self, robot_id: str) -> Robot | None:
        return self._items.get(robot_id)

    def list(self) -> list[Robot]:
        return sorted(self._items.values(), key=lambda item: item.robot_id)


class InMemoryApprovalRepository:
    def __init__(self) -> None:
        self._items: dict[str, ApprovalRequest] = {}

    def add(self, approval: ApprovalRequest) -> None:
        if approval.approval_id in self._items:
            raise ValueError(f"approval already exists: {approval.approval_id}")
        self._items[approval.approval_id] = approval

    def save(self, approval: ApprovalRequest) -> None:
        self._items[approval.approval_id] = approval

    def get(self, approval_id: str) -> ApprovalRequest | None:
        return self._items.get(approval_id)

    def list(self) -> list[ApprovalRequest]:
        return sorted(self._items.values(), key=lambda item: item.created_at)


class InMemoryCommandRepository:
    def __init__(self) -> None:
        self._items: dict[str, Command] = {}
        self._idempotency_keys: dict[str, str] = {}

    def add(self, command: Command) -> None:
        if command.command_id in self._items:
            raise ValueError(f"command already exists: {command.command_id}")
        if command.idempotency_key in self._idempotency_keys:
            raise ValueError(f"duplicate idempotency key: {command.idempotency_key}")
        self._items[command.command_id] = command
        self._idempotency_keys[command.idempotency_key] = command.command_id

    def get(self, command_id: str) -> Command | None:
        return self._items.get(command_id)

    def get_by_idempotency_key(self, idempotency_key: str) -> Command | None:
        command_id = self._idempotency_keys.get(idempotency_key)
        return self._items.get(command_id) if command_id else None

    def list(self) -> list[Command]:
        return sorted(self._items.values(), key=lambda item: item.command_id)


class InMemoryEventRepository:
    def __init__(self) -> None:
        self._events: list[DomainEvent] = []

    def append(self, event: DomainEvent) -> None:
        self._events.append(event)

    def list(self, aggregate_id: str | None = None) -> list[DomainEvent]:
        if aggregate_id is None:
            return list(self._events)
        return [event for event in self._events if event.aggregate_id == aggregate_id]

    def delete_before(self, before: datetime) -> int:
        retained = [event for event in self._events if event.occurred_at >= before]
        deleted = len(self._events) - len(retained)
        self._events = retained
        return deleted
