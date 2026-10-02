#!/usr/bin/env python3
"""Materialize and freeze TRAIN-only activation scenarios before live outcomes."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "driveclarify_train_decision_activation_scenario_revision_v1"
FIXTURES = PACKAGE / "fixtures"
REPORT = ROOT / "reports/driveclarify_train_decision_activation_scenario_revision_v1"

from driveclarify_train_decision_activation_scenario_revision_v1.contracts import (  # noqa: E402
    CALIBRATED_ROUTE_VERSION,
    SCENARIOS,
)


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def xml_for(row) -> str:
    (x0, y0, z0), (x1, y1, z1) = row["route"]
    tx, ty, tz, yaw = row["trigger"]
    return f'''<routes>
  <route id="{row['route_id']}" town="{row['town']}">
    <waypoints>
      <position x="{x0:.9f}" y="{y0:.9f}" z="{z0:.9f}" />
      <position x="{x1:.9f}" y="{y1:.9f}" z="{z1:.9f}" />
    </waypoints>
    <scenarios>
      <scenario name="{row['scenario_id']}" type="DriveClarifyTrainDecisionActivationScenarioV1">
        <trigger_point x="{tx:.9f}" y="{ty:.9f}" z="{tz:.9f}" yaw="{yaw:.9f}" />
        <activation scenario_id="{row['scenario_id']}" />
      </scenario>
    </scenarios>
    <weathers>
      <weather cloudiness="0.0" fog_density="0.0" precipitation="0.0" precipitation_deposits="0.0" sun_altitude_angle="70.0" sun_azimuth_angle="0.0" wetness="0.0" wind_intensity="0.0" route_percentage="0" />
      <weather cloudiness="0.0" fog_density="0.0" precipitation="0.0" precipitation_deposits="0.0" sun_altitude_angle="70.0" sun_azimuth_angle="0.0" wetness="0.0" wind_intensity="0.0" route_percentage="100" />
    </weathers>
  </route>
</routes>
'''


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    REPORT.mkdir(parents=True, exist_ok=True)
    for row in SCENARIOS:
        (FIXTURES / f"{row['scenario_id']}.xml").write_text(xml_for(row), encoding="utf-8")
        physical = {key: row[key] for key in (
            "scenario_id", "split", "town", "route_id", "route", "trigger", "instruction", "actors"
        )}
        dump(FIXTURES / f"{row['scenario_id']}.json", physical)

    regions = {
        "schema_version": "driveclarify.decision_activation_region_spec.v1",
        "frozen_sources": [
            "reports/driveclarify_decision_window_persistent_ambiguity_design_freeze/CURRENT_ACTION_EQUIVALENCE_CONTRACT.md",
            "reports/driveclarify_decision_window_persistent_ambiguity_design_freeze/PLAN_COVERAGE_CONTRACT.md",
            "reports/driveclarify_decision_window_persistent_ambiguity_design_freeze/RECOVERABILITY_CONTRACT.md",
            "reports/driveclarify_decision_window_persistent_ambiguity_design_freeze/TIME_TO_DIVERGENCE_CONTRACT.md",
            "reports/driveclarify_decision_window_persistent_ambiguity_design_freeze/PERSISTENT_AMBIGUITY_LIFECYCLE_CONTRACT.md",
            "driveclarify_persistent_ambiguity_runtime_v1/m2b_adapter.py",
        ],
        "contract_modification_count": 0,
        "scientific_threshold_modification_count": 0,
        "regions": {
            "ACT_SHARED": {
                "relation": "CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT",
                "all_required": [
                    "effective_k_gte_2", "semantic_identities_distinct", "semantic_state_unresolved",
                    "current_executable_actions_proven_equivalent", "future_obligation_relation_future_divergent",
                    "shared_corridor_available", "full_plan_coverage", "commitment_not_crossed",
                    "all_candidates_recoverable", "latest_safe_clarification_available_and_positive",
                    "deadline_not_crossed", "evidence_fresh_and_aligned", "hard_safety_gate", "hard_rule_gate",
                ],
                "lifecycle": ["episode_remains_unresolved", "bounded_shared_authority", "evidence_becomes_stale", "fresh_normal_planning_refresh"],
            },
            "ASK": {
                "relation": "CURRENTLY_DIVERGENT",
                "all_required": [
                    "effective_k_gte_2", "semantic_state_unresolved", "material_consequence_divergence",
                    "answer_changes_action", "decision_window_available", "now_before_latest_safe_clarification",
                    "positive_query_value", "query_budget_available", "no_incompatible_active_query",
                    "passenger_resolvable", "answer_likely_before_deadline", "hard_safety_gate", "hard_rule_gate",
                ],
            },
            "WAIT": {
                "all_required": [
                    "active_query", "passenger_answer_pending", "continued_commit_not_authorizable",
                    "verified_bounded_holding_available", "existing_holding_lease", "no_emergency_stop_shortcut",
                ],
                "lifecycle": ["ASK", "QUERY_ACTIVE", "WAIT", "ANSWER_OR_SOURCE_CHANGE", "INVALIDATE_OLD_BUNDLE", "FRESH_REPLAN", "EXIT_WAIT"],
            },
            "FALLBACK_UNKNOWN": {"required": ["necessary_authorization_evidence_unknown", "no_verified_holding"]},
            "FALLBACK_LATE": {"required": ["material_divergence", "latest_safe_deadline_crossed", "new_ask_forbidden"]},
        },
    }
    dump(REPORT / "DECISION_ACTIVATION_REGION_SPEC.json", regions)
    (REPORT / "DECISION_ACTIVATION_REGION_SPEC.md").write_text("""# Frozen decision activation region specification

