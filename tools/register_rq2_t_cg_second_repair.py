#!/usr/bin/env python3
"""Register ENG-010 and refresh the still-unexposed late-reveal contract."""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_rq2_t.measurement import canonical_sha256  # noqa: E402
from driveclarify_rq2_t_e2_v3.usc_admission import project_point_to_polyline  # noqa: E402
from driveclarify_rq2_t_cg.builder import COMMITMENT_POINTS  # noqa: E402
from driveclarify_rq2_t_cg.lifecycle import static_route_admission  # noqa: E402
from driveclarify_rq2_t_cg.scene_bindings import frozen_binding  # noqa: E402
from tools.prepare_rq2_t_cg import REPORT, materialize_route, sha256, write_json  # noqa: E402


def certificate(identity: str, scene: str, path: Path):
    binding = frozen_binding(scene)
    value = {
        "schema_version": "driveclarify.rq2_t_cg.engineering_scene_certificate.v1",
        "scene": scene, "identity": identity, "template": binding["template"], "binding": binding,
        "route_admission": static_route_admission(path, binding),
        "candidate_certification": binding["candidate_set_certification"],
        "candidate_bindings_semantically_distinct": True, "passenger_intent_identified": False,
        "event_owners_independent_of_views_and_outcomes": True,
        "accepted_v1_shared_prefix_owner": "ORD-01" if binding["family"] == "ORDER" else "REF-01",
        "formal_scientific_exposure": False,
    }
    value["certificate_digest"] = canonical_sha256(value)
    write_json(REPORT / "ENGINEERING_SCENE_CERTIFICATES" / (scene + ".json"), value)
    return value


