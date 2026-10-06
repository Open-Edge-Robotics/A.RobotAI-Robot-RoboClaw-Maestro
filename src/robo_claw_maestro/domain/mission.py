"""Mission model and task dependency validation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from .event import DomainEvent
from .task import Task, TaskStatus


class MissionStatus(StrEnum):
    DRAFT = "DRAFT"
    PLANNING = "PLANNING"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    SUCCEEDED = "SUCCEEDED"
    PARTIALLY_SUCCEEDED = "PARTIALLY_SUCCEEDED"
    FAILED = "FAILED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"


_ALLOWED_MISSION_TRANSITIONS: dict[MissionStatus, frozenset[MissionStatus]] = {
    MissionStatus.DRAFT: frozenset(
        {MissionStatus.PLANNING, MissionStatus.READY, MissionStatus.CANCEL_REQUESTED}
    ),
    MissionStatus.PLANNING: frozenset(
        {MissionStatus.READY, MissionStatus.FAILED, MissionStatus.CANCEL_REQUESTED}
    ),
    MissionStatus.READY: frozenset(
        {
            MissionStatus.RUNNING,
            MissionStatus.PAUSED,
            MissionStatus.FAILED,
            MissionStatus.CANCEL_REQUESTED,
        }
    ),
    MissionStatus.RUNNING: frozenset(
        {
            MissionStatus.PAUSED,
            MissionStatus.SUCCEEDED,
            MissionStatus.PARTIALLY_SUCCEEDED,
            MissionStatus.FAILED,
            MissionStatus.CANCEL_REQUESTED,
        }
    ),
    MissionStatus.PAUSED: frozenset({MissionStatus.RUNNING, MissionStatus.CANCEL_REQUESTED}),
    MissionStatus.CANCEL_REQUESTED: frozenset({MissionStatus.CANCELLED, MissionStatus.FAILED}),
    MissionStatus.SUCCEEDED: frozenset(),
    MissionStatus.PARTIALLY_SUCCEEDED: frozenset(),
    MissionStatus.FAILED: frozenset(),
    MissionStatus.CANCELLED: frozenset(),
}


class InvalidMissionTransition(ValueError):
    """Raised when a mission state transition is not allowed."""


class InvalidMissionPlan(ValueError):
    """Raised when a mission contains an invalid task graph."""


@dataclass(slots=True)
class Mission:
    mission_id: str
    goal: str
    priority: int = 0
    created_by: str = "system"
    plan_version: int = 1
    status: MissionStatus = MissionStatus.DRAFT
    tasks: dict[str, Task] = field(default_factory=dict)
    constraints: dict[str, object] = field(default_factory=dict)
    summary: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    _events: list[DomainEvent] = field(default_factory=list, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.mission_id.strip():
            raise ValueError("mission_id must not be empty")
        if not self.goal.strip():
            raise ValueError("goal must not be empty")
        if self.plan_version < 1:
            raise ValueError("plan_version must be at least 1")

    def transition_to(self, new_status: MissionStatus) -> None:
        if new_status == self.status:
            return
        if new_status not in _ALLOWED_MISSION_TRANSITIONS[self.status]:
            raise InvalidMissionTransition(
                f"invalid mission transition: {self.mission_id} {self.status} -> {new_status}"
            )
        previous = self.status
        self.status = new_status
        now = datetime.now(UTC)
        if new_status is MissionStatus.RUNNING and self.started_at is None:
            self.started_at = now
        if new_status in {
            MissionStatus.SUCCEEDED,
            MissionStatus.PARTIALLY_SUCCEEDED,
            MissionStatus.FAILED,
            MissionStatus.CANCELLED,
        }:
            self.finished_at = now
        self._events.append(
            DomainEvent.create(
                event_type="mission.status_changed",
                aggregate_type="mission",
                aggregate_id=self.mission_id,
                payload={"from": previous.value, "to": new_status.value},
                correlation_id=self.mission_id,
            )
        )

    def pull_events(self) -> list[DomainEvent]:
        events, self._events = self._events, []
        return events

    def add_task(self, task: Task) -> None:
        if task.mission_id != self.mission_id:
            raise InvalidMissionPlan(
                f"task {task.task_id} belongs to {task.mission_id}, not {self.mission_id}"
            )
        if task.task_id in self.tasks:
            raise InvalidMissionPlan(f"duplicate task id: {task.task_id}")
        self.tasks[task.task_id] = task
        try:
            self.validate_plan()
        except Exception:
            del self.tasks[task.task_id]
            raise

    def add_tasks(self, tasks: Iterable[Task]) -> None:
        snapshot = dict(self.tasks)
        try:
            for task in tasks:
                if task.mission_id != self.mission_id:
                    raise InvalidMissionPlan(
                        f"task {task.task_id} belongs to {task.mission_id}, not {self.mission_id}"
                    )
                if task.task_id in self.tasks:
                    raise InvalidMissionPlan(f"duplicate task id: {task.task_id}")
                self.tasks[task.task_id] = task
            self.validate_plan()
        except Exception:
            self.tasks = snapshot
            raise

    def validate_plan(self) -> None:
        task_ids = set(self.tasks)
        for task in self.tasks.values():
            missing = task.depends_on - task_ids
            if missing:
                raise InvalidMissionPlan(
                    f"task {task.task_id} depends on missing tasks: {sorted(missing)}"
                )

        # Kahn's algorithm gives deterministic cycle detection without external deps.
        incoming = {task_id: set(task.depends_on) for task_id, task in self.tasks.items()}
        ready = sorted(task_id for task_id, deps in incoming.items() if not deps)
        visited = 0
        while ready:
            current = ready.pop(0)
            visited += 1
            for task_id, deps in incoming.items():
                if current in deps:
                    deps.remove(current)
                    if not deps:
                        ready.append(task_id)
            ready.sort()
        if visited != len(self.tasks):
            cyclic = sorted(task_id for task_id, deps in incoming.items() if deps)
            raise InvalidMissionPlan(f"task dependency cycle detected: {cyclic}")

    def ready_tasks(self) -> list[Task]:
        """Return tasks whose dependencies succeeded and which are not started."""
        self.validate_plan()
        ready: list[Task] = []
        for task in self.tasks.values():
            if task.status not in {TaskStatus.CREATED, TaskStatus.BLOCKED, TaskStatus.READY}:
                continue
            if all(self.tasks[dep].status == TaskStatus.SUCCEEDED for dep in task.depends_on):
                ready.append(task)
        return sorted(ready, key=lambda item: (-item.priority, item.task_id))

    def mark_ready_tasks(self) -> list[str]:
        ids = []
        for task in self.ready_tasks():
            if task.status in {TaskStatus.CREATED, TaskStatus.BLOCKED}:
                task.transition_to(TaskStatus.READY)
            ids.append(task.task_id)
        return ids

    def is_complete(self) -> bool:
        return bool(self.tasks) and all(task.is_terminal for task in self.tasks.values())

    def refresh_status(self) -> MissionStatus:
        if not self.tasks:
            return self.status
        statuses = {task.status for task in self.tasks.values()}
        failure_statuses = {
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.TIMED_OUT,
            TaskStatus.UNKNOWN,
        }
        terminal_statuses = {TaskStatus.SUCCEEDED, *failure_statuses}
        target = self.status
        if all(status == TaskStatus.SUCCEEDED for status in statuses):
            target = MissionStatus.SUCCEEDED
        elif statuses.issubset(failure_statuses):
            target = MissionStatus.FAILED
        elif statuses.issubset(terminal_statuses):
            target = MissionStatus.PARTIALLY_SUCCEEDED
        elif any(
            status
            in {TaskStatus.RUNNING, TaskStatus.QUEUED, TaskStatus.ACCEPTED, TaskStatus.ASSIGNED}
            for status in statuses
        ):
            target = MissionStatus.RUNNING
        if target != self.status:
            self.transition_to(target)
        return self.status
