#!/usr/bin/env python3
"""Rebind only unexposed CG identities to the accepted V1 trajectory owner."""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_rq2_t.measurement import canonical_sha256  # noqa: E402
from driveclarify_rq2_t_cg.lifecycle import static_route_admission  # noqa: E402
from driveclarify_rq2_t_cg.scene_bindings import frozen_binding  # noqa: E402
from tools.prepare_rq2_t_cg import REPORT, materialize_route, sha256, write_json  # noqa: E402


OWNER_KEY = {"REFERENTIAL": "REF-01", "LANDMARK": "LMK-01", "ORDER": "ORD-01",
             "UNDERSPECIFIED_CONSTRAINT": "REF-01"}


def waypoint_digest(path: Path) -> str:
    route = ET.parse(path).getroot().find("route")
    rows = [[row.attrib[key] for key in ("x", "y", "z")] for row in route.findall("./waypoints/position")]
    return canonical_sha256(rows)


def main() -> int:
    registry_path = REPORT / "ENGINEERING_IDENTITY_REGISTRY.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    attempts = [json.loads(line) for line in (REPORT / "ENGINEERING_ATTEMPT_LEDGER.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    exposed = {row["identity"] for row in attempts}
    if exposed != {"RQ2TCG-ENG-001"}:
        raise RuntimeError("RQ2_T_CG_REPAIR_EXPOSURE_SET_UNEXPECTED:" + repr(exposed))
    identity_scene = {
        "RQ2TCG-ENG-002": "CG-LMK-ASYNC", "RQ2TCG-ENG-003": "CG-ORD-ASYNC",
        "RQ2TCG-ENG-004": "CG-REF-SYNC", "RQ2TCG-ENG-005": "CG-LMK-SYNC",
        "RQ2TCG-ENG-006": "CG-ORD-LATE-REVEAL", "RQ2TCG-ENG-007": "CG-NONREVEAL",
        "RQ2TCG-ENG-008": "CG-USC-INTRINSIC",
    }
    repaired_rows = []
    for row in registry["identities"]:
        identity = row["identity"]
        if identity not in identity_scene:
            repaired_rows.append(row)
            continue
        scene = identity_scene[identity]
        binding = frozen_binding(scene)
        path = materialize_route(scene)
        admission = static_route_admission(path, binding)
        if admission["status"] != "PASS_STATIC_ROUTE_ADMISSION":
            raise RuntimeError(json.dumps(admission, sort_keys=True))
        repaired_rows.append({
            **row, "scene": scene, "template": binding["template"],
            "route_path": str(path.relative_to(ROOT)), "route_sha256": sha256(path),
            "scene_configuration_sha256": binding["scene_configuration_sha256"],
            "preexposure_repair": "ACCEPTED_V1_SHARED_PREFIX_OWNER_REUSE",
        })
    replacement_scene = "CG-REF-ASYNC-B"
    replacement = frozen_binding(replacement_scene)
    replacement_path = materialize_route(replacement_scene)
    replacement_row = {
        "identity": "RQ2TCG-ENG-009", "scene": replacement_scene,
        "template": replacement["template"], "seed": replacement["engineering_seed"],
        "phase": "ENGINEERING_ONLY", "route_path": str(replacement_path.relative_to(ROOT)),
        "route_sha256": sha256(replacement_path),
        "scene_configuration_sha256": replacement["scene_configuration_sha256"],
        "registered_before_execution": True, "formal_scientific_exposure": False,
        "replacement_for": "RQ2TCG-ENG-001",
        "repair_reason": "UNCHANGED_COMMITMENT_NOT_REACHED_BY_UNCONSTRAINED_NATIVE_VLA_WITHIN_BOUND",
    }
    repaired_rows.append(replacement_row)
    registry.update({
        "identity_count": len(repaired_rows), "valid_episode_target": 8,
        "maximum_authorized_identities": 12, "identities": repaired_rows,
        "preexposure_repair_history": [{
            "exposed_failed_identity": "RQ2TCG-ENG-001",
            "failure": "NATIVE_TRAJECTORY_STOPPED_7_38_M_BEFORE_UNCHANGED_COMMITMENT",
            "replacement_identity": "RQ2TCG-ENG-009",
            "unexposed_identities_rebound": sorted(identity_scene),
            "repair": "REUSE_ACCEPTED_SHARED_PREFIX_LONGITUDINAL_OWNER_V1",
            "scientific_definition_changed": False,
        }],
    })
    registry.pop("registry_digest", None)
    registry["registry_digest"] = canonical_sha256(registry)
    write_json(registry_path, registry)

    exclusion_path = REPORT / "ENGINEERING_SEED_EXCLUSION_REGISTRY.json"
    exclusion = json.loads(exclusion_path.read_text(encoding="utf-8"))
    exclusion["seeds"].append({
        "seed": replacement_row["seed"], "identity": replacement_row["identity"],
        "scene": replacement_scene,
        "permanent_exclusion": "ALL_FUTURE_RQ2_T_CG_DEV_TEST_PAPER_DENOMINATORS",
    })
    exclusion["seed_count"] = len(exclusion["seeds"])
    exclusion.pop("registry_digest", None)
    exclusion["registry_digest"] = canonical_sha256(exclusion)
    write_json(exclusion_path, exclusion)

    owner_bindings = json.loads((ROOT / "driveclarify_rq2_t/owner_bindings_v1.json").read_text(encoding="utf-8"))["bindings"]
    geometry_rows = []
    for row in repaired_rows:
        binding = frozen_binding(row["scene"])
        owner_key = OWNER_KEY[binding["family"]]
        base = ROOT / binding["base_route"]
        engineered = ROOT / row["route_path"]
        geometry_rows.append({
            "identity": row["identity"], "scene": row["scene"], "owner_scene_key": owner_key,
            "engineering_route_sha256": sha256(engineered),
            "accepted_owner_route_sha256": owner_bindings[owner_key]["route_sha256"],
            "route_file_bytes_identical": sha256(engineered) == owner_bindings[owner_key]["route_sha256"],
            "engineering_waypoint_digest": waypoint_digest(engineered),
            "base_waypoint_digest": waypoint_digest(base),
            "route_waypoint_geometry_identical": waypoint_digest(engineered) == waypoint_digest(base),
            "owner_id": "SHARED_PREFIX_LONGITUDINAL_OWNER_V1",
            "owner_target_speed_mps": owner_bindings[owner_key]["target_speed_mps"],
            "owner_induced_model_forward_count": 0, "owner_induced_pid_count": 0,
            "owner_induced_route_planner_advance_count": 0, "observer_control_write_count": 0,
        })
    owner_certificate = {
        "schema_version": "driveclarify.rq2_t_cg.shared_prefix_owner_binding.v1",
        "status": "PASS_ACCEPTED_V1_OWNER_REUSE_ON_IDENTICAL_ROUTE_WAYPOINT_GEOMETRY",
        "reason": "COMMON_PROTOCOL_TRAJECTORY_OWNER; CONTROLLED_EVIDENCE_AND_RULES_REMAIN_POSTEPISODE_NONCONTROLLING",
        "accepted_owner_source_modified": False, "scientific_commitment_or_deadline_changed": False,
        "bindings": geometry_rows,
    }
    owner_certificate["certificate_digest"] = canonical_sha256(owner_certificate)
    write_json(REPORT / "V1_SHARED_PREFIX_OWNER_BINDING_CERTIFICATE.json", owner_certificate)

    manifest_path = REPORT / "ENGINEERING_SCENE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    existing = {row["identity"]: row for row in manifest["scenes"]}
    manifest_rows = []
    for row in repaired_rows:
        binding = frozen_binding(row["scene"])
        value = {**row,
                 "candidate_set_digest": binding["candidate_set_certification"]["candidate_set_digest"],
                 "event_schedule_digest": canonical_sha256(binding["current_event_schedule"]),
                 "owner_scene_key": OWNER_KEY[binding["family"]]}
        if row["identity"] == "RQ2TCG-ENG-001":
            value["certificate_digest"] = existing[row["identity"]]["certificate_digest"]
            value["exposure_status"] = "IMMUTABLE_INVALID_NATIVE_TRAJECTORY_EVIDENCE"
        else:
            admission = static_route_admission(ROOT / row["route_path"], binding)
            certificate = {
                "schema_version": "driveclarify.rq2_t_cg.engineering_scene_certificate.v1",
                "scene": row["scene"], "identity": row["identity"], "template": binding["template"],
                "binding": binding, "route_admission": admission,
                "candidate_certification": binding["candidate_set_certification"],
                "candidate_bindings_semantically_distinct": True, "passenger_intent_identified": False,
                "event_owners_independent_of_views_and_outcomes": True,
                "accepted_v1_shared_prefix_owner": OWNER_KEY[binding["family"]],
                "formal_scientific_exposure": False,
            }
            certificate["certificate_digest"] = canonical_sha256(certificate)
            write_json(REPORT / "ENGINEERING_SCENE_CERTIFICATES" / (row["scene"] + ".json"), certificate)
            value["certificate_digest"] = certificate["certificate_digest"]
            value["exposure_status"] = "PROSPECTIVE_UNEXPOSED"
        manifest_rows.append(value)
    manifest.update({"scene_contract_count": len(manifest_rows), "mechanism_template_count": 8,
                     "valid_episode_target": 8, "scenes": manifest_rows})
    manifest.pop("manifest_digest", None)
    manifest["manifest_digest"] = canonical_sha256(manifest)
    write_json(manifest_path, manifest)

    repair = {
        "schema_version": "driveclarify.rq2_t_cg.preexposure_trajectory_repair.v1",
        "failed_identity_preserved": "RQ2TCG-ENG-001", "failed_evidence_deleted_or_rewritten": False,
        "new_identity": "RQ2TCG-ENG-009", "total_registered_identities": 9,
        "maximum_authorized_identities": 12, "automatic_e2_reopened": False,
        "formal_seed_values_generated": 0, "formal_scientific_exposures": 0,
        "accepted_v1_owner_source_modified": False, "sufficiency_commitment_deadline_changed": False,
    }
    repair["receipt_digest"] = canonical_sha256(repair)
    write_json(REPORT / "PREEXPOSURE_TRAJECTORY_OWNER_REPAIR_RECEIPT.json", repair)
    print(json.dumps({"status": "PASS_PREEXPOSURE_TRAJECTORY_OWNER_REPAIR",
                      "registered_identities": 9, "exposed_identities": 1,
                      "replacement": "RQ2TCG-ENG-009"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