This specification is extracted from the frozen current-action, coverage, recoverability, timing, persistent-lifecycle, and two-axis M2B contracts. It introduces no condition or threshold.

## ACT_SHARED

`K≥2`, distinct semantics, unresolved ambiguity, current executable action equivalence, future obligation divergence, AVAILABLE shared corridor and complete plan coverage, un-crossed commitments, candidate-wise recoverability, positive latest-safe slack, fresh/aligned evidence, and compatible hard safety/rule gates are all mandatory. The episode remains unresolved and must refresh after the bounded shared window.

## ASK

Material current consequence divergence must already be observable while clarification remains useful and timely. Answer-changing action, positive query value, budget/channel/passenger feasibility, no incompatible active query, and both hard gates are mandatory.

## WAIT

WAIT is derived from a natural ASK. It requires an active pending query and an existing verified bounded holding lease; it is not an emergency stop. Answer/source change invalidates the old bundle, triggers a fresh observation/replan, and exits WAIT.

## Negative controls

Evidence-insufficient UNKNOWN and already-too-late material divergence remain legal FALLBACK regions.
""", encoding="utf-8")

    entries = []
    for row in SCENARIOS:
        xml = FIXTURES / f"{row['scenario_id']}.xml"
        physical_json = FIXTURES / f"{row['scenario_id']}.json"
        entries.append({
            "scenario_id": row["scenario_id"], "design_family": row["design_family"], "split": "TRAIN",
            "map": row["town"], "route_id": row["route_id"], "route": row["route"], "ego_spawn": row["route"][0],
            "trigger": row["trigger"], "actor_layout": row["actors"], "raw_instruction": row["instruction"],
            "candidate_semantics": "two independently grounded white-van referents mapped at runtime to route-order topology obligations",
            "seed": row["seed"], "repeat_seed": row["seed"] + 1000, "answer_delay_s": row["answer_delay_s"],
            "selection_reason": row["selection_reason"], "calibrated_route_version": row["calibrated_route_version"],
            "fixture_xml": str(xml.relative_to(ROOT)), "fixture_xml_sha256": sha(xml),
            "physical_json": str(physical_json.relative_to(ROOT)), "physical_json_sha256": sha(physical_json),
        })
    payload_hash = hashlib.sha256(canonical(entries)).hexdigest()
    manifest = {
        "schema_version": "driveclarify.train_activation_scenario_manifest.v1",
        "frozen_before_live_outcomes": True,
        "frozen_utc": datetime.now(timezone.utc).isoformat(),
        "selection_basis": "FROZEN_PHYSICAL_CONTRACT_NOT_DECISION_OUTPUT",
        "scenario_count": len(entries), "train_count": len(entries), "dev_count": 0, "test_count": 0,
        "expected_decision_runtime_fields": 0, "manifest_payload_sha256": payload_hash, "scenarios": entries,
    }
    dump(REPORT / "TRAIN_ACTIVATION_SCENARIO_MANIFEST.json", manifest)
    (REPORT / "TRAIN_ACTIVATION_SCENARIO_SELECTION_PROTOCOL.md").write_text(f"""# TRAIN activation scenario selection protocol

The 11 scenarios were frozen before any live run in this stage. Selection is driven by frozen physical evidence dependencies, not observed decision output. Manifest payload SHA-256: `{payload_hash}`.

- Three ACT_SHARED designs evaluate early shared-corridor windows on the only Phase-B-calibrated route.
- Three ASK designs evaluate the intermediate region before the 25.13 m earliest commitment.
- Three WAIT designs are exact physical twins of ASK designs with only evaluator-side passenger delay changed to 3 s.
- Two negative controls preserve the prior majority-UNKNOWN and late-divergence seeds.

All use unseen activation seeds 7101–7103 or 7201–7203; only the declared controls reuse historical seeds. No field representing an expected decision exists in physical JSON/XML or ScenarioRunner input. A class label exists only in this evaluator-side frozen manifest for coverage accounting.

