"""Mission lifecycle and scheduling orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from robo_claw_maestro.adapters.database.in_memory import (
    InMemoryEventRepository,
    InMemoryMissionRepository,
)
from robo_claw_maestro.domain.event import DomainEvent
from robo_claw_maestro.domain.mission import Mission, MissionStatus
from robo_claw_maestro.domain.task import TaskStatus
from robo_claw_maestro.ports.repositories import EventRepository, MissionRepository
from robo_claw_maestro.telemetry.tracing import (
    process_inputs,
    process_outputs,
    set_current_metadata,
    traceable,
)

from .resource_locks import ResourceLockManager, ResourceLocks
from .scheduler import Assignment, FleetScheduler
from .task_dispatcher import DispatchResult, TaskDispatcher

if TYPE_CHECKING:
    from .reconciliation import ReconciliationResult, ReconciliationService


@dataclass(frozen=True, slots=True)
class SchedulingRound:
    assignments: tuple[Assignment, ...]
    results: tuple[DispatchResult, ...]
    blocked_task_ids: tuple[str, ...]


class MissionOrchestrator:
    """Run validated Mission task graphs without involving an LLM."""

    def __init__(
        self,
        scheduler: FleetScheduler,
        dispatcher: TaskDispatcher,
        mission_repository: MissionRepository | None = None,
        event_repository: EventRepository | None = None,
        resource_locks: ResourceLocks | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._dispatcher = dispatcher
        self._missions = mission_repository or InMemoryMissionRepository()
        self._events = event_repository or InMemoryEventRepository()
        self._resource_locks = resource_locks or ResourceLockManager()

    @property
    def events(self) -> EventRepository:
        return self._events

    @traceable(
        run_type="chain",
        name="persist_mission",
        process_inputs=process_inputs,
        process_outputs=process_outputs,
    )
    def create(self, mission: Mission) -> Mission:
        mission.validate_plan()
        if self._missions.get(mission.mission_id) is not None:
            raise ValueError(f"mission already exists: {mission.mission_id}")
        self._missions.add(mission)
        self._events.append(
            DomainEvent.create(
                event_type="mission.created",
                aggregate_type="mission",
                aggregate_id=mission.mission_id,
                payload={"goal": mission.goal, "task_count": len(mission.tasks)},
                correlation_id=mission.mission_id,
            )
        )
        return mission

    @traceable(
        run_type="chain",
        name="persist_replacement_plan",
        process_inputs=process_inputs,
        process_outputs=process_outputs,
    )
    def replace_plan(self, mission: Mission) -> Mission:
        current = self.get(mission.mission_id)
        if mission.plan_version != current.plan_version + 1:
            raise ValueError("replacement plan_version must increment by one")
        mission.validate_plan()
        if mission.status is MissionStatus.DRAFT:
            mission.transition_to(MissionStatus.READY)
        self._missions.save(mission)
        self._events.append(
            DomainEvent.create(
                event_type="mission.replanned",
                aggregate_type="mission",
                aggregate_id=mission.mission_id,
                payload={
                    "from_plan_version": current.plan_version,
                    "to_plan_version": mission.plan_version,
                },
                correlation_id=mission.mission_id,
            )
        )
        self._append_transition_events(mission)
        return mission

    def get(self, mission_id: str) -> Mission:
        mission = self._missions.get(mission_id)
        if mission is None:
            raise KeyError(f"mission not found: {mission_id}")
        return mission

    def list(self) -> list[Mission]:
        return self._missions.list()

    @traceable(run_type="chain", name="mission_start", process_inputs=process_inputs)
    def start(self, mission_id: str) -> Mission:
        mission = self.get(mission_id)
        if mission.status is MissionStatus.DRAFT:
            mission.transition_to(MissionStatus.READY)
        self._append_transition_events(mission)
        self._save(mission)
        return mission

    @traceable(run_type="chain", name="mission_pause", process_inputs=process_inputs)
    def pause(self, mission_id: str) -> Mission:
        mission = self.get(mission_id)
        mission.transition_to(MissionStatus.PAUSED)
        self._append_transition_events(mission)
        self._save(mission)
        return mission

    @traceable(run_type="chain", name="mission_resume", process_inputs=process_inputs)
    def resume(self, mission_id: str) -> Mission:
        mission = self.get(mission_id)
        mission.transition_to(MissionStatus.RUNNING)
        self._append_transition_events(mission)
        self._save(mission)
        return mission

    @traceable(run_type="chain", name="mission_cancel", process_inputs=process_inputs)
    def cancel(self, mission_id: str) -> Mission:
        mission = self.get(mission_id)
        mission.transition_to(MissionStatus.CANCEL_REQUESTED)
        active = {
            TaskStatus.ASSIGNED,
            TaskStatus.ACCEPTED,
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
        }
        for task in mission.tasks.values():
            if task.is_terminal:
                continue
            was_active = task.status in active
            task.transition_to(TaskStatus.CANCEL_REQUESTED)
            if not was_active:
                task.transition_to(TaskStatus.CANCELLED)
        if all(
            task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}
            for task in mission.tasks.values()
        ):
            mission.transition_to(MissionStatus.CANCELLED)
        self._append_transition_events(mission)
        self._save(mission)
        return mission

    @traceable(
        run_type="chain",
        name="mission_scheduling_round",
        tags=["component:maestro", "operation:schedule"],
        process_inputs=process_inputs,
        process_outputs=process_outputs,
    )
    async def run_round(self, mission_id: str) -> SchedulingRound:
        set_current_metadata({"component": "maestro", "mission_id": mission_id})
        mission = self.get(mission_id)
        if mission.status is MissionStatus.DRAFT:
            mission.transition_to(MissionStatus.READY)
        if mission.status not in {MissionStatus.READY, MissionStatus.RUNNING}:
            return SchedulingRound((), (), ())

        now = datetime.now(UTC)
        for task in mission.tasks.values():
            if task.deadline is not None and not task.is_terminal:
                deadline = task.deadline
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=UTC)
                if deadline <= now and task.status in {
                    TaskStatus.CREATED,
                    TaskStatus.BLOCKED,
                    TaskStatus.READY,
                    TaskStatus.ASSIGNED,
                    TaskStatus.ACCEPTED,
                    TaskStatus.QUEUED,
                }:
                    task.error_code = "DEADLINE_EXCEEDED"
                    task.error_message = "task deadline elapsed before completion"
                    task.transition_to(TaskStatus.TIMED_OUT)

        mission.mark_ready_tasks()
        ready = [task for task in mission.ready_tasks() if task.status is TaskStatus.READY]
        lock_blocked: list[str] = []
        lock_ready = []
        for task in ready:
            resources = {str(item) for item in task.params.get("resources", [])}
            if self._resource_locks.acquire_many(resources, task.task_id, task.timeout_sec + 30):
                lock_ready.append(task)
            else:
                lock_blocked.append(task.task_id)
        assignments = self._scheduler.assign_many(lock_ready)
        assigned_ids = {assignment.task_id for assignment in assignments}
        blocked = tuple(
            sorted(
                set(lock_blocked)
                | {task.task_id for task in lock_ready if task.task_id not in assigned_ids}
            )
        )
        for task in lock_ready:
            if task.task_id not in assigned_ids:
                self._resource_locks.release_owner(task.task_id)
        task_by_id = {task.task_id: task for task in ready}
        if ready and mission.status is MissionStatus.READY:
            mission.transition_to(MissionStatus.RUNNING)

        results = await asyncio.gather(
            *(
                self._dispatcher.dispatch(task_by_id[item.task_id], item.robot_id)
                for item in assignments
            )
        )
        for result in results:
            task = mission.tasks[result.task_id]
            self._resource_locks.release_owner(task.task_id)
            if task.status in {TaskStatus.FAILED, TaskStatus.TIMED_OUT} and task.can_retry():
                task.prepare_retry()
        mission.refresh_status()
        self._append_transition_events(mission)
        assignment_by_task = {item.task_id: item for item in assignments}
        for result in results:
            assignment = assignment_by_task[result.task_id]
            self._events.append(
                DomainEvent.create(
                    event_type="task.dispatched",
                    aggregate_type="task",
                    aggregate_id=result.task_id,
                    payload={
                        "mission_id": mission.mission_id,
                        "robot_id": result.robot_id,
                        "ambiguous": result.ambiguous,
                        "success": result.remote_result.success if result.remote_result else None,
                        "scheduler_score": assignment.score,
                        "scheduler_reason": assignment.reason,
                    },
                    correlation_id=mission.mission_id,
                )
            )
        self._save(mission)
        return SchedulingRound(tuple(assignments), tuple(results), blocked)

    def _append_transition_events(self, mission: Mission) -> None:
        for event in mission.pull_events():
            self._events.append(event)
        for task in mission.tasks.values():
            for event in task.pull_events():
                self._events.append(event)

    def _save(self, mission: Mission) -> None:
        save = getattr(self._missions, "save", None)
        if save is not None:
            save(mission)

    @traceable(
        run_type="chain",
        name="startup_reconciliation",
        process_inputs=process_inputs,
        process_outputs=process_outputs,
    )
    async def reconcile_all(
        self, service: ReconciliationService
    ) -> dict[str, ReconciliationResult]:
        results: dict[str, ReconciliationResult] = {}
        for mission in self.list():
            if not any(task.status is TaskStatus.UNKNOWN for task in mission.tasks.values()):
                continue
            results[mission.mission_id] = await service.reconcile(mission)
            self._append_transition_events(mission)
            self._save(mission)
        return results

    async def run_until_settled(self, mission_id: str, max_rounds: int = 100) -> Mission:
        if max_rounds < 1:
            raise ValueError("max_rounds must be at least 1")
        mission = self.get(mission_id)
        for _ in range(max_rounds):
            if mission.is_complete():
                mission.refresh_status()
                return mission
            round_result = await self.run_round(mission_id)
            if not round_result.assignments:
                mission.refresh_status()
                return mission
        return mission
