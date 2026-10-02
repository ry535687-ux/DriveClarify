"""Fail-closed prospective scientific scene certificate validation."""

from __future__ import annotations

from typing import Any, Mapping

from .measurement import canonical_sha256


CERTIFIED_STATUS = "CERTIFIED"
REJECTION_STATUSES = frozenset(
    {
        "REJECTED_IMPLEMENTATION_IMPOSSIBLE",
        "REJECTED_SEMANTICALLY_NOT_AMBIGUOUS",
        "REJECTED_NO_DISTINCT_PLANNING_CONSEQUENCE",
        "REJECTED_COMMITMENT_NOT_CERTIFIABLE",
        "REJECTED_NATIVE_ROUTE_INVALID",
        "REJECTED_ORACLE_BOUNDARY_INVALID",
        "REJECTED_SEMANTIC_ASSET_NOT_EXACTLY_VERIFIABLE",
    }
)
REQUIRED_TOP_LEVEL = (
    "scene_id",
    "candidate_template_id",
    "version",
    "ambiguity_family",
    "admission_status",
    "original_ambiguous_instruction",
    "reasonable_interpretation_set",
    "semantic_ambiguity_certificate",
    "planning_relevance_certificate",
    "map_identity",
    "route_identity",
    "route_sha256",
    "scenario_configuration_sha256",
    "road_lane_junction_identities",
    "topology_certificate",
    "commitment_point_certificate",
    "expected_observation_interval_before_commitment",
    "query_necessity_gold_provenance",
    "oracle_runtime_information_boundary",
    "native_viability_certificate",
    "not_label_only_reason",
    "performance_independent_selection_reason",
)
CERTIFICATE_FIELDS = (
    "semantic_ambiguity_certificate",
    "planning_relevance_certificate",
    "topology_certificate",
    "commitment_point_certificate",
    "native_viability_certificate",
)
FORBIDDEN_CERTIFICATION_KEYS = frozenset(
    {
        "policy_outcome",
        "t_accum",
        "t_accum_result",
        "ask_occurred",
        "learned_success",
        "method_performance",
        "opportunity_window_observed",
    }
)
METHOD_V27_COMMITMENT_OWNER = {
    "source_path": "driveclarify_method_v2_7/commitment.py",
    "maneuver_direction_function": "derive_maneuver_direction_boundary",
    "execution_location_function": "derive_execution_location_boundary",
    "maneuver_direction_event_class": "CANDIDATE_EXCLUSIVE_BRANCH_ENTRY_CUT",
    "execution_location_event_class": "ORDERED_QUALIFYING_OPPORTUNITY_CONSUMPTION",
}


