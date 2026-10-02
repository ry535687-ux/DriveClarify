"""CPU-only provenance round-trip and malformed nested evidence audits."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from typing import Any

from driveclarify_persistent_ambiguity_runtime_v1.runtime import (
    EvidenceContractError,
    HardGateCertificate,
    HardGateCertificateStatus,
    HardGateEvidenceEnvelope,
    PersistentAmbiguityReferentialRuntime,
)

from .contracts import canonical_sha256, evaluate_route_fixture
from .candidate_certification import certify_candidate_pair
from .candidate_evidence import build_synthetic_corridor_evidence
from .fixtures import route_fixtures


def _certificate(kind: str, frame: int | None, timestamp: float | None = None) -> HardGateCertificate:
    status = (
        HardGateCertificateStatus.VERIFIED_PHYSICAL_SAFETY_PASS
        if kind == "physical"
        else HardGateCertificateStatus.VERIFIED_ROUTE_LOCAL_PASS
    )
    return HardGateCertificate(
        certificate_id=f"{kind}-{frame}" if frame is not None else None,
        certificate_sha256=canonical_sha256([kind, frame]) if frame is not None else None,
        source_frame_id=frame,
        status=status if frame is not None else HardGateCertificateStatus.UNKNOWN,
        source_timestamp=timestamp,
    )


def _envelope(
    *, source_frame: int | None, current_frame: int, observation: str = "obs-410",
    bundle: str = "bundle-1", source_timestamp: float | None = 40.9,
    current_timestamp: float | None = 41.0,
) -> HardGateEvidenceEnvelope:
    return HardGateEvidenceEnvelope(
        source_observation_id=observation,
        source_frame_id=source_frame,
        physical_safety=_certificate("physical", source_frame, source_timestamp),
        route_local_hard_rule=_certificate("route", source_frame, source_timestamp),
        source_timestamp=source_timestamp,
        current_frame_id=current_frame,
        current_timestamp=current_timestamp,
        producer_id="R4_1_PROVENANCE_AUDIT",
        candidate_bundle_id=bundle,
    )


def provenance_round_trip_audit() -> dict[str, Any]:
    cases = [
        ("fresh_same_frame", _envelope(source_frame=410, current_frame=410), "obs-410", "bundle-1"),
        ("stale_one_frame", _envelope(source_frame=409, current_frame=410), "obs-410", "bundle-1"),
        ("stale_multi_frame", _envelope(source_frame=405, current_frame=410), "obs-410", "bundle-1"),
        ("missing_source_frame", _envelope(source_frame=None, current_frame=410, source_timestamp=None), "obs-410", "bundle-1"),
        ("mismatched_observation_id", _envelope(source_frame=410, current_frame=410, observation="wrong-obs"), "obs-410", "bundle-1"),
        ("mismatched_candidate_bundle_id", _envelope(source_frame=410, current_frame=410, bundle="wrong-bundle"), "obs-410", "bundle-1"),
        ("source_timestamp_newer_than_current", _envelope(source_frame=410, current_frame=410, source_timestamp=41.1, current_timestamp=41.0), "obs-410", "bundle-1"),
    ]
    rows = []
    provenance_fields = (
        "source_observation_id", "source_frame_id", "source_timestamp", "current_frame_id",
        "current_timestamp", "producer_id", "candidate_bundle_id",
    )
    for case_id, envelope, expected_observation, expected_bundle in cases:
        first = envelope.to_dict()
        restored = HardGateEvidenceEnvelope.from_dict(first)
        second = restored.to_dict()
        fields_preserved = all(first.get(field) == second.get(field) for field in provenance_fields)
        eligible = restored.authorization_eligible(
            expected_source_observation_id=expected_observation,
            expected_current_frame_id=410,
            expected_candidate_bundle_id=expected_bundle,
        )
        rows.append({
            "case_id": case_id,
            "serialized_before": first,
            "serialized_after": second,
            "provenance_fields_preserved": fields_preserved,
            "authorization_eligible": eligible,
            "expected_authorization_eligible": case_id == "fresh_same_frame",
            "pass": fields_preserved and eligible == (case_id == "fresh_same_frame"),
        })
    value = {
        "schema_version": "driveclarify.stale_provenance_round_trip_audit.r4_1.v1",
        "status": "PASS" if all(row["pass"] for row in rows) else "FAIL",
        "cases": rows,
    }
    value["audit_sha256"] = canonical_sha256(value)
    return value


def _valid_mapping() -> dict[str, Any]:
    return _envelope(source_frame=410, current_frame=410).to_dict()


def malformed_nested_evidence_audit() -> dict[str, Any]:
    raw_cases = []
    invalid_enum = _valid_mapping(); invalid_enum["physical_safety"]["status"] = "NOT_AN_ENUM"
    raw_cases.append(("invalid_nested_enum", lambda: HardGateEvidenceEnvelope.from_dict(invalid_enum)))
    wrong_type = _valid_mapping(); wrong_type["physical_safety"] = []
    raw_cases.append(("wrong_nested_type", lambda: HardGateEvidenceEnvelope.from_dict(wrong_type)))
    missing_field = _valid_mapping(); missing_field["route_local_hard_rule"].pop("status")
    raw_cases.append(("missing_required_nested_field", lambda: HardGateEvidenceEnvelope.from_dict(missing_field)))
    raw_cases.append(("malformed_route_control_record", lambda: evaluate_route_fixture({"source_frame_id": 410})))
    raw_cases.append(("malformed_traffic_control_record", lambda: evaluate_route_fixture({"source_frame_id": 410, "ego_lane": {}, "route_lanes": [], "fixture_id": "bad", "controls": [{"state": "RED"}]})))
    valid_route = route_fixtures()[0]
    ego_direction_empty = deepcopy(valid_route); ego_direction_empty["ego_lane"]["direction"] = []
    raw_cases.append(("ego_lane_direction_empty", lambda: evaluate_route_fixture(ego_direction_empty)))
    control_direction_short = deepcopy(valid_route); control_direction_short["controls"][0]["lane"]["direction"] = [1.0]
    raw_cases.append(("control_lane_direction_short", lambda: evaluate_route_fixture(control_direction_short)))
    route_direction_empty = deepcopy(valid_route); route_direction_empty["route_lanes"][0]["direction"] = []
    raw_cases.append(("route_lane_direction_empty", lambda: evaluate_route_fixture(route_direction_empty)))
    nonserializable = replace(_envelope(source_frame=410, current_frame=410), producer_id=object())
    raw_cases.append(("non_serializable_nested_object", nonserializable.to_dict))
    decision_cases = [
        ("decision_invalid_nested_enum", {"active_candidate_count": 2, "current_action_relation": "BAD_ENUM"}),
        ("decision_wrong_nested_type", {"active_candidate_count": 2, "query_lifecycle": []}),
        ("decision_missing_required_field", {"query_lifecycle": {}}),
    ]
    rows = []
    for case_id, operation in raw_cases:
        try:
            result = operation()
        except EvidenceContractError as error:
            rows.append({"case_id": case_id, "result": "PRE_ENTRY_REJECTED", "authorization_eligible": False, "uncaught_exception": False, "reason_code": str(error), "pass": True})
        else:
            fail_closed = isinstance(result, dict) and result.get("authorization_eligible") is False and result.get("status") == "PRE_ENTRY_REJECTED"
            rows.append({"case_id": case_id, "result": result.get("status") if isinstance(result, dict) else "UNEXPECTED_ACCEPT", "authorization_eligible": False if fail_closed else None, "uncaught_exception": False, "pass": fail_closed})
    for case_id, evidence in decision_cases:
        result = PersistentAmbiguityReferentialRuntime.evaluate_decision_opportunity_evidence(evidence)
        rows.append({
            "case_id": case_id, "result": "UNKNOWN_FALLBACK",
            "authorization_eligible": result["authorization_eligible"],
            "decision": result["decision"], "uncaught_exception": False,
            "reason_codes": result["reason_codes"],
            "pass": result["decision"] == "FALLBACK" and result["authorization_eligible"] is False,
        })
    static_wait = {
        "active_candidate_count": 2,
        "query_lifecycle": {
            "ask_emitted": True, "active_query": True, "answer_pending": True,
            "holding_valid": True, "lease_valid": True, "query_bound": True,
        },
    }
    result = PersistentAmbiguityReferentialRuntime.evaluate_decision_opportunity_lifecycle(static_wait)
    rows.append({
        "case_id": "static_wait_reducer_context_forbidden",
        "result": "UNKNOWN_FALLBACK",
        "authorization_eligible": result["authorization_eligible"],
        "decision": result["decision"], "uncaught_exception": False,
        "reason_codes": result["reason_codes"],
        "pass": result["decision"] == "FALLBACK" and result["authorization_eligible"] is False,
    })
    result = PersistentAmbiguityReferentialRuntime.evaluate_decision_opportunity_evidence(static_wait)
    rows.append({
        "case_id": "generic_owner_static_wait_context_forbidden",
        "result": "UNKNOWN_FALLBACK",
        "authorization_eligible": result["authorization_eligible"],
        "decision": result["decision"], "uncaught_exception": False,
        "reason_codes": result["reason_codes"],
        "pass": (
            result["decision"] == "FALLBACK"
            and result["authorization_eligible"] is False
            and "STATIC_QUERY_LIFECYCLE_CONTEXT_FORBIDDEN" in result["reason_codes"]
        ),
    })
    root = Path(__file__).resolve().parents[1]
    matrix_path = root / "reports/driveclarify_method_v1_r4_cpu_contract_completion_and_controlled_gpu_validation/COUNTED_GPU_FORWARD_MATRIX.json"
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))["records"]
    parent_records = [row for row in matrix if row["fixture_id"] == "CERTIFIED_PAIR_POSITIVE"]
    base_candidate_evidence = build_synthetic_corridor_evidence(parent_records)
    target_record_id = str(parent_records[0]["ordinal"])
    candidate_cases = []
    route_object = deepcopy(base_candidate_evidence)
    route_object["derived_overrides"][target_record_id] = {"route": [[object(), 0.0]]}
    candidate_cases.append(("candidate_route_nonserializable_nested_object", route_object))
    map_object = deepcopy(base_candidate_evidence)
    map_object["topology"]["0"]["map_payload"]["extra"] = object()
    candidate_cases.append(("candidate_map_payload_nonserializable_nested_object", map_object))
    for case_id, candidate_evidence in candidate_cases:
        certificate = certify_candidate_pair(case_id, parent_records, evidence=candidate_evidence)
        rows.append({
            "case_id": case_id,
            "result": "UNKNOWN_FALLBACK",
            "authorization_eligible": False,
            "candidate_classification": certificate["classification"],
            "uncaught_exception": False,
            "pass": certificate["classification"] == "UNKNOWN_INSUFFICIENT_EVIDENCE",
        })
    value = {
        "schema_version": "driveclarify.malformed_nested_evidence_fail_closed_audit.r4_1.v1",
        "status": "PASS" if all(row["pass"] for row in rows) else "FAIL",
        "cases": rows,
    }
    value["audit_sha256"] = canonical_sha256(value)
    return value


__all__ = ["provenance_round_trip_audit", "malformed_nested_evidence_audit"]
