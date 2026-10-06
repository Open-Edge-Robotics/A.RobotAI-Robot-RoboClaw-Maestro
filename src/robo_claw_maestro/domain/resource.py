"""Shared resource lease domain value."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ResourceLease:
    resource_id: str
    owner_id: str
    expires_at: datetime
