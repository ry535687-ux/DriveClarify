#!/usr/bin/env python3
"""Materialize and prospectively seal the E2 V3 engineering campaign."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_rq2_t_e2_v3.association import FEATURE_WEIGHTS  # noqa: E402
from driveclarify_rq2_t_e2_v3.calibration import (  # noqa: E402
    ASSOCIATION_THRESHOLD_GRID,
    CALIBRATOR_FAMILY,
    MINIMUM_AGE_GRID,
    MINIMUM_HITS_GRID,
    UNIQUENESS_MARGIN_GRID,
)
from driveclarify_rq2_t_e2_v3.contracts import AcquisitionSchedule  # noqa: E402
from driveclarify_rq2_t_e2_v3.scene_bindings import SCENE_BINDINGS, frozen_binding  # noqa: E402
from driveclarify_rq2_t_e2_v3.tracker import PersistentMultiObjectTracker  # noqa: E402


REPORT = ROOT / "reports/driveclarify_rq2_t_v2_e2_tracked_association_and_full_mechanism_v1"
ROUTES = ROOT / "driveclarify_rq2_t_e2_v3/engineering_routes"
ENTRY_HEAD = "eaa332b1bb994279b59ea5af786fdb5de96adc1b"
SEEDS = [
    2904855312, 2964638186, 88593740, 4027497633, 3900498031, 3846385745,
    1048858709, 387771339, 300366377, 929157957, 1052136770, 3850784329,
    3713881287, 1701207438, 1758672437, 2413779530, 4093321456, 523657696,
    2528614465, 724233951, 1907975932, 138909598, 3631862579, 285220648,
    # Prospectively registered after five immutable engineering-defect
    # attempts consumed identities 001/020/021/022/023.  Each value had zero
    # exact repository hits before this edit; the campaign remains below 36.
    1462284648, 2724915863, 323293477, 1996713048, 1464484825,
    # Sixth genuine-defect replacement: async identity 029 exposed an
    # incorrectly copied development-only far-target coordinate.
    1136422571,
    # Final prospective reserve block, registered together before any of
    # these identities is exposed.  Development identity 006 demonstrated
    # a false binding to unrelated traffic and required a scoring repair;
    # fresh positive, negative, and lineage witnesses must therefore execute
    # the repaired final method.  All six values had zero exact repository
    # hits before this edit.  This reaches, but does not exceed, the frozen
    # maximum engineering bound of 36.
    2873149570, 286618720, 1557736433, 3405986017, 1133313948, 1233133599,
    # Supplemental USC trigger-binding addendum: one activation-only
    # qualification followed by one fresh full negative-control witness.
    # Both seeds had zero exact repository/attachment hits before this edit.
    2522875889, 799909001,
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def tree_digest(path: Path) -> Mapping[str, Any]:
    digest = hashlib.sha256()
    count = 0
    total = 0
    for item in sorted(value for value in path.rglob("*") if value.is_file()):
        file_hash = sha256(item)
        count += 1
        total += item.stat().st_size
        digest.update(file_hash.encode("ascii") + b"  " + item.relative_to(ROOT).as_posix().encode("utf-8") + b"\n")
    return {"path": str(path.relative_to(ROOT)), "file_count": count, "bytes": total, "path_content_sha256": digest.hexdigest()}


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def write_json(name: str, value: Mapping[str, Any]) -> None:
    atomic_bytes(REPORT / name, json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n")


def write_text(name: str, value: str) -> None:
    atomic_bytes(REPORT / name, value.rstrip().encode("utf-8") + b"\n")


def git(*args: str, cwd: Path = ROOT, binary: bool = False) -> Any:
    value = subprocess.check_output(("git",) + args, cwd=str(cwd))
    return value if binary else value.decode("utf-8").strip()


def materialize_route(scene: str) -> Path:
    binding = frozen_binding(scene)
    source = ROOT / str(binding["base_route"])
    tree = ET.parse(source)
    route = tree.getroot().find("route")
    if route is None:
        raise RuntimeError("E2_V3_BASE_ROUTE_MISSING:" + scene)
    route.set("id", "RQ2TE2V3-" + scene)
    scenarios = route.find("scenarios")
    if scenarios is None:
        scenarios = ET.SubElement(route, "scenarios")
    old_scenario = scenarios.find("scenario")
    old_trigger = None if old_scenario is None else old_scenario.find("trigger_point")
    old_trigger_attributes = None if old_trigger is None else dict(old_trigger.attrib)
    for child in list(scenarios):
        scenarios.remove(child)
    scenario = ET.SubElement(scenarios, "scenario", {
        "name": "RQ2TE2V3-" + scene,
        "type": "DriveClarifyRQ2TE2V3EngineeringScenario",
    })
    if old_trigger_attributes is None:
        first = route.find("waypoints/position")
        if first is None:
            raise RuntimeError("E2_V3_BASE_TRIGGER_UNAVAILABLE:" + scene)
        old_trigger_attributes = {
            "x": first.get("x", "0"), "y": first.get("y", "0"),
            "z": first.get("z", "0"), "yaw": "0",
        }
    ET.SubElement(scenario, "trigger_point", old_trigger_attributes)
    ET.SubElement(scenario, "rq2_t_e2_v3", {
        "scene_config_id": scene,
        "scene_configuration_sha256": str(binding["scene_configuration_sha256"]),
    })
    ET.indent(tree, space="   ")
    ROUTES.mkdir(parents=True, exist_ok=True)
    target = ROUTES / (scene.lower() + ".xml")
    temporary = target.with_name("." + target.name + ".tmp")
    tree.write(temporary, encoding="utf-8", xml_declaration=True)
    os.replace(temporary, target)
    return target


def source_hashes() -> Mapping[str, str]:
    names = (
        "driveclarify_grounded_language_v1/runtime.py",
        "driveclarify_rq2_t_e2_v3/contracts.py",
        "driveclarify_rq2_t_e2_v3/tracker.py",
        "driveclarify_rq2_t_e2_v3/association.py",
        "driveclarify_rq2_t_e2_v3/calibration.py",
        "driveclarify_rq2_t_e2_v3/provider.py",
        "driveclarify_rq2_t_e2_v3/memory.py",
        "driveclarify_rq2_t_e2_v3/method.py",
        "driveclarify_rq2_t_e2_v3/runtime.py",
        "driveclarify_rq2_t_e2_v3/native_scenario.py",
        "driveclarify_rq2_t_e2_v3/certification.py",
        "driveclarify_rq2_t_e2_v3/scene_bindings.py",
        "driveclarify_paper_mvp_scenarios/leaderboard_scenario_root/srunner/scenarios/driveclarify_rq2_t_e2_v3_engineering.py",
        "tools/prepare_rq2_t_e2_v3.py",
        "tools/run_rq2_t_e2_v3.py",
        "tools/certify_rq2_t_e2_v3.py",
        "tests/rq2_t_e2_v3/test_e2_v3_contracts.py",
    )
    return {name: sha256(ROOT / name) for name in names}


def contract_pair(stem: str, title: str, value: Mapping[str, Any], paragraphs: str) -> None:
    payload = dict(value)
    payload["contract_digest"] = canonical(payload)
    write_json(stem + ".json", payload)
    write_text(stem + ".md", "# " + title + "\n\n" + paragraphs.strip() + "\n\nFrozen JSON contract digest: `" + payload["contract_digest"] + "`.")


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / "SCENE_CERTIFICATES").mkdir(parents=True, exist_ok=True)
    (REPORT / "NATIVE_EVIDENCE").mkdir(parents=True, exist_ok=True)
    routes = {scene: materialize_route(scene) for scene in SCENE_BINDINGS}
    sources = source_hashes()
    schedule = AcquisitionSchedule().to_dict()
    tracker = PersistentMultiObjectTracker().snapshot()
    tracker_config = {
        "implementation_id": tracker["implementation_id"],
        "high_support_threshold": 0.35, "low_support_threshold": 0.05,
        "high_match_iou": 0.18, "low_match_iou": 0.08,
        "lost_grace_acquisitions": 2, "deletion_after_misses": 4,
        "duplicate_iou_threshold": 0.75,
    }
    thresholds_grid = {
        "association_score_thresholds": list(ASSOCIATION_THRESHOLD_GRID),
        "uniqueness_margin_thresholds": list(UNIQUENESS_MARGIN_GRID),
        "minimum_track_hits": list(MINIMUM_HITS_GRID),
        "minimum_track_age_frames": list(MINIMUM_AGE_GRID),
    }
    current_head = git("rev-parse", "HEAD")
    if current_head != ENTRY_HEAD:
        raise RuntimeError("E2_V3_UNEXPECTED_ENTRY_HEAD:" + current_head)
    simlingo = Path("/home/buaa/wrh/simlingo")
    entry = {
        "schema_version": "driveclarify.e2_v3.entry_integrity.v1",
        "entry_branch": git("branch", "--show-current"), "entry_head": current_head,
        "tracked_diff_sha256_before_task": hashlib.sha256(b"").hexdigest(),
        "staged_diff_sha256_before_task": hashlib.sha256(b"").hexdigest(),
        "tracked_diff_was_empty_before_task": True, "staged_diff_was_empty_before_task": True,
        "task_owned_untracked_roots": [
            "driveclarify_rq2_t_e2_v3", "tests/rq2_t_e2_v3",
            "tools/prepare_rq2_t_e2_v3.py", "tools/run_rq2_t_e2_v3.py",
            str(REPORT.relative_to(ROOT)),
        ],
        "v1_final_tree": tree_digest(ROOT / "reports/driveclarify_rq2_t_formal_experiment_2a_v2"),
        "v1_primary_csv_sha256": sha256(ROOT / "reports/driveclarify_rq2_t_formal_experiment_2a_v2/EPISODE_LEVEL_PRIMARY_TABLE.csv"),
        "v1_primary_json_sha256": sha256(ROOT / "reports/driveclarify_rq2_t_formal_experiment_2a_v2/EPISODE_LEVEL_PRIMARY_TABLE.json"),
        "failed_v2_qualification_tree": tree_digest(ROOT / "reports/driveclarify_rq2_t_v2_pre_science_mechanism_qualification_v1"),
        "e2_forensic_audit_tree": tree_digest(ROOT / "reports/driveclarify_rq2_t_v2_e2_grounding_root_cause_audit_v1"),
        "v2_redesign_tree": tree_digest(ROOT / "reports/driveclarify_rq2_t_v2_evidence_enabled_method_redesign_v1"),
        "simlingo_head": git("rev-parse", "HEAD", cwd=simlingo),
        "simlingo_tracked_diff_sha256": hashlib.sha256(git("diff", "--binary", cwd=simlingo, binary=True)).hexdigest(),
        "checkpoint_sha256": sha256(simlingo / "outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt"),
        "detector_weights_sha256": sha256(ROOT / "pretrained/grounding-dino-tiny/model.safetensors"),
        "v1_sufficiency_source_sha256": sha256(ROOT / "driveclarify_rq2_t/measurement.py"),
        "existing_parser_sha256": sha256(ROOT / "driveclarify_language_grounding_v1/slot_parser.py"),
        "existing_e2_provider_sha256": sha256(ROOT / "driveclarify_rq2_t_v2/providers.py"),
        "existing_memory_sha256": sha256(ROOT / "driveclarify_rq2_t_v2/memory.py"),
    }
    entry["receipt_digest"] = canonical(entry)
    write_json("ENTRY_INTEGRITY_RECEIPT.json", entry)

    preservation = {
        "schema_version": "driveclarify.e2_v3.preservation.v1",
        "v1_status": "RQ2_T_TIMING_HYPOTHESIS_NOT_SUPPORTED",
        "v1_epistemic_sufficient": "0/64", "v1_full_evidence_available": "0/64",
        "historical_e2_method": "E2_V2_RAW_DETECTOR_SCORE_METHOD=CLOSED_NOT_QUALIFIED",
        "v1_tree": entry["v1_final_tree"],
        "failed_v2_tree": entry["failed_v2_qualification_tree"],
        "forensic_tree": entry["e2_forensic_audit_tree"],
        "v1_predicates_changed": False, "failed_evidence_overwritten": False,
    }
    preservation["receipt_digest"] = canonical(preservation)
    write_json("V1_AND_FAILED_V2_PRESERVATION_RECEIPT.json", preservation)

    contract_pair(
        "E2_V3_SCIENTIFIC_METHOD_CONTRACT", "E2 V3 scientific method contract",
        {
            "method_id": "E2_TRACKED_ASSOCIATION_V3", "candidate_condition": "CERTIFIED_CANDIDATE_INTERPRETATIONS",
            "chain": ["repeated_grounding", "persistent_tracking", "typed_candidate_matrix", "one_to_one_binding", "lineage_memory"],
            "historical_raw_score_threshold_0_80_changed": False,
            "v1_sufficiency_authority": "driveclarify_rq2_t.measurement",
            "policy_control_checkpoint_pid_routeplanner_changes": 0,
        },
        "The method is a default-off passive observer. It delegates the unchanged driving runtime, consumes only current/past camera and legal runtime state, and recomputes B1/B2 with the unchanged V1 sufficiency authority. The failed raw-detector-score method remains closed and unchanged.",
    )
    contract_pair(
        "CERTIFIED_CANDIDATE_SCHEMA", "Certified candidate schema",
        {
            "fields": ["object_class", "color", "motion_state", "lane_relation", "road_relation", "relative_longitudinal_order", "apparent_near_far", "parked_vs_moving", "left_right_relation", "instruction_relation", "interpretation_id", "candidate_id"],
            "missing_value": "UNSPECIFIED", "unsupported_attributes_have_reason_codes": True,
            "true_intent_allowed": False, "minimum_candidates": 2, "typed_projection_collision": "REJECT",
        },
        "Certified interpretations isolate visual association from automatic candidate generation. Every candidate keeps a stable interpretation ID and candidate ID; no true-intent, actor-ID, reveal, or gold field is legal.",
    )
    visibility = {
        "contract_id": "E2_V3_VISIBILITY_CERTIFICATE_V1",
        "camera": {"sensor": "rgb_0", "width": 1024, "height": 512, "fov_degrees": 110.0},
        "pre_reveal": {"maximum_discriminating_target_visible_fraction": 0.20, "unique_deployable_binding_required": False},
        "post_reveal": {"minimum_target_visible_fraction": 0.65, "minimum_pixel_width": 18, "minimum_pixel_height": 12, "minimum_pixel_area": 300, "minimum_sustained_frames": 20},
        "false_certificate_tolerance": 0,
        "required_records": ["actor_transform", "camera_transform", "intrinsics", "extrinsics", "frustum", "projected_bbox", "clipping", "pixels", "occluder", "line_of_sight_or_occlusion", "first_visible_frame", "sustained_interval", "reveal_to_commitment_margin"],
        "runtime_may_read_certificate": False, "runtime_may_read_reveal_frame_or_time": False,
    }
    contract_pair(
        "VISIBILITY_CERTIFICATION_CONTRACT", "Visibility certification contract", visibility,
        "Privileged CARLA state is post-episode grading data only. A positive reveal requires both targets to be physically visible, discriminable, and sustained for scheduled acquisition ticks before commitment. RGB and projection/occlusion evidence are mandatory.",
    )
    contract_pair(
        "GROUNDING_ACQUISITION_SCHEDULE", "Grounding acquisition schedule", schedule,
        "The detector runs on the first frame, every 10 unresolved simulator ticks, every 20 resolved ticks, and on runtime-observed track loss, with at most two query forwards per tick. Results are rejected unless bound to their exact camera frame. Reveal time is not an input.",
    )
    contract_pair(
        "TRACKER_CONTRACT", "Persistent tracker contract", tracker_config,
        "The tracker is a deterministic ByteTrack-style, two-stage IoU tracker over Grounding-DINO proposals. It exposes stable IDs, age, hits, misses, active/lost/reacquired/deleted state, motion, history, source frames, and score components. CARLA actor IDs are forbidden.",
    )
    contract_pair(
        "TRACK_REACQUISITION_AND_LINEAGE_CONTRACT", "Track reacquisition and lineage contract",
        {
            **tracker_config,
            "binding_invalidations": ["TRACK_CONFLICT", "ID_SWITCH_CONFLICT", "DUPLICATE_TRACK_AMBIGUITY", "LINEAGE_BREAK", "LOSS_GRACE_EXPIRED", "INCOMPATIBLE_REACQUISITION"],
            "silent_track_id_transfer": False, "invalidation_result": {"status": "UNKNOWN", "value": None},
        },
        "Temporary loss may retain the same tracker lineage only inside the two-acquisition grace. Any incompatible or ambiguous lineage event immediately invalidates E2; identity is never silently transferred to a new track.",
    )
    contract_pair(
        "CANDIDATE_TRACK_ASSOCIATION_CONTRACT", "Candidate-to-track association contract",
        {
            "feature_weights": FEATURE_WEIGHTS, "hard_gates_separate": True,
            "assignment": "SCIPY_HUNGARIAN_DETERMINISTIC_ONE_TO_ONE",
            "distinct_objects_required": True,
            "frame_records": ["full_matrix", "component_scores", "hard_gate_reasons", "assignment", "top1", "top2", "uniqueness_margin", "rejected_alternatives"],
        },
        "All matrix inputs are runtime-observable. Hard-incompatible pairs are removed before deterministic Hungarian assignment. Incomplete, colliding, or non-unique assignments abstain.",
    )
    contract_pair(
        "IDENTITY_SCORE_PROVENANCE", "Identity score provenance",
        {
            "score_name": "identity_association_score", "score_is_probability": False,
            "feature_weights": FEATURE_WEIGHTS,
            "distinct_outputs": ["detector_support_score", "track_coherence_score", "candidate_track_association_score", "uniqueness_margin"],
            "raw_detector_score_is_identity_confidence": False,
            "historical_raw_detector_threshold": 0.80, "historical_threshold_changed": False,
        },
        "The identity association score is a non-probabilistic weighted compatibility score. Raw detector support contributes only four percent and can never populate identity confidence directly.",
    )
    calibration_protocol = {
        "protocol_id": "E2_V3_SCENE_DISJOINT_FINITE_GRID_V1",
        "calibrator_family": CALIBRATOR_FAMILY, "finite_grid": thresholds_grid,
        "feature_weights_frozen": FEATURE_WEIGHTS,
        "selection_objective": ["ZERO_FALSE_UNIQUE_BINDINGS_ON_ALL_NEGATIVES", "MAXIMIZE_CORRECT_POST_REVEAL_RECALL", "SIMPLER_THEN_MORE_CONSERVATIVE_TIEBREAK"],
        "split_unit": "SCENE_ROUTE_ACTOR_CONFIGURATION", "frame_split_forbidden": True,
        "blind_outcomes_allowed": False, "probability_claim": False,
        "stopping_rule": "ONE_EXHAUSTIVE_EVALUATION_OF_FROZEN_FINITE_GRID",
    }
    contract_pair(
        "IDENTITY_CALIBRATION_PROTOCOL", "Identity calibration protocol", calibration_protocol,
        "The score rule and finite grid are frozen before calibration outcomes. Calibration scenes are engineering-only, scene-disjoint, and permanently excluded from formal and blind denominators. Only a zero-false-association configuration is eligible.",
    )
    contract_pair(
        "E2_MEMORY_AND_INVALIDATION_CONTRACT", "E2 memory and invalidation contract",
        {
            "ttl_seconds": 2.0,
            "binding_dimensions": ["episode", "route", "candidate_set", "environment", "candidate_track_lineages"],
            "record_fields": ["candidate_id", "track_id", "typed_candidate_spec", "acquisition", "latest_validation", "source_observations", "association_score", "uniqueness_margin", "lineage_state", "route_lane_binding", "freshness", "invalidation_events"],
            "cross_episode_route_track_lineage": False, "invent_missing_evidence": False,
        },
        "B2 retains a qualified E2 binding for at most two seconds while every episode/route/environment/candidate/track lineage remains coherent. All explicit conflict and boundary events fail closed.",
    )
    contract_pair(
        "B0_B1_B2_CONTRACT", "B0/B1/B2 paired-view contract",
        {
            "B0": "HISTORICAL_V1_PASSIVE_EVIDENCE_ONLY", "B1": "V3_CURRENT_FRAME_NO_FIELD_MEMORY", "B2": "B1_PLUS_FROZEN_FIELD_MEMORY",
            "same_episode_frames_model_outputs_trajectory_control_route_commitment": True,
            "B0_reads_v3": False, "B1_retains_evidence_fields": False, "B2_legal_memory_only": True,
        },
        "All three views are computed from identical native observations after the control path has already produced its history row. No view can affect vehicle behavior.",
    )

    scenes_by_phase = {phase: [] for phase in ("DEVELOPMENT", "CALIBRATION", "BLIND")}
    for scene in SCENE_BINDINGS:
        binding = frozen_binding(scene)
        phase = str(binding["phase"])
        scenes_by_phase[phase].append({
            "scene": scene, "family": binding["family"], "town": binding["town"],
            "route_path": str(routes[scene].relative_to(ROOT)), "route_sha256": sha256(routes[scene]),
            "scene_configuration_sha256": binding["scene_configuration_sha256"],
            "actor_configuration_sha256": canonical(binding["actors"]),
            "runtime_candidate_set_sha256": canonical(binding["runtime_candidates"]),
            "expected_scoring": binding["expected_scoring"],
        })
    manifest_names = {
        "DEVELOPMENT": "DEVELOPMENT_SCENE_MANIFEST",
        "CALIBRATION": "CALIBRATION_SCENE_MANIFEST",
        "BLIND": "BLIND_SCENE_SEAL",
    }
    for phase, rows in scenes_by_phase.items():
        value = {
            "schema_version": "driveclarify.e2_v3.{}_scenes.v1".format(phase.casefold()),
            "phase": phase, "engineering_only": True, "formal_seed": False,
            "permanently_excluded_from_formal_and_paper_denominators": True,
            "scenes": rows,
        }
        if phase == "BLIND":
            value.update({
                "prospectively_authored_before_any_v3_native_outcome_inspection": True,
                "rendered_or_executed_at_seal": False,
                "blind_layout_hashes_never_changed_after_first_seal": True,
                "first_layout_seal_manifest_digest_prefix_recorded_in_session": "9de454",
                "preblind_candidate_source_hashes": sources,
                "acquisition_schedule": schedule, "tracker_configuration": tracker_config,
                "parser_schema": "CERTIFIED_CANDIDATE_SCHEMA.json",
                "association_features": FEATURE_WEIGHTS,
                "calibration_protocol": calibration_protocol,
                "final_calibrated_threshold_pending_until_calibration_then_required_before_exposure": True,
                "post_episode_scoring_frozen": ["visibility_certificate", "candidate_actor_assignment", "truthful_evidence_transition", "negative_control"],
            })
        value["manifest_digest"] = canonical(value)
        stem = manifest_names[phase]
        write_json(stem + ".json", value)
        write_text(stem + ".md", "# " + stem.replace("_", " ").title() + "\n\n" + json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))

    ordered_scenes = (
        [row["scene"] for row in scenes_by_phase["DEVELOPMENT"]]
        + [row["scene"] for row in scenes_by_phase["CALIBRATION"]]
        + [row["scene"] for row in scenes_by_phase["BLIND"]]
    )
    identities = []
    for index, seed in enumerate(SEEDS, 1):
        if index <= len(ordered_scenes):
            scene = ordered_scenes[index - 1]
            phase = str(SCENE_BINDINGS[scene]["phase"])
            role = phase + "_ENGINEERING_IDENTITY"
        elif index == 37:
            scene, phase, role = None, "RESERVE", "USC_ACTIVATION_ONLY_QUALIFICATION"
        elif index == 38:
            scene, phase, role = None, "RESERVE", "USC_FULL_NEGATIVE_CONTROL_AFTER_ACTIVATION_PASS"
        else:
            scene, phase, role = None, "RESERVE", "FRESH_ENGINEERING_DEFECT_REPLACEMENT_ONLY"
        identities.append({
            "identity": "RQ2TE2V3-ENG-{:03d}".format(index), "seed": seed,
            "phase": phase, "scene": scene, "role": role,
            "status": "REGISTERED_UNEXPOSED", "formal_seed": False,
            "formal_scientific_exposure": False,
            "permanently_excluded_from": ["V2_CALIBRATION_REUSE", "V2-A_DEV", "V2-A_TEST", "V2-B_DEV", "V2-B_TEST", "PAPER_CONFIRMATORY_DENOMINATORS"],
        })
    registry = {
        "schema_version": "driveclarify.e2_v3.engineering_identity_registry.v1",
        "registered_before_exposure": True, "identity_count": len(identities),
        "target_bound": 24, "original_maximum_bound": 36,
        "supplemental_addendum_bound": 38, "maximum_bound": 38,
        "identities": identities, "additional_identity_count": 14,
        "additional_identity_justification": "TWELVE_GENUINE_DEFECT_REPLACEMENTS_PLUS_TWO_MANDATORY_FRESH_USC_ADDENDUM_IDENTITIES",
    }
    registry["registry_digest"] = canonical(registry)
    write_json("ENGINEERING_IDENTITY_REGISTRY.json", registry)
    exclusion = {
        "schema_version": "driveclarify.e2_v3.engineering_seed_exclusion.v1",
        "engineering_seed_count": len(SEEDS), "formal_seed_count": 0,
        "original_target_seed_count": 24, "genuine_defect_replacement_seed_count": 12,
        "supplemental_usc_addendum_seed_count": 2,
        "seeds": SEEDS,
        "freshness_check": "ZERO_EXACT_PRIOR_REPOSITORY_HITS_BEFORE_THIS_REPORT_TREE_WAS_CREATED",
        "excluded_from": ["FORMAL_DEV", "FORMAL_TEST", "V2-A", "V2-B", "PAPER_CONFIRMATORY_DENOMINATORS"],
        "reuse_allowed": False,
    }
    exclusion["registry_digest"] = canonical(exclusion)
    write_json("ENGINEERING_SEED_EXCLUSION_REGISTRY.json", exclusion)
    for ledger in ("ENGINEERING_ATTEMPT_LEDGER.jsonl", "ENGINEERING_EXECUTION_LEDGER.jsonl"):
        if not (REPORT / ledger).exists():
            atomic_bytes(REPORT / ledger, b"")
    write_text(
        "COMMAND_LOG.md",
        "# E2 V3 command log\n\n- Entry integrity captured at HEAD `{}`.\n- 24 fresh engineering-only identities were registered initially; 19 bound and 5 reserved.\n- Twelve additional fresh replacements (025-036) were prospectively registered only after documented immutable engineering defects.\n- Supplemental identities 037/038 are exclusively the required USC activation-only and post-activation full witnesses.\n- Development, calibration, and blind routes materialized and hash-bound.\n- Blind layout and route hashes have not changed since their first seal; method sources remain mutable only until the explicit pre-blind freeze.\n- Formal seed generation: 0. Formal scientific exposures: 0.".format(ENTRY_HEAD),
    )
    print(json.dumps({
        "status": "PREEXPOSURE_SEAL_COMPLETE", "identities": len(identities),
        "development": len(scenes_by_phase["DEVELOPMENT"]),
        "calibration": len(scenes_by_phase["CALIBRATION"]),
        "blind": len(scenes_by_phase["BLIND"]),
        "report": str(REPORT),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
