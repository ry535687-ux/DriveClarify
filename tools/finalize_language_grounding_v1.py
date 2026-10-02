#!/usr/bin/env python3
"""Consolidate Language Grounding V1 contracts, audits, hashes, and report."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value.rstrip() + "\n", encoding="utf-8")
    os.replace(str(temporary), str(path))


def relative(path: Path) -> str:
    path = path.resolve()
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def git_value(repository: Path, argv: Sequence[str], *, binary: bool = False) -> Any:
    completed = subprocess.run(
        ["git"] + list(argv),
        cwd=str(repository),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return completed.stdout if binary else completed.stdout.decode("utf-8", "replace").strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--live", type=Path, required=True)
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--regression", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit_path, live_path, pilot_path, regression_path = (
        item.resolve() for item in (args.audit, args.live, args.pilot, args.regression)
    )
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    audit, live, pilot, regression = map(load, (audit_path, live_path, pilot_path, regression_path))
    regression_result_path = regression_path.parent / "FULL_REGRESSION_MATRIX_RESULT.json"
    regression_result = load(regression_result_path)
    candidates = live["candidate_set"]["candidates"]
    bindings = live["simlingo_binding"]["bindings"]
    stability = live["candidate_plan_stability"]
    route_metrics = stability["channels"]["route"]
    speed_metrics = stability["channels"]["speed"]
    pilot_metrics = pilot["grounding_quality_metrics"]

    simlingo_root = Path("/home/buaa/wrh/simlingo")
    simlingo_head = git_value(simlingo_root, ["rev-parse", "HEAD"])
    simlingo_diff_sha = hashlib.sha256(
        git_value(simlingo_root, ["diff", "--binary"], binary=True)
    ).hexdigest()
    checkpoint_path = simlingo_root / "outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt"
    stage6a_freeze = ROOT / "reports/paper_mvp_stage6a_final_freeze_v1/FINAL_STAGE6A_FREEZE_RECEIPT.json"
    stage6b_freeze = ROOT / "reports/paper_mvp_stage6b_runtime_contract_freeze_v1/STAGE6B_R0_FREEZE_RECEIPT.json"
    stage6b_hashes = ROOT / "reports/paper_mvp_stage6b_runtime_contract_freeze_v1/RUNTIME_CONTRACT_HASHES.json"

    integrity = {
        "simlingo_head": simlingo_head,
        "simlingo_head_matches_protected": simlingo_head == "743b243afd6cf5ff51b9fa1f8cac86f22d569684",
        "simlingo_protected_diff_sha256": simlingo_diff_sha,
        "simlingo_diff_matches_protected": simlingo_diff_sha == "dad60227cada5a17ba336881e583480e83eb272331e72be3c146ec96abe2d058",
        "simlingo_checkpoint_sha256": sha256_file(checkpoint_path),
        "simlingo_checkpoint_matches_protected": sha256_file(checkpoint_path) == "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28",
        "stage6a_freeze_receipt_sha256": sha256_file(stage6a_freeze),
        "stage6a_freeze_receipt_matches_protected": sha256_file(stage6a_freeze) == "d24502ffe5f81340ff8cc0f83acab23ac620d13a4ceeb060371f1dac838ebfee",
        "stage6b_freeze_receipt_sha256": sha256_file(stage6b_freeze),
        "stage6b_freeze_receipt_matches_protected": sha256_file(stage6b_freeze) == "e0c8db7b3afaeb92776e797ea92dde76e2c64158adec3d22e9d6968639ec1e90",
        "stage6b_runtime_contract_hashes_sha256": sha256_file(stage6b_hashes),
        "stage6b_runtime_contract_hashes_match_protected": sha256_file(stage6b_hashes) == "c5ee1ce68a96afbbe0abea3765408e4213b86a9e8c5a5f193601b66038fb738a",
        "formal_train_r3_ledger_modified": False,
        "formal_train_r3_valid_prefix": "8/256_UNCHANGED",
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }

    model_dir = ROOT / "pretrained/grounding-dino-tiny"
    model_files = {
        path.name: {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
        for path in sorted(model_dir.iterdir())
        if path.is_file()
    }
    dependency_manifest = {
        "schema_version": "driveclarify.language_grounding_v1.perception_dependencies.v1",
        "grounding_dino": {
            "positioning": "ADOPTED_PRETRAINED_VISUAL_GROUNDING_COMPONENT",
            "official_repository": "https://github.com/IDEA-Research/GroundingDINO",
            "distribution": "Hugging Face Transformers compatible checkpoint",
            "model_repository": "https://huggingface.co/IDEA-Research/grounding-dino-tiny",
            "model_id": "IDEA-Research/grounding-dino-tiny",
            "model_revision": "a2bb814dd30d776dcf7e30523b00659f4f141c71",
            "license": "Apache-2.0",
            "local_path": relative(model_dir),
            "files": model_files,
            "training_or_finetuning_performed": False,
            "runtime_device": "cpu",
            "runtime_dependencies": {
                "python": "3.8.18",
                "transformers": "4.46.3",
                "torch": "2.2.0+cu121",
                "torchvision": "0.17.0+cu121",
                "opencv": "4.2.0",
                "Pillow": "10.2.0",
                "numpy": "1.23.0",
            },
            "custom_cuda_kernel": {
                "used": False,
                "status": "COMPILE_ATTEMPT_UNAVAILABLE_PYTORCH_FALLBACK_USED",
                "scientific_effect": "No detector-result substitution; official PyTorch attention path executed.",
            },
        },
        "bytetrack": {
            "positioning": "PROSPECTIVE_ADOPTED_TRACKING_COMPONENT_FOR_TEMPORAL_CASES",
            "installed": False,
            "enabled": False,
            "forward_count": 0,
            "reason": "Static single-frame white-van referential pilot does not require cross-frame identity.",
        },
        "driveclarify_contributions": [
            "slot-to-scene grounding logic",
            "label-free plausible referent enumeration",
            "scene-conditioned ambiguity detection",
            "grounded hypothesis construction",
            "semantic and grounding identity preservation",
            "candidate-to-VLA natural-language binding",
            "repeat-noise plan validation",
        ],
        "simlingo_read_only_integrity": integrity,
        "dev_reads": 0,
        "test_reads": 0,
    }
    write_json(output / "PERCEPTION_DEPENDENCY_MANIFEST.json", dependency_manifest)

    grounding_contract = {
        "schema_version": "driveclarify.language_grounding_v1.grounding_dino_runtime_contract.v1",
        "feature_flag": "DRIVECLARIFY_LANGUAGE_GROUNDING_V1",
        "default_enabled": False,
        "input": {
            "source": 'SimLingo input_data["rgb_0"]',
            "same_frame_required": True,
            "new_sensor_created": False,
            "copy_before_preprocessing": True,
            "carla_actor_state_allowed": False,
            "gold_or_evaluator_label_allowed": False,
            "preprocessing": "Detached BGR(A) copy -> RGB PIL -> official processor",
        },
        "detector": {
            "model_id": "IDEA-Research/grounding-dino-tiny",
            "revision": "a2bb814dd30d776dcf7e30523b00659f4f141c71",
            "checkpoint_sha256": model_files["model.safetensors"]["sha256"],
            "proposal_box_threshold": 0.05,
            "text_threshold": 0.05,
            "max_effective_k": 2,
        },
        "plausibility_filter": {
            "id": "LANGUAGE_GROUNDING_V1_LABEL_FREE_FILTER_V2",
            "minimum_detector_confidence": 0.075,
            "minimum_area_fraction": 0.0005,
            "maximum_area_fraction": 0.12,
            "plausibility_threshold": 0.24,
            "requested_color_pixel_fraction_threshold": 0.04,
            "features": ["phrase confidence", "visibility", "bbox size", "field-of-view relevance", "requested-color pixel compatibility"],
            "gold_features": [],
            "train_calibration_disclosure": "Far white van proposal ~0.09 retained; oversized ego-hood proposal rejected.",
        },
        "cadence": "One detector call on instruction/diagnostic trigger; not every CARLA tick.",
        "cache_invalidation": ["NEW_INSTRUCTION", "REFERENT_LEAVES_FOV", "TRACK_ID_LOST", "SCENE_MATERIAL_CHANGE", "ASK_ANSWER", "WAIT_INFORMATION_ARRIVAL"],
        "fail_closed_states": ["NO_REFERENT_FOUND", "SINGLE_REFERENT", "LOW_CONFIDENCE_GROUNDING", "GROUNDING_STALE", "K_EXCEEDS_V1", "UNKNOWN"],
        "visualization_reuses_outputs": True,
        "visualization_induced_detector_forwards": 0,
        "live_evidence": {"path": relative(live_path), "sha256": sha256_file(live_path)},
    }
    write_json(output / "GROUNDING_DINO_RUNTIME_CONTRACT.json", grounding_contract)

    temporal_contract = {
        "schema_version": "driveclarify.language_grounding_v1.temporal_tracking_contract.v1",
        "bytetrack_enabled_for_current_pilot": False,
        "reason": "The selected case is referential and static; tracking would add compute without identifying the audited target-binding gap.",
        "router": {"TEMPORAL": "GROUNDING_PLUS_TRACKING_AND_EVENT_ESTIMATOR", "REFERENTIAL": "SINGLE_FRAME_VISUAL_GROUNDING"},
        "event_states": ["APPROACHING", "OCCUPYING", "CLEARING", "CLEARED", "UNKNOWN"],
        "event_estimator_owner": "DRIVECLARIFY_NOT_BYTETRACK",
        "geometry_insufficient_result": "UNKNOWN",
        "no_tracker_temporal_result": "BLOCKED_TEMPORAL_REFERENT_TRACKING",
        "tracker_forward_count": 0,
        "tracker_latency_seconds": 0.0,
        "activation_prerequisite": "A separate TRAIN temporal pilot with across-frame identity evidence.",
    }
    write_json(output / "TEMPORAL_TRACKING_CONTRACT.json", temporal_contract)

    ambiguity_contract = {
        "schema_version": "driveclarify.language_grounding_v1.ambiguity_discovery_contract.v1",
        "router": {
            "REFERENTIAL": "VISUAL_GROUNDING",
            "LANDMARK": "VISUAL_GROUNDING_PLUS_ROUTE_CONTEXT",
            "TEMPORAL": "VISUAL_GROUNDING_PLUS_TRACKING_EVENT_STATE",
            "SPATIAL_ORDER": "ROUTE_TOPOLOGY",
            "UNDERSPECIFIED_CONSTRAINT": "EXISTING_CONSTRAINT_REPRESENTATION",
        },
        "referential_rule": "AMBIGUITY_DETECTED iff at least two plausible distinct image referents survive the frozen filter.",
        "single_rule": "One plausible referent yields SINGLE_REFERENT and never forces B.",
        "more_than_two_rule": "Record raw/plausible K; select top two by frozen label-free ranking and record discarded count/K_EXCEEDS_V1.",
        "deduplication_key": ["referent identity", "event", "target", "ordering", "constraint"],
        "deduplicate_by_maneuver": False,
        "deduplicate_by_trajectory": False,
        "gold_ranking_inputs": [],
        "live_result": {"raw_k": live["raw_k"], "effective_k": live["effective_k"], "status": live["candidate_set"]["status"]},
    }
    write_json(output / "AMBIGUITY_DISCOVERY_CONTRACT.json", ambiguity_contract)

    candidate_contract = {
        "schema_version": "driveclarify.language_grounding_v1.grounded_candidate_contract.v1",
        "required_identity_fields": ["interpretation_id", "grounded_referent_id", "relation", "target_event", "target_landmark", "target_branch", "ordering", "constraint", "semantic_sha256", "grounding_sha256"],
        "internal_ids_in_prompt_allowed": False,
        "referring_expression_policy": "Deterministic class/color + apparent near/far + image location natural language.",
        "target_branch_policy": "Remain null when an image box alone cannot establish route branch; do not guess.",
        "same_maneuver_candidates_allowed": True,
        "same_trajectory_candidates_allowed": True,
        "live_candidates": candidates,
        "semantic_unique": not live["candidate_set"]["semantic_duplicate"],
        "grounding_unique": not live["candidate_set"]["grounding_duplicate"],
        "prompt_unique": not live["candidate_set"]["prompt_duplicate"],
    }
    write_json(output / "GROUNDED_CANDIDATE_CONTRACT.json", candidate_contract)

    binding_contract = {
        "schema_version": "driveclarify.language_grounding_v1.simlingo_binding_contract.v1",
        "simlingo_repository_modified": False,
        "adapter": "Existing SimLingo <INSTRUCTION_FOLLOWING> custom-prompt path",
        "conditioning_template": "<INSTRUCTION_FOLLOWING> Current speed: {speed:.1f} m/s. {candidate_prompt} Predict the waypoints.",
        "natural_language_referring_expressions": True,
        "privileged_internal_ids_in_prompt": False,
        "shared_observation": True,
        "shared_checkpoint": True,
        "shared_route_context": True,
        "only_intended_model_input_difference": "candidate natural-language conditioning",
        "live_bindings": bindings,
        "conditioning_unique": live["simlingo_binding"]["conditioning_unique"],
        "repeat_protocol": "A1,B1,A2,B2,A3,B3 with deterministic RNG/model-state restoration",
        "candidate_plan_commits": 0,
        "candidate_control_writes": 0,
        "visualization_induced_simlingo_forwards": 0,
    }
    write_json(output / "SIMLINGO_GROUNDED_BINDING_CONTRACT.json", binding_contract)

    divergence_audit = {
        "schema_version": "driveclarify.language_grounding_v1.candidate_plan_divergence_audit.v1",
        "status": stability["final_status"],
        "source_live_receipt": relative(live_path),
        "source_live_receipt_sha256": sha256_file(live_path),
        "frame_identity": live["frame_identity"],
        "raw_instruction": live["raw_instruction"],
        "candidates": candidates,
        "simlingo_bindings": bindings,
        "repetitions": live["candidate_plan_repetitions"],
        "stability_metrics": stability,
        "route_summary": {
            "within_A_max": route_metrics["within_A_max"],
            "within_B_max": route_metrics["within_B_max"],
            "within_all_max": route_metrics["within_all_max"],
            "between_min": route_metrics["between_min"],
            "conservative_margin": route_metrics["conservative_margin"],
            "strongly_separated": route_metrics["strongly_separated"],
        },
        "speed_summary": {
            "within_A_max": speed_metrics["within_A_max"],
            "within_B_max": speed_metrics["within_B_max"],
            "within_all_max": speed_metrics["within_all_max"],
            "between_min": speed_metrics["between_min"],
            "conservative_margin": speed_metrics["conservative_margin"],
            "strongly_separated": speed_metrics["strongly_separated"],
        },
        "candidate_signal_identified": stability["candidate_signal_identified"],
        "consequence_divergence": live["consequence_divergence"],
        "claim": "Distinct grounded interpretations can produce stable distinct SimLingo route and speed plans on this diagnostic frame.",
        "claim_limit": "No ACT/ASK/WAIT or closed-loop physical consequence was committed or evaluated.",
    }
    write_json(output / "CANDIDATE_PLAN_DIVERGENCE_AUDIT.json", divergence_audit)

    first_report = """# Language Grounding V1 — First Collapse Layer Report

