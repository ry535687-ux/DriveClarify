"""Runtime-only post-blind regression predictor for the already exposed set.

This module never opens evaluation labels.  It is not a fresh blind evaluator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .canonical_ontology import canonical_json_bytes, stable_sha256
from .integrated_runtime import IntegratedRuntimeV1


EXPECTED_RUNTIME_SHA256 = "2b2e21a8a068ddb9b98995bf9fc7e4e1ba7cfac3312cd07cd2c8ae4bec665bec"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def product_source_hashes(package_dir: Path) -> dict[str, str]:
    return {
        path.name: file_sha256(path)
        for path in sorted(package_dir.glob("*.py"))
        if path.is_file()
    }


def _compatibility_projection(case: Mapping[str, Any], output: Mapping[str, Any]) -> dict[str, Any]:
    language = output.get("language_result") or {}
    parse = language.get("structured_parse") or {
        "ambiguity_type": "UNSUPPORTED",
        "unresolved_slots": [],
        "parse_status": "FAIL_CLOSED",
        "reason_codes": ["SCHEMA_TRANSLATION_FAILURE"],
    }
    candidates = language.get("candidate_interpretations", [])
    valid = [item for item in candidates if item.get("candidate_status") == "VALID"]
    decision = output.get("decision_recommendation") or {}
    inferred = output.get("consequence_result", {}).get("matrix", {})
    inferred_cells = inferred.get("cells", []) if isinstance(inferred, Mapping) else []
    pair_relation = "UNKNOWN"
    if inferred_cells and all(item.get("task_outcome") == "PASS" for item in inferred_cells):
        pair_relation = "TASK_EQUIVALENT"
    elif inferred_cells and all(item.get("task_outcome") != "UNKNOWN" for item in inferred_cells):
        pair_relation = "TASK_CRITICAL"
    return {
        "challenge_id": str(case.get("challenge_id", "")),
        "group_id": str(case.get("group_id", "")),
        "case_type": str(case.get("case_type", "")),
        "tags": list(case.get("tags", [])),
        "language": {
            "structured_parse": parse,
            "candidate_interpretations": candidates,
            "valid_candidate_ids": [str(item.get("candidate_id")) for item in valid],
            "valid_symbolic_target_ids": sorted(
                str(item.get("candidate_specific_task_binding", {}).get("symbolic_target_id"))
                for item in valid
                if item.get("candidate_specific_task_binding", {}).get("symbolic_target_id") is not None
            ),
            "valid_runtime_candidate_ids": [str(item.get("candidate_id")) for item in valid],
            "candidate_id_to_runtime_id": {str(item.get("candidate_id")): str(item.get("candidate_id")) for item in valid},
            "duplicate_detected": any(item.get("candidate_status") == "DUPLICATE" for item in candidates),
            "grounding_abstained": any(item.get("candidate_status") in {"UNGROUNDED", "CONTRADICTORY", "UNSUPPORTED"} for item in candidates),
            "question_status": language.get("question_proposal", {}).get("proposal_status", "QUESTION_NOT_REALIZABLE"),
            "question_proposal": language.get("question_proposal"),
            "deterministic_runtime_sha256": language.get("language_output_sha256"),
        },
        "consequence": {
            "model_component": "CandidateSpecificConsequenceEngineV1",
            "learned_head_status": "LEGACY_RESEARCH_PROTOTYPE_NOT_DEPLOYED_IN_V1",
            "candidate_outcomes": {
                candidate_id: {
                    "label": next(
                        (
                            item.get("task_outcome", "UNKNOWN")
                            for item in inferred_cells
                            if item.get("action_candidate_id") == candidate_id and item.get("hypothesis_candidate_id") == candidate_id
                        ),
                        "UNKNOWN",
                    )
                }
                for candidate_id in inferred.get("candidate_ids", [])
            },
            "counterfactual_cell_predictions": [
                {
                    "action_candidate_id": item.get("action_candidate_id"),
                    "hypothesis_candidate_id": item.get("hypothesis_candidate_id"),
                    "label": item.get("task_outcome", "UNKNOWN"),
                    "abstained": item.get("task_outcome") == "UNKNOWN",
                }
                for item in inferred_cells
            ],
            "pair_relation": pair_relation,
            "pair_abstained": pair_relation == "UNKNOWN",
            "task_conditioned_status": output.get("consequence_result", {}).get("inference_status"),
            "runtime_declared_matrix_was_not_used_as_m1_label": True,
            "declared_matrix_decision_only": output.get("matrix_source") == "DECLARED_SYMBOLIC_STRESS_INPUT",
            "control_authorized": False,
        },
        "decision": {
            "decision_input_source": output.get("matrix_source"),
            "recommendation": decision,
            "selected_expected_loss": None,
            "deterministic_runtime_sha256": output.get("deterministic_output_sha256"),
            "override_applied": False,
            "used_for_control": False,
            "authorization_eligible": False,
            "control_authorized": False,
            "live_ask_issued": False,
            "live_wait_controller_invoked": False,
            "vehicle_control_generated": False,
        },
        "integrated_output": output,
        "errors": [],
    }


def build_prediction(runtime_path: Path, package_dir: Path) -> dict[str, Any]:
    runtime_hash = file_sha256(runtime_path)
    if runtime_hash != EXPECTED_RUNTIME_SHA256:
        raise RuntimeError(f"RUNTIME_HASH_MISMATCH:{runtime_hash}")
    document = json.loads(runtime_path.read_text(encoding="utf-8"))
    cases = document.get("cases")
    if not isinstance(cases, list) or len(cases) != 112:
        raise RuntimeError("RUNTIME_CASE_COUNT_MISMATCH")
    runtime = IntegratedRuntimeV1()
    outputs = [runtime.run(case) for case in cases]
    predictions = [_compatibility_projection(case, output) for case, output in zip(cases, outputs)]
    failures = [item["challenge_id"] for item in predictions if item["errors"]]
    missing = [
        item["challenge_id"]
        for item in predictions
        if not isinstance(item.get("decision", {}).get("recommendation"), Mapping)
    ]
    distribution: dict[str, int] = {}
    for item in predictions:
        decision = str(item["decision"]["recommendation"].get("decision", "MISSING"))
        distribution[decision] = distribution.get(decision, 0) + 1
    prediction = {
        "schema_version": "driveclarify.m2c_post_blind_repair_prediction.v1",
        "designation": "POST_BLIND_REPAIR_REGRESSION",
        "evidence_status": [
            "M2C_ALREADY_EXPOSED",
            "NOT_FRESH_BLIND",
            "NOT_GENERALIZATION_EVIDENCE",
            "NOT_PRIMARY_EVIDENCE",
            "NOT_PAPER_RESULT",
        ],
        "runtime_input_sha256": runtime_hash,
        "runtime_case_count": len(cases),
        "prediction_case_count": len(predictions),
        "failure_case_ids": failures,
        "missing_case_ids": missing,
        "decision_distribution": dict(sorted(distribution.items())),
        "product_source_hashes": product_source_hashes(package_dir),
        "execution_contract": {
            "training_performed": False,
            "threshold_or_policy_tuning_performed": False,
            "evaluation_labels_loaded": False,
            "control_authorized": False,
            "live_actions_issued": False,
            "carla_launch_count": 0,
            "simlingo_model_or_checkpoint_load_count": 0,
            "gpu_or_cuda_used": False,
        },
        "predictions": predictions,
    }
    return {**prediction, "prediction_content_sha256": stable_sha256(prediction)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--package-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    prediction = build_prediction(args.input.resolve(), args.package_dir.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(prediction))
    print(json.dumps({
        "prediction_case_count": prediction["prediction_case_count"],
        "failure_case_ids": prediction["failure_case_ids"],
        "missing_case_ids": prediction["missing_case_ids"],
        "decision_distribution": prediction["decision_distribution"],
        "output_sha256": file_sha256(args.output),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

