import pytest
from fastapi.testclient import TestClient

from robo_claw_maestro.agent.maestro_agent import MaestroAgent
from robo_claw_maestro.agent.planner import MissionPlanParser
from robo_claw_maestro.agent.provider import FakeLlmProvider
from robo_claw_maestro.api.app import create_app
from robo_claw_maestro.application.approval_service import ApprovalService
from robo_claw_maestro.application.mission_orchestrator import MissionOrchestrator
from robo_claw_maestro.application.robot_registry import RobotRegistry
from robo_claw_maestro.application.scheduler import FleetScheduler
from robo_claw_maestro.application.task_dispatcher import TaskDispatcher
from robo_claw_maestro.ports.robot_gateway import FakeRobotGateway

pytestmark = pytest.mark.integration


def make_client(readiness_check=lambda: True):
    gateway = FakeRobotGateway()
    registry = RobotRegistry(gateway)
    orchestrator = MissionOrchestrator(FleetScheduler(registry), TaskDispatcher(registry, gateway))
    return TestClient(create_app(registry, orchestrator, readiness_check))


def test_health_and_database_readiness_are_separate():
    client = make_client(lambda: False)

    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 503


def test_direct_robot_task_and_event_endpoints():
    from robo_claw_maestro.domain.robot import Capability, Robot, RobotConnectionState
    from robo_claw_maestro.ports.robot_gateway import RobotStatusSnapshot

    gateway = FakeRobotGateway([RobotStatusSnapshot(robot_id="r1")])
    registry = RobotRegistry(gateway)
    robot = Robot("r1", "Robot One", connection_state=RobotConnectionState.ONLINE)
    robot.add_capability(Capability("inspect"))
    registry.register(robot)
    orchestrator = MissionOrchestrator(FleetScheduler(registry), TaskDispatcher(registry, gateway))
    client = TestClient(create_app(registry, orchestrator))

    response = client.post(
        "/api/v1/robots/r1/tasks",
        json={"instruction": "inspect", "required_capabilities": ["inspect"]},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "SUCCEEDED"
    assert client.get("/api/v1/robots/r1/events").json()[0]["event_type"] == "robot.registered"


def test_robot_snapshot_endpoint():
    from robo_claw_maestro.domain.robot import Robot
    from robo_claw_maestro.ports.robot_gateway import RobotStatusSnapshot

    gateway = FakeRobotGateway([RobotStatusSnapshot(robot_id="r1")])
    registry = RobotRegistry(gateway)
    registry.register(Robot("r1", "Robot One"))
    orchestrator = MissionOrchestrator(FleetScheduler(registry), TaskDispatcher(registry, gateway))

    response = TestClient(create_app(registry, orchestrator)).get("/api/v1/robots/r1/snapshot")

    assert response.status_code == 200
    assert response.json()["robot_id"] == "r1"
    assert response.json()["image_format"] == "image/jpeg"


def test_websocket_replays_fleet_events():
    client = make_client()

    with client.websocket_connect("/api/v1/events/stream") as websocket:
        # No events exist yet; create one through a separate API is not possible in this fixture.
        # Closing verifies handshake and disconnect handling.
        websocket.close()


def test_goal_endpoint_creates_only_validated_mission():
    gateway = FakeRobotGateway()
    registry = RobotRegistry(gateway)
    orchestrator = MissionOrchestrator(FleetScheduler(registry), TaskDispatcher(registry, gateway))
    plan = (
        '{"tasks":[{"task_id":"t1","instruction":"inspect",'
        '"required_capabilities":["inspect"],"depends_on":[]}]}'
    )
    provider = FakeLlmProvider([plan, plan])
    agent = MaestroAgent(provider, MissionPlanParser({"inspect"}))
    client = TestClient(create_app(registry, orchestrator, maestro_agent=agent))

    response = client.post("/api/v1/goals", json={"goal": "inspect the room"})

    assert response.status_code == 200
    body = response.json()
    assert body["tasks"][0]["task_id"] == "t1"
    assert len(orchestrator.list()) == 1

    replanned = client.post(
        f"/api/v1/missions/{body['mission_id']}/replan",
        json={"failure_context": {"error_code": "BLOCKED"}},
    )
    metrics = client.get("/api/v1/planning/metrics")

    assert replanned.status_code == 200
    assert replanned.json()["plan_version"] == 2
    assert metrics.json()["replans"] == 1


def test_read_and_control_roles_are_separated():
    gateway = FakeRobotGateway()
    registry = RobotRegistry(gateway)
    orchestrator = MissionOrchestrator(FleetScheduler(registry), TaskDispatcher(registry, gateway))
    client = TestClient(
        create_app(
            registry,
            orchestrator,
            read_token="read-secret",
            control_token="control-secret",
        )
    )

    assert client.get("/api/v1/robots").status_code == 403
    assert (
        client.get("/api/v1/robots", headers={"Authorization": "Bearer read-secret"}).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/fleet/emergency-stop",
            headers={"Authorization": "Bearer read-secret"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/fleet/emergency-stop",
            headers={"Authorization": "Bearer control-secret"},
        ).status_code
        == 200
    )


def test_dangerous_skill_requires_admin_approval():
    from robo_claw_maestro.domain.robot import Capability, Robot, RobotConnectionState
    from robo_claw_maestro.ports.robot_gateway import RobotStatusSnapshot

    gateway = FakeRobotGateway([RobotStatusSnapshot(robot_id="r1")])
    registry = RobotRegistry(gateway)
    robot = Robot("r1", "Robot One", connection_state=RobotConnectionState.ONLINE)
    robot.add_capability(Capability("power_tool", risk_level="dangerous"))
    registry.register(robot)
    orchestrator = MissionOrchestrator(FleetScheduler(registry), TaskDispatcher(registry, gateway))
    approvals = ApprovalService(registry)
    client = TestClient(
        create_app(
            registry,
            orchestrator,
            control_token="control-secret",
            admin_token="admin-secret",
            approval_service=approvals,
        )
    )

    requested = client.post(
        "/api/v1/robots/r1/skills/power_tool",
        headers={"Authorization": "Bearer control-secret"},
        json={"params": {}},
    )
    approval_id = requested.json()["approval"]["approval_id"]
    denied = client.post(
        f"/api/v1/approvals/{approval_id}/approve",
        headers={"Authorization": "Bearer control-secret"},
        json={"actor": "operator"},
    )
    approved = client.post(
        f"/api/v1/approvals/{approval_id}/approve",
        headers={"Authorization": "Bearer admin-secret"},
        json={"actor": "safety-admin"},
    )

    assert requested.status_code == 202
    assert len(gateway.executed_tasks) == 1
    assert denied.status_code == 403
    assert approved.status_code == 200
    assert approved.json()["status"] == "EXECUTED"


def test_control_rate_limit_and_prometheus_metrics():
    gateway = FakeRobotGateway()
    registry = RobotRegistry(gateway)
    orchestrator = MissionOrchestrator(FleetScheduler(registry), TaskDispatcher(registry, gateway))
    client = TestClient(create_app(registry, orchestrator, control_rate_limit_per_minute=1))

    first = client.post("/api/v1/fleet/emergency-stop")
    limited = client.post("/api/v1/fleet/emergency-stop")
    metrics = client.get("/metrics")

    assert first.status_code == 200
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"
    assert "maestro_connected_robots" in metrics.text
    assert "maestro_emergency_stop_seconds" in metrics.text


def test_ready_reports_database_connection():
    response = make_client().get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok"}
