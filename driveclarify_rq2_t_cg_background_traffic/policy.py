"""Machine-readable actor classification for the controlled mechanism experiment."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from driveclarify_rq2_t.measurement import canonical_sha256
from driveclarify_rq2_t_cg_formal_freeze.scenes import SCENE_ORDER


POLICY_ID = "RQ2_T_CG_BACKGROUND_TRAFFIC_POLICY_V2"
RANDOM_BACKGROUND_OWNER = "BENCH2DRIVE_ROUTE_SCENARIO_BACKGROUND_BEHAVIOR_TRAFFIC_MANAGER"
PARKED_BACKGROUND_OWNER = "BENCH2DRIVE_ROUTE_SCENARIO_AUTOMATIC_PARKED_MESH"


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def _layout_role(layout: Mapping[str, Any]) -> str:
    if layout.get("role"):
        return str(layout["role"])
    if layout.get("kind") == "CERTIFIED_TASK_JUNCTION":
        return "certified task junction {}".format(layout.get("junction_id"))
    return str(layout.get("kind", "scenario-owned scientific layout entity"))


def _scene_actor_manifest(scene: Mapping[str, Any]) -> Mapping[str, Any]:
    code = str(scene["scene_code"])
    scene_id = str(scene["formal_scene_id"])
    rows = [
        {
            "actor_identity": scene_id + ":EGO",
            "actor_owner": "BENCH2DRIVE_ROUTE_SCENARIO_EGO_OWNER",
            "actor_category": "NATIVE_EGO_VEHICLE",
            "scientific_role": "native trace and route-progress source",
            "classification": "KEEP",
            "reason": "Required for the scientific episode, commitment, horizon, recorder, and control provenance.",
        }
    ]
    for layout in scene.get("actor_or_task_layout", ()):
        rows.append({
            "actor_identity": str(layout["layout_id"]),
            "actor_owner": "RQ2_T_CG_FROZEN_SCENE_CONTRACT",
            "actor_category": (
                "SCIENTIFIC_TASK_TOPOLOGY_ENTITY"
                if layout.get("kind") == "CERTIFIED_TASK_JUNCTION"
                else "SCIENTIFIC_SCENARIO_LAYOUT_ENTITY"
            ),
            "scientific_role": _layout_role(layout),
            "classification": "KEEP",
            "reason": "Explicitly referenced by the frozen ambiguity/candidate/evidence scene contract.",
        })
    rows.extend((
        {
            "actor_identity": code + ":RUNTIME_BACKGROUND_BEHAVIOR:role_name=background:*",
            "actor_owner": RANDOM_BACKGROUND_OWNER,
            "actor_category": "RANDOM_TRAFFIC_MANAGER_BACKGROUND_VEHICLE_GENERATOR",
            "scientific_role": "NONE",
            "classification": "REMOVE_DO_NOT_SPAWN",
            "reason": "Not named by candidate bindings, E1-E9 evidence events, commitment, topology, safety, or negative-control construction.",
        },
        {
            "actor_identity": code + ":AUTOMATIC_PARKED_MESH:*",
            "actor_owner": PARKED_BACKGROUND_OWNER,
            "actor_category": "UNRELATED_AUTOMATIC_PARKED_BACKGROUND_GENERATOR",
            "scientific_role": "NONE",
            "classification": "REMOVE_DO_NOT_SPAWN",
            "reason": "Not explicitly authored by the RQ2-T-CG scientific scene contract.",
        },
    ))
    contract_text = json.dumps({
        "candidate_bindings": scene.get("candidate_bindings", ()),
        "events": scene.get("events", ()),
        "commitment": scene.get("commitment", {}),
        "actor_or_task_layout": scene.get("actor_or_task_layout", ()),
    }, sort_keys=True).casefold()
    removed = [row for row in rows if row["classification"] == "REMOVE_DO_NOT_SPAWN"]
    checks = {
        "ego_retained": rows[0]["classification"] == "KEEP",
        "all_explicit_layout_entities_retained": sum(row["classification"] == "KEEP" for row in rows) == 1 + len(scene.get("actor_or_task_layout", ())),
        "traffic_manager_background_generator_removed": any(row["actor_owner"] == RANDOM_BACKGROUND_OWNER for row in removed),
        "automatic_parked_background_generator_removed": any(row["actor_owner"] == PARKED_BACKGROUND_OWNER for row in removed),
        "removed_generator_not_referenced_by_scientific_contract": "backgroundbehavior" not in contract_text and "role_name=background" not in contract_text and "automatic_parked_mesh" not in contract_text,
    }
    value = {
        "scene_code": code,
        "scene_identity": scene_id,
        "scene_contract_digest": scene["formal_scene_digest"],
        "actors": rows,
        "random_background_vehicles_retained": 0,
        "scientific_or_scenario_owned_entries_retained": sum(row["classification"] == "KEEP" for row in rows),
        "removed_generator_entry_count": len(removed),
        "checks": checks,
        "pass": all(checks.values()),
    }
    value["manifest_digest"] = canonical_sha256(value)
    return value


def build_actor_manifests(scenes: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any]:
    if tuple(scenes) != tuple(SCENE_ORDER):
        raise ValueError("BACKGROUND_POLICY_REQUIRES_CANONICAL_EIGHT_SCENE_ORDER")
    manifests = {code: _scene_actor_manifest(scenes[code]) for code in SCENE_ORDER}
    retained = sum(row["scientific_or_scenario_owned_entries_retained"] for row in manifests.values())
    value = {
        "schema_version": "driveclarify.rq2_t_cg.scientific_actor_manifests.v2",
        "policy_id": POLICY_ID,
        "scene_count": len(manifests),
        "scene_order": list(SCENE_ORDER),
        "random_background_vehicles_retained": 0,
        "scientific_or_scenario_owned_entries_retained": retained,
        "manifests": manifests,
        "checks": {
            "eight_scene_manifests": len(manifests) == 8,
            "all_scene_manifests_pass": all(row["pass"] for row in manifests.values()),
            "random_background_retained_zero_every_scene": all(row["random_background_vehicles_retained"] == 0 for row in manifests.values()),
        },
    }
    value["status"] = "PASS_EIGHT_SCIENTIFIC_ACTOR_MANIFESTS" if all(value["checks"].values()) else "FAIL_SCIENTIFIC_ACTOR_MANIFESTS"
    value["manifest_set_digest"] = canonical_sha256(value)
    return value


def persist_policy(report: Path, scenes: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any]:
    manifests = build_actor_manifests(scenes)
    policy = {
        "schema_version": "driveclarify.rq2_t_cg.background_traffic_policy.v2",
        "policy_id": POLICY_ID,
        "status": "PASS_BACKGROUND_TRAFFIC_POLICY_PROSPECTIVELY_FROZEN",
        "scope": list(SCENE_ORDER),
        "controlled_mechanism_claim_boundary": "Random non-scientific background traffic is disabled; this experiment does not test robustness to arbitrary natural traffic.",
        "keep_rule": "Keep the ego and only explicitly scenario-owned scientific entities/tasks required by candidate binding, ambiguity, evidence, topology, safety/holding, commitment, or negative-control construction.",
        "remove_rule": "Do not attach Bench2Drive BackgroundBehavior and do not auto-spawn unrelated parked meshes for any RQ2-T-CG V2 controlled child.",
        "implementation": {
            "child_policy_environment": POLICY_ID,
            "background_behavior_attached": False,
            "traffic_manager_generated_background_vehicle_request_count": 0,
            "automatic_parked_mesh_request_count": 0,
            "scenario_owned_actor_initialization_changed": False,
            "ego_vehicle_control_changed": False,
        },
        "prospective_family_manifests": manifests,
        "proof_removed_actors_have_no_scientific_role": {
            "method": "Exact scan of candidate bindings, evidence events, commitment, and actor_or_task_layout in each scene contract.",
            "all_eight_scene_scans_pass": all(row["checks"]["removed_generator_not_referenced_by_scientific_contract"] for row in manifests["manifests"].values()),
            "random_background_vehicles_retained": 0,
        },
    }
    policy["policy_digest"] = canonical_sha256(policy)
    _write_json(report / "BACKGROUND_TRAFFIC_POLICY_V2.json", policy)
    lines = [
        "# RQ2-T-CG Background Traffic Policy V2",
        "",
        "Status: `PASS_BACKGROUND_TRAFFIC_POLICY_PROSPECTIVELY_FROZEN`.",
        "",
        "For all eight controlled scene families, the evaluator does not attach Bench2Drive `BackgroundBehavior` and does not auto-spawn unrelated parked meshes. The ego and every explicitly authored scientific layout entity/task remain classified `KEEP`.",
        "",
        "Random background vehicles retained: `0`. Scientific/scenario-owned manifest entries retained: `{}`.".format(manifests["scientific_or_scenario_owned_entries_retained"]),
        "",
        "Removed generator namespaces do not occur in any frozen candidate binding, E1-E9 event contract, commitment, topology task, or negative-control construction. This is a controlled-mechanism isolation policy and is not evidence of arbitrary-background-traffic robustness.",
    ]
    (report / "BACKGROUND_TRAFFIC_POLICY_V2.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return policy


def persist_final_actor_manifests(report: Path, scenes: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any]:
    value = build_actor_manifests(scenes)
    value = dict(value)
    value["manifest_stage"] = "FINAL_FORMAL_V2_PRESEED"
    value["manifest_set_digest"] = canonical_sha256({key: child for key, child in value.items() if key != "manifest_set_digest"})
    _write_json(report / "FINAL_SCIENTIFIC_ACTOR_MANIFESTS.json", value)
    return value


__all__ = [
    "POLICY_ID",
    "build_actor_manifests",
    "persist_final_actor_manifests",
    "persist_policy",
]
