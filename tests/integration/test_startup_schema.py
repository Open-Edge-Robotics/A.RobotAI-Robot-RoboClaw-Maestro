import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from robo_claw_maestro.adapters.database.migrations import upgrade_database
from robo_claw_maestro.adapters.database.sqlmodel import SqlModelStore
from robo_claw_maestro.api.app import create_app
from robo_claw_maestro.application.mission_orchestrator import MissionOrchestrator
from robo_claw_maestro.application.robot_registry import RobotRegistry
from robo_claw_maestro.application.scheduler import FleetScheduler
from robo_claw_maestro.application.task_dispatcher import TaskDispatcher
from robo_claw_maestro.ports.robot_gateway import FakeRobotGateway

pytestmark = pytest.mark.integration


def test_sqlite_store_health_requires_schema_and_can_bootstrap(tmp_path):
    url = f"sqlite:///{tmp_path / 'fresh.db'}"

    assert not SqlModelStore(url).healthcheck()
    upgrade_database(url)
    store = SqlModelStore(url)

    assert store.healthcheck()
    assert "alembic_version" in inspect(store.engine).get_table_names()


def test_auto_migration_recovers_legacy_create_all_database(tmp_path):
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    legacy = SqlModelStore(url, create_tables=True)
    assert "alembic_version" not in inspect(legacy.engine).get_table_names()

    upgrade_database(url)

    assert "alembic_version" in inspect(legacy.engine).get_table_names()


def test_startup_failure_stops_already_started_fleet_server():
    class FakeFleetServer:
        def __init__(self):
            self.started = False
            self.stopped = False

        async def start(self):
            self.started = True

        async def stop(self):
            self.stopped = True

    async def failing_startup():
        raise RuntimeError("migration missing")

    gateway = FakeRobotGateway()
    registry = RobotRegistry(gateway)
    orchestrator = MissionOrchestrator(FleetScheduler(registry), TaskDispatcher(registry, gateway))
    fleet = FakeFleetServer()
    app = create_app(
        registry,
        orchestrator,
        startup_hook=failing_startup,
        fleet_control_server=fleet,
    )

    with pytest.raises(RuntimeError, match="migration missing"):
        with TestClient(app):
            pass

    assert fleet.started
    assert fleet.stopped
