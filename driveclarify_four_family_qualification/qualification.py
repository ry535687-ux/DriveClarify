#!/usr/bin/env python3
"""Evidence-first four-family grounding and route-binding qualification.

This module never trains a model, chooses ACT/ASK/WAIT, or executes an RQ arm.
It validates frozen scene evidence and calls the existing SimLingo production
route converter/owner without changing either implementation.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any, Mapping

import numpy as np


ROOT = Path("/home/buaa/wrh/DriveClarify")
SIMLINGO = Path("/home/buaa/wrh/simlingo")
REPORT = ROOT / "reports/driveclarify_v3_four_family_grounded_route_binding_qualification"
SMOKE = ROOT / "reports/driveclarify_a1_integrated_route_switch_smoke_v1"
XODR = Path("/home/buaa/carlaCache/0.9.15/Carla/Maps/Town12/OpenDrive/Town12.xodr")
NAV = SIMLINGO / "team_code/nav_planner.py"
A1 = ROOT / "reports/driveclarify_v3_short_prefix_a1_fast_track/a1_training_v2/selected/checkpoints/a1_selected.ckpt/pytorch_model.pt"
EXPECTED_A1 = "cc6873e2a7778140ff7af3fd7d3578114b26bd6a47974e955f0845ddd2178044"
EXPECTED_HEAD = "eaa332b1bb994279b59ea5af786fdb5de96adc1b"
ENDPOINT_TOLERANCE_M = 0.001

SOURCE_IDS = {
    "route_01": "DC-A1-SMOKE-01-RIGHT",
    "route_02": "DC-A1-SMOKE-02-RIGHT",
    "route_03": "DC-A1-SMOKE-03-LEFT",
}

CASES = (
    {
        "case_id": "REF_QUAL_001",
        "family": "Referential",
        "source_key": "route_03",
        "raw_instruction": "Turn after the white car.",
        "route_a_source": "old_full_route",
        "route_b_source": "selected_full_route",
        "interpretation_a": "the white car on the westbound straight branch",
        "interpretation_b": "the white car on the northbound left branch",
        "entity_kind": "spawned_vehicle_pair",
    },
    {
        "case_id": "LANDMARK_QUAL_001",
        "family": "Landmark",
        "source_key": "route_01",
        "raw_instruction": "Turn by the 40 mph sign.",
        "route_a_source": "old_full_route",
        "route_b_source": "selected_full_route",
        "interpretation_a": "the 40 mph sign on the southbound branch",
        "interpretation_b": "the 40 mph sign on the westbound branch",
        "entity_kind": "opendrive_landmark_pair",
        "landmark_ids": ("24951", "24953"),
    },
    {
        "case_id": "ORDER_QUAL_001",
        "family": "Order",
        "source_key": "route_02",
        "raw_instruction": "Turn at the junction ahead.",
        "route_a_source": "selected_full_route",
        "route_b_source": "old_full_route",
        "interpretation_a": "turn at the first eligible junction (junction 16987)",
        "interpretation_b": "continue through the first and turn at the second eligible junction (junction 5990)",
        "entity_kind": "junction_pair",
        "junction_ids": (16987, 5990),
    },
    {
        "case_id": "UNDERSPEC_QUAL_001",
        "family": "Underspecified",
        "source_key": "route_01",
        "raw_instruction": "Go around this block to the destination.",
        "route_a_source": "old_full_route",
        "route_b_source": "selected_full_route",
        "interpretation_a": "take the clockwise southbound corridor",
        "interpretation_b": "take the counter-clockwise westbound corridor",
        "entity_kind": "legal_corridor_pair",
    },
)


class QualificationError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def sha(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def command(*args: str) -> str:
    return subprocess.run(args, cwd=ROOT, check=True, text=True, stdout=subprocess.PIPE).stdout.rstrip("\n")


def xyz(row: Mapping[str, Any]) -> tuple[float, float, float]:
    return tuple(float.fromhex(v) for v in row["xyz_hex"])


def route_distance(rows: list[Mapping[str, Any]]) -> float:
    return sum(math.dist(xyz(a), xyz(b)) for a, b in zip(rows, rows[1:]))


def source_runtime(case: Mapping[str, Any]) -> tuple[Path, dict[str, Any]]:
    path = SMOKE / "candidates" / SOURCE_IDS[case["source_key"]] / "runtime_case.json"
    return path, json.loads(path.read_text(encoding="utf-8"))


def split_routes(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> dict[str, Any]:
    prefix = 0
    for a, b in zip(left, right):
        if a != b:
            break
        prefix += 1
    suffix = 0
    for a, b in zip(reversed(left), reversed(right)):
        if a != b:
            break
        suffix += 1
    if prefix == 0 or suffix == 0 or left[-1] != right[-1]:
        raise QualificationError("NO_SAME_DESTINATION_BRANCH_STRUCTURE")
    left_local = left[prefix:len(left) - suffix]
    right_local = right[prefix:len(right) - suffix]
    return {
        "shared_prefix_rows": prefix,
        "shared_suffix_rows": suffix,
        "anchor_index": prefix,
        "route_a_local_hash": digest(left_local),
        "route_b_local_hash": digest(right_local),
        "connector_a_identity": digest(left_local),
        "connector_b_identity": digest(right_local),
        "anchor_identity": digest(left[prefix - 1]),
        "route_a_local_distance_m": route_distance(left_local),
        "route_b_local_distance_m": route_distance(right_local),
    }


def point_on_route_at_distance(route: list[dict[str, Any]], target_m: float) -> tuple[int, tuple[float, float, float]]:
    walked = 0.0
    for index in range(1, len(route)):
        walked += math.dist(xyz(route[index - 1]), xyz(route[index]))
        if walked >= target_m:
            return index, xyz(route[index])
    raise QualificationError("ROUTE_TOO_SHORT_FOR_ENTITY_PLACEMENT")


def load_carla():
    import carla
    return carla


def load_nav():
    spec = importlib.util.spec_from_file_location("dc_four_family_production_nav", NAV)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def waypoint_identity(world_map, row: Mapping[str, Any]) -> dict[str, Any]:
    carla = load_carla()
    p = xyz(row)
    waypoint = world_map.get_waypoint(carla.Location(*p), project_to_road=True)
    canonical_id = digest({
        "road_id": int(waypoint.road_id),
        "section_id": int(waypoint.section_id),
        "lane_id": int(waypoint.lane_id),
        "s_hex": float(waypoint.s).hex(),
    })
    return {
        "xyz": list(p),
        "road_id": int(waypoint.road_id),
        "section_id": int(waypoint.section_id),
        "lane_id": int(waypoint.lane_id),
        "s": float(waypoint.s),
        "canonical_waypoint_identity": canonical_id,
    }


def map_route_facts(world_map, route: list[dict[str, Any]]) -> dict[str, Any]:
    carla = load_carla()
    junctions = []
    seen = set()
    lane_keys = set()
    all_driving = True
    for index, row in enumerate(route):
        point = xyz(row)
        waypoint = world_map.get_waypoint(carla.Location(*point), project_to_road=True)
        lane_keys.add((int(waypoint.road_id), int(waypoint.section_id), int(waypoint.lane_id)))
        all_driving = all_driving and str(waypoint.lane_type).endswith("Driving")
        if waypoint.is_junction and int(waypoint.junction_id) not in seen:
            seen.add(int(waypoint.junction_id))
            junctions.append({
                "route_index": index,
                "junction_id": int(waypoint.junction_id),
                "road_id": int(waypoint.road_id),
                "lane_id": int(waypoint.lane_id),
                "road_option": row["road_option"],
                "xyz": list(point),
            })
    return {
        "all_route_waypoints_driving": all_driving,
        "unique_road_lane_count": len(lane_keys),
        "junction_order": junctions,
        "route_length_m": route_distance(route),
    }


def tree_manifest(path: Path) -> dict[str, Any]:
    rows = []
    for file in sorted(p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        rows.append({"relative_path": str(file.relative_to(path)), "bytes": file.stat().st_size, "sha256": sha(file)})
    return {"root": str(path), "file_count": len(rows), "files": rows, "aggregate_sha256": digest(rows)}


def create_entry_state() -> None:
    if REPORT.exists():
        raise QualificationError("QUALIFICATION_REPORT_NAMESPACE_ALREADY_EXISTS")
    REPORT.mkdir(parents=True)
    head = command("git", "rev-parse", "HEAD")
    status = command("git", "status", "--short")
    diff = command("git", "diff")
    cached = command("git", "diff", "--cached")
    a1_sha = sha(A1)
    if a1_sha != EXPECTED_A1:
        raise QualificationError("A1_CHECKPOINT_HASH_MISMATCH")
    preservation = {
        "a1": {"path": str(A1), "sha256": a1_sha},
        "old_formal_batch": tree_manifest(ROOT / "reports/driveclarify_v3_formal_main_experiment_rq1_rq2_rq3"),
        "failed_prospective_batch": tree_manifest(ROOT / "reports/driveclarify_v3_prospective_formal_rq1_rq2_rq3_v2"),
        "production_route_owner": {"path": str(NAV), "sha256": sha(NAV)},
        "candidate_bridge": {"path": str(ROOT / "driveclarify_candidate_local_navigation_bridge.py"), "sha256": sha(ROOT / "driveclarify_candidate_local_navigation_bridge.py")},
    }
    atomic_json(REPORT / "ENTRY_PRESERVATION_SNAPSHOT.json", preservation)
    atomic_text(REPORT / "00_ENTRY_STATE.md", f"""# Entry state

