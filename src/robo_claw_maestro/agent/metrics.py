"""Process-local LLM planning metrics snapshot."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PlanningMetrics:
    requests: int = 0
    succeeded: int = 0
    provider_errors: int = 0
    validation_errors: int = 0
    replans: int = 0
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    total_latency_sec: float = 0.0

    @staticmethod
    def estimate_tokens(text: str) -> int:
        return max(1, (len(text) + 3) // 4) if text else 0

    def snapshot(self) -> dict[str, int | float]:
        return {
            "requests": self.requests,
            "succeeded": self.succeeded,
            "provider_errors": self.provider_errors,
            "validation_errors": self.validation_errors,
            "replans": self.replans,
            "estimated_input_tokens": self.estimated_input_tokens,
            "estimated_output_tokens": self.estimated_output_tokens,
            "total_latency_sec": self.total_latency_sec,
        }
