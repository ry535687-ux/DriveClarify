"""Fail-closed admission checks for an independently authorized seed stage."""

from __future__ import annotations

from typing import Any, Mapping

from driveclarify_rq2_t.measurement import canonical_sha256


REQUIRED_PREDECESSOR_STATUS = "PASS_CONTROLLED_GROUNDING_TEMPORAL_MEMORY_MECHANISM_READY_FOR_FORMAL_SCENE_FREEZE_REVIEW"
READY_STATUS = "PASS_RQ2_T_CG_FORMAL_SCENE_AND_PROTOCOL_FREEZE_READY_FOR_INDEPENDENT_SEED_AUTHORIZATION"


def require_predecessor_gate(receipt: Mapping[str, Any]) -> None:
    if (
        receipt.get("predecessor_primary_status") != REQUIRED_PREDECESSOR_STATUS
        or receipt.get("gate_pass") is not True
        or receipt.get("failed_checks") != []
    ):
        raise PermissionError("PREDECESSOR_GATE_NOT_SATISFIED_NO_FORMAL_SCENE_FREEZE")


def decide_preseed_admission(checks: Mapping[str, Any]) -> Mapping[str, Any]:
    required = (
        "predecessor_raw_gate_pass", "exact_eight_formal_scenes", "formal_scene_freshness_pass",
        "candidate_certificates_complete", "event_contracts_sealed", "view_contract_sealed",
        "rule_contract_sealed", "hypotheses_endpoints_sealed", "analysis_plan_sealed",
        "seed_run_retry_protocols_sealed", "oracle_true_intent_firewall_pass",
        "source_freeze_pass", "dual_environment_tests_pass",
        "formal_seed_values_zero", "formal_roster_rows_zero", "formal_scientific_exposures_zero",
        "native_formal_episodes_zero", "online_ask_zero", "automatic_e2_reopened_zero",
    )
    normalized = {key: checks.get(key) for key in required}
    missing_or_failed = [key for key, value in normalized.items() if value is not True]
    status = READY_STATUS if not missing_or_failed else "BLOCKED_RQ2_T_CG_FORMAL_SCENE_AND_PROTOCOL_FREEZE_NOT_READY"
    result = {
        "schema_version": "driveclarify.rq2_t_cg.preseed_admission.v1",
        "required_checks": normalized, "failed_checks": missing_or_failed,
        "admitted": not missing_or_failed, "status": status,
        "formal_seed_generation_authorized": False,
        "formal_seed_values_generated": 0, "formal_roster_rows_generated": 0,
        "formal_scientific_exposures": 0,
    }
    result["admission_digest"] = canonical_sha256(result)
    return result


__all__ = [
    "READY_STATUS", "REQUIRED_PREDECESSOR_STATUS", "decide_preseed_admission",
    "require_predecessor_gate",
]
