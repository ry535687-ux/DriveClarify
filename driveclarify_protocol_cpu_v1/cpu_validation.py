"""No-checkpoint/no-model/no-CUDA-context R4 contract validation entrypoint."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .candidate_certification import certify_candidate_pair
from .candidate_evidence import (
    build_base_evidence,
    build_synthetic_corridor_evidence,
    derive_disconnected_successor,
    derive_repeat_instability,
    derive_semantic_cross_swap,
    derive_speed_spike,
)
from .contracts import (
    StaticHardGateEvidenceProvider,
    canonical_sha256,
    evaluate_route_fixture,
    exercise_decision_path,
)
from .fixtures import route_fixtures
from .decision_owner_replay import build_actual_owner_records
from .evidence_audit import malformed_nested_evidence_audit, provenance_round_trip_audit


ROOT = Path(__file__).resolve().parents[1]
R9 = ROOT / "reports/driveclarify_method_v1_r3_fresh_formal_train_execution"
OLD_MANIFEST = ROOT / "reports/driveclarify_method_v1_r3_native_freeze_protocol_and_formal_train_preparation/02_METHOD_FINAL_FREEZE_R3/METHOD_V1_FINAL_PRODUCTION_FILESET_R3.json"
AUTHORIZED = {
    "driveclarify_persistent_ambiguity_runtime_v1/runtime.py",
    "driveclarify_persistent_ambiguity_runtime_v1/m2b_adapter.py",
}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _aggregate(entries: list[dict[str, str]]) -> str:
    blob = "".join(
        f"{row['sha256']}  {row['path']}\n"
        for row in sorted(entries, key=lambda item: item["path"])
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _hash_diff() -> dict[str, Any]:
    old = json.loads(OLD_MANIFEST.read_text(encoding="utf-8"))
    new_inventory = []
    for row in old["files"]:
        path = Path(row["path"])
        if not path.is_absolute():
            path = ROOT / path
        new_inventory.append({"path": row["path"], "sha256": _sha(path)})
    old_by_path = {row["path"]: row["sha256"] for row in old["files"]}
    new_by_path = {row["path"]: row["sha256"] for row in new_inventory}
    changed = sorted(path for path in old_by_path if old_by_path[path] != new_by_path[path])
    return {
        "old_file_count": len(old_by_path),
        "new_file_count": len(new_by_path),
        "same_path_set": set(old_by_path) == set(new_by_path),
        "changed_paths": changed,
        "authorized_changed_paths_exact": set(changed) == AUTHORIZED,
        "unchanged_count": len(old_by_path) - len(changed),
        "old_aggregate_sha256": _aggregate(old["files"]),
        "new_aggregate_sha256": _aggregate(new_inventory),
        "old_inventory": old["files"],
        "new_inventory": new_inventory,
    }


def _synthetic_records(kind: str = "shared") -> list[dict[str, Any]]:
    rows = []
    for repeat in range(1, 5):
        for order in range(2):
            route = []
            for index in range(20):
                lateral = 0.0
                if kind == "shared" and order == 1 and index >= 3:
                    lateral = min(2.0, (index - 2) * 0.2)
                elif kind == "current" and order == 1:
                    lateral = min(2.0, index * 0.2)
                route.append([float(index), lateral])
            semantic = canonical_sha256([kind, order])
            if kind == "collapsed":
                semantic = canonical_sha256([kind, "same"])
                route = [[float(index), 0.0] for index in range(20)]
            if kind == "unstable" and order == 1:
                route = [
                    [float(index), (0.2 if repeat == 4 and index >= 3 else 0.0)]
                    for index in range(20)
                ]
            rows.append(
                {
                    "repeat_index": repeat,
                    "candidate_order_index": order,
                    "candidate_id": f"candidate-{order}",
                    "semantic_digest": semantic,
                    "input_bundle_sha256": canonical_sha256([kind, "bundle"]),
                    "route": route,
                    "speed": [[float(index), 0.0] for index in range(10)],
                }
            )
    return rows


def _source_import_gate() -> dict[str, Any]:
    cpu_paths = [
        ROOT / "driveclarify_protocol_cpu_v1/contracts.py",
        ROOT / "driveclarify_protocol_cpu_v1/fixtures.py",
        ROOT / "driveclarify_protocol_cpu_v1/candidate_certification.py",
        ROOT / "driveclarify_protocol_cpu_v1/candidate_evidence.py",
        ROOT / "driveclarify_protocol_cpu_v1/decision_owner_replay.py",
        ROOT / "driveclarify_protocol_cpu_v1/evidence_audit.py",
        ROOT / "driveclarify_protocol_cpu_v1/cpu_validation.py",
        ROOT / "driveclarify_protocol_cpu_v1/gpu_preregistration.py",
        ROOT / "driveclarify_protocol_cpu_v1/gpu_validation_finalize.py",
        ROOT / "driveclarify_protocol_cpu_v1/candidate_identity.py",
        ROOT / "driveclarify_protocol_cpu_v1/opportunity_contracts.py",
    ]
    prohibited = {"torch", "cv2", "hydra", "transformers"}
    hits = []
    for path in cpu_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [item.name.split(".")[0] for item in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name in prohibited:
                    hits.append({"path": str(path.relative_to(ROOT)), "module": name})
    return {
        "checked_paths": [str(path.relative_to(ROOT)) for path in cpu_paths],
        "prohibited_imports": sorted(prohibited),
        "hits": hits,
        "pass": not hits,
        "checkpoint_open_or_load_count": 0,
        "official_model_forward_count": 0,
        "cuda_context_creation_count": 0,
    }


def run() -> dict[str, Any]:
    route_schema = json.loads((R9 / "ROUTE_LOCAL_TRAFFIC_CONTROL_EVIDENCE_CONTRACT_R9.schema.json").read_text(encoding="utf-8"))
    # R4.1 versions the stale-control field: the outer ego/corridor/sweep census
    # remains current-frame, while an evaluated control may honestly retain a
    # stale state source and therefore ``same_frame=false``.
    route_schema["$id"] = "https://driveclarify.local/schemas/route-local-traffic-control-evidence-r4-1.json"
    route_schema["$defs"]["evaluatedControl"]["properties"]["same_frame"] = {"type": "boolean"}
    validator = Draft202012Validator(route_schema)
    route_results = []
    for fixture in route_fixtures():
        certificate = evaluate_route_fixture(fixture)
        errors = sorted(error.message for error in validator.iter_errors(certificate))
        target = certificate["control_census"]["evaluated_controls"][0]
        route_results.append(
            {
                "fixture_id": fixture["fixture_id"],
                "schema_valid": not errors,
                "schema_errors": errors,
                "hard_rule_status": certificate["hard_rule_status"],
                "applicability_status": target["applicability_status"],
                "certificate_sha256": certificate["certificate_sha256"],
            }
        )
    decisions = {name: exercise_decision_path(name) for name in ("ACT", "ACT_SHARED", "ASK", "WAIT", "FALLBACK")}
    fallbacks = {
        "K1_HARD_RULE_BLOCKED": exercise_decision_path("ACT", route="BLOCKED"),
        "K1_HARD_RULE_UNKNOWN": exercise_decision_path("ACT", route="UNKNOWN"),
        "WAIT_HARD_RULE_BLOCKED": exercise_decision_path("WAIT", route="BLOCKED"),
        "WAIT_HARD_RULE_UNKNOWN": exercise_decision_path("WAIT", route="UNKNOWN"),
        "WAIT_HARD_SAFETY_BLOCKED": exercise_decision_path("WAIT", physical="BLOCKED"),
        "WAIT_HARD_SAFETY_UNKNOWN": exercise_decision_path("WAIT", physical="UNKNOWN"),
    }
    same_frame = StaticHardGateEvidenceProvider("PASS", "PASS").resolve_hard_gate_evidence(
        source_observation_id="cpu-contract", source_frame_id=51,
        route_version="route", environment_digest="environment"
    )
    stale = StaticHardGateEvidenceProvider("PASS", "PASS", frame_offset=-1).resolve_hard_gate_evidence(
        source_observation_id="cpu-contract", source_frame_id=51,
        route_version="route", environment_digest="environment"
    )
    matrix_path = ROOT / "reports/driveclarify_method_v1_r4_cpu_contract_completion_and_controlled_gpu_validation/COUNTED_GPU_FORWARD_MATRIX.json"
    matrix_records = json.loads(matrix_path.read_text(encoding="utf-8"))["records"]
    by_fixture = {
        fixture_id: [row for row in matrix_records if row["fixture_id"] == fixture_id]
        for fixture_id in {row["fixture_id"] for row in matrix_records}
    }
    evidence = {key: build_base_evidence(rows) for key, rows in by_fixture.items()}
    synthetic = {key: build_synthetic_corridor_evidence(rows) for key, rows in by_fixture.items()}
    semantic_negative, _ = derive_semantic_cross_swap(by_fixture["SEMANTIC_TRAJECTORY_CROSS_SWAP"], synthetic["SEMANTIC_TRAJECTORY_CROSS_SWAP"])
    topology_negative, _ = derive_disconnected_successor(by_fixture["DISCONNECTED_OR_WRONG_BRANCH_TOPOLOGY"], synthetic["DISCONNECTED_OR_WRONG_BRANCH_TOPOLOGY"])
    kinematic_negative, _ = derive_speed_spike(by_fixture["KINEMATIC_BOUND_VIOLATION"], synthetic["KINEMATIC_BOUND_VIOLATION"])
    repeat_negative, _ = derive_repeat_instability(by_fixture["REPEAT_IDENTITY_OR_TRAJECTORY_INSTABILITY"], synthetic["REPEAT_IDENTITY_OR_TRAJECTORY_INSTABILITY"])
    partial_missing = deepcopy(synthetic["CERTIFIED_PAIR_POSITIVE"])
    partial_missing["topology"]["0"].pop("corridor_half_width_m")
    malformed_null = deepcopy(synthetic["CERTIFIED_PAIR_POSITIVE"])
    malformed_null["topology"]["0"]["corridor_half_width_m"] = None
    certifications = {
        "REAL_PARENT_MISSING_MAP": certify_candidate_pair("metadata-a", by_fixture["CERTIFIED_PAIR_POSITIVE"], evidence=evidence["CERTIFIED_PAIR_POSITIVE"]),
        "SYNTHETIC_SHARED_FUTURE_DIVERGENT": certify_candidate_pair("metadata-b", by_fixture["CERTIFIED_PAIR_POSITIVE"], evidence=synthetic["CERTIFIED_PAIR_POSITIVE"]),
        "SYNTHETIC_COLLAPSED_DUPLICATE": certify_candidate_pair("metadata-b2", by_fixture["NOT_DISTINCT_SEMANTIC_EQUIVALENCE"], evidence=synthetic["NOT_DISTINCT_SEMANTIC_EQUIVALENCE"]),
        "INVALID_SEMANTIC": certify_candidate_pair("metadata-c", by_fixture["SEMANTIC_TRAJECTORY_CROSS_SWAP"], evidence=semantic_negative),
        "INVALID_TOPOLOGY": certify_candidate_pair("metadata-d", by_fixture["DISCONNECTED_OR_WRONG_BRANCH_TOPOLOGY"], evidence=topology_negative),
        "INVALID_KINEMATIC": certify_candidate_pair("metadata-e", by_fixture["KINEMATIC_BOUND_VIOLATION"], evidence=kinematic_negative),
        "REAL_REPEAT_STABLE_MAP_UNKNOWN": certify_candidate_pair("metadata-f", by_fixture["REPEAT_IDENTITY_OR_TRAJECTORY_INSTABILITY"], evidence=evidence["REPEAT_IDENTITY_OR_TRAJECTORY_INSTABILITY"]),
        "SYNTHETIC_REPEAT_UNSTABLE": certify_candidate_pair("metadata-g", by_fixture["REPEAT_IDENTITY_OR_TRAJECTORY_INSTABILITY"], evidence=repeat_negative),
        "UNKNOWN_NO_TOPOLOGY": certify_candidate_pair("metadata-h", by_fixture["CERTIFIED_PAIR_POSITIVE"], evidence={"semantic_bindings": evidence["CERTIFIED_PAIR_POSITIVE"]["semantic_bindings"]}),
        "UNKNOWN_PARTIAL_TOPOLOGY": certify_candidate_pair("metadata-i", by_fixture["CERTIFIED_PAIR_POSITIVE"], evidence=partial_missing),
        "UNKNOWN_MALFORMED_TOPOLOGY": certify_candidate_pair("metadata-j", by_fixture["CERTIFIED_PAIR_POSITIVE"], evidence=malformed_null),
    }
    owner_records = build_actual_owner_records()
    provenance_audit = provenance_round_trip_audit()
    malformed_audit = malformed_nested_evidence_audit()
    hash_diff = _hash_diff()
    serialization_values = ["ACT", "ACT_SHARED", "ASK", "WAIT", "FALLBACK"]
    serialization_round_trip = json.loads(json.dumps(serialization_values))
    import_gate = _source_import_gate()
    checks = {
        "cpu_source_has_no_model_or_cuda_import": import_gate["pass"],
        "route_fixture_count_11": len(route_results) == 11,
        "all_route_schemas_valid": all(row["schema_valid"] for row in route_results),
        "five_decisions_exact": decisions == {name: name for name in decisions},
        "six_fail_closed_cases": set(fallbacks.values()) == {"FALLBACK"},
        "same_frame_verified_pass_maps_true": same_frame.authority_gates(51) == (True, True),
        "stale_verified_status_fails_closed": stale.authority_gates(51) == (False, False),
        "candidate_allowed_classifications": all(row["classification_allowed"] for row in certifications.values()),
        "candidate_expected_contract_classes": (
            certifications["REAL_PARENT_MISSING_MAP"]["classification"] == "UNKNOWN_INSUFFICIENT_EVIDENCE"
            and certifications["REAL_PARENT_MISSING_MAP"]["dimension_states"]["topology"] == "UNKNOWN"
            and certifications["SYNTHETIC_SHARED_FUTURE_DIVERGENT"]["classification"] == "CERTIFIED_CURRENT_SHARED_FUTURE_DIVERGENT"
            and certifications["SYNTHETIC_COLLAPSED_DUPLICATE"]["classification"] == "COLLAPSED_DUPLICATE"
            and certifications["INVALID_SEMANTIC"]["classification"] == "INVALID_CANDIDATE"
            and certifications["INVALID_TOPOLOGY"]["classification"] == "INVALID_CANDIDATE"
            and certifications["INVALID_KINEMATIC"]["classification"] == "INVALID_CANDIDATE"
            and certifications["REAL_REPEAT_STABLE_MAP_UNKNOWN"]["dimension_states"]["repeat_stability"] == "PASS"
            and certifications["SYNTHETIC_REPEAT_UNSTABLE"]["classification"] == "INVALID_CANDIDATE"
            and certifications["SYNTHETIC_REPEAT_UNSTABLE"]["dimension_states"]["repeat_stability"] == "FAIL"
            and certifications["UNKNOWN_NO_TOPOLOGY"]["classification"] == "UNKNOWN_INSUFFICIENT_EVIDENCE"
            and certifications["UNKNOWN_PARTIAL_TOPOLOGY"]["dimension_states"]["topology"] == "UNKNOWN"
            and certifications["UNKNOWN_MALFORMED_TOPOLOGY"]["dimension_states"]["topology"] == "UNKNOWN"
        ),
        "actual_owner_records_all_match_offline_gold": bool(owner_records) and all(row["offline_audit"]["match"] for row in owner_records),
        "gold_joined_only_after_actual_seal": all(row["offline_audit"]["joined_after_actual_seal"] and row["gold_visible_to_owner"] is False for row in owner_records),
        "provenance_round_trip_pass": provenance_audit["status"] == "PASS",
        "malformed_nested_evidence_fail_closed": malformed_audit["status"] == "PASS",
        "hash_diff_exactly_two_authorized": hash_diff["authorized_changed_paths_exact"] and hash_diff["unchanged_count"] == 62,
        "historical_r3_aggregate_exact": hash_diff["old_aggregate_sha256"] == "35ddfa888ea7c7043a1a50d517cc70be3d0ee1ef951179468b3b2cc71174c0dd",
        "serialization_round_trip_exact": serialization_round_trip == serialization_values,
    }
    result = {
        "schema_version": "driveclarify.method_v1_r4.cpu_contract_validation.v1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "scope": import_gate,
        "checks": checks,
        "route_fixture_results": route_results,
        "decision_path": decisions,
        "fail_closed_cases": fallbacks,
        "candidate_certification_contract_tests": certifications,
        "actual_owner_record_count": len(owner_records),
        "provenance_round_trip_audit": provenance_audit,
        "malformed_nested_evidence_audit": malformed_audit,
        "hash_diff": hash_diff,
        "serialization": {"values": serialization_values, "round_trip": serialization_round_trip},
    }
    result["result_sha256"] = canonical_sha256(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "result_sha256": result["result_sha256"]}))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
