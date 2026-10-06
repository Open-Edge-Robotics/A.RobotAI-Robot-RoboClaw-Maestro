"""SQLModel persistence adapter.

Aggregates are stored as JSON documents so domain dataclasses stay independent
from SQLModel. Event rows remain append-only and independently queryable.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, TypeVar

from sqlalchemy import delete, inspect, text
from sqlmodel import Field, Session, SQLModel, create_engine, select

from robo_claw_maestro.domain.approval import ApprovalRequest, ApprovalStatus
from robo_claw_maestro.domain.command import Command, CommandStatus
from robo_claw_maestro.domain.event import DomainEvent
from robo_claw_maestro.domain.mission import Mission, MissionStatus
from robo_claw_maestro.domain.resource import ResourceLease
from robo_claw_maestro.domain.robot import (
    Capability,
    Robot,
    RobotConnectionState,
    RobotOperationalState,
)
from robo_claw_maestro.domain.task import Task, TaskStatus


class MissionRecord(SQLModel, table=True):
    __tablename__ = "missions"
    mission_id: str = Field(primary_key=True)
    payload_json: str


class RobotRecord(SQLModel, table=True):
    __tablename__ = "robots"
    robot_id: str = Field(primary_key=True)
    payload_json: str


class ApprovalRecord(SQLModel, table=True):
    __tablename__ = "approvals"
    approval_id: str = Field(primary_key=True)
    status: str = Field(index=True)
    created_at: datetime = Field(index=True)
    payload_json: str


class CommandRecord(SQLModel, table=True):
    __tablename__ = "commands"
    command_id: str = Field(primary_key=True)
    idempotency_key: str = Field(unique=True, index=True)
    payload_json: str


class ResourceLeaseRecord(SQLModel, table=True):
    __tablename__ = "resource_leases"
    resource_id: str = Field(primary_key=True)
    owner_id: str = Field(index=True)
    expires_at: datetime = Field(index=True)


class EventRecord(SQLModel, table=True):
    __tablename__ = "events"
    event_id: str = Field(primary_key=True)
    aggregate_id: str = Field(index=True)
    occurred_at: datetime = Field(index=True)
    payload_json: str


T = TypeVar("T", Mission, Robot)


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, Enum)):
        return value.isoformat() if isinstance(value, datetime) else value.value
    if isinstance(value, set | frozenset | tuple):
        return list(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _task_to_dict(task: Task) -> dict[str, Any]:
    return {
        name: getattr(task, name) for name in task.__dataclass_fields__ if not name.startswith("_")
    }


def _mission_to_json(mission: Mission) -> str:
    data = {
        name: getattr(mission, name)
        for name in mission.__dataclass_fields__
        if name != "tasks" and not name.startswith("_")
    }
    data["tasks"] = [_task_to_dict(task) for task in mission.tasks.values()]
    return json.dumps(data, default=_json_default, ensure_ascii=False)


def _mission_from_json(raw: str) -> Mission:
    data = json.loads(raw)
    tasks = data.pop("tasks", [])
    data["status"] = MissionStatus(data["status"])
    for name in ("created_at", "started_at", "finished_at"):
        if data.get(name):
            data[name] = datetime.fromisoformat(data[name])
    mission = Mission(**data)
    for item in tasks:
        item["status"] = TaskStatus(item["status"])
        item["required_capabilities"] = set(item.get("required_capabilities", []))
        item["depends_on"] = set(item.get("depends_on", []))
        item["attempted_robot_ids"] = set(item.get("attempted_robot_ids", []))
        if item.get("deadline"):
            item["deadline"] = datetime.fromisoformat(item["deadline"])
        mission.tasks[item["task_id"]] = Task(**item)
    mission.validate_plan()
    return mission


def _robot_to_json(robot: Robot) -> str:
    data = {name: getattr(robot, name) for name in robot.__dataclass_fields__}
    data["capabilities"] = {
        name: {field: getattr(capability, field) for field in capability.__dataclass_fields__}
        for name, capability in robot.capabilities.items()
    }
    return json.dumps(data, default=_json_default, ensure_ascii=False)


def _robot_from_json(raw: str) -> Robot:
    data = json.loads(raw)
    data["connection_state"] = RobotConnectionState(data["connection_state"])
    data["operational_state"] = RobotOperationalState(data["operational_state"])
    if data.get("last_seen_at"):
        data["last_seen_at"] = datetime.fromisoformat(data["last_seen_at"])
    data["capabilities"] = {
        name: Capability(
            **{
                **value,
                "requirements": tuple(value.get("requirements", [])),
            }
        )
        for name, value in data.get("capabilities", {}).items()
    }
    return Robot(**data)


def _approval_to_json(approval: ApprovalRequest) -> str:
    data = {name: getattr(approval, name) for name in approval.__dataclass_fields__}
    return json.dumps(data, default=_json_default, ensure_ascii=False)


def _approval_from_json(raw: str) -> ApprovalRequest:
    data = json.loads(raw)
    data["status"] = ApprovalStatus(data["status"])
    for name in ("created_at", "expires_at", "decided_at"):
        if data.get(name):
            data[name] = datetime.fromisoformat(data[name])
    return ApprovalRequest(**data)


def _command_to_json(command: Command) -> str:
    data = {name: getattr(command, name) for name in command.__dataclass_fields__}
    return json.dumps(data, default=_json_default, ensure_ascii=False)


def _command_from_json(raw: str) -> Command:
    data = json.loads(raw)
    data["status"] = CommandStatus(data["status"])
    if data.get("issued_at"):
        data["issued_at"] = datetime.fromisoformat(data["issued_at"])
    return Command(**data)


def _event_to_json(event: DomainEvent) -> str:
    data = {name: getattr(event, name) for name in event.__dataclass_fields__}
    return json.dumps(data, default=_json_default, ensure_ascii=False)


def _event_from_json(raw: str) -> DomainEvent:
    data = json.loads(raw)
    data["occurred_at"] = datetime.fromisoformat(data["occurred_at"])
    return DomainEvent(**data)


class SqlModelStore:
    """Small synchronous unit-of-work used by repository adapters."""

    def __init__(self, database_url: str, *, create_tables: bool = False) -> None:
        self.engine = create_engine(database_url)
        if create_tables:
            SQLModel.metadata.create_all(self.engine)

    def healthcheck(self) -> bool:
        try:
            with Session(self.engine) as session:
                session.exec(text("SELECT 1"))
            required = {
                "missions",
                "robots",
                "commands",
                "events",
                "resource_leases",
                "approvals",
            }
            return required.issubset(inspect(self.engine).get_table_names())
        except Exception:
            return False

    def add(self, value: Mission | Robot) -> None:
        record = self._record(value)
        with Session(self.engine) as session:
            try:
                session.add(record)
                session.commit()
            except Exception:
                session.rollback()
                raise

    def save(self, value: Mission | Robot) -> None:
        record = self._record(value)
        with Session(self.engine) as session:
            session.merge(record)
            session.commit()

    def get(self, kind: type[T], identity: str) -> T | None:
        record_type = MissionRecord if kind is Mission else RobotRecord
        with Session(self.engine) as session:
            record = session.get(record_type, identity)
        if record is None:
            return None
        return (
            _mission_from_json(record.payload_json)
            if kind is Mission
            else _robot_from_json(record.payload_json)
        )  # type: ignore[return-value]

    def list(self, kind: type[T]) -> list[T]:
        record_type = MissionRecord if kind is Mission else RobotRecord
        key = MissionRecord.mission_id if kind is Mission else RobotRecord.robot_id
        with Session(self.engine) as session:
            records = list(session.exec(select(record_type).order_by(key)))
        parser = _mission_from_json if kind is Mission else _robot_from_json
        return [parser(item.payload_json) for item in records]  # type: ignore[misc,return-value]

    def append(self, event: DomainEvent) -> None:
        record = EventRecord(
            event_id=event.event_id,
            aggregate_id=event.aggregate_id,
            occurred_at=event.occurred_at,
            payload_json=_event_to_json(event),
        )
        with Session(self.engine) as session:
            session.add(record)
            session.commit()

    def events(self, aggregate_id: str | None = None) -> list[DomainEvent]:
        statement = select(EventRecord).order_by(EventRecord.occurred_at, EventRecord.event_id)
        if aggregate_id is not None:
            statement = statement.where(EventRecord.aggregate_id == aggregate_id)
        with Session(self.engine) as session:
            records = list(session.exec(statement))
        return [_event_from_json(item.payload_json) for item in records]

    @staticmethod
    def _record(value: Mission | Robot) -> MissionRecord | RobotRecord:
        if isinstance(value, Mission):
            return MissionRecord(mission_id=value.mission_id, payload_json=_mission_to_json(value))
        if isinstance(value, Robot):
            return RobotRecord(robot_id=value.robot_id, payload_json=_robot_to_json(value))
        raise TypeError(f"unsupported aggregate: {type(value).__name__}")


class SqlModelMissionRepository:
    def __init__(self, store: SqlModelStore) -> None:
        self.store = store

    def add(self, mission: Mission) -> None:
        self.store.add(mission)

    def save(self, mission: Mission) -> None:
        self.store.save(mission)

    def get(self, mission_id: str) -> Mission | None:
        return self.store.get(Mission, mission_id)

    def list(self) -> list[Mission]:
        return self.store.list(Mission)


class SqlModelRobotRepository:
    def __init__(self, store: SqlModelStore) -> None:
        self.store = store

    def add(self, robot: Robot) -> None:
        self.store.add(robot)

    def save(self, robot: Robot) -> None:
        self.store.save(robot)

    def get(self, robot_id: str) -> Robot | None:
        return self.store.get(Robot, robot_id)

    def list(self) -> list[Robot]:
        return self.store.list(Robot)


class SqlModelApprovalRepository:
    def __init__(self, store: SqlModelStore) -> None:
        self.store = store

    def add(self, approval: ApprovalRequest) -> None:
        with Session(self.store.engine) as session:
            session.add(
                ApprovalRecord(
                    approval_id=approval.approval_id,
                    status=approval.status.value,
                    created_at=approval.created_at,
                    payload_json=_approval_to_json(approval),
                )
            )
            session.commit()

    def save(self, approval: ApprovalRequest) -> None:
        with Session(self.store.engine) as session:
            session.merge(
                ApprovalRecord(
                    approval_id=approval.approval_id,
                    status=approval.status.value,
                    created_at=approval.created_at,
                    payload_json=_approval_to_json(approval),
                )
            )
            session.commit()

    def get(self, approval_id: str) -> ApprovalRequest | None:
        with Session(self.store.engine) as session:
            record = session.get(ApprovalRecord, approval_id)
        return _approval_from_json(record.payload_json) if record else None

    def list(self) -> list[ApprovalRequest]:
        with Session(self.store.engine) as session:
            records = list(session.exec(select(ApprovalRecord).order_by(ApprovalRecord.created_at)))
        return [_approval_from_json(record.payload_json) for record in records]


class SqlResourceLockManager:
    """Database-backed resource locks that survive Maestro restarts."""

    def __init__(self, store: SqlModelStore) -> None:
        self.store = store

    def acquire_many(self, resources: set[str], owner_id: str, lease_sec: float) -> bool:
        if lease_sec <= 0:
            raise ValueError("lease_sec must be positive")
        if not resources:
            return True
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=lease_sec)
        with Session(self.store.engine) as session:
            try:
                session.exec(
                    delete(ResourceLeaseRecord).where(ResourceLeaseRecord.expires_at <= now)
                )
                statement = (
                    select(ResourceLeaseRecord)
                    .where(ResourceLeaseRecord.resource_id.in_(resources))
                    .with_for_update()
                )
                existing = list(session.exec(statement))
                if any(record.owner_id != owner_id for record in existing):
                    session.rollback()
                    return False
                for resource_id in resources:
                    session.merge(
                        ResourceLeaseRecord(
                            resource_id=resource_id,
                            owner_id=owner_id,
                            expires_at=expires_at,
                        )
                    )
                session.commit()
                return True
            except Exception:
                session.rollback()
                return False

    def release_owner(self, owner_id: str) -> None:
        with Session(self.store.engine) as session:
            session.exec(
                delete(ResourceLeaseRecord).where(ResourceLeaseRecord.owner_id == owner_id)
            )
            session.commit()

    def cleanup_expired(self) -> int:
        now = datetime.now(UTC)
        with Session(self.store.engine) as session:
            result = session.exec(
                delete(ResourceLeaseRecord).where(ResourceLeaseRecord.expires_at <= now)
            )
            session.commit()
            return int(result.rowcount or 0)

    def list(self) -> list[ResourceLease]:
        now = datetime.now(UTC)
        with Session(self.store.engine) as session:
            records = list(
                session.exec(
                    select(ResourceLeaseRecord)
                    .where(ResourceLeaseRecord.expires_at > now)
                    .order_by(ResourceLeaseRecord.resource_id)
                )
            )
        return [
            ResourceLease(record.resource_id, record.owner_id, record.expires_at)
            for record in records
        ]


class SqlModelCommandRepository:
    def __init__(self, store: SqlModelStore) -> None:
        self.store = store

    def add(self, command: Command) -> None:
        record = CommandRecord(
            command_id=command.command_id,
            idempotency_key=command.idempotency_key,
            payload_json=_command_to_json(command),
        )
        with Session(self.store.engine) as session:
            try:
                session.add(record)
                session.commit()
            except Exception:
                session.rollback()
                raise

    def get(self, command_id: str) -> Command | None:
        with Session(self.store.engine) as session:
            record = session.get(CommandRecord, command_id)
        return _command_from_json(record.payload_json) if record else None

    def get_by_idempotency_key(self, idempotency_key: str) -> Command | None:
        statement = select(CommandRecord).where(CommandRecord.idempotency_key == idempotency_key)
        with Session(self.store.engine) as session:
            record = session.exec(statement).first()
        return _command_from_json(record.payload_json) if record else None

    def list(self) -> list[Command]:
        with Session(self.store.engine) as session:
            records = list(session.exec(select(CommandRecord).order_by(CommandRecord.command_id)))
        return [_command_from_json(record.payload_json) for record in records]


class SqlModelEventRepository:
    def __init__(self, store: SqlModelStore) -> None:
        self.store = store

    def append(self, event: DomainEvent) -> None:
        self.store.append(event)

    def list(self, aggregate_id: str | None = None) -> list[DomainEvent]:
        return self.store.events(aggregate_id)

    def delete_before(self, before: datetime) -> int:
        with Session(self.store.engine) as session:
            result = session.exec(delete(EventRecord).where(EventRecord.occurred_at < before))
            session.commit()
            return int(result.rowcount or 0)
