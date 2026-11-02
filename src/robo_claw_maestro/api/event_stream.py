"""Bounded, incremental event feed backing the WebSocket event stream.

A dashboard client must not receive the whole audit history on every
(re)connect: a long running fleet accumulates tens of thousands of telemetry
events and replaying them saturates both the API and the browser. The feed
replays a bounded tail once and then polls only events newer than a watermark.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Protocol

from robo_claw_maestro.domain.event import DomainEvent

Watermark = tuple[datetime, str]


class EventSource(Protocol):
    """Minimal event repository surface used by the feed."""

    def list(self, aggregate_id: str | None = None) -> list[DomainEvent]: ...


def event_key(event: DomainEvent) -> Watermark:
    return (event.occurred_at, event.event_id)


def _ordered(events: Iterable[DomainEvent]) -> list[DomainEvent]:
    return sorted(events, key=lambda item: (item.occurred_at, item.event_id))


def _unique_sources(sources: Sequence[EventSource]) -> tuple[EventSource, ...]:
    unique: list[EventSource] = []
    for source in sources:
        if all(source is not existing for existing in unique):
            unique.append(source)
    return tuple(unique)


class EventStreamFeed:
    """Deliver a bounded tail then only newer events, without unbounded state.

    ``replay_limit`` events (the newest ones) are delivered on :meth:`replay`.
    Every later :meth:`poll` returns just the events recorded after the
    delivered tail, so reconnects stay cheap regardless of history size.
    """

    def __init__(self, sources: Sequence[EventSource], *, replay_limit: int = 200) -> None:
        if replay_limit < 0:
            raise ValueError("replay_limit must not be negative")
        self._sources = _unique_sources(sources)
        self._replay_limit = replay_limit
        self._watermark: Watermark | None = None
        self._delivered: set[str] = set()

    def replay(self) -> list[DomainEvent]:
        if self._replay_limit == 0:
            self._prime()
            return []
        tail = self._tail()
        if tail:
            self._delivered = {event.event_id for event in tail}
            self._watermark = event_key(tail[0])
        else:
            self._prime()
        return tail

    def poll(self) -> list[DomainEvent]:
        pending: list[DomainEvent] = []
        for source in self._sources:
            pending.extend(self._read(source, self._watermark))
        fresh = _ordered(event for event in pending if event.event_id not in self._delivered)
        if not fresh:
            return []
        self._advance(fresh)
        self._delivered.clear()
        return fresh

    def _tail(self) -> list[DomainEvent]:
        collected: list[DomainEvent] = []
        for source in self._sources:
            collected.extend(self._read(source, None, limit=self._replay_limit))
        return _ordered(collected)[-self._replay_limit :]

    def _prime(self) -> None:
        """Anchor the watermark at the newest stored event without delivering it."""
        newest = self._read_source_newest()
        if newest is not None:
            self._watermark = event_key(newest)

    def _read_source_newest(self) -> DomainEvent | None:
        newest: DomainEvent | None = None
        for source in self._sources:
            events = self._read(source, None, limit=1)
            if events and (newest is None or event_key(events[-1]) > event_key(newest)):
                newest = events[-1]
        return newest

    def _read(
        self, source: EventSource, since: Watermark | None, *, limit: int | None = None
    ) -> list[DomainEvent]:
        reader = getattr(source, "events_after", None)
        if reader is not None:
            return list(reader(since, limit=limit))
        events = [event for event in source.list() if since is None or event_key(event) > since]
        return events[-limit:] if limit is not None else events

    def _advance(self, events: Sequence[DomainEvent]) -> None:
        newest = event_key(events[-1])
        if self._watermark is None or newest > self._watermark:
            self._watermark = newest
