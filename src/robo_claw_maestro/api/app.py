"""Optional FastAPI application for the first Maestro vertical slice."""

import asyncio
import hmac
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from robo_claw_maestro.adapters.grpc.fleet_control_server import FleetControlServer
from robo_claw_maestro.agent.maestro_agent import MaestroAgent, ReplanningBudgetExceeded
from robo_claw_maestro.agent.planner import PlanValidationError
from robo_claw_maestro.application.approval_service import ApprovalService
from robo_claw_maestro.application.mission_orchestrator import MissionOrchestrator
from robo_claw_maestro.application.robot_monitor import RobotMonitor
from robo_claw_maestro.application.robot_registry import RobotRegistry
from robo_claw_maestro.domain.approval import ApprovalRequest
from robo_claw_maestro.domain.event import DomainEvent
from robo_claw_maestro.domain.mission import Mission
from robo_claw_maestro.domain.task import Task
from robo_claw_maestro.telemetry.metrics import MaestroMetrics
from robo_claw_maestro.telemetry.tracing import (
    process_inputs,
    process_outputs,
    set_current_metadata,
    traceable,
)


@traceable(
    run_type="chain",
    name="maestro_api_request",
    tags=["component:maestro", "boundary:http"],
    process_inputs=process_inputs,
    process_outputs=process_outputs,
)
async def _trace_api_request(method: str, path: str, request, call_next):
    set_current_metadata({"component": "maestro", "http_method": method, "http_path": path})
    return await call_next(request)


def _robot_json(robot: Any) -> dict[str, Any]:
    data = asdict(robot)
    data["connection_state"] = robot.connection_state.value
    data["operational_state"] = robot.operational_state.value
    data["last_seen_at"] = robot.last_seen_at.isoformat() if robot.last_seen_at else None
    data["capabilities"] = sorted(robot.capabilities)
    return data


def _task_json(task: Task) -> dict[str, Any]:
    data = asdict(task)
    data["status"] = task.status.value
    data["required_capabilities"] = sorted(task.required_capabilities)
    data["depends_on"] = sorted(task.depends_on)
    return data


def _approval_json(approval: ApprovalRequest) -> dict[str, Any]:
    data = asdict(approval)
    data["status"] = approval.status.value
    for name in ("created_at", "expires_at", "decided_at"):
        value = getattr(approval, name)
        data[name] = value.isoformat() if value else None
    return data


def _event_json(event: DomainEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "occurred_at": event.occurred_at.isoformat(),
        "payload": event.payload,
        "actor": event.actor,
        "correlation_id": event.correlation_id,
    }


def _mission_json(mission: Mission) -> dict[str, Any]:
    data = asdict(mission)
    data["status"] = mission.status.value
    data["tasks"] = [_task_json(task) for task in mission.tasks.values()]
    return data


