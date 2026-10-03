"""Identity hooks for the supported native model path."""
from __future__ import annotations
from typing import Any

class _InertRuntimeProbe:
    """No-op probe used when disabled or when the harness cannot be imported."""

    enabled = False

    def __init__(self, reason: str = "DISABLED") -> None:
        self.reason = reason
        self.harness_errors = 0

    def on_tick(self, *a: Any, **k: Any) -> None:
        return None

    def on_model_output(self, *a: Any, **k: Any) -> None:
        return None

    def prepare_model_input(self, model_input: Any) -> Any:
        return model_input

    def select_connector_target_window(
        self,
        active_route: Any,
        ego_xyz_m: Any,
        baseline_targets: tuple[Any, Any],
        *a: Any,
        **k: Any,
    ) -> tuple[Any, Any]:
        del active_route, ego_xyz_m, a, k
        return baseline_targets

    def select_plan_source(self, baseline_route: Any, baseline_speed: Any, *a: Any, **k: Any) -> tuple[Any, Any]:
        return baseline_route, baseline_speed

    def on_pid_invocation(self, *a: Any, **k: Any) -> None:
        return None

    def on_candidate_set(self, *a: Any, **k: Any) -> None:
        return None

    def on_baseline_control(self, *a: Any, **k: Any) -> None:
        return None

    def on_m2b_decision(self, *a: Any, **k: Any) -> None:
        return None

    def commit(self, *a: Any, **k: Any) -> None:
        return None

    def close(self, *a: Any, **k: Any) -> None:
        return None

    def audit_summary(self) -> dict[str, Any]:
        return {"enabled": False, "reason": self.reason}

    def should_end_scientific_horizon(self, simulation_elapsed_s: float) -> bool:
        del simulation_elapsed_s
        return False

    def record_execution_terminal(self, state: str, simulation_elapsed_s: float) -> None:
        del state, simulation_elapsed_s
        return None

    def scientific_execution_terminal(self) -> None:
        return None


def build_runtime_probe(agent=None):
    return _InertRuntimeProbe("DISABLED_BY_ENV")