def main() -> int:
    attempts = [json.loads(line) for line in (REPORT / "ENGINEERING_ATTEMPT_LEDGER.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if {row["identity"] for row in attempts} != {"RQ2TCG-ENG-001", "RQ2TCG-ENG-009"}:
        raise RuntimeError("RQ2_T_CG_SECOND_REPAIR_EXPOSURE_SET_UNEXPECTED")
    registry_path = REPORT / "ENGINEERING_IDENTITY_REGISTRY.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if any(row["identity"] == "RQ2TCG-ENG-010" for row in registry["identities"]):
        raise RuntimeError("RQ2_T_CG_ENG_010_ALREADY_REGISTERED")
    # Identity 006 has not been exposed; refresh its late event to be 0.50 m
    # before the unchanged owner's 0.75-m commitment arrival radius.
    late_scene = "CG-ORD-LATE-REVEAL"
    late_binding = frozen_binding(late_scene)
    late_path = materialize_route(late_scene)
    for row in registry["identities"]:
        if row["identity"] == "RQ2TCG-ENG-006":
            row.update({"route_sha256": sha256(late_path),
                        "scene_configuration_sha256": late_binding["scene_configuration_sha256"],
                        "preexposure_late_reveal_repair": "ALIGN_WITH_UNCHANGED_V1_OWNER_0_75_M_ARRIVAL_RADIUS"})
    late_certificate = certificate("RQ2TCG-ENG-006", late_scene, late_path)

    scene = "CG-REF-ASYNC-C"
    binding = frozen_binding(scene)
    path = materialize_route(scene)
    row10 = {
        "identity": "RQ2TCG-ENG-010", "scene": scene, "template": binding["template"],
        "seed": binding["engineering_seed"], "phase": "ENGINEERING_ONLY",
        "route_path": str(path.relative_to(ROOT)), "route_sha256": sha256(path),
        "scene_configuration_sha256": binding["scene_configuration_sha256"],
        "registered_before_execution": True, "formal_scientific_exposure": False,
        "replacement_for": "RQ2TCG-ENG-009",
        "repair_reason": "INDEPENDENT_INVALIDATION_PRECEDED_OWNER_PACED_OBLIGATION_IN_ENG_009",
    }
    registry["identities"].append(row10)
    registry["identity_count"] = len(registry["identities"])
    registry["preexposure_repair_history"].append({
        "failed_mechanism_identity": "RQ2TCG-ENG-009", "replacement_identity": "RQ2TCG-ENG-010",
        "repair": "OBLIGATION_PROGRESS_1_30_M_AND_INVALIDATION_AT_3_50_S",
        "frozen_TTL_or_sufficiency_changed": False,
    })
    registry.pop("registry_digest", None)
    registry["registry_digest"] = canonical_sha256(registry)
    write_json(registry_path, registry)
    exclusion_path = REPORT / "ENGINEERING_SEED_EXCLUSION_REGISTRY.json"
    exclusion = json.loads(exclusion_path.read_text(encoding="utf-8"))
    exclusion["seeds"].append({"seed": row10["seed"], "identity": row10["identity"], "scene": scene,
                               "permanent_exclusion": "ALL_FUTURE_RQ2_T_CG_DEV_TEST_PAPER_DENOMINATORS"})
    exclusion["seed_count"] = len(exclusion["seeds"])
    exclusion.pop("registry_digest", None)
    exclusion["registry_digest"] = canonical_sha256(exclusion)
    write_json(exclusion_path, exclusion)
    ref_certificate = certificate("RQ2TCG-ENG-010", scene, path)
    manifest_path = REPORT / "ENGINEERING_SCENE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for row in manifest["scenes"]:
        if row["identity"] == "RQ2TCG-ENG-006":
            row.update({"route_sha256": sha256(late_path),
                        "scene_configuration_sha256": late_binding["scene_configuration_sha256"],
                        "certificate_digest": late_certificate["certificate_digest"]})
    manifest["scenes"].append({**row10, "certificate_digest": ref_certificate["certificate_digest"],
                                "candidate_set_digest": binding["candidate_set_certification"]["candidate_set_digest"],
                                "event_schedule_digest": canonical_sha256(binding["current_event_schedule"]),
                                "owner_scene_key": "REF-01", "exposure_status": "PROSPECTIVE_UNEXPOSED"})
    manifest["scene_contract_count"] = len(manifest["scenes"])
    manifest.pop("manifest_digest", None)
    manifest["manifest_digest"] = canonical_sha256(manifest)
    write_json(manifest_path, manifest)

    route = ET.parse(late_path).getroot().find("route")
    points = [[float(item.attrib[key]) for key in ("x", "y", "z")] for item in route.findall("./waypoints/position")]
    commitment = dict(project_point_to_polyline(COMMITMENT_POINTS["ORDER"], points))
    remaining = float(late_binding["late_reveal_remaining_to_commitment_m"])
    owner_radius, speed = 0.75, 1.25
    late = {
        "schema_version": "driveclarify.rq2_t_cg.late_reveal_scene_certificate.v2",
        "certified_before_execution": True, "scene": late_scene, "route_sha256": sha256(late_path),
        "commitment_projection": commitment,
        "late_reveal_route_arc_length_m": float(commitment["route_arc_length_m"]) - remaining,
        "geometric_remaining_to_boundary_m": remaining,
        "unchanged_v1_owner_commitment_arrival_radius_m": owner_radius,
        "prospective_distance_to_owner_commitment_m": remaining - owner_radius,
        "frozen_target_speed_reference_mps": speed,
        "prospective_TTCmt_reference_s": (remaining - owner_radius) / speed,
        "prospectively_inside_0_to_1_20_s_interval": 0.0 < (remaining - owner_radius) / speed < 1.2,
        "strictly_before_commitment": remaining > owner_radius,
        "independent_event_owner": late_binding["current_event_schedule"]["obligation_owner"],
        "runtime_gold_trigger": False, "actual_native_temporal_certification_pending_execution": True,
    }
    late["certificate_digest"] = canonical_sha256(late)
    write_json(REPORT / "LATE_REVEAL_SCENE_CERTIFICATE.json", late)
    receipt = {
        "schema_version": "driveclarify.rq2_t_cg.second_preexposure_repair.v1",
        "identity_009_preserved": True, "identity_010_registered": True,
        "total_registered_identities": 10, "maximum_authorized_identities": 12,
        "identity_006_unexposed_at_late_contract_refresh": True,
        "scientific_contract_changed": False, "automatic_e2_reopened": False,
        "formal_seed_values_generated": 0, "formal_scientific_exposures": 0,
    }
    receipt["receipt_digest"] = canonical_sha256(receipt)
    write_json(REPORT / "SECOND_PREEXPOSURE_MECHANISM_REPAIR_RECEIPT.json", receipt)
    print(json.dumps({"status": "PASS_SECOND_PREEXPOSURE_REPAIR", "identity_count": 10,
                      "replacement": "RQ2TCG-ENG-010", "late_prospective_TTCmt_s": late["prospective_TTCmt_reference_s"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
