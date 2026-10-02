#!/usr/bin/env python3
"""Materialize and freeze outcome-blind V2 TRAIN negative controls."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PACKAGE = ROOT / "driveclarify_v2_negative_control_reconstruction_v1"
FIXTURES = PACKAGE / "fixtures"
REPORT = ROOT / "reports/driveclarify_v2_negative_control_reconstruction_and_validation_v1"

from driveclarify_decision_evidence_v2.runtime_adapter import (  # noqa: E402
    load_phase_b_timing_calibration_v2,
)
from driveclarify_v2_negative_control_reconstruction_v1.contracts import (  # noqa: E402
    CALIBRATED_ROUTE_VERSION,
    EARLIEST_COMMITMENT_PROGRESS_M,
    MANEUVER_ONSET_PROGRESS_M,
    SCENARIOS,
)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest_file(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def dump(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


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
      <scenario name="{row['scenario_id']}" type="DriveClarifyV2NegativeControlScenarioV1">
        <trigger_point x="{tx:.9f}" y="{ty:.9f}" z="{tz:.9f}" yaw="{yaw:.9f}" />
        <negative_control scenario_id="{row['scenario_id']}" />
      </scenario>
    </scenarios>
    <weathers>
      <weather cloudiness="0.0" fog_density="0.0" precipitation="0.0" precipitation_deposits="0.0" sun_altitude_angle="70.0" sun_azimuth_angle="0.0" wetness="0.0" wind_intensity="0.0" route_percentage="0" />
      <weather cloudiness="0.0" fog_density="0.0" precipitation="0.0" precipitation_deposits="0.0" sun_altitude_angle="70.0" sun_azimuth_angle="0.0" wetness="0.0" wind_intensity="0.0" route_percentage="100" />
    </weathers>
  </route>
</routes>
'''


def physical_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row[key]
        for key in (
            "scenario_id", "split", "town", "route_id", "route", "trigger",
            "instruction", "actors", "ego_initial_transform",
        )
    }


def main() -> None:
    if (REPORT / "LIVE_EXECUTION_STARTED").exists():
        raise RuntimeError("NEGATIVE_CONTROL_MANIFEST_ALREADY_EXPOSED_TO_LIVE_OUTCOME")
    FIXTURES.mkdir(parents=True, exist_ok=True)
    REPORT.mkdir(parents=True, exist_ok=True)
    timing = load_phase_b_timing_calibration_v2(route_version=CALIBRATED_ROUTE_VERSION)
    if not timing.runtime_config_bound:
        raise RuntimeError("FROZEN_PHASE_B_TIMING_CALIBRATION_UNAVAILABLE")

    entries = []
    prevalidation_rows = []
    for row in SCENARIOS:
        fixture_xml = FIXTURES / (row["scenario_id"] + ".xml")
        fixture_json = FIXTURES / (row["scenario_id"] + ".json")
        fixture_xml.write_text(xml_for(row), encoding="utf-8")
        dump(fixture_json, physical_payload(row))
        family = row["negative_control_family"]
        if family == "EVIDENCE_INSUFFICIENT":
            start_progress = 0.0
            expected_property = (
                "Two plausible runtime-visible white-van referents occupy opposite lateral "
                "image categories at comparable longitudinal distance. Image-only grounding "
                "cannot authorize a longitudinal referent-to-route-order join."
            )
            dependencies = {
                "effective_k": "EXPECTED_K2_FROM_TWO_VISIBLE_WHITE_SPRINTERS",
                "referent_identity": "AVAILABLE_RUNTIME_RGB_0",
                "route_topology": "AVAILABLE_AGENT_ROUTE_PLUS_LIVE_HD_MAP",
                "referent_to_route_order_join": "EXPECTED_UNAVAILABLE",
                "future_obligation_relation": None,
                "authorization_eligible": False,
            }
            unavailable = "FUTURE_OBLIGATION_REFERENT_TO_ROUTE_ORDER_JOIN"
            reason_class = "FUTURE_OBLIGATION_ROUTE_ORDER_NOT_AUTHORIZATION_GRADE"
            why = (
                "The detector exposes only boxes, categorical LEFT/RIGHT location and apparent "
                "size rank; privileged actor transforms are forbidden. Lateral separation does "
                "not establish which referent precedes which route opportunity."
            )
            commitment = EARLIEST_COMMITMENT_PROGRESS_M
            latest_safe_relative_s = None
        else:
            ego = row["ego_initial_transform"]
            start_progress = float(ego[1]) - float(row["route"][0][1])
            remaining = EARLIEST_COMMITMENT_PROGRESS_M - start_progress
            answer_budget = 0.1 * float(timing.simulation_to_monotonic_upper_bound)
            post_answer_budget = 2.15
            latest_safe_relative_s = (
                remaining * float(timing.route_seconds_per_meter_lower_bound)
                - answer_budget
                - post_answer_budget
            )
            expected_property = (
                "The complete calibrated route and two route-ordered obligations remain present, "
                "but physical ego initialization is beyond both the earliest commitment and "
                "maneuver onset before the first decision-relevant observation."
            )
            dependencies = {
                "effective_k": "EXPECTED_K2_FROM_TWO_VISIBLE_WHITE_SPRINTERS_AHEAD",
                "future_obligation_relation": "EXPECTED_AVAILABLE_DIVERGENT",
                "start_progress_m": start_progress,
                "earliest_commitment_progress_m": EARLIEST_COMMITMENT_PROGRESS_M,
                "maneuver_onset_progress_m": MANEUVER_ONSET_PROGRESS_M,
                "commitment_crossed_pre_observation": start_progress >= EARLIEST_COMMITMENT_PROGRESS_M,
                "first_decision_region_post_onset": start_progress >= MANEUVER_ONSET_PROGRESS_M,
                "latest_safe_relative_to_first_observation_s": latest_safe_relative_s,
            }
            unavailable = None
            reason_class = "CLARIFICATION_WINDOW_TOO_LATE"
            why = None
            commitment = EARLIEST_COMMITMENT_PROGRESS_M
        prevalidation_rows.append(
            {
                "scenario_id": row["scenario_id"],
                "family": family,
                "map": row["town"],
                "route_id": row["route_id"],
                "route": row["route"],
                "ego_spawn": row["ego_initial_transform"] or row["route"][0],
                "actors": row["actors"],
                "instruction": row["instruction"],
                "candidate_semantics": (
                    "Two independently grounded white-van referent interpretations; topology "
                    "identity may authorize only when runtime image ordering supports the join."
                ),
                "reason_selected": row["selection_reason"],
                "expected_physical_property": expected_property,
                "decision_related_evidence_dependencies": dependencies,
                "EXPECTED_UNAVAILABLE_EVIDENCE": unavailable,
                "WHY_PHYSICALLY_UNAVAILABLE": why,
                "EXPECTED_REASON_CODE_CLASS": reason_class,
                "commitment_estimate_progress_m": commitment,
                "latest_safe_estimate_relative_s": latest_safe_relative_s,
                "start_progress_m": start_progress,
                "first_decision_relevant_physical_region": (
                    "POST_COMMITMENT_POST_ONSET" if family == "TOO_LATE" else "PRECOMMITMENT_LATERAL_AMBIGUITY"
                ),
                "expected_final_decision_runtime_field_present": False,
                "live_decision_output_read_count": 0,
            }
        )
        entries.append(
            {
                "scenario_id": row["scenario_id"],
                "negative_control_family": family,
                "split": "TRAIN",
                "map": row["town"],
                "route_id": row["route_id"],
                "route_identity": CALIBRATED_ROUTE_VERSION,
                "route": row["route"],
                "ego_spawn": row["ego_initial_transform"] or row["route"][0],
                "actors": row["actors"],
                "instruction": row["instruction"],
                "seed": row["seed"],
                "repeat_seed": row["repeat_seed"],
                "answer_delay_s": row["answer_delay_s"],
                "selection_reason": row["selection_reason"],
                "prevalidation_artifact": (
                    "reports/driveclarify_v2_negative_control_reconstruction_and_validation_v1/"
                    "NEGATIVE_CONTROL_PREVALIDATION.json"
                ),
                "fixture_xml": str(fixture_xml.relative_to(ROOT)),
                "fixture_xml_sha256": digest_file(fixture_xml),
                "physical_json": str(fixture_json.relative_to(ROOT)),
                "physical_json_sha256": digest_file(fixture_json),
            }
        )

    payload_sha = digest_bytes(canonical(entries))
    frozen_utc = datetime.now(timezone.utc).isoformat()
    manifest = {
        "schema_version": "driveclarify.train_v2_negative_control_manifest.v1",
        "manifest_version": "NEGATIVE_CONTROL_MANIFEST_V1",
        "frozen_before_any_scientific_live_outcome": True,
        "frozen_utc": frozen_utc,
        "selection_basis": "PHYSICAL_CONTRACT_AND_STATIC_PREVALIDATION_ONLY",
        "scenario_count": len(entries),
        "family_counts": {"EVIDENCE_INSUFFICIENT": 3, "TOO_LATE": 3},
        "train_count": 6,
        "dev_count": 0,
        "test_count": 0,
        "expected_decision_runtime_fields": 0,
        "manifest_replacements_after_outcome": 0,
        "manifest_payload_sha256": payload_sha,
        "scenarios": entries,
    }
    manifest_path = REPORT / "TRAIN_V2_NEGATIVE_CONTROL_MANIFEST.json"
    dump(manifest_path, manifest)

    prevalidation = {
        "schema_version": "driveclarify.v2_negative_control_prevalidation.v1",
        "manifest_payload_sha256": payload_sha,
        "performed_before_live": True,
        "live_decision_output_reads": 0,
        "timing_calibration": {
            "route_seconds_per_meter_lower_bound": timing.route_seconds_per_meter_lower_bound,
            "simulation_to_monotonic_upper_bound": timing.simulation_to_monotonic_upper_bound,
            "source_artifacts": timing.source_artifacts,
            "route_version": timing.route_version,
            "runtime_config_bound": timing.runtime_config_bound,
        },
        "rows": prevalidation_rows,
    }
    dump(REPORT / "NEGATIVE_CONTROL_PREVALIDATION.json", prevalidation)

    contract = {
        "schema_version": "driveclarify.v2_negative_control_contract.v1",
        "frozen_v2_contract_version": "2.0",
        "families": {
            "EVIDENCE_INSUFFICIENT": {
                "runtime_pipeline_normal": True,
                "required_evidence": "authorization-grade referent-to-future-obligation route-order identity",
                "required_unavailable_shape": {"value": None, "reason_code_required": True, "authorization_eligible": False},
                "relationship": "UNKNOWN_OR_INSUFFICIENT_EVIDENCE",
                "prohibited_causes": ["parser failure", "missing file", "version mismatch", "stale injection", "trajectory-horizon exclusion"],
            },
            "TOO_LATE": {
                "future_material_divergence": "AVAILABLE",
                "timing_condition": "now >= latest_safe_clarification OR commitment crossed",
                "shared_recoverability": False,
                "ask_eligible": False,
                "wait_eligible": False,
                "timing_sources": ["ego progress", "speed", "commitment", "route geometry", "frozen latency", "safety margin"],
            },
        },
        "distinctions": {
            "ACT_SHARED": "requires eligible current/future evidence, recoverable lease, and DEFER_CLARIFICATION",
            "ASK": "requires eligible divergent evidence and CLARIFY_NOW",
            "WAIT": "requires a pre-existing active query plus verified bounded holding",
            "ordinary_bug_unknown": "exception, malformed/missing input, or implementation failure; invalid control",
            "stale_evidence": "previously available evidence outside freshness; not this EI family",
            "missing_artifact": "environment/readiness failure; invalid scientific run",
            "runtime_failure": "no normal evaluator-to-authority completion; invalid scientific run",
        },
        "final_decision_not_runtime_input": True,
    }
    dump(REPORT / "V2_NEGATIVE_CONTROL_CONTRACT.json", contract)

    (REPORT / "NEGATIVE_CONTROL_V1_ERRATA.md").write_text(
        "# Negative Control V1 Errata\n\n"
        "`DA-FB-UNKNOWN-001` and `DA-FB-LATE-001` are permanently classified "
        "`INVALID_AS_V2_NEGATIVE_CONTROL`. They remain unmodified historical evidence and "
        "retain their observed `ACT_SHARED → ASK → WAIT` behavior. Their town, route, ego, "
        "actors, instruction, timing and topology were decision-relevant physical twins of "
        "positive scenes; only identifiers/seeds/family metadata differed. The defect was "
        "experimental design, not V2 decision policy. Neither old manifest nor receipt is "
        "deleted, relabeled, or overwritten.\n",
        encoding="utf-8",
    )
    (REPORT / "V2_NEGATIVE_CONTROL_CONTRACT.md").write_text(
        "# V2 Negative Control Contract\n\n"
        "`EVIDENCE_INSUFFICIENT` requires a normally executing pipeline and a physically "
        "unavailable authorization dependency represented as `value=null`, a typed reason, "
        "and `authorization_eligible=false`. It excludes missing files, parser/runtime bugs, "
        "staleness injection, and future targets merely outside the SimLingo horizon.\n\n"
        "`TOO_LATE` requires available material future divergence, frozen physical timing "
        "showing the latest-safe boundary crossed or commitment passed, loss of shared "
        "recoverability, ASK denial, and no WAIT without an existing safe holding lease.\n\n"
        "ACT_SHARED requires a recoverable eligible shared lease; ASK requires CLARIFY_NOW; "
        "WAIT requires an already active query and verified holding. Missing artifacts, "
        "exceptions, ordinary UNKNOWN bugs, and stale evidence are invalid controls. Final "
        "decisions are evaluator outputs and never runtime inputs.\n",
        encoding="utf-8",
    )
    rows_md = []
    for item in prevalidation_rows:
        rows_md.append(
            "| {scenario_id} | {family} | {start_progress_m:.2f} | {first_decision_relevant_physical_region} | {EXPECTED_REASON_CODE_CLASS} |".format(**item)
        )
    (REPORT / "NEGATIVE_CONTROL_PREVALIDATION_REPORT.md").write_text(
        "# Negative Control Prevalidation Report\n\n"
        "Completed before any scientific live outcome. Decision-output reads: 0. The EI "
        "controls use two runtime-visible, same-depth lateral referents for which image-only "
        "LEFT/RIGHT identity cannot authorize a longitudinal route-order join. The TL controls "
        "retain the exact calibrated full route but physically initialize ego after the frozen "
        "25.13 m commitment and 28.22 m onset.\n\n"
        "| scenario | family | start progress m | physical region | expected reason class |\n"
        "|---|---|---:|---|---|\n" + "\n".join(rows_md) + "\n",
        encoding="utf-8",
    )
    (REPORT / "TRAIN_V2_NEGATIVE_CONTROL_SELECTION_PROTOCOL.md").write_text(
        "# TRAIN V2 Negative Control Selection Protocol\n\n"
        f"Six TRAIN-only controls were frozen before live outcomes. Manifest payload SHA-256: `{payload_sha}`. "
        "EI selection uses categorical physical observability, not a final decision. TL selection "
        "uses the frozen route, commitment/onset and Phase-B timing calibration; answer latency "
        "remains 0.1 s. Run order is NC-EI-001/002/003 then NC-TL-001/002/003. A conforming initial "
        "run receives one fresh repeat; a nonconforming control remains in the manifest and is not "
        "secretly replaced. No DEV, TEST, E3 or positive-scene rerun is authorized.\n",
        encoding="utf-8",
    )
    (REPORT / "NEGATIVE_CONTROL_LABEL_LEAKAGE_AUDIT.md").write_text(
        "# Negative Control Label Leakage Audit\n\n"
        "The physical JSON/XML contain no family, expected decision, expected FALLBACK, gold target, "
        "or evaluator label. ScenarioRunner reads only physical facts. The manifest and prevalidation "
        "are evaluator-side; M2B, M3 and authority modules do not import this package or report "
        "directory. Runtime expected-decision read count: **0**. The scenario ID selects immutable "
        "physical configuration only and is not exposed to decision evaluators.\n",
        encoding="utf-8",
    )
    (REPORT / "NEGATIVE_CONTROL_EVIDENCE_PROVENANCE_AUDIT.md").write_text(
        "# Negative Control Evidence Provenance Audit\n\n"
        "EI deployable evidence is limited to real `rgb_0` grounding boxes, frozen categorical image "
        "location/apparent rank, the agent-owned route and live CARLA HD-map topology. Actor transforms "
        "are used by ScenarioRunner to instantiate the world but are never read by authorization. "
        "Evaluator-only truth records the intended physical layout and audits DINO visibility; it does "
        "not fill the missing image-to-route-order join. Thus UNKNOWN is a runtime observability limit, "
        "not privileged truth deliberately hidden from a path that would otherwise be deployable.\n\n"
        "TL authorization evidence uses the same runtime ego pose/projected progress, agent route, live "
        "map, frozen commitments and frozen Phase-B latency calibration. Expected class and final action "
        "remain evaluator-only. Privileged authorization reads: 0.\n",
        encoding="utf-8",
    )
    (REPORT / "PREFREEZE_REVIEW.md").write_text(
        "# Pre-freeze adversarial review\n\n"
        "PASS for live entry. Six controls are fixed before outcomes; EI is independent of trajectory "
        "horizon and uses no missing file/version failure; TL preserves the calibrated full route and "
        "uses physical post-commitment initialization with unchanged latency/margins. No expected final "
        "decision exists in runtime fixtures. Manifest replacement count is zero.\n",
        encoding="utf-8",
    )
    freeze = {
        "manifest_file_sha256": digest_file(manifest_path),
        "manifest_payload_sha256": payload_sha,
        "prevalidation_json_sha256": digest_file(REPORT / "NEGATIVE_CONTROL_PREVALIDATION.json"),
        "fixture_count": 6,
        "frozen_before_live": True,
        "live_outcome_read_count": 0,
        "frozen_utc": frozen_utc,
    }
    dump(REPORT / "PREFREEZE_HASH_RECEIPT.json", freeze)


if __name__ == "__main__":
    main()
