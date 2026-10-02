"""CPU-only orchestration for Task consequence fixtures and read-only S1 artifacts."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from .offline_decision import DecisionContext, evaluate_baselines, recommend_offline
from .serialization import canonical_json, write_json
from .task_atoms import (
    evaluate_task_atom,
    map_candidate_plan_to_task_atoms,
    reduce_task_pair,
)


FIXTURE_SCHEMA = "driveclarify.offline_task_consequence_fixture_set.v1"
ANALYSIS_VERSION = "driveclarify.offline_task_consequence_mvp.v1"
S1_PROTOCOL = "DRIVECLARIFY_CANDIDATE_SENSITIVITY_PILOT_V1"


def deterministic_json_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _hashed(value: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(value)
    result["deterministic_json_sha256"] = deterministic_json_hash(value)
    return result


def _points(value: Any) -> list[tuple[float, float]] | None:
    route = value
    if isinstance(route, list) and len(route) == 1 and isinstance(route[0], list):
        route = route[0]
    if not isinstance(route, list) or not route:
        return None
    points: list[tuple[float, float]] = []
    for point in route:
        if not isinstance(point, list) or len(point) != 2:
            return None
        x, y = point
        if isinstance(x, bool) or isinstance(y, bool):
            return None
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            return None
        if not math.isfinite(float(x)) or not math.isfinite(float(y)):
            return None
        points.append((float(x), float(y)))
    return points


def _raw_l2(left: Any, right: Any) -> float | None:
    left_points = _points(left)
    right_points = _points(right)
    if left_points is None or right_points is None or len(left_points) != len(right_points):
        return None
    return math.sqrt(
        sum(
            (lx - rx) ** 2 + (ly - ry) ** 2
            for (lx, ly), (rx, ry) in zip(left_points, right_points)
        )
    )


def raw_plan_diagnostics(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return raw-only diagnostics; never a Task, safety, collision, or physical consequence."""

    if len(candidates) != 2:
        route_l2 = None
        speed_l2 = None
        reason_code = "RAW_DIAGNOSTIC_REQUIRES_EXACTLY_TWO_CANDIDATES"
    else:
        route_l2 = _raw_l2(candidates[0].get("pred_route_raw"), candidates[1].get("pred_route_raw"))
        speed_l2 = _raw_l2(
            candidates[0].get("pred_speed_wps_raw"), candidates[1].get("pred_speed_wps_raw")
        )
        reason_code = (
            "RAW_PLAN_DIFFERENCE_AVAILABLE"
            if route_l2 is not None or speed_l2 is not None
            else "RAW_PLAN_DIAGNOSTIC_UNAVAILABLE"
        )
    return {
        "status": "AVAILABLE" if route_l2 is not None or speed_l2 is not None else "UNKNOWN",
        "reason_code": reason_code,
        "route_l2": route_l2,
        "speed_l2": speed_l2,
        "unit": "MODEL_RAW_L2",
        "frame": "MODEL_LOCAL_RAW",
        "usage_purpose": "DIAGNOSTIC_ONLY",
        "task_pair_classification_used": False,
        "safety_inference_allowed": False,
        "physical_distance_inference_allowed": False,
        "authorization_eligible": False,
        "safety_critical_eligible": False,
    }


def analyze_case(case: Mapping[str, Any]) -> dict[str, Any]:
    case_id = str(case.get("case_id", "UNNAMED_CASE"))
    observation_id = str(case.get("observation_id", ""))
    candidates_raw = case.get("candidate_plans")
    candidates = candidates_raw if isinstance(candidates_raw, list) else []
    registered_raw = case.get("registered_task_atoms")
    registered = registered_raw if isinstance(registered_raw, list) else []
    registered_ids = [str(item.get("atom_id", "")) for item in registered if isinstance(item, Mapping)]

    atoms_by_candidate: dict[str, list[dict[str, Any]]] = {}
    evaluations_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for candidate in sorted(candidates, key=lambda item: str(item.get("candidate_id", ""))):
        candidate_id = str(candidate.get("candidate_id", ""))
        atoms = map_candidate_plan_to_task_atoms(candidate, registered, observation_id)
        atoms_by_candidate[candidate_id] = [atom.to_dict() for atom in atoms]
        evaluations_by_candidate[candidate_id] = [evaluate_task_atom(atom) for atom in atoms]

    pair = reduce_task_pair(evaluations_by_candidate, registered_ids)
    context = DecisionContext.from_dict(pair["pair_class"], case.get("decision_context"))
    recommendation = recommend_offline(context)
    baselines = evaluate_baselines(context)
    expected = case.get("expected") if isinstance(case.get("expected"), Mapping) else None
    expected_match = None
    if expected is not None:
        expected_match = (
            expected.get("pair_class") == pair["pair_class"]
            and expected.get("recommendation") == recommendation["recommendation"]
        )
    return {
        "case_id": case_id,
        "observation_id": observation_id,
        "fixture_provenance": case.get("fixture_provenance"),
        "registered_task_atom_count": len(registered),
        "atoms_by_candidate": atoms_by_candidate,
        "evaluations_by_candidate": evaluations_by_candidate,
        "pair_equivalence": pair,
        "decision_context": context.to_dict(),
        "recommendation": recommendation,
        "baselines": baselines,
        "raw_plan_diagnostics": raw_plan_diagnostics(candidates),
        "expected": dict(expected) if expected is not None else None,
        "expected_match": expected_match,
        "diagnostic_only": True,
        "authorization_eligible": False,
        "safety_critical_eligible": False,
    }


