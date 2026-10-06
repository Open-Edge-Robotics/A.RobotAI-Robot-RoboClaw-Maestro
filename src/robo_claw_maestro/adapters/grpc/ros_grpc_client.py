"""Adapter for Robo-Claw's existing ``RosGrpc`` service.

Generated protobuf modules are deliberately injected into the constructor. This
keeps the Maestro domain and tests independent from a ROS workspace while still
allowing deployment to use ``robo_claw_grpc.robo_pb2`` and its stub.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from robo_claw_maestro.domain.robot import RobotConnectionState, RobotOperationalState
from robo_claw_maestro.ports.robot_gateway import (
    RemoteTaskResult,
    RobotDescriptor,
    RobotGatewayError,
    RobotSnapshot,
    RobotStatusSnapshot,
    RobotUnavailable,
)
from robo_claw_maestro.telemetry.tracing import process_inputs, process_outputs, traceable


class RosGrpcRobotGateway:
    """Async client adapter around one generated ``RosGrpcStub`` instance."""

    def __init__(self, stub: Any, messages: Any, *, default_timeout_sec: float = 30.0) -> None:
        if default_timeout_sec <= 0:
            raise ValueError("default_timeout_sec must be positive")
        self._stub = stub
        self._messages = messages
        self._default_timeout = default_timeout_sec

    async def ping(self, robot_id: str) -> bool:
        del robot_id
        try:
            response = await self._stub.Ping(
                self._messages.PingRequest(message="maestro-ping"),
                timeout=self._default_timeout,
            )
            return bool(getattr(response, "message", ""))
        except Exception:
            return False

    async def get_info(self, robot_id: str) -> RobotDescriptor:
        try:
            response = await self._stub.GetRobotInfo(
                self._messages.EmptyRequest(), timeout=self._default_timeout
            )
            returned_id = str(getattr(response, "id", "") or robot_id)
            if returned_id != robot_id:
                raise RobotGatewayError(
                    f"robot identity mismatch: expected {robot_id}, got {returned_id}"
                )
            return RobotDescriptor(
                robot_id=returned_id,
                display_name=str(getattr(response, "name", "") or returned_id),
                robot_type=str(getattr(response, "type", "unknown")),
            )
        except RobotGatewayError:
            raise
        except Exception as exc:
            raise RobotGatewayError(f"GetRobotInfo failed for {robot_id}: {exc}") from exc

    async def get_status(self, robot_id: str) -> RobotStatusSnapshot:
        try:
            response = await self._stub.GetRobotStatus(
                self._messages.EmptyRequest(), timeout=self._default_timeout
            )
            if response.robot_id and response.robot_id != robot_id:
                raise RobotGatewayError(
                    f"robot identity mismatch: expected {robot_id}, got {response.robot_id}"
                )
            return self._snapshot(robot_id, response)
        except RobotGatewayError:
            raise
        except Exception as exc:
            raise RobotUnavailable(f"GetRobotStatus failed for {robot_id}: {exc}") from exc

    async def stream_status(self, robot_id: str) -> AsyncIterator[RobotStatusSnapshot]:
        try:
            stream = self._stub.GetRobotStatusStream(self._messages.EmptyRequest(), timeout=None)
            async for response in stream:
                if response.robot_id and response.robot_id != robot_id:
                    raise RobotGatewayError(
                        f"robot identity mismatch: expected {robot_id}, got {response.robot_id}"
                    )
                yield self._snapshot(robot_id, response)
        except RobotGatewayError:
            raise
        except Exception as exc:
            raise RobotUnavailable(f"GetRobotStatusStream failed for {robot_id}: {exc}") from exc

    @traceable(
        run_type="tool",
        name="ros_grpc.execute_task",
        tags=["transport:ros-grpc"],
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
        del idempotency_key, mission_id, attempt_id, correlation_id
        try:
            response = await self._stub.ExecuteTask(
                self._messages.ExecuteTaskRequest(instruction=instruction),
                timeout=timeout_sec or self._default_timeout,
            )
            return RemoteTaskResult(
                success=bool(response.success),
                message=str(response.result_message),
                result={"robot_id": robot_id, "task_id": task_id},
                remote_task_id=task_id,
            )
        except TimeoutError as exc:
            raise RobotGatewayError(f"ExecuteTask timeout for {robot_id}") from exc
        except Exception as exc:
            raise RobotGatewayError(f"ExecuteTask failed for {robot_id}: {exc}") from exc

    @traceable(
        run_type="tool",
        name="ros_grpc.execute_skill",
        tags=["transport:ros-grpc"],
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
        try:
            response = await self._stub.ExecuteSkill(
                self._messages.ExecuteSkillRequest(
                    skill_name=skill_name,
                    params_json=json.dumps(params),
                    timeout_sec=timeout_sec,
                ),
                timeout=timeout_sec or self._default_timeout,
            )
            raw_result = str(getattr(response, "result_json", "") or "")
            try:
                result = json.loads(raw_result) if raw_result else {}
            except json.JSONDecodeError:
                result = {"raw_result": raw_result}
            return RemoteTaskResult(
                bool(response.success),
                str(response.message),
                result,
                remote_task_id=f"skill:{skill_name}",
            )
        except Exception as exc:
            raise RobotGatewayError(f"ExecuteSkill failed for {robot_id}: {exc}") from exc

    async def request_snapshot(self, robot_id: str) -> RobotSnapshot:
        try:
            response = await self._stub.GetRobotCameraImage(
                self._messages.CameraRequest(image_type=self._messages.COMPRESSED),
                timeout=self._default_timeout,
            )
            if response.robot_id and response.robot_id != robot_id:
                raise RobotGatewayError(
                    f"robot identity mismatch: expected {robot_id}, got {response.robot_id}"
                )
            return RobotSnapshot(
                robot_id=robot_id,
                image_base64=str(response.image),
                image_format=str(response.format or "image/jpeg"),
                timestamp_ms=int(response.timestamp),
            )
        except RobotGatewayError:
            raise
        except Exception as exc:
            raise RobotGatewayError(f"GetRobotCameraImage failed for {robot_id}: {exc}") from exc

    async def emergency_stop(self, robot_id: str) -> RemoteTaskResult:
        try:
            response = await self._stub.EmergencyStop(
                self._messages.EmptyRequest(), timeout=self._default_timeout
            )
            return RemoteTaskResult(bool(response.success), str(response.message))
        except Exception as exc:
            raise RobotGatewayError(f"EmergencyStop failed for {robot_id}: {exc}") from exc

    @classmethod
    def _snapshot(cls, robot_id: str, response: Any) -> RobotStatusSnapshot:
        position = response.current_pose.position
        return RobotStatusSnapshot(
            robot_id=robot_id,
            connection_state=RobotConnectionState.ONLINE,
            operational_state=cls._operational_state(response.current_task),
            battery_percentage=float(response.battery_percentage),
            pose={
                "frame_id": "map",
                "x": float(position.x),
                "y": float(position.y),
                "z": float(position.z),
            },
            current_task_id=response.current_task or None,
        )

    @staticmethod
    def _operational_state(current_task: str) -> RobotOperationalState:
        value = (current_task or "IDLE").upper()
        if value in {state.value for state in RobotOperationalState}:
            return RobotOperationalState(value)
        if value in {"NAVIGATING", "EXECUTING"}:
            return RobotOperationalState.EXECUTING
        return RobotOperationalState.UNKNOWN
