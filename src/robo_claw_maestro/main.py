"""Production API composition root."""

from __future__ import annotations

import logging

from robo_claw_maestro.adapters.database.migrations import upgrade_database
from robo_claw_maestro.adapters.database.sqlmodel import (
    SqlModelApprovalRepository,
    SqlModelCommandRepository,
    SqlModelEventRepository,
    SqlModelMissionRepository,
    SqlModelRobotRepository,
    SqlModelStore,
    SqlResourceLockManager,
)
from robo_claw_maestro.adapters.grpc.fleet_control_server import (
    FleetControlHub,
    FleetControlServer,
    FleetPreferredGateway,
)
from robo_claw_maestro.adapters.grpc.multi_robot import MultiRobotGateway
from robo_claw_maestro.adapters.llm.factory import build_llm_provider
from robo_claw_maestro.agent.maestro_agent import MaestroAgent
from robo_claw_maestro.agent.planner import MissionPlanParser
from robo_claw_maestro.api.app import create_app
from robo_claw_maestro.application.approval_service import ApprovalService
from robo_claw_maestro.application.mission_orchestrator import MissionOrchestrator
from robo_claw_maestro.application.reconciliation import ReconciliationService
from robo_claw_maestro.application.robot_monitor import RobotMonitor
from robo_claw_maestro.application.robot_registry import RobotRegistry
from robo_claw_maestro.application.scheduler import FleetScheduler
from robo_claw_maestro.application.task_dispatcher import TaskDispatcher
from robo_claw_maestro.config import Settings
from robo_claw_maestro.domain.robot import Capability, Robot
from robo_claw_maestro.logging import configure_logging
from robo_claw_maestro.telemetry.metrics import MaestroMetrics
from robo_claw_maestro.telemetry.tracing import init_tracing


def build_app():
    """Wire adapters and application services.

    Robots with an ``endpoint`` use one RosGrpc channel each. API startup does
    not contact robots or issue physical commands; synchronization is explicit.
    """
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    logging.getLogger(__name__).info(init_tracing())
    if settings.auto_create_schema:
        upgrade_database(settings.database_url)
    store = SqlModelStore(settings.database_url)
    endpoints = {
        str(item["robot_id"]): str(item["endpoint"])
        for item in settings.robots
        if item.get("robot_id") and item.get("endpoint")
    }
    ros_gateway = MultiRobotGateway.from_endpoints(endpoints) if endpoints else MultiRobotGateway()
    fleet_hub = FleetControlHub() if settings.fleet_control_enabled else None
    gateway = FleetPreferredGateway(fleet_hub, ros_gateway) if fleet_hub else ros_gateway
    robot_repository = SqlModelRobotRepository(store)
    event_repository = SqlModelEventRepository(store)
    registry = RobotRegistry(
        gateway,
        heartbeat_timeout_sec=settings.heartbeat_timeout_sec,
        repository=robot_repository,
        event_repository=event_repository,
    )
    if fleet_hub is not None:
        fleet_hub.attach_registry(registry)

    for item in settings.robots:
        robot_id = str(item.get("robot_id", "")).strip()
        if not robot_id or robot_repository.get(robot_id) is not None:
            continue
        capabilities = {str(name): Capability(str(name)) for name in item.get("capabilities", [])}
        registry.register(
            Robot(
                robot_id=robot_id,
                display_name=str(item.get("display_name") or robot_id),
                robot_type=str(item.get("robot_type") or "unknown"),
                site_id=str(item.get("site_id") or ""),
                map_id=str(item.get("map_id") or ""),
                map_version=str(item.get("map_version") or ""),
                frame_id=str(item.get("frame_id") or "map"),
                capabilities=capabilities,
            )
        )
    metrics = MaestroMetrics()
    dispatcher = TaskDispatcher(
        registry,
        gateway,
        command_repository=SqlModelCommandRepository(store),
        metrics=metrics,
    )
    orchestrator = MissionOrchestrator(
        FleetScheduler(registry),
        dispatcher,
        mission_repository=SqlModelMissionRepository(store),
        event_repository=event_repository,
        resource_locks=SqlResourceLockManager(store),
    )
    approval_service = ApprovalService(
        registry,
        SqlModelApprovalRepository(store),
        event_repository,
        allowed_skills=set(settings.allowed_skills)
        if settings.allowed_skills is not None
        else None,
    )
    reconciliation = ReconciliationService(registry)

    async def startup_reconcile() -> None:
        await orchestrator.reconcile_all(reconciliation)

    maestro_agent = None
    llm_provider = build_llm_provider(settings)
    if llm_provider is not None:
        static_capabilities = {
            str(capability)
            for robot in settings.robots
            for capability in robot.get("capabilities", [])
        }

        # Robots report capabilities on FleetControl registration, so read the registry live.
        def fleet_capabilities() -> set[str]:
            return static_capabilities | {
                name for robot in registry.list() for name in robot.capabilities
            }

        def fleet_robot_ids() -> set[str]:
            return set(endpoints) | {robot.robot_id for robot in registry.list()}

        def fleet_catalog() -> list[dict[str, object]]:
            return [
                {
                    "robot_id": robot.robot_id,
                    "robot_type": robot.robot_type,
                    "connection_state": str(robot.connection_state),
                    "site_id": robot.site_id,
                    "map_id": robot.map_id,
                    "capabilities": sorted(robot.capabilities),
                }
                for robot in registry.list()
            ]

        maestro_agent = MaestroAgent(
            llm_provider,
            MissionPlanParser(fleet_capabilities, known_robot_ids=fleet_robot_ids),
            timeout_sec=settings.llm_timeout_sec,
            fleet_catalog=fleet_catalog,
        )
    fleet_server = None
    if fleet_hub is not None:
        fleet_server = FleetControlServer(
            fleet_hub,
            host=settings.fleet_control_host,
            port=settings.fleet_control_port,
            certificate_path=settings.fleet_tls_certificate,
            private_key_path=settings.fleet_tls_private_key,
            client_ca_path=settings.fleet_tls_client_ca,
        )
    return create_app(
        registry,
        orchestrator,
        readiness_check=store.healthcheck,
        read_token=settings.read_token,
        control_token=settings.control_token,
        admin_token=settings.admin_token,
        maestro_agent=maestro_agent,
        robot_monitor=RobotMonitor(registry, gateway, metrics=metrics),
        startup_hook=startup_reconcile,
        fleet_control_server=fleet_server,
        metrics=metrics,
        control_rate_limit_per_minute=settings.control_rate_limit_per_minute,
        event_retention_days=settings.event_retention_days,
        approval_service=approval_service,
    )


app = build_app()