def load_fixture_cases(path: str | Path) -> list[dict[str, Any]]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if value.get("schema_version") != FIXTURE_SCHEMA:
        raise ValueError("BAD_TASK_FIXTURE_SCHEMA")
    if value.get("fixture_provenance") != "HAND_AUTHORED_SYNTHETIC_DEVELOPMENT":
        raise ValueError("TASK_FIXTURES_MUST_BE_HAND_AUTHORED")
    cases = value.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("TASK_FIXTURE_CASES_MISSING")
    return [dict(case) for case in cases]


def _artifact_record_index(value: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    records = value.get("records")
    if not isinstance(records, list):
        raise ValueError("S1_RECORDS_MISSING")
    result: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("S1_RECORD_INVALID")
        candidate_id = str(record.get("candidate_id", ""))
        if not candidate_id or candidate_id in result:
            raise ValueError("S1_CANDIDATE_ID_INVALID")
        result[candidate_id] = record
    return result


def _verify_embedded_evidence_hash(value: Mapping[str, Any]) -> tuple[str, str]:
    """Recompute a pilot artifact's self-hash without importing its runtime producer.

    The sensitivity pilot hashes the canonical JSON payload before adding ``evidence_sha256``.
    Repeating that pure-standard-library operation here makes S1 identity verification
    fail-closed while keeping the source artifact read-only.
    """

    recorded = value.get("evidence_sha256")
    if not isinstance(recorded, str) or len(recorded) != 64:
        raise ValueError("S1_EVIDENCE_HASH_MISSING_OR_INVALID")
    try:
        int(recorded, 16)
    except ValueError as exc:
        raise ValueError("S1_EVIDENCE_HASH_MISSING_OR_INVALID") from exc
    unsigned = copy.deepcopy(dict(value))
    unsigned.pop("evidence_sha256", None)
    recomputed = hashlib.sha256(canonical_json(unsigned).encode("utf-8")).hexdigest()
    if recorded != recomputed:
        raise ValueError("S1_EVIDENCE_HASH_MISMATCH")
    return recorded, recomputed


def inspect_s1_artifact(path: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate S1 identity fields and derive a no-Task-ground-truth integration case."""

    source = Path(path)
    source_bytes = source.read_bytes()
    value = json.loads(source_bytes.decode("utf-8"))
    if value.get("protocol") != S1_PROTOCOL:
        raise ValueError("S1_PROTOCOL_MISMATCH")
    recorded_evidence_hash, recomputed_evidence_hash = _verify_embedded_evidence_hash(value)
    records = _artifact_record_index(value)
    required = ("A1", "B1", "A2", "B2")
    if tuple(sorted(records)) != tuple(sorted(required)):
        raise ValueError("S1_COMPLETE_CANDIDATE_SET_MISMATCH")
    if value.get("candidate_count") != 4:
        raise ValueError("S1_CANDIDATE_COUNT_MISMATCH")
    for candidate_id in required:
        record = records[candidate_id]
        if record.get("completion_status") != "COMPLETE" or record.get("missing_output") is not False:
            raise ValueError("S1_CANDIDATE_NOT_COMPLETE")
        if record.get("route_persisted_device") != "cpu" or record.get("speed_persisted_device") != "cpu":
            raise ValueError("S1_OUTPUT_NOT_CPU_PERSISTED")
        if record.get("interpretation_id") != candidate_id[0]:
            raise ValueError("S1_LOGICAL_CANDIDATE_ID_MISMATCH")
    identity_fields = {
        (
            records[candidate_id].get("source_observation_id"),
            records[candidate_id].get("source_observation_digest"),
            records[candidate_id].get("source_frame"),
        )
        for candidate_id in required
    }
    if len(identity_fields) != 1:
        raise ValueError("S1_OBSERVATION_IDENTITY_MISMATCH")
    observation_id, observation_digest, source_frame = next(iter(identity_fields))
    a_repeat_equal = (
        records["A1"].get("route_sha256") == records["A2"].get("route_sha256")
        and records["A1"].get("speed_sha256") == records["A2"].get("speed_sha256")
    )
    b_repeat_equal = (
        records["B1"].get("route_sha256") == records["B2"].get("route_sha256")
        and records["B1"].get("speed_sha256") == records["B2"].get("speed_sha256")
    )
    ab_route_different = records["A1"].get("route_sha256") != records["B1"].get("route_sha256")
    ab_speed_different = records["A1"].get("speed_sha256") != records["B1"].get("speed_sha256")

    candidates = []
    for logical_id, artifact_id in (("A", "A1"), ("B", "B1")):
        record = records[artifact_id]
        candidates.append(
            {
                "candidate_id": logical_id,
                "artifact_candidate_id": artifact_id,
                "source_observation_id": observation_id,
                "pred_route_raw": record.get("route"),
                "pred_speed_wps_raw": record.get("speed"),
                "plan_frame": "MODEL_LOCAL_RAW",
                "plan_unit": "RAW_UNIT",
            }
        )
    case = {
        "case_id": "s1_real_artifact_task_mapping",
        "observation_id": observation_id,
        "fixture_provenance": "READ_ONLY_REAL_S1_ARTIFACT",
        "candidate_plans": candidates,
        "registered_task_atoms": [],
        "decision_context": {
            field: None
            for field in (
                "ambiguity_present",
                "query_can_change_decision",
                "query_deadline_feasible",
                "option_preserving_control_available",
                "holding_available",
                "active_query",
                "cache_fresh",
                "evidence_eligible",
            )
        },
    }
    integration = {
        "schema_version": "driveclarify.s1_task_mapping_integration.v1",
        "integration_status": "S1_TASK_MAPPING_SUPPORTED_BUT_INCOMPLETE",
        "real_evidence_integration_verdict": "SUPPORTED_BUT_INCOMPLETE_S1_TASK_MAPPING",
        "source_artifact": str(source.resolve()),
        "source_artifact_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "source_recorded_evidence_sha256": recorded_evidence_hash,
        "source_recomputed_evidence_sha256": recomputed_evidence_hash,
        "recorded_evidence_hash_recomputed": True,
        "recorded_evidence_hash_matches": True,
        "artifact_identity_fields_verified": True,
        "protocol": value.get("protocol"),
        "run_id": value.get("run_id"),
        "observation_id": observation_id,
        "observation_digest": observation_digest,
        "source_frame": source_frame,
        "complete_candidate_ids": list(required),
        "logical_candidate_ids": ["A", "B"],
        "within_repeat_evidence": {
            "A1_equals_A2_by_route_and_speed_hash": a_repeat_equal,
            "B1_equals_B2_by_route_and_speed_hash": b_repeat_equal,
            "A3_B3_available": False,
            "formal_ratio_available": False,
            "formal_margin_available": False,
        },
        "between_candidate_raw_output_difference": {
            "route_hash_different": ab_route_different,
            "speed_hash_different": ab_speed_different,
            "classification": "RAW_PLAN_DIFFERENCE_ONLY",
            "safety_or_task_consequence_inference_allowed": False,
        },
        "raw_plan_diagnostics": raw_plan_diagnostics(candidates),
        "task_mapping_available": False,
        "task_mapping_reason_code": "NO_VERIFIED_PLAN_TO_TASK_MAPPING_OR_TARGET_EVIDENCE",
        "destination_ground_truth_available": False,
        "maneuver_ground_truth_available": False,
        "decision_use_available": False,
        "decision_reason_code": "REAL_TIMING_QUERY_HOLDING_RECOVERABILITY_UNAVAILABLE",
        "candidate_mechanism_assessment": "SUPPORTED_BUT_INCOMPLETE",
        "candidate_mechanism_development_blocker": "NOT_A_CURRENT_DEVELOPMENT_BLOCKER",
        "phase0a_formal_pass": False,
        "authorization_eligible": False,
        "safety_critical_eligible": False,
        "control_authorized": False,
    }
    return case, integration


def build_outputs(
    fixture_path: str | Path,
    *,
    s1_artifact_path: str | Path | None = None,
) -> dict[str, Any]:
    fixture_cases = load_fixture_cases(fixture_path)
    analyzed = [analyze_case(case) for case in fixture_cases]
    integration = None
    if s1_artifact_path is not None:
        s1_case, integration = inspect_s1_artifact(s1_artifact_path)
        analyzed.append(analyze_case(s1_case))

    fixture_results = [item for item in analyzed if item["expected"] is not None]
    all_expected_match = all(item["expected_match"] is True for item in fixture_results)
    common = {
        "analysis_version": ANALYSIS_VERSION,
        "mode": "DIAGNOSTIC_ONLY",
        "authorization_eligible": False,
        "safety_critical_eligible": False,
        "control_authorized": False,
    }
    task_output = _hashed(
        {
            "schema_version": "driveclarify.task_evaluation_set.v1",
            **common,
            "cases": [
                {
                    "case_id": item["case_id"],
                    "observation_id": item["observation_id"],
                    "fixture_provenance": item["fixture_provenance"],
                    "registered_task_atom_count": item["registered_task_atom_count"],
                    "atoms_by_candidate": item["atoms_by_candidate"],
                    "evaluations_by_candidate": item["evaluations_by_candidate"],
                    "raw_plan_diagnostics": item["raw_plan_diagnostics"],
                }
                for item in analyzed
            ],
        }
    )
    pair_output = _hashed(
        {
            "schema_version": "driveclarify.task_pair_equivalence_set.v1",
            **common,
            "cases": [
                {
                    "case_id": item["case_id"],
                    "pair_equivalence": item["pair_equivalence"],
                    "expected_pair_class": (
                        item["expected"].get("pair_class") if item["expected"] else None
                    ),
                }
                for item in analyzed
            ],
        }
    )
    decision_output = _hashed(
        {
            "schema_version": "driveclarify.offline_decision_recommendation_set.v1",
            **common,
            "cases": [
                {
                    "case_id": item["case_id"],
                    "decision_context": item["decision_context"],
                    "recommendation": item["recommendation"],
                    "expected_recommendation": (
                        item["expected"].get("recommendation") if item["expected"] else None
                    ),
                    "expected_match": item["expected_match"],
                }
                for item in analyzed
            ],
        }
    )
    baseline_output = _hashed(
        {
            "schema_version": "driveclarify.offline_baseline_comparison_set.v1",
            **common,
            "cases": [
                {"case_id": item["case_id"], "policies": item["baselines"]}
                for item in analyzed
            ],
        }
    )
    return {
        "task_evaluation.json": task_output,
        "pair_equivalence.json": pair_output,
        "offline_decision_recommendations.json": decision_output,
        "baseline_comparison.json": baseline_output,
        "S1_INTEGRATION_REPORT.json": _hashed(integration) if integration is not None else None,
        "summary": {
            "fixture_case_count": len(fixture_results),
            "all_hand_authored_expectations_match": all_expected_match,
            "s1_integrated": integration is not None,
            "s1_status": integration.get("integration_status") if integration else None,
            "case_results": [
                {
                    "case_id": item["case_id"],
                    "pair_class": item["pair_equivalence"]["pair_class"],
                    "recommendation": item["recommendation"]["recommendation"],
                    "expected_match": item["expected_match"],
                }
                for item in analyzed
            ],
        },
    }


def summary_markdown(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Offline Task Consequence MVP Summary",
        "",
        "- Implementation: `IMPLEMENTED`",
        f"- Hand-authored development fixtures: `TESTED` ({summary['fixture_case_count']} cases; expectations match={str(summary['all_hand_authored_expectations_match']).lower()})",
        f"- S1 integration: `{summary.get('s1_status') or 'NOT_EVALUATED'}`",
        "- Candidate mechanism: `SUPPORTED_BUT_INCOMPLETE`; `NOT_A_CURRENT_DEVELOPMENT_BLOCKER`",
        "- Output mode: `DIAGNOSTIC_ONLY`",
        "- `authorization_eligible=false`; `safety_critical_eligible=false`; no control is sent.",
        "- WAIT is not an emergency stop. ASK is not triggered by ambiguity alone. ACT is not a confidence shortcut.",
        "- Raw route/speed L2 is structural diagnostic data only; it is not safety, Task consequence, metres, or CARLA_WORLD.",
        "- No safety claim, policy-superiority claim, ACT/ASK/WAIT validation claim, or live-control authorization is made.",
        "",
        "## Offline cases",
        "",
        "| Case | Pair class | Recommendation | Expected match |",
        "|---|---|---|---|",
    ]
    for item in summary["case_results"]:
        match = "N/A" if item["expected_match"] is None else str(item["expected_match"])
        lines.append(
            f"| {item['case_id']} | {item['pair_class']} | {item['recommendation']} | {match} |"
        )
    return "\n".join(lines) + "\n"


def write_outputs(output_dir: str | Path, outputs: Mapping[str, Any]) -> None:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    for name in (
        "task_evaluation.json",
        "pair_equivalence.json",
        "offline_decision_recommendations.json",
        "baseline_comparison.json",
        "S1_INTEGRATION_REPORT.json",
    ):
        value = outputs.get(name)
        if value is not None:
            write_json(directory / name, value)
    (directory / "summary.md").write_text(
        summary_markdown(outputs["summary"]), encoding="utf-8"
    )
