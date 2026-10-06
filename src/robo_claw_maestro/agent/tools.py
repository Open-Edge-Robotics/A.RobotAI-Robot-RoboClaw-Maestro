"""Application-level tools exposed to the Maestro LLM agent."""

from __future__ import annotations

from robo_claw_maestro.application.mission_orchestrator import MissionOrchestrator
from robo_claw_maestro.application.robot_registry import FleetStopResult, RobotRegistry
from robo_claw_maestro.domain.mission import Mission
from robo_claw_maestro.domain.robot import Capability, Robot

from .maestro_agent import MaestroAgent


class MaestroTools:
    """Narrow facade that prevents an LLM from accessing DB or raw gRPC clients."""

    def __init__(
        self,
        registry: RobotRegistry,
        orchestrator: MissionOrchestrator,
        planner: MaestroAgent | None = None,
    ) -> None:
        self._registry = registry
        self._orchestrator = orchestrator
        self._planner = planner

    def list_robots(self) -> list[Robot]:
        return self._registry.list()

    def get_robot_status(self, robot_id: str) -> Robot:
        return self._registry.get(robot_id)

    def get_robot_capabilities(self, robot_id: str) -> dict[str, Capability]:
        return dict(self._registry.get(robot_id).capabilities)

    async def create_mission_plan(self, goal: str, created_by: str) -> Mission:
        if self._planner is None:
            raise RuntimeError("LLM planner is not configured")
        return await self._planner.create_plan(goal, created_by=created_by)

    def submit_mission(self, mission: Mission) -> Mission:
        return self._orchestrator.create(mission)

    def get_mission_status(self, mission_id: str) -> Mission:
        return self._orchestrator.get(mission_id)

    def cancel_mission(self, mission_id: str) -> Mission:
        return self._orchestrator.cancel(mission_id)

    async def request_robot_observation(self, robot_id: str) -> Robot:
        return await self._registry.synchronize(robot_id)

    async def emergency_stop_robot(self, robot_id: str):
        return await self._registry.emergency_stop(robot_id)

    async def emergency_stop_all(self) -> FleetStopResult:
        return await self._registry.emergency_stop_all()
