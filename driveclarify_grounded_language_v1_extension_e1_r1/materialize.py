"""Materialize append-only E1-R1 fixtures and prefreeze contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from .contracts import (
    ARTIFACT_ROOT,
    FIXTURE_ROOT,
    METHODS,
    PHYSICAL_FIXTURES,
    REPORT_ROOT,
    SCHEMA_PREFIX,
    assert_runtime_clean,
    canonical_sha256,
)
from .population import build_language_scene_catalog


ROOT = Path(__file__).resolve().parents[1]


def _json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def _text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value.rstrip() + "\n", encoding="utf-8")
    os.replace(str(temporary), str(path))


def _route_geometry(fixture: Mapping[str, Any]) -> tuple[list[tuple[float, float, float]], tuple[float, float, float, float]]:
    suffix = int(str(fixture["fixture_id"]).rsplit("-", 1)[-1])
    if fixture["town"] == "Town03":
        start_y = 98.0 + 2.0 * suffix
        end_y = 181.0 + 2.0 * suffix
        return [(-9.65, start_y, 0.02), (-9.10, end_y, 0.02)], (-9.65, start_y + 0.5, 0.02, 89.6)
    return [
        (119.637657261, -2.070423923, 0.016792297),
        (106.98, -30.0 - float(suffix - 1), 0.016792297),
    ], (119.137662215, -2.072649585, 0.016792297, -179.744956994)


def _xml(fixture: Mapping[str, Any]) -> str:
    waypoints, trigger = _route_geometry(fixture)
    rows = [
        "<routes>",
        '  <route id="{}" town="{}">'.format(fixture["route_id"], fixture["town"]),
        "    <waypoints>",
    ]
    rows.extend(
        '      <position x="{:.9f}" y="{:.9f}" z="{:.9f}" />'.format(*point)
        for point in waypoints
    )
    rows.extend(
        [
            "    </waypoints>",
            "    <scenarios>",
            '      <scenario name="{}" type="DriveClarifyGroundedLanguageE1R1Scenario">'.format(
                fixture["fixture_id"]
            ),
            '        <trigger_point x="{:.9f}" y="{:.9f}" z="{:.9f}" yaw="{:.9f}" />'.format(
                *trigger
            ),
            '        <fixture fixture_id="{}" />'.format(fixture["fixture_id"]),
            "      </scenario>",
            "    </scenarios>",
            "    <weathers>",
            '      <weather cloudiness="0.0" fog_density="0.0" precipitation="0.0" precipitation_deposits="0.0" sun_altitude_angle="70.0" sun_azimuth_angle="0.0" wetness="0.0" wind_intensity="0.0" route_percentage="0" />',
            '      <weather cloudiness="0.0" fog_density="0.0" precipitation="0.0" precipitation_deposits="0.0" sun_altitude_angle="70.0" sun_azimuth_angle="0.0" wetness="0.0" wind_intensity="0.0" route_percentage="100" />',
            "    </weathers>",
            "  </route>",
            "</routes>",
        ]
    )
    return "\n".join(rows) + "\n"


def _fixture_manifest(fixture: Mapping[str, Any]) -> dict[str, Any]:
    waypoints, trigger = _route_geometry(fixture)
    actors = []
    for actor in fixture["actors"]:
        actors.append(
            {
                "blueprint": actor[0],
                "appearance": "white",
                "spawn_transform": {
                    "x": actor[1],
                    "y": actor[2],
                    "z": actor[3],
                    "yaw": actor[4],
                },
                "policy_visibility": "rgb_0 only",
            }
        )
    value = {
        "schema_version": SCHEMA_PREFIX + ".physical_fixture.v1",
        "fixture_id": fixture["fixture_id"],
        "split": "TRAIN",
        "population_status": "E1_R1_APPEND_ONLY_PREFREEZE",
        "mechanism_family": fixture["mechanism_family"],
        "town": fixture["town"],
        "route_id": fixture["route_id"],
        "instruction": fixture["instruction"],
        "route_waypoints": [list(point) for point in waypoints],
        "trigger_point": list(trigger),
        "actors": actors,
        "actor_motion_source": fixture["motion_source"],
        "motion": fixture.get("motion"),
        "runtime_policy_inputs": [
            "real_rgb_0",
            "runtime_route_deque",
            "live_carla_hd_map",
            "simulation_frame_and_time",
            "existing_ego_state",
        ],
        "privileged_policy_inputs": 0,
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }
    assert_runtime_clean(value)
    return value


def materialize() -> dict[str, Any]:
    report_root = ROOT / REPORT_ROOT
    artifact_root = ROOT / ARTIFACT_ROOT
    fixture_root = ROOT / FIXTURE_ROOT
    for path in (
        report_root,
        artifact_root / "act_representative",
        artifact_root / "ask_representative",
        artifact_root / "wait_representative",
        fixture_root,
    ):
        path.mkdir(parents=True, exist_ok=True)

    physical_rows = []
    for fixture in PHYSICAL_FIXTURES:
        manifest = _fixture_manifest(fixture)
        manifest_path = fixture_root / (str(fixture["fixture_id"]) + ".json")
        route_path = fixture_root / (str(fixture["fixture_id"]) + ".xml")
        _json(manifest_path, manifest)
        _text(route_path, _xml(fixture))
        physical_rows.append(
            {
                **manifest,
                "runtime_manifest_path": str(manifest_path.relative_to(ROOT)),
                "route_path": str(route_path.relative_to(ROOT)),
                "physical_configuration_sha256": canonical_sha256(manifest),
            }
        )

    language_rows = build_language_scene_catalog()
    physical_catalog = {
        "schema_version": SCHEMA_PREFIX + ".physical_fixture_catalog.v1",
        "status": "PREFREEZE_MATERIALIZED_PENDING_NATIVE_E0",
        "physical_fixture_count": len(physical_rows),
        "counts_by_mechanism": {
            mechanism: sum(row["mechanism_family"] == mechanism for row in physical_rows)
            for mechanism in ("ACT", "ASK", "WAIT")
        },
        "language_identity_relabeling_is_not_physical_diversity": True,
        "fixtures": physical_rows,
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }
    language_catalog = {
        "schema_version": SCHEMA_PREFIX + ".language_scene_identity_catalog.v1",
        "status": "PASS_IDENTITY_FIXTURE_SEPARATION",
        "language_scene_identity_count": len(language_rows),
        "physical_fixture_count": len(physical_rows),
        "split_counts": {
            split: sum(row["split"] == split for row in language_rows)
            for split in ("train", "dev", "test")
        },
        "identities": language_rows,
        "extension_dev_attempt_count": 0,
        "extension_test_attempt_count": 0,
        "extension_test_consumed": False,
    }
    _json(report_root / "PHYSICAL_FIXTURE_CATALOG.json", physical_catalog)
    _json(report_root / "LANGUAGE_SCENE_IDENTITY_CATALOG.json", language_catalog)

    topology_contract = {
        "schema_version": SCHEMA_PREFIX + ".topology_target_binding_contract.v1",
        "status": "CLOSED_PREFREEZE_CONTRACT",
        "input_boundary": ["runtime_route_deque", "live_carla_hd_map"],
        "image_topology_join": "apparent near-to-far referent order joins route opportunity order",
        "ask_binding": [
            "nearer referent -> first legal right-junction branch",
            "farther referent -> second legal right-junction branch",
        ],
        "hard_gates": [
            "effective_k >= 2",
            "maneuver_opportunity_count >= 2",
            "junction_id A != junction_id B",
            "branch_id A != branch_id B",
            "target_id A != target_id B",
            "both targets available and route reachable",
        ],
        "runtime_label_join_allowed": False,
        "actor_transform_truth_allowed": False,
        "scenario_identity_to_decision_mapping_allowed": False,
    }
    opportunity_contract = {
        "schema_version": SCHEMA_PREFIX + ".maneuver_opportunity_contract.v1",
        "status": "CLOSED_PREFREEZE_CONTRACT",
        "required_fields": [
            "junction_id",
            "branch_id",
            "target_id",
            "maneuver_direction",
            "route_order_index",
            "route_opportunity_index",
            "anchor_xy",
            "distance_or_progress",
            "availability",
            "route_reachable",
            "source_provenance",
        ],
        "directions_in_scope": ["RIGHT", "CONTINUE"],
        "source": "online route deque plus live CARLA HD map topology",
        "maximum_candidate_targets": 2,
    }
    wait_contract = {
        "schema_version": SCHEMA_PREFIX + ".wait_actor_independence_contract.v1",
        "status": "CLOSED_PREFREEZE_CONTRACT",
        "actor_motion_policy_independent": True,
        "motion_source": "ScenarioRunner ActorTransformSetter + Idle + KeepVelocity + DriveDistance",
        "start_condition": "scenario trigger at route start; no ego hold-point crossing",
        "depends_on_ego_wait": False,
        "depends_on_policy_decision": False,
        "cleared_evidence": "tracked actor leaves image event region for three consecutive active-track frames",
        "actor_destruction_as_cleared_evidence": False,
        "formal_wait_fixture_count": 3,
    }
    visualization_contract = {
        "schema_version": SCHEMA_PREFIX + ".visualization_contract_v2",
        "status": "CLOSED_HARD_GATE",
        "native_physical_display_required": True,
        "left_window": "DriveClarify Grounded V1 Runtime",
        "right_window": "CarlaUE4",
        "forbidden_modes": ["headless", "Xvfb", "VNC", "RenderOffScreen"],
        "capture_readiness": [
            "dashboard visible bounds valid",
            "CarlaUE4 visible bounds valid",
            "dashboard refresh count > 0",
            "RGB valid frame count > 0",
            "runtime tick > 0",
            "decision populated",
            "non-white screenshot content",
        ],
        "passive_side_effect_counts_required_zero": [
            "Grounding DINO forward",
            "SimLingo forward",
            "ByteTrack update",
            "planner advance",
            "PID invocation",
            "VehicleControl mutation",
        ],
    }
    _json(report_root / "TOPOLOGY_TARGET_BINDING_CONTRACT.json", topology_contract)
    _json(report_root / "MANEUVER_OPPORTUNITY_CONTRACT.json", opportunity_contract)
    _json(report_root / "WAIT_ACTOR_INDEPENDENCE_CONTRACT.json", wait_contract)
    _json(report_root / "VISUALIZATION_CONTRACT_V2.json", visualization_contract)
    _json(
        report_root / "ASK_MULTI_TARGET_FIXTURE_AUDIT.json",
        {
            "schema_version": SCHEMA_PREFIX + ".ask_multi_target_fixture_audit.v1",
            "status": "PENDING_NATIVE_E0",
            "fixture_count": 3,
            "required_geometry": "two visually ordered referents plus first-turn/second-turn legal branches",
            "records": [],
        },
    )
    _json(
        report_root / "WAIT_DEADLOCK_FIXTURE_AUDIT.json",
        {
            "schema_version": SCHEMA_PREFIX + ".wait_deadlock_fixture_audit.v1",
            "status": "PENDING_NATIVE_E0",
            "fixture_count": 3,
            "actor_motion_policy_independent": True,
            "records": [],
        },
    )
    _text(
        report_root / "E1R1_REVISION_PROTOCOL.md",
        """# Grounded Language V1 Extension E1-R1 revision protocol

