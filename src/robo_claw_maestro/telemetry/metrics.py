"""Prometheus metrics owned by one Maestro application instance."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest


class MaestroMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.connected_robots = Gauge(
            "maestro_connected_robots",
            "Number of robots currently online",
            registry=self.registry,
        )
        self.websocket_clients = Gauge(
            "maestro_websocket_clients",
            "Connected fleet event WebSocket clients",
            registry=self.registry,
        )
        self.grpc_reconnects = Counter(
            "maestro_grpc_reconnect_total",
            "Robot status stream reconnect attempts",
            ["robot_id"],
            registry=self.registry,
        )
        self.task_results = Counter(
            "maestro_task_result_total",
            "Task dispatch outcomes",
            ["status"],
            registry=self.registry,
        )
        self.emergency_stop_latency = Histogram(
            "maestro_emergency_stop_seconds",
            "Emergency stop request latency",
            registry=self.registry,
        )
        self.llm_requests = Gauge(
            "maestro_llm_requests_total_snapshot",
            "Current process LLM request counter snapshot",
            registry=self.registry,
        )
        self.llm_validation_errors = Gauge(
            "maestro_llm_validation_errors_total_snapshot",
            "Current process LLM validation error snapshot",
            registry=self.registry,
        )

    def render(self) -> bytes:
        return generate_latest(self.registry)
