"""Structured domain events used for audit and reconciliation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class DomainEvent:
    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    occurred_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)
    actor: str = "system"
    correlation_id: str = ""

    @classmethod
    def create(
        cls,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any] | None = None,
        actor: str = "system",
        correlation_id: str = "",
    ) -> DomainEvent:
        for name, value in (
            ("event_type", event_type),
            ("aggregate_type", aggregate_type),
            ("aggregate_id", aggregate_id),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be empty")
        return cls(
            event_id=uuid4().hex,
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            occurred_at=datetime.now(UTC),
            payload=payload or {},
            actor=actor,
            correlation_id=correlation_id,
        )
