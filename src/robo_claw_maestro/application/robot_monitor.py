"""Long-running status stream collection with bounded exponential backoff."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from robo_claw_maestro.ports.robot_gateway import RobotGateway
from robo_claw_maestro.telemetry.metrics import MaestroMetrics

from .robot_registry import RobotRegistry

logger = logging.getLogger(__name__)


class RobotMonitor:
    def __init__(
        self,
        registry: RobotRegistry,
        gateway: RobotGateway,
        *,
        initial_backoff_sec: float = 1.0,
        max_backoff_sec: float = 30.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        metrics: MaestroMetrics | None = None,
    ) -> None:
        if initial_backoff_sec <= 0 or max_backoff_sec < initial_backoff_sec:
            raise ValueError("invalid reconnect backoff range")
        self._registry = registry
        self._gateway = gateway
        self._initial_backoff = initial_backoff_sec
        self._max_backoff = max_backoff_sec
        self._sleep = sleep
        self._metrics = metrics

    async def run(self, robot_id: str, stop: asyncio.Event) -> None:
        """Collect one robot stream until stop is requested.

        A stream ending or raising marks the robot offline. Reconnection delay is
        reset after any valid telemetry and grows exponentially after failures.
        """
        delay = self._initial_backoff
        while not stop.is_set():
            received = False
            try:
                async for snapshot in self._gateway.stream_status(robot_id):
                    self._registry.apply_snapshot(snapshot)
                    received = True
                    delay = self._initial_backoff
                    if stop.is_set():
                        return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("robot status stream failed: %s", robot_id)
            if self._metrics is not None:
                self._metrics.grpc_reconnects.labels(robot_id=robot_id).inc()
            self._registry.mark_offline(robot_id)
            if stop.is_set():
                return
            await self._sleep(delay)
            delay = self._initial_backoff if received else min(delay * 2, self._max_backoff)

    async def close(self) -> None:
        close = getattr(self._gateway, "close", None)
        if close is not None:
            await close()

    async def run_fleet(self, stop: asyncio.Event) -> None:
        async with asyncio.TaskGroup() as group:
            for robot in self._registry.list():
                group.create_task(self.run(robot.robot_id, stop))