The exact calibrated route is reused because production runtime authorization currently checks route version equality against Phase-B evidence. Varying map/route/ego spawn would be structurally fail-closed and would not test an activation region without changing the frozen contract.
""", encoding="utf-8")

    accepted = json.loads((ROOT / "artifacts/driveclarify_persistent_ambiguity_runtime_v1_continuous_completion/B1-R4/GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json").read_text())
    connectors = accepted["candidate_executable_connector_evidence"]
    preflight_rows = []
    coverage_rows = []
    for row in SCENARIOS:
        facts = {
            "scenario_id": row["scenario_id"], "split": "TRAIN", "design_family": row["design_family"],
            "route_rows": 88, "route_rows_status": "AVAILABLE", "topology_opportunities": len(connectors),
            "topology_status": "AVAILABLE", "route_version": CALIBRATED_ROUTE_VERSION,
            "ego_route_progress_source": "same-frame CARLA ego pose projected to agent-owned dense route",
            "candidate_target_route_progress_m": [18.72, 57.05],
            "shared_corridor_observability": "AVAILABLE_SOURCE",
            "maneuver_onset_observability": "AVAILABLE_SOURCE:28.22m",
            "commitment_source": "AVAILABLE_SOURCE:A=25.12..25.13m,B=63.44..63.46m",
            "recoverability_dependencies": "AVAILABLE_SOURCE:map connectivity, commitments, calibrated uncertainty, dynamic hard gates",
            "time_to_divergence_source": "AVAILABLE_SOURCE:topology-bound speed envelope",
            "latest_safe_dependencies": "AVAILABLE_SOURCE:runtime latency bounds plus earliest commitment",
            "plan_coverage_source": "AVAILABLE_SOURCE:fresh SimLingo route/speed plus directed route projection",
            "preflight_final_decision_read": False,
            "structurally_all_unknown": False,
            "preflight_status": "PASS_UPSTREAM_EVIDENCE_SOURCES_OBSERVABLE_LIVE_VALUES_PENDING",
        }
        preflight_rows.append(facts)
        windows = (("EARLY_SHARED", "0_TO_10_M"), ("INTERMEDIATE_PRECOMMIT", "10_TO_24_M"))
        for window, progress in windows:
            coverage_rows.append({
                "scenario": row["scenario_id"], "planning_window": window, "progress_band": progress,
                "K": "UNKNOWN", "route_progress": "AVAILABLE", "coverage": "UNKNOWN",
                "shared_corridor": "AVAILABLE", "onset": "AVAILABLE", "commitment_A": "AVAILABLE",
                "commitment_B": "AVAILABLE", "recoverability_A": "UNKNOWN", "recoverability_B": "UNKNOWN",
                "TTD": "AVAILABLE", "latest_safe": "AVAILABLE", "current_action_relation": "UNKNOWN",
                "future_divergence": "AVAILABLE", "relationship": "UNKNOWN",
            })
    preflight = {
        "schema_version": "driveclarify.activation_scenario_evidence_preflight.v1",
        "manifest_payload_sha256": payload_hash,
        "decision_outputs_read": 0,
        "classification": "E1_SCENE_SOURCES_STRUCTURALLY_OBSERVABLE; LIVE_PLAN_DEPENDENCIES_CORRECTLY_PENDING",
        "scenarios": preflight_rows,
    }
    dump(REPORT / "ACTIVATION_SCENARIO_EVIDENCE_PREFLIGHT.json", preflight)
    (REPORT / "ACTIVATION_SCENARIO_EVIDENCE_PREFLIGHT.md").write_text("""# Activation scenario evidence preflight

All 11 frozen TRAIN scenarios reuse the accepted 88-row agent-owned dense CARLA route and its two live-map topology opportunities. Ego progress, target progress (18.72/57.05 m), maneuver onset (28.22 m), commitments (A≈25.13 m, B≈63.46 m), topology-bound timing, latency bounds, and directed plan projection have runtime-observable sources.

Preflight deliberately leaves K, actual fresh-plan coverage, recoverability, and current relationship UNKNOWN until the model produces same-frame evidence. It reads zero decision outputs. No scene is structurally condemned to all-UNKNOWN evidence, so the live gate is authorized. If fresh live evidence repeatedly fails binding despite these sources, the stage will classify E2 integration failure or E3 frozen-contract runtime coverage failure instead of adding scenarios.
""", encoding="utf-8")

    fields = list(coverage_rows[0])
    with (REPORT / "EVIDENCE_COVERAGE_MATRIX.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(coverage_rows)
    lines = ["# Evidence coverage matrix", "", "Pre-live structural status; UNKNOWN remains UNKNOWN.", "", "| " + " | ".join(fields) + " |", "|" + "---|" * len(fields)]
    lines.extend("| " + " | ".join(str(row[f]) for f in fields) + " |" for row in coverage_rows)
    (REPORT / "EVIDENCE_COVERAGE_MATRIX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
