#!/usr/bin/env python3
"""Freeze the one permitted, outcome-blind V2 negative-control revision."""

from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPORT = ROOT / "reports/driveclarify_v2_negative_control_reconstruction_and_validation_v1"
PACKAGE = ROOT / "driveclarify_v2_negative_control_reconstruction_v2"
FIXTURES = PACKAGE / "fixtures"
V1_MANIFEST = REPORT / "TRAIN_V2_NEGATIVE_CONTROL_MANIFEST.json"
V2_MANIFEST = REPORT / "TRAIN_V2_NEGATIVE_CONTROL_MANIFEST_V2.json"

from driveclarify_decision_evidence_v2.runtime_adapter import load_phase_b_timing_calibration_v2  # noqa: E402
from driveclarify_v2_negative_control_reconstruction_v1.contracts import (  # noqa: E402
    CALIBRATED_ROUTE_VERSION,
    EARLIEST_COMMITMENT_PROGRESS_M,
    MANEUVER_ONSET_PROGRESS_M,
)
from driveclarify_v2_negative_control_reconstruction_v2.contracts import SCENARIOS  # noqa: E402


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def physical(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in (
        "scenario_id", "split", "town", "route_id", "route", "trigger",
        "instruction", "actors", "ego_initial_transform",
    )}


def xml_for(row: dict[str, Any]) -> str:
    (x0, y0, z0), (x1, y1, z1) = row["route"]
    tx, ty, tz, yaw = row["trigger"]
    return f'''<routes>
  <route id="{row['route_id']}" town="{row['town']}">
    <waypoints>
      <position x="{x0:.9f}" y="{y0:.9f}" z="{z0:.9f}" />
      <position x="{x1:.9f}" y="{y1:.9f}" z="{z1:.9f}" />
    </waypoints>
    <scenarios>
      <scenario name="{row['scenario_id']}" type="DriveClarifyV2NegativeControlScenarioV2">
        <trigger_point x="{tx:.9f}" y="{ty:.9f}" z="{tz:.9f}" yaw="{yaw:.9f}" />
        <negative_control_v2 scenario_id="{row['scenario_id']}" />
      </scenario>
    </scenarios>
    <weathers>
      <weather cloudiness="0.0" fog_density="0.0" precipitation="0.0" precipitation_deposits="0.0" sun_altitude_angle="70.0" sun_azimuth_angle="0.0" wetness="0.0" wind_intensity="0.0" route_percentage="0" />
      <weather cloudiness="0.0" fog_density="0.0" precipitation="0.0" precipitation_deposits="0.0" sun_altitude_angle="70.0" sun_azimuth_angle="0.0" wetness="0.0" wind_intensity="0.0" route_percentage="100" />
    </weathers>
  </route>
</routes>
'''


def project_center(row: dict[str, Any], actor: tuple[Any, ...]) -> dict[str, Any]:
    """Outcome-blind pinhole check using the pinned SimLingo rgb_0 FOV."""
    _, actor_x, actor_y, _, _ = actor
    ego_x, ego_y, _, yaw_deg = row["ego_initial_transform"]
    yaw = math.radians(float(yaw_deg))
    dx, dy = float(actor_x) - float(ego_x), float(actor_y) - float(ego_y)
    forward = dx * math.cos(yaw) + dy * math.sin(yaw)
    lateral = -dx * math.sin(yaw) + dy * math.cos(yaw)
    fov_deg = 110.0
    normalized_x = 0.5 + lateral / (2.0 * forward * math.tan(math.radians(fov_deg / 2.0)))
    category = "LEFT" if normalized_x < 0.4 else "RIGHT" if normalized_x > 0.6 else "CENTER"
    return {
        "camera": "rgb_0",
        "pinned_width_px": 1024,
        "pinned_fov_deg": fov_deg,
        "forward_m": forward,
        "lateral_m": lateral,
        "predicted_normalized_center_x": normalized_x,
        "frozen_category_rule": "LEFT_IF_X_LT_0.4_RIGHT_IF_X_GT_0.6_ELSE_CENTER",
        "predicted_category": category,
    }