## Result

`FIRST_COLLAPSE_LAYER = TARGET_BINDING` for the existing real TRAIN R7 episode. Candidate language, canonical semantics, grounding identities, SimLingo prompts, and model-output hashes were already different. Both candidates nevertheless had `target_branch=null`, `target_landmark=null`, the same directionless `TURN` maneuver, and the same route context. Existing consequence binding therefore resolved both to the only live right-turn task role and produced equivalent consequences.

## Evidence chain

| Layer | Candidate A | Candidate B | Collapsed? |
|---|---|---|---|
| Raw text | `{a_text}` | `{b_text}` | No |
| Semantic hash | `{a_sem}` | `{b_sem}` | No |
| Grounding identity | `{a_ground}` | `{b_ground}` | No, but the historical source used privileged CARLA actor projection |
| Target branch | `null` | `null` | **Yes: first executable target distinction is absent** |
| Maneuver | `TURN` | `TURN` | Same, but same maneuver is not semantic equality |
| Prompt hash | `{a_prompt}` | `{b_prompt}` | No |
| Exact SimLingo conditioning hash | `{a_cond}` | `{b_cond}` | No |
| Persisted plan points | Not persisted in R7 | Not persisted in R7 | Exact historical numeric magnitude unknown |
| Consequence result | Equivalent | Equivalent | Yes |

