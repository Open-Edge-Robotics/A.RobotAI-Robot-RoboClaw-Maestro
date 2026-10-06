"""Human approval workflow for dangerous robot skills."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from robo_claw_maestro.adapters.database.in_memory import InMemoryApprovalRepository
from robo_claw_maestro.domain.approval import ApprovalRequest, ApprovalStatus
from robo_claw_maestro.domain.event import DomainEvent
from robo_claw_maestro.ports.repositories import ApprovalRepository, EventRepository
from robo_claw_maestro.ports.robot_gateway import RemoteTaskResult

from .robot_registry import RobotRegistry


class SkillNotAllowed(ValueError):
    pass


class ApprovalService:
    def __init__(
        self,
        registry: RobotRegistry,
        repository: ApprovalRepository | None = None,
        event_repository: EventRepository | None = None,
        *,
        approval_ttl_sec: float = 900,
        allowed_skills: set[str] | None = None,
    ) -> None:
        self._registry = registry
        self._repository = repository or InMemoryApprovalRepository()
        self._events = event_repository or registry.events
        self._ttl = approval_ttl_sec
        self._allowed_skills = allowed_skills

    def list(self, *, pending_only: bool = False) -> list[ApprovalRequest]:
        approvals = self._repository.list()
        if pending_only:
            return [item for item in approvals if item.status is ApprovalStatus.PENDING]
        return approvals

    def get(self, approval_id: str) -> ApprovalRequest:
        approval = self._repository.get(approval_id)
        if approval is None:
            raise KeyError(f"approval not found: {approval_id}")
        return approval

    async def request_skill(
        self,
        robot_id: str,
        skill_name: str,
        params: dict[str, Any],
        timeout_sec: float,
        requested_by: str,
    ) -> ApprovalRequest | RemoteTaskResult:
        robot = self._registry.get(robot_id)
        if self._allowed_skills is not None and skill_name not in self._allowed_skills:
            raise SkillNotAllowed(f"skill is not in the Maestro allowlist: {skill_name}")
        capability = robot.capabilities.get(skill_name)
        if capability is None:
            raise SkillNotAllowed(f"robot does not declare skill capability: {skill_name}")
        if capability.risk_level != "dangerous":
            return await self._registry.execute_skill(robot_id, skill_name, params, timeout_sec)
        approval = ApprovalRequest.create(
            robot_id=robot_id,
            action=f"skill:{skill_name}",
            payload={"params": params, "timeout_sec": timeout_sec},
            requested_by=requested_by,
            reason=f"skill risk_level={capability.risk_level}",
            expires_at=datetime.now(UTC) + timedelta(seconds=self._ttl),
        )
        self._repository.add(approval)
        self._emit("approval.requested", approval)
        return approval

    async def approve(self, approval_id: str, actor: str) -> ApprovalRequest:
        approval = self.get(approval_id)
        approval.approve(actor)
        self._repository.save(approval)
        self._emit("approval.approved", approval)
        if not approval.action.startswith("skill:"):
            raise ValueError(f"unsupported approval action: {approval.action}")
        skill_name = approval.action.removeprefix("skill:")
        result = await self._registry.execute_skill(
            approval.robot_id,
            skill_name,
            dict(approval.payload.get("params", {})),
            float(approval.payload.get("timeout_sec", 30)),
        )
        approval.mark_executed(
            result.success,
            {"message": result.message, "result": result.result},
        )
        self._repository.save(approval)
        self._emit("approval.executed" if result.success else "approval.failed", approval)
        return approval

    def reject(self, approval_id: str, actor: str) -> ApprovalRequest:
        approval = self.get(approval_id)
        approval.reject(actor)
        self._repository.save(approval)
        self._emit("approval.rejected", approval)
        return approval

    def _emit(self, event_type: str, approval: ApprovalRequest) -> None:
        self._events.append(
            DomainEvent.create(
                event_type=event_type,
                aggregate_type="approval",
                aggregate_id=approval.approval_id,
                payload={
                    "robot_id": approval.robot_id,
                    "action": approval.action,
                    "status": approval.status.value,
                },
                actor=approval.decided_by or approval.requested_by,
                correlation_id=approval.approval_id,
            )
        )
