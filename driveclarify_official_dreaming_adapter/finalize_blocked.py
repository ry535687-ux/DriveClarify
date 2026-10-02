"""Seal the protocol-mandated blocked result without inventing skipped evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
from PIL import Image

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/driveclarify_official_dreaming_adapter_alignment"
ARTIFACT = ROOT / "artifacts/driveclarify_official_dreaming_adapter_alignment"
LIVE_ROOT = ARTIFACT / "own_scene_explicit_a1b1_attempt4"
STATUS = "BLOCKED_ADAPTER_ALIGNED_OWN_SCENE_EXPLICIT_BRANCHING_NOT_IDENTIFIED"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def corrected_visual(live: dict[str, Any]) -> None:
    rows = live["candidate_plan_repetitions"]
    a = next(row for row in rows if row["candidate_id"] == "A1")
    b = next(row for row in rows if row["candidate_id"] == "B1")
    image = Image.open(LIVE_ROOT / "same_observation_rgb_0.png").convert("RGB")
    ra, rb = np.asarray(a["route"]), np.asarray(b["route"])
    fig = plt.figure(figsize=(16, 8), constrained_layout=True)
    grid = fig.add_gridspec(2, 2, width_ratios=(1.05, 1.2), height_ratios=(3, 1))
    ax_image = fig.add_subplot(grid[0, 0])
    ax_image.imshow(image)
    ax_image.set_title("Same live CARLA RGB · TRAIN LOCAL-06")
    ax_image.axis("off")
    ax_text = fig.add_subplot(grid[1, 0])
    ax_text.axis("off")
    ax_text.text(0, .87, "A · exact official lane-change phrase", color="#d88b00", weight="bold")
    ax_text.text(0, .66, "Move one lane towards the right.", fontsize=11)
    ax_text.text(0, .42, "B · exact official current-lane phrase", color="#008fc9", weight="bold")
    ax_text.text(0, .21, "Continue driving on your current lane.", fontsize=11)
    ax_text.text(0, -.04, "Protocol caveat: A is not a junction-right instruction.", color="#b22222", weight="bold")
    ax = fig.add_subplot(grid[:, 1])
    ax.plot(ra[:, 1], ra[:, 0], color="#f3ad3d", lw=4, label="REAL Plan A · rightward/lane-change-like")
    ax.plot(rb[:, 1], rb[:, 0], color="#39bdf8", lw=4, label="REAL Plan B · observed NON-STRAIGHT")
    ax.scatter([0], [0], c="black", s=55, label="ego")
    ax.set_xlabel("lateral coordinate (m)")
    ax.set_ylabel("forward coordinate (m)")
    ax.axis("equal")
    ax.grid(alpha=.25)
    ax.legend(loc="best")
    ax.set_title("BLOCKED: no topology-level RIGHT vs STRAIGHT\nRMSE 0.825 m · max separation 1.515 m")
    fig.savefig(ARTIFACT / "OWN_SCENE_EXPLICIT_AB.png", dpi=180)
    plt.close(fig)


def main() -> int:
    live = load(LIVE_ROOT / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json")
    result = load(LIVE_ROOT / "OFFICIAL_ALIGNED_EXPLICIT_RESULT.json")
    matrix = load(REPORT / "OLD_ADAPTER_INTERVENTION_MATRIX.json")
    parity_in = load(REPORT / "NEW_ADAPTER_PARITY_INPUT_AUDIT.json")
    parity_out = load(REPORT / "NEW_ADAPTER_PARITY_OUTPUT_AUDIT.json")
    rows = live["candidate_plan_repetitions"]
    a = next(row for row in rows if row["candidate_id"] == "A1")
    b = next(row for row in rows if row["candidate_id"] == "B1")
    correction = {
        "schema_version": "driveclarify.adapter_root_cause_attribution.v2",
        "status": "EXACT_CAUSAL_ROOT_UNRESOLVED",
        "historical_observation": "Old live RIGHT/STRAIGHT diagnostic did not reach topology-level branching.",
        "official_observation": "Official same-RGB left/right pair clearly branches.",
        "controlled_ladder_result": [
            {
                "step": row["step"],
                "route_rmse_m": row["equal_spaced_route_divergence"]["rmse_m"],
                "max_separation_m": row["equal_spaced_route_divergence"]["max_separation_m"],
                "official_level_branching": row["official_level_branching"],
                "intervention": row["intervention"],
            }
            for row in matrix["rows"]
        ],
        "recovery_point": None,
        "exact_official_output_recovery_point": "R2_SUFFIX_REMOVAL",
        "causal_findings": {
            "historical_suffix_attenuates_official_pair": True,
            "historical_suffix_causes_collapse_on_official_pair": False,
            "historical_adapter_format_sufficient_to_explain_old_collapse": False,
            "navigation_target_dominance_confirmed": False,
            "scene_vs_instruction_distribution_isolated": False,
            "postprocess_hides_raw_divergence": False,
        },
        "authenticity_caveats": [
            "R1 reconstructs the historical model-consumed question and empty placeholder map through the released helper because the historical builder's unused offset-mapping request is unsupported by the official slow tokenizer.",
            "Forward entry and official sample image are held official throughout; R4-R6 are documented no-op rows rather than invented differences.",
            "The ladder never reproduces collapse, so it cannot identify a collapse-to-recovery transition.",
            "No experiment independently separates live image domain, junction geometry, and instruction distribution.",
        ],
        "defensible_conclusion": "The old prompt format is not causally sufficient for collapse on the controlled official pair. The exact cause of the historical live topology collapse remains unresolved.",
        "causal_confidence": "HIGH_FOR_NEGATIVE_SUFFICIENCY_FINDING; INSUFFICIENT_FOR_EXACT_ROOT_CAUSE",
        "withdrawn_claim": "LIKELY_NAVIGATION_TARGET_DOMINANCE",
    }
    dump(REPORT / "ADAPTER_ROOT_CAUSE_ATTRIBUTION.json", correction)
    matrix["interpretation_caveat"] = correction["authenticity_caveats"]
    dump(REPORT / "OLD_ADAPTER_INTERVENTION_MATRIX.json", matrix)

    explicit = {
        "schema_version": "driveclarify.own_scene_explicit_branching_audit.v1",
        "status": STATUS,
        "fixture": "LOCAL-06",
        "split": "TRAIN",
        "selected_execution": str(LIVE_ROOT),
        "implementation_bringup_attempts": [
            {"attempt": 1, "status": "BLOCKED_LIVE_DTYPE_AUTocast_MISMATCH"},
            {"attempt": 2, "status": "BLOCKED_LIVE_DTYPE_AUTocast_MISMATCH"},
            {"attempt": 3, "status": "BLOCKED_LIVE_DTYPE_AUTocast_MISMATCH_WITH_DIAGNOSTIC"},
            {"attempt": 4, "status": "VALID_MODEL_OUTPUT_GATE_EVALUATED"},
        ],
        "instruction_A": "Move one lane towards the right.",
        "instruction_B": "Continue driving on your current lane.",
        "instruction_provenance": "Exact released Eval_Dreamer corpus strings.",
        "semantic_protocol_caveat": "The released Dreamer index contains no right-turn/junction instruction; A is a lane-change phrase and does not satisfy the requested upcoming-junction RIGHT semantic.",
        "same_observation": result["same_observation"],
        "same_rgb_sha256": live["same_observation"]["rgb_0_sha256"],
        "same_non_language_input_hash": live["non_language_input_hash_a"] == live["non_language_input_hash_b"],
        "navigation_A_B": "OMITTED_FROM_PROMPT_OFFICIAL_NO_NAVIGATION_BRANCH",
        "shared_base_target_tensor_transported_but_unconsumed": True,
        "candidate_specific_numeric_target_point": False,
        "target_placeholder_count_A_B": [a["forward_evidence"]["target_point_placeholder_count"], b["forward_evidence"]["target_point_placeholder_count"]],
        "target_embedding_injected_A_B": [a["forward_evidence"]["target_point_embedding_injected"], b["forward_evidence"]["target_point_embedding_injected"]],
        "candidate_A_endpoint": result["candidate_A_endpoint"],
        "candidate_B_endpoint": result["candidate_B_endpoint"],
        "candidate_A_geometry_class": "RIGHTWARD_LANE_CHANGE_LIKE_NOT_VERIFIED_JUNCTION_TURN",
        "candidate_B_geometry_class": result["candidate_B_maneuver"],
        "route_rmse_m": result["route_rmse_m"],
        "max_separation_m": result["max_separation_m"],
        "fraction_waypoints_separated_ge_1m": result["fraction_waypoints_separated_ge_1m"],
        "speed_divergence_rmse": live["speed_divergence_rmse"],
        "hard_gates": result["hard_gates"],
        "topology_level_right_vs_straight": False,
        "visually_obvious_right_vs_straight": False,
        "model_forward_count": live["candidate_simlingo_forward_count"],
        "candidate_control_write_count": live["candidate_direct_vehicle_control_write_count"],
        "new_pid_count": live["new_pid_count"],
        "native_cleanup": load(LIVE_ROOT / "GROUNDED_LANGUAGE_V1_NATIVE_RUN_RECEIPT.json")["cleanup_status"],
        "gold_read_evidence": "CODE_REVIEW_FOUND_NO_GOLD_READ; NATIVE_GENERIC_RUN_RECEIPT_FIELD_IS_NULL_NOT_AFFIRMATIVE_ZERO",
    }
    dump(REPORT / "OWN_SCENE_EXPLICIT_BRANCHING_AUDIT.json", explicit)
    dump(REPORT / "OWN_SCENE_EXPLICIT_REPEAT_AUDIT.json", {
        "status": "NOT_RUN_EXPLICIT_A1_B1_TOPOLOGY_GATE_FAILED",
        "repeat_count_A": 0,
        "repeat_count_B": 0,
        "reason": STATUS,
    })
    skip = {
        "status": "NOT_RUN_EXPLICIT_GATE_FAILED",
        "blocking_status": STATUS,
        "grounding_dino_forward_count": 0,
        "white_van_simlingo_forward_count": 0,
        "effective_k": None,
        "target_A": None,
        "target_B": None,
    }
    for name in (
        "GROUNDED_WHITE_VAN_INPUT_AUDIT.json",
        "GROUNDED_WHITE_VAN_REPEAT_AUDIT.json",
        "GROUNDED_WHITE_VAN_PLAN_DIVERGENCE.json",
        "CONSEQUENCE_SHADOW_AUDIT.json",
    ):
        dump(REPORT / name, {**skip, "artifact": name})

    corrected_visual(live)
    dump(ARTIFACT / "own_scene_candidate_A_pred_route.json", {"source": "REAL_SIMLINGO_OFFICIAL_EQUAL_SPACED_ROUTE", "route": a["route"], "sha256": a["route_sha256"]})
    dump(ARTIFACT / "own_scene_candidate_B_pred_route.json", {"source": "REAL_SIMLINGO_OFFICIAL_EQUAL_SPACED_ROUTE", "route": b["route"], "sha256": b["route_sha256"]})
    dump(ARTIFACT / "own_scene_candidate_A_pred_speed.json", {"source": "REAL_SIMLINGO_PRED_SPEED", "speed": a["speed"], "sha256": a["speed_sha256"]})
    dump(ARTIFACT / "own_scene_candidate_B_pred_speed.json", {"source": "REAL_SIMLINGO_PRED_SPEED", "speed": b["speed"], "sha256": b["speed_sha256"]})
    visual_audit = {
        "status": "PARTIAL_EXPLICIT_FAILURE_EVIDENCE_ONLY",
        "corrected_explicit_panel": str(ARTIFACT / "OWN_SCENE_EXPLICIT_AB.png"),
        "original_runtime_panel_retained": str(LIVE_ROOT / "DISTINCT_LOCAL_CANDIDATE_PANEL.png"),
        "original_runtime_panel_caveat": "Its static A RIGHT/B STRAIGHT labels are misleading and are superseded by the corrected panel.",
        "white_van_panel": "NOT_CREATED_EXPLICIT_GATE_FAILED",
        "white_van_native_desktop": "NOT_CREATED_EXPLICIT_GATE_FAILED",
        "real_simlingo_pred_route_only": True,
        "visualization_induced": {
            "dino_forward": live["visualization_induced_detector_forward_count"],
            "simlingo_forward": live["visualization_induced_simlingo_forward_count"],
            "planner_advance": live["visualization_induced_planner_advance_count"],
            "pid": live["visualization_induced_pid_count"],
            "control_mutation": live["visualization_induced_vehicle_control_mutation_count"],
        },
    }
    dump(REPORT / "VISUALIZATION_AUDIT.json", visual_audit)
    (REPORT / "INDEPENDENT_REVIEW.md").write_text(
        "# Independent read-only review\n\n"
        "Verdict: the blocked status and lower-level official parity are supported, but the exact historical root cause is not established.\n\n"
        "Major findings: R1 reconstructs the historical consumed query through the released helper and R4–R6 are no-op rows, so no causal collapse/recovery transition exists; scene versus instruction was not isolated. The own-scene A phrase is an official lane-change instruction, not a junction-right instruction, and endpoint lateral displacement must not be called a verified topology turn. Both live paths bend similarly and B is NON_STRAIGHT.\n\n"
        "Checks: SimLingo/checkpoint/E3 identities exact; same A/B observation and non-language hashes; no candidate-specific target, target token, or embedding; real model outputs and official equal-spacing; no candidate control; passive visualization counters zero; no scenario-ID answer lookup or gold-read code found. Caveats: live drops unconsumed placeholder transport while parity preserves it; generic native gold counter is null; original runtime panel labels are misleading; repeats/white-van correctly not run. Focused reviewer tests: 6 passed.\n",
        encoding="utf-8",
    )
    regression = {
        "schema_version": "driveclarify.adapter_alignment.full_regression_receipt.v1",
        "focused_affected": {
            "status": "PASS",
            "tests": 55,
            "failures": 0,
            "command": "pytest tests/official_dreaming_adapter tests/simlingo_local_candidate_diagnostic tests/grounded_language_v1_controlled_integration tests/grounded_language_v1_extension_e1_r1 tests/temporal_grounding_v1 tests/paper_mvp_stage6b tests/paper_mvp_stage6b_r0",
        },
        "repository_per_directory": {
            "status": "NOT_ALL_GREEN_PREEXISTING_UNRELATED_ENVIRONMENT_BASELINE_FAILURES",
            "directories": 63,
            "green": 58,
            "failed": 5,
            "failed_directories": ["candidate_stability_offline", "m1_real_dataset_expansion_v2", "m1_real_dataset_expansion_v3", "m3_shadow_bridge", "multi_topology_static_units"],
            "failure_causes": ["Python-3.8 ElementTree.indent unavailable", "frozen float-byte regeneration differences", "torch already imported under SimLingo test environment", "pre-existing M3 bridge __dict__/slots contract"],
        },
        "monolithic_collection": "INVALID_LEGACY_TOP_LEVEL_SUPPORT_MODULE_COLLISION_AND_IMPORT_TIME_SYSTEMEXIT",
        "regression_conclusion": "Focused affected/Stage6B regression is green; repository-wide all-green acceptance is not met.",
    }
    dump(REPORT / "FULL_REGRESSION_RECEIPT.json", regression)

    final = {
        "schema_version": "driveclarify.official_dreaming_adapter_alignment.final_receipt.v1",
        "created_at": datetime.now().astimezone().isoformat(),
        "status": STATUS,
        "adapter": "OfficialDreamingCandidateAdapter.v1",
        "official_input_parity": parity_in["status"],
        "official_output_parity": parity_out["status"],
        "root_cause": correction["status"],
        "recovery_point": None,
        "exact_official_output_recovery_point": "R2_SUFFIX_REMOVAL",
        "own_scene_explicit": explicit,
        "own_scene_repeats": "NOT_RUN",
        "grounded_white_van": "NOT_RUN",
        "consequence_shadow": "NOT_RUN",
        "natural_decision": "NOT_APPLICABLE_EXPLICIT_DIAGNOSTIC",
        "forced_decision_count": 0,
        "training_steps": 0,
        "optimizer_steps": 0,
        "backward_calls": 0,
        "weight_updates": 0,
        "candidate_vehicle_control_writes": 0,
        "simlingo_source_modifications": 0,
        "simlingo": {"head": "743b243afd6cf5ff51b9fa1f8cac86f22d569684", "protected_diff_sha256": "dad60227cada5a17ba336881e583480e83eb272331e72be3c146ec96abe2d058", "checkpoint_sha256": "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28"},
        "e3": {"scheduled": 216, "started": 111, "valid": 110, "blocked_slot": 111, "unchanged": True, "hashes": ["9a7bba939f6865247674260dacd9c92d61561d691b3ae3ad0fb43d3b062fcc14", "bd546a75126b782b547ea5633ab10272f29ade884b8fe053ffdcd6cae880f798", "0e252efd2f1b9bc1bd9ae822d87da853fbaa1faabebc334d891e8ccbd0b99eeb"]},
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
        "visualization": visual_audit,
        "regression": regression,
        "independent_review": "BLOCKED_STATUS_CONFIRMED_WITH_MAJOR_CAUSAL_AND_SEMANTIC_CAVEATS_INCORPORATED",
        "cleanup": "PASS_NO_CARLA_EVALUATOR_OR_GPU_PROCESS",
        "single_next_action": "REQUEST_SEPARATE_PROTOCOL_REVISION_FOR_A_VALID_NO_NAVIGATION_JUNCTION_RIGHT_LANGUAGE_CONDITION; DO_NOT_RUN_WHITE_VAN_E3_DEV_OR_TEST",
    }
    dump(REPORT / "ADAPTER_ALIGNMENT_FINAL_RECEIPT.json", final)
    (REPORT / "ADAPTER_ALIGNMENT_FINAL_REPORT.md").write_text(
        "# DriveClarify official Dreaming adapter alignment\n\n"
        f"Final status: `{STATUS}`.\n\n"
        "The adapter achieves exact input/output parity on both official reference conditions. The controlled ladder shows that the historical suffix attenuates official-pair branching but does not collapse it; therefore the exact historical root cause is unresolved and no causal recovery intervention can be claimed.\n\n"
        "The live gate is blocked twice over: the released Dreamer corpus provides no valid junction-right instruction, so the exact A phrase is lane-change rather than junction-turn semantics; and the observed B plan is not straight. The genuine same-observation routes reach only 0.824918 m RMSE / 1.514842 m maximum separation, with endpoints A [17.5, 3.28125] and B [17.625, 1.921875]. Repeats, Grounding DINO white-van integration, consequence shadow, and final native screenshots were therefore not run.\n\n"
        "Focused regression is 55/55 green; full repository all-green acceptance is not met because five unrelated legacy/environment test directories fail. SimLingo, checkpoint, E3, DEV, and TEST remain protected.\n",
        encoding="utf-8",
    )
    report_hashes = {
        str(path.relative_to(ROOT)): sha(path)
        for path in sorted(REPORT.rglob("*"))
        if path.is_file() and path.name != "ARTIFACT_HASHES.json"
    }
    artifact_hashes = {
        str(path.relative_to(ROOT)): sha(path)
        for path in sorted(ARTIFACT.rglob("*"))
        if path.is_file()
    }
    source_paths = [
        ROOT / "driveclarify_official_dreaming_adapter/__init__.py",
        ROOT / "driveclarify_official_dreaming_adapter/adapter.py",
        ROOT / "driveclarify_official_dreaming_adapter/renderer.py",
        ROOT / "driveclarify_official_dreaming_adapter/runtime.py",
        ROOT / "driveclarify_official_dreaming_adapter/offline_alignment.py",
        ROOT / "driveclarify_official_dreaming_adapter/live_runner.py",
        ROOT / "driveclarify_official_dreaming_adapter/finalize_blocked.py",
        ROOT / "driveclarify_grounded_language_v1/runtime.py",
        ROOT / "driveclarify_simlingo_local_candidate_diagnostic/runtime.py",
        ROOT / "tests/official_dreaming_adapter/test_renderer.py",
        ROOT / "CURRENT_HANDOFF.md",
        ROOT / "STATE.json",
        ROOT / "AGENT_WORKLOG.md",
    ]
    dump(REPORT / "ARTIFACT_HASHES.json", {
        "schema_version": "driveclarify.official_dreaming_adapter_alignment.artifact_hashes.v1",
        "reports": report_hashes,
        "artifacts": artifact_hashes,
        "source_and_sot": {str(path.relative_to(ROOT)): sha(path) for path in source_paths},
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
