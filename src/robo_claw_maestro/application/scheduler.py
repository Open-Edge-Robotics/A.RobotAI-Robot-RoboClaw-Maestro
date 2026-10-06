"""Deterministic robot selection for ready tasks."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from robo_claw_maestro.domain.policies import SchedulingSafetyPolicy
from robo_claw_maestro.domain.robot import Robot
from robo_claw_maestro.domain.task import Task
from robo_claw_maestro.telemetry.tracing import process_inputs, process_outputs, traceable

from .robot_registry import RobotRegistry


@traceable(
    run_type="chain",
    name="evaluate_safety_policy",
    process_inputs=process_inputs,
    process_outputs=process_outputs,
)
def _evaluate_candidate(policy: SchedulingSafetyPolicy, robot: Robot, task: Task):
    return policy.evaluate(robot, task)


@dataclass(frozen=True, slots=True)
class Assignment:
    task_id: str
    robot_id: str
    score: float
    reason: str


class NoEligibleRobot(ValueError):
    """Raised when no registered robot can safely perform a task."""


class FleetScheduler:
    """Select the least-loaded eligible robot with deterministic tie-breaking."""

    def __init__(
        self,
        registry: RobotRegistry,
        safety_policy: SchedulingSafetyPolicy | None = None,
    ) -> None:
        self._registry = registry
        self._safety = safety_policy or SchedulingSafetyPolicy()

    @traceable(
        run_type="chain",
        name="score_robot_candidates",
        process_inputs=process_inputs,
        process_outputs=process_outputs,
    )
    def assign(self, task: Task, *, excluded_robot_ids: Iterable[str] = ()) -> Assignment:
        excluded = set(excluded_robot_ids)
        if task.attempt_count > 0 and not task.params.get("allow_same_robot_retry", False):
            excluded.update(task.attempted_robot_ids)
        candidates = []
        for robot in self._registry.list():
            if robot.robot_id in excluded:
                continue
            preferred = task.params.get("preferred_robot_id")
            if preferred and preferred != robot.robot_id:
                continue
            decision = _evaluate_candidate(self._safety, robot, task)
            if decision.allowed:
                candidates.append((robot, decision.reason))
        if not candidates:
            raise NoEligibleRobot(f"no eligible robot for task {task.task_id}")
        scored = sorted(
            ((self._score(robot, task), robot, reason) for robot, reason in candidates),
            key=lambda item: (item[0], item[1].robot_id),
        )
        score, robot, policy_reason = scored[0]
        return Assignment(
            task_id=task.task_id,
            robot_id=robot.robot_id,
            score=score,
            reason=(
                f"{policy_reason}; queue_depth={robot.queue_depth}; "
                f"battery={robot.battery_percentage}; score={score:.3f}"
            ),
        )

    @traceable(
        run_type="chain",
        name="assign_ready_tasks",
        process_inputs=process_inputs,
        process_outputs=process_outputs,
    )
    def assign_many(self, tasks: Iterable[Task]) -> list[Assignment]:
        """Assign at most one task to each robot in one scheduling round."""
        assignments: list[Assignment] = []
        reserved: set[str] = set()
        for task in sorted(tasks, key=lambda item: (-item.priority, item.task_id)):
            try:
                assignment = self.assign(task, excluded_robot_ids=reserved)
            except NoEligibleRobot:
                continue
            assignments.append(assignment)
            reserved.add(assignment.robot_id)
        return assignments

    @staticmethod
    def _score(robot: Robot, task: Task) -> float:
        score = float(robot.queue_depth) * 10.0
        if robot.battery_percentage is not None:
            score += max(0.0, (50.0 - robot.battery_percentage) / 10.0)
        target = task.params
        if (
            robot.pose
            and all(key in robot.pose for key in ("x", "y"))
            and all(key in target for key in ("x", "y"))
        ):
            score += math.hypot(
                float(robot.pose["x"]) - float(target["x"]),
                float(robot.pose["y"]) - float(target["y"]),
            )
        return score