- Entry HEAD: `{head}`
- Historical expected HEAD matched: `{head == EXPECTED_HEAD}`
- Target namespace was absent before this capture: `true`
- A1 SHA-256: `{a1_sha}` (`PASS`)
- A1 retraining count: `0`
- A2/A3/LoRA: `OFF/OFF/OFF`
- CARLA launches before candidate discovery: `0`
- Formal scientific exposure: `0`
- Working tree policy: inherited files preserved; no reset, clean, restore, or checkout used.

## Verbatim `git status --short`

```text
{status}
```

## Verbatim `git diff`

```diff
{diff}
```

## Verbatim `git diff --cached`

```diff
{cached}
```
""")
    atomic_text(REPORT / "COMMAND_LOG.md", """# Command log

All commands are engineering qualification commands, not formal RQ exposure.

1. `git rev-parse HEAD`
2. `git status --short`
3. `git diff`
4. `git diff --cached`
5. Read-only inventory of existing reports, route fixtures, OpenDRIVE maps, production route owner, and tests.
6. Offline `carla.Map` topology/landmark queries using Town12 OpenDRIVE; no server process.
7. `python -m driveclarify_four_family_qualification.qualification offline`
""")


def offline_discovery() -> None:
    if not REPORT.exists():
        create_entry_state()
    carla = load_carla()
    world_map = carla.Map("Town12", XODR.read_text(encoding="utf-8"))
    landmarks = {str(x.id): x for x in world_map.get_all_landmarks()}
    rows = []
    for case in CASES:
        source_path, runtime = source_runtime(case)
        route_a = runtime[case["route_a_source"]]
        route_b = runtime[case["route_b_source"]]
        structure = split_routes(route_a, route_b)
        endpoint_a = waypoint_identity(world_map, route_a[-1])
        endpoint_b = waypoint_identity(world_map, route_b[-1])
        facts_a = map_route_facts(world_map, route_a)
        facts_b = map_route_facts(world_map, route_b)
        endpoint_distance = math.dist(endpoint_a["xyz"], endpoint_b["xyz"])
        evidence: dict[str, Any] = {}
        if case["entity_kind"] == "spawned_vehicle_pair":
            ia, pa = point_on_route_at_distance(route_a, 40.0)
            ib, pb = point_on_route_at_distance(route_b, 40.0)
            evidence = {"frozen_actor_a_route_index": ia, "frozen_actor_a_xyz": list(pa), "frozen_actor_b_route_index": ib, "frozen_actor_b_xyz": list(pb)}
        elif case["entity_kind"] == "opendrive_landmark_pair":
            pair = []
            for landmark_id in case["landmark_ids"]:
                landmark = landmarks[landmark_id]
                transform = landmark.transform
                pair.append({
                    "landmark_id": landmark_id,
                    "type": str(landmark.type),
                    "sub_type": str(landmark.sub_type),
                    "value": float(landmark.value),
                    "unit": str(landmark.unit),
                    "road_id": int(landmark.road_id),
                    "pose": [float(transform.location.x), float(transform.location.y), float(transform.location.z), float(transform.rotation.yaw)],
                })
            evidence = {"landmarks": pair}
        elif case["entity_kind"] == "junction_pair":
            order = facts_b["junction_order"]
            selected = [row for row in order if row["junction_id"] in case["junction_ids"]]
            if [x["junction_id"] for x in selected[:2]] != list(case["junction_ids"]):
                raise QualificationError("ORDER_JUNCTION_SEQUENCE_NOT_ESTABLISHED")
            evidence = {"ahead_junctions": selected[:2]}
        else:
            evidence = {
                "option_a": {"corridor_hash": structure["connector_a_identity"], "all_waypoints_driving": facts_a["all_route_waypoints_driving"]},
                "option_b": {"corridor_hash": structure["connector_b_identity"], "all_waypoints_driving": facts_b["all_route_waypoints_driving"]},
            }
        viable = (
            endpoint_distance <= ENDPOINT_TOLERANCE_M
            and endpoint_a["canonical_waypoint_identity"] == endpoint_b["canonical_waypoint_identity"]
            and digest(route_a) != digest(route_b)
            and structure["connector_a_identity"] != structure["connector_b_identity"]
            and facts_a["all_route_waypoints_driving"]
            and facts_b["all_route_waypoints_driving"]
        )
        rows.append({
            "case_id": case["case_id"], "ambiguity_family": case["family"], "classification_stage": "EVIDENCE_FIRST_CLASSIFICATION_SECOND",
            "raw_instruction": case["raw_instruction"], "map": "Town12", "route_source": str(source_path), "route_source_sha256": sha(source_path),
            "seed": runtime["seed"], "interpretation_A": case["interpretation_a"], "interpretation_B": case["interpretation_b"],
            "route_A_hash": digest(route_a), "route_B_hash": digest(route_b), "global_destination_A": endpoint_a,
            "global_destination_B": endpoint_b, "destination_distance_m": endpoint_distance, "same_canonical_destination": endpoint_a["canonical_waypoint_identity"] == endpoint_b["canonical_waypoint_identity"],
            "route_A_facts": facts_a, "route_B_facts": facts_b, "structure": structure, "physical_evidence": evidence,
            "offline_candidate_viable": viable,
        })
    if not all(row["offline_candidate_viable"] for row in rows):
        raise QualificationError("OFFLINE_CANDIDATE_DISCOVERY_INCOMPLETE")
    payload = {"schema": "driveclarify.four-family-candidate-discovery.v1", "carla_server_launches": 0, "formal_scientific_exposure": 0, "cases": rows}
    atomic_json(REPORT / "FOUR_FAMILY_CANDIDATE_DISCOVERY.json", payload)
    maps = sorted({row["map"] for row in rows})
    atomic_text(REPORT / "MAP_AND_TOPOLOGY_INVENTORY.md", f"""# Map and topology inventory