def create_app(
    registry: RobotRegistry,
    orchestrator: MissionOrchestrator,
    readiness_check: Callable[[], bool] | None = None,
    *,
    read_token: str = "",
    control_token: str = "",
    admin_token: str = "",
    maestro_agent: MaestroAgent | None = None,
    robot_monitor: RobotMonitor | None = None,
    startup_hook: Callable[[], Awaitable[None]] | None = None,
    fleet_control_server: FleetControlServer | None = None,
    metrics: MaestroMetrics | None = None,
    control_rate_limit_per_minute: int = 120,
    event_retention_days: int = 30,
    approval_service: ApprovalService | None = None,
):
    """Create the HTTP app.

    FastAPI is an optional dependency so domain tests do not require a web
    framework. Install ``.[api]`` to run this adapter.
    """
    if control_rate_limit_per_minute < 1:
        raise ValueError("control_rate_limit_per_minute must be positive")
    if event_retention_days < 1:
        raise ValueError("event_retention_days must be positive")
    try:
        from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
        from fastapi.responses import JSONResponse
        from pydantic import BaseModel, Field
    except ImportError as exc:  # pragma: no cover - exercised in deployment
        raise RuntimeError("install robo-claw-maestro[api] to use the HTTP API") from exc

    class TaskInput(BaseModel):
        task_id: str
        instruction: str
        task_type: str = "instruction"
        skill_name: str = ""
        params: dict[str, Any] = Field(default_factory=dict)
        required_capabilities: set[str] = Field(default_factory=set)
        depends_on: set[str] = Field(default_factory=set)
        priority: int = 0
        timeout_sec: float = 300.0
        max_attempts: int = 1

    class DirectTaskInput(BaseModel):
        instruction: str = Field(min_length=1, max_length=4000)
        required_capabilities: set[str] = Field(default_factory=set)
        timeout_sec: float = Field(default=300.0, gt=0, le=3600)
        max_attempts: int = Field(default=1, ge=1, le=5)

    class SkillInput(BaseModel):
        params: dict[str, Any] = Field(default_factory=dict)
        timeout_sec: float = Field(default=30.0, gt=0, le=3600)
        requested_by: str = Field(default="api", min_length=1, max_length=200)

    class ApprovalDecisionInput(BaseModel):
        actor: str = Field(min_length=1, max_length=200)

    class GoalInput(BaseModel):
        goal: str = Field(min_length=1, max_length=8000)
        created_by: str = Field(default="api", min_length=1, max_length=200)
        auto_start: bool = False

    class ReplanInput(BaseModel):
        failure_context: dict[str, Any] = Field(default_factory=dict)
        created_by: str = Field(default="api", min_length=1, max_length=200)

    class MissionInput(BaseModel):
        mission_id: str
        goal: str
        priority: int = 0
        created_by: str = "api"
        tasks: list[TaskInput] = Field(default_factory=list)

    async def retention_loop() -> None:
        while True:
            registry.cleanup_events(event_retention_days)
            await asyncio.sleep(3600)

    @asynccontextmanager
    async def lifespan(_app):
        stop = asyncio.Event()
        tasks: list[asyncio.Task] = []
        fleet_started = False
        try:
            if fleet_control_server is not None:
                await fleet_control_server.start()
                fleet_started = True
            if startup_hook is not None:
                await startup_hook()
            tasks.append(asyncio.create_task(retention_loop()))
            if robot_monitor is not None:
                tasks.append(asyncio.create_task(robot_monitor.run_fleet(stop)))
            yield
        finally:
            stop.set()
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task
            if robot_monitor is not None:
                await robot_monitor.close()
            if fleet_started and fleet_control_server is not None:
                await fleet_control_server.stop()

    metrics = metrics or MaestroMetrics()
    control_requests: dict[str, deque[float]] = defaultdict(deque)
    app = FastAPI(title="Robo-Claw Maestro", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def authorize(request, call_next):
        if request.url.path in {"/health", "/ready", "/metrics", "/docs", "/openapi.json"}:
            return await call_next(request)
        header = request.headers.get("authorization", "")
        supplied = header[7:] if header.startswith("Bearer ") else ""
        if not read_token and not control_token and not admin_token:
            allowed = True
        else:
            is_admin = bool(admin_token) and hmac.compare_digest(supplied, admin_token)
            is_control = bool(control_token) and hmac.compare_digest(supplied, control_token)
            is_read = bool(read_token) and hmac.compare_digest(supplied, read_token)
            approval_decision = request.method == "POST" and request.url.path.startswith(
                "/api/v1/approvals/"
            )
            allowed = is_admin or (
                not approval_decision and (is_control or (request.method == "GET" and is_read))
            )
        if not allowed:
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            client = request.client.host if request.client else "unknown"
            key = supplied or client
            now = time.monotonic()
            bucket = control_requests[key]
            while bucket and now - bucket[0] >= 60:
                bucket.popleft()
            if len(bucket) >= control_rate_limit_per_minute:
                return JSONResponse(
                    {"detail": "control rate limit exceeded"},
                    status_code=429,
                    headers={"Retry-After": "60"},
                )
            bucket.append(now)
        traced_path = request.url.path.startswith(("/api/v1/goals", "/api/v1/missions"))
        if traced_path and request.method != "GET":
            return await _trace_api_request(request.method, request.url.path, request, call_next)
        return await call_next(request)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Report process and database health without hiding partial failure."""
        database = "ok" if readiness_check is None or readiness_check() else "unavailable"
        return {"status": "ok", "database": database}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        """Readiness check that includes the configured persistence backend."""
        if readiness_check is not None and not readiness_check():
            raise HTTPException(status_code=503, detail="database unavailable")
        return {"status": "ready", "database": "ok"}

    @app.get("/metrics")
    async def prometheus_metrics():
        from fastapi import Response

        online = sum(robot.connection_state.value == "ONLINE" for robot in registry.list())
        metrics.connected_robots.set(online)
        if maestro_agent is not None:
            snapshot = maestro_agent.metrics.snapshot()
            metrics.llm_requests.set(snapshot["requests"])
            metrics.llm_validation_errors.set(snapshot["validation_errors"])
        return Response(metrics.render(), media_type="text/plain; version=0.0.4")

    @app.get("/api/v1/robots")
    async def robots() -> list[dict[str, Any]]:
        items = registry.list()
        metrics.connected_robots.set(
            sum(robot.connection_state.value == "ONLINE" for robot in items)
        )
        return [_robot_json(robot) for robot in items]

    @app.get("/api/v1/robots/{robot_id}")
    async def robot(robot_id: str) -> dict[str, Any]:
        try:
            return _robot_json(registry.get(robot_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/robots/synchronize")
    async def synchronize_robots() -> list[dict[str, Any]]:
        return [_robot_json(robot) for robot in await registry.synchronize_all()]

    @app.get("/api/v1/robots/{robot_id}/events")
    async def robot_events(robot_id: str) -> list[dict[str, Any]]:
        try:
            registry.get(robot_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return [_event_json(event) for event in registry.events.list(robot_id)]

    @app.post("/api/v1/robots/{robot_id}/tasks")
    async def execute_robot_task(robot_id: str, payload: DirectTaskInput) -> dict[str, Any]:
        try:
            registry.get(robot_id)
            suffix = uuid4().hex
            mission = Mission(f"direct-{suffix}", payload.instruction, created_by="api")
            mission.add_task(
                Task(
                    task_id=f"task-{suffix}",
                    mission_id=mission.mission_id,
                    instruction=payload.instruction,
                    params={"preferred_robot_id": robot_id},
                    required_capabilities=payload.required_capabilities,
                    timeout_sec=payload.timeout_sec,
                    max_attempts=payload.max_attempts,
                )
            )
            orchestrator.create(mission)
            await orchestrator.run_round(mission.mission_id)
            return _mission_json(mission)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/robots/{robot_id}/skills/{skill_name}")
    async def execute_skill(robot_id: str, skill_name: str, payload: SkillInput) -> dict[str, Any]:
        try:
            if approval_service is None:
                result = await registry.execute_skill(
                    robot_id, skill_name, payload.params, payload.timeout_sec
                )
            else:
                result = await approval_service.request_skill(
                    robot_id,
                    skill_name,
                    payload.params,
                    payload.timeout_sec,
                    payload.requested_by,
                )
            if isinstance(result, ApprovalRequest):
                return JSONResponse(
                    {"approval_required": True, "approval": _approval_json(result)},
                    status_code=202,
                )
            return asdict(result)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/v1/approvals")
    async def approvals(pending_only: bool = True) -> list[dict[str, Any]]:
        if approval_service is None:
            return []
        return [_approval_json(item) for item in approval_service.list(pending_only=pending_only)]

    @app.post("/api/v1/approvals/{approval_id}/approve")
    async def approve_request(approval_id: str, payload: ApprovalDecisionInput) -> dict[str, Any]:
        if approval_service is None:
            raise HTTPException(status_code=503, detail="approval service is not configured")
        try:
            return _approval_json(await approval_service.approve(approval_id, payload.actor))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/approvals/{approval_id}/reject")
    async def reject_request(approval_id: str, payload: ApprovalDecisionInput) -> dict[str, Any]:
        if approval_service is None:
            raise HTTPException(status_code=503, detail="approval service is not configured")
        try:
            return _approval_json(approval_service.reject(approval_id, payload.actor))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/robots/{robot_id}/snapshot")
    async def robot_snapshot(robot_id: str) -> dict[str, Any]:
        try:
            return asdict(await registry.request_snapshot(robot_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/v1/robots/{robot_id}/emergency-stop")
    async def emergency_stop_robot(robot_id: str) -> dict[str, Any]:
        try:
            return asdict(await registry.emergency_stop(robot_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/v1/fleet/emergency-stop")
    async def emergency_stop() -> dict[str, Any]:
        started = time.monotonic()
        result = await registry.emergency_stop_all()
        metrics.emergency_stop_latency.observe(time.monotonic() - started)
        return {
            "requested": result.requested,
            "stopped": list(result.stopped),
            "failed": result.failed,
        }

    @app.post("/api/v1/goals")
    async def create_goal(payload: GoalInput) -> dict[str, Any]:
        if maestro_agent is None:
            raise HTTPException(status_code=503, detail="LLM planner is not configured")
        try:
            mission = await maestro_agent.create_plan(payload.goal, created_by=payload.created_by)
            orchestrator.create(mission)
            if payload.auto_start:
                orchestrator.start(mission.mission_id)
            return _mission_json(mission)
        except PlanValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="LLM provider timeout") from exc

    @app.get("/api/v1/planning/metrics")
    async def planning_metrics() -> dict[str, int | float]:
        if maestro_agent is None:
            raise HTTPException(status_code=503, detail="LLM planner is not configured")
        return maestro_agent.metrics.snapshot()

    @app.get("/api/v1/missions")
    async def missions() -> list[dict[str, Any]]:
        return [_mission_json(mission) for mission in orchestrator.list()]

    @app.post("/api/v1/missions")
    async def create_mission(payload: MissionInput) -> dict[str, Any]:
        try:
            mission = Mission(
                mission_id=payload.mission_id,
                goal=payload.goal,
                priority=payload.priority,
                created_by=payload.created_by,
            )
            mission.add_tasks(
                Task(
                    task_id=item.task_id,
                    mission_id=payload.mission_id,
                    instruction=item.instruction,
                    task_type=item.task_type,
                    skill_name=item.skill_name,
                    params=item.params,
                    required_capabilities=item.required_capabilities,
                    depends_on=item.depends_on,
                    priority=item.priority,
                    timeout_sec=item.timeout_sec,
                    max_attempts=item.max_attempts,
                )
                for item in payload.tasks
            )
            return _mission_json(orchestrator.create(mission))
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/missions/{mission_id}")
    async def mission(mission_id: str) -> dict[str, Any]:
        try:
            return _mission_json(orchestrator.get(mission_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/missions/{mission_id}/events")
    async def mission_events(mission_id: str) -> list[dict[str, Any]]:
        try:
            orchestrator.get(mission_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return [_event_json(event) for event in orchestrator.events.list(mission_id)]

    @app.post("/api/v1/missions/{mission_id}/start")
    async def start_mission(mission_id: str) -> dict[str, Any]:
        try:
            return _mission_json(orchestrator.start(mission_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/missions/{mission_id}/pause")
    async def pause_mission(mission_id: str) -> dict[str, Any]:
        try:
            return _mission_json(orchestrator.pause(mission_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/missions/{mission_id}/resume")
    async def resume_mission(mission_id: str) -> dict[str, Any]:
        try:
            return _mission_json(orchestrator.resume(mission_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/missions/{mission_id}/replan")
    async def replan_mission(mission_id: str, payload: ReplanInput) -> dict[str, Any]:
        if maestro_agent is None:
            raise HTTPException(status_code=503, detail="LLM planner is not configured")
        try:
            current = orchestrator.get(mission_id)
            replacement = await maestro_agent.replan(
                current,
                payload.failure_context,
                created_by=payload.created_by,
            )
            return _mission_json(orchestrator.replace_plan(replacement))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ReplanningBudgetExceeded as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PlanValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="LLM provider timeout") from exc

    @app.post("/api/v1/missions/{mission_id}/cancel")
    async def cancel_mission(mission_id: str) -> dict[str, Any]:
        try:
            return _mission_json(orchestrator.cancel(mission_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/missions/{mission_id}/run")
    async def run_mission(mission_id: str) -> dict[str, Any]:
        try:
            result = await orchestrator.run_round(mission_id)
            return {
                "mission": _mission_json(orchestrator.get(mission_id)),
                "assignments": [asdict(item) for item in result.assignments],
                "blocked_task_ids": list(result.blocked_task_ids),
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.websocket("/api/v1/events/stream")
    async def event_stream(websocket: WebSocket) -> None:
        if read_token or control_token or admin_token:
            supplied = websocket.query_params.get("token", "")
            authorized = (
                (bool(read_token) and hmac.compare_digest(supplied, read_token))
                or (bool(control_token) and hmac.compare_digest(supplied, control_token))
                or (bool(admin_token) and hmac.compare_digest(supplied, admin_token))
            )
            if not authorized:
                await websocket.close(code=4403)
                return
        await websocket.accept()
        metrics.websocket_clients.inc()
        sent: set[str] = set()
        try:
            while True:
                events = registry.events.list() + orchestrator.events.list()
                for event in sorted(events, key=lambda item: (item.occurred_at, item.event_id)):
                    if event.event_id not in sent:
                        await websocket.send_json(_event_json(event))
                        sent.add(event.event_id)
                await asyncio.sleep(0.25)
        except WebSocketDisconnect:
            return
        finally:
            metrics.websocket_clients.dec()

    return app