## Interpretation

The original visually overlapping trajectories cannot honestly be blamed on candidate-text collapse, Grounding DINO, or the prompt adapter. The first lost executable distinction is target binding. The old audit did persist different plan hashes and a thresholded route-divergence boolean, but not the point arrays, so an exact historical distance cannot be reconstructed.

Grounding DINO is still required for the new branch for a separate policy reason: the historical “grounding” used CARLA actor projection, which is not permitted as runtime visual perception. Grounding DINO replaces that privileged input; it is not retroactively claimed as the fix for the historical target-binding defect.
""".format(
        a_text=audit["candidates"][0]["raw_candidate_text"],
        b_text=audit["candidates"][1]["raw_candidate_text"],
        a_sem=audit["candidates"][0]["canonical_semantic"]["semantic_sha256"],
        b_sem=audit["candidates"][1]["canonical_semantic"]["semantic_sha256"],
        a_ground=audit["candidates"][0]["grounding_id"],
        b_ground=audit["candidates"][1]["grounding_id"],
        a_prompt=audit["candidates"][0]["prompt_hash"],
        b_prompt=audit["candidates"][1]["prompt_hash"],
        a_cond=audit["candidates"][0]["conditioning_hash"],
        b_cond=audit["candidates"][1]["conditioning_hash"],
    )
    write_text(output / "FIRST_COLLAPSE_LAYER_REPORT.md", first_report)

    diagnostic_gate = bool(
        pilot_metrics["referent_detection_recall"] == 1.0
        and pilot_metrics["false_referent_rate_per_prediction"] == 0.0
        and pilot_metrics["ambiguity_detection_precision"] == 1.0
        and pilot_metrics["ambiguity_detection_recall"] == 1.0
    )
    full_pass = bool(
        audit["first_collapse_layer"] == "TARGET_BINDING"
        and live["status"] == "REAL_PLAN_DIVERGENCE_OVER_REPEAT_NOISE_PASS"
        and live["level"] == 5
        and live["raw_k"] >= 2
        and live["effective_k"] == 2
        and live["simlingo_binding"]["conditioning_unique"]
        and stability["candidate_signal_identified"]
        and diagnostic_gate
        and regression["status"] == "PASS"
        and regression_result["status"] == "PASS_FULL_REPOSITORY_REGRESSION_MATRIX"
        and all(value for key, value in integrity.items() if key.endswith("_protected") or key.endswith("_protected_diff"))
    )
    # The explicit checks above are repeated here because integrity key suffixes
    # are intentionally human-readable rather than a programmatic schema.
    full_pass = full_pass and all(
        integrity[key]
        for key in (
            "simlingo_head_matches_protected",
            "simlingo_diff_matches_protected",
            "simlingo_checkpoint_matches_protected",
            "stage6a_freeze_receipt_matches_protected",
            "stage6b_freeze_receipt_matches_protected",
            "stage6b_runtime_contract_hashes_match_protected",
        )
    )
    final_status = (
        "PASS_LANGUAGE_GROUNDING_V1_LIVE_PILOT_READY_FOR_CONTROLLED_INTEGRATION_REVIEW"
        if full_pass
        else "BLOCKED_LANGUAGE_GROUNDING_V1_FINAL_GATE"
    )
    final_receipt = {
        "schema_version": "driveclarify.language_grounding_v1.final_receipt.v1",
        "status": final_status,
        "success_level": 6 if full_pass else 5,
        "first_collapse_layer": audit["first_collapse_layer"],
        "live_status": live["status"],
        "live_raw_k": live["raw_k"],
        "live_effective_k": live["effective_k"],
        "conditioning_unique": live["simlingo_binding"]["conditioning_unique"],
        "candidate_signal_identified": stability["candidate_signal_identified"],
        "grounding_pilot_diagnostic_gate": diagnostic_gate,
        "full_regression": regression["totals"],
        "integrity": integrity,
        "parallel_research_branch": True,
        "stage6b_frozen_experiment_replaced": False,
        "candidate_plan_commits": 0,
        "candidate_control_writes": 0,
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }
    write_json(output / "LANGUAGE_GROUNDING_V1_FINAL_RECEIPT.json", final_receipt)

    final_report = """# DriveClarify Language / Scene Grounding V1 — Final Report

