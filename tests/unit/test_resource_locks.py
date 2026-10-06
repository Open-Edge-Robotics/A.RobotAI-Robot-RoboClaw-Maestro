from datetime import UTC, datetime, timedelta

import pytest

from robo_claw_maestro.application.resource_locks import ResourceLockManager

pytestmark = pytest.mark.unit


class FakeClock:
    def __init__(self):
        self.value = datetime(2026, 1, 1, tzinfo=UTC)

    def now(self):
        return self.value


def test_resource_lease_blocks_other_owner_until_expiry():
    clock = FakeClock()
    locks = ResourceLockManager(clock)

    assert locks.acquire_many({"corridor-a"}, "task-1", 10)
    assert not locks.acquire_many({"corridor-a"}, "task-2", 10)

    clock.value += timedelta(seconds=11)

    assert locks.acquire_many({"corridor-a"}, "task-2", 10)
    assert locks.list()[0].owner_id == "task-2"


def test_resource_acquisition_is_atomic():
    locks = ResourceLockManager()
    assert locks.acquire_many({"door-a"}, "task-1", 10)

    assert not locks.acquire_many({"door-a", "charger"}, "task-2", 10)
    assert {lease.resource_id for lease in locks.list()} == {"door-a"}