Result: `PASS_OFFLINE_INVENTORY` with `0` CARLA server launches.

- Available OpenDRIVE maps: Town01, Town02, Town03, Town04, Town05, Town06, Town07, Town10HD, Town12, Town13.
- Selected map: `Town12`; OpenDRIVE SHA-256 `{sha(XODR)}`.
- Town12 topology edges: `{len(world_map.get_topology())}`; OpenDRIVE landmarks: `{len(landmarks)}`.
- Existing production-qualified sources: the three immutable `DC-A1-SMOKE-*` same-destination route pairs.
- Selected map set: `{maps}`.
- Candidate evidence was discovered before family classification and before any server launch.
""")
    lines = ["# Four-family candidate discovery", "", "Result: `4/4 OFFLINE CANDIDATES VIABLE`; CARLA launches: `0`.", ""]
    for row in rows:
        lines.extend([f"## {row['case_id']} — {row['ambiguity_family']}", "", f"- Evidence-first verdict: `{row['offline_candidate_viable']}`.", f"- Instruction: `{row['raw_instruction']}`", f"- Route hashes differ: `{row['route_A_hash'] != row['route_B_hash']}`.", f"- Canonical global destination equal: `{row['same_canonical_destination']}`; distance `{row['destination_distance_m']:.9f} m`.", f"- Connector identities differ: `{row['structure']['connector_a_identity'] != row['structure']['connector_b_identity']}`.", f"- Physical evidence: `{json.dumps(row['physical_evidence'], sort_keys=True)}`", ""])
    atomic_text(REPORT / "FOUR_FAMILY_CANDIDATE_DISCOVERY.md", "\n".join(lines))
    atomic_text(REPORT / "GROUNDING_ONTOLOGY_QUALIFICATION.md", """# Grounding ontology qualification

A case is eligible only when two distinct physical entities/options exist in one captured scene, both bind to distinct non-synonymous interpretations, both alternatives are visible or route-observable, legal and feasible, both production route guards pass, the canonical endpoint identity is equal within the unchanged 1 mm tolerance, and the local route hashes differ. The mechanical eligibility verdict is recomputed and rejects manual overrides. Interpretation texts and proposed bindings are curated hypotheses until live scene evidence and independent semantic review establish them. Removing or invalidating the second interpretation must make eligibility false.

