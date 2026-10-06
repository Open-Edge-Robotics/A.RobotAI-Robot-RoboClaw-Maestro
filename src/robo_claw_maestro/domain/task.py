"""Task model and deterministic state transitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from .event import DomainEvent


class TaskStatus(StrEnum):
    CREATED = "CREATED"
    BLOCKED = "BLOCKED"
    READY = "READY"
    ASSIGNED = "ASSIGNED"
    ACCEPTED = "ACCEPTED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    UNKNOWN = "UNKNOWN"


_TERMINAL = frozenset({TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED})
_ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.CREATED: frozenset(
        {
            TaskStatus.BLOCKED,
            TaskStatus.READY,
            TaskStatus.CANCEL_REQUESTED,
            TaskStatus.TIMED_OUT,
        }
    ),
    TaskStatus.BLOCKED: frozenset(
        {TaskStatus.READY, TaskStatus.CANCEL_REQUESTED, TaskStatus.TIMED_OUT}
    ),
    TaskStatus.READY: frozenset(
        {TaskStatus.ASSIGNED, TaskStatus.CANCEL_REQUESTED, TaskStatus.TIMED_OUT}
    ),
    TaskStatus.ASSIGNED: frozenset(
        {
            TaskStatus.ACCEPTED,
            TaskStatus.QUEUED,
            TaskStatus.UNKNOWN,
            TaskStatus.CANCEL_REQUESTED,
            TaskStatus.TIMED_OUT,
        }
    ),
    TaskStatus.ACCEPTED: frozenset(
        {
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
            TaskStatus.UNKNOWN,
            TaskStatus.CANCEL_REQUESTED,
            TaskStatus.TIMED_OUT,
        }
    ),
    TaskStatus.QUEUED: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.CANCEL_REQUESTED,
            TaskStatus.UNKNOWN,
            TaskStatus.TIMED_OUT,
        }
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCEL_REQUESTED,
            TaskStatus.TIMED_OUT,
            TaskStatus.UNKNOWN,
        }
    ),
    TaskStatus.CANCEL_REQUESTED: frozenset(
        {TaskStatus.CANCELLED, TaskStatus.UNKNOWN, TaskStatus.FAILED}
    ),
    TaskStatus.TIMED_OUT: frozenset({TaskStatus.UNKNOWN, TaskStatus.READY, TaskStatus.FAILED}),
    TaskStatus.UNKNOWN: frozenset(
        {
            TaskStatus.READY,
            TaskStatus.ASSIGNED,
            TaskStatus.RUNNING,
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCEL_REQUESTED,
        }
    ),
    TaskStatus.SUCCEEDED: frozenset(),
    TaskStatus.FAILED: frozenset({TaskStatus.READY}),
    TaskStatus.CANCELLED: frozenset(),
}


class InvalidTaskTransition(ValueError):
    """Raised when a task state transition is not allowed."""


@dataclass(slots=True)
class Task:
    task_id: str
    mission_id: str
    instruction: str
    task_type: str = "instruction"
    skill_name: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    required_capabilities: set[str] = field(default_factory=set)
    depends_on: set[str] = field(default_factory=set)
    assigned_robot_id: str | None = None
    priority: int = 0
    deadline: datetime | None = None
    timeout_sec: float = 300.0
    max_attempts: int = 1
    attempt_count: int = 0
    attempted_robot_ids: set[str] = field(default_factory=set)
    idempotency_key: str = ""
    status: TaskStatus = TaskStatus.CREATED
    progress: int = 0
    result: dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    error_message: str = ""
    _events: list[DomainEvent] = field(default_factory=list, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("task_id must not be empty")
        if not self.mission_id.strip():
            raise ValueError("mission_id must not be empty")
        if not self.instruction.strip():
            raise ValueError("instruction must not be empty")
        if self.timeout_sec <= 0:
            raise ValueError("timeout_sec must be positive")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if not 0 <= self.progress <= 100:
            raise ValueError("progress must be between 0 and 100")
        if self.task_id in self.depends_on:
            raise ValueError("task must not depend on itself")

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL

    def transition_to(self, new_status: TaskStatus) -> None:
        if new_status == self.status:
            return
        if new_status not in _ALLOWED_TRANSITIONS[self.status]:
            raise InvalidTaskTransition(
                f"invalid task transition: {self.task_id} {self.status} -> {new_status}"
            )
        previous = self.status
        self.status = new_status
        self._events.append(
            DomainEvent.create(
                event_type="task.status_changed",
                aggregate_type="task",
                aggregate_id=self.task_id,
                payload={
                    "mission_id": self.mission_id,
                    "from": previous.value,
                    "to": new_status.value,
                },
                correlation_id=self.mission_id,
            )
        )
        if new_status == TaskStatus.SUCCEEDED:
            self.progress = 100
        if new_status in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            self.assigned_robot_id = self.assigned_robot_id

    def pull_events(self) -> list[DomainEvent]:
        events, self._events = self._events, []
        return events

    def assign(self, robot_id: str) -> None:
        if not robot_id.strip():
            raise ValueError("robot_id must not be empty")
        self.assigned_robot_id = robot_id
        self.transition_to(TaskStatus.ASSIGNED)

    def start_attempt(self) -> None:
        if not self.assigned_robot_id:
            raise ValueError(f"task {self.task_id} must be assigned before execution")
        if self.attempt_count >= self.max_attempts:
            raise ValueError(f"task {self.task_id} exhausted its retry budget")
        self.attempt_count += 1
        if self.status == TaskStatus.READY:
            self.transition_to(TaskStatus.ASSIGNED)
        if self.status == TaskStatus.ASSIGNED:
            self.transition_to(TaskStatus.ACCEPTED)
        if self.status == TaskStatus.ACCEPTED:
            self.transition_to(TaskStatus.RUNNING)

    def update_progress(self, progress: int) -> None:
        if self.status not in (TaskStatus.ACCEPTED, TaskStatus.QUEUED, TaskStatus.RUNNING):
            raise InvalidTaskTransition(f"cannot update progress while task is {self.status}")
        if not 0 <= progress <= 100:
            raise ValueError("progress must be between 0 and 100")
        self.progress = progress

    def can_retry(self) -> bool:
        return self.attempt_count < self.max_attempts and self.status in {
            TaskStatus.FAILED,
            TaskStatus.TIMED_OUT,
            TaskStatus.UNKNOWN,
        }

    def prepare_retry(self) -> None:
        if not self.can_retry():
            raise ValueError(f"task {self.task_id} cannot be retried")
        self.transition_to(TaskStatus.READY)
        self.assigned_robot_id = None
        self.error_code = ""
        self.error_message = ""
        self.progress = 0
