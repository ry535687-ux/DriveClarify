#!/usr/bin/env python3
"""Register ENG-011 after ENG-005's immutable CUDA-allocation failure."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_rq2_t.measurement import canonical_sha256  # noqa: E402
from driveclarify_rq2_t_cg.lifecycle import static_route_admission  # noqa: E402
from driveclarify_rq2_t_cg.scene_bindings import frozen_binding  # noqa: E402
from tools.prepare_rq2_t_cg import REPORT, materialize_route, sha256, write_json  # noqa: E402


def main() -> int:
    attempts = [json.loads(line) for line in (REPORT / "ENGINEERING_ATTEMPT_LEDGER.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    attempted = {row["identity"] for row in attempts}
    if "RQ2TCG-ENG-005" not in attempted or "RQ2TCG-ENG-011" in attempted:
        raise RuntimeError("RQ2_T_CG_INFRASTRUCTURE_RETRY_EXPOSURE_SET_UNEXPECTED")
    failed = json.loads((REPORT / "NATIVE_EVIDENCE/RQ2TCG-ENG-005/attempt_01/ENGINEERING_EPISODE_RESULT.json").read_text(encoding="utf-8"))
    stdout = (REPORT / "NATIVE_EVIDENCE/RQ2TCG-ENG-005/attempt_01/evaluator_stdout.log").read_text(encoding="utf-8", errors="replace")
    if failed.get("status") != "INVALID_ENGINEERING_ATTEMPT" or "torch.cuda.OutOfMemoryError" not in stdout:
        raise RuntimeError("RQ2_T_CG_ENG_005_NOT_CERTIFIED_AS_CUDA_INFRASTRUCTURE_FAILURE")

    scene = "CG-LMK-SYNC-B"
    identity = "RQ2TCG-ENG-011"
    binding = frozen_binding(scene)
    path = materialize_route(scene)
    registry_path = REPORT / "ENGINEERING_IDENTITY_REGISTRY.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if any(row["identity"] == identity for row in registry["identities"]):
        raise RuntimeError("RQ2_T_CG_ENG_011_ALREADY_REGISTERED")
    if len(registry["identities"]) >= int(registry["maximum_authorized_identities"]):
        raise RuntimeError("RQ2_T_CG_ENGINEERING_IDENTITY_CAP_EXHAUSTED")
    row = {
        "identity": identity,
        "scene": scene,
        "template": binding["template"],
        "seed": binding["engineering_seed"],
        "phase": "ENGINEERING_ONLY",
        "route_path": str(path.relative_to(ROOT)),
        "route_sha256": sha256(path),
        "scene_configuration_sha256": binding["scene_configuration_sha256"],
        "registered_before_execution": True,
        "formal_scientific_exposure": False,
        "replacement_for": "RQ2TCG-ENG-005",
        "repair_reason": "CUDA_OOM_FROM_PRECEDING_NATIVE_CHILD_EXIT_OVERLAP",
        "mechanism_configuration_changed": False,
    }
    registry["identities"].append(row)
    registry["identity_count"] = len(registry["identities"])
    registry["preexposure_repair_history"].append({
        "infrastructure_failed_identity": "RQ2TCG-ENG-005",
        "replacement_identity": identity,
        "failure": "CUDA_OOM_DURING_AGENT_INITIALIZATION",
        "repair": "SERIAL_START_AFTER_GPU_PROCESS_EXIT_CONFIRMED",
        "mechanism_configuration_changed": False,
        "threshold_or_scientific_definition_changed": False,
    })
    registry.pop("registry_digest", None)
    registry["registry_digest"] = canonical_sha256(registry)
    write_json(registry_path, registry)

    exclusion_path = REPORT / "ENGINEERING_SEED_EXCLUSION_REGISTRY.json"
    exclusion = json.loads(exclusion_path.read_text(encoding="utf-8"))
    exclusion["seeds"].append({
        "seed": row["seed"], "identity": identity, "scene": scene,
        "permanent_exclusion": "ALL_FUTURE_RQ2_T_CG_DEV_TEST_PAPER_DENOMINATORS",
    })
    exclusion["seed_count"] = len(exclusion["seeds"])
    exclusion.pop("registry_digest", None)
    exclusion["registry_digest"] = canonical_sha256(exclusion)
    write_json(exclusion_path, exclusion)

    certificate = {
        "schema_version": "driveclarify.rq2_t_cg.engineering_scene_certificate.v1",
        "scene": scene,
        "identity": identity,
        "template": binding["template"],
        "binding": binding,
        "route_admission": static_route_admission(path, binding),
        "candidate_certification": binding["candidate_set_certification"],
        "candidate_bindings_semantically_distinct": True,
        "passenger_intent_identified": False,
        "event_owners_independent_of_views_and_outcomes": True,
        "accepted_v1_shared_prefix_owner": "REF-01",
        "formal_scientific_exposure": False,
        "infrastructure_retry_only": True,
    }
    certificate["certificate_digest"] = canonical_sha256(certificate)
    write_json(REPORT / "ENGINEERING_SCENE_CERTIFICATES" / (scene + ".json"), certificate)

    manifest_path = REPORT / "ENGINEERING_SCENE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["scenes"].append({
        **row,
        "certificate_digest": certificate["certificate_digest"],
        "candidate_set_digest": binding["candidate_set_certification"]["candidate_set_digest"],
        "event_schedule_digest": canonical_sha256(binding["current_event_schedule"]),
        "owner_scene_key": "LMK-01",
        "exposure_status": "PROSPECTIVE_UNEXPOSED",
    })
    manifest["scene_contract_count"] = len(manifest["scenes"])
    manifest.pop("manifest_digest", None)
    manifest["manifest_digest"] = canonical_sha256(manifest)
    write_json(manifest_path, manifest)

    receipt = {
        "schema_version": "driveclarify.rq2_t_cg.infrastructure_retry_registration.v1",
        "failed_identity_preserved": "RQ2TCG-ENG-005",
        "replacement_identity_registered": identity,
        "failure_class": "CUDA_RESOURCE_CONTENTION_DURING_AGENT_INITIALIZATION",
        "gpu_processes_present_before_registration": 0,
        "mechanism_configuration_changed": False,
        "threshold_or_scientific_definition_changed": False,
        "total_registered_identities": registry["identity_count"],
        "maximum_authorized_identities": registry["maximum_authorized_identities"],
        "automatic_e2_reopened": False,
        "formal_seed_values_generated": 0,
        "formal_scientific_exposures": 0,
    }
    receipt["receipt_digest"] = canonical_sha256(receipt)
    write_json(REPORT / "LMK_SYNC_INFRASTRUCTURE_RETRY_RECEIPT.json", receipt)
    print(json.dumps({"status": "PASS_INFRASTRUCTURE_RETRY_REGISTRATION", "identity": identity,
                      "identity_count": registry["identity_count"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