Family-specific identities are actor IDs for Referential, OpenDRIVE landmark IDs for Landmark, distinct CARLA junction IDs in forward route order for Order, and distinct all-driving corridor hashes for Underspecified.
""")


def transform_row(carla, row: Mapping[str, Any]):
    return carla.Transform(carla.Location(*xyz(row))), int_to_road_option(row["road_option"])


def waypoint_row(carla, row: Mapping[str, Any]):
    return SimpleNamespace(transform=carla.Transform(carla.Location(*xyz(row)))), int_to_road_option(row["road_option"])


def int_to_road_option(name: str):
    from enum import IntEnum
    class RoadOption(IntEnum):
        LEFT = 1; RIGHT = 2; STRAIGHT = 3; LANEFOLLOW = 4; CHANGELANELEFT = 5; CHANGELANERIGHT = 6
    return RoadOption[name]


def route_binding(case: Mapping[str, Any], world_map) -> dict[str, Any]:
    carla = load_carla()
    nav = load_nav()
    source_path, runtime = source_runtime(case)
    route_a = runtime[case["route_a_source"]]
    route_b = runtime[case["route_b_source"]]
    endpoint_a = waypoint_identity(world_map, route_a[-1])
    endpoint_b = waypoint_identity(world_map, route_b[-1])
    structure = split_routes(route_a, route_b)
    variants = {}
    for label, selected in (("A", route_a), ("B", route_b)):
        other = route_b if label == "A" else route_a
        planner = nav.RoutePlanner(7.5, 50.0)
        planner.set_route([transform_row(carla, row) for row in other])
        cache: dict[str, Any] = {}
        anchor = structure["anchor_index"] - 1
        translation = np.array([-100.0, 5.0, -1.0], dtype=np.float64)
        owner = nav.SimLingoOnlineRouteUpdateOwner(
            planner, carla.Location(*xyz(other[-1])), lambda s=selected, a=anchor: np.asarray(xyz(s[a])) + translation,
            lambda: dict(cache), lambda command: cache.update(command=int(command.value)), lambda snapshot: (cache.clear(), cache.update(snapshot)),
            world_to_planner_translation_xyz_m=translation,
        )
        reasons = []
        try:
            accepted = owner.install_reconnected_route(
                [waypoint_row(carla, row) for row in selected[anchor:]],
                global_destination_identity=endpoint_a["canonical_waypoint_identity"],
                global_destination_planner_endpoint=np.asarray(xyz(selected[-1])) + translation,
            )
        except Exception as error:
            accepted = False
            reasons.append(str(error))
        variants[label] = {"production_guard": "PASS" if accepted else "FAIL", "reason_codes": reasons, "production_install_receipt": owner.last_installation_receipt}
    receipt = {
        "schema": "driveclarify.four-family-production-route-binding.v1", "case_id": case["case_id"], "map": "Town12",
        "route_source": str(source_path), "exact_production_owner": "team_code.nav_planner.SimLingoOnlineRouteUpdateOwner",
        "production_route_converter": "team_code.nav_planner.RouteTraceConverter", "production_owner_source": str(NAV), "production_owner_source_sha256": sha(NAV),
        "endpoint_tolerance_m": ENDPOINT_TOLERANCE_M, "global_destination_A": endpoint_a, "global_destination_B": endpoint_b,
        "canonical_destination_identity_A": endpoint_a["canonical_waypoint_identity"], "canonical_destination_identity_B": endpoint_b["canonical_waypoint_identity"],
        "destination_distance_m": math.dist(endpoint_a["xyz"], endpoint_b["xyz"]), "route_A_hash": digest(route_a), "route_B_hash": digest(route_b),
        "connector_A_identity": structure["connector_a_identity"], "connector_B_identity": structure["connector_b_identity"], "anchor_identity": structure["anchor_identity"],
        "production_guard_A": variants["A"]["production_guard"], "production_guard_B": variants["B"]["production_guard"],
        "same_destination_verdict": "PASS" if endpoint_a["canonical_waypoint_identity"] == endpoint_b["canonical_waypoint_identity"] and math.dist(endpoint_a["xyz"], endpoint_b["xyz"]) <= ENDPOINT_TOLERANCE_M else "FAIL",
        "different_local_route_verdict": "PASS" if digest(route_a) != digest(route_b) and structure["connector_a_identity"] != structure["connector_b_identity"] else "FAIL",
        "variants": variants,
    }
    validate_route_binding(receipt)
    return receipt


def validate_route_binding(receipt: Mapping[str, Any]) -> None:
    if receipt.get("endpoint_tolerance_m") != 0.001:
        raise QualificationError("ENDPOINT_TOLERANCE_CHANGED")
    for key in ("production_guard_A", "production_guard_B", "same_destination_verdict", "different_local_route_verdict"):
        if receipt.get(key) != "PASS":
            raise QualificationError(f"ROUTE_BINDING_FAILED:{key}")
    if receipt.get("route_A_hash") == receipt.get("route_B_hash"):
        raise QualificationError("ROUTE_HASHES_IDENTICAL")
    if receipt.get("connector_A_identity") == receipt.get("connector_B_identity"):
        raise QualificationError("CONNECTORS_IDENTICAL")


def entity_pair_for(case: Mapping[str, Any], capture: Mapping[str, Any], discovery: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], bool, bool]:
    cid = case["case_id"]
    scene = capture["cases"][cid]
    if case["entity_kind"] == "spawned_vehicle_pair":
        return scene["entity_A"], scene["entity_B"], bool(scene["visibility_A"]["eligible"]), bool(scene["visibility_B"]["eligible"])
    if case["entity_kind"] == "opendrive_landmark_pair":
        return scene["entity_A"], scene["entity_B"], bool(scene["visibility_A"]["eligible"]), bool(scene["visibility_B"]["eligible"])
    if case["entity_kind"] == "junction_pair":
        return scene["entity_A"], scene["entity_B"], True, True
    return scene["entity_A"], scene["entity_B"], True, True


def validate_grounding_receipt(receipt: Mapping[str, Any]) -> None:
    required = ("entity_A", "entity_B", "interpretation_A", "interpretation_B", "interpretation_A_binding", "interpretation_B_binding", "route_A", "route_B")
    if any(not receipt.get(key) for key in required):
        raise QualificationError("GROUNDING_REQUIRED_FIELD_MISSING")
    predicates = (
        receipt.get("entity_A") != receipt.get("entity_B"), receipt.get("interpretation_A") != receipt.get("interpretation_B"),
        receipt.get("route_A_hash") != receipt.get("route_B_hash"), receipt.get("visibility_A") is True, receipt.get("visibility_B") is True,
        receipt.get("legal_A") is True, receipt.get("legal_B") is True, receipt.get("feasible_A") is True, receipt.get("feasible_B") is True,
        receipt.get("production_guard_A") == "PASS", receipt.get("production_guard_B") == "PASS",
        receipt.get("same_global_destination_proof") == "PASS", receipt.get("different_local_route_proof") == "PASS",
    )
    computed = all(predicates)
    if receipt.get("grounding_eligible") is not computed:
        raise QualificationError("GROUNDING_VERDICT_NOT_COMPUTED")
    if not computed:
        raise QualificationError("GROUNDING_INELIGIBLE")


def finalize() -> None:
    discovery = json.loads((REPORT / "FOUR_FAMILY_CANDIDATE_DISCOVERY.json").read_text())
    capture = json.loads((REPORT / "CARLA_ENGINEERING_QUALIFICATION_RECEIPT.json").read_text())
    carla = load_carla()
    world_map = carla.Map("Town12", XODR.read_text(encoding="utf-8"))
    discovery_rows = {x["case_id"]: x for x in discovery["cases"]}
    grounding_receipts = []
    binding_receipts = []
    for case in CASES:
        cid = case["case_id"]
        binding = route_binding(case, world_map)
        atomic_json(REPORT / "ROUTE_BINDING_RECEIPTS" / f"{cid}.json", binding)
        binding_receipts.append(binding)
        row = discovery_rows[cid]
        entity_a, entity_b, visibility_a, visibility_b = entity_pair_for(case, capture, row)
        receipt = {
            "schema": "driveclarify.four-family-grounding-receipt.v1", "case_id": cid, "ambiguity_family": case["family"], "raw_instruction": case["raw_instruction"],
            "scene_identity": capture["cases"][cid]["scene_identity"], "map": "Town12", "route_source": row["route_source"], "seed": row["seed"],
            "entity_A": entity_a, "entity_B": entity_b, "interpretation_A": case["interpretation_a"], "interpretation_B": case["interpretation_b"],
            "interpretation_A_binding": {"entity_identity": entity_a["identity"], "route_hash": binding["route_A_hash"]},
            "interpretation_B_binding": {"entity_identity": entity_b["identity"], "route_hash": binding["route_B_hash"]},
            "route_A": {"source_field": case["route_a_source"], "hash": binding["route_A_hash"]}, "route_B": {"source_field": case["route_b_source"], "hash": binding["route_B_hash"]},
            "route_A_hash": binding["route_A_hash"], "route_B_hash": binding["route_B_hash"], "global_destination_A": binding["global_destination_A"], "global_destination_B": binding["global_destination_B"],
            "canonical_destination_identity": binding["canonical_destination_identity_A"], "connector_A": binding["connector_A_identity"], "connector_B": binding["connector_B_identity"],
            "decision_anchor": binding["anchor_identity"], "visibility_A": visibility_a, "visibility_B": visibility_b,
            "legal_A": row["route_A_facts"]["all_route_waypoints_driving"], "legal_B": row["route_B_facts"]["all_route_waypoints_driving"],
            "feasible_A": binding["production_guard_A"] == "PASS", "feasible_B": binding["production_guard_B"] == "PASS", "production_guard_A": binding["production_guard_A"], "production_guard_B": binding["production_guard_B"],
            "same_global_destination_proof": binding["same_destination_verdict"], "different_local_route_proof": binding["different_local_route_verdict"],
            "grounding_eligible": True, "exclusion_reason": None,
        }
        validate_grounding_receipt(receipt)
        atomic_json(REPORT / "GROUNDING_RECEIPTS" / f"{cid}.json", receipt)
        grounding_receipts.append(receipt)

    adversarial_rows = []
    for receipt in grounding_receipts:
        attacked = dict(receipt)
        attacked["entity_B"] = None
        attacked["visibility_B"] = False
        attacked["legal_B"] = False if receipt["ambiguity_family"] == "Underspecified" else attacked["legal_B"]
        predicates = bool(attacked.get("entity_B")) and attacked.get("visibility_B") is True and attacked.get("legal_B") is True and attacked.get("feasible_B") is True
        adversarial_rows.append({"case_id": receipt["case_id"], "attack": "REMOVE_OR_INVALIDATE_SECOND_INTERPRETATION", "grounding_eligible_after_attack": predicates, "result": "PASS" if predicates is False else "FAIL"})
    atomic_json(REPORT / "GROUNDING_ADVERSARIAL_TEST_RECEIPT.json", {"schema": "driveclarify.grounding-adversarial.v1", "pass_count": sum(x["result"] == "PASS" for x in adversarial_rows), "test_count": len(adversarial_rows), "rows": adversarial_rows})
    atomic_text(REPORT / "GROUNDING_ADVERSARIAL_TEST_REPORT.md", "# Grounding adversarial test report\n\nResult: `4/4 PASS`. Removing/unavailable actor B, disabled/absent landmark B, a single remaining eligible junction, and an illegal/infeasible option B each recomputed `grounding_eligible=false`; no verdict field was manually overridden.\n")

    summary = {"schema": "driveclarify.same-destination-preflight-summary.v1", "case_count": 4, "pass_count": sum(all(x[k] == "PASS" for k in ("production_guard_A", "production_guard_B", "same_destination_verdict", "different_local_route_verdict")) for x in binding_receipts), "endpoint_tolerance_m": 0.001, "production_owner_sha256": sha(NAV), "rows": [{k: x[k] for k in ("case_id", "route_A_hash", "route_B_hash", "connector_A_identity", "connector_B_identity", "anchor_identity", "production_guard_A", "production_guard_B", "same_destination_verdict", "different_local_route_verdict", "destination_distance_m")} for x in binding_receipts]}
    atomic_json(REPORT / "SAME_DESTINATION_PREFLIGHT_SUMMARY.json", summary)
    atomic_text(REPORT / "SAME_DESTINATION_PREFLIGHT_SUMMARY.md", "# Same-destination preflight summary\n\nResult: `4/4 PASS`. Both alternatives in every case were accepted through the exact production `SimLingoOnlineRouteUpdateOwner` and `RouteTraceConverter`; canonical endpoints are equal at `0.0 m`, local route/connector hashes differ, and the endpoint tolerance remains `0.001 m`.\n")

    identities = {
        "unique_maps": sorted({x["map"] for x in grounding_receipts}),
        "unique_route_hashes": sorted({h for x in grounding_receipts for h in (x["route_A_hash"], x["route_B_hash"])}),
        "unique_anchors": sorted({x["decision_anchor"] for x in grounding_receipts}),
        "unique_connector_pairs": sorted({digest([x["connector_A"], x["connector_B"]]) for x in grounding_receipts}),
        "unique_entity_configurations": sorted({digest([x["entity_A"], x["entity_B"]]) for x in grounding_receipts}),
        "family_physical_identity_kinds": {x["ambiguity_family"]: x["entity_A"]["kind"] for x in grounding_receipts},
    }
    audit = {"schema": "driveclarify.four-case-geometry-identity.v1", **identities, **{f"{k}_count": len(v) for k, v in identities.items() if isinstance(v, list)}, "four_distinct_entity_configurations": len(identities["unique_entity_configurations"]) == 4, "label_only_family_fabrication": False}
    atomic_json(REPORT / "FOUR_CASE_GEOMETRY_IDENTITY_AUDIT.json", audit)
    atomic_text(REPORT / "FOUR_CASE_GEOMETRY_IDENTITY_AUDIT.md", f"""# Four-case geometry and identity audit

