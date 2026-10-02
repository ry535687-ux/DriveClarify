#!/usr/bin/env python3
"""Prospectively register the fresh development witness after ENG-001."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_rq2_t_e2_v4.contracts import canonical_sha256  # noqa: E402
from driveclarify_rq2_t_e2_v4.lifecycle import static_route_lifecycle_admission  # noqa: E402
from driveclarify_rq2_t_e2_v4.scene_bindings import SCENE_BINDINGS, frozen_binding  # noqa: E402
from tools.prepare_rq2_t_e2_v4 import REPORT, materialize_route, sha256, write_json  # noqa: E402


FAILED_SCENE = "DEV-V4-REF-DOMAIN-A"
FRESH_SCENE = "DEV-V4-REF-DOMAIN-B"
FRESH_IDENTITY = "RQ2TE2V4-ENG-012"
FRESH_SEED = 1487650337


def main() -> int:
    attempts = [json.loads(line) for line in (REPORT / "ENGINEERING_ATTEMPT_LEDGER.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if [row["identity"] for row in attempts] != ["RQ2TE2V4-ENG-001"]:
        raise RuntimeError("E2_V4_REPAIR_REGISTRATION_EXPOSURE_STATE_MISMATCH")
    registry = json.loads((REPORT / "ENGINEERING_IDENTITY_REGISTRY.json").read_text(encoding="utf-8"))
    if any(row["identity"] == FRESH_IDENTITY for row in registry["identities"]):
        raise RuntimeError("E2_V4_REPAIR_IDENTITY_ALREADY_REGISTERED")
    admissions = {}
    routes = {}
    # Preserve the exact failed A route. Every still-unexposed route is repaired
    # before its identity is admitted, and B is a new scene/configuration.
    for scene in SCENE_BINDINGS:
        if scene == FAILED_SCENE:
            continue
        binding = frozen_binding(scene)
        route = materialize_route(scene, binding)
        admission = static_route_lifecycle_admission(
            route_path=route, binding=binding, expected_route_sha256=sha256(route),
            expected_scene_configuration_sha256=binding["scene_configuration_sha256"],
        )
        if admission["status"] != "PASS_STATIC_ROUTE_LIFECYCLE_ADMISSION":
            raise RuntimeError("E2_V4_REPAIRED_STATIC_ADMISSION_FAILED:" + scene)
        routes[scene] = route
        admissions[scene] = admission
    registry["identities"].append({
        "identity": FRESH_IDENTITY, "phase": "DEVELOPMENT", "scene": FRESH_SCENE,
        "role": "REF_DOMAIN_SHIFT_ROUTE_YAW_REPAIR", "seed": FRESH_SEED,
        "formal_seed": False, "formal_scientific_exposure": False,
        "permanently_excluded_from_formal_v2": True,
    })
    registry["identity_count_after_lifecycle_repair"] = len(registry["identities"])
    registry["repair_history"] = [{
        "failed_identity": "RQ2TE2V4-ENG-001", "failed_scene": FAILED_SCENE,
        "classification": "SCENARIORUNNER_ROUTE_TRIGGER_YAW_MISMATCH_SCENARIO_IGNORED",
        "fresh_identity": FRESH_IDENTITY, "fresh_scene": FRESH_SCENE,
    }]
    registry.pop("registry_digest", None)
    registry["registry_digest"] = canonical_sha256(registry)
    write_json("ENGINEERING_IDENTITY_REGISTRY.json", registry)
    exclusions = json.loads((REPORT / "ENGINEERING_SEED_EXCLUSION_REGISTRY.json").read_text(encoding="utf-8"))
    exclusions["engineering_only_seeds"].append(FRESH_SEED)
    exclusions["repair_seed_added_before_exposure"] = True
    write_json("ENGINEERING_SEED_EXCLUSION_REGISTRY.json", exclusions)
    for phase in ("DEVELOPMENT", "CALIBRATION"):
        path = REPORT / (phase + "_SCENE_MANIFEST.json")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        rows = []
        for row in manifest["scenes"]:
            scene = row["scene"]
            if scene != FAILED_SCENE:
                binding = frozen_binding(scene)
                row.update({
                    "scene_configuration_sha256": binding["scene_configuration_sha256"],
                    "route_sha256": sha256(routes[scene]),
                    "lifecycle_admission_receipt_digest": admissions[scene]["admission_receipt_digest"],
                })
            rows.append(row)
        if phase == "DEVELOPMENT":
            binding = frozen_binding(FRESH_SCENE)
            rows.append({
                "scene": FRESH_SCENE, "identity": FRESH_IDENTITY, "seed": FRESH_SEED,
                "family": binding["family"], "scene_role": binding["scene_role"],
                "scene_configuration_sha256": binding["scene_configuration_sha256"],
                "route_path": str(routes[FRESH_SCENE].relative_to(ROOT)),
                "route_sha256": sha256(routes[FRESH_SCENE]),
                "lifecycle_admission_receipt_digest": admissions[FRESH_SCENE]["admission_receipt_digest"],
            })
        manifest["scenes"] = rows
        manifest["scene_count"] = len(rows)
        manifest["failed_identity_preserved"] = "RQ2TE2V4-ENG-001" if phase == "DEVELOPMENT" else None
        manifest.pop("manifest_digest", None)
        manifest["manifest_digest"] = canonical_sha256(manifest)
        write_json(phase + "_SCENE_MANIFEST.json", manifest)
    lifecycle = json.loads((REPORT / "ROUTE_LIFECYCLE_CLOSURE_RECEIPT.json").read_text(encoding="utf-8"))
    lifecycle["admissions"].update(admissions)
    lifecycle["development_defect_repair"] = {
        "failed_identity": "RQ2TE2V4-ENG-001", "root_owner": "TRIGGER_YAW_NOT_PRESERVED_DURING_ROUTE_MATERIALIZATION",
        "failed_route_preserved": True, "fresh_identity": FRESH_IDENTITY,
        "all_unexposed_routes_recertified": True,
    }
    lifecycle.pop("receipt_digest", None)
    lifecycle["receipt_digest"] = canonical_sha256(lifecycle)
    write_json("ROUTE_LIFECYCLE_CLOSURE_RECEIPT.json", lifecycle)
    seal = json.loads((REPORT / "FRESH_BLIND_GENERATOR_SEAL.json").read_text(encoding="utf-8"))
    seal["generator_source_sha256"] = sha256(ROOT / "tools/materialize_rq2_t_e2_v4_blind.py")
    seal["lifecycle_repair_before_calibration_and_blind"] = True
    seal.pop("seal_digest", None)
    seal["seal_digest"] = canonical_sha256(seal)
    write_json("FRESH_BLIND_GENERATOR_SEAL.json", seal)
    print(json.dumps({
        "status": "PASS_LIFECYCLE_ROUTE_YAW_REPAIR_REGISTERED_BEFORE_FRESH_EXPOSURE",
        "failed_identity_preserved": "RQ2TE2V4-ENG-001", "fresh_identity": FRESH_IDENTITY,
        "recertified_unexposed_routes": len(admissions),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
