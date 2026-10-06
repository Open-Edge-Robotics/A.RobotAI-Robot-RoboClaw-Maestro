"""Reconcile ambiguous local task state with current robot telemetry."""

from __future__ import annotations

from dataclasses import dataclass

from robo_claw_maestro.domain.mission import Mission
from robo_claw_maestro.domain.robot import RobotConnectionState
from robo_claw_maestro.domain.task import TaskStatus

from .robot_registry import RobotRegistry


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    running: tuple[str, ...]
    unresolved: tuple[str, ...]
    failed: tuple[str, ...]


class ReconciliationService:
    def __init__(self, registry: RobotRegistry) -> None:
        self._registry = registry

    async def reconcile(self, mission: Mission) -> ReconciliationResult:
        running: list[str] = []
        unresolved: list[str] = []
        failed: list[str] = []
        for task in mission.tasks.values():
            if task.status is not TaskStatus.UNKNOWN or not task.assigned_robot_id:
                continue
            robot = await self._registry.synchronize(task.assigned_robot_id)
            if robot.connection_state is RobotConnectionState.OFFLINE:
                unresolved.append(task.task_id)
            elif robot.current_task_id == task.task_id:
                task.transition_to(TaskStatus.RUNNING)
                running.append(task.task_id)
            else:
                task.error_code = "RECONCILIATION_INCONCLUSIVE"
                task.error_message = "robot is online but does not report the ambiguous task"
                task.transition_to(TaskStatus.FAILED)
                failed.append(task.task_id)
        mission.refresh_status()
        return ReconciliationResult(tuple(running), tuple(unresolved), tuple(failed))
