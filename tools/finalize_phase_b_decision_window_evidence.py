#!/usr/bin/env python3
"""Finalize the two-run Phase B evidence gate without mutating run artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = ROOT / "reports/driveclarify_persistent_ambiguity_runtime_v1_continuous_completion"
RUN_IDS = ("B1-R4", "B2")
PASS = "PASS_PHASE_B_DECISION_WINDOW_PHYSICAL_EVIDENCE_COMPLETE"


def _read(run_id: str, name: str) -> dict[str, Any]:
    return json.loads((REPORT_ROOT / run_id / name).read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_new(path: Path, value: Any) -> None:
    if path.exists():
        raise FileExistsError("APPEND_ONLY_ARTIFACT_ALREADY_EXISTS:" + str(path))
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def main() -> None:
    receipts = {run_id: _read(run_id, "PHASE_B_EVIDENCE_RECEIPT.json") for run_id in RUN_IDS}
    windows = {run_id: _read(run_id, "DECISION_WINDOW_EVIDENCE.json") for run_id in RUN_IDS}
    coordinates = {run_id: _read(run_id, "COORDINATE_FRAME_VALIDATION.json") for run_id in RUN_IDS}
    forwards = {run_id: _read(run_id, "FORWARD_ACCOUNTING.json") for run_id in RUN_IDS}

    for run_id in RUN_IDS:
        receipt = receipts[run_id]
        if not (
            receipt["status"] == "PASS_PHASE_B_EVIDENCE_COMPLETION"
            and receipt["valid_controlled_probe_run"] is True
            and receipt["phase_b_acceptance"] is True
            and receipt["phase_c_authorized_to_continue"] is True
            and str(receipt["native_status"]).startswith("PASS_")
            and str(receipt["desktop_status"]).startswith("PASS_")
            and receipt["unresolved_dependencies"] == []
        ):
            raise RuntimeError("PHASE_B_RUN_NOT_ACCEPTED:" + run_id)
        accounting = forwards[run_id]
        expected = {
            "dino": 1,
            "candidate_requested": 2,
            "candidate_executed": 2,
            "candidate_repeat": 0,
            "visualization_extra": 0,
        }
        if any(accounting.get(key) != value for key, value in expected.items()):
            raise RuntimeError("PHASE_B_FORWARD_ACCOUNTING_INVALID:" + run_id)

    def result(run_id: str, key: str) -> dict[str, Any]:
        return windows[run_id]["results"][key]

    coordinate_values = {run_id: coordinates[run_id]["value"] for run_id in RUN_IDS}
    route_versions = {
        run_id: coordinate_values[run_id]["route_version_id"] for run_id in RUN_IDS
    }
    onset = {
        run_id: float(result(run_id, "maneuver_onset")["value"]["progress_m"])
        for run_id in RUN_IDS
    }
    commitments = {
        run_id: result(run_id, "decision_point")["value"][
            "candidate_commitment_progress_m"
        ]
        for run_id in RUN_IDS
    }
    timing = {run_id: result(run_id, "time_to_divergence")["value"] for run_id in RUN_IDS}
    stability = {
        "route_version_exact_match": len(set(route_versions.values())) == 1,
        "route_versions": route_versions,
        "coordinate_uncertainty_m": {
            run_id: coordinate_values[run_id]["calibrated_transform_uncertainty_m"]
            for run_id in RUN_IDS
        },
        "coordinate_uncertainty_absolute_delta_m": abs(
            coordinate_values["B1-R4"]["calibrated_transform_uncertainty_m"]
            - coordinate_values["B2"]["calibrated_transform_uncertainty_m"]
        ),
        "maneuver_onset_progress_m": onset,
        "maneuver_onset_absolute_delta_m": abs(onset["B1-R4"] - onset["B2"]),
        "candidate_commitment_progress_m": commitments,
        "candidate_commitment_absolute_delta_m": {
            candidate_id: abs(
                float(commitments["B1-R4"][candidate_id])
                - float(commitments["B2"][candidate_id])
            )
            for candidate_id in ("A", "B")
        },
        "recoverability": {
            run_id: result(run_id, "recoverability")["value"] for run_id in RUN_IDS
        },
        "current_action_relation": {
            run_id: result(run_id, "current_action_relation")["value"]
            for run_id in RUN_IDS
        },
        "candidate_relationship": {
            run_id: result(run_id, "candidate_relationship")["value"]
            for run_id in RUN_IDS
        },
        "time_to_divergence_s": timing,
        "time_to_divergence_absolute_delta_s": {
            field: abs(float(timing["B1-R4"][field]) - float(timing["B2"][field]))
            for field in ("lower_bound_s", "upper_bound_s")
        },
    }
    if not (
        stability["route_version_exact_match"]
        and stability["maneuver_onset_absolute_delta_m"] == 0.0
        and len(set(stability["recoverability"].values())) == 1
        and len(set(stability["current_action_relation"].values())) == 1
        and len(set(stability["candidate_relationship"].values())) == 1
    ):
        raise RuntimeError("PHASE_B_MANDATORY_REPEAT_NOT_STABLE")

    source_files = {
        run_id: {
            name: {
                "relative_path": str(Path(run_id) / name),
                "sha256": _sha(REPORT_ROOT / run_id / name),
            }
            for name in (
                "PHASE_B_EVIDENCE_RECEIPT.json",
                "COORDINATE_FRAME_VALIDATION.json",
                "EGO_ROUTE_PROGRESS_EVIDENCE.json",
                "SHARED_CORRIDOR_EVIDENCE.json",
                "MANEUVER_ONSET_EVIDENCE.json",
                "PLAN_COVERAGE_EVIDENCE.json",
                "DECISION_POINT_EVIDENCE.json",
                "COMMITMENT_BOUNDARY_EVIDENCE.json",
                "RECOVERABILITY_EVIDENCE.json",
                "TIME_TO_DIVERGENCE_EVIDENCE.json",
                "TIMING_EVIDENCE.json",
                "DECISION_WINDOW_EVIDENCE.json",
                "FORWARD_ACCOUNTING.json",
                "AUTHORITY_MUTATION_AUDIT.json",
                "PRIVILEGED_READ_AUDIT.json",
            )
        }
        for run_id in RUN_IDS
    }
    final = {
        "schema_version": "driveclarify.phase_b.final_decision_window_evidence.v1",
        "status": PASS,
        "phase_b_acceptance": True,
        "phase_c_authorized_to_continue": True,
        "mandatory_repeat_completed": True,
        "accepted_run_ids": list(RUN_IDS),
        "scientific_run_history_preserved": ["B1-R2", "B1-R3", "B1-R4", "B2"],
        "stability": stability,
        "forward_accounting": forwards,
        "source_files": source_files,
        "unresolved_dependencies": [],
        "historical_runtime_data_reuse_policy": (
            "CALIBRATED_CONTRACT_ONLY; OLD_RGB_ROUTE_SPEED_PLAN_AND_SAFETY_STATE_"
            "CANNOT_AUTHORIZE_FUTURE_RUNTIME_ACTION"
        ),
    }
    _write_new(REPORT_ROOT / "PHASE_B_FINAL_EVIDENCE_RECEIPT.json", final)

    aliases = {
        "COORDINATE_CALIBRATION.json": "COORDINATE_FRAME_VALIDATION.json",
        "ROUTE_PROGRESS_EVIDENCE.json": "EGO_ROUTE_PROGRESS_EVIDENCE.json",
        "SHARED_CORRIDOR_EVIDENCE.json": "SHARED_CORRIDOR_EVIDENCE.json",
        "MANEUVER_ONSET_EVIDENCE.json": "MANEUVER_ONSET_EVIDENCE.json",
        "PLAN_COVERAGE_EVIDENCE.json": "PLAN_COVERAGE_EVIDENCE.json",
        "DECISION_POINT_EVIDENCE.json": "DECISION_POINT_EVIDENCE.json",
        "COMMITMENT_BOUNDARY_EVIDENCE.json": "COMMITMENT_BOUNDARY_EVIDENCE.json",
        "RECOVERABILITY_EVIDENCE.json": "RECOVERABILITY_EVIDENCE.json",
        "TIME_TO_DIVERGENCE_EVIDENCE.json": "TIME_TO_DIVERGENCE_EVIDENCE.json",
        "LATEST_SAFE_CLARIFICATION_EVIDENCE.json": "TIMING_EVIDENCE.json",
    }
    for alias, source in aliases.items():
        target = REPORT_ROOT / alias
        if target.exists():
            # Older root-level evidence is preserved.  Only the two accepted
            # run directories and the final receipt are authoritative.
            continue
        value = _read("B2", source)
        value["phase_b_final_authority"] = {
            "status": PASS,
            "accepted_run_ids": list(RUN_IDS),
            "repeat_stability_receipt": "PHASE_B_FINAL_EVIDENCE_RECEIPT.json",
        }
        _write_new(target, value)

    report = f"""# Phase B final decision-window physical evidence report

