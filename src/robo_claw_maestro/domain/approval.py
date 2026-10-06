"""Human approval model for safety-sensitive commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


@dataclass(slots=True)
class ApprovalRequest:
    approval_id: str
    robot_id: str
    action: str
    payload: dict[str, Any]
    requested_by: str
    reason: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    decided_at: datetime | None = None
    decided_by: str = ""
    result: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        robot_id: str,
        action: str,
        payload: dict[str, Any],
        requested_by: str,
        reason: str,
        expires_at: datetime | None = None,
    ) -> ApprovalRequest:
        if not robot_id or not action or not requested_by or not reason:
            raise ValueError("approval identity and reason are required")
        return cls(
            approval_id=uuid4().hex,
            robot_id=robot_id,
            action=action,
            payload=payload,
            requested_by=requested_by,
            reason=reason,
            expires_at=expires_at,
        )

    def approve(self, actor: str, now: datetime | None = None) -> None:
        self._ensure_pending(now)
        self.status = ApprovalStatus.APPROVED
        self.decided_by = actor
        self.decided_at = now or datetime.now(UTC)

    def reject(self, actor: str, now: datetime | None = None) -> None:
        self._ensure_pending(now)
        self.status = ApprovalStatus.REJECTED
        self.decided_by = actor
        self.decided_at = now or datetime.now(UTC)

    def mark_executed(self, success: bool, result: dict[str, Any]) -> None:
        if self.status is not ApprovalStatus.APPROVED:
            raise ValueError("only approved requests can be executed")
        self.status = ApprovalStatus.EXECUTED if success else ApprovalStatus.FAILED
        self.result = result

    def _ensure_pending(self, now: datetime | None) -> None:
        if self.status is not ApprovalStatus.PENDING:
            raise ValueError(f"approval is not pending: {self.status}")
        current = now or datetime.now(UTC)
        expires = self.expires_at
        if expires is not None:
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            if expires <= current:
                self.status = ApprovalStatus.EXPIRED
                raise ValueError("approval request expired")
