"""Offline-only ACT/ASK/WAIT/FALLBACK interface and fail-closed reducer."""

from __future__ import annotations

from typing import Any, Dict

from .types import GateResult, GateStatus


class OfflineDecisionStateMachine:
    """Produce a shadow envelope without authorizing a semantic action."""

    interface_actions = ("ACT", "ASK", "WAIT", "FALLBACK")

    def reduce(
        self,
        gate: GateResult,
        consequence: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not gate.decision_evaluation_allowed:
            return {
                "mode": "OFFLINE_ONLY",
                "state": "DIAGNOSTIC_ONLY",
                "interface_actions": list(self.interface_actions),
                "authorized_action": None,
                "shadow_proposal": None,
                "reason_codes": [
                    "CANDIDATE_STABILITY_GATE_BLOCKED",
                    gate.status.value,
                ]
                + list(gate.reason_codes),
                "entered_decision_evaluation": False,
                "act_ask_wait_authorized": False,
                "control_authorized": False,
                "fallback_is_emergency_stop": False,
            }

        hard_unknown = any(
            consequence[field].get("status") == "UNKNOWN"
            for field in ("goal", "rule", "risk", "timing", "recoverability")
        )
        if hard_unknown:
            return {
                "mode": "OFFLINE_ONLY",
                "state": "FALLBACK",
                "interface_actions": list(self.interface_actions),
                "authorized_action": None,
                "shadow_proposal": "FALLBACK_INSUFFICIENT_EVIDENCE",
                "reason_codes": ["HARD_CONSEQUENCE_FIELDS_UNKNOWN"],
                "entered_decision_evaluation": True,
                "act_ask_wait_authorized": False,
                "control_authorized": False,
                "fallback_is_emergency_stop": False,
            }

        # The skeleton intentionally has no path that authorizes a real action.
        return {
            "mode": "OFFLINE_ONLY",
            "state": "DIAGNOSTIC_ONLY",
            "interface_actions": list(self.interface_actions),
            "authorized_action": None,
            "shadow_proposal": None,
            "reason_codes": ["OFFLINE_SKELETON_HAS_NO_ACTION_AUTHORITY"],
            "entered_decision_evaluation": True,
            "act_ask_wait_authorized": False,
            "control_authorized": False,
            "fallback_is_emergency_stop": False,
        }
