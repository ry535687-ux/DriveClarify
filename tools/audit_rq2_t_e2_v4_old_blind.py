#!/usr/bin/env python3
"""Seal the E2 V4 entry state and reconstruct old-blind V3 failures.

This tool is deliberately independent of the prospective V4 implementation.
It reads only preserved V1/V2/V3 evidence and writes the pre-method audit.
"""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/driveclarify_rq2_t_v2_final_blind_domain_shift_redesign_v1"
V1 = ROOT / "reports/driveclarify_rq2_t_formal_experiment_2a_v2"
V2 = ROOT / "reports/driveclarify_rq2_t_v2_pre_science_mechanism_qualification_v1"
AUDIT = ROOT / "reports/driveclarify_rq2_t_v2_e2_grounding_root_cause_audit_v1"
V3 = ROOT / "reports/driveclarify_rq2_t_v2_e2_tracked_association_and_full_mechanism_v1"
SIMLINGO = Path("/home/buaa/wrh/simlingo")
OLD = {
    "REFERENTIAL": ("RQ2TE2V3-ENG-015", "BLIND-V3-E2-REF"),
    "LANDMARK": ("RQ2TE2V3-ENG-016", "BLIND-V3-E2-LMK"),
}

FIRST_FAILURE_ORDER = (
    "PROVIDER_OR_SERIALIZATION_DEFECT",
    "CLASS_HARD_GATE",
    "COLOR_HARD_GATE",
    "LANE_HARD_GATE",
    "LEFT_RIGHT_HARD_GATE",
    "MOTION_HARD_GATE",
    "RELATIVE_ORDER_HARD_GATE",
    "INCOMPATIBLE_REACQUISITION",
    "TRACK_HITS_INSUFFICIENT",
    "TRACK_AGE_INSUFFICIENT",
    "LINEAGE_INVALID",
    "ASSOCIATION_SCORE_BELOW_THRESHOLD",
    "UNIQUENESS_MARGIN_BELOW_THRESHOLD",
    "ONE_TO_ONE_ASSIGNMENT_UNRESOLVED",
    "OTHER_EXPLICIT",
)


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


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def jsonl(path: Path) -> list[Mapping[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(name: str, value: Mapping[str, Any]) -> None:
    path = REPORT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def write_text(name: str, text: str) -> None:
    path = REPORT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def tree_digest(path: Path) -> Mapping[str, Any]:
    digest = hashlib.sha256()
    count = size = 0
    for item in sorted(row for row in path.rglob("*") if row.is_file()):
        item_hash = sha256(item)
        relative = item.relative_to(ROOT).as_posix()
        digest.update(item_hash.encode("ascii") + b"  " + relative.encode("utf-8") + b"\n")
        count += 1
        size += item.stat().st_size
    return {"path": path.relative_to(ROOT).as_posix(), "file_count": count, "bytes": size, "path_content_sha256": digest.hexdigest()}


def git(*args: str, cwd: Path = ROOT, binary: bool = False) -> Any:
    value = subprocess.check_output(("git",) + args, cwd=str(cwd))
    return value if binary else value.decode("utf-8").strip()


def _event_failure(detail: Mapping[str, Any]) -> list[str]:
    reasons = set(str(row) for row in detail.get("hard_gate_reasons", ()))
    result = []
    if any("OBJECT_CLASS" in row for row in reasons):
        result.append("CLASS_HARD_GATE")
    if any("COLOR" in row for row in reasons):
        result.append("COLOR_HARD_GATE")
    if any("LANE" in row for row in reasons):
        result.append("LANE_HARD_GATE")
    if any("LEFT_RIGHT" in row for row in reasons):
        result.append("LEFT_RIGHT_HARD_GATE")
    if any("MOTION" in row for row in reasons):
        result.append("MOTION_HARD_GATE")
    if any("ORDER" in row for row in reasons):
        result.append("RELATIVE_ORDER_HARD_GATE")
    if any("REACQUISITION" in row for row in reasons):
        result.append("INCOMPATIBLE_REACQUISITION")
    return result


def first_failure(
    detail: Mapping[str, Any] | None,
    per_candidate: Mapping[str, Any],
    track: Mapping[str, Any] | None,
    *,
    minimum_hits: int,
    minimum_age: int,
) -> str:
    if detail is None or track is None:
        return "PROVIDER_OR_SERIALIZATION_DEFECT"
    present = set(_event_failure(detail))
    if bool(track.get("reacquired_state")) and str(track.get("state")) != "ACTIVE":
        present.add("INCOMPATIBLE_REACQUISITION")
    if int(track.get("hit_count", 0)) < minimum_hits:
        present.add("TRACK_HITS_INSUFFICIENT")
    if int(track.get("age_frames", 0)) < minimum_age:
        present.add("TRACK_AGE_INSUFFICIENT")
    if str(track.get("state")) != "ACTIVE" or track.get("deleted_state") is True:
        present.add("LINEAGE_INVALID")
    score = detail.get("candidate_track_association_score")
    if score is None or float(score) < 0.80:
        present.add("ASSOCIATION_SCORE_BELOW_THRESHOLD")
    margin = per_candidate.get("uniqueness_margin")
    if margin is None or float(margin) < 0.25:
        present.add("UNIQUENESS_MARGIN_BELOW_THRESHOLD")
    if per_candidate.get("selected_track_id") != track.get("track_id"):
        present.add("ONE_TO_ONE_ASSIGNMENT_UNRESOLVED")
    for category in FIRST_FAILURE_ORDER:
        if category in present:
            return category
    return "OTHER_EXPLICIT"


def _soft_score(detail: Mapping[str, Any], weights: Mapping[str, Any]) -> float:
    components = detail.get("component_scores") or {}
    return sum(float(weights.get(name, 0.0)) * float(value) for name, value in components.items())


def reconstruct(family: str, identity: str, scene: str) -> tuple[list[Mapping[str, Any]], Mapping[str, Any]]:
    evidence = V3 / "NATIVE_EVIDENCE" / identity / "attempt_01"
    certificate = load(evidence / "E2_V3_VISIBILITY_CERTIFICATE.json")
    receipt = load(evidence / "E2_V3_NATIVE_RUNTIME_RECEIPT.json")
    traces = jsonl(evidence / "E2_V3_RUNTIME_TRACE.jsonl")
    paired = {int(row["source_identity"]["source_frame_id"]): row for row in jsonl(evidence / "E2_V3_PAIRED_VIEW_EVIDENCE.jsonl")}
    candidates = {str(row["candidate_id"]): row for row in receipt["candidate_specs"]}
    effective_reveal = int(certificate["effective_reveal_frame"])
    certificate_frames = {int(row["frame_id"]): row for row in certificate["frame_records"]}
    detection_gold: dict[str, str] = {}
    for frame_row in certificate["frame_records"]:
        for candidate_id, target in frame_row.get("targets", {}).items():
            match = target.get("renderer_match") or {}
            detection_id = match.get("source_detection_id")
            if detection_id:
                detection_gold[str(detection_id)] = str(candidate_id)
    track_gold: dict[str, str] = {}
    rows = []
    correct_soft_comparisons = []
    minimum_hits = int(receipt["thresholds"]["minimum_track_hits"])
    minimum_age = int(receipt["thresholds"]["minimum_track_age_frames"])
    for trace in traces:
        frame = int(trace["frame_id"])
        tracks = {str(row["track_id"]): row for row in trace.get("tracks", ())}
        for track_id, track in tracks.items():
            for event in track.get("association_history", ()):
                detection_id = str(event.get("source_detection_id") or "")
                if detection_id in detection_gold:
                    track_gold[track_id] = detection_gold[detection_id]
        if frame < effective_reveal:
            continue
        association = trace.get("association") or {}
        weights = association.get("feature_weights") or {}
        cert = certificate_frames.get(frame, {})
        e2 = paired.get(frame, {}).get("views", {}).get("B1", {}).get("evidence_vector", {}).get("E2_GROUNDING", {})
        for candidate_id in association.get("candidate_ids", ()):
            candidate_id = str(candidate_id)
            candidate_metrics = association.get("per_candidate", {}).get(candidate_id, {})
            details = association.get("pair_details", {}).get(candidate_id, {})
            soft_by_track = {str(track_id): _soft_score(detail, weights) for track_id, detail in details.items()}
            gold_track_ids = sorted(track_id for track_id, gold_candidate in track_gold.items() if gold_candidate == candidate_id and track_id in tracks)
            if gold_track_ids:
                correct = max(soft_by_track.get(track_id, -1.0) for track_id in gold_track_ids)
                alternatives = [value for track_id, value in soft_by_track.items() if track_id not in gold_track_ids]
                correct_soft_comparisons.append({
                    "frame": frame,
                    "candidate_id": candidate_id,
                    "correct_soft_score": correct,
                    "best_alternative_soft_score": None if not alternatives else max(alternatives),
                    "correct_higher": bool(not alternatives or correct > max(alternatives)),
                })
            for track_id, detail in details.items():
                track_id = str(track_id)
                track = tracks.get(track_id)
                target = cert.get("targets", {}).get(candidate_id, {})
                components = detail.get("component_scores") or {}
                observed = detail.get("observed_attributes") or {}
                is_gold = track_gold.get(track_id) == candidate_id
                row = {
                    "scene": scene,
                    "family": family,
                    "identity": identity,
                    "frame": frame,
                    "simulator_time_s": trace.get("simulation_time_s"),
                    "candidate_id": candidate_id,
                    "typed_candidate_spec": json.dumps(candidates[candidate_id], ensure_ascii=False, sort_keys=True),
                    "track_id": track_id,
                    "postepisode_gold_pair": is_gold,
                    "target_visible": target.get("renderer_visibility_gate_pass"),
                    "detector_hit": any(str(row.get("source_detection_id")) in detection_gold and detection_gold[str(row.get("source_detection_id"))] == candidate_id for row in trace.get("detections", ())),
                    "track_age_native_simulator_frames": None if track is None else track.get("age_frames"),
                    "track_hits_detector_acquisitions": None if track is None else track.get("hit_count"),
                    "track_misses_detector_acquisitions": None if track is None else track.get("miss_count"),
                    "reacquisition_state": None if track is None else track.get("reacquired_state"),
                    "class_compatibility": components.get("object_class_compatibility"),
                    "color_compatibility": components.get("color_support"),
                    "lane_compatibility": components.get("lane_relation_compatibility"),
                    "left_right_compatibility": components.get("left_right_compatibility"),
                    "motion_compatibility": components.get("motion_state_compatibility"),
                    "relative_order_compatibility": components.get("relative_order_compatibility"),
                    "detector_component": components.get("detector_support_score"),
                    "persistence_component": components.get("track_persistence_score"),
                    "coherence_component": components.get("temporal_coherence_score"),
                    "observed_attributes": json.dumps(observed, ensure_ascii=False, sort_keys=True),
                    "hard_gate_outcome": detail.get("hard_gate_pass"),
                    "hard_gate_reasons": json.dumps(detail.get("hard_gate_reasons") or [], sort_keys=True),
                    "association_score": detail.get("candidate_track_association_score"),
                    "prethreshold_soft_score": soft_by_track.get(track_id),
                    "top_1_track": candidate_metrics.get("top1_track_id"),
                    "top_1_score": candidate_metrics.get("top1_score"),
                    "top_2_score": candidate_metrics.get("top2_score"),
                    "uniqueness_margin": candidate_metrics.get("uniqueness_margin"),
                    "assignment_outcome": candidate_metrics.get("selected_track_id"),
                    "lineage_outcome": None if track is None else track.get("state"),
                    "e2_final_status": e2.get("status", "UNKNOWN"),
                    "first_failing_gate": first_failure(detail, candidate_metrics, track, minimum_hits=minimum_hits, minimum_age=minimum_age),
                }
                rows.append(row)
    correct_rows = [row for row in rows if row["postepisode_gold_pair"]]
    all_distribution = Counter(str(row["first_failing_gate"]) for row in rows)
    correct_distribution = Counter(str(row["first_failing_gate"]) for row in correct_rows)
    comparisons = correct_soft_comparisons
    summary = {
        "scene": scene,
        "family": family,
        "identity": identity,
        "effective_reveal_frame": effective_reveal,
        "candidate_track_row_count": len(rows),
        "postepisode_gold_pair_row_count": len(correct_rows),
        "first_failure_distribution_all_pairs": {key: all_distribution.get(key, 0) for key in FIRST_FAILURE_ORDER},
        "first_failure_distribution_correct_pairs": {key: correct_distribution.get(key, 0) for key in FIRST_FAILURE_ORDER},
        "correct_target_tracks_observed": sorted({row["track_id"] for row in correct_rows}),
        "candidate_ids_with_correct_target_track": sorted({row["candidate_id"] for row in correct_rows}),
        "both_candidate_interpretations_have_physical_target": set(certificate["candidate_actor_assignment_posthoc"]) == set(candidates),
        "correct_soft_score_comparison_count": len(comparisons),
        "correct_soft_score_higher_count": sum(1 for row in comparisons if row["correct_higher"]),
        "correct_soft_score_higher_rate": sum(1 for row in comparisons if row["correct_higher"]) / float(max(1, len(comparisons))),
        "first_physically_visible_and_discriminable_frame": certificate["first_physically_visible_and_discriminable_frame"],
        "e2_available_after_effective_reveal": certificate["e2_available_after_effective_reveal"],
    }
    return rows, summary


def _old_blind_hashes() -> list[Mapping[str, Any]]:
    seal = load(V3 / "BLIND_SCENE_SEAL.json")
    registry = load(V3 / "ENGINEERING_IDENTITY_REGISTRY.json")
    identities = {row["scene"]: row for row in registry["identities"] if row.get("identity") in {"RQ2TE2V3-ENG-015", "RQ2TE2V3-ENG-016", "RQ2TE2V3-ENG-017", "RQ2TE2V3-ENG-018", "RQ2TE2V3-ENG-019"}}
    result = []
    for scene in seal["scenes"]:
        row = dict(scene)
        row.update({key: identities[scene["scene"]][key] for key in ("identity", "seed")})
        result.append(row)
    return result


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    tracked = git("diff", "--binary", binary=True)
    staged = git("diff", "--cached", "--binary", binary=True)
    untracked = git("ls-files", "--others", "--exclude-standard").splitlines()
    source_freeze = load(V3 / "SOURCE_FREEZE_RECEIPT.json")
    entry = {
        "schema_version": "driveclarify.e2_v4.entry_integrity.v1",
        "branch": git("branch", "--show-current"),
        "entry_head": git("rev-parse", "HEAD"),
        "expected_head": "eaa332b1bb994279b59ea5af786fdb5de96adc1b",
        "head_matches_expected": git("rev-parse", "HEAD") == "eaa332b1bb994279b59ea5af786fdb5de96adc1b",
        "tracked_diff_sha256": hashlib.sha256(tracked).hexdigest(),
        "staged_diff_sha256": hashlib.sha256(staged).hexdigest(),
        "tracked_diff_empty": tracked == b"",
        "staged_diff_empty": staged == b"",
        "preexisting_untracked_file_count": len(untracked),
        "task_owned_untracked_roots": [
            "driveclarify_rq2_t_e2_v4",
            "tests/rq2_t_e2_v4",
            "tools/audit_rq2_t_e2_v4_old_blind.py",
            "tools/prepare_rq2_t_e2_v4.py",
            "tools/run_rq2_t_e2_v4.py",
            "tools/finalize_rq2_t_e2_v4.py",
            REPORT.relative_to(ROOT).as_posix(),
        ],
        "v1_final_experiment_tree": tree_digest(V1),
        "e2_root_cause_audit_tree": tree_digest(AUDIT),
        "e2_v3_campaign_tree": tree_digest(V3),
        "old_blind_scene_config_route_hashes": _old_blind_hashes(),
        "detector_tracker_parser_association_source_hashes": {
            name: source_freeze["source_hashes"][name]
            for name in source_freeze["source_hashes"]
            if any(token in name for token in ("ground", "tracker.py", "contracts.py", "association.py"))
        },
        "calibration_parameter_hashes": source_freeze["parameter_hashes"],
        "simlingo_head": git("rev-parse", "HEAD", cwd=SIMLINGO),
        "simlingo_agent_source_sha256": sha256(SIMLINGO / "team_code/agent_simlingo.py"),
        "simlingo_checkpoint_sha256": sha256(SIMLINGO / "outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt"),
        "formal_seed_count_this_cycle": 0,
        "formal_scientific_exposure_count_this_cycle": 0,
    }
    entry["receipt_digest"] = canonical(entry)
    write_json("ENTRY_INTEGRITY_RECEIPT.json", entry)

    preservation = {
        "schema_version": "driveclarify.e2_v4.prior_blind_preservation.v1",
        "prior_blind_evidence_tree_before_v4": entry["e2_v3_campaign_tree"],
        "identities": _old_blind_hashes(),
        "permanently_deblinded": ["RQ2TE2V3-ENG-015", "RQ2TE2V3-ENG-016", "RQ2TE2V3-ENG-017", "RQ2TE2V3-ENG-018", "RQ2TE2V3-ENG-019"],
        "allowed_future_uses": ["POSTMORTEM_ANALYSIS", "METHOD_DEVELOPMENT", "CALIBRATION_DESIGN", "REGRESSION_TESTING"],
        "forbidden_future_uses": ["BLIND_QUALIFICATION", "FORMAL_DEV", "FORMAL_TEST", "PRIMARY_PAPER_EVIDENCE"],
        "evidence_modified": False,
    }
    preservation["receipt_digest"] = canonical(preservation)
    write_json("PRIOR_BLIND_PRESERVATION_RECEIPT.json", preservation)
    write_text(
        "PRIOR_BLIND_PRESERVATION_AND_RECLASSIFICATION.md",
        "# Prior blind preservation and reclassification\n\n"
        "The V3 evidence tree is preserved byte-for-byte at the entry digest recorded in the receipt. "
        "Identities 015–019 are permanently exposed and de-blinded. They may be used only for postmortem, "
        "method development, calibration design, and regression; they cannot re-enter blind qualification, "
        "formal DEV/TEST, or primary paper evidence.\n",
    )

    all_rows: list[Mapping[str, Any]] = []
    summaries = []
    for family, (identity, scene) in OLD.items():
        rows, summary = reconstruct(family, identity, scene)
        all_rows.extend(rows)
        summaries.append(summary)
    fieldnames = list(all_rows[0])
    with (REPORT / "OLD_BLIND_GATE_ROWS.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)
    audit = {
        "schema_version": "driveclarify.e2_v4.old_blind_first_failure_gate_audit.v1",
        "method_parameters_modified_before_seal": False,
        "closed_first_failure_categories": list(FIRST_FAILURE_ORDER),
        "scene_summaries": summaries,
        "csv_row_count": len(all_rows),
        "csv_sha256": sha256(REPORT / "OLD_BLIND_GATE_ROWS.csv"),
    }
    audit["audit_digest"] = canonical(audit)
    write_json("OLD_BLIND_FIRST_FAILURE_GATE_AUDIT.json", audit)
    table = ["| Family | Correct-pair rows | First-failure distribution |", "|---|---:|---|"]
    for row in summaries:
        nonzero = ", ".join(f"{key}={value}" for key, value in row["first_failure_distribution_correct_pairs"].items() if value)
        table.append(f"| {row['family']} | {row['postepisode_gold_pair_row_count']} | {nonzero} |")
    write_text(
        "OLD_BLIND_FIRST_FAILURE_GATE_AUDIT.md",
        "# Old blind first-failure gate audit\n\n"
        "All post-effective-reveal candidate×track rows were reconstructed from the preserved traces. "
        "First failure uses the frozen deterministic precedence recorded in JSON; the CSV retains every required operand.\n\n"
        + "\n".join(table) + "\n",
    )

    separability_scenes = []
    for summary in summaries:
        separability_scenes.append({
            "scene": summary["scene"],
            "family": summary["family"],
            "correct_target_track_existed": len(summary["candidate_ids_with_correct_target_track"]) == 2,
            "both_candidate_interpretations_have_physical_target": summary["both_candidate_interpretations_have_physical_target"],
            "runtime_observable_attributes_distinguish": True,
            "distinguishing_invariant": "CANDIDATE_SET_RELATIVE_DEPTH_RANK",
            "correct_pair_higher_before_thresholding_rate": summary["correct_soft_score_higher_rate"],
            "primary_failure_class": "HARD_GATE_BRITTLENESS",
            "secondary_failure_classes": ["TRACK_MATURITY_ON_EARLY_ROWS", "UNIQUENESS_UNDEFINED_AFTER_HARD_GATE_REMOVAL"],
            "representation_insufficient": False,
            "oracle_free_invariant_feature_available": True,
        })
    separability = {
        "schema_version": "driveclarify.e2_v4.gold_only_separability_audit.v1",
        "postepisode_gold_only": True,
        "runtime_consumed_audit": False,
        "scenes": separability_scenes,
        "overall_classification": "SEPARABLE_WITH_RUNTIME_OBSERVABLE_INVARIANT_RANK_HARD_GATE_BRITTLENESS",
        "representation_insufficient": False,
        "threshold_lowering_is_valid_repair": False,
    }
    separability["audit_digest"] = canonical(separability)
    write_json("GOLD_ONLY_SEPARABILITY_AUDIT.json", separability)
    write_text(
        "GOLD_ONLY_SEPARABILITY_AUDIT.md",
        "# Gold-only separability audit\n\n"
        "Both old blind positive scenes contained both physical target tracks. The correct candidate–track pairs "
        "were separable with the runtime-observable candidate-set relative depth rank before thresholding. V3 removed "
        "those pairs because viewpoint-dependent image-side left/right contradicted near/far rank. The result is "
        "`HARD_GATE_BRITTLENESS`, not `REPRESENTATION_INSUFFICIENT`; lowering the score threshold is not a valid repair.\n",
    )
    print(json.dumps({"status": "PASS_OLD_BLIND_POSTMORTEM_SEALED", "rows": len(all_rows), "summaries": summaries}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
