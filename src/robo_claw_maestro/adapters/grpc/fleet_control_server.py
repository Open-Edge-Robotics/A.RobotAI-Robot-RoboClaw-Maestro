"""FleetControl bidirectional stream server and RobotGateway adapter."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import grpc

from robo_claw_maestro.application.robot_registry import RobotRegistry
from robo_claw_maestro.domain.robot import (
    Capability,
    Robot,
    RobotConnectionState,
    RobotOperationalState,
)
from robo_claw_maestro.ports.robot_gateway import (
    RemoteTaskResult,
    RobotDescriptor,
    RobotGatewayError,
    RobotSnapshot,
    RobotStatusSnapshot,
    RobotUnavailable,
)
from robo_claw_maestro.telemetry.tracing import process_inputs, process_outputs, traceable

from .generated import fleet_control_pb2 as pb2
from .generated import fleet_control_pb2_grpc as pb2_grpc

PROTOCOL_VERSION = "1"
_TERMINAL_UPDATES = {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}


@dataclass(slots=True)
class FleetSession:
    robot_id: str
    session_id: str
    descriptor: RobotDescriptor
    outbound: asyncio.Queue[Any]
    latest_status: RobotStatusSnapshot
    pending: dict[str, asyncio.Future[RemoteTaskResult]] = field(default_factory=dict)
    subscribers: set[asyncio.Queue[RobotStatusSnapshot]] = field(default_factory=set)


class FleetControlHub(pb2_grpc.FleetControlServicer):
    def __init__(
        self,
        registry: RobotRegistry | None = None,
        *,
        queue_limit: int = 128,
        command_timeout_sec: float = 300.0,
        require_peer_identity: bool = False,
    ) -> None:
        if queue_limit < 1 or command_timeout_sec <= 0:
            raise ValueError("invalid FleetControl limits")
        self._registry = registry
        self._queue_limit = queue_limit
        self._command_timeout = command_timeout_sec
        self._sessions: dict[str, FleetSession] = {}
        self.require_peer_identity = require_peer_identity
        self._lock = asyncio.Lock()

    def attach_registry(self, registry: RobotRegistry) -> None:
        self._registry = registry

    def connected(self, robot_id: str) -> bool:
        return robot_id in self._sessions

    async def Connect(self, request_iterator, context):
        try:
            first = await anext(request_iterator)
        except StopAsyncIteration:
            return
        if first.protocol_version != PROTOCOL_VERSION or not first.HasField("register"):
            yield pb2.MaestroToRobot(
                protocol_version=PROTOCOL_VERSION,
                registration_result=pb2.RegistrationResult(
                    accepted=False, message="unsupported protocol or missing registration"
                ),
            )
            return
        descriptor = first.register.descriptor
        peer_identities = context.peer_identities() or []
        identity_matches = first.robot_id.encode() in peer_identities
        if self.require_peer_identity and not identity_matches:
            yield pb2.MaestroToRobot(
                protocol_version=PROTOCOL_VERSION,
                registration_result=pb2.RegistrationResult(
                    accepted=False, message="client certificate identity mismatch"
                ),
            )
            return
        if not descriptor.robot_id or descriptor.robot_id != first.robot_id:
            yield pb2.MaestroToRobot(
                protocol_version=PROTOCOL_VERSION,
                registration_result=pb2.RegistrationResult(
                    accepted=False, message="robot identity mismatch"
                ),
            )
            return
        session = FleetSession(
            robot_id=first.robot_id,
            session_id=first.session_id,
            descriptor=RobotDescriptor(
                robot_id=descriptor.robot_id,
                display_name=descriptor.display_name or descriptor.robot_id,
                robot_type=descriptor.robot_type or "unknown",
            ),
            outbound=asyncio.Queue(maxsize=self._queue_limit),
            latest_status=RobotStatusSnapshot(robot_id=first.robot_id),
        )
        async with self._lock:
            previous = self._sessions.get(session.robot_id)
            if previous is not None:
                self._fail_pending(previous, "robot opened a replacement session")
            self._sessions[session.robot_id] = session
        self._register_robot(first, descriptor)
        yield pb2.MaestroToRobot(
            protocol_version=PROTOCOL_VERSION,
            registration_result=pb2.RegistrationResult(accepted=True, message="registered"),
        )
        consumer = asyncio.create_task(self._consume(session, request_iterator))
        try:
            while not context.done():
                if consumer.done() and session.outbound.empty():
                    break
                try:
                    command = await asyncio.wait_for(session.outbound.get(), timeout=0.5)
                except TimeoutError:
                    continue
                yield command
        finally:
            consumer.cancel()
            removed_current = False
            async with self._lock:
                if self._sessions.get(session.robot_id) is session:
                    del self._sessions[session.robot_id]
                    removed_current = True
            self._fail_pending(session, "robot stream disconnected")
            if removed_current and self._registry is not None:
                self._registry.mark_offline(session.robot_id)

    async def _consume(self, session: FleetSession, requests) -> None:
        async for message in requests:
            if message.robot_id != session.robot_id or message.session_id != session.session_id:
                continue
            if message.HasField("telemetry"):
                snapshot = self._snapshot(session.robot_id, message.telemetry)
                session.latest_status = snapshot
                if self._registry is not None:
                    self._registry.apply_snapshot(snapshot)
                for subscriber in tuple(session.subscribers):
                    if subscriber.full():
                        subscriber.get_nowait()
                    subscriber.put_nowait(snapshot)
            elif message.HasField("heartbeat"):
                if self._registry is not None:
                    observed = (
                        datetime.fromtimestamp(message.heartbeat.timestamp_ms / 1000, tz=UTC)
                        if message.heartbeat.timestamp_ms
                        else datetime.now(UTC)
                    )
                    self._registry.heartbeat(session.robot_id, observed)
            elif message.HasField("command_ack"):
                ack = message.command_ack
                if not ack.accepted:
                    self._resolve(
                        session,
                        ack.command_id,
                        RemoteTaskResult(False, ack.message, {"error_code": ack.error_code}),
                    )
            elif message.HasField("task_update"):
                update = message.task_update
                if update.status in _TERMINAL_UPDATES:
                    try:
                        result = json.loads(update.result_json) if update.result_json else {}
                    except json.JSONDecodeError:
                        result = {"raw_result": update.result_json}
                    if update.error_code:
                        result["error_code"] = update.error_code
                    self._resolve(
                        session,
                        update.command_id,
                        RemoteTaskResult(
                            update.status == "SUCCEEDED",
                            update.message,
                            result,
                            remote_task_id=update.task_id,
                        ),
                    )

    def _register_robot(self, message, descriptor) -> None:
        if self._registry is None:
            return
        try:
            robot = self._registry.get(message.robot_id)
        except KeyError:
            robot = Robot(
                robot_id=message.robot_id,
                display_name=descriptor.display_name or message.robot_id,
                robot_type=descriptor.robot_type or "unknown",
                software_version=descriptor.software_version,
                protocol_version=message.protocol_version,
                site_id=descriptor.site_id,
                map_id=descriptor.map_id,
                map_version=descriptor.map_version,
                frame_id=descriptor.frame_id or "map",
            )
            self._registry.register(robot)
        robot.session_id = message.session_id
        robot.site_id = descriptor.site_id
        robot.map_id = descriptor.map_id
        robot.map_version = descriptor.map_version
        robot.frame_id = descriptor.frame_id or "map"
        robot.capabilities = {
            item.name: Capability(
                name=item.name,
                version=item.version or "1.0",
                risk_level=item.risk_level or "normal",
                requirements=tuple(item.requirements),
                input_schema=json.loads(item.input_schema_json or "{}"),
            )
            for item in descriptor.capabilities
        }
        robot.connection_state = RobotConnectionState.ONLINE
        self._registry.save(robot)

    @staticmethod
    def _snapshot(robot_id: str, telemetry) -> RobotStatusSnapshot:
        try:
            operational = RobotOperationalState(telemetry.operational_state or "UNKNOWN")
        except ValueError:
            operational = RobotOperationalState.UNKNOWN
        observed = (
            datetime.fromtimestamp(telemetry.observed_at_ms / 1000, tz=UTC)
            if telemetry.observed_at_ms
            else datetime.now(UTC)
        )
        return RobotStatusSnapshot(
            robot_id=robot_id,
            connection_state=RobotConnectionState.ONLINE,
            operational_state=operational,
            battery_percentage=telemetry.battery_percentage,
            pose={
                "map_id": telemetry.map_id,
                "frame_id": telemetry.frame_id,
                "x": telemetry.x,
                "y": telemetry.y,
                "z": telemetry.z,
                "yaw": telemetry.yaw,
            },
            current_task_id=telemetry.current_task_id or None,
            current_skill=telemetry.current_skill,
            queue_depth=telemetry.queue_depth,
            health_issues=tuple(telemetry.health_issues),
            observed_at=observed,
        )

    def _session(self, robot_id: str) -> FleetSession:
        session = self._sessions.get(robot_id)
        if session is None:
            raise RobotUnavailable(f"FleetControl robot is offline: {robot_id}")
        return session

    async def _command(
        self, robot_id: str, command, timeout_sec: float | None = None
    ) -> RemoteTaskResult:
        session = self._session(robot_id)
        command_id = command.command_id
        future = asyncio.get_running_loop().create_future()
        session.pending[command_id] = future
        try:
            session.outbound.put_nowait(command)
        except asyncio.QueueFull as exc:
            session.pending.pop(command_id, None)
            raise RobotUnavailable(f"command queue full for robot {robot_id}") from exc
        try:
            return await asyncio.wait_for(future, timeout=timeout_sec or self._command_timeout)
        except TimeoutError as exc:
            raise RobotGatewayError(f"FleetControl command timeout: {command_id}") from exc
        finally:
            session.pending.pop(command_id, None)

    @staticmethod
    def _resolve(session: FleetSession, command_id: str, result: RemoteTaskResult) -> None:
        future = session.pending.get(command_id)
        if future is not None and not future.done():
            future.set_result(result)

    @staticmethod
    def _fail_pending(session: FleetSession, message: str) -> None:
        for future in session.pending.values():
            if not future.done():
                future.set_exception(RobotUnavailable(message))

    async def ping(self, robot_id: str) -> bool:
        return robot_id in self._sessions

    async def get_info(self, robot_id: str) -> RobotDescriptor:
        return self._session(robot_id).descriptor

    async def get_status(self, robot_id: str) -> RobotStatusSnapshot:
        return self._session(robot_id).latest_status

    async def stream_status(self, robot_id: str) -> AsyncIterator[RobotStatusSnapshot]:
        session = self._session(robot_id)
        queue: asyncio.Queue[RobotStatusSnapshot] = asyncio.Queue(maxsize=8)
        session.subscribers.add(queue)
        try:
            yield session.latest_status
            while self._sessions.get(robot_id) is session:
                yield await queue.get()
        finally:
            session.subscribers.discard(queue)

    @traceable(
        run_type="tool",
        name="fleet_control.execute_task",
        tags=["transport:fleet-control"],
        process_inputs=process_inputs,
        process_outputs=process_outputs,
    )
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
        command_id = uuid4().hex
        return await self._command(
            robot_id,
            pb2.MaestroToRobot(
                protocol_version=PROTOCOL_VERSION,
                command_id=command_id,
                execute_task=pb2.ExecuteTaskCommand(
                    mission_id=mission_id,
                    task_id=task_id,
                    attempt_id=attempt_id or f"{task_id}-attempt",
                    correlation_id=correlation_id or mission_id or task_id,
                    idempotency_key=idempotency_key or task_id,
                    instruction=instruction,
                    deadline_ms=int((datetime.now(UTC).timestamp() + timeout_sec) * 1000),
                ),
            ),
            timeout_sec,
        )

    @traceable(
        run_type="tool",
        name="fleet_control.execute_skill",
        tags=["transport:fleet-control"],
        process_inputs=process_inputs,
        process_outputs=process_outputs,
    )
    async def execute_skill(
        self,
        robot_id: str,
        *,
        skill_name: str,
        params: dict[str, Any],
        timeout_sec: float,
    ) -> RemoteTaskResult:
        command_id = uuid4().hex
        return await self._command(
            robot_id,
            pb2.MaestroToRobot(
                protocol_version=PROTOCOL_VERSION,
                command_id=command_id,
                execute_skill=pb2.ExecuteSkillCommand(
                    task_id=f"skill:{skill_name}",
                    attempt_id=command_id,
                    correlation_id=command_id,
                    idempotency_key=command_id,
                    skill_name=skill_name,
                    params_json=json.dumps(params),
                    deadline_ms=int((datetime.now(UTC).timestamp() + timeout_sec) * 1000),
                ),
            ),
            timeout_sec,
        )

    async def request_snapshot(self, robot_id: str) -> RobotSnapshot:
        command_id = uuid4().hex
        result = await self._command(
            robot_id,
            pb2.MaestroToRobot(
                protocol_version=PROTOCOL_VERSION,
                command_id=command_id,
                request_snapshot=pb2.RequestSnapshotCommand(image_type="compressed"),
            ),
            min(self._command_timeout, 30.0),
        )
        return RobotSnapshot(
            robot_id=robot_id,
            image_base64=str(result.result.get("image_base64", "")),
            image_format=str(result.result.get("image_format", "image/jpeg")),
            timestamp_ms=int(result.result.get("timestamp_ms", 0)),
        )

    async def emergency_stop(self, robot_id: str) -> RemoteTaskResult:
        command_id = uuid4().hex
        return await self._command(
            robot_id,
            pb2.MaestroToRobot(
                protocol_version=PROTOCOL_VERSION,
                command_id=command_id,
                emergency_stop=pb2.EmergencyStopCommand(reason="fleet emergency stop"),
            ),
            min(self._command_timeout, 10.0),
        )


class FleetPreferredGateway:
    """Use an outbound FleetControl session when present, otherwise use RosGrpc."""

    def __init__(self, fleet: FleetControlHub, fallback) -> None:
        self._fleet = fleet
        self._fallback = fallback

    def _gateway(self, robot_id: str):
        return self._fleet if self._fleet.connected(robot_id) else self._fallback

    async def ping(self, robot_id: str) -> bool:
        return await self._gateway(robot_id).ping(robot_id)

    async def get_info(self, robot_id: str) -> RobotDescriptor:
        return await self._gateway(robot_id).get_info(robot_id)

    async def get_status(self, robot_id: str) -> RobotStatusSnapshot:
        return await self._gateway(robot_id).get_status(robot_id)

    async def stream_status(self, robot_id: str) -> AsyncIterator[RobotStatusSnapshot]:
        async for snapshot in self._gateway(robot_id).stream_status(robot_id):
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
        return await self._gateway(robot_id).execute_task(
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
        return await self._gateway(robot_id).execute_skill(
            robot_id,
            skill_name=skill_name,
            params=params,
            timeout_sec=timeout_sec,
        )

    async def request_snapshot(self, robot_id: str) -> RobotSnapshot:
        return await self._gateway(robot_id).request_snapshot(robot_id)

    async def emergency_stop(self, robot_id: str) -> RemoteTaskResult:
        return await self._gateway(robot_id).emergency_stop(robot_id)

    async def close(self) -> None:
        close = getattr(self._fallback, "close", None)
        if close is not None:
            await close()


class FleetControlServer:
    def __init__(
        self,
        hub: FleetControlHub,
        *,
        host: str = "0.0.0.0",
        port: int = 50053,
        certificate_path: str = "",
        private_key_path: str = "",
        client_ca_path: str = "",
    ) -> None:
        self.hub = hub
        self._host = host
        self.address = f"{host}:{port}"
        self._certificate_path = certificate_path
        self._private_key_path = private_key_path
        self._client_ca_path = client_ca_path
        self._server: grpc.aio.Server | None = None

    async def start(self) -> None:
        self._server = grpc.aio.server()
        pb2_grpc.add_FleetControlServicer_to_server(self.hub, self._server)
        if self._certificate_path and self._private_key_path:
            certificate = Path(self._certificate_path).read_bytes()
            private_key = Path(self._private_key_path).read_bytes()
            root = Path(self._client_ca_path).read_bytes() if self._client_ca_path else None
            self.hub.require_peer_identity = bool(root)
            credentials = grpc.ssl_server_credentials(
                [(private_key, certificate)],
                root_certificates=root,
                require_client_auth=bool(root),
            )
            bound_port = self._server.add_secure_port(self.address, credentials)
        else:
            bound_port = self._server.add_insecure_port(self.address)
        if bound_port == 0:
            raise RuntimeError(f"failed to bind FleetControl server: {self.address}")
        self.address = f"{self._host}:{bound_port}"
        await self._server.start()

    async def stop(self) -> None:
        if self._server is not None:
            await self._server.stop(grace=1)
            self._server = None
