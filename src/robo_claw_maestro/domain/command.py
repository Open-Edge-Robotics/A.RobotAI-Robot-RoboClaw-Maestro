"""Remote command identity and delivery state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4


class CommandStatus(StrEnum):
    CREATED = "CREATED"
    SENT = "SENT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class Command:
    command_id: str
    mission_id: str
    task_id: str
    robot_id: str
    idempotency_key: str
    instruction: str
    skill_name: str = ""
    params: dict[str, object] | None = None
    attempt_id: str = ""
    correlation_id: str = ""
    deadline_epoch_ms: int | None = None
    issued_at: datetime | None = None
    status: CommandStatus = CommandStatus.CREATED

    @classmethod
    def create(
        cls,
        *,
        mission_id: str,
        task_id: str,
        robot_id: str,
        instruction: str,
        attempt: int,
        skill_name: str = "",
        params: dict[str, object] | None = None,
        deadline_epoch_ms: int | None = None,
    ) -> Command:
        if not instruction.strip():
            raise ValueError("instruction must not be empty")
        if attempt < 1:
            raise ValueError("attempt must be at least 1")
        idempotency_key = f"{mission_id}:{task_id}:{robot_id}:{attempt}"
        return cls(
            command_id=uuid4().hex,
            mission_id=mission_id,
            task_id=task_id,
            robot_id=robot_id,
            idempotency_key=idempotency_key,
            instruction=instruction,
            skill_name=skill_name,
            params=params,
            attempt_id=f"{task_id}-attempt-{attempt}",
            correlation_id=mission_id,
            deadline_epoch_ms=deadline_epoch_ms,
            issued_at=datetime.now(UTC),
        )