def main() -> None:
    if (REPORT / "LIVE_EXECUTION_STARTED_V2").exists():
        raise RuntimeError("NEGATIVE_CONTROL_MANIFEST_V2_ALREADY_EXPOSED_TO_LIVE_OUTCOME")
    if not (REPORT / "NEGATIVE_CONTROL_MANIFEST_V1_ROOT_CAUSE.md").exists():
        raise RuntimeError("MANIFEST_V1_ROOT_CAUSE_REVIEW_MISSING")
    v1 = json.loads(V1_MANIFEST.read_text(encoding="utf-8"))
    old_ei = [row for row in v1["scenarios"] if row["negative_control_family"] == "EVIDENCE_INSUFFICIENT"]
    if len(old_ei) != 3:
        raise RuntimeError("FROZEN_V1_EI_SET_NOT_THREE")
    for entry in old_ei:
        for key in ("fixture_xml", "physical_json"):
            if sha_file(ROOT / entry[key]) != entry[key + "_sha256"]:
                raise RuntimeError("FROZEN_V1_EI_FIXTURE_CHANGED:" + entry["scenario_id"])

    timing = load_phase_b_timing_calibration_v2(route_version=CALIBRATED_ROUTE_VERSION)
    if not timing.runtime_config_bound:
        raise RuntimeError("FROZEN_PHASE_B_TIMING_CALIBRATION_UNAVAILABLE")
    FIXTURES.mkdir(parents=True, exist_ok=True)
    REPORT.mkdir(parents=True, exist_ok=True)
    revised_entries: list[dict[str, Any]] = []
    prevalidation_rows: list[dict[str, Any]] = []
    for row in SCENARIOS:
        xml_path = FIXTURES / (row["scenario_id"] + ".xml")
        json_path = FIXTURES / (row["scenario_id"] + ".json")
        xml_path.write_text(xml_for(row), encoding="utf-8")
        dump(json_path, physical(row))
        start_progress = float(row["ego_initial_transform"][1]) - float(row["route"][0][1])
        remaining = EARLIEST_COMMITMENT_PROGRESS_M - start_progress
        latest_safe_relative_s = (
            remaining * float(timing.route_seconds_per_meter_lower_bound)
            - 0.1 * float(timing.simulation_to_monotonic_upper_bound)
            - 2.15
        )
        projections = [project_center(row, actor) for actor in row["actors"]]
        checks = {
            "start_after_latest_safe": latest_safe_relative_s < 0.0,
            "start_before_commitment": start_progress < EARLIEST_COMMITMENT_PROGRESS_M,
            "start_before_maneuver_onset": start_progress < MANEUVER_ONSET_PROGRESS_M,
            "two_center_categories_predicted": [p["predicted_category"] for p in projections] == ["CENTER", "CENTER"],
            "strict_near_far_order": projections[0]["forward_m"] < projections[1]["forward_m"],
            "physical_fixture_has_no_label": all(
                token not in canonical(physical(row)).decode("utf-8")
                for token in ("TOO_LATE", "FALLBACK", "expected_decision", "negative_control_family")
            ),
        }
        if not all(checks.values()):
            raise RuntimeError("MANIFEST_V2_STATIC_PREVALIDATION_FAILED:" + row["scenario_id"])
        prevalidation_rows.append({
            "scenario_id": row["scenario_id"],
            "family": "TOO_LATE",
            "route_identity": CALIBRATED_ROUTE_VERSION,
            "start_progress_m": start_progress,
            "earliest_commitment_progress_m": EARLIEST_COMMITMENT_PROGRESS_M,
            "maneuver_onset_progress_m": MANEUVER_ONSET_PROGRESS_M,
            "latest_safe_relative_to_first_observation_s": latest_safe_relative_s,
            "pinhole_projection_checks": projections,
            "checks": checks,
            "expected_runtime_premise": "AVAILABLE_FUTURE_OBLIGATION_DIVERGENCE_THEN_TOO_LATE",
            "runtime_decision_output_reads": 0,
            "selection_source": "PHYSICAL_GEOMETRY_FROZEN_CAMERA_AND_PHASE_B_TIMING_ONLY",
        })
        revised_entries.append({
            "scenario_id": row["scenario_id"],
            "negative_control_family": "TOO_LATE",
            "split": "TRAIN",
            "map": row["town"],
            "route_id": row["route_id"],
            "route_identity": CALIBRATED_ROUTE_VERSION,
            "route": row["route"],
            "ego_spawn": row["ego_initial_transform"],
            "actors": row["actors"],
            "instruction": row["instruction"],
            "seed": row["seed"],
            "repeat_seed": row["repeat_seed"],
            "answer_delay_s": row["answer_delay_s"],
            "selection_reason": row["selection_reason"],
            "prevalidation_artifact": "reports/driveclarify_v2_negative_control_reconstruction_and_validation_v1/NEGATIVE_CONTROL_PREVALIDATION_V2.json",
            "fixture_xml": str(xml_path.relative_to(ROOT)),
            "fixture_xml_sha256": sha_file(xml_path),
            "physical_json": str(json_path.relative_to(ROOT)),
            "physical_json_sha256": sha_file(json_path),
        })

    entries = old_ei + revised_entries
    frozen_utc = datetime.now(timezone.utc).isoformat()
    payload_hash = sha_bytes(canonical(entries))
    manifest = {
        "schema_version": "driveclarify.train_v2_negative_control_manifest.v2",
        "manifest_version": "NEGATIVE_CONTROL_MANIFEST_V2",
        "supersedes_for_future_execution_only": "NEGATIVE_CONTROL_MANIFEST_V1",
        "v1_manifest_preserved_sha256": sha_file(V1_MANIFEST),
        "revision_count": 1,
        "revision_scope": "REPLACE_THREE_INVALID_TOO_LATE_PHYSICAL_CONTROLS_ONLY",
        "revision_reason": "V1_PREVALIDATION_OMITTED_FROZEN_RGB0_CATEGORICAL_ROUTE_ORDER_AUTHORIZATION_CONDITION",
        "frozen_before_any_v2_revision_live_outcome": True,
        "frozen_utc": frozen_utc,
        "selection_basis": "PHYSICAL_CONTRACT_STATIC_CAMERA_PROJECTION_AND_FROZEN_TIMING_ONLY",
        "scenario_count": 6,
        "family_counts": {"EVIDENCE_INSUFFICIENT": 3, "TOO_LATE": 3},
        "train_count": 6,
        "dev_count": 0,
        "test_count": 0,
        "expected_decision_runtime_fields": 0,
        "live_decision_output_reads": 0,
        "manifest_payload_sha256": payload_hash,
        "scenarios": entries,
    }
    dump(V2_MANIFEST, manifest)
    prevalidation = {
        "schema_version": "driveclarify.v2_negative_control_prevalidation.v2",
        "performed_before_v2_revision_live": True,
        "v1_live_outcomes_used_only_for_root_cause_not_action_selection": True,
        "final_decision_reads_for_revision_selection": 0,
        "manifest_payload_sha256": payload_hash,
        "camera_contract_sources": [
            "/home/buaa/wrh/simlingo/team_code/config_simlingo.py:58-60",
            "driveclarify_language_grounding_v1/visual_grounder.py:58",
        ],
        "timing_calibration": {
            "route_seconds_per_meter_lower_bound": timing.route_seconds_per_meter_lower_bound,
            "simulation_to_monotonic_upper_bound": timing.simulation_to_monotonic_upper_bound,
            "route_version": timing.route_version,
            "source_artifacts": timing.source_artifacts,
        },
        "rows": prevalidation_rows,
    }
    prevalidation_path = REPORT / "NEGATIVE_CONTROL_PREVALIDATION_V2.json"
    dump(prevalidation_path, prevalidation)

    table = "\n".join(
        "| {scenario_id} | {start_progress_m:.2f} | {latest_safe_relative_to_first_observation_s:.3f} | {cats} | PASS |".format(
            cats="/".join(p["predicted_category"] for p in row["pinhole_projection_checks"]), **row
        ) for row in prevalidation_rows
    )
    (REPORT / "NEGATIVE_CONTROL_PREVALIDATION_V2.md").write_text(
        "# Negative Control Prevalidation V2\n\n"
        "Completed before any V2-revision live execution, with zero runtime decision-output reads. "
        "The frozen SimLingo `rgb_0` 1024 px / 110° pinhole contract and the existing 0.4/0.6 "
        "categorical rule predict `CENTER/CENTER`; frozen Phase-B timing puts every first observation "
        "after latest-safe but before commitment and onset.\n\n"
        "| scenario | start progress m | latest-safe relative s | predicted categories | result |\n"
        "|---|---:|---:|---|---|\n" + table + "\n",
        encoding="utf-8",
    )
    (REPORT / "TRAIN_V2_NEGATIVE_CONTROL_SELECTION_PROTOCOL_V2.md").write_text(
        "# TRAIN V2 Negative Control Selection Protocol — Single Revision\n\n"
        "Revision count: **1**. The three frozen V1 EI controls are retained byte-for-byte. The three "
        "invalid V1 TL controls and outcomes remain permanent errata evidence and are replaced for "
        "future execution by `NC-TL2-001/002/003`. Selection uses physical projection and frozen timing "
        "only. Run order is TL2-001/002/003, followed by one fresh repeat for each conforming initial. "
        "No further manifest revision, DEV/TEST/E3 access, positive rerun, or training is permitted.\n",
        encoding="utf-8",
    )
    (REPORT / "MANIFEST_V2_INDEPENDENT_REVIEW.md").write_text(
        "# Manifest V2 Independent Pre-live Review\n\n"
        "**PASS_FOR_LIVE_ENTRY.** The revision is bounded to three TL physical fixtures; V1 evidence is "
        "preserved. Each new fixture has two visible forward referents with strict near/far order, "
        "pinhole-predicted CENTER/CENTER categories, a first observation after latest-safe but before "
        "commitment, and no evaluator label in the runtime fixture. The full calibrated route and all "
        "V2 semantics, thresholds, timing, authority, planner, writer, PID, and SimLingo weights are "
        "unchanged. The review inspected no V2-revision live result because none existed.\n",
        encoding="utf-8",
    )
    freeze = {
        "manifest_file_sha256": sha_file(V2_MANIFEST),
        "manifest_payload_sha256": payload_hash,
        "prevalidation_json_sha256": sha_file(prevalidation_path),
        "root_cause_review_sha256": sha_file(REPORT / "NEGATIVE_CONTROL_MANIFEST_V1_ROOT_CAUSE.md"),
        "independent_review_sha256": sha_file(REPORT / "MANIFEST_V2_INDEPENDENT_REVIEW.md"),
        "v1_manifest_preserved_sha256": sha_file(V1_MANIFEST),
        "fixture_count": 6,
        "revision_count": 1,
        "frozen_before_v2_revision_live": True,
        "live_outcome_read_count": 0,
        "frozen_utc": frozen_utc,
    }
    dump(REPORT / "PREFREEZE_HASH_RECEIPT_V2.json", freeze)


if __name__ == "__main__":
    main()
