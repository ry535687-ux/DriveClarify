"""UNKNOWN-preserving consequence envelope for offline candidate diagnostics."""

from __future__ import annotations

from typing import Any, Dict

from .types import CandidateBatchContext, GateResult


def _unknown(reason: str) -> Dict[str, Any]:
    return {
        "status": "UNKNOWN",
        "value": None,
        "unit": None,
        "source": None,
        "reason": reason,
    }


class ConsequenceEvaluator:
    """Build diagnostics only; it implements no physical risk algorithm."""

    def evaluate_diagnostics(
        self,
        context: CandidateBatchContext,
        gate: GateResult,
    ) -> Dict[str, Any]:
        channels = gate.metrics.get("channels", {})
        route = channels.get("route")
        speed = channels.get("speed")
        return {
            "result_type": "DRIVECLARIFY_OFFLINE_CONSEQUENCE_ENVELOPE_V1",
            "offline_only": True,
            "source_run_id": context.run_id,
            "observation_id": context.observation_id,
            "source_frame": context.source_frame,
            "stability_gate_status": gate.status.value,
            "eligible_for_decision_evaluation": gate.decision_evaluation_allowed,
            "decision_fields_evaluated": False,
            "route_divergence": (
                {
                    "status": "AVAILABLE",
                    "unit": "MODEL_RAW_L2",
                    "within_A": route["within_A"],
                    "within_B": route["within_B"],
                    "between_repetition_means": route["between"],
                    "separation_ratio": route["separation_ratio"],
                    "margin": route["margin"],
                    "classification": route["classification"],
                }
                if route is not None
                else _unknown("candidate stability metrics unavailable")
            ),
            "speed_divergence": (
                {
                    "status": "AVAILABLE",
                    "unit": "MODEL_RAW_L2",
                    "within_A": speed["within_A"],
                    "within_B": speed["within_B"],
                    "between_repetition_means": speed["between"],
                    "separation_ratio": speed["separation_ratio"],
                    "margin": speed["margin"],
                    "classification": speed["classification"],
                }
                if speed is not None
                else _unknown("candidate stability metrics unavailable")
            ),
            "goal": _unknown(
                "no verified topology/goal transform in recorded candidate evidence"
            ),
            "rule": _unknown(
                "no verified map/rule linkage in recorded candidate evidence"
            ),
            "risk": {
                "status": "UNKNOWN",
                "collision": None,
                "ttc": None,
                "clearance": None,
                "reason": "no physical transform, actor rollout or validated risk model",
            },
            "timing": _unknown(
                "per-forward and consequence latency were not recorded by psplan13"
            ),
            "recoverability": _unknown(
                "no verified topology or holding capability in recorded evidence"
            ),
            "control_authorization": False,
            "unknown_is_not_zero_or_safe": True,
        }
