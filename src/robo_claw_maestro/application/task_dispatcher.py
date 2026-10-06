"""Dispatch validated tasks to Robo-Claw agents."""

from __future__ import annotations

import time
from dataclasses import dataclass

from robo_claw_maestro.adapters.database.in_memory import InMemoryCommandRepository
from robo_claw_maestro.domain.command import Command
from robo_claw_maestro.domain.task import Task, TaskStatus
from robo_claw_maestro.ports.repositories import CommandRepository
from robo_claw_maestro.ports.robot_gateway import (
    RemoteTaskResult,
    RobotGateway,
    RobotGatewayError,
)
from robo_claw_maestro.telemetry.metrics import MaestroMetrics
from robo_claw_maestro.telemetry.tracing import (
    process_inputs,
    process_outputs,
    set_current_metadata,
    traceable,
)

from .robot_registry import RobotRegistry


@dataclass(frozen=True, slots=True)
class DispatchResult:
    task_id: str
    robot_id: str
    command: Command
    remote_result: RemoteTaskResult | None
    ambiguous: bool = False


class TaskDispatchError(ValueError):
    """Raised when a task cannot be dispatched deterministically."""


class TaskDispatcher:
    """Send one task at a time and map remote outcomes to local task state.

    A transport exception is deliberately mapped to ``UNKNOWN`` rather than
    ``FAILED``: the remote robot may have accepted or started the command before
    the connection broke. Re-sending is therefore a reconciliation decision,
    not an automatic retry in this class.
    """

    def __init__(
        self,
        registry: RobotRegistry,
        gateway: RobotGateway,
        command_repository: CommandRepository | None = None,
        metrics: MaestroMetrics | None = None,
    ) -> None:
        self._registry = registry
        self._gateway = gateway
        self._commands = command_repository or InMemoryCommandRepository()
        self._metrics = metrics

    @traceable(
        run_type="tool",
        name="dispatch_task",
        tags=["component:maestro", "operation:dispatch"],
        process_inputs=process_inputs,
        process_outputs=process_outputs,
    )
    async def dispatch(self, task: Task, robot_id: str) -> DispatchResult:
        set_current_metadata(
            {
                "component": "maestro",
                "mission_id": task.mission_id,
                "task_id": task.task_id,
                "robot_id": robot_id,
            },
            tags=[f"robot:{robot_id}"],
        )
        if task.status is not TaskStatus.READY:
            raise TaskDispatchError(f"task {task.task_id} must be READY, got {task.status}")
        robot = self._registry.get(robot_id)
        if not robot.is_eligible(task.required_capabilities):
            raise TaskDispatchError(f"robot {robot_id} is not eligible for task {task.task_id}")

        task.assign(robot_id)
        task.attempted_robot_ids.add(robot_id)
        task.start_attempt()
        robot.current_task_id = task.task_id
        command = Command.create(
            mission_id=task.mission_id,
            task_id=task.task_id,
            robot_id=robot_id,
            instruction=task.instruction,
            attempt=task.attempt_count,
            skill_name=task.skill_name,
            params=task.params,
            deadline_epoch_ms=int((time.time() + task.timeout_sec) * 1000),
        )
        self._commands.add(command)
        task.idempotency_key = command.idempotency_key

        try:
            remote_result = await self._gateway.execute_task(
                robot_id,
                instruction=task.instruction,
                task_id=task.task_id,
                timeout_sec=task.timeout_sec,
                idempotency_key=command.idempotency_key,
                mission_id=command.mission_id,
                attempt_id=command.attempt_id,
                correlation_id=command.correlation_id,
            )
        except RobotGatewayError as exc:
            task.error_code = "REMOTE_STATUS_UNKNOWN"
            task.error_message = str(exc)
            task.transition_to(TaskStatus.UNKNOWN)
            robot.current_task_id = None
            self._record_result(TaskStatus.UNKNOWN)
            return DispatchResult(task.task_id, robot_id, command, None, ambiguous=True)
        except Exception as exc:
            task.error_code = "GATEWAY_ERROR"
            task.error_message = str(exc)
            task.transition_to(TaskStatus.UNKNOWN)
            robot.current_task_id = None
            self._record_result(TaskStatus.UNKNOWN)
            return DispatchResult(task.task_id, robot_id, command, None, ambiguous=True)

        if remote_result.success:
            task.result = dict(remote_result.result)
            task.transition_to(TaskStatus.SUCCEEDED)
        else:
            task.error_code = str(remote_result.result.get("code", "REMOTE_FAILURE"))
            task.error_message = remote_result.message
            task.transition_to(TaskStatus.FAILED)
        robot.current_task_id = None
        self._record_result(task.status)
        return DispatchResult(task.task_id, robot_id, command, remote_result)

    def _record_result(self, status: TaskStatus) -> None:
        if self._metrics is not None:
            self._metrics.task_results.labels(status=status.value).inc()

    async def dispatch_many(self, assignments: list[tuple[Task, str]]) -> list[DispatchResult]:
        """Dispatch independent assignments concurrently.

        The caller is responsible for dependency validation and resource locks.
        ``asyncio.gather`` is intentionally used here so independent robots do
        not block one another.
        """
        import asyncio

        return list(
            await asyncio.gather(*(self.dispatch(task, robot_id) for task, robot_id in assignments))
        )
