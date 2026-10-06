import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from robo_claw_maestro.application.approval_service import ApprovalService, SkillNotAllowed
from robo_claw_maestro.application.robot_registry import RobotRegistry
from robo_claw_maestro.domain.approval import ApprovalStatus
from robo_claw_maestro.domain.robot import Capability, Robot, RobotConnectionState
from robo_claw_maestro.ports.robot_gateway import FakeRobotGateway, RobotStatusSnapshot

pytestmark = pytest.mark.component


def make_service():
    gateway = FakeRobotGateway([RobotStatusSnapshot(robot_id="r1")])
    registry = RobotRegistry(gateway)
    robot = Robot("r1", "Robot One", connection_state=RobotConnectionState.ONLINE)
    robot.add_capability(Capability("open_gripper", risk_level="normal"))
    robot.add_capability(Capability("power_tool", risk_level="dangerous"))
    registry.register(robot)
    return ApprovalService(registry), gateway


def test_normal_skill_executes_without_approval():
    service, gateway = make_service()

    result = asyncio.run(
        service.request_skill("r1", "open_gripper", {}, 10, requested_by="operator")
    )

    assert result.success
    assert gateway.executed_tasks
    assert service.list(pending_only=True) == []


def test_dangerous_skill_requires_approval_before_execution():
    service, gateway = make_service()

    approval = asyncio.run(service.request_skill("r1", "power_tool", {"speed": 1}, 10, "operator"))

    assert approval.status is ApprovalStatus.PENDING
    assert gateway.executed_tasks == []

    executed = asyncio.run(service.approve(approval.approval_id, "safety-admin"))

    assert executed.status is ApprovalStatus.EXECUTED
    assert gateway.executed_tasks


def test_unknown_or_disallowed_skill_is_rejected():
    service, _ = make_service()
    with pytest.raises(SkillNotAllowed, match="does not declare"):
        asyncio.run(service.request_skill("r1", "unknown", {}, 10, "operator"))


def test_expired_approval_cannot_execute():
    service, _ = make_service()
    approval = asyncio.run(service.request_skill("r1", "power_tool", {}, 10, "operator"))
    approval.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    with pytest.raises(ValueError, match="expired"):
        asyncio.run(service.approve(approval.approval_id, "safety-admin"))
    assert approval.status is ApprovalStatus.EXPIRED
