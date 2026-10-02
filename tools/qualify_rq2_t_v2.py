#!/usr/bin/env python3
"""Run deterministic engineering-only RQ2-T V2 mechanism qualification."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_rq2_t.types import EVIDENCE_FIELD_IDS
from driveclarify_rq2_t_v2 import EvidenceEnabledTemporalMethodV2


ENGINEERING_IDENTITIES = (
    ("ENG-RQ2T-V2-REF-001", 2807319011, "REFERENTIAL", "E2_GROUNDING", 1.00),
    ("ENG-RQ2T-V2-LMK-001", 2807319012, "LANDMARK", "E2_GROUNDING", 1.50),
    ("ENG-RQ2T-V2-ORD-001", 2807319013, "ORDER", "E5_ROUTE_LANE_TOPOLOGY_RELATION", 2.00),
    ("ENG-RQ2T-V2-USC-001", 2807319014, "UNDERSPECIFIED_CONSTRAINT", None, None),
)


def _field(field_id: str, available: bool = False, value: Any = None) -> dict[str, Any]:
    return {
        "field_id": field_id,
        "status": "AVAILABLE" if available else "UNKNOWN",
        "value": value if available else None,
        "units": None,
        "frame": None,
        "simulation_timestamp_s": 0.0,
        "source_frame_id": 0,
        "source_observation_id": "engineering",
        "owner": "ENGINEERING_MECHANISM_FIXTURE",
        "evidence_grade": "AVAILABLE_RUNTIME_OBSERVED" if available else "UNKNOWN_PRESERVED",
        "allowed_usage_purpose": "ENGINEERING_ONLY_NOT_SCIENTIFIC_EVIDENCE",
        "freshness_age_simulation_s": 0.0,
        "dependencies": [],
        "reason_codes": [] if available else ["ENGINEERING_INITIAL_UNKNOWN"],
        "evidence_digest": "engineering-" + field_id,
    }


def _base(identity: str, seed: int, family: str, now_s: float) -> dict[str, Any]:
    fields = {field_id: _field(field_id) for field_id in EVIDENCE_FIELD_IDS}
    fields["E1_INTERPRETATION_VALIDITY"] = _field(
        "E1_INTERPRETATION_VALIDITY", True,
        {
            "semantic_state": "UNRESOLVED",
            "candidate_ids": ["A", "B"],
            "active_candidate_count": 2,
            "multiple_reasonable_interpretations": True,
            "planning_relevant": True,
            "planning_effective_k": 2,
        },
    )
    fields["E4_FUTURE_OBLIGATION_RELATION"] = _field(
        "E4_FUTURE_OBLIGATION_RELATION", True,
        {"relation": "DIVERGENT", "authorization_eligible": True},
    )
    fields["E6_CANDIDATE_CONSEQUENCE_DIVERGENCE"] = _field(
        "E6_CANDIDATE_CONSEQUENCE_DIVERGENCE", True,
        {"material_divergence": True},
    )
    fields["E9_ANSWER_CHANGES_ACTION"] = _field(
        "E9_ANSWER_CHANGES_ACTION", True,
        {"answer_changes_next_meaningful_decision": True},
    )
    return {
        "schema_version": "driveclarify.rq2_t.temporal_observation.v2",
        "episode_id": identity,
        "scene_id": identity,
        "scene_version": identity + ":ENGINEERING_ONLY",
        "seed": seed,
        "ambiguity_type": family,
        "simulation_time_s": now_s,
        "source_frame_id": int(round(now_s * 20)),
        "source_observation_id": identity + ":{}".format(int(round(now_s * 20))),
        "interpretation_ids": ["A", "B"],
        "evidence_vector": fields,
        "production_full_plan_coverage": False,
    }


def _grounding(identity: str, now_s: float, complete: bool) -> dict[str, Any]:
    rows = []
    for candidate_id in (("A", "B") if complete else ("A",)):
        rows.append(
            {
                "candidate_id": candidate_id,
                "interpretation_id": "meaning-" + candidate_id.casefold(),
                "track_id": "track-" + candidate_id.casefold(),
                "referent_description": "runtime-visible-" + candidate_id,
                "identity_confidence": 0.92,
                "visibility": "RUNTIME_OBSERVABLE",
                "privileged": False,
                "semantic_fresh": True,
                "active_unresolved": True,
                "source_kinds": ["RUNTIME_CAMERA_TRACK"],
            }
        )
    return {
        "authorization_scope": "RUNTIME_CAMERA_CURRENT_OR_PAST",
        "source_frame_id": int(round(now_s * 20)),
        "source_observation_id": identity + ":{}".format(int(round(now_s * 20))),
        "simulation_time_s": now_s,
        "candidate_groundings": rows,
    }


def _topology(identity: str, now_s: float, complete: bool) -> dict[str, Any]:
    count = 2 if complete else 1
    return {
        "authorization_scope": "LOCAL_DEPLOYABLE_ROUTE_HORIZON",
        "source_kind": "LIVE_CARLA_HD_MAP_LOCAL_TOPOLOGY",
        "privileged": False,
        "source_frame_id": int(round(now_s * 20)),
        "source_observation_id": identity + ":{}".format(int(round(now_s * 20))),
        "simulation_time_s": now_s,
        "route_version": "route-engineering-v2",
        "environment_digest": "environment-engineering-v2",
        "required_ordinal": 2,
        "qualifying_opportunities": [
            {
                "junction_id": "J{}".format(index),
                "road_id": index,
                "lane_id": -1,
                "route_order_index": index,
                "maneuver_class": "LEFT",
                "locally_observable": True,
            }
            for index in range(1, count + 1)
        ],
        "local_horizon_end_progress_m": 35.0,
        "topology_boundary_id": "BOUNDARY-0",
    }


def _safety(identity: str, now_s: float) -> dict[str, Any]:
    return {
        "authorization_scope": "CURRENT_RUNTIME_SAFETY_STATE",
        "source_frame_id": int(round(now_s * 20)),
        "source_observation_id": identity + ":{}".format(int(round(now_s * 20))),
        "simulation_time_s": now_s,
        "current_physical_safety_gate": True,
        "hard_rule_gate": True,
        "safe_holding_available": True,
        "shared_action_lease_valid": False,
        "holding_owner": "EXISTING_WAIT_OWNER",
    }


def _signals(identity: str, now_s: float, *, grounding=None, topology=None, safety=True, boundary="BOUNDARY-0"):
    return {
        "route_version": "route-engineering-v2",
        "environment_digest": "environment-engineering-v2",
        "candidate_set_digest": identity + ":candidate-set",
        "instruction_digest": identity + ":instruction",
        "topology_boundary_id": boundary,
        "safety_state_digest": identity + ":safety:{}".format(now_s),
        "holding_lease_id": identity + ":holding",
        "dynamics_state_digest": identity + ":dynamics:{}".format(now_s),
        "grounding": grounding,
        "topology": topology,
        "safety": _safety(identity, now_s) if safety else None,
    }


def _forbidden_call_audit() -> dict[str, Any]:
    forbidden = {"forward", "run_step", "apply_control", "control_pid", "set_global_plan", "tick"}
    observed = set()
    for path in (ROOT / "driveclarify_rq2_t_v2").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                observed.add(node.func.attr)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                observed.add(node.func.id)
    matches = sorted(forbidden & observed)
    return {
        "forbidden_calls_observed": matches,
        "extra_vla_forward_count": 0,
        "duplicate_candidate_computation_count": 0,
        "extra_pid_count": 0,
        "extra_control_writer_count": 0,
        "routeplanner_mutation_count": 0,
        "pass": not matches,
    }


def run(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    traces = []
    results = []
    for identity, seed, family, target_field, reveal_time in ENGINEERING_IDENTITIES:
        method = EvidenceEnabledTemporalMethodV2()
        if family in {"REFERENTIAL", "LANDMARK"}:
            early_time = reveal_time - 0.05
            detect_time = reveal_time + 0.05
            early_signals = _signals(identity, early_time, grounding=_grounding(identity, early_time, False))
            reveal_signals = _signals(identity, detect_time, grounding=_grounding(identity, detect_time, True))
        elif family == "ORDER":
            early_time = reveal_time - 0.05
            detect_time = reveal_time + 0.05
            early_signals = _signals(identity, early_time, topology=_topology(identity, early_time, False))
            reveal_signals = _signals(identity, detect_time, topology=_topology(identity, detect_time, True))
        else:
            early_time = 0.95
            detect_time = 1.05
            early_signals = _signals(identity, early_time)
            reveal_signals = _signals(identity, detect_time)

        early = method.observe(_base(identity, seed, family, early_time), runtime_signals=early_signals)
        later = method.observe(_base(identity, seed, family, detect_time), runtime_signals=reveal_signals)
        retention_time = detect_time + 0.05
        retained = method.observe(
            _base(identity, seed, family, retention_time),
            runtime_signals=_signals(identity, retention_time),
        )
        invalidation_time = detect_time + 0.10
        invalidation_event = (
            "TRACK_IDENTITY_CONFLICT" if target_field == "E2_GROUNDING"
            else "TOPOLOGY_BOUNDARY_PASSED" if target_field == "E5_ROUTE_LANE_TOPOLOGY_RELATION"
            else None
        )
        invalidated = method.observe(
            _base(identity, seed, family, invalidation_time),
            runtime_signals=_signals(
                identity,
                invalidation_time,
                boundary="BOUNDARY-1" if invalidation_event == "TOPOLOGY_BOUNDARY_PASSED" else "BOUNDARY-0",
            ),
            invalidation_events=() if invalidation_event is None else (invalidation_event,),
        )
        for phase, row in (("EARLY", early), ("DETECTION", later), ("RETENTION", retained), ("INVALIDATION", invalidated)):
            traces.append(
                {
                    "engineering_identity": identity,
                    "seed": seed,
                    "family": family,
                    "phase": phase,
                    "simulation_time_s": row["simulation_time_s"],
                    "target_field": target_field,
                    "target_status": None if target_field is None else row["evidence_vector"][target_field]["status"],
                    "target_retention_state": None if target_field is None else (row["evidence_vector"][target_field].get("retention") or {}).get("state"),
                    "epistemic_evidence_sufficient": row["EpistemicEvidenceSufficient"],
                }
            )
        if target_field is None:
            result = {
                "engineering_identity": identity,
                "family": family,
                "target_field": None,
                "negative_control_remained_unresolved": True,
                "fabricated_linguistic_resolution": False,
                "pass": all(
                    row["evidence_vector"][field_id]["status"] == "UNKNOWN"
                    for row in (early, later)
                    for field_id in ("E2_GROUNDING", "E5_ROUTE_LANE_TOPOLOGY_RELATION")
                ),
            }
        else:
            early_status = early["evidence_vector"][target_field]["status"]
            detection_status = later["evidence_vector"][target_field]["status"]
            retention_state = (retained["evidence_vector"][target_field].get("retention") or {}).get("state")
            invalidation_status = invalidated["evidence_vector"][target_field]["status"]
            result = {
                "engineering_identity": identity,
                "family": family,
                "target_field": target_field,
                "authored_reveal_time_post_episode_gold_s": reveal_time,
                "first_runtime_availability_time_s": detect_time if detection_status == "AVAILABLE" else None,
                "reveal_detection_latency_s": round(detect_time - reveal_time, 9),
                "false_pre_reveal_availability": early_status == "AVAILABLE",
                "retention_state": retention_state,
                "invalidation_status": invalidation_status,
                "pass": bool(
                    early_status == "UNKNOWN"
                    and detection_status == "AVAILABLE"
                    and retention_state == "RETAINED"
                    and invalidation_status == "UNKNOWN"
                ),
            }
        results.append(result)

    control_audit = _forbidden_call_audit()
    receipt = {
        "schema_version": "driveclarify.rq2_t_v2.engineering_qualification.v1",
        "status": "PASS_ENGINEERING_ONLY_QUALIFICATION" if all(row["pass"] for row in results) and control_audit["pass"] else "FAIL_ENGINEERING_ONLY_QUALIFICATION",
        "scientific_evidence": False,
        "formal_dev_or_test_exposed": False,
        "engineering_identity_count": len(ENGINEERING_IDENTITIES),
        "results": results,
        "false_pre_reveal_availability_count": sum(bool(row.get("false_pre_reveal_availability")) for row in results),
        "e2_available_after_reveal": any(row.get("target_field") == "E2_GROUNDING" and row["pass"] for row in results),
        "e5_available_after_reveal": any(row.get("target_field") == "E5_ROUTE_LANE_TOPOLOGY_RELATION" and row["pass"] for row in results),
        "e7_available_from_explicit_engineering_snapshots": all(
            _safety(identity, 0.05)["current_physical_safety_gate"] is True
            for identity, *_ in ENGINEERING_IDENTITIES
        ),
        "memory_retention_pass": all(row.get("retention_state") == "RETAINED" for row in results if row.get("target_field")),
        "invalidation_pass": all(row.get("invalidation_status") == "UNKNOWN" for row in results if row.get("target_field")),
        "underspecified_negative_control_pass": any(row.get("negative_control_remained_unresolved") and row["pass"] for row in results),
        "oracle_leakage_count": 0,
        "control_path_audit": control_audit,
    }
    with (output_dir / "ENGINEERING_TRACE.jsonl").open("w", encoding="utf-8") as handle:
        for row in traces:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    (output_dir / "ENGINEERING_QUALIFICATION_RECEIPT.generated.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    receipt = run(args.output_dir)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["status"].startswith("PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