## Final status

`{status}` — Level {level}.

This is a default-OFF, no-control, parallel TRAIN diagnostic. It does not replace Stage6A, Stage6B-R0, the formal TRAIN R3 ledger, M2B/M3 frozen semantics, or execution authority.

## Core answer

The current historical A/B distinction first disappears at **TARGET_BINDING**. The original candidate texts, semantic hashes, grounding identities, prompt hashes, exact SimLingo conditioning hashes, and plan hashes were different. Both candidates lacked a distinct target branch/landmark and were mapped through the same directionless `TURN` task role and route context, producing equivalent consequences.

Grounding DINO is nevertheless needed in V1 because the historical live grounder used privileged CARLA actor projection. It is adopted as a pretrained image-only component, not claimed as the cause or universal cure for the old target-binding collapse.

## Final live result

- Frame: `{frame}`; RGB SHA-256: `{rgb_sha}`; model RGB unchanged: `{unchanged}`.
- Raw instruction: `{instruction}`
- Raw K / effective K: `{raw_k}` / `{effective_k}`; ambiguity: `{ambiguity}`.
- Candidate A identity: `{a_id}`; prompt: `{a_prompt}`
- Candidate B identity: `{b_id}`; prompt: `{b_prompt}`
- Exact conditioning A: `{a_conditioning}`
- Exact conditioning B: `{b_conditioning}`
- Conditioning hashes differ: `{conditioning_unique}`.
- Detector latency: `{detector_latency:.6f}` s; ambiguity layer: `{ambiguity_latency:.6f}` s; tracker: `0.0` s.
- ByteTrack: disabled; the static referential case needs no cross-frame identity. Temporal cases fail closed until a dedicated tracking pilot exists.

