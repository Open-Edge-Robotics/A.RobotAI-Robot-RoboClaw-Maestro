"""Route gateway operations to one lazily-created RosGrpc connection per robot."""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from typing import Any

from robo_claw_maestro.ports.robot_gateway import (
    RemoteTaskResult,
    RobotDescriptor,
    RobotGateway,
    RobotSnapshot,
    RobotStatusSnapshot,
    RobotUnavailable,
)

from .ros_grpc_client import RosGrpcRobotGateway


class LazyRosGrpcRobotGateway:
    """Create grpc.aio resources inside the running application event loop."""

    def __init__(self, endpoint: str, default_timeout_sec: float) -> None:
        self._endpoint = endpoint
        self._timeout = default_timeout_sec
        self._channel: Any = None
        self._delegate: RosGrpcRobotGateway | None = None

    def _client(self) -> RosGrpcRobotGateway:
        if self._delegate is None:
            grpc = importlib.import_module("grpc")
            messages = importlib.import_module("robo_claw_maestro.adapters.grpc.generated.robo_pb2")
            services = importlib.import_module(
                "robo_claw_maestro.adapters.grpc.generated.robo_pb2_grpc"
            )
            self._channel = grpc.aio.insecure_channel(self._endpoint)
            self._delegate = RosGrpcRobotGateway(
                services.RosGrpcStub(self._channel),
                messages,
                default_timeout_sec=self._timeout,
            )
        return self._delegate

    async def ping(self, robot_id: str) -> bool:
        return await self._client().ping(robot_id)

    async def get_info(self, robot_id: str) -> RobotDescriptor:
        return await self._client().get_info(robot_id)

    async def get_status(self, robot_id: str) -> RobotStatusSnapshot:
        return await self._client().get_status(robot_id)

    async def stream_status(self, robot_id: str) -> AsyncIterator[RobotStatusSnapshot]:
        async for snapshot in self._client().stream_status(robot_id):
            yield snapshot

    async def execute_task(
        self,
        robot_id: str,
        *,
        instruction: str,
        task_id: str,
        timeout_sec: float,
        idempotency_key: str = "",
        mission_id: str = "",
        attempt_id: str = "",
        correlation_id: str = "",
    ) -> RemoteTaskResult:
        return await self._client().execute_task(
            robot_id,
            instruction=instruction,
            task_id=task_id,
            timeout_sec=timeout_sec,
            idempotency_key=idempotency_key,
            mission_id=mission_id,
            attempt_id=attempt_id,
            correlation_id=correlation_id,
        )

    async def execute_skill(
        self,
        robot_id: str,
        *,
        skill_name: str,
        params: dict[str, Any],
        timeout_sec: float,
    ) -> RemoteTaskResult:
        return await self._client().execute_skill(
            robot_id,
            skill_name=skill_name,
            params=params,
            timeout_sec=timeout_sec,
        )

    async def request_snapshot(self, robot_id: str) -> RobotSnapshot:
        return await self._client().request_snapshot(robot_id)

    async def emergency_stop(self, robot_id: str) -> RemoteTaskResult:
        return await self._client().emergency_stop(robot_id)

    async def close(self) -> None:
        if self._channel is not None:
            await self._channel.close()
            self._channel = None
            self._delegate = None


class MultiRobotGateway:
    def __init__(self, gateways: dict[str, RobotGateway] | None = None) -> None:
        self._gateways = dict(gateways or {})

    def add(self, robot_id: str, gateway: RobotGateway) -> None:
        if robot_id in self._gateways:
            raise ValueError(f"duplicate robot gateway: {robot_id}")
        self._gateways[robot_id] = gateway

    def _get(self, robot_id: str) -> RobotGateway:
        try:
            return self._gateways[robot_id]
        except KeyError as exc:
            raise RobotUnavailable(f"robot endpoint is not configured: {robot_id}") from exc

    async def ping(self, robot_id: str) -> bool:
        try:
            return await self._get(robot_id).ping(robot_id)
        except RobotUnavailable:
            return False

    async def get_info(self, robot_id: str) -> RobotDescriptor:
        return await self._get(robot_id).get_info(robot_id)

    async def get_status(self, robot_id: str) -> RobotStatusSnapshot:
        return await self._get(robot_id).get_status(robot_id)

    async def stream_status(self, robot_id: str) -> AsyncIterator[RobotStatusSnapshot]:
        async for snapshot in self._get(robot_id).stream_status(robot_id):
            yield snapshot

    async def execute_task(
        self,
        robot_id: str,
        *,
        instruction: str,
        task_id: str,
        timeout_sec: float,
        idempotency_key: str = "",
        mission_id: str = "",
        attempt_id: str = "",
        correlation_id: str = "",
    ) -> RemoteTaskResult:
        return await self._get(robot_id).execute_task(
            robot_id,
            instruction=instruction,
            task_id=task_id,
            timeout_sec=timeout_sec,
            idempotency_key=idempotency_key,
            mission_id=mission_id,
            attempt_id=attempt_id,
            correlation_id=correlation_id,
        )

    async def execute_skill(
        self,
        robot_id: str,
        *,
        skill_name: str,
        params: dict[str, Any],
        timeout_sec: float,
    ) -> RemoteTaskResult:
        return await self._get(robot_id).execute_skill(
            robot_id,
            skill_name=skill_name,
            params=params,
            timeout_sec=timeout_sec,
        )

    async def request_snapshot(self, robot_id: str) -> RobotSnapshot:
        return await self._get(robot_id).request_snapshot(robot_id)

    async def emergency_stop(self, robot_id: str) -> RemoteTaskResult:
        return await self._get(robot_id).emergency_stop(robot_id)

    async def close(self) -> None:
        for gateway in self._gateways.values():
            close = getattr(gateway, "close", None)
            if close is not None:
                await close()

    @classmethod
    def from_endpoints(
        cls,
        endpoints: dict[str, str],
        *,
        default_timeout_sec: float = 30.0,
    ) -> MultiRobotGateway:
        return cls(
            {
                robot_id: LazyRosGrpcRobotGateway(endpoint, default_timeout_sec)
                for robot_id, endpoint in endpoints.items()
            }
        )
