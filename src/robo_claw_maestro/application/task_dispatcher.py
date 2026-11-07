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


DISPATCH_MODES = frozenset({"skill", "instruction"})
# Planner/scheduler hints stored in Task.params that are not arguments of the robot skill.
_ROUTING_PARAMS = frozenset(
    {
        "preferred_robot_id",
        "resources",
        "allow_same_robot_retry",
        "motion",
        "followup_of",
        "requires_approval",
        "peer_request",
    }
)


def skill_arguments(params: dict) -> dict:
    """Return the params a robot skill receives, without Maestro routing hints."""
    return {key: value for key, value in params.items() if key not in _ROUTING_PARAMS}


class TaskDispatcher:
    """Send one task at a time and map remote outcomes to local task state.

    A transport exception is deliberately mapped to ``UNKNOWN`` rather than
    ``FAILED``: the remote robot may have accepted or started the command before
    the connection broke. Re-sending is therefore a reconciliation decision,
    not an automatic retry in this class.

    ``dispatch_mode="skill"`` (default) sends a task that names a ``skill_name`` as
    ``ExecuteSkill`` so the robot runs exactly the planned skill with its params.
    Tasks without a skill, or ``dispatch_mode="instruction"``, use natural-language
    ``ExecuteTask`` and the robot agent re-plans the instruction.
    """

    def __init__(
        self,
        registry: RobotRegistry,
        gateway: RobotGateway,
        command_repository: CommandRepository | None = None,
        metrics: MaestroMetrics | None = None,
        dispatch_mode: str = "skill",
        allowed_skills: set[str] | frozenset[str] | None = None,
    ) -> None:
        if dispatch_mode not in DISPATCH_MODES:
            raise ValueError(f"dispatch_mode must be one of {sorted(DISPATCH_MODES)}")
        self._dispatch_mode = dispatch_mode
        self._allowed_skills = frozenset(allowed_skills) if allowed_skills is not None else None
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
    async def dispatch(
        self, task: Task, robot_id: str, *, approved: bool = False
    ) -> DispatchResult:
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
            cycle=task.cycle,
            rerun=task.rerun,
        )
        self._commands.add(command)
        task.idempotency_key = command.idempotency_key

        refusal = (
            self._skill_refusal(task, robot, approved=approved) if self.uses_skill(task) else ""
        )
        if not refusal and not approved and task.params.get("requires_approval"):
            refusal = f"task {task.task_id} requires human approval"
        if refusal:
            # Dangerous or non-allowlisted skills need the human approval flow; never send them.
            task.error_code = "SKILL_NOT_DISPATCHABLE"
            task.error_message = refusal
            task.transition_to(TaskStatus.FAILED)
            robot.current_task_id = None
            self._record_result(TaskStatus.FAILED)
            return DispatchResult(
                task.task_id, robot_id, command, RemoteTaskResult(False, refusal, {})
            )

        identity = {
            "task_id": task.task_id,
            "timeout_sec": task.timeout_sec,
            "idempotency_key": command.idempotency_key,
            "mission_id": command.mission_id,
            "attempt_id": command.attempt_id,
            "correlation_id": command.correlation_id,
        }
        try:
            if self.uses_skill(task):
                remote_result = await self._gateway.execute_skill(
                    robot_id,
                    skill_name=task.skill_name,
                    params=skill_arguments(task.params),
                    **identity,
                )
            else:
                remote_result = await self._gateway.execute_task(
                    robot_id, instruction=task.instruction, **identity
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

        if task.status is TaskStatus.CANCEL_REQUESTED:
            # The mission was cancelled while the robot was still working.
            task.result = dict(remote_result.result)
            task.error_message = remote_result.message
            task.transition_to(TaskStatus.CANCELLED if remote_result.success else TaskStatus.FAILED)
        elif remote_result.success:
            task.result = dict(remote_result.result)
            task.transition_to(TaskStatus.SUCCEEDED)
        else:
            task.error_code = str(remote_result.result.get("code", "REMOTE_FAILURE"))
            task.error_message = remote_result.message
            task.transition_to(TaskStatus.FAILED)
        robot.current_task_id = None
        self._record_result(task.status)
        return DispatchResult(task.task_id, robot_id, command, remote_result)

    def requires_approval(self, task: Task, robot) -> bool:
        """Dangerous skills and tasks flagged ``requires_approval`` need a human approval."""
        if task.params.get("requires_approval"):
            return True
        if not self.uses_skill(task):
            return False
        capability = robot.capabilities.get(task.skill_name)
        return capability is not None and capability.risk_level == "dangerous"

    def _skill_refusal(self, task: Task, robot, *, approved: bool = False) -> str:
        if self._allowed_skills is not None and task.skill_name not in self._allowed_skills:
            return f"skill is not in the Maestro allowlist: {task.skill_name}"
        if not approved and self.requires_approval(task, robot):
            return f"skill {task.skill_name} is dangerous and requires human approval"
        return ""

    def uses_skill(self, task: Task) -> bool:
        return self._dispatch_mode == "skill" and bool(task.skill_name)

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
