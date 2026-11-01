"""Parse and validate LLM mission plans before they reach the orchestrator."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from robo_claw_maestro.domain.mission import InvalidMissionPlan, Mission
from robo_claw_maestro.domain.task import Task
from robo_claw_maestro.telemetry.tracing import process_inputs, process_outputs, traceable


class PlanValidationError(ValueError):
    pass


NameSource = set[str] | Callable[[], set[str]]


def _resolve(source: NameSource) -> set[str]:
    return set(source()) if callable(source) else set(source)


class MissionPlanParser:
    def __init__(
        self,
        available_capabilities: NameSource,
        *,
        known_robot_ids: NameSource | None = None,
        max_tasks: int = 20,
        max_attempts: int = 3,
        max_timeout_sec: float = 3600,
        single_skill: bool = False,
    ) -> None:
        if max_tasks < 1:
            raise ValueError("max_tasks must be positive")
        # Callables are resolved per parse so robots registered at runtime are honored.
        self._capabilities = available_capabilities
        self._robots = known_robot_ids if known_robot_ids is not None else set()
        self._max_tasks = max_tasks
        self._max_attempts = max_attempts
        self._max_timeout = max_timeout_sec
        # Single-skill plans map one task to one skill call, so they convert directly to BT leaves.
        self.single_skill = single_skill

    @traceable(
        run_type="chain",
        name="validate_mission_plan",
        process_inputs=process_inputs,
        process_outputs=process_outputs,
    )
    def parse(
        self,
        response: str,
        *,
        goal: str,
        created_by: str,
        mission_id: str | None = None,
        available_capabilities: set[str] | None = None,
        known_robot_ids: set[str] | None = None,
    ) -> Mission:
        data = self._decode(response)
        raw_tasks = data.get("tasks")
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise PlanValidationError("plan must contain a non-empty tasks array")
        if len(raw_tasks) > self._max_tasks:
            raise PlanValidationError(f"plan exceeds maximum task count: {self._max_tasks}")
        identity = mission_id or f"mission-{uuid4().hex}"
        mission = Mission(
            mission_id=identity,
            goal=goal,
            created_by=created_by,
            summary=str(data.get("summary", "")),
        )
        resolved_capabilities, resolved_robots = self.resolve_sources()
        capabilities = (
            resolved_capabilities if available_capabilities is None else available_capabilities
        )
        robots = resolved_robots if known_robot_ids is None else known_robot_ids
        tasks = [self._task(item, identity, capabilities, robots) for item in raw_tasks]
        try:
            mission.add_tasks(tasks)
        except (InvalidMissionPlan, ValueError) as exc:
            raise PlanValidationError(str(exc)) from exc
        return mission

    def metadata(self, response: str) -> tuple[dict[str, float | int] | None, dict[str, str]]:
        """Return the plan-level ``repeat`` setting and each task's ``on_failure`` policy.

        These fields shape the behavior tree but are not part of the Task domain model.
        """
        data = self._decode(response)
        repeat = data.get("repeat")
        if repeat is not None:
            if not isinstance(repeat, dict):
                raise PlanValidationError("repeat must be an object")
            try:
                interval = float(repeat.get("interval_sec", 0))
                cycles = int(repeat.get("max_cycles", 0))
            except (TypeError, ValueError) as exc:
                raise PlanValidationError("repeat fields must be numeric") from exc
            if interval <= 0 or cycles < 0:
                raise PlanValidationError("repeat needs interval_sec > 0 and max_cycles >= 0")
            repeat = {"interval_sec": interval, "max_cycles": cycles}
        policies = {
            str(item.get("task_id", "")).strip(): str(item.get("on_failure", "abort"))
            for item in data.get("tasks", [])
            if isinstance(item, dict)
        }
        return repeat, policies

    def resolve_sources(self) -> tuple[set[str], set[str]]:
        return _resolve(self._capabilities), _resolve(self._robots)

    @staticmethod
    @traceable(
        run_type="chain",
        name="decode_plan_json",
        process_inputs=process_inputs,
        process_outputs=process_outputs,
    )
    def _decode(response: str) -> dict[str, Any]:
        text = response.strip()
        if text.startswith("```"):
            first_newline = text.find("\n")
            text = text[first_newline + 1 :] if first_newline >= 0 else text
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3]
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            raise PlanValidationError("LLM response does not contain a JSON object")
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise PlanValidationError(f"invalid plan JSON: {exc.msg}") from exc
        if not isinstance(data, dict):
            raise PlanValidationError("plan root must be an object")
        return data

    @traceable(
        run_type="chain",
        name="validate_plan_task",
        process_inputs=process_inputs,
        process_outputs=process_outputs,
    )
    def _task(self, item: Any, mission_id: str, capabilities: set[str], robots: set[str]) -> Task:
        if not isinstance(item, dict):
            raise PlanValidationError("each task must be an object")
        task_id = str(item.get("task_id", "")).strip()
        instruction = str(item.get("instruction", "")).strip()
        required = {str(value) for value in item.get("required_capabilities", [])}
        skill_name = str(item.get("skill_name", "")).strip()
        if self.single_skill:
            if not skill_name:
                raise PlanValidationError(f"task {task_id!r} must call exactly one skill_name")
            if required - {skill_name}:
                raise PlanValidationError(
                    f"task {task_id!r} must require only its skill_name {skill_name!r}"
                )
            required = {skill_name}
            if str(item.get("on_failure", "abort")) not in {"abort", "continue"}:
                raise PlanValidationError(f"on_failure must be abort or continue in {task_id!r}")
        unknown = required - capabilities
        if unknown:
            raise PlanValidationError(f"unknown capabilities: {sorted(unknown)}")
        preferred_robot = str(item.get("preferred_robot_id", "")).strip()
        if preferred_robot and robots and preferred_robot not in robots:
            raise PlanValidationError(f"unknown robot: {preferred_robot}")
        try:
            attempts = int(item.get("max_attempts", 1))
            timeout = float(item.get("timeout_sec", 300))
            priority = int(item.get("priority", 0))
        except (TypeError, ValueError) as exc:
            raise PlanValidationError(f"invalid numeric field in task {task_id!r}") from exc
        if not 1 <= attempts <= self._max_attempts:
            raise PlanValidationError("task retry budget exceeds policy")
        if not 0 < timeout <= self._max_timeout:
            raise PlanValidationError("task timeout exceeds policy")
        raw_params = item.get("params", {})
        if not isinstance(raw_params, dict):
            raise PlanValidationError(f"params must be an object in task {task_id!r}")
        params = dict(raw_params)
        if preferred_robot:
            params["preferred_robot_id"] = preferred_robot
        try:
            return Task(
                task_id=task_id,
                mission_id=mission_id,
                instruction=instruction,
                task_type="skill"
                if self.single_skill
                else str(item.get("task_type", "instruction")),
                skill_name=skill_name,
                params=params,
                required_capabilities=required,
                depends_on={str(value) for value in item.get("depends_on", [])},
                priority=priority,
                timeout_sec=timeout,
                max_attempts=attempts,
            )
        except ValueError as exc:
            raise PlanValidationError(str(exc)) from exc
