"""Deterministic in-process resource lease manager."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

from robo_claw_maestro.domain.resource import ResourceLease


class ResourceLocks(Protocol):
    def acquire_many(self, resources: set[str], owner_id: str, lease_sec: float) -> bool: ...
    def release_owner(self, owner_id: str) -> None: ...
    def cleanup_expired(self) -> int: ...
    def list(self) -> list[ResourceLease]: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class ResourceLockManager:
    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()
        self._leases: dict[str, ResourceLease] = {}

    def acquire_many(self, resources: set[str], owner_id: str, lease_sec: float) -> bool:
        if lease_sec <= 0:
            raise ValueError("lease_sec must be positive")
        self.cleanup_expired()
        if any(
            resource in self._leases and self._leases[resource].owner_id != owner_id
            for resource in resources
        ):
            return False
        expires_at = self._clock.now() + timedelta(seconds=lease_sec)
        for resource in resources:
            self._leases[resource] = ResourceLease(resource, owner_id, expires_at)
        return True

    def release_owner(self, owner_id: str) -> None:
        self._leases = {
            resource: lease
            for resource, lease in self._leases.items()
            if lease.owner_id != owner_id
        }

    def cleanup_expired(self) -> int:
        now = self._clock.now()
        expired = [resource for resource, lease in self._leases.items() if lease.expires_at <= now]
        for resource in expired:
            del self._leases[resource]
        return len(expired)

    def list(self) -> list[ResourceLease]:
        self.cleanup_expired()
        return sorted(self._leases.values(), key=lambda lease: lease.resource_id)
