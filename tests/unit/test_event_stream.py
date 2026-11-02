"""Behavior of the bounded WebSocket event feed."""

from datetime import UTC, datetime, timedelta

import pytest

from robo_claw_maestro.adapters.database.in_memory import InMemoryEventRepository
from robo_claw_maestro.api.event_stream import EventStreamFeed
from robo_claw_maestro.domain.event import DomainEvent

pytestmark = pytest.mark.unit

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def make_event(index: int, *, aggregate_id: str = "robot-a", milliseconds: int | None = None):
    occurred_at = BASE + timedelta(milliseconds=index if milliseconds is None else milliseconds)
    return DomainEvent(
        event_id=f"e{index:05d}",
        event_type="robot.telemetry_updated",
        aggregate_type="robot",
        aggregate_id=aggregate_id,
        occurred_at=occurred_at,
        payload={"index": index},
    )


def fill(repository: InMemoryEventRepository, *events: DomainEvent) -> InMemoryEventRepository:
    for event in events:
        repository.append(event)
    return repository


def test_replay_returns_only_the_newest_events():
    repository = fill(InMemoryEventRepository(), *[make_event(index) for index in range(10)])

    feed = EventStreamFeed([repository], replay_limit=3)

    assert [event.event_id for event in feed.replay()] == ["e00007", "e00008", "e00009"]


def test_poll_returns_only_events_recorded_after_the_replay():
    repository = fill(InMemoryEventRepository(), *[make_event(index) for index in range(10)])
    feed = EventStreamFeed([repository], replay_limit=2)

    feed.replay()
    repository.append(make_event(10))

    assert [event.event_id for event in feed.poll()] == ["e00010"]
    assert feed.poll() == []


def test_replay_limit_of_zero_streams_new_events_without_history():
    repository = fill(InMemoryEventRepository(), *[make_event(index) for index in range(5)])
    feed = EventStreamFeed([repository], replay_limit=0)

    assert feed.replay() == []

    repository.append(make_event(5))

    assert [event.event_id for event in feed.poll()] == ["e00005"]


def test_empty_replay_streams_events_recorded_after_connect():
    repository = InMemoryEventRepository()
    feed = EventStreamFeed([repository], replay_limit=5)

    assert feed.replay() == []

    for index in range(4):
        repository.append(make_event(index))

    assert [event.event_id for event in feed.poll()] == [
        "e00000",
        "e00001",
        "e00002",
        "e00003",
    ]


def test_shared_repository_is_read_once():
    repository = fill(InMemoryEventRepository(), *[make_event(index) for index in range(3)])

    feed = EventStreamFeed([repository, repository], replay_limit=10)

    assert [event.event_id for event in feed.replay()] == ["e00000", "e00001", "e00002"]


def test_events_from_multiple_repositories_are_merged_in_order():
    first = fill(InMemoryEventRepository(), make_event(0), make_event(2))
    second = fill(InMemoryEventRepository(), make_event(1), make_event(3))

    feed = EventStreamFeed([first, second], replay_limit=3)

    assert [event.event_id for event in feed.replay()] == ["e00001", "e00002", "e00003"]


def test_events_sharing_a_timestamp_are_delivered_once():
    same_moment = [
        make_event(0, milliseconds=500),
        make_event(1, milliseconds=500),
        make_event(2, milliseconds=500),
    ]
    repository = fill(InMemoryEventRepository(), *same_moment)
    feed = EventStreamFeed([repository], replay_limit=1)

    replay = feed.replay()
    polled = feed.poll()

    assert [event.event_id for event in replay] == ["e00002"]
    assert polled == []


def test_repository_without_incremental_reads_still_works():
    class PlainRepository:
        def __init__(self) -> None:
            self.events: list[DomainEvent] = []

        def list(self, aggregate_id: str | None = None) -> list[DomainEvent]:
            return list(self.events)

    repository = PlainRepository()
    repository.events.extend(make_event(index) for index in range(4))
    feed = EventStreamFeed([repository], replay_limit=2)

    assert [event.event_id for event in feed.replay()] == ["e00002", "e00003"]

    repository.events.append(make_event(4))

    assert [event.event_id for event in feed.poll()] == ["e00004"]


def test_negative_replay_limit_is_rejected():
    with pytest.raises(ValueError, match="replay_limit"):
        EventStreamFeed([InMemoryEventRepository()], replay_limit=-1)