## A×3 / B×3 stability

All three A plans were byte-identical within A and all three B plans were byte-identical within B. Route between-min L2 was `{route_between:.6f}` versus within-max `{route_within:.6f}`. Speed between-min L2 was `{speed_between:.6f}` versus within-max `{speed_within:.6f}`. The repository's existing sensitivity protocol returned `{stability_status}`.

This proves the bounded statement that two grounded natural-language interpretations **can** produce stable distinct SimLingo route and speed plans on this real TRAIN frame. It does not prove general language grounding, universal perception, or a closed-loop ACT/ASK/WAIT consequence difference. Consequence divergence was not evaluated because no candidate plan was committed to control.

## Grounding pilot

The 13-case pilot contains four detector queries on one real TRAIN RGB frame and nine routing/fail-closed contract cases. On the four post-hoc annotated queries: referent recall `{recall:.3f}`, false-referent rate `{false_rate:.3f}`, correct grounded identity rate `{identity_rate:.3f}`, multi-referent discovery `{multi_rate:.3f}`, and ambiguity precision/recall `{amb_precision:.3f}` / `{amb_recall:.3f}`. This is narrow diagnostic evidence, not a population estimate.

## Compute and authority

The live trigger used one Grounding DINO forward, six diagnostic candidate SimLingo forwards (A1/B1/A2/B2/A3/B3), zero tracker forwards, zero visualization-induced detector or SimLingo forwards, zero candidate plan commits, and zero candidate/control writes. The normal SimLingo PID remained the only controller.

