"""Parse and validate LLM mission plans before they reach the orchestrator."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from robo_claw_maestro.domain.mission import InvalidMissionPlan, Mission
from robo_claw_maestro.domain.task import Task
from robo_claw_maestro.telemetry.tracing import process_inputs, process_outputs, traceable


class PlanValidationError(ValueError):
    pass


class MissionPlanParser:
    def __init__(
        self,
        available_capabilities: set[str],
        *,
        known_robot_ids: set[str] | None = None,
        max_tasks: int = 20,
        max_attempts: int = 3,
        max_timeout_sec: float = 3600,
    ) -> None:
        if max_tasks < 1:
            raise ValueError("max_tasks must be positive")
        self._capabilities = available_capabilities
        self._robots = known_robot_ids or set()
        self._max_tasks = max_tasks
        self._max_attempts = max_attempts
        self._max_timeout = max_timeout_sec

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
        tasks = [self._task(item, identity) for item in raw_tasks]
        try:
            mission.add_tasks(tasks)
        except (InvalidMissionPlan, ValueError) as exc:
            raise PlanValidationError(str(exc)) from exc
        return mission

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
    def _task(self, item: Any, mission_id: str) -> Task:
        if not isinstance(item, dict):
            raise PlanValidationError("each task must be an object")
        task_id = str(item.get("task_id", "")).strip()
        instruction = str(item.get("instruction", "")).strip()
        required = {str(value) for value in item.get("required_capabilities", [])}
        unknown = required - self._capabilities
        if unknown:
            raise PlanValidationError(f"unknown capabilities: {sorted(unknown)}")
        preferred_robot = str(item.get("preferred_robot_id", "")).strip()
        if preferred_robot and self._robots and preferred_robot not in self._robots:
            raise PlanValidationError(f"unknown robot: {preferred_robot}")
        attempts = int(item.get("max_attempts", 1))
        timeout = float(item.get("timeout_sec", 300))
        if not 1 <= attempts <= self._max_attempts:
            raise PlanValidationError("task retry budget exceeds policy")
        if not 0 < timeout <= self._max_timeout:
            raise PlanValidationError("task timeout exceeds policy")
        params = dict(item.get("params", {}))
        if preferred_robot:
            params["preferred_robot_id"] = preferred_robot
        return Task(
            task_id=task_id,
            mission_id=mission_id,
            instruction=instruction,
            task_type=str(item.get("task_type", "instruction")),
            skill_name=str(item.get("skill_name", "")),
            params=params,
            required_capabilities=required,
            depends_on={str(value) for value in item.get("depends_on", [])},
            priority=int(item.get("priority", 0)),
            timeout_sec=timeout,
            max_attempts=attempts,
        )
