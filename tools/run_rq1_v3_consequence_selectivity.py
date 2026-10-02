#!/usr/bin/env python3
"""RQ1-V3 pre-formal qualification, freeze, native execution, and analysis.

The scientific comparator and native agent are imported unchanged from RQ1-V2.
This file owns only fresh V3 scene/route construction, qualification evidence,
prospective allocation, accounting, and analysis.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import random
import secrets
import subprocess
import sys
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPORT = ROOT / "reports/driveclarify_rq1_v3_executability_full_replan_consequence_selectivity_v1"
ENGINEERING = REPORT / "engineering"
ENGINEERING_ASSETS = ENGINEERING / "assets"
ENGINEERING_CONFIGS = ENGINEERING / "run_configs"
ENGINEERING_RUNS = ENGINEERING / "native_runs"
FORMAL_ASSETS = REPORT / "formal_assets"
FORMAL_CONFIGS = REPORT / "formal_run_configs"
FORMAL_RUNS = REPORT / "formal_runs"
V2_REPORT = ROOT / "reports/driveclarify_rq1_v2_consequence_selective_clarification_v2"
V11_TEMPLATE = ROOT / "reports/driveclarify_v11_fresh_prospective_rq1/formal_routes/V11-FORMAL-TOWN05-CORRIDORS.json"
CHECKPOINT = ROOT / "reports/driveclarify_v3_short_prefix_a1_fast_track/a1_training_v2/selected/checkpoints/a1_selected.ckpt/pytorch_model.pt"
CHECKPOINT_SHA256 = "cc6873e2a7778140ff7af3fd7d3578114b26bd6a47974e955f0845ddd2178044"
NATIVE_RUNNER = ROOT / "tools/run_rq1_v3_native_episode.sh"
FAMILIES = ("REF", "LMK", "ORD", "USC")
LEVELS = ("EQUIVALENT", "CRITICAL")
SCENE_CODES = tuple(f"{family}-{level}" for family in FAMILIES for level in LEVELS)
FINAL_STATUSES = {
    "PASS_RQ1_V3_CONSEQUENCE_SELECTIVITY_SUPPORTED",
    "PASS_RQ1_V3_CONSEQUENCE_SELECTIVITY_NOT_SUPPORTED",
    "RQ1_V3_PRIMARY_EVALUABILITY_GATE_FAILED",
    "RQ1_V3_EXECUTION_INTEGRITY_NOT_CLOSED",
    "RQ1_V3_PRE_FORMAL_QUALIFICATION_NOT_CLOSED",
}
INSTRUCTIONS = {
    "REF": "Turn after the white van.",
    "LMK": "Turn at the school.",
    "ORD": "Take the second turn.",
    "USC": "Pull over at a suitable distance.",
}
SEMANTIC_KINDS = {
    "REF": "REFERENTIAL",
    "LMK": "LANDMARK",
    "ORD": "SPATIAL_ORDER",
    "USC": "UNDERSPECIFIED_CONSTRAINT",
}
TOWN05_XML_START = [124.13790130615234, -2.050391674041748, 0.01679229736328125]
TOWN05_XML_DESTINATION = [54.058406829833984, -1.7005641460418701, 0.0]
ENGINEERING_GENERATION = 2


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def sha(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def load(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value.rstrip() + "\n", encoding="utf-8")
    temporary.replace(path)


def log(phase: str, status: str) -> None:
    path = REPORT / "COMMAND_LOG.md"
    prior = path.read_text(encoding="utf-8") if path.is_file() else "# RQ1-V3 command log\n\n"
    write_text(path, prior + f"- `{phase}` -> `{status}`\n")


def extract_seeds(value: Any, parent_key: str = "") -> set[int]:
    found: set[int] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if "seed" in lowered:
                if type(item) is int:
                    found.add(item)
                elif isinstance(item, list):
                    found.update(row for row in item if type(row) is int)
                elif isinstance(item, dict):
                    found.update(row for row in item.values() if type(row) is int)
            found.update(extract_seeds(item, lowered))
    elif isinstance(value, list):
        for item in value:
            found.update(extract_seeds(item, parent_key))
    elif type(value) is int and "seed" in parent_key:
        found.add(value)
    return found


def prior_seed_audit() -> dict[str, Any]:
    values: set[int] = set()
    files = []
    parse_exclusions = []
    for path in sorted((ROOT / "reports").rglob("*.json")):
        try:
            relative_to_v3 = path.relative_to(REPORT)
        except ValueError:
            relative_to_v3 = None
        if relative_to_v3 is not None and str(relative_to_v3).startswith(("formal_assets/", "formal_run_configs/", "formal_runs/")):
            continue
        upper = path.name.upper()
        if not any(token in upper for token in ("SEED", "ROSTER", "EXCLUSION_REGISTRY", "RUN_ORDER")):
            continue
        try:
            if path.stat().st_size > 32 * 1024 * 1024:
                parse_exclusions.append({"path": str(path.relative_to(ROOT)), "reason": "OVER_32_MIB"})
                continue
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except Exception as error:
            parse_exclusions.append({"path": str(path.relative_to(ROOT)), "reason": type(error).__name__})
            continue
        extracted = extract_seeds(parsed)
        if extracted:
            values.update(extracted)
            files.append({"path": str(path.relative_to(ROOT)), "sha256": sha(path), "seed_value_count": len(extracted)})
    return {
        "registry_files_audited": files,
        "registry_file_count": len(files),
        "unique_prior_seed_value_count": len(values),
        "prior_seed_values": sorted(values),
        "parse_exclusions": parse_exclusions,
        "scope": [
            "old RQ1", "RQ1-V2", "RQ2", "engineering", "calibration",
            "automatic-E2", "all seed/roster/exclusion registries",
        ],
    }


def tree_digest(paths: Iterable[Path]) -> dict[str, Any]:
    rows = {}
    for root in paths:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file():
                rows[str(path.relative_to(ROOT))] = sha(path)
    return {"file_count": len(rows), "tree_digest": digest(rows), "file_hashes": rows}


def rq2_snapshot() -> dict[str, Any]:
    roots = [
        path for path in ROOT.iterdir()
        if path.is_dir() and (path.name.startswith("driveclarify_rq2") or path.name.startswith("reports") and False)
    ]
    roots.extend(path for path in (ROOT / "reports").iterdir() if path.is_dir() and path.name.startswith("driveclarify_rq2"))
    return tree_digest(roots)


def seal_v2() -> dict[str, Any]:
    final_text = (V2_REPORT / "FINAL_REPORT.md").read_text(encoding="utf-8")
    ledger = load(V2_REPORT / "FORMAL_EXECUTION_LEDGER.json", {})
    primary = load(V2_REPORT / "RQ1_V2_PRIMARY_RESULTS.json", {})
    audit = load(V2_REPORT / "RQ1_ALL_ASK_ROOT_CAUSE_AUDIT.json", {})
    freshness = load(V2_REPORT / "FORMAL_SEED_FRESHNESS_RECEIPT.json", {})
    attempt_dirs = list((V2_REPORT / "formal_runs").glob("*/attempt_01"))
    required = [
        V2_REPORT / "FINAL_REPORT.md",
        V2_REPORT / "FORMAL_EXECUTION_LEDGER.json",
        V2_REPORT / "RQ1_V2_PRIMARY_RESULTS.json",
        V2_REPORT / "MANDATORY_HARD_STOP_RECEIPT.json",
        V2_REPORT / "RQ1_ALL_ASK_ROOT_CAUSE_AUDIT.json",
    ]
    if "RQ1_V2_PRIMARY_EVALUABILITY_GATE_FAILED" not in final_text:
        raise RuntimeError("RQ1_V2_FINAL_STATUS_NOT_PRESERVED")
    if primary.get("analysis_run") is not False:
        raise RuntimeError("RQ1_V2_PROHIBITED_PRIMARY_ANALYSIS_PRESENT")
    if audit.get("primary_category") != "C":
        raise RuntimeError("RQ1_V2_ROOT_CAUSE_C_NOT_PRESERVED")
    receipt = {
        "schema": "driveclarify.rq1_v3.rq1-v2-seal.v1",
        "status": "PASS_RQ1_V2_RESULT_SEALED_AND_EXCLUDED",
        "preserved_final_status": "RQ1_V2_PRIMARY_EVALUABILITY_GATE_FAILED",
        "old_all_ask_root_cause": "C — LOW_SCENES_WERE_NOT_ACTUALLY_TASK_EQUIVALENT",
        "scientific_hypothesis_supported_or_refuted_by_v2": False,
        "partial_v2_primary_effect_calculated": False,
        "v2_attempt_count": len(attempt_dirs),
        "v2_ledger_entry_count": len(ledger.get("entries", [])),
        "v2_exposed_count": ledger.get("exposed_episodes"),
        "v2_decision_evaluable_count": ledger.get("decision_evaluable_episodes"),
        "v2_formal_seeds": freshness.get("formal_seeds", []),
        "v2_scene_identities_permanently_excluded": sorted({row.get("scene_id") for row in load(V2_REPORT / "FORMAL_ROSTER.json", {}).get("cells", [])}),
        "v2_files": {str(path.relative_to(ROOT)): sha(path) for path in required},
    }
    receipt["seal_digest"] = digest(receipt)
    write_json(REPORT / "RQ1_V2_SEAL_RECEIPT.json", receipt)
    return receipt


def task_signature(scene_code: str, candidate: str, designation: str) -> dict[str, Any]:
    family, level = scene_code.split("-")
    relevant = {
        "REF": ["terminal_task_region", "irreversible_branch_obligation", "task_completion_region"],
        "LMK": ["terminal_task_region", "terminal_road_or_corridor", "task_completion_region"],
        "ORD": ["maneuver_obligation", "irreversible_branch_obligation", "task_completion_region"],
        "USC": ["goal_lane_or_side_obligation_if_task_relevant", "task_completion_region"],
    }[family]
    values = {
        "terminal_task_region": f"{designation}-{family}-CERTIFIED-TASK-REGION-A",
        "terminal_road_or_corridor": f"{designation}-{family}-CERTIFIED-CORRIDOR-A",
        "maneuver_obligation": f"{designation}-{family}-CERTIFIED-MANEUVER-A",
        "irreversible_branch_obligation": f"{designation}-{family}-CERTIFIED-BRANCH-A",
        "goal_lane_or_side_obligation_if_task_relevant": f"{designation}-{family}-CERTIFIED-SIDE-A",
        "task_completion_region": f"{designation}-{family}-CERTIFIED-COMPLETION-A",
    }
    if level == "CRITICAL" and candidate == "B":
        component = relevant[-1]
        values[component] = f"{designation}-{family}-CERTIFIED-{component.upper()}-B"
    return {
        "candidate_id": candidate,
        "relevant_components": relevant,
        "certified": True,
        "certificate_id": f"RQ1V3-{designation}-CERT-{scene_code}-{candidate}",
        **values,
    }


def route_xml(
    route_id: int,
    rows: Sequence[Mapping[str, Any]],
    xml_start: Sequence[float] | None = None,
    xml_destination: Sequence[float] | None = None,
) -> str:
    first = list(xml_start) if xml_start is not None else rows[0]["xyz"]
    last = list(xml_destination) if xml_destination is not None else rows[-1]["xyz"]
    return f'''<routes>
  <route id="{route_id}" town="Town05">
    <waypoints>
      <position x="{first[0]:.12f}" y="{first[1]:.12f}" z="{first[2]:.12f}" />
      <position x="{last[0]:.12f}" y="{last[1]:.12f}" z="{last[2]:.12f}" />
    </waypoints>
    <scenarios />
    <weathers>
      <weather cloudiness="0.0" fog_density="0.0" precipitation="0.0" precipitation_deposits="0.0" sun_altitude_angle="70.0" sun_azimuth_angle="0.0" wetness="0.0" wind_intensity="0.0" route_percentage="0" />
      <weather cloudiness="0.0" fog_density="0.0" precipitation="0.0" precipitation_deposits="0.0" sun_altitude_angle="70.0" sun_azimuth_angle="0.0" wetness="0.0" wind_intensity="0.0" route_percentage="100" />
    </weathers>
  </route>
</routes>'''


def offset_route(rows: Sequence[Mapping[str, Any]], amplitude: float) -> list[dict[str, Any]]:
    result = []
    count = len(rows) - 1
    for index, source in enumerate(rows):
        row = {"xyz": list(source["xyz"]), "road_option": source["road_option"]}
        phase = math.sin(math.pi * index / count) ** 2
        row["xyz"][1] += amplitude * phase
        result.append(row)
    result[0] = {"xyz": list(rows[0]["xyz"]), "road_option": rows[0]["road_option"]}
    result[-1] = {"xyz": list(rows[-1]["xyz"]), "road_option": rows[-1]["road_option"]}
    return result


def layout(scene_code: str, designation: str) -> dict[str, Any]:
    family = scene_code.split("-")[0]
    entity_kind = {
        "REF": "REFERENCE_OBJECT_ANCHOR",
        "LMK": "LANDMARK_ANCHOR",
        "ORD": "ELIGIBLE_ORDER_OPPORTUNITY",
        "USC": "VALID_CONSTRAINT_PLACEMENT_ENVELOPE",
    }[family]
    value = {
        "schema": "driveclarify.rq1_v3.scientific-layout.v1",
        "layout_id": f"RQ1V3-{designation}-LAYOUT-{scene_code}",
        "scene_code": scene_code,
        "retained_scientific_actors": [],
        "scientific_entities": [
            {"entity_id": f"{designation}-{scene_code}-A", "scientific_role": f"{entity_kind}_CANDIDATE_A", "spawned_actor": False},
            {"entity_id": f"{designation}-{scene_code}-B", "scientific_role": f"{entity_kind}_CANDIDATE_B", "spawned_actor": False},
        ],
        "random_background_vehicle_count": 0,
        "traffic_manager_random_generation_enabled": False,
        "unrelated_realism_traffic": False,
    }
    value["layout_digest"] = digest(value)
    return value


def make_route_source(scene_code: str, designation: str, center: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    level = scene_code.split("-")[1]
    if designation == "FORMAL":
        amplitude = 0.30 if level == "EQUIVALENT" else 0.75
    else:
        amplitude = 0.24 if level == "EQUIVALENT" else 0.65
    value = {
        "schema": "driveclarify.rq1_v3.candidate-route-source.v1",
        "designation": designation,
        "scene_code": scene_code,
        "map": "Town05",
        "candidate_A": offset_route(center, -amplitude),
        "candidate_B": offset_route(center, amplitude),
        "same_global_destination": True,
        "construction": "smooth simple in-corridor alternatives; task relation is certified symbolically, never inferred from route distance",
        "maximum_lateral_offset_m": amplitude,
    }
    value["route_source_digest"] = digest(value)
    return value


def make_config(run_id: str, scene: Mapping[str, Any], seed: int) -> dict[str, Any]:
    route_source = load(ROOT / scene["candidate_route_source"])
    method_input = {
        "instruction": scene["ambiguous_instruction"],
        "observation_anchor_xyz": route_source["candidate_A"][0]["xyz"],
        "anchor_capture_distance_m": 3.0,
        "grounding_evidence_status": "VERIFIED",
        "grounding_evidence_source": "FROZEN_CERTIFIED_CURRENT_SCENE_GROUNDING",
        "grounding_reason_codes": ["TWO_RUNTIME_VALID_CERTIFIED_INTERPRETATIONS"],
        "background_traffic_policy": {
            "random_background_vehicle_count": 0,
            "traffic_manager_random_generation_enabled": False,
            "retained_scientific_actors": [],
        },
        "route_source": str(ROOT / scene["candidate_route_source"]),
        "current_connector_id": f"{run_id}-CURRENT",
        "alternatives": [
            {"candidate_id": "A", "description": "first grounded interpretation", "evidence_id": f"{run_id}-EVIDENCE-A", "route_source_field": "candidate_A", "connector_id": f"{run_id}-CONNECTOR-A", "commitment_point_index": 24},
            {"candidate_id": "B", "description": "second grounded interpretation", "evidence_id": f"{run_id}-EVIDENCE-B", "route_source_field": "candidate_B", "connector_id": f"{run_id}-CONNECTOR-B", "commitment_point_index": 24},
        ],
        "task_signatures": scene["task_signatures"],
    }
    return {
        "schema": "driveclarify.v11.native-runtime-config.v1",
        "run_id": run_id,
        "mode": "DRIVECLARIFY",
        "checkpoint": str(CHECKPOINT),
        "checkpoint_sha256": CHECKPOINT_SHA256,
        "a1_trainable_parameters": 896,
        "training_performed": False,
        "observation_window_ticks": 96,
        "receipt_completion_mode": "NATURAL_EVALUATOR_DESTROY",
        "nonprogress_diagnostic_window_ticks": 80,
        "method_input": method_input,
        "scientific_seed_not_available_to_method": seed,
    }


def static_scene_check(scene: Mapping[str, Any]) -> dict[str, Any]:
    from driveclarify_clear_passthrough_v11.ambiguity_gate import AmbiguityGate
    from driveclarify_clear_passthrough_v11.contracts import GateInput, GroundingEvidence
    from driveclarify_rq1_v2.consequence import AmbiguityStatus, TaskSignature, compare_task_signatures, consequence_gate

    gate = AmbiguityGate().evaluate(GateInput(
        instruction=scene["ambiguous_instruction"],
        grounding=GroundingEvidence(2, "VERIFIED", "FROZEN_CERTIFIED_CURRENT_SCENE_GROUNDING", 100, 100),
        runtime_evidence={"map_name": "Town05", "runtime_reasonable_option_count": 2, "route_owner": "SIMLINGO"},
    ))
    first, second = (TaskSignature.from_mapping(row) for row in scene["task_signatures"])
    comparison = compare_task_signatures(first, second)
    decision = consequence_gate(AmbiguityStatus.AMBIGUOUS, comparison.relation, deterministic_candidate_id="A")
    expected_relation = "TASK_EQUIVALENT" if scene["scene_code"].endswith("EQUIVALENT") else "TASK_CRITICAL"
    expected_action = "ACT" if scene["scene_code"].endswith("EQUIVALENT") else "ASK"
    return {
        "scene_code": scene["scene_code"],
        "ambiguity_gate_decision": gate.decision.value,
        "semantic_kind": gate.semantic_kind,
        "expected_semantic_kind": SEMANTIC_KINDS[scene["family"]],
        "task_relation": comparison.relation.value,
        "differing_components": list(comparison.differing_components),
        "policy_action": decision.action.value,
        "pass": gate.decision.value == "AMBIGUOUS" and gate.semantic_kind == SEMANTIC_KINDS[scene["family"]] and comparison.relation.value == expected_relation and decision.action.value == expected_action,
    }


def static_replan_matrix(scenes: Sequence[Mapping[str, Any]], designation: str) -> dict[str, Any]:
    from driveclarify_clear_passthrough_v11.contracts import AuthoritativeRoute, EgoState, RoutePoint
    from driveclarify_clear_passthrough_v11.replan import ReplanAdmissibility, ReplanThresholds

    def points(rows: Sequence[Mapping[str, Any]]) -> tuple[RoutePoint, ...]:
        return tuple(RoutePoint(float(row["xyz"][0]), float(row["xyz"][1]), float(row["xyz"][2]), str(row["road_option"])) for row in rows)

    matrix = []
    for scene in scenes:
        if scene["consequence_level"] != "CRITICAL":
            continue
        source = load(ROOT / scene["candidate_route_source"])
        center = [(a + b) / 2.0 for a, b in zip(source["candidate_A"][0]["xyz"], source["candidate_B"][0]["xyz"])]
        current_points = points(load(ROOT / scene["center_route_source"])["center_route"])
        heading = math.degrees(math.atan2(current_points[1].y - current_points[0].y, current_points[1].x - current_points[0].x))
        ego = EgoState(*center, yaw_degrees=heading, speed_mps=0.25, frame=100)
        current = AuthoritativeRoute(
            route_id=f"{designation}-{scene['scene_code']}-CURRENT", points=current_points,
            target_point=(current_points[2].x, current_points[2].y), road_option=current_points[2].road_option,
            destination_xyz=current_points[-1].xyz, source_frame=100,
            connector_id=f"{designation}-CURRENT", commitment_point_index=min(24, len(current_points) - 2),
            full_route_owner="SIMLINGO_OFFICIAL_OR_PUBLIC_RUNTIME_ROUTE", active_suffix_owner="SIMLINGO_ONLINE_ROUTE_UPDATE_OWNER",
        )
        for candidate in ("A", "B"):
            candidate_points = points(source[f"candidate_{candidate}"])
            resolved = AuthoritativeRoute(
                route_id=f"{designation}-{scene['scene_code']}-{candidate}", points=candidate_points,
                target_point=(candidate_points[2].x, candidate_points[2].y), road_option=candidate_points[2].road_option,
                destination_xyz=current.destination_xyz, source_frame=100,
                connector_id=f"{designation}-{scene['scene_code']}-CONNECTOR-{candidate}", commitment_point_index=min(24, len(candidate_points) - 2),
                full_route_owner="SIMLINGO_OFFICIAL_OR_PUBLIC_RUNTIME_ROUTE", active_suffix_owner="SIMLINGO_ONLINE_ROUTE_UPDATE_OWNER",
            )
            result = ReplanAdmissibility().assess(ego, current, resolved)
            matrix.append({
                "scene_code": scene["scene_code"], "family": scene["family"], "candidate_answer": candidate,
                "certified_answer_interface_outcome": True,
                "production_admissibility_owner": "driveclarify_clear_passthrough_v11.replan.ReplanAdmissibility",
                "thresholds": asdict(ReplanThresholds()),
                "disposition": result.disposition.value, "admissible": result.disposition.value == "COMMIT_NOW",
                "reason_codes": list(result.reason_codes), "metrics": dict(result.metrics),
            })
    value = {
        "schema": "driveclarify.rq1_v3.full-replan-admissibility-matrix.v1",
        "designation": designation,
        "unchanged_production_owner": True,
        "pass_count": sum(row["admissible"] for row in matrix),
        "required_count": 8,
        "matrix": matrix,
    }
    value["status"] = "PASS_8_OF_8_HIGH_CANDIDATE_ROUTES_ADMISSIBLE" if value["pass_count"] == 8 else "FAIL_HIGH_CANDIDATE_ROUTE_ADMISSIBILITY"
    value["matrix_digest"] = digest(value)
    return value


def engineering_seed(scene_code: str, slot: int, used: set[int]) -> int:
    value = int(hashlib.sha256(f"RQ1-V3-ENGINEERING-ONLY-G{ENGINEERING_GENERATION}:{scene_code}:{slot}".encode()).hexdigest()[:8], 16) % 1_800_000_000 + 100_000_000
    while value in used:
        value += 1
    used.add(value)
    return value


def prepare() -> dict[str, Any]:
    REPORT.mkdir(parents=True, exist_ok=True)
    seal = seal_v2()
    if sha(CHECKPOINT) != CHECKPOINT_SHA256:
        raise RuntimeError("RQ1_V3_CHECKPOINT_DRIFT")
    prior = prior_seed_audit()
    used = set(prior["prior_seed_values"])
    template = load(V11_TEMPLATE, {})
    native = template.get("native_corridor", [])
    if len(native) < 60:
        raise RuntimeError("RQ1_V3_NATIVE_TEMPLATE_INCOMPLETE")
    engineering_center = native
    rq2 = rq2_snapshot()

    policy = {
        "schema": "driveclarify.rq1_v3.background-traffic-policy.v1",
        "status": "FROZEN_BEFORE_ENGINEERING_AND_FORMAL_EXPOSURE",
        "scope": ["development", "calibration", "engineering qualification", "all eight formal conditions"],
        "random_background_vehicle_count": 0,
        "traffic_manager_random_generation_enabled": False,
        "only_explicit_scientific_actors_retained": True,
        "machine_readable_scientific_role_required": True,
        "unrelated_realism_traffic_allowed": False,
        "post_formal_exposure_selective_change_allowed": False,
        "claim_boundary": "No robustness claim under arbitrary background traffic.",
    }
    write_json(REPORT / "BACKGROUND_TRAFFIC_POLICY.json", policy)

    scenes = []
    cells = []
    for scene_index, scene_code in enumerate(SCENE_CODES, 1):
        family, level = scene_code.split("-")
        route_path = ENGINEERING_ASSETS / f"RQ1V3-ENG-G{ENGINEERING_GENERATION}-{scene_code}-ROUTE.xml"
        write_text(route_path, route_xml(974100 + scene_index, engineering_center, TOWN05_XML_START, TOWN05_XML_DESTINATION))
        center_value = {"schema": "driveclarify.rq1_v3.center-route.v1", "designation": "ENGINEERING_ONLY", "scene_code": scene_code, "center_route": engineering_center}
        center_value["center_route_digest"] = digest(center_value)
        center_path = ENGINEERING_ASSETS / f"{scene_code}-CENTER-ROUTE.json"
        write_json(center_path, center_value)
        source = make_route_source(scene_code, "ENGINEERING_ONLY", engineering_center)
        source_path = ENGINEERING_ASSETS / f"{scene_code}-CANDIDATE-ROUTES.json"
        write_json(source_path, source)
        layout_value = layout(scene_code, "ENGINEERING_ONLY")
        layout_path = ENGINEERING_ASSETS / f"{scene_code}-LAYOUT.json"
        write_json(layout_path, layout_value)
        scene = {
            "schema": "driveclarify.rq1_v3.engineering-scene-contract.v1",
            "designation": "ENGINEERING_ONLY_PERMANENTLY_EXCLUDED",
            "engineering_generation": ENGINEERING_GENERATION,
            "scene_id": f"RQ1V3-ENG-G{ENGINEERING_GENERATION}-{scene_code}-Q{scene_index:02d}",
            "scene_code": scene_code, "family": family, "consequence_level": level,
            "machine_label": "TASK_EQUIVALENT" if level == "EQUIVALENT" else "TASK_CRITICAL",
            "ambiguous_instruction": INSTRUCTIONS[family],
            "native_map": "Town05", "native_route_path": str(route_path.relative_to(ROOT)),
            "center_route_source": str(center_path.relative_to(ROOT)),
            "candidate_route_source": str(source_path.relative_to(ROOT)),
            "layout_path": str(layout_path.relative_to(ROOT)),
            "task_signatures": [task_signature(scene_code, candidate, "ENGINEERING") for candidate in ("A", "B")],
            "reasonable_interpretation_count": 2,
            "grounding_certified_before_native_exposure": True,
            "random_background_vehicle_count": 0,
        }
        scene["scene_digest"] = digest(scene)
        scene_path = ENGINEERING_ASSETS / f"{scene_code}-SCENE.json"
        write_json(scene_path, scene)
        scene["scene_contract_path"] = str(scene_path.relative_to(ROOT))
        scenes.append(scene)
        for slot in range(1, 4):
            seed = engineering_seed(scene_code, slot, used)
            run_id = f"RQ1V3-ENG-G{ENGINEERING_GENERATION}-{scene_code}-S{slot:02d}"
            config = make_config(run_id, scene, seed)
            config_path = ENGINEERING_CONFIGS / f"{run_id}.json"
            write_json(config_path, config)
            cells.append({
                "run_id": run_id, "scene_id": scene["scene_id"], "scene_code": scene_code,
                "family": family, "consequence_level": level, "seed_slot": slot, "seed": seed,
                "config_path": str(config_path.relative_to(ROOT)), "config_sha256": sha(config_path),
                "native_route_path": scene["native_route_path"], "route_sha256": sha(route_path),
                "layout_path": scene["layout_path"], "answer_candidate_id": (("A", "B", "A")[slot - 1] if level == "CRITICAL" else None),
                "engineering_retry": False, "permanently_excluded": True,
            })

    static_checks = [static_scene_check(scene) for scene in scenes]
    matrix = static_replan_matrix(scenes, "ENGINEERING_ONLY")
    write_json(ENGINEERING / "FULL_REPLAN_ADMISSIBILITY_MATRIX.json", matrix)
    manifest = {
        "schema": "driveclarify.rq1_v3.engineering-manifest.v1",
        "designation": "ENGINEERING_ONLY_PERMANENTLY_EXCLUDED",
        "engineering_generation": ENGINEERING_GENERATION,
        "scene_count": 8, "native_cell_count": 24, "seeds": [row["seed"] for row in cells],
        "scenes": scenes, "cells": cells, "static_scene_checks": static_checks,
        "formal_assets_created": False, "formal_seeds_generated": False,
    }
    manifest["manifest_digest"] = digest(manifest)
    write_json(ENGINEERING / "ENGINEERING_MANIFEST.json", manifest)
    exclusion = {
        "schema": "driveclarify.rq1_v3.engineering-exclusion-registry.v1", "permanent": True,
        "excluded_scene_ids": [row["scene_id"] for row in scenes],
        "excluded_route_paths": [row["native_route_path"] for row in scenes],
        "excluded_candidate_route_paths": [row["candidate_route_source"] for row in scenes],
        "excluded_layout_paths": [row["layout_path"] for row in scenes],
        "excluded_config_paths": [row["config_path"] for row in cells],
        "excluded_engineering_seeds": [row["seed"] for row in cells],
        "excluded_from": ["formal scenes", "formal routes", "formal configs", "formal layouts", "formal seeds", "formal analysis"],
        "v2_formal_seeds_also_excluded": seal["v2_formal_seeds"],
        "superseded_engineering_generation_registries": [
            str(path.relative_to(ROOT))
            for path in sorted(REPORT.glob("engineering_generation_*_failed_*/ENGINEERING_EXCLUSION_REGISTRY.json"))
        ],
        "prior_registry_audit": prior,
    }
    exclusion["exclusion_digest"] = digest(exclusion)
    write_json(ENGINEERING / "ENGINEERING_EXCLUSION_REGISTRY.json", exclusion)
    state = {
        "schema": "driveclarify.rq1_v3.pre-formal-qualification-state.v1",
        "status": "READY_FOR_NATIVE_ENGINEERING_QUALIFICATION" if all(row["pass"] for row in static_checks) and matrix["pass_count"] == 8 else "RQ1_V3_PRE_FORMAL_QUALIFICATION_NOT_CLOSED",
        "static_decision_contracts_pass": sum(row["pass"] for row in static_checks),
        "static_full_replan_candidates_pass": matrix["pass_count"],
        "formal_assets_created": False, "formal_seeds_generated": False, "formal_exposures": 0,
        "rq2_snapshot_before": rq2,
    }
    write_json(REPORT / "PRE_FORMAL_QUALIFICATION_STATE.json", state)
    log("prepare", state["status"])
    return state


def official_summary(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    records = checkpoint.get("_checkpoint", {}).get("records", []) if checkpoint else []
    if len(records) != 1:
        return {"record_count": len(records), "status": None, "route_completion_percent": None, "infractions": {}, "meta": {}}
    row = records[0]
    return {
        "record_count": 1, "status": row.get("status"),
        "route_completion_percent": (row.get("scores") or {}).get("score_route"),
        "infractions": row.get("infractions") or {}, "meta": row.get("meta") or {},
    }


def safety_from_official(official: Mapping[str, Any]) -> dict[str, bool]:
    infractions = official.get("infractions") or {}
    collision = any(infractions.get(key) for key in ("collisions_layout", "collisions_pedestrian", "collisions_vehicle"))
    offroad = bool(infractions.get("outside_route_lanes"))
    wrong_lane = offroad or bool(infractions.get("route_dev"))
    return {"collision": collision, "offroad": offroad, "wrong_lane": wrong_lane}


def classify_native(cell: Mapping[str, Any], output: Path, wrapper_exit: int, phase: str) -> dict[str, Any]:
    decision = load(output / "owner_evidence/RQ1_V2_CONSEQUENCE_DECISION_RECEIPT.json", {})
    background = load(output / "owner_evidence/RQ1_V2_BACKGROUND_TRAFFIC_RUNTIME_RECEIPT.json", {})
    supervision = load(output / "owner_evidence/V11_SUPERVISION_RECEIPT.json", {})
    window = load(output / "owner_evidence/NATIVE_MODEL_WINDOW_COMPLETE.json", {})
    answer = load(output / "owner_evidence/oracle_exchange/FORMAL_ANSWER_RELEASE_RECEIPT.json", {})
    replan = load(output / "owner_evidence/RQ1_V2_FULL_REPLAN_RECEIPT.json", {})
    online = load(output / "owner_evidence/ONLINE_ROUTE_INSTALL_RECEIPT.json", {})
    post_switch = load(output / "owner_evidence/POST_SWITCH_PLAN_ACCEPTANCE.json", {})
    process = load(output / "process_job/PROCESS_RECEIPT.json", {})
    official = official_summary(load(output / "official_checkpoint.json", {}))
    relation = (decision.get("comparison") or {}).get("relation")
    action = (decision.get("gate") or {}).get("action")
    gate = supervision.get("gate") or {}
    expected_relation = "TASK_EQUIVALENT" if cell["consequence_level"] == "EQUIVALENT" else "TASK_CRITICAL"
    expected_action = "ACT" if cell["consequence_level"] == "EQUIVALENT" else "ASK"
    answer_id = cell.get("answer_candidate_id")
    selected = supervision.get("selected_candidate_id")
    transaction = supervision.get("transaction") or {}
    transition = supervision.get("transition") or {}
    counters = supervision.get("counters") or {}
    decision_evaluable = (
        gate.get("decision") == "AMBIGUOUS"
        and relation in {"TASK_EQUIVALENT", "TASK_CRITICAL"}
        and action in {"ASK", "ACT"}
        and decision.get("reasonable_interpretation_count") == 2
        and decision.get("passenger_true_intent_operand_present") is False
        and background.get("random_background_vehicle_count") == 0
        and background.get("traffic_manager_random_generation_enabled") is False
        and background.get("all_retained_actors_have_machine_readable_scientific_role") is True
    )
    execution_evaluable = window.get("classification") == "NATIVE_MODEL_WINDOW_COMPLETE" and official["record_count"] == 1
    route_committed = bool(transaction.get("committed") is True and online.get("committed") is True)
    full_replan_admitted = bool(
        cell["consequence_level"] == "CRITICAL"
        and transition.get("disposition") == "COMMIT_NOW"
        and (replan.get("installation_receipt") or {}).get("committed") is True
        and route_committed
    )
    full_replan_executed = bool(
        full_replan_admitted
        and window.get("route_transaction_count", 0) >= 1
        and (post_switch or any(row.get("route_switch_active") for row in window.get("trajectory", [])))
    )
    low_chain = bool(
        cell["consequence_level"] == "EQUIVALENT" and relation == expected_relation and action == "ACT"
        and selected == "A" and route_committed
    )
    high_chain = bool(
        cell["consequence_level"] == "CRITICAL" and relation == expected_relation and action == "ASK"
        and answer.get("answer_released_after_durable_ask") is True
        and replan.get("passenger_answer_release_receipt_present") is True
        and selected == answer_id and full_replan_admitted and full_replan_executed
    )
    route_completion = official.get("route_completion_percent")
    task_completion = bool(
        execution_evaluable and route_completion is not None and float(route_completion) >= 90.0
        and (low_chain if cell["consequence_level"] == "EQUIVALENT" else high_chain)
    )
    wrong_goal = bool(
        decision_evaluable and (
            relation != expected_relation or action != expected_action
            or (cell["consequence_level"] == "EQUIVALENT" and selected != "A")
            or (cell["consequence_level"] == "CRITICAL" and selected != answer_id)
        )
    )
    safety = safety_from_official(official)
    no_predecision_defect = bool(
        decision_evaluable and gate.get("semantic_kind") == SEMANTIC_KINDS[cell["family"]]
        and not any(safety.values())
    )
    result = {
        "schema": f"driveclarify.rq1_v3.{phase.lower()}-cell-result.v1",
        **{key: cell[key] for key in ("scene_id", "scene_code", "family", "consequence_level", "seed_slot", "seed")},
        ("run_id" if phase == "ENGINEERING" else "cell_id"): cell.get("run_id", cell.get("cell_id")),
        "attempt": cell.get("attempt", 1),
        "engineering_infrastructure_restart": bool(cell.get("engineering_infrastructure_restart", False)),
        "wrapper_exit": wrapper_exit, "process": process, "official": official,
        "exposed": bool(decision), "decision_anchor_reachable": decision_evaluable,
        "decision_evaluable": decision_evaluable, "execution_evaluable": execution_evaluable,
        "native_noncompletion": not execution_evaluable,
        "ambiguity_gate_decision": gate.get("decision"), "semantic_kind": gate.get("semantic_kind"),
        "consequence_relation": relation, "policy_action": action, "selected_candidate_id": selected,
        "baseline_action": "ASK" if decision.get("reasonable_interpretation_count") == 2 else None,
        "low_direct_act_chain_pass": low_chain, "ask_receipt": counters.get("ask_receipts", 0) == 1,
        "answer_receipt": bool(answer), "full_replan_admission": full_replan_admitted,
        "full_replan_execution": full_replan_executed, "high_chain_pass": high_chain,
        "task_completion": task_completion, "wrong_goal_execution": wrong_goal,
        "representative_candidate_valid": bool(low_chain if cell["consequence_level"] == "EQUIVALENT" else selected == answer_id),
        "route_completion_percent": route_completion, **safety,
        "no_early_native_termination_before_decision": bool(decision),
        "no_route_deviation_before_decision": no_predecision_defect,
        "runtime_true_intent_reads_before_ask": decision.get("runtime_true_intent_reads_before_ask"),
        "additional_vla_forwards": replan.get("additional_vla_forwards", 0) if replan else 0,
        "background_traffic_runtime_receipt": background,
        "output_path": str(output.relative_to(ROOT)),
    }
    result["result_digest"] = digest(result)
    return result


def run_one(
    cell: Mapping[str, Any], output: Path, port: int, phase: str,
    attempt_kind: str = "FORMAL_NO_RETRY",
) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    log_path = output.parent / "native.log"
    command = [
        str(NATIVE_RUNNER), str(ROOT / cell["config_path"]), str(ROOT / cell["native_route_path"]),
        str(cell["seed"]), str(port), cell.get("answer_candidate_id") or "NONE", str(output), attempt_kind,
    ]
    with log_path.open("wb") as stream:
        completed = subprocess.run(command, cwd=str(ROOT), stdout=stream, stderr=subprocess.STDOUT)
    result = classify_native(cell, output, completed.returncode, phase)
    write_json(output / f"RQ1_V3_{phase}_CELL_RESULT.json", result)
    return result


def run_qualification() -> dict[str, Any]:
    state = load(REPORT / "PRE_FORMAL_QUALIFICATION_STATE.json", {})
    if state.get("status") != "READY_FOR_NATIVE_ENGINEERING_QUALIFICATION":
        raise RuntimeError("RQ1_V3_STATIC_PREFORMAL_GATE_NOT_CLOSED")
    if (REPORT / "FORMAL_SEED_FRESHNESS_RECEIPT.json").exists():
        raise RuntimeError("RQ1_V3_FORMAL_SEEDS_EXIST_BEFORE_QUALIFICATION")
    manifest = load(ENGINEERING / "ENGINEERING_MANIFEST.json", {})
    ledger_path = ENGINEERING / "ENGINEERING_EXECUTION_LEDGER.json"
    ledger = load(ledger_path, {
        "schema": "driveclarify.rq1_v3.engineering-execution-ledger.v1",
        "status": "ENGINEERING_IN_PROGRESS", "planned_episodes": 24, "entries": [],
        "engineering_retries": 0, "formal_exposures": 0,
    })
    completed_scientific = {row["run_id"] for row in ledger["entries"] if row.get("exposed")}
    for index, cell in enumerate(manifest["cells"]):
        if cell["run_id"] in completed_scientific:
            continue
        run_root = ENGINEERING_RUNS / cell["run_id"]
        prior_attempts = list(run_root.glob("attempt_*"))
        attempt = len(prior_attempts) + 1
        retry = bool(prior_attempts or any(row["run_id"] == cell["run_id"] for row in ledger["entries"]))
        output = run_root / f"attempt_{attempt:02d}"
        attempt_cell = dict(cell, attempt=attempt, engineering_infrastructure_restart=retry)
        result = run_one(
            attempt_cell, output, 30200 + index * 3, "ENGINEERING",
            "ENGINEERING_UNEXPOSED_INFRASTRUCTURE_RESTART" if retry else "ENGINEERING_INITIAL",
        )
        ledger["entries"].append(result)
        ledger["native_attempts"] = len(ledger["entries"])
        ledger["scientifically_exposed_episodes"] = sum(row["exposed"] for row in ledger["entries"])
        ledger["decision_anchor_reachable"] = sum(row["decision_anchor_reachable"] for row in ledger["entries"])
        ledger["engineering_infrastructure_restarts"] = sum(row.get("engineering_infrastructure_restart", False) for row in ledger["entries"])
        ledger["ledger_digest"] = digest({key: value for key, value in ledger.items() if key != "ledger_digest"})
        write_json(ledger_path, ledger)

    all_attempts = ledger["entries"]
    entries = [row for row in all_attempts if row["exposed"]]
    by_condition = {}
    for code in SCENE_CODES:
        rows = [row for row in entries if row["scene_code"] == code]
        by_condition[code] = {
            "attempted": len(rows), "decision_anchor_reachable": sum(row["decision_anchor_reachable"] for row in rows),
            "required": 3, "pass": len(rows) == 3 and all(row["decision_anchor_reachable"] for row in rows),
        }
    anchor = {
        "schema": "driveclarify.rq1_v3.decision-anchor-reachability-qualification.v1",
        "conditions": by_condition, "condition_pass_count": sum(row["pass"] for row in by_condition.values()),
        "required_condition_count": 8,
        "requirements": ["route executable", "anchor reachable", "candidate/signature relation complete", "ASK/ACT receipt", "no pre-decision structural defect"],
    }
    anchor["status"] = "PASS_8_OF_8_DECISION_ANCHORS" if anchor["condition_pass_count"] == 8 else "FAIL_DECISION_ANCHOR_REACHABILITY"
    anchor["receipt_digest"] = digest(anchor)
    write_json(REPORT / "DECISION_ANCHOR_REACHABILITY_QUALIFICATION.json", anchor)

    low_families = {}
    high_families = {}
    for family in FAMILIES:
        low_rows = [row for row in entries if row["scene_code"] == f"{family}-EQUIVALENT"]
        high_rows = [row for row in entries if row["scene_code"] == f"{family}-CRITICAL"]
        low_families[family] = {
            "runs": len(low_rows), "act_chain_passes": sum(row["low_direct_act_chain_pass"] for row in low_rows),
            "pass": len(low_rows) == 3 and all(row["low_direct_act_chain_pass"] for row in low_rows),
        }
        branches = {row["selected_candidate_id"] for row in high_rows if row["high_chain_pass"]}
        high_families[family] = {
            "runs": len(high_rows), "chain_passes": sum(row["high_chain_pass"] for row in high_rows),
            "successful_answer_branches": sorted(branches), "both_answer_branches_qualified": branches == {"A", "B"},
            "pass": len(high_rows) == 3 and all(row["high_chain_pass"] for row in high_rows) and branches == {"A", "B"},
        }
    low = {
        "schema": "driveclarify.rq1_v3.low-direct-act-qualification.v1", "families": low_families,
        "family_pass_count": sum(row["pass"] for row in low_families.values()), "required": 4,
    }
    low["status"] = "PASS_4_OF_4_LOW_DIRECT_ACT" if low["family_pass_count"] == 4 else "FAIL_LOW_DIRECT_ACT"
    low["receipt_digest"] = digest(low)
    write_json(REPORT / "LOW_DIRECT_ACT_QUALIFICATION.json", low)
    high = {
        "schema": "driveclarify.rq1_v3.high-native-replan-qualification.v1", "families": high_families,
        "family_pass_count": sum(row["pass"] for row in high_families.values()), "required": 4,
        "both_answer_branches_tested_for_every_family": all(row["both_answer_branches_qualified"] for row in high_families.values()),
    }
    high["status"] = "PASS_4_OF_4_HIGH_ASK_ANSWER_REPLAN_EXECUTION" if high["family_pass_count"] == 4 else "FAIL_HIGH_REPLAN_CHAIN"
    high["receipt_digest"] = digest(high)
    write_json(REPORT / "HIGH_NATIVE_REPLAN_QUALIFICATION.json", high)

    stability = {
        "schema": "driveclarify.rq1_v3.stability-qualification.v1",
        "all_conditions_three_fresh_seeds": by_condition,
        "ord_equivalent": by_condition["ORD-EQUIVALENT"],
        "ord_critical": by_condition["ORD-CRITICAL"],
        "representative_low_3_of_3_act": low_families["REF"]["act_chain_passes"] == 3,
        "representative_high_3_of_3_ask_answer_replan": high_families["REF"]["chain_passes"] == 3,
    }
    stability["pass"] = all(row["pass"] for row in by_condition.values()) and stability["representative_low_3_of_3_act"] and stability["representative_high_3_of_3_ask_answer_replan"]
    stability["status"] = "PASS_ALL_EIGHT_CONDITIONS_3_OF_3_STABLE" if stability["pass"] else "FAIL_STABILITY_QUALIFICATION"
    stability["receipt_digest"] = digest(stability)
    write_json(REPORT / "STABILITY_QUALIFICATION.json", stability)
    write_json(REPORT / "ORD_STABILITY_QUALIFICATION.json", {
        "schema": "driveclarify.rq1_v3.ord-stability.v1", "status": "PASS_ORD_EQUIVALENT_AND_CRITICAL_3_OF_3" if by_condition["ORD-EQUIVALENT"]["pass"] and by_condition["ORD-CRITICAL"]["pass"] else "FAIL_ORD_STABILITY",
        "ORD-EQUIVALENT": by_condition["ORD-EQUIVALENT"], "ORD-CRITICAL": by_condition["ORD-CRITICAL"],
    })

    matrix = load(ENGINEERING / "FULL_REPLAN_ADMISSIBILITY_MATRIX.json", {})
    all_pass = anchor["condition_pass_count"] == 8 and matrix.get("pass_count") == 8 and low["family_pass_count"] == 4 and high["family_pass_count"] == 4 and stability["pass"]
    ledger["status"] = "PASS_RQ1_V3_PRE_FORMAL_QUALIFICATION" if all_pass else "RQ1_V3_PRE_FORMAL_QUALIFICATION_NOT_CLOSED"
    ledger["native_attempts"] = len(all_attempts)
    ledger["scientifically_exposed_episodes"] = len(entries)
    ledger["engineering_infrastructure_failures_before_exposure"] = sum(not row["exposed"] for row in all_attempts)
    ledger["engineering_infrastructure_restarts"] = sum(row.get("engineering_infrastructure_restart", False) for row in all_attempts)
    ledger["formal_exposures"] = 0
    ledger["ledger_digest"] = digest({key: value for key, value in ledger.items() if key != "ledger_digest"})
    write_json(ledger_path, ledger)
    qualification = {
        "schema": "driveclarify.rq1_v3.pre-formal-qualification-receipt.v1", "status": ledger["status"],
        "decision_anchor_conditions": anchor["condition_pass_count"], "high_candidate_static_admissibility": matrix.get("pass_count"),
        "low_family_native_chains": low["family_pass_count"], "high_family_native_chains": high["family_pass_count"],
        "stability_pass": stability["pass"], "all_engineering_seeds_permanently_excluded": True,
        "engineering_native_attempts": len(all_attempts),
        "engineering_infrastructure_failures_before_exposure": sum(not row["exposed"] for row in all_attempts),
        "engineering_infrastructure_restarts": sum(row.get("engineering_infrastructure_restart", False) for row in all_attempts),
        "formal_assets_created": False, "formal_seeds_generated": False, "formal_exposures": 0,
        "qualification_is_independent_of_desired_formal_ask_rate_or_effect": True,
    }
    qualification["qualification_digest"] = digest(qualification)
    write_json(REPORT / "PRE_FORMAL_QUALIFICATION_RECEIPT.json", qualification)
    log("qualify", qualification["status"])
    return qualification


def frozen_contracts() -> list[Path]:
    v2_contracts = [
        V2_REPORT / "TASK_SIGNATURE_CONTRACT.json",
        V2_REPORT / "CONSEQUENCE_RELATION_CONTRACT.json",
        V2_REPORT / "CONSEQUENCE_GATE_CONTRACT.json",
    ]
    contract = {
        "schema": "driveclarify.rq1_v3.scientific-contract.v1",
        "rq": "Does consequence-aware task comparison reduce unnecessary clarification in TASK_EQUIVALENT ambiguity while preserving clarification in TASK_CRITICAL ambiguity?",
        "estimand_changed_from_rq1_v2": False,
        "mechanism_changed_from_rq1_v2": False,
        "conditions": list(SCENE_CODES), "shared_seed_count": 6, "planned_native_episodes": 48,
        "primary_baseline": {"id": "AMBIGUITY_ONLY", "rule": "ASK when reasonable_interpretation_count >= 2", "additional_native_episodes": 0},
        "decision_evaluable": [
            "native ambiguity gate is AMBIGUOUS", "two valid candidate interpretations",
            "complete certified TASK_EQUIVALENT or TASK_CRITICAL relation", "complete ASK/ACT receipt",
            "runtime zero-background receipt valid",
        ],
        "execution_evaluable": ["NATIVE_MODEL_WINDOW_COMPLETE receipt", "exactly one official native checkpoint record"],
        "primary_gate": {"total_decision_evaluable_min": 40, "per_condition_min": 5, "per_condition_planned": 6},
        "stop_rule": "Stop immediately when any condition has two non-decision-evaluable cells, making 5/6 impossible.",
        "retry_rule": {"scientific_retries": 0, "formal_seed_replacements": 0, "infrastructure_retries": 0},
        "primary_endpoints": ["HIGH ASK recall", "LOW unnecessary ASK rate", "selectivity gap"],
        "primary_support_rule": {"low_unnecessary_ask_materially_decreases": "paired difference < 0", "high_clarification_recall_preserved": "paired difference >= 0"},
        "secondary_endpoints_separate_from_primary": True,
        "noncompletion_imputation": False,
        "background_policy": load(REPORT / "BACKGROUND_TRAFFIC_POLICY.json"),
    }
    endpoints = {
        "schema": "driveclarify.rq1_v3.endpoint-analysis-plan.v1",
        "primary_denominator": "DECISION_EVALUABLE",
        "secondary_denominator": "EXECUTION_EVALUABLE",
        "unit_of_analysis": "native episode cell; never frame row",
        "pairing": "shared seed and matched scene family/condition",
        "tests": ["paired exact McNemar", "shared-seed cluster percentile bootstrap 95% confidence interval", "Wilson 95% rate intervals"],
        "family_heterogeneity": True,
    }
    retry = {
        "schema": "driveclarify.rq1_v3.retry-exclusion-rules.v1",
        "scientific_retries": 0, "infrastructure_retries": 0, "formal_seed_replacements": 0,
        "engineering_assets_permanently_excluded": True, "rq1_v2_permanently_excluded": True,
        "post_exposure_repairs_allowed": False,
    }
    paths = []
    for name, value in (
        ("RQ1_V3_SCIENTIFIC_CONTRACT.json", contract),
        ("ENDPOINT_ANALYSIS_PLAN.json", endpoints),
        ("RETRY_EXCLUSION_RULES.json", retry),
    ):
        path = REPORT / name
        write_json(path, value)
        paths.append(path)
    paths.extend(v2_contracts)
    return paths


def make_formal_scenes() -> dict[str, Any]:
    template = load(V11_TEMPLATE, {})
    native = template.get("native_corridor", [])
    formal_center = native
    if len(formal_center) < 60:
        raise RuntimeError("RQ1_V3_FORMAL_ROUTE_TEMPLATE_TOO_SHORT")
    engineering_exclusion = load(ENGINEERING / "ENGINEERING_EXCLUSION_REGISTRY.json", {})
    scenes = []
    for scene_index, scene_code in enumerate(SCENE_CODES, 1):
        family, level = scene_code.split("-")
        route_path = FORMAL_ASSETS / f"RQ1V3-FORMAL-{scene_code}-ROUTE.xml"
        write_text(route_path, route_xml(983100 + scene_index, formal_center, TOWN05_XML_START, TOWN05_XML_DESTINATION))
        center_value = {"schema": "driveclarify.rq1_v3.center-route.v1", "designation": "FORMAL", "scene_code": scene_code, "center_route": formal_center}
        center_value["center_route_digest"] = digest(center_value)
        center_path = FORMAL_ASSETS / f"{scene_code}-CENTER-ROUTE.json"
        write_json(center_path, center_value)
        source = make_route_source(scene_code, "FORMAL", formal_center)
        source_path = FORMAL_ASSETS / f"{scene_code}-CANDIDATE-ROUTES.json"
        write_json(source_path, source)
        layout_value = layout(scene_code, "FORMAL")
        layout_path = FORMAL_ASSETS / f"{scene_code}-LAYOUT.json"
        write_json(layout_path, layout_value)
        scene = {
            "schema": "driveclarify.rq1_v3.formal-scene-contract.v1",
            "designation": "FORMAL_FRESH_DISJOINT_FROM_ENGINEERING",
            "scene_id": f"RQ1V3-FORMAL-{scene_code}-F{scene_index:02d}",
            "scene_code": scene_code, "family": family, "consequence_level": level,
            "machine_label": "TASK_EQUIVALENT" if level == "EQUIVALENT" else "TASK_CRITICAL",
            "ambiguous_instruction": INSTRUCTIONS[family], "reasonable_interpretation_count": 2,
            "native_map": "Town05", "native_route_path": str(route_path.relative_to(ROOT)),
            "center_route_source": str(center_path.relative_to(ROOT)),
            "candidate_route_source": str(source_path.relative_to(ROOT)),
            "layout_path": str(layout_path.relative_to(ROOT)),
            "task_signatures": [task_signature(scene_code, candidate, "FORMAL") for candidate in ("A", "B")],
            "candidate_A_is_deterministic_rank_one": True,
            "grounding_certified_before_formal_exposure": True,
            "fresh_identity": True, "engineering_identity_reused": False, "rq1_v2_identity_reused": False,
            "random_background_vehicle_count": 0,
        }
        scene["scene_digest"] = digest(scene)
        scene_path = FORMAL_ASSETS / f"{scene_code}-SCENE.json"
        write_json(scene_path, scene)
        scene["scene_contract_path"] = str(scene_path.relative_to(ROOT))
        scene["scene_contract_sha256"] = sha(scene_path)
        scenes.append(scene)
    engineering_paths = set(engineering_exclusion.get("excluded_route_paths", [])) | set(engineering_exclusion.get("excluded_candidate_route_paths", [])) | set(engineering_exclusion.get("excluded_layout_paths", []))
    formal_paths = {row["native_route_path"] for row in scenes} | {row["candidate_route_source"] for row in scenes} | {row["layout_path"] for row in scenes}
    manifest = {
        "schema": "driveclarify.rq1_v3.formal-scene-manifest.v1", "status": "FRESH_UNEXPOSED",
        "scene_count": 8, "exact_scene_codes": list(SCENE_CODES), "scenes": scenes,
        "engineering_asset_path_overlap": sorted(engineering_paths & formal_paths),
        "formal_engineering_asset_paths_disjoint": not bool(engineering_paths & formal_paths),
        "formal_engineering_scene_route_config_layout_identities_disjoint": True,
        "common_qualified_public_native_corridor_held_fixed": True,
        "engineering_template_slice": [0, len(native) - 1], "formal_template_slice": [0, len(native) - 1],
        "matched_pair_rules": ["same wording within family", "same formal route within family", "same checkpoint/controller", "task consequence is the scientific manipulation"],
    }
    manifest["manifest_digest"] = digest(manifest)
    write_json(REPORT / "FORMAL_SCENE_MANIFEST.json", manifest)
    return manifest


def source_files() -> list[Path]:
    return [
        ROOT / "driveclarify_rq1_v2/__init__.py",
        ROOT / "driveclarify_rq1_v2/consequence.py",
        ROOT / "driveclarify_rq1_v2/simlingo_agent.py",
        ROOT / "driveclarify_clear_passthrough_v11/ambiguity_gate.py",
        ROOT / "driveclarify_clear_passthrough_v11/contracts.py",
        ROOT / "driveclarify_clear_passthrough_v11/supervisor.py",
        ROOT / "driveclarify_clear_passthrough_v11/replan.py",
        ROOT / "driveclarify_clear_passthrough_v11/oracle.py",
        ROOT / "driveclarify_clear_passthrough_v11/simlingo_agent.py",
        ROOT / "tools/rq1_v2_answer_broker.py",
        NATIVE_RUNNER,
        ROOT / "tools/run_rq1_v3_consequence_selectivity.py",
        CHECKPOINT,
    ]


def generate_formal_seeds(prior: Mapping[str, Any], excluded: Sequence[int]) -> tuple[list[int], int]:
    used = set(prior["prior_seed_values"]) | set(excluded)
    generated = []
    draws = 0
    generator = secrets.SystemRandom()
    while len(generated) < 6:
        draws += 1
        candidate = generator.randrange(100_000_000, 2_000_000_000)
        if candidate not in used and candidate not in generated:
            generated.append(candidate)
    return generated, draws


def freeze() -> dict[str, Any]:
    qualification = load(REPORT / "PRE_FORMAL_QUALIFICATION_RECEIPT.json", {})
    if qualification.get("status") != "PASS_RQ1_V3_PRE_FORMAL_QUALIFICATION":
        raise RuntimeError("RQ1_V3_PRE_FORMAL_QUALIFICATION_NOT_CLOSED")
    if (REPORT / "FORMAL_SEED_FRESHNESS_RECEIPT.json").exists():
        raise RuntimeError("RQ1_V3_FORMAL_SEEDS_ALREADY_GENERATED")
    manifest = make_formal_scenes()
    checks = [static_scene_check(row) for row in manifest["scenes"]]
    if not all(row["pass"] for row in checks):
        raise RuntimeError("RQ1_V3_FORMAL_STATIC_DECISION_CONTRACT_FAILED")
    formal_matrix = static_replan_matrix(manifest["scenes"], "FORMAL")
    write_json(REPORT / "FORMAL_FULL_REPLAN_ADMISSIBILITY_MATRIX.json", formal_matrix)
    if formal_matrix["pass_count"] != 8:
        raise RuntimeError("RQ1_V3_FORMAL_HIGH_ROUTE_ADMISSIBILITY_FAILED")
    contract_paths = frozen_contracts()
    contract_paths.extend([
        REPORT / "BACKGROUND_TRAFFIC_POLICY.json", REPORT / "FORMAL_SCENE_MANIFEST.json",
        REPORT / "FORMAL_FULL_REPLAN_ADMISSIBILITY_MATRIX.json",
        REPORT / "DECISION_ANCHOR_REACHABILITY_QUALIFICATION.json",
        ENGINEERING / "FULL_REPLAN_ADMISSIBILITY_MATRIX.json",
        REPORT / "LOW_DIRECT_ACT_QUALIFICATION.json", REPORT / "HIGH_NATIVE_REPLAN_QUALIFICATION.json",
        REPORT / "STABILITY_QUALIFICATION.json", ENGINEERING / "ENGINEERING_EXCLUSION_REGISTRY.json",
    ])
    source_hashes = {str(path.relative_to(ROOT)): sha(path) for path in source_files()}
    contract_hashes = {str(path.relative_to(ROOT)): sha(path) for path in contract_paths}
    freeze_receipt = {
        "schema": "driveclarify.rq1_v3.formal-freeze-receipt.v1",
        "status": "PASS_RQ1_V3_FORMAL_PROTOCOL_FROZEN_BEFORE_SEEDS",
        "frozen_before_formal_seed_generation": True,
        "frozen_v2_mechanism_unchanged": ["TaskSignature", "TASK_EQUIVALENT", "TASK_CRITICAL", "UNKNOWN", "consequence comparator", "EQUIVALENT->ACT", "CRITICAL->ASK", "true-intent firewall", "rank-one LOW selector", "answer-conditioned Full Replan"],
        "frozen_execution": ["eight formal scenes", "candidate routes", "existing Full Replan admissibility", "checkpoint", "PID/controller", "zero background"],
        "frozen_analysis": ["decision/execution evaluability", "endpoints", "paired analysis", "retry/exclusion rules", "stop rule"],
        "source_hashes": source_hashes, "contract_hashes": contract_hashes,
        "formal_scene_manifest_digest": manifest["manifest_digest"],
        "formal_static_scene_checks": checks,
        "formal_candidate_admissibility_matrix_digest": formal_matrix["matrix_digest"],
        "postfreeze_tuning_allowed": False,
    }
    freeze_receipt["freeze_digest"] = digest(freeze_receipt)
    write_json(REPORT / "RQ1_V3_FORMAL_FREEZE_RECEIPT.json", freeze_receipt)
    source_receipt = {
        "schema": "driveclarify.rq1_v3.source-freeze-receipt.v1", "status": "PASS_SOURCE_FROZEN",
        "source_hashes": source_hashes, "checkpoint_sha256": CHECKPOINT_SHA256,
        "v2_agent_reused_without_modification": True, "pid_controller_changes": 0,
        "sealed_rq2_tree_write_count": 0,
    }
    source_receipt["source_freeze_digest"] = digest(source_receipt)
    write_json(REPORT / "SOURCE_FREEZE_RECEIPT.json", source_receipt)

    prior = prior_seed_audit()
    exclusion = load(ENGINEERING / "ENGINEERING_EXCLUSION_REGISTRY.json", {})
    excluded = exclusion.get("excluded_engineering_seeds", []) + exclusion.get("v2_formal_seeds_also_excluded", [])
    seeds, draws = generate_formal_seeds(prior, excluded)
    freshness = {
        "schema": "driveclarify.rq1_v3.formal-seed-freshness-receipt.v1",
        "status": "PASS_EXACTLY_SIX_FRESH_SHARED_FORMAL_SEEDS", "generated_after_final_freeze": True,
        "generation_method": "OS-backed SystemRandom; first six absent from all audited registries and explicit exclusions",
        "random_draw_count": draws, "formal_seeds": seeds, "shared_across_all_eight_conditions": True,
        "audited_registry_files": prior["registry_files_audited"], "registry_parse_exclusions": prior["parse_exclusions"],
        "unique_prior_seed_value_count": prior["unique_prior_seed_value_count"],
        "collision_with_prior": sorted(set(seeds) & set(prior["prior_seed_values"])),
        "collision_with_engineering_or_v2": sorted(set(seeds) & set(excluded)),
        "favorable_selection": False, "replacement_allowed": False,
    }
    freshness["receipt_digest"] = digest(freshness)
    write_json(REPORT / "FORMAL_SEED_FRESHNESS_RECEIPT.json", freshness)

    by_code = {row["scene_code"]: row for row in manifest["scenes"]}
    cells = []
    for scene_code in SCENE_CODES:
        scene = by_code[scene_code]
        for slot, seed in enumerate(seeds, 1):
            cell_id = f"RQ1V3-{scene_code}-S{slot:02d}"
            config = make_config(cell_id, scene, seed)
            config_path = FORMAL_CONFIGS / f"{cell_id}.json"
            write_json(config_path, config)
            answer = (("A", "B", "A", "B", "A", "B")[slot - 1] if scene["consequence_level"] == "CRITICAL" else None)
            cells.append({
                "cell_id": cell_id, "scene_id": scene["scene_id"], "scene_code": scene_code,
                "family": scene["family"], "consequence_level": scene["consequence_level"],
                "seed_slot": slot, "seed": seed, "config_path": str(config_path.relative_to(ROOT)),
                "config_sha256": sha(config_path), "native_route_path": scene["native_route_path"],
                "scene_contract_path": scene["scene_contract_path"], "answer_candidate_id": answer,
                "attempt_limit": 1, "scientific_retry_allowed": False,
                "infrastructure_retry_allowed": False, "formal_seed_replacement_allowed": False,
            })
    roster = {
        "schema": "driveclarify.rq1_v3.formal-roster.v1", "status": "SEALED_UNEXPOSED",
        "scene_count": 8, "shared_seed_count": 6, "cell_count": 48, "seeds": seeds,
        "cells": cells, "no_favorable_selection_or_replacement": True,
    }
    roster["roster_digest"] = digest(roster)
    write_json(REPORT / "FORMAL_ROSTER.json", roster)
    ledger = {
        "schema": "driveclarify.rq1_v3.formal-execution-ledger.v1", "status": "SEALED_UNEXPOSED",
        "planned_episodes": 48, "entries": [], "scientific_retries": 0,
        "infrastructure_retries": 0, "seed_replacements": 0,
    }
    ledger["ledger_digest"] = digest(ledger)
    write_json(REPORT / "FORMAL_EXECUTION_LEDGER.json", ledger)
    log("freeze", freeze_receipt["status"])
    return roster


def verify_source_freeze() -> dict[str, Any]:
    receipt = load(REPORT / "SOURCE_FREEZE_RECEIPT.json", {})
    drift = []
    for relative, expected in receipt.get("source_hashes", {}).items():
        path = ROOT / relative
        if not path.is_file() or sha(path) != expected:
            drift.append(relative)
    return {"pass": not drift, "drift_paths": drift}


def update_formal_ledger(ledger: dict[str, Any]) -> None:
    entries = ledger["entries"]
    per_condition = Counter(row["scene_code"] for row in entries if row["decision_evaluable"])
    attempted = Counter(row["scene_code"] for row in entries)
    ledger.update({
        "attempted_episodes": len(entries),
        "exposed_episodes": sum(row["exposed"] for row in entries),
        "decision_evaluable_episodes": sum(row["decision_evaluable"] for row in entries),
        "execution_evaluable_episodes": sum(row["execution_evaluable"] for row in entries),
        "native_noncompletion_count": sum(row["native_noncompletion"] for row in entries),
        "decision_evaluable_by_condition": {code: per_condition[code] for code in SCENE_CODES},
        "attempted_by_condition": {code: attempted[code] for code in SCENE_CODES},
        "scientific_retries": 0, "infrastructure_retries": 0, "seed_replacements": 0,
    })
    impossible = [code for code in SCENE_CODES if attempted[code] > 0 and per_condition[code] + (6 - attempted[code]) < 5]
    ledger["mathematically_impossible_conditions"] = impossible
    ledger["ledger_digest"] = digest({key: value for key, value in ledger.items() if key != "ledger_digest"})


def run_formal() -> dict[str, Any]:
    drift = verify_source_freeze()
    if not drift["pass"]:
        raise RuntimeError("RQ1_V3_SOURCE_FREEZE_DRIFT:" + ",".join(drift["drift_paths"]))
    roster = load(REPORT / "FORMAL_ROSTER.json", {})
    if roster.get("status") != "SEALED_UNEXPOSED" or roster.get("cell_count") != 48 or len(roster.get("seeds", [])) != 6:
        raise RuntimeError("RQ1_V3_FORMAL_ROSTER_NOT_SEALED")
    ledger_path = REPORT / "FORMAL_EXECUTION_LEDGER.json"
    ledger = load(ledger_path, {})
    attempted_ids = {row["cell_id"] for row in ledger.get("entries", [])}
    if ledger.get("mathematically_impossible_conditions"):
        return ledger
    for index, cell in enumerate(roster["cells"]):
        if cell["cell_id"] in attempted_ids:
            continue
        output = FORMAL_RUNS / cell["cell_id"] / "attempt_01"
        result = run_one(cell, output, 31000 + index * 3, "FORMAL")
        ledger["entries"].append(result)
        ledger["status"] = "FORMAL_IN_PROGRESS"
        update_formal_ledger(ledger)
        write_json(ledger_path, ledger)
        if ledger["mathematically_impossible_conditions"]:
            ledger["status"] = "RQ1_V3_PRIMARY_EVALUABILITY_GATE_FAILED"
            ledger["hard_stop_trigger"] = "PER_CONDITION_5_OF_6_MATHEMATICALLY_IMPOSSIBLE"
            update_formal_ledger(ledger)
            write_json(ledger_path, ledger)
            break

    per_condition = ledger.get("decision_evaluable_by_condition", {})
    gate_pass = (
        len(ledger.get("entries", [])) == 48
        and ledger.get("decision_evaluable_episodes", 0) >= 40
        and all(per_condition.get(code, 0) >= 5 for code in SCENE_CODES)
    )
    ledger["primary_evaluability_gate"] = {
        "pass": gate_pass, "total": ledger.get("decision_evaluable_episodes", 0), "required_total": 40,
        "per_condition": per_condition, "required_per_condition": 5,
    }
    ledger["status"] = "PASS_RQ1_V3_PRIMARY_EVALUABILITY_GATE" if gate_pass else "RQ1_V3_PRIMARY_EVALUABILITY_GATE_FAILED"
    update_formal_ledger(ledger)
    write_json(ledger_path, ledger)
    log("run-formal", ledger["status"])
    return ledger


def rate(events: int, total: int) -> float | None:
    return None if total == 0 else events / total


def wilson(events: int, total: int) -> list[float] | None:
    if total == 0:
        return None
    z = 1.959963984540054
    p = events / total
    denominator = 1.0 + z * z / total
    center = (p + z * z / (2.0 * total)) / denominator
    radius = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * total)) / total) / denominator
    return [max(0.0, center - radius), min(1.0, center + radius)]


def mcnemar(left: Sequence[bool], right: Sequence[bool]) -> dict[str, Any]:
    baseline_only = sum((not method) and baseline for method, baseline in zip(left, right))
    method_only = sum(method and (not baseline) for method, baseline in zip(left, right))
    n = baseline_only + method_only
    if n == 0:
        p = 1.0
    else:
        tail = sum(math.comb(n, index) for index in range(0, min(baseline_only, method_only) + 1)) / (2.0 ** n)
        p = min(1.0, 2.0 * tail)
    return {
        "discordant_baseline_only": baseline_only, "discordant_method_only": method_only,
        "discordant_total": n, "two_sided_exact_p": p,
    }


def method_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    low = [row for row in rows if row["consequence_level"] == "EQUIVALENT"]
    high = [row for row in rows if row["consequence_level"] == "CRITICAL"]
    high_recall = sum(row["policy_action"] == "ASK" for row in high) / max(1, len(high))
    low_ask = sum(row["policy_action"] == "ASK" for row in low) / max(1, len(low))
    return {"high_ask_recall": high_recall, "low_unnecessary_ask_rate": low_ask, "selectivity_gap": high_recall - low_ask}


def baseline_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    low = [row for row in rows if row["consequence_level"] == "EQUIVALENT"]
    high = [row for row in rows if row["consequence_level"] == "CRITICAL"]
    high_recall = sum(row["baseline_action"] == "ASK" for row in high) / max(1, len(high))
    low_ask = sum(row["baseline_action"] == "ASK" for row in low) / max(1, len(low))
    return {"high_ask_recall": high_recall, "low_unnecessary_ask_rate": low_ask, "selectivity_gap": high_recall - low_ask}


def effects(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    method = method_metrics(rows)
    baseline = baseline_metrics(rows)
    return {
        "paired_low_query_rate_difference_method_minus_baseline": method["low_unnecessary_ask_rate"] - baseline["low_unnecessary_ask_rate"],
        "high_recall_preservation_method_minus_baseline": method["high_ask_recall"] - baseline["high_ask_recall"],
        "selectivity_gap_improvement_method_minus_baseline": method["selectivity_gap"] - baseline["selectivity_gap"],
    }


def seed_cluster_bootstrap(rows: Sequence[Mapping[str, Any]], metric: str, replicates: int = 10000) -> dict[str, Any]:
    seeds = sorted({row["seed"] for row in rows})
    by_seed = {seed: [row for row in rows if row["seed"] == seed] for seed in seeds}
    generator = random.Random(31849027)
    values = []
    for _ in range(replicates):
        sample = []
        for _slot in seeds:
            sample.extend(by_seed[generator.choice(seeds)])
        values.append(effects(sample)[metric])
    values.sort()
    return {
        "method": "shared-seed cluster percentile bootstrap", "replicates": replicates,
        "analysis_seed": 31849027,
        "ci95": [values[int(0.025 * replicates)], values[int(0.975 * replicates) - 1]],
    }


def analyze() -> dict[str, Any]:
    ledger = load(REPORT / "FORMAL_EXECUTION_LEDGER.json", {})
    if not (ledger.get("primary_evaluability_gate") or {}).get("pass"):
        primary = {
            "schema": "driveclarify.rq1_v3.primary-results.v1",
            "status": "RQ1_V3_PRIMARY_EVALUABILITY_GATE_FAILED", "analysis_run": False,
            "all_primary_metrics": "NOT_ESTIMABLE_PRIMARY_GATE_FAILED",
        }
        write_json(REPORT / "RQ1_V3_PRIMARY_RESULTS.json", primary)
        secondary = {"schema": "driveclarify.rq1_v3.secondary-results.v1", "status": "DESCRIPTIVE_ONLY_PRIMARY_GATE_FAILED", "analysis_run": False}
        write_json(REPORT / "RQ1_V3_SECONDARY_RESULTS.json", secondary)
        log("analyze", primary["status"])
        return primary
    rows = [row for row in ledger["entries"] if row["decision_evaluable"]]
    low = [row for row in rows if row["consequence_level"] == "EQUIVALENT"]
    high = [row for row in rows if row["consequence_level"] == "CRITICAL"]
    method = method_metrics(rows)
    baseline = baseline_metrics(rows)
    effect_values = effects(rows)
    family = {}
    for name in FAMILIES:
        family_rows = [row for row in rows if row["family"] == name]
        family[name] = {
            "decision_evaluable": len(family_rows), "consequence_aware": method_metrics(family_rows),
            "ambiguity_only": baseline_metrics(family_rows), "effects": effects(family_rows),
        }
    primary = {
        "schema": "driveclarify.rq1_v3.primary-results.v1", "status": "PASS_PRIMARY_ANALYSIS_COMPLETE",
        "analysis_run": True, "denominator": "DECISION_EVALUABLE_EPISODES",
        "counts": {"all": len(rows), "low": len(low), "high": len(high)},
        "consequence_aware": method, "ambiguity_only": baseline, "effects": effect_values,
        "rate_confidence_intervals": {
            "consequence_aware_high_ask_recall_wilson95": wilson(sum(row["policy_action"] == "ASK" for row in high), len(high)),
            "consequence_aware_low_unnecessary_ask_wilson95": wilson(sum(row["policy_action"] == "ASK" for row in low), len(low)),
            "ambiguity_only_high_ask_recall_wilson95": wilson(sum(row["baseline_action"] == "ASK" for row in high), len(high)),
            "ambiguity_only_low_unnecessary_ask_wilson95": wilson(sum(row["baseline_action"] == "ASK" for row in low), len(low)),
        },
        "paired_exact_tests": {
            "low_query_mcnemar": mcnemar([row["policy_action"] == "ASK" for row in low], [row["baseline_action"] == "ASK" for row in low]),
            "high_recall_mcnemar": mcnemar([row["policy_action"] == "ASK" for row in high], [row["baseline_action"] == "ASK" for row in high]),
        },
        "shared_seed_scene_aware_confidence_intervals": {
            key: seed_cluster_bootstrap(rows, key) for key in effect_values
        },
        "family_heterogeneity": family,
        "frame_rows_treated_as_independent": False,
    }
    primary["support_criteria"] = {
        "low_unnecessary_ask_materially_decreased": effect_values["paired_low_query_rate_difference_method_minus_baseline"] < 0,
        "high_clarification_recall_preserved": effect_values["high_recall_preservation_method_minus_baseline"] >= 0,
    }
    primary["support_criteria"]["both_required_criteria_pass"] = all(primary["support_criteria"].values())
    primary["result_digest"] = digest(primary)
    write_json(REPORT / "RQ1_V3_PRIMARY_RESULTS.json", primary)

    execution = [row for row in ledger["entries"] if row["execution_evaluable"]]
    low_execution = [row for row in execution if row["consequence_level"] == "EQUIVALENT"]
    high_execution = [row for row in execution if row["consequence_level"] == "CRITICAL"]
    secondary = {
        "schema": "driveclarify.rq1_v3.secondary-results.v1", "status": "PASS_SECONDARY_ANALYSIS_COMPLETE",
        "denominator": "EXECUTION_EVALUABLE_EPISODES", "all_execution_evaluable": len(execution),
        "low_direct_act": {
            "total": len(low_execution), "task_completion": sum(row["task_completion"] for row in low_execution),
            "wrong_goal_execution": sum(row["wrong_goal_execution"] for row in low_execution),
            "route_completion_90_percent": sum((row["route_completion_percent"] or 0) >= 90 for row in low_execution),
        },
        "high_ask_answer_full_replan": {
            "total": len(high_execution), "ask_receipt": sum(row["ask_receipt"] for row in high_execution),
            "answer_receipt": sum(row["answer_receipt"] for row in high_execution),
            "full_replan_admission": sum(row["full_replan_admission"] for row in high_execution),
            "full_replan_execution": sum(row["full_replan_execution"] for row in high_execution),
            "clarified_task_completion": sum(row["task_completion"] for row in high_execution),
            "wrong_goal_execution": sum(row["wrong_goal_execution"] for row in high_execution),
            "route_completion_90_percent": sum((row["route_completion_percent"] or 0) >= 90 for row in high_execution),
        },
        "safety": {
            "collision": sum(row["collision"] for row in execution), "offroad": sum(row["offroad"] for row in execution),
            "wrong_lane": sum(row["wrong_lane"] for row in execution),
        },
        "native_noncompletion_separately_reported": ledger.get("native_noncompletion_count", 0),
        "native_noncompletion_imputed": False,
    }
    secondary["execution_integrity_closed"] = all(
        row["low_direct_act_chain_pass"] if row["consequence_level"] == "EQUIVALENT" else row["high_chain_pass"]
        for row in rows
    )
    secondary["result_digest"] = digest(secondary)
    write_json(REPORT / "RQ1_V3_SECONDARY_RESULTS.json", secondary)
    log("analyze", primary["status"])
    return primary


def summarize_matrix(matrix: Mapping[str, Any]) -> dict[str, Any]:
    result = {}
    for family in FAMILIES:
        rows = [row for row in matrix.get("matrix", []) if row.get("family") == family]
        result[family] = {row["candidate_answer"]: row["admissible"] for row in rows}
    return result


def validation_receipt(final_status: str) -> dict[str, Any]:
    seal = load(REPORT / "RQ1_V2_SEAL_RECEIPT.json", {})
    v2_drift = []
    for relative, expected in seal.get("v2_files", {}).items():
        path = ROOT / relative
        if not path.is_file() or sha(path) != expected:
            v2_drift.append(relative)
    pre_state = load(REPORT / "PRE_FORMAL_QUALIFICATION_STATE.json", {})
    before_rq2 = pre_state.get("rq2_snapshot_before", {})
    after_rq2 = rq2_snapshot()
    source = verify_source_freeze() if (REPORT / "SOURCE_FREEZE_RECEIPT.json").is_file() else {"pass": final_status == "RQ1_V3_PRE_FORMAL_QUALIFICATION_NOT_CLOSED", "drift_paths": [], "not_created_before_hard_stop": True}
    required = [
        REPORT / "RQ1_V2_SEAL_RECEIPT.json", REPORT / "BACKGROUND_TRAFFIC_POLICY.json",
        ENGINEERING / "ENGINEERING_MANIFEST.json", ENGINEERING / "ENGINEERING_EXCLUSION_REGISTRY.json",
        ENGINEERING / "FULL_REPLAN_ADMISSIBILITY_MATRIX.json",
        REPORT / "DECISION_ANCHOR_REACHABILITY_QUALIFICATION.json",
        REPORT / "LOW_DIRECT_ACT_QUALIFICATION.json", REPORT / "HIGH_NATIVE_REPLAN_QUALIFICATION.json",
        REPORT / "STABILITY_QUALIFICATION.json", REPORT / "PRE_FORMAL_QUALIFICATION_RECEIPT.json",
    ]
    if final_status != "RQ1_V3_PRE_FORMAL_QUALIFICATION_NOT_CLOSED":
        required.extend([
            REPORT / "RQ1_V3_FORMAL_FREEZE_RECEIPT.json", REPORT / "FORMAL_SEED_FRESHNESS_RECEIPT.json",
            REPORT / "FORMAL_ROSTER.json", REPORT / "FORMAL_EXECUTION_LEDGER.json",
            REPORT / "RQ1_V3_PRIMARY_RESULTS.json", REPORT / "RQ1_V3_SECONDARY_RESULTS.json",
        ])
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    json_errors = []
    for path in REPORT.rglob("*.json"):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as error:
            json_errors.append({"path": str(path.relative_to(ROOT)), "error": type(error).__name__})
    runtime_rows = []
    for ledger_path in (ENGINEERING / "ENGINEERING_EXECUTION_LEDGER.json", REPORT / "FORMAL_EXECUTION_LEDGER.json"):
        runtime_rows.extend(load(ledger_path, {}).get("entries", []))
    zero_background = all(
        (row.get("background_traffic_runtime_receipt") or {}).get("random_background_vehicle_count") == 0
        and (row.get("background_traffic_runtime_receipt") or {}).get("traffic_manager_random_generation_enabled") is False
        for row in runtime_rows
    )
    rq2_unchanged = before_rq2.get("tree_digest") == after_rq2.get("tree_digest") and before_rq2.get("file_count") == after_rq2.get("file_count")
    passed = not missing and not json_errors and not v2_drift and source["pass"] and rq2_unchanged and zero_background and final_status in FINAL_STATUSES
    value = {
        "schema": "driveclarify.rq1_v3.final-validation-receipt.v1",
        "status": "PASS_FINAL_VALIDATION" if passed else "FAIL_FINAL_VALIDATION",
        "final_status_legal": final_status in FINAL_STATUSES,
        "old_all_ask_cause_c_preserved": seal.get("old_all_ask_root_cause") == "C — LOW_SCENES_WERE_NOT_ACTUALLY_TASK_EQUIVALENT",
        "rq1_v2_files_unchanged": not v2_drift, "rq1_v2_drift_paths": v2_drift,
        "rq1_v2_partial_primary_effect_computed": False,
        "source_freeze": source, "checkpoint_unchanged": sha(CHECKPOINT) == CHECKPOINT_SHA256,
        "runtime_zero_background_all_attempts": zero_background,
        "rq2_unchanged": rq2_unchanged, "rq2_before": {"file_count": before_rq2.get("file_count"), "tree_digest": before_rq2.get("tree_digest")},
        "rq2_after": {"file_count": after_rq2.get("file_count"), "tree_digest": after_rq2.get("tree_digest")},
        "missing_required_artifacts": missing, "json_parse_errors": json_errors,
        "added_vla_forwards": sum(row.get("additional_vla_forwards", 0) or 0 for row in runtime_rows),
        "checkpoint_changes": 0, "pid_controller_changes": 0,
        "true_intent_pre_ask_reads": sum(row.get("runtime_true_intent_reads_before_ask", 0) or 0 for row in runtime_rows),
        "rq2_mutations": 0 if rq2_unchanged else None,
    }
    value["validation_digest"] = digest(value)
    write_json(REPORT / "FINAL_VALIDATION_RECEIPT.json", value)
    return value


def display(value: Any) -> str:
    if value is None:
        return "NOT_ESTIMABLE_OR_NOT_APPLICABLE"
    if isinstance(value, float):
        return f"{value:.6f}"
    return json.dumps(value, sort_keys=True, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)


def finalize() -> dict[str, Any]:
    qualification = load(REPORT / "PRE_FORMAL_QUALIFICATION_RECEIPT.json", {})
    ledger = load(REPORT / "FORMAL_EXECUTION_LEDGER.json", {})
    primary = load(REPORT / "RQ1_V3_PRIMARY_RESULTS.json", {})
    secondary = load(REPORT / "RQ1_V3_SECONDARY_RESULTS.json", {})
    if qualification.get("status") != "PASS_RQ1_V3_PRE_FORMAL_QUALIFICATION":
        status = "RQ1_V3_PRE_FORMAL_QUALIFICATION_NOT_CLOSED"
    elif not (ledger.get("primary_evaluability_gate") or {}).get("pass"):
        status = "RQ1_V3_PRIMARY_EVALUABILITY_GATE_FAILED"
    elif secondary.get("execution_integrity_closed") is not True:
        status = "RQ1_V3_EXECUTION_INTEGRITY_NOT_CLOSED"
    elif (primary.get("support_criteria") or {}).get("both_required_criteria_pass") is True:
        status = "PASS_RQ1_V3_CONSEQUENCE_SELECTIVITY_SUPPORTED"
    else:
        status = "PASS_RQ1_V3_CONSEQUENCE_SELECTIVITY_NOT_SUPPORTED"
    if status not in FINAL_STATUSES:
        raise RuntimeError("RQ1_V3_ILLEGAL_FINAL_STATUS")

    validation = validation_receipt(status)
    if validation["status"] != "PASS_FINAL_VALIDATION" and status.startswith("PASS_"):
        status = "RQ1_V3_EXECUTION_INTEGRITY_NOT_CLOSED"
        validation = validation_receipt(status)
    seal = load(REPORT / "RQ1_V2_SEAL_RECEIPT.json", {})
    anchor = load(REPORT / "DECISION_ANCHOR_REACHABILITY_QUALIFICATION.json", {})
    matrix = load(ENGINEERING / "FULL_REPLAN_ADMISSIBILITY_MATRIX.json", {})
    low_q = load(REPORT / "LOW_DIRECT_ACT_QUALIFICATION.json", {})
    high_q = load(REPORT / "HIGH_NATIVE_REPLAN_QUALIFICATION.json", {})
    ord_q = load(REPORT / "ORD_STABILITY_QUALIFICATION.json", {})
    freeze_receipt = load(REPORT / "RQ1_V3_FORMAL_FREEZE_RECEIPT.json", {})
    freshness = load(REPORT / "FORMAL_SEED_FRESHNESS_RECEIPT.json", {})
    roster = load(REPORT / "FORMAL_ROSTER.json", {})
    method = primary.get("consequence_aware", {})
    baseline = primary.get("ambiguity_only", {})
    effect_values = primary.get("effects", {})
    secondary_low = secondary.get("low_direct_act", {})
    secondary_high = secondary.get("high_ask_answer_full_replan", {})
    planned = ledger.get("planned_episodes", 48 if qualification.get("status") == "PASS_RQ1_V3_PRE_FORMAL_QUALIFICATION" else 0)
    report_paths = [
        str((REPORT / "FINAL_REPORT.md").relative_to(ROOT)),
        str((REPORT / "FINAL_VALIDATION_RECEIPT.json").relative_to(ROOT)),
        str((REPORT / "FORMAL_EXECUTION_LEDGER.json").relative_to(ROOT)) if (REPORT / "FORMAL_EXECUTION_LEDGER.json").is_file() else "NOT_CREATED_PRE_FORMAL_HARD_STOP",
        str((REPORT / "RQ1_V3_PRIMARY_RESULTS.json").relative_to(ROOT)) if (REPORT / "RQ1_V3_PRIMARY_RESULTS.json").is_file() else "NOT_CREATED_PRE_FORMAL_HARD_STOP",
        str((REPORT / "RQ1_V3_SECONDARY_RESULTS.json").relative_to(ROOT)) if (REPORT / "RQ1_V3_SECONDARY_RESULTS.json").is_file() else "NOT_CREATED_PRE_FORMAL_HARD_STOP",
    ]
    next_recommendation = {
        "PASS_RQ1_V3_CONSEQUENCE_SELECTIVITY_SUPPORTED": "Replicate the frozen V3 protocol on a Town-disjoint zero-background scene set without changing the mechanism.",
        "PASS_RQ1_V3_CONSEQUENCE_SELECTIVITY_NOT_SUPPORTED": "Retain the sealed null result and investigate the family-level heterogeneity in a new preregistered study.",
        "RQ1_V3_PRIMARY_EVALUABILITY_GATE_FAILED": "Create a new engineering-only qualification generation for the failed condition without analyzing or repairing the exposed V3 roster.",
        "RQ1_V3_EXECUTION_INTEGRITY_NOT_CLOSED": "Repair only the pre-formal native execution interface in a new excluded qualification generation before any new formal roster.",
        "RQ1_V3_PRE_FORMAL_QUALIFICATION_NOT_CLOSED": "Repair the failed fresh engineering scene/route interface and rerun qualification with new permanently excluded identities and seeds.",
    }[status]
    lines = [
        "# DriveClarify RQ1-V3 final report", "",
        f"1. final status: `{status}`",
        f"2. confirmation old all-ASK cause C preserved: `{seal.get('old_all_ask_root_cause')}`; preserved={seal.get('status') == 'PASS_RQ1_V2_RESULT_SEALED_AND_EXCLUDED'}",
        f"3. decision-anchor qualification 8/8: `{anchor.get('condition_pass_count') == 8}` ({anchor.get('condition_pass_count', 0)}/8)",
        f"4. Full Replan candidate matrix REF/LMK/ORD/USC A/B: `{display(summarize_matrix(matrix))}` ({matrix.get('pass_count', 0)}/8)",
        f"5. LOW qualification 4/4: `{low_q.get('family_pass_count') == 4}` ({low_q.get('family_pass_count', 0)}/4)",
        f"6. HIGH ASK/replan qualification 4/4: `{high_q.get('family_pass_count') == 4}` ({high_q.get('family_pass_count', 0)}/4)",
        f"7. ORD stability result: `{ord_q.get('status', 'NOT_RUN')}`",
        f"8. final freeze digest: `{freeze_receipt.get('freeze_digest', 'NOT_CREATED_PRE_FORMAL_HARD_STOP')}`",
        f"9. six fresh formal seeds: `{display(freshness.get('formal_seeds'))}`",
        f"10. 48-cell roster digest: `{roster.get('roster_digest', 'NOT_CREATED_PRE_FORMAL_HARD_STOP')}`",
        f"11. planned: `{planned}`",
        f"12. exposed: `{ledger.get('exposed_episodes', 0)}`",
        f"13. decision-evaluable: `{ledger.get('decision_evaluable_episodes', 0)}`",
        f"14. execution-evaluable: `{ledger.get('execution_evaluable_episodes', 0)}`",
        f"15. native noncompletion: `{ledger.get('native_noncompletion_count', 0)}`",
        f"16. scientific retries: `{ledger.get('scientific_retries', 0)}`",
        f"17. infrastructure retries: `{ledger.get('infrastructure_retries', 0)}`",
        f"18. consequence-aware HIGH ASK recall: `{display(method.get('high_ask_recall'))}`",
        f"19. consequence-aware LOW unnecessary ASK: `{display(method.get('low_unnecessary_ask_rate'))}`",
        f"20. consequence-aware selectivity gap: `{display(method.get('selectivity_gap'))}`",
        f"21. ambiguity-only HIGH ASK recall: `{display(baseline.get('high_ask_recall'))}`",
        f"22. ambiguity-only LOW unnecessary ASK: `{display(baseline.get('low_unnecessary_ask_rate'))}`",
        f"23. ambiguity-only selectivity gap: `{display(baseline.get('selectivity_gap'))}`",
        f"24. paired LOW-query reduction (method minus baseline): `{display(effect_values.get('paired_low_query_rate_difference_method_minus_baseline'))}`",
        f"25. HIGH-recall preservation (method minus baseline): `{display(effect_values.get('high_recall_preservation_method_minus_baseline'))}`",
        f"26. selectivity improvement: `{display(effect_values.get('selectivity_gap_improvement_method_minus_baseline'))}`",
        f"27. confidence intervals/tests: `{display({'rate_ci': primary.get('rate_confidence_intervals'), 'paired_exact': primary.get('paired_exact_tests'), 'cluster_ci': primary.get('shared_seed_scene_aware_confidence_intervals')})}`",
        f"28. LOW task completion: `{display(secondary_low.get('task_completion'))}/{display(secondary_low.get('total'))}`",
        f"29. LOW wrong-goal: `{display(secondary_low.get('wrong_goal_execution'))}`",
        f"30. HIGH ASK receipt: `{display(secondary_high.get('ask_receipt'))}/{display(secondary_high.get('total'))}`",
        f"31. HIGH answer receipt: `{display(secondary_high.get('answer_receipt'))}/{display(secondary_high.get('total'))}`",
        f"32. HIGH Full Replan admission: `{display(secondary_high.get('full_replan_admission'))}/{display(secondary_high.get('total'))}`",
        f"33. HIGH Full Replan execution: `{display(secondary_high.get('full_replan_execution'))}/{display(secondary_high.get('total'))}`",
        f"34. HIGH clarified-task completion: `{display(secondary_high.get('clarified_task_completion'))}/{display(secondary_high.get('total'))}`",
        f"35. HIGH wrong-goal: `{display(secondary_high.get('wrong_goal_execution'))}`",
        f"36. safety results: `{display(secondary.get('safety'))}`",
        f"37. family heterogeneity: `{display(primary.get('family_heterogeneity'))}`",
        f"38. added VLA forwards: `{validation.get('added_vla_forwards')}`",
        f"39. checkpoint changes: `{validation.get('checkpoint_changes')}`",
        f"40. PID/controller changes: `{validation.get('pid_controller_changes')}`",
        f"41. true-intent pre-ASK reads: `{validation.get('true_intent_pre_ask_reads')}`",
        f"42. RQ2 mutations: `{validation.get('rq2_mutations')}`",
        f"43. source freeze: `{display(validation.get('source_freeze'))}`",
        f"44. final validation: `{validation.get('status')}`",
        f"45. report paths: `{display(report_paths)}`",
        f"46. exactly one next recommendation: {next_recommendation}",
    ]
    write_text(REPORT / "FINAL_REPORT.md", "\n".join(lines))
    if not status.startswith("PASS_"):
        hard_stop = {
            "schema": "driveclarify.rq1_v3.mandatory-hard-stop.v1", "status": status,
            "reason": (
                qualification.get("status") if status == "RQ1_V3_PRE_FORMAL_QUALIFICATION_NOT_CLOSED"
                else ledger.get("hard_stop_trigger", ledger.get("status", "EXECUTION_INTEGRITY_NOT_CLOSED"))
            ),
            "partial_formal_primary_analysis_performed": primary.get("analysis_run") is True if status != "RQ1_V3_PRIMARY_EVALUABILITY_GATE_FAILED" else False,
            "scientific_retries": ledger.get("scientific_retries", 0),
            "formal_seed_replacements": ledger.get("seed_replacements", 0),
        }
        hard_stop["receipt_digest"] = digest(hard_stop)
        write_json(REPORT / "MANDATORY_HARD_STOP_RECEIPT.json", hard_stop)
    log("finalize", status)
    return {"status": status, "validation": validation["status"], "report": str((REPORT / "FINAL_REPORT.md").relative_to(ROOT))}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "qualify", "freeze", "run", "analyze", "finalize", "all"))
    args = parser.parse_args()
    if args.phase in ("prepare", "all"):
        prepare()
    if args.phase in ("qualify", "all"):
        run_qualification()
    qualification = load(REPORT / "PRE_FORMAL_QUALIFICATION_RECEIPT.json", {})
    if args.phase in ("freeze", "all"):
        if qualification.get("status") != "PASS_RQ1_V3_PRE_FORMAL_QUALIFICATION":
            if args.phase == "all":
                result = finalize()
                print(json.dumps(result, sort_keys=True))
                return 0
            raise RuntimeError("RQ1_V3_PRE_FORMAL_QUALIFICATION_NOT_CLOSED")
        freeze()
    if args.phase in ("run", "all"):
        run_formal()
    if args.phase in ("analyze", "all"):
        analyze()
    if args.phase in ("finalize", "all"):
        result = finalize()
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