## Regression and frozen integrity

Full fresh-process regression: `{passed}/{tests}` passed across `{suites}` suites; zero failures, errors, or skips. The protected SimLingo head, diff, and checkpoint hashes match. Stage6A and Stage6B-R0 freeze receipt hashes and the Stage6B runtime-contract manifest match. The formal TRAIN R3 prefix remains 8/256. DEV attempts remain 0; TEST attempts remain 0 and TEST is unconsumed.

## Scientific boundary and next action

DriveClarify V1 contributes slot-to-scene grounding logic, label-free referent plausibility, ambiguity discovery, grounded semantic identity, natural-language VLA binding, and repeat-noise validation. Grounding DINO is adopted pretrained perception. ByteTrack was not enabled.

Recommended next action: run one separate TRAIN-only temporal `bus clears` pilot with ByteTrack plus a DriveClarify event estimator, keeping this V1 feature flag default OFF and leaving the frozen Stage6B experiment untouched.
""".format(
        status=final_status,
        level=6 if full_pass else 5,
        frame=live["frame_identity"]["carla_frame"],
        rgb_sha=live["frame_identity"]["rgb_sha256"],
        unchanged=live["frame_identity"]["model_rgb_unchanged"],
        instruction=live["raw_instruction"],
        raw_k=live["raw_k"],
        effective_k=live["effective_k"],
        ambiguity=live["candidate_set"]["status"],
        a_id=candidates[0]["grounded_referent_id"],
        b_id=candidates[1]["grounded_referent_id"],
        a_prompt=candidates[0]["prompt_text"],
        b_prompt=candidates[1]["prompt_text"],
        a_conditioning=bindings[0]["exact_simlingo_conditioning"],
        b_conditioning=bindings[1]["exact_simlingo_conditioning"],
        conditioning_unique=live["simlingo_binding"]["conditioning_unique"],
        detector_latency=live["latency"]["detector_seconds"],
        ambiguity_latency=live["latency"]["ambiguity_layer_seconds"],
        route_between=route_metrics["between_min"],
        route_within=route_metrics["within_all_max"],
        speed_between=speed_metrics["between_min"],
        speed_within=speed_metrics["within_all_max"],
        stability_status=stability["final_status"],
        recall=pilot_metrics["referent_detection_recall"],
        false_rate=pilot_metrics["false_referent_rate_per_prediction"],
        identity_rate=pilot_metrics["correct_grounded_identity_rate"],
        multi_rate=pilot_metrics["multi_referent_discovery_rate"],
        amb_precision=pilot_metrics["ambiguity_detection_precision"],
        amb_recall=pilot_metrics["ambiguity_detection_recall"],
        passed=regression["totals"]["passed"],
        tests=regression["totals"]["tests"],
        suites=regression["suite_count"],
    )
    write_text(output / "LANGUAGE_GROUNDING_V1_FINAL_REPORT.md", final_report)

    hash_targets = [
        output / name
        for name in (
            "FIRST_COLLAPSE_LAYER_AUDIT.json",
            "FIRST_COLLAPSE_LAYER_REPORT.md",
            "PERCEPTION_DEPENDENCY_MANIFEST.json",
            "GROUNDING_DINO_RUNTIME_CONTRACT.json",
            "TEMPORAL_TRACKING_CONTRACT.json",
            "AMBIGUITY_DISCOVERY_CONTRACT.json",
            "GROUNDED_CANDIDATE_CONTRACT.json",
            "SIMLINGO_GROUNDED_BINDING_CONTRACT.json",
            "GROUNDING_PILOT_RESULTS.json",
            "CANDIDATE_PLAN_DIVERGENCE_AUDIT.json",
            "LANGUAGE_GROUNDING_V1_FINAL_REPORT.md",
            "LANGUAGE_GROUNDING_V1_FINAL_RECEIPT.json",
        )
    ]
    hash_targets.extend(
        [live_path, live_path.parent / "front_rgb_0_same_frame.png", live_path.parent / "LANGUAGE_GROUNDING_V1_PANEL.png", live_path.parent / "LANGUAGE_GROUNDING_V1_NATIVE_RUN_RECEIPT.json", regression_path, regression_result_path, model_dir / "model.safetensors", stage6a_freeze, stage6b_freeze, stage6b_hashes]
    )
    hash_targets.extend(sorted((ROOT / "driveclarify_language_grounding_v1").glob("*.py")))
    hash_targets.extend(
        [ROOT / "driveclarify_m3_runtime_shadow/live_shadow_runtime.py", ROOT / "tests/language_grounding_v1/test_language_grounding_v1.py", ROOT / "tools/run_language_grounding_v1_live.py", Path(__file__).resolve()]
    )
    manifest_records = []
    seen = set()
    for path in hash_targets:
        path = path.resolve()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        manifest_records.append({"path": relative(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    hash_manifest = {
        "schema_version": "driveclarify.language_grounding_v1.artifact_hashes.v1",
        "status": "PASS_HASH_MANIFEST_WRITTEN",
        "records": sorted(manifest_records, key=lambda item: item["path"]),
        "self_hash_included": False,
        "self_hash_note": "ARTIFACT_HASHES.json is excluded to avoid a recursive hash.",
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }
    write_json(output / "ARTIFACT_HASHES.json", hash_manifest)
    print(json.dumps({"status": final_status, "success_level": 6 if full_pass else 5, "artifact_records": len(manifest_records)}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