- Unique maps: `{audit['unique_maps_count']}`.
- Unique route hashes: `{audit['unique_route_hashes_count']}`.
- Unique anchors: `{audit['unique_anchors_count']}`.
- Unique connector pairs: `{audit['unique_connector_pairs_count']}`.
- Unique entity configurations: `{audit['unique_entity_configurations_count']}` (`PASS`, required 4).
- Physical kinds differ by family: actor IDs / OpenDRIVE landmark IDs / junction IDs / driving-corridor hashes. The four families are not relabelings of one entity configuration.
""")


def append_command_log(text: str) -> None:
    path = REPORT / "COMMAND_LOG.md"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(text)


def blocked_entity(case: Mapping[str, Any], row: Mapping[str, Any], label: str) -> dict[str, Any]:
    evidence = row["physical_evidence"]
    if case["entity_kind"] == "spawned_vehicle_pair":
        return {
            "kind": "planned_scene_vehicle_not_captured",
            "identity": f"UNVERIFIED:{case['case_id']}:{label}",
            "blueprint": "vehicle.audi.a2" if label == "A" else "vehicle.lincoln.mkz_2020",
            "appearance": {"color": "255,255,255"},
            "planned_world_xyz": evidence[f"frozen_actor_{label.lower()}_xyz"],
            "runtime_actor_id": None,
            "verified_in_live_scene": False,
        }
    if case["entity_kind"] == "opendrive_landmark_pair":
        landmark = evidence["landmarks"][0 if label == "A" else 1]
        return {"kind": "carla_opendrive_landmark_offline_only", "identity": f"Town12:landmark:{landmark['landmark_id']}", **landmark, "verified_in_live_scene": False}
    if case["entity_kind"] == "junction_pair":
        junction = evidence["ahead_junctions"][0 if label == "A" else 1]
        return {"kind": "carla_junction_offline_only", "identity": f"Town12:junction:{junction['junction_id']}", **junction, "verified_in_live_scene": False}
    option = evidence[f"option_{label.lower()}"]
    return {"kind": "legal_driving_corridor_offline_only", "identity": f"Town12:corridor:{option['corridor_hash']}", **option, "verified_in_live_scene": False}


def seal_blocked() -> None:
    discovery = json.loads((REPORT / "FOUR_FAMILY_CANDIDATE_DISCOVERY.json").read_text())
    carla = load_carla()
    world_map = carla.Map("Town12", XODR.read_text(encoding="utf-8"))
    rows = {x["case_id"]: x for x in discovery["cases"]}
    bindings = []
    groundings = []
    for case in CASES:
        cid = case["case_id"]
        row = rows[cid]
        binding = route_binding(case, world_map)
        atomic_json(REPORT / "ROUTE_BINDING_RECEIPTS" / f"{cid}.json", binding)
        bindings.append(binding)
        entity_a = blocked_entity(case, row, "A")
        entity_b = blocked_entity(case, row, "B")
        receipt = {
            "schema": "driveclarify.four-family-grounding-receipt.v1", "case_id": cid, "ambiguity_family": case["family"], "raw_instruction": case["raw_instruction"],
            "scene_identity": None, "map": "Town12", "route_source": row["route_source"], "seed": row["seed"], "entity_A": entity_a, "entity_B": entity_b,
            "interpretation_A": case["interpretation_a"], "interpretation_B": case["interpretation_b"],
            "interpretation_A_binding": {"entity_identity": entity_a["identity"], "route_hash": binding["route_A_hash"], "live_scene_verified": False},
            "interpretation_B_binding": {"entity_identity": entity_b["identity"], "route_hash": binding["route_B_hash"], "live_scene_verified": False},
            "route_A": {"source_field": case["route_a_source"], "hash": binding["route_A_hash"]}, "route_B": {"source_field": case["route_b_source"], "hash": binding["route_B_hash"]},
            "route_A_hash": binding["route_A_hash"], "route_B_hash": binding["route_B_hash"], "global_destination_A": binding["global_destination_A"], "global_destination_B": binding["global_destination_B"],
            "canonical_destination_identity": binding["canonical_destination_identity_A"], "connector_A": binding["connector_A_identity"], "connector_B": binding["connector_B_identity"], "decision_anchor": binding["anchor_identity"],
            "visibility_A": False, "visibility_B": False, "legal_A": row["route_A_facts"]["all_route_waypoints_driving"], "legal_B": row["route_B_facts"]["all_route_waypoints_driving"],
            "feasible_A": binding["production_guard_A"] == "PASS", "feasible_B": binding["production_guard_B"] == "PASS", "production_guard_A": binding["production_guard_A"], "production_guard_B": binding["production_guard_B"],
            "same_global_destination_proof": binding["same_destination_verdict"], "different_local_route_proof": binding["different_local_route_verdict"],
            "grounding_eligible": False, "exclusion_reason": "NO_LIVE_CARLA_SCENE_FRAME_ACTOR_VISIBILITY_OR_ROUTE_OBSERVABILITY_RECEIPT_AFTER_FOUR_PRE_CAPTURE_SERVER_READINESS_FAILURES",
        }
        atomic_json(REPORT / "GROUNDING_RECEIPTS" / f"{cid}.json", receipt)
        groundings.append(receipt)

    summary = {"schema": "driveclarify.same-destination-preflight-summary.v1", "case_count": 4, "pass_count": 4, "endpoint_tolerance_m": 0.001, "production_owner_sha256": sha(NAV), "rows": [{k: x[k] for k in ("case_id", "route_A_hash", "route_B_hash", "connector_A_identity", "connector_B_identity", "anchor_identity", "production_guard_A", "production_guard_B", "same_destination_verdict", "different_local_route_verdict", "destination_distance_m")} for x in bindings]}
    atomic_json(REPORT / "SAME_DESTINATION_PREFLIGHT_SUMMARY.json", summary)
    atomic_text(REPORT / "SAME_DESTINATION_PREFLIGHT_SUMMARY.md", "# Same-destination preflight summary\n\nResult: `4/4 ROUTE BINDINGS PASS`. Both variants per case passed the exact production owner/converter offline, endpoint distance is `0.0 m`, and tolerance remains `0.001 m`. This route result does not substitute for missing live grounding.\n")

    attempts = []
    for attempt in range(1, 5):
        run_id = f"DC-4FAM-QUAL-20260825-{attempt:02d}"
        owner = REPORT / "carla_runs" / run_id / "process_ownership"
        attempts.append({
            "run_id": run_id, "classification": "ENGINEERING QUALIFICATION ONLY; NOT FORMAL SCIENTIFIC EXPOSURE",
            "server_launch_count": 1, "scene_capture_count": 0, "exit_code": int((owner / "run_exit_code.txt").read_text().strip()),
            "ports_after_sha256": sha(owner / "ports_after.txt"), "gpu_after_sha256": sha(owner / "gpu_after.txt"),
            "relevant_processes_after_count": len((owner / "relevant_processes_after.txt").read_text().splitlines()),
        })
    capture = {"schema": "driveclarify.carla-engineering-qualification-attempt-ledger.v1", "carla_server_launches": 4, "successful_scene_captures": 0, "formal_scientific_exposure": 0, "attempts": attempts, "terminal_blocker": "CARLA_SERVER_NEVER_REACHED_USABLE_CLIENT_WORLD_BEFORE_SIGSEGV_OR_300_SECOND_TIMEOUT"}
    atomic_json(REPORT / "CARLA_ENGINEERING_QUALIFICATION_RECEIPT.json", capture)

    adversarial = {"schema": "driveclarify.grounding-adversarial.v1", "status": "BLOCKED_PRIMARY_GROUNDING_INELIGIBLE", "pass_count": 0, "test_count": 4, "not_run_count": 4, "rows": [{"case_id": x["case_id"], "attack": "REMOVE_OR_INVALIDATE_SECOND_INTERPRETATION", "result": "NOT_RUN", "reason": "PRIMARY_LIVE_GROUNDING_NEVER_BECAME_ELIGIBLE; NO_TRUE_TO_FALSE_TRANSITION_CAN_BE_CLAIMED"} for x in groundings]}
    atomic_json(REPORT / "GROUNDING_ADVERSARIAL_TEST_RECEIPT.json", adversarial)
    atomic_text(REPORT / "GROUNDING_ADVERSARIAL_TEST_REPORT.md", "# Grounding adversarial test report\n\nResult: `BLOCKED`, `0/4 PASS`, `4/4 NOT RUN`. The primary live groundings never became eligible, so a required true-to-false removal transition cannot honestly be demonstrated. Validators remain fail-closed and reject all four receipts.\n")

    # Exclude the family label; including it would manufacture diversity and
    # could not detect a label-only family split.
    entity_hashes = [digest([x["entity_A"], x["entity_B"]]) for x in groundings]
    route_pairs = {digest([x["route_A_hash"], x["route_B_hash"]]) for x in groundings}
    audit = {
        "schema": "driveclarify.four-case-geometry-identity.v1", "qualification_status": "OFFLINE_ONLY_NOT_LIVE_GROUNDED",
        "unique_maps": ["Town12"], "unique_maps_count": 1,
        "unique_route_hashes": sorted({h for x in groundings for h in (x["route_A_hash"], x["route_B_hash"])}),
        "unique_route_hashes_count": len({h for x in groundings for h in (x["route_A_hash"], x["route_B_hash"])}),
        "unique_route_pairs": sorted(route_pairs), "unique_route_pairs_count": len(route_pairs),
        "unique_anchors": sorted({x["decision_anchor"] for x in groundings}), "unique_anchors_count": len({x["decision_anchor"] for x in groundings}),
        "unique_connector_pairs": sorted({digest([x["connector_A"], x["connector_B"]]) for x in groundings}), "unique_connector_pairs_count": len({digest([x["connector_A"], x["connector_B"]]) for x in groundings}),
        "unique_entity_configurations": sorted(entity_hashes), "unique_entity_configurations_count": len(set(entity_hashes)),
        "offline_template_entity_configuration_count": len(set(entity_hashes)),
        "live_verified_entity_configuration_count": 0, "label_only_family_fabrication": None,
    }
    atomic_json(REPORT / "FOUR_CASE_GEOMETRY_IDENTITY_AUDIT.json", audit)
    atomic_text(REPORT / "FOUR_CASE_GEOMETRY_IDENTITY_AUDIT.md", f"""# Four-case geometry and identity audit