This append-only TRAIN revision fixes the two causal defects recorded by the prior E1: referential candidates now bind to distinct online route/map topology targets, and WAIT fixtures give the environment actor scenario-owned motion independent of the ego hold.

The execution gate is E0 → E1 (18 six-method smoke episodes) → E2 (ACT×3, ASK×3, WAIT×3). E3 and the 216-episode TRAIN expansion are out of scope and must not run. DEV and TEST payloads remain unopened and unconsumed.

The new population contains 24 language-scene identities and nine physical fixtures (three per mechanism). Rephrasing does not count as physical diversity. Every physical fixture must pass native CARLA instantiate/tick/actor-lifecycle validation before E0 freezes.

ASK requires image-only effective K≥2, two live-map legal route-ordered junction/branch targets, A×3/B×3 SimLingo planning, natural ASK, answer consumption, stale-candidate invalidation, fresh replan, and ACT. WAIT requires observed bus motion, no premature CLEARED, information arrival, fresh replan, and ACT. ACT requires a non-empty executable continuation target and natural ACT.

Visualization is a hard gate on the physical Ubuntu display: the DriveClarify dashboard and CarlaUE4 must coexist, the screenshot must be captured only after valid RGB/runtime/dashboard refresh, and a white/blank compositor frame is invalid.
""",
    )
    return {
        "status": "PREFREEZE_MATERIALIZED_PENDING_NATIVE_E0",
        "physical_fixture_count": len(physical_rows),
        "language_scene_identity_count": len(language_rows),
        "methods": list(METHODS),
    }


if __name__ == "__main__":
    print(json.dumps(materialize(), ensure_ascii=False, indent=2, sort_keys=True))