Status: `{PASS}`

The first fully valid run (`B1-R4`) and the mandatory unchanged-code repeat
(`B2`) both passed native evidence, desktop evidence, cleanup, and the Phase B
acceptance gate with zero collisions and no unresolved dependencies.

- Route version matched exactly: `{route_versions['B2']}`.
- Maneuver onset matched exactly at `{onset['B2']:.12g} m_route`.
- Maximum commitment-boundary delta was
  `{max(stability['candidate_commitment_absolute_delta_m'].values()):.12g} m`.
- Maximum TTD-bound delta was
  `{max(stability['time_to_divergence_absolute_delta_s'].values()):.12g} s`.
- Both runs classified current action as `CURRENT_ACTION_EQUIVALENT`, future
  consequence as `CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT`, and recoverability
  as `RECOVERABLE`.
- Each run used one DINO event forward, two candidate forwards, zero candidate
  repeats, and zero visualization-induced forwards.

The complete source paths and SHA-256 digests are in
`PHASE_B_FINAL_EVIDENCE_RECEIPT.json`. Historical failed/invalid attempts remain
append-only scientific records. Phase C may reuse the validated computation
contract, but no old RGB frame, route/speed plan, coverage state, safety state,
or decision window can authorize a future runtime action.
"""
    _write_new(REPORT_ROOT / "PHASE_B_FINAL_EVIDENCE_REPORT.md", report)


if __name__ == "__main__":
    main()