def _keys(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            result.add(str(key).lower())
            result.update(_keys(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            result.update(_keys(child))
    return result


def assert_certification_policy_independent(certificate: Mapping[str, Any]) -> None:
    overlap = FORBIDDEN_CERTIFICATION_KEYS.intersection(_keys(certificate))
    if overlap:
        raise PermissionError(
            "RQ2_T_SCENE_CERTIFICATION_POLICY_OUTCOME_INPUT_FORBIDDEN:"
            + sorted(overlap)[0]
        )


def _commitment_owner_valid(value: Any) -> bool:
    if not isinstance(value, Mapping) or value.get("status") != "PASS":
        return False
    owner = value.get("owner")
    if not isinstance(owner, Mapping):
        return False
    if owner.get("source_path") != METHOD_V27_COMMITMENT_OWNER["source_path"]:
        return False
    event = value.get("commitment_event_class")
    if event == METHOD_V27_COMMITMENT_OWNER["maneuver_direction_event_class"]:
        return owner.get("function") == METHOD_V27_COMMITMENT_OWNER["maneuver_direction_function"]
    if event == METHOD_V27_COMMITMENT_OWNER["execution_location_event_class"]:
        return owner.get("function") == METHOD_V27_COMMITMENT_OWNER["execution_location_function"]
    return False


def validate_scene_certificate(certificate: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one attempted template without converting rejection to pending."""

    assert_certification_policy_independent(certificate)
    admission = certificate.get("admission_status")
    if admission in REJECTION_STATUSES:
        result = {
            "status": admission,
            "formal_roster_eligible": False,
            "missing_required_fields": [],
            "failed_certificate_fields": [],
            "exact_map_route_topology_binding_complete": False,
            "structural_rejection_reason": certificate.get("rejection_reason"),
        }
        if not result["structural_rejection_reason"]:
            result["status"] = "PENDING_BINDING_FAIL_CLOSED"
        result["validation_digest"] = canonical_sha256(result)
        return result

    missing = [key for key in REQUIRED_TOP_LEVEL if certificate.get(key) in (None, "", [], {})]
    failed = [
        key
        for key in CERTIFICATE_FIELDS
        if not isinstance(certificate.get(key), Mapping)
        or certificate[key].get("status") != "PASS"
        or not certificate[key].get("provenance_sha256")
    ]
    exact_binding = bool(
        certificate.get("map_identity") not in (None, "", "PENDING_BINDING")
        and certificate.get("route_identity") not in (None, "", "PENDING_BINDING")
        and certificate.get("route_sha256")
        and certificate.get("scenario_configuration_sha256")
        and certificate.get("road_lane_junction_identities")
    )
    commitment_owner_matches = _commitment_owner_valid(
        certificate.get("commitment_point_certificate")
    )
    if (
        admission != CERTIFIED_STATUS
        or missing
        or failed
        or not exact_binding
        or not commitment_owner_matches
    ):
        status = "PENDING_BINDING_FAIL_CLOSED"
        eligible = False
    else:
        status = "FULLY_CERTIFIED_PROSPECTIVE"
        eligible = True
    result = {
        "status": status,
        "formal_roster_eligible": eligible,
        "missing_required_fields": missing,
        "failed_certificate_fields": failed,
        "exact_map_route_topology_binding_complete": exact_binding,
        "method_v2_7_commitment_owner_matches": commitment_owner_matches,
    }
    result["validation_digest"] = canonical_sha256(result)
    return result


def validate_roster_gate(certificates: list[Mapping[str, Any]]) -> dict[str, Any]:
    family_counts = {
        family: 0
        for family in (
            "REFERENTIAL",
            "LANDMARK",
            "ORDER",
            "UNDERSPECIFIED_CONSTRAINT",
        )
    }
    certified: list[str] = []
    rejected: list[dict[str, str]] = []
    for certificate in certificates:
        validation = validate_scene_certificate(certificate)
        scene = str(certificate.get("candidate_template_id", ""))
        if validation["formal_roster_eligible"]:
            certified.append(scene)
            family_counts[str(certificate["ambiguity_family"])] += 1
        else:
            rejected.append({"scene_id": scene, "status": str(validation["status"])})
    passed = len(certified) >= 8 and all(value >= 2 for value in family_counts.values())
    result = {
        "status": "PASS_FINAL_ROSTER_GATE" if passed else "FAIL_FINAL_ROSTER_GATE",
        "passed": passed,
        "attempted_count": len(certificates),
        "certified_count": len(certified),
        "family_counts": family_counts,
        "certified_scene_ids": certified,
        "rejected_or_pending": rejected,
        "minimum_rule": "CERTIFIED_COUNT_GE_8_AND_EACH_OF_4_FAMILIES_GE_2",
    }
    result["gate_digest"] = canonical_sha256(result)
    return result


__all__ = [
    "CERTIFIED_STATUS",
    "METHOD_V27_COMMITMENT_OWNER",
    "REJECTION_STATUSES",
    "REQUIRED_TOP_LEVEL",
    "assert_certification_policy_independent",
    "validate_roster_gate",
    "validate_scene_certificate",
]