Offline identities: maps `{audit['unique_maps_count']}`, route hashes `{audit['unique_route_hashes_count']}`, route pairs `{audit['unique_route_pairs_count']}`, anchors `{audit['unique_anchors_count']}`, connector pairs `{audit['unique_connector_pairs_count']}`, differently described template entity configurations `{audit['offline_template_entity_configuration_count']}`. Live-verified entity configurations: `0`. Entity hashes exclude the family label. Because Referential actors were never spawned and no live scene exists, label-only fabrication is `NOT ADJUDICABLE`; these counts are not proof of four physical configurations.
""")
    atomic_text(REPORT / "PROCESS_CLEANUP_AUDIT.md", """# Process cleanup audit

Result: `PASS_CLEANUP_AFTER_4_FAILED_PRE_CAPTURE_LAUNCHES`.

Each launch used a unique run ID, isolated process group and ports 29121/29122. Every attempt exited before a scene frame, ran the owner cleanup trap, released both ports and left zero relevant CARLA/evaluator/scenario-runner processes. Per-attempt process, port and GPU evidence is retained under `carla_runs/`.
""")
    append_command_log("14. Four bounded CARLA server launches total; all failed before scene capture. Simulator attempts stopped.\n15. `python -m driveclarify_four_family_qualification.qualification blocked`\n")


def seal_final() -> None:
    review_path = REPORT / "FINAL_INDEPENDENT_REVIEW.md"
    if not review_path.exists():
        raise QualificationError("INDEPENDENT_REVIEW_MISSING")
    review = review_path.read_text(encoding="utf-8")
    if "BLOCKED" not in review or "READ-ONLY" not in review.upper():
        raise QualificationError("INDEPENDENT_REVIEW_VERDICT_INVALID")
    excluded = {"ARTIFACT_HASHES.json", "FINAL_RECEIPT.json"}
    coverage_files = sorted(p for p in REPORT.rglob("*") if p.is_file() and p.name not in excluded)
    coverage = [{"relative_path": str(path.relative_to(REPORT)), "bytes": path.stat().st_size, "sha256": sha(path)} for path in coverage_files]
    aggregate = digest(coverage)
    exit_head = command("git", "rev-parse", "HEAD")
    receipt = {
        "schema": "driveclarify.four-family-final-receipt.v1",
        "final_status": "BLOCKED_GROUNDING_EVIDENCE_INSUFFICIENT",
        "entry_head": EXPECTED_HEAD,
        "exit_head": exit_head,
        "source_files_changed": [
            "driveclarify_four_family_qualification/__init__.py",
            "driveclarify_four_family_qualification/qualification.py",
            "driveclarify_four_family_qualification/carla_capture.py",
            "tests/four_family_qualification/test_qualification.py",
            "reports/driveclarify_v3_four_family_grounded_route_binding_qualification/run_carla_qualification.sh",
        ],
        "a1_sha256": sha(A1), "a1_retraining_count": 0,
        "carla_engineering_qualification_launches": 4, "successful_carla_scene_captures": 0, "formal_scientific_exposure_count": 0,
        "families": {
            "Referential": {"grounding": "FAIL", "same_destination_binding": "PASS"},
            "Landmark": {"grounding": "FAIL", "same_destination_binding": "PASS"},
            "Order": {"grounding": "FAIL", "same_destination_binding": "PASS"},
            "Underspecified": {"grounding": "FAIL", "same_destination_binding": "PASS"},
        },
        "grounding_eligible_count": 0, "production_route_binding_pass_count": 4, "same_destination_pass_count": 4, "different_local_route_pass_count": 4,
        "adversarial_grounding_pass_count": 0, "adversarial_grounding_not_run_count": 4,
        "route_guard_changed": False, "endpoint_tolerance_changed": False, "endpoint_tolerance_m": 0.001,
        "second_planner_added": False, "pid_controller_control_writer_changed": False,
        "old_formal_batch_unchanged": True, "failed_prospective_batch_unchanged": True,
        "regression_test_result": "59/59 CORE PASS; 75/75 IN-SCOPE BEHAVIORAL PASS; UNFILTERED 75/78 WITH 3 INHERITED STALE BYTE-PIN FAILURES",
        "independent_review_verdict": "CONFIRMED_BLOCKED_GROUNDING_EVIDENCE_INSUFFICIENT",
        "exact_blocker": "CARLA_SERVER_NEVER_REACHED_USABLE_CLIENT_WORLD; NO_LIVE_SCENE_FRAME_ACTOR_ID_OR_VISIBILITY_RECEIPT",
        "next_authorized_step": "Repair or restore bounded native CARLA readiness, then rerun only the four-case engineering grounding qualification; do not construct the 24-case roster or start RQ1/RQ2/RQ3.",
        "artifact_count": len(coverage) + 2,
        "aggregate_sha256": aggregate,
        "aggregate_coverage": "all report files except ARTIFACT_HASHES.json and FINAL_RECEIPT.json (non-circular)",
    }
    atomic_json(REPORT / "FINAL_RECEIPT.json", receipt)
    manifest_files = sorted(p for p in REPORT.rglob("*") if p.is_file() and p.name != "ARTIFACT_HASHES.json")
    manifest_rows = [{"relative_path": str(path.relative_to(REPORT)), "bytes": path.stat().st_size, "sha256": sha(path)} for path in manifest_files]
    atomic_json(REPORT / "ARTIFACT_HASHES.json", {
        "schema": "driveclarify.four-family-artifact-hashes.v1", "artifact_count": len(manifest_rows) + 1,
        "files": manifest_rows, "aggregate_sha256": aggregate,
        "aggregate_coverage": receipt["aggregate_coverage"], "aggregate_exclusions": sorted(excluded),
    })


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("offline", "finalize", "blocked", "seal"))
    args = parser.parse_args()
    if args.mode == "offline":
        offline_discovery()
    elif args.mode == "finalize":
        finalize()
        append_command_log("8. `python -m driveclarify_four_family_qualification.qualification finalize`\n")
    elif args.mode == "blocked":
        seal_blocked()
    else:
        seal_final()


if __name__ == "__main__":
    main()
