"""Gold-free R1 orchestration boundary.

The tested policy receives only the original runtime case. Membership and
commitment metadata are appended after a policy result returns.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Mapping

from .r1_contracts import RAW_PREDICTION_FIELDS, policy_input_leak_paths, validate_raw_prediction_record


def policy_input_from_runtime_case(case: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(case)
    leaks = policy_input_leak_paths(result)
    if leaks:
        raise ValueError("R1_POLICY_INPUT_MEMBERSHIP_OR_GOLD_LEAK:" + ",".join(leaks[:10]))
    return result


def attach_control_plane_fields(
    policy_record: Mapping[str, Any], partition: Mapping[str, Any], *,
    comparison_id: str, comparison_index: int, hashes: Mapping[str, str],
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "case_id": policy_record["case_id"],
        "canonical_case_index": partition["canonical_execution_index"],
        "track": partition["track"],
        "track_r": partition["track_r"],
        "track_s_full_pool": partition["track_s_full_pool"],
        "track_s_primary_core": partition["track_s_primary_core"],
        "boundary_stress": partition["boundary_stress"],
        "core_nonboundary": partition["core_nonboundary"],
        "matrix_archetype_id": partition["matrix_archetype_id"],
        "profile_id": partition["profile_id"],
        "comparison_id": comparison_id,
        "canonical_comparison_index": comparison_index,
        "selected_action": policy_record["selected_action"],
        "selected_candidate_id": policy_record.get("selected_candidate_id"),
        "act_subtype": policy_record["act_subtype"],
        "reason_codes": list(policy_record["decision_reason_codes"]),
        "legal_action_mask": dict(policy_record["legal_action_mask"]),
        "R_act_A": policy_record.get("r_act_a"),
        "R_act_B": policy_record.get("r_act_b"),
        "R_ask": policy_record.get("r_ask"),
        "R_wait": policy_record.get("r_wait"),
        "V_ask": policy_record.get("v_ask"),
        "V_wait": policy_record.get("v_wait"),
        "posterior_summary": policy_record.get("posterior_summary", []),
        "query_episode_state": policy_record["query_episode_state"],
        "matrix_sha256": policy_record["matrix_sha256"],
        "profile_sha256": policy_record["profile_sha256"],
        "partition_manifest_sha256": hashes["partition_manifest_sha256"],
        "protocol_sha256": hashes["protocol_sha256"],
        "runtime_package_sha256": hashes["runtime_package_sha256"],
        "comparison_set_sha256": hashes["comparison_set_sha256"],
        "prediction_schema_sha256": hashes["prediction_schema_sha256"],
        "authorization_eligible": False,
        "used_for_control": False,
        "control_authorized": False,
        "override_applied": False,
    }
    record = OrderedDict((name, values[name]) for name in RAW_PREDICTION_FIELDS)
    validate_raw_prediction_record(record)
    return dict(record)
