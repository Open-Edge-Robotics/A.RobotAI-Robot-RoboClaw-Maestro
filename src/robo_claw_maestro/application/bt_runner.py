"""Execute a mission's behavior tree on the fleet.

Maestro ticks the tree; each ``Action`` leaf is the mission Task with the same name and
is dispatched to its robot through :class:`TaskDispatcher` (``ExecuteSkill`` for skill
tasks). Robots therefore run exactly the planned skills while Maestro keeps the
cross-robot ordering (Sequence stages, Parallel stages, Fallback recovery, repeat loop).

While a tree run is active it is the only writer of its mission: pause, resume and
cancel requests for that mission must go through this runner.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from robo_claw_maestro.agent.behavior_tree import plan_to_tree, validate_tree
from robo_claw_maestro.domain.event import DomainEvent
from robo_claw_maestro.domain.mission import Mission, MissionStatus
from robo_claw_maestro.domain.task import Task, TaskStatus

from .approval_service import ApprovalService
from .mission_orchestrator import MissionOrchestrator
from .robot_registry import RobotRegistry
from .task_dispatcher import skill_arguments

SUCCESS = "SUCCESS"
FAILURE = "FAILURE"
RUNNING = "RUNNING"
WAITING_APPROVAL = "WAITING_APPROVAL"
_STARTABLE = {MissionStatus.DRAFT, MissionStatus.READY, MissionStatus.RUNNING}
# Robot binding problems are resolved at run time (fallback robot or a failed action).
_RUNTIME_ISSUES = ("robot_id가 비어", "알 수 없는 로봇", "capability가 없습니다")


def peer_requests(result: dict[str, Any] | None) -> list[dict[str, Any]]:
    """``peer_requests`` from a robot result, top level or nested under ``result``."""
    result = result or {}
    nested = result.get("result") if isinstance(result.get("result"), dict) else {}
    for container in (result, nested):
        requests = container.get("peer_requests")
        if isinstance(requests, list):
            return [
                item
                for item in requests
                if isinstance(item, dict) and str(item.get("instruction") or "").strip()
            ]
    return []


def _drop_followups(mission: Mission) -> None:
    for task_id in [
        task.task_id for task in mission.tasks.values() if task.params.get("followup_of")
    ]:
        del mission.tasks[task_id]


def _retryable(task: Task) -> bool:
    # UNKNOWN means the robot may have run the command; resending is a reconciliation call.
    return task.status in {TaskStatus.FAILED, TaskStatus.TIMED_OUT} and task.can_retry()


class TreeRunConflict(ValueError):
    """Raised when a tree run cannot start or a request conflicts with a running tree."""


@dataclass(slots=True)
class TreeRun:
    mission_id: str
    mission: Mission
    tree: dict[str, Any]
    status: str = RUNNING  # RUNNING | SUCCEEDED | FAILED | CANCELLED | ERROR
    cycle: int = 1
    last_cycle: bool = True
    message: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.status == RUNNING

    def mark(self, path: str, status: str, **detail: Any) -> None:
        entry = self.nodes.setdefault(path, {})
        entry.update({"status": status, "cycle": self.cycle, **detail})
        stamp = datetime.now(UTC).isoformat()
        if status == RUNNING:
            entry["started_at"] = stamp
            entry.pop("finished_at", None)
        else:
            entry["finished_at"] = stamp

    def snapshot(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "status": self.status,
            "active": self.active,
            "paused": self.mission.status is MissionStatus.PAUSED,
            "cycle": self.cycle,
            "message": self.message,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "nodes": self.nodes,
        }


def mission_tree(mission: Mission) -> dict[str, Any]:
    """Return the tree stored at planning time, or derive one from the task graph."""
    stored = mission.constraints.get("behavior_tree")
    if isinstance(stored, dict) and isinstance(stored.get("root"), dict):
        return stored
    stages: dict[str, int] = {}

    def stage(task_id: str) -> int:
        if task_id not in stages:
            deps = mission.tasks[task_id].depends_on
            stages[task_id] = 1 + max((stage(dep) for dep in deps), default=0)
        return stages[task_id]

    planned = mission.constraints.get("planned_robots") or {}
    policies = mission.constraints.get("on_failure") or {}
    tasks = [
        {
            "task_id": task.task_id,
            "stage": stage(task.task_id),
            "depends_on": sorted(task.depends_on),
            "assigned_robot_id": str(
                task.params.get("preferred_robot_id") or planned.get(task.task_id) or ""
            ),
            "skill_name": task.skill_name,
            "params": {k: v for k, v in task.params.items() if k != "preferred_robot_id"},
            "timeout_sec": task.timeout_sec,
            "max_attempts": task.max_attempts,
            "on_failure": policies.get(task.task_id, "abort"),
        }
        for task in sorted(mission.tasks.values(), key=lambda item: item.task_id)
        if not task.params.get("followup_of")
    ]
    repeat = mission.constraints.get("repeat")
    return plan_to_tree(
        name=mission.mission_id[:64].lower(),
        goal=mission.goal,
        tasks=tasks,
        repeat=repeat if isinstance(repeat, dict) else None,
        battery_guard_pct=None,
    )


class MissionTreeRunner:
    def __init__(
        self,
        orchestrator: MissionOrchestrator,
        registry: RobotRegistry,
        *,
        poll_sec: float = 1.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        approval_service: ApprovalService | None = None,
    ) -> None:
        self._approvals = approval_service
        self._orchestrator = orchestrator
        self._registry = registry
        self._poll = poll_sec
        self._sleep = sleep
        self._runs: dict[str, TreeRun] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._robot_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    # ----------------------------------------------------------------- control
    def is_running(self, mission_id: str) -> bool:
        run = self._runs.get(mission_id)
        return run is not None and run.active

    def snapshot(self, mission_id: str) -> dict[str, Any] | None:
        run = self._runs.get(mission_id)
        return run.snapshot() if run is not None else None

    def mission(self, mission_id: str) -> Mission:
        """The live mission object of an active run, else the stored mission."""
        run = self._runs.get(mission_id)
        if run is not None and run.active:
            return run.mission
        return self._orchestrator.get(mission_id)

    def start(self, mission_id: str) -> TreeRun:
        if self.is_running(mission_id):
            raise TreeRunConflict("behavior tree is already running for this mission")
        mission = self._orchestrator.get(mission_id)
        if mission.status not in _STARTABLE:
            raise TreeRunConflict(f"mission must be DRAFT, READY or RUNNING, got {mission.status}")
        tree = mission_tree(mission)
        skills = {robot.robot_id: set(robot.capabilities) for robot in self._registry.list()}
        errors = [
            issue
            for issue in validate_tree(tree, skills)
            if issue.level == "error" and not any(key in issue.message for key in _RUNTIME_ISSUES)
        ]
        if errors:
            raise TreeRunConflict(
                "invalid behavior tree: "
                + "; ".join(f"{issue.path}: {issue.message}" for issue in errors)
            )
        if mission.status is MissionStatus.DRAFT:
            mission.transition_to(MissionStatus.READY)
        mission.constraints["behavior_tree"] = tree
        run = TreeRun(mission_id=mission_id, mission=mission, tree=tree)
        self._runs[mission_id] = run
        self._event(run, "behavior_tree.started", {})
        self._orchestrator.persist(mission)
        self._tasks[mission_id] = asyncio.create_task(self._run(run))
        return run

    def rerun(self, mission_id: str, *, failed_only: bool = True) -> TreeRun:
        """Reopen a finished mission and run its tree again.

        With ``failed_only`` the SUCCEEDED actions are skipped, so the tree resumes at the
        failed step. Reset tasks get a new idempotency key (``:r<n>``).
        """
        if self.is_running(mission_id):
            raise TreeRunConflict("behavior tree is already running for this mission")
        mission = self._orchestrator.get(mission_id)
        if not failed_only:
            _drop_followups(mission)  # sources rerun and report their peer work again
        try:
            mission.reopen(failed_only=failed_only)
        except ValueError as exc:
            raise TreeRunConflict(str(exc)) from exc
        # Rebuild the generated tree from the stored plan so tree-structure fixes apply.
        mission.constraints.pop("behavior_tree", None)
        self._orchestrator.persist(mission)
        return self.start(mission_id)

    def pause(self, mission_id: str) -> Mission:
        run = self._active(mission_id)
        run.mission.transition_to(MissionStatus.PAUSED)
        self._orchestrator.persist(run.mission)
        return run.mission

    def resume(self, mission_id: str) -> Mission:
        run = self._active(mission_id)
        run.mission.transition_to(MissionStatus.RUNNING)
        self._orchestrator.persist(run.mission)
        return run.mission

    def cancel(self, mission_id: str) -> Mission:
        run = self._active(mission_id)
        MissionOrchestrator.apply_cancel(run.mission)
        run.message = "취소 요청됨: 실행 중인 Action이 끝나면 멈춥니다."
        self._orchestrator.persist(run.mission)
        return run.mission

    async def wait(self, mission_id: str) -> None:
        task = self._tasks.get(mission_id)
        if task is not None:
            await asyncio.shield(task)

    async def close(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        for task in list(self._tasks.values()):
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _active(self, mission_id: str) -> TreeRun:
        run = self._runs.get(mission_id)
        if run is None or not run.active:
            raise TreeRunConflict("no behavior tree is running for this mission")
        return run

    # --------------------------------------------------------------- execution
    async def _run(self, run: TreeRun) -> None:
        root = run.tree["root"]
        loop = run.tree.get("loop") or {}
        repeating = bool(loop.get("enabled"))
        max_cycles = int(loop.get("max_cycles") or 0)
        run.cycle = max((task.cycle for task in run.mission.tasks.values()), default=1)
        result = FAILURE
        try:
            while True:
                run.last_cycle = not repeating or (max_cycles > 0 and run.cycle >= max_cycles)
                result = await self._tick(root, "root", run)
                if result != SUCCESS or run.last_cycle or self._stopping(run):
                    break
                run.cycle += 1
                _drop_followups(run.mission)
                for task in run.mission.tasks.values():
                    task.reset_for_cycle(run.cycle)
                self._event(run, "behavior_tree.cycle_started", {"cycle": run.cycle})
                self._orchestrator.persist(run.mission)
            self._finish(run, result)
        except asyncio.CancelledError:
            run.status, run.message = "CANCELLED", "Maestro 종료로 실행이 중단되었습니다."
            run.finished_at = datetime.now(UTC)
            self._orchestrator.persist(run.mission)
            raise
        except Exception as exc:  # keep the run inspectable instead of losing the error
            run.status, run.message = "ERROR", f"{type(exc).__name__}: {exc}"
            run.finished_at = datetime.now(UTC)
            self._event(run, "behavior_tree.finished", {"status": run.status})
            self._orchestrator.persist(run.mission)
        finally:
            self._tasks.pop(run.mission_id, None)

    def _stopping(self, run: TreeRun) -> bool:
        return run.mission.status in {
            MissionStatus.CANCEL_REQUESTED,
            MissionStatus.CANCELLED,
            MissionStatus.FAILED,
        }

    async def _checkpoint(self, run: TreeRun) -> bool:
        """Wait while paused; return False when the run must stop."""
        while run.mission.status is MissionStatus.PAUSED:
            await self._sleep(self._poll)
        return not self._stopping(run)

    async def _tick(self, node: dict[str, Any], path: str, run: TreeRun) -> str:
        kind = node.get("type")
        if not await self._checkpoint(run):
            run.mark(path, FAILURE, message="취소됨")
            return FAILURE
        run.mark(path, RUNNING)
        children = node.get("children") or []
        detail: dict[str, Any] = {}
        if kind == "Sequence":
            result = SUCCESS
            for index, child in enumerate(children):
                if await self._tick(child, f"{path}.{index}", run) != SUCCESS:
                    result = FAILURE
                    break
        elif kind == "Fallback":
            result = FAILURE
            for index, child in enumerate(children):
                if await self._tick(child, f"{path}.{index}", run) == SUCCESS:
                    result = SUCCESS
                    break
        elif kind == "Parallel":
            outcomes = await asyncio.gather(
                *(self._tick(child, f"{path}.{index}", run) for index, child in enumerate(children))
            )
            succeeded = sum(1 for item in outcomes if item == SUCCESS)
            needed = int(node.get("success_count") or len(children))
            result = SUCCESS if succeeded >= needed else FAILURE
            detail["message"] = f"{succeeded}/{len(children)} 성공 (필요 {needed})"
        elif kind == "Action":
            result, detail = await self._action(node, path, run)
        elif kind == "Condition":
            result, detail = self._condition(node)
        elif kind == "AlwaysSuccess":
            result = SUCCESS
        elif kind == "SleepBetweenCycles":
            result, detail = await self._sleep_between_cycles(node, run)
        else:
            result, detail = FAILURE, {"message": f"알 수 없는 노드 타입: {kind}"}
        run.mark(path, result, **detail)
        return result

    async def _sleep_between_cycles(self, node: dict[str, Any], run: TreeRun):
        if run.last_cycle:
            return SUCCESS, {"message": "마지막 사이클이라 대기하지 않음"}
        remaining = float(node.get("interval_sec") or 0)
        while remaining > 0:
            if not await self._checkpoint(run):
                return FAILURE, {"message": "취소됨"}
            step = min(self._poll, remaining)
            await self._sleep(step)
            remaining -= step
        return SUCCESS, {}

    def _condition(self, node: dict[str, Any]):
        if node.get("condition") != "CheckBatterySufficient":
            return FAILURE, {"message": f"알 수 없는 조건: {node.get('condition')}"}
        robot_id = str(node.get("robot_id") or "")
        try:
            robot = self._registry.get(robot_id)
        except KeyError:
            return FAILURE, {"robot_id": robot_id, "message": "등록되지 않은 로봇"}
        minimum = float((node.get("params") or {}).get("min_pct", 20))
        battery = robot.battery_percentage
        ok = battery is not None and battery >= minimum
        return (SUCCESS if ok else FAILURE), {
            "robot_id": robot_id,
            "message": f"battery={battery} (기준 {minimum:g}%)",
        }

    async def _await_approval(
        self, task: Task, robot_id: str, path: str, run: TreeRun
    ) -> tuple[str | None, str]:
        """Request a human approval for a dangerous skill and wait for the decision.

        Returns ``(approval_id, "")`` once approved, or ``(None, reason)``.
        """
        if self._approvals is None:
            return None, f"{task.task_id}는 사람 승인이 필요하지만 승인 서비스가 없습니다."
        request = task.params.get("peer_request")
        if isinstance(request, dict):
            reason = (
                f"{request.get('source_robot') or '동료'}의 {task.params.get('followup_of')} 중 "
                f"동료 작업 요청: {request.get('kind')} · 대상 {request.get('target') or '-'} · "
                f"장소 {request.get('place') or '-'}"
            )
        else:
            reason = f"BT {task.task_id}: {task.skill_name}는 risk_level=dangerous 스킬입니다."
        approval = self._approvals.request_mission_task(
            robot_id=robot_id,
            mission_id=run.mission_id,
            task_id=task.task_id,
            skill_name=task.skill_name or "peer_request",
            params=skill_arguments(task.params) if task.skill_name else {},
            reason=reason,
        )
        self._orchestrator.persist(run.mission)
        while True:
            if not await self._checkpoint(run):
                self._approvals.withdraw(approval.approval_id, "maestro:cancelled")
                return None, "취소됨"
            status = self._approvals.current(approval.approval_id).status.value
            if status == "APPROVED":
                return approval.approval_id, ""
            if status != "PENDING":
                return None, f"승인 {status}: {robot_id}에 보내지 않았습니다."
            run.mark(
                path,
                WAITING_APPROVAL,
                robot_id=robot_id,
                approval_id=approval.approval_id,
                message="승인 대기 중: 콘솔의 승인 대기에서 승인하면 실행합니다.",
            )
            await self._sleep(self._poll)

    async def _action(self, node: dict[str, Any], path: str, run: TreeRun):
        mission = run.mission
        task = mission.tasks.get(str(node.get("name") or ""))
        if task is None:
            return FAILURE, {"message": f"Mission에 Task {node.get('name')}가 없습니다."}
        if task.status is TaskStatus.SUCCEEDED:
            result, detail = (
                SUCCESS,
                {
                    "robot_id": task.assigned_robot_id,
                    "message": "이미 성공한 Task",
                },
            )
        else:
            result, detail = await self._run_task(task, str(node.get("robot_id") or ""), path, run)
        if result == SUCCESS:
            followups = await self._run_followups(task, path, run)
            if followups:
                detail["followups"] = followups
        return result, detail

    async def _run_followups(self, source: Task, path: str, run: TreeRun) -> list[dict[str, Any]]:
        """Run the peer work a robot reported instead of calling the peer itself.

        Robo-Claw robots under Maestro return ``peer_requests`` (see robo-claw
        ``skills/fleet_handoff.py``). Each becomes a follow-up Task bound to the suggested
        peer, approved by a human, and dispatched through Maestro like any other Action.
        Follow-up failures do not fail the source Action; the mission ends partially.
        """
        mission = run.mission
        followups = [
            task
            for task in mission.tasks.values()
            if task.params.get("followup_of") == source.task_id
        ]
        if not followups:
            followups = self._create_followups(source, run)
        summary = []
        for index, task in enumerate(sorted(followups, key=lambda item: item.task_id), start=1):
            child = f"{path}.peer{index}"
            if task.status is TaskStatus.SUCCEEDED:
                result, detail = (
                    SUCCESS,
                    {"robot_id": task.assigned_robot_id, "message": "이미 성공"},
                )
            else:
                run.mark(child, RUNNING, task_id=task.task_id)
                result, detail = await self._run_task(task, "", child, run)
            run.mark(child, result, task_id=task.task_id, **detail)
            summary.append({"task_id": task.task_id, "status": result, **detail})
        return summary

    def _create_followups(self, source: Task, run: TreeRun) -> list[Task]:
        mission = run.mission
        created = []
        for index, request in enumerate(peer_requests(source.result), start=1):
            suffix = f"-c{run.cycle}" if run.cycle > 1 else ""
            task_id = f"{source.task_id}-peer-{index}{suffix}"
            if task_id in mission.tasks:
                continue
            peer = str(request.get("suggested_peer") or "")
            if not peer or peer == source.assigned_robot_id:
                # Without a named peer any robot could be picked; report instead of guessing.
                self._event(
                    run,
                    "behavior_tree.followup_skipped",
                    {"source_task_id": source.task_id, "reason": "no suggested peer"},
                )
                continue
            task = Task(
                task_id=task_id,
                mission_id=mission.mission_id,
                instruction=str(request["instruction"]),
                task_type="peer_request",
                params={
                    "followup_of": source.task_id,
                    # Bound to the peer the robot picked; never silently substituted.
                    "preferred_robot_id": peer,
                    "requires_approval": True,
                    "motion": True,  # the peer moves to the place: battery policy applies
                    "peer_request": {**request, "source_robot": source.assigned_robot_id},
                },
                depends_on={source.task_id},
                timeout_sec=300.0,
            )
            mission.add_task(task)
            created.append(task)
        if created:
            self._event(
                run,
                "behavior_tree.followups_created",
                {"source_task_id": source.task_id, "task_ids": [task.task_id for task in created]},
            )
            self._orchestrator.persist(mission)
        return created

    async def _run_task(self, task: Task, planned: str, path: str, run: TreeRun):
        mission = run.mission
        approved_for: dict[str, str] = {}  # robot_id -> approval_id, reused by retries
        while True:
            if not await self._checkpoint(run):
                return FAILURE, {"message": "취소됨"}
            if task.status in {TaskStatus.CREATED, TaskStatus.BLOCKED}:
                task.transition_to(TaskStatus.READY)
            elif _retryable(task):
                task.prepare_retry()
            if task.status is not TaskStatus.READY:
                return FAILURE, {
                    "robot_id": task.assigned_robot_id,
                    "message": f"Task 상태 {task.status.value}: {task.error_message}".rstrip(": "),
                }
            robot_id, note = self._choose_robot(task, planned)
            if robot_id is None:
                return FAILURE, {"robot_id": planned, "message": note}
            dispatcher = self._orchestrator.dispatcher
            approval_id = approved_for.get(robot_id)
            if approval_id is None and dispatcher.requires_approval(
                task, self._registry.get(robot_id)
            ):
                approval_id, reason = await self._await_approval(task, robot_id, path, run)
                if approval_id is None:
                    return FAILURE, {"robot_id": robot_id, "message": reason}
                approved_for[robot_id] = approval_id
                run.mark(path, RUNNING, robot_id=robot_id, message="승인됨")
                robot_id, note = self._choose_robot(task, robot_id)  # recheck after the wait
                if robot_id not in approved_for:
                    return FAILURE, {
                        "robot_id": robot_id,
                        "message": note or "승인 후 로봇 상태 변경",
                    }
            async with self._robot_locks[robot_id]:
                if not await self._checkpoint(run):
                    return FAILURE, {"message": "취소됨"}
                if mission.status is MissionStatus.READY:
                    mission.transition_to(MissionStatus.RUNNING)
                self._orchestrator.persist(mission)
                mode = "skill" if dispatcher.uses_skill(task) else "instruction"
                result = await dispatcher.dispatch(
                    task, robot_id, approved=approved_for.get(robot_id) is not None
                )
            if approved_for.get(robot_id) is not None and self._approvals is not None:
                self._approvals.complete(
                    approved_for.pop(robot_id),
                    task.status is TaskStatus.SUCCEEDED,
                    {"task_id": task.task_id, "message": task.error_message or "성공"},
                )
            self._event(
                run,
                "task.dispatched",
                {
                    "robot_id": robot_id,
                    "task_id": task.task_id,
                    "mode": mode,
                    "ambiguous": result.ambiguous,
                    "success": result.remote_result.success if result.remote_result else None,
                    "cycle": run.cycle,
                },
                aggregate=("task", task.task_id),
            )
            self._orchestrator.persist(mission)
            if task.status is TaskStatus.SUCCEEDED:
                return SUCCESS, {"robot_id": robot_id, "message": note or "성공"}
            if _retryable(task) and not self._stopping(run):
                continue
            return FAILURE, {
                "robot_id": robot_id,
                "message": f"{task.status.value}: {task.error_message}".rstrip(": "),
            }

    def _choose_robot(self, task: Task, planned: str) -> tuple[str | None, str]:
        """Use the planned robot when it passes the safety policy, else a safe substitute."""
        scheduler = self._orchestrator.scheduler
        preferred = str(task.params.get("preferred_robot_id") or "")
        target = preferred or planned
        reason = ""
        if target:
            try:
                decision = scheduler.evaluate(self._registry.get(target), task)
            except KeyError:
                decision = None
                reason = f"{target} 미등록"
            if decision is not None and decision.allowed:
                return target, ""
            if decision is not None:
                reason = f"{target} 사용 불가({decision.reason})"
            if preferred:
                return None, reason
        try:
            assignment = scheduler.assign(task)
        except ValueError:
            return None, (reason + "; " if reason else "") + "실행 가능한 로봇이 없습니다."
        if reason:
            return assignment.robot_id, f"{reason} → {assignment.robot_id}로 대체"
        return assignment.robot_id, ""

    def _finish(self, run: TreeRun, result: str) -> None:
        mission = run.mission
        cancelled = mission.status in {MissionStatus.CANCEL_REQUESTED, MissionStatus.CANCELLED}
        for task in mission.tasks.values():
            if task.status is TaskStatus.CANCEL_REQUESTED:
                task.transition_to(TaskStatus.CANCELLED)
            elif task.status in {TaskStatus.CREATED, TaskStatus.BLOCKED, TaskStatus.READY}:
                # Not reached by the tree (an earlier step aborted the sequence).
                task.transition_to(TaskStatus.CANCEL_REQUESTED)
                task.transition_to(TaskStatus.CANCELLED)
        if cancelled:
            mission.transition_to(MissionStatus.CANCELLED)
            run.status = "CANCELLED"
        else:
            if mission.status in {MissionStatus.READY, MissionStatus.PAUSED}:
                mission.transition_to(MissionStatus.RUNNING)
            if result == SUCCESS:
                every = all(task.status is TaskStatus.SUCCEEDED for task in mission.tasks.values())
                mission.transition_to(
                    MissionStatus.SUCCEEDED if every else MissionStatus.PARTIALLY_SUCCEEDED
                )
                run.status = "SUCCEEDED"
            else:
                # Independent chains keep running when one fails, so some work may be done.
                some = any(task.status is TaskStatus.SUCCEEDED for task in mission.tasks.values())
                mission.transition_to(
                    MissionStatus.PARTIALLY_SUCCEEDED if some else MissionStatus.FAILED
                )
                run.status = "FAILED"
        run.finished_at = datetime.now(UTC)
        if not run.message or run.status != "CANCELLED":
            run.message = {
                "SUCCEEDED": "Behavior Tree 완료",
                "FAILED": "Behavior Tree 실패: 실패한 노드를 확인하세요.",
                "CANCELLED": "취소됨",
            }[run.status]
        self._event(run, "behavior_tree.finished", {"status": run.status, "cycle": run.cycle})
        self._orchestrator.persist(mission)

    def _event(
        self,
        run: TreeRun,
        event_type: str,
        payload: dict[str, Any],
        *,
        aggregate: tuple[str, str] | None = None,
    ) -> None:
        aggregate_type, aggregate_id = aggregate or ("mission", run.mission_id)
        self._orchestrator.events.append(
            DomainEvent.create(
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                payload={"mission_id": run.mission_id, **payload},
                correlation_id=run.mission_id,
            )
        )
