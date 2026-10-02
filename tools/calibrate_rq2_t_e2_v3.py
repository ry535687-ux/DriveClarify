#!/usr/bin/env python3
"""Grade the sealed calibration split once and freeze E2 V3 thresholds."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_rq2_t_e2_v3.calibration import select_thresholds  # noqa: E402
from driveclarify_rq2_t_e2_v3.contracts import canonical_sha256  # noqa: E402


REPORT = ROOT / "reports/driveclarify_rq2_t_v2_e2_tracked_association_and_full_mechanism_v1"
EVIDENCE = REPORT / "NATIVE_EVIDENCE"
CALIBRATION_IDENTITIES = tuple("RQ2TE2V3-ENG-{:03d}".format(index) for index in range(9, 15))
INVALID_EXPECTED = {"RQ2TE2V3-ENG-009"}
NONREVEAL = {"RQ2TE2V3-ENG-013"}
WRONG_PAIR = {"RQ2TE2V3-ENG-014"}


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def jsonl(path: Path) -> list[Mapping[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    atomic_bytes(
        path,
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n",
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _gold_track_mappings(
    paired: list[Mapping[str, Any]], certificate: Mapping[str, Any]
) -> list[Mapping[str, str]]:
    """Join certified renderer detections to runtime tracks after execution."""

    paired_by_frame = {
        int(row["source_identity"]["source_frame_id"]): row for row in paired
    }
    mappings: list[Mapping[str, str]] = []
    for frame_record in certificate.get("frame_records", ()):
        if frame_record.get("all_targets_renderer_certified") is not True:
            continue
        paired_row = paired_by_frame.get(int(frame_record["frame_id"]))
        if paired_row is None:
            continue
        mapping: dict[str, str] = {}
        for candidate_id, target in frame_record.get("targets", {}).items():
            match = target.get("renderer_match") or {}
            detection_id = match.get("source_detection_id")
            hits = [
                str(track["track_id"])
                for track in paired_row["tracker_snapshot"].get("tracks", ())
                if any(
                    event.get("source_detection_id") == detection_id
                    for event in track.get("association_history", ())
                )
            ]
            if len(hits) == 1:
                mapping[str(candidate_id)] = hits[0]
        if len(mapping) == len(frame_record.get("targets", {})) and mapping not in mappings:
            mappings.append(mapping)
    if not mappings:
        raise RuntimeError("E2_V3_CALIBRATION_GOLD_TRACK_JOIN_EMPTY")
    return mappings


def _calibration_metrics(
    row: Mapping[str, Any], assignment: Mapping[str, str]
) -> Mapping[str, Any]:
    association = row["association"]
    tracks = {
        str(track["track_id"]): track for track in row["tracker_snapshot"].get("tracks", ())
    }
    candidate_ids = [str(value) for value in association.get("candidate_ids", ())]
    details = []
    selected_tracks = []
    for candidate_id in candidate_ids:
        track_id = assignment.get(candidate_id)
        detail = (association.get("pair_details", {}).get(candidate_id, {}) or {}).get(track_id)
        track = tracks.get(str(track_id))
        if detail is None or track is None:
            continue
        details.append(detail)
        selected_tracks.append(track)
    complete = bool(
        len(details) == len(candidate_ids)
        and len(set(assignment.values())) == len(candidate_ids)
        and all(detail.get("candidate_track_association_score") is not None for detail in details)
    )
    scores = [float(detail["candidate_track_association_score"]) for detail in details if detail.get("candidate_track_association_score") is not None]
    margins = []
    for candidate_id in candidate_ids:
        if assignment.get(candidate_id) == association.get("per_candidate", {}).get(candidate_id, {}).get("top1_track_id"):
            value = association["per_candidate"][candidate_id].get("uniqueness_margin")
            if value is not None:
                margins.append(float(value))
    return {
        "assignment_complete": complete,
        "minimum_association_score": min(scores) if complete and scores else -1.0,
        "minimum_uniqueness_margin": min(margins) if complete and len(margins) == len(candidate_ids) else -1.0,
        "minimum_track_hits": min((int(track["hit_count"]) for track in selected_tracks), default=0),
        "minimum_track_age_frames": min((int(track["age_frames"]) for track in selected_tracks), default=0),
        "active_invalidation_events": list(row.get("invalidation_events") or ()),
    }


def _manifest_disjoint_proof() -> Mapping[str, Any]:
    manifests = {
        "DEVELOPMENT": load(REPORT / "DEVELOPMENT_SCENE_MANIFEST.json"),
        "CALIBRATION": load(REPORT / "CALIBRATION_SCENE_MANIFEST.json"),
        "BLIND": load(REPORT / "BLIND_SCENE_SEAL.json"),
    }
    fields = ("scene_configuration_sha256", "route_sha256")
    intersections: dict[str, Any] = {}
    for field in fields:
        sets = {
            split: {str(row[field]) for row in value.get("scenes", ())}
            for split, value in manifests.items()
        }
        intersections[field] = {
            "calibration_vs_development": sorted(sets["CALIBRATION"].intersection(sets["DEVELOPMENT"])),
            "calibration_vs_blind": sorted(sets["CALIBRATION"].intersection(sets["BLIND"])),
        }
    registry = load(REPORT / "ENGINEERING_IDENTITY_REGISTRY.json")
    seeds = {
        phase: {
            int(row["seed"]) for row in registry.get("identities", ()) if row.get("phase") == phase
        }
        for phase in ("DEVELOPMENT", "CALIBRATION", "BLIND")
    }
    seed_intersections = {
        "calibration_vs_development": sorted(seeds["CALIBRATION"].intersection(seeds["DEVELOPMENT"])),
        "calibration_vs_blind": sorted(seeds["CALIBRATION"].intersection(seeds["BLIND"])),
    }
    proof = {
        "split_unit": "SCENE_ROUTE_ACTOR_CONFIGURATION",
        "manifest_intersections": intersections,
        "seed_intersections": seed_intersections,
        "frame_level_random_split_used": False,
    }
    proof["scene_route_actor_seed_disjoint"] = bool(
        not any(intersections[field][key] for field in fields for key in intersections[field])
        and not any(seed_intersections.values())
    )
    return proof


def main() -> int:
    threshold_path = REPORT / "FROZEN_CALIBRATED_THRESHOLDS.json"
    if threshold_path.exists():
        raise RuntimeError("E2_V3_CALIBRATION_STOPPING_RULE_ALREADY_CONSUMED")
    attempts = jsonl(REPORT / "ENGINEERING_ATTEMPT_LEDGER.jsonl")
    blind_attempts = [row for row in attempts if row.get("phase") == "BLIND"]
    if blind_attempts:
        raise RuntimeError("E2_V3_CALIBRATION_AFTER_BLIND_EXPOSURE_FORBIDDEN")
    rows: list[Mapping[str, Any]] = []
    excluded = []
    scene_counts: dict[str, Any] = {}
    for identity in CALIBRATION_IDENTITIES:
        output = EVIDENCE / identity / "attempt_01"
        result = load(output / "ENGINEERING_EPISODE_RESULT.json")
        if result.get("status") != "VALID_ENGINEERING_EPISODE":
            excluded.append({
                "identity": identity,
                "status": result.get("status"),
                "reason": "NO_VALID_NATIVE_CALIBRATION_ROWS",
            })
            continue
        paired = jsonl(output / "E2_V3_PAIRED_VIEW_EVIDENCE.jsonl")
        certificate_path = REPORT / "SCENE_CERTIFICATES" / (identity + "_VISIBILITY_CERTIFICATE.json")
        certificate = load(certificate_path) if certificate_path.exists() else {}
        scene = str(result["scene"])
        reveal_frame = certificate.get("effective_reveal_frame")
        gold_mappings = [] if identity in NONREVEAL else _gold_track_mappings(paired, certificate)
        counts: dict[str, int] = {}
        for paired_row in paired:
            frame = int(paired_row["source_identity"]["source_frame_id"])
            selected = {
                str(key): str(value)
                for key, value in paired_row["association"].get("selected_assignment", {}).items()
            }
            if identity in NONREVEAL:
                label = "NEGATIVE_NONREVEAL"
            elif reveal_frame is not None and frame < int(reveal_frame):
                label = "NEGATIVE_PRE_REVEAL"
            elif selected in gold_mappings:
                label = "CORRECT_POST_REVEAL_UNIQUE"
            else:
                label = "NEGATIVE_AMBIGUOUS_OR_WRONG_POST_REVEAL"
            row = {
                "schema_version": "driveclarify.e2_v3.calibration_row.v1",
                "split": "CALIBRATION",
                "identity": identity,
                "scene_id": scene,
                "source_frame_id": frame,
                "simulation_time_s": float(paired_row["source_identity"]["simulation_time_s"]),
                "label": label,
                "label_provenance": "POST_EPISODE_ACTOR_PROJECTION_TO_DETECTION_TO_TRACK_LINEAGE_JOIN",
                "selected_assignment": selected,
                "privileged_label_runtime_read_count": 0,
                **_calibration_metrics(paired_row, selected),
            }
            rows.append(row)
            counts[label] = counts.get(label, 0) + 1
            if identity in WRONG_PAIR and label == "CORRECT_POST_REVEAL_UNIQUE" and len(selected) == 2:
                candidate_ids = sorted(selected)
                reversed_assignment = {
                    candidate_ids[0]: selected[candidate_ids[1]],
                    candidate_ids[1]: selected[candidate_ids[0]],
                }
                wrong = {
                    **row,
                    "label": "NEGATIVE_WRONG_CANDIDATE_TRACK_PAIR",
                    "label_provenance": "POST_EPISODE_DELIBERATE_CANDIDATE_TRACK_REVERSAL",
                    "selected_assignment": reversed_assignment,
                    "synthetic_from_source_frame": frame,
                    **_calibration_metrics(paired_row, reversed_assignment),
                }
                rows.append(wrong)
                counts[wrong["label"]] = counts.get(wrong["label"], 0) + 1
        scene_counts[scene] = counts
    proof = _manifest_disjoint_proof()
    if proof["scene_route_actor_seed_disjoint"] is not True:
        raise RuntimeError("E2_V3_CALIBRATION_SPLIT_NOT_DISJOINT")
    selection = dict(select_thresholds(rows))
    selection.update({
        "schema_version": "driveclarify.e2_v3.identity_calibration_results.v1",
        "calibration_identity_count_registered": len(CALIBRATION_IDENTITIES),
        "calibration_identity_count_valid": len(CALIBRATION_IDENTITIES) - len(excluded),
        "excluded_invalid_identities": excluded,
        "invalid_identity_set_matches_expected": {row["identity"] for row in excluded} == INVALID_EXPECTED,
        "calibration_row_count": len(rows),
        "label_counts": {
            label: sum(row["label"] == label for row in rows)
            for label in sorted({str(row["label"]) for row in rows})
        },
        "scene_label_counts": scene_counts,
        "split_disjoint_proof": proof,
        "blind_outcomes_read": False,
        "calibration_consumption_count": 1,
        "historical_failed_e2_raw_score_threshold": 0.80,
        "historical_threshold_reused_as_provenance": False,
    })
    selection["calibration_rows_digest"] = canonical_sha256(rows)
    selection["receipt_digest"] = canonical_sha256(selection)
    rows_payload = b"".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"
        for row in rows
    )
    atomic_bytes(REPORT / "E2_V3_CALIBRATION_ROWS.jsonl", rows_payload)
    for name in ("IDENTITY_CALIBRATION_RESULTS.json", "E2_CALIBRATION_RESULTS.json"):
        atomic_json(REPORT / name, selection)
    metrics = selection.get("selected_metrics") or {}
    markdown = "\n".join([
        "# E2 V3 identity calibration results",
        "",
        "Status: `{}`".format(selection["status"]),
        "",
        "- Valid calibration identities: {} / {} (009 was a pre-frame CARLA crash and was excluded).".format(
            selection["calibration_identity_count_valid"], selection["calibration_identity_count_registered"]
        ),
        "- Rows: {} (scene/route/actor/seed disjoint from development and blind).".format(len(rows)),
        "- Selected thresholds: `{}`".format(json.dumps(selection.get("selected_thresholds"), sort_keys=True)),
        "- Precision: {:.6f}; recall: {:.6f}; false-association rate: {:.6f}; abstention rate: {:.6f}.".format(
            float(metrics.get("precision", 0.0)), float(metrics.get("recall", 0.0)),
            float(metrics.get("false_association_rate", 0.0)), float(metrics.get("abstention_rate", 0.0)),
        ),
        "- Probability calibration claimed: no; the output is a non-probabilistic identity association score.",
        "- Blind outcomes read: no.",
    ]) + "\n"
    for name in ("IDENTITY_CALIBRATION_RESULTS.md", "E2_CALIBRATION_RESULTS.md"):
        atomic_bytes(REPORT / name, markdown.encode("utf-8"))
    if selection.get("status") == "PASS_E2_V3_CALIBRATION_DEFENSIBLE":
        frozen = {
            "schema_version": "driveclarify.e2_v3.frozen_calibrated_thresholds.v1",
            "status": "FROZEN_BEFORE_BLIND_EXECUTION",
            "selected_thresholds": selection["selected_thresholds"],
            "selected_metrics": selection["selected_metrics"],
            "calibrator_family": selection["calibrator_family"],
            "probability_calibration_claimed": False,
            "identity_score_name": "identity_association_score",
            "calibration_results_sha256": sha256(REPORT / "E2_CALIBRATION_RESULTS.json"),
            "calibration_rows_sha256": sha256(REPORT / "E2_V3_CALIBRATION_ROWS.jsonl"),
            "calibration_receipt_digest": selection["receipt_digest"],
            "historical_raw_score_threshold_0_80_unchanged": True,
            "historical_threshold_provenance_not_reused": True,
        }
        frozen["receipt_digest"] = canonical_sha256(frozen)
        atomic_json(threshold_path, frozen)
    print(json.dumps({
        "status": selection["status"],
        "selected_thresholds": selection.get("selected_thresholds"),
        "selected_metrics": selection.get("selected_metrics"),
        "valid_identities": selection["calibration_identity_count_valid"],
        "excluded": excluded,
        "row_count": len(rows),
        "receipt_digest": selection["receipt_digest"],
    }, indent=2, sort_keys=True))
    return 0 if selection.get("status") == "PASS_E2_V3_CALIBRATION_DEFENSIBLE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
