"""Hash-verified access to the accepted formal freeze."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from driveclarify_rq2_t.measurement import canonical_sha256
from driveclarify_rq2_t_cg.contracts import assert_no_true_intent, validate_candidate_bindings
from driveclarify_rq2_t_cg_formal_freeze.scenes import SCENE_ORDER


ROOT = Path(__file__).resolve().parents[1]
FREEZE = ROOT / "reports" / "driveclarify_rq2_t_cg_formal_scene_and_protocol_freeze_v1"
EXPECTED_STATUS = "PASS_RQ2_T_CG_FORMAL_SCENE_AND_PROTOCOL_FREEZE_READY_FOR_INDEPENDENT_SEED_AUTHORIZATION"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def accepted_scenes() -> Sequence[Mapping[str, Any]]:
    # The machine-readable accepted manifest is authoritative.  In
    # particular, its route-length floats and resulting digests must not be
    # recomputed by a different Python/libm version during execution.
    return tuple(_load(FREEZE / "FINAL_FORMAL_SCENE_MANIFEST.json")["scenes"])


def scene_by_code(code: str) -> Mapping[str, Any]:
    rows = [scene for scene in accepted_scenes() if scene["scene_code"] == code]
    if len(rows) != 1:
        raise KeyError("RQ2_T_CG_FORMAL_SCENE_NOT_UNIQUE:" + str(code))
    return rows[0]


def scene_by_id(scene_id: str) -> Mapping[str, Any]:
    rows = [scene for scene in accepted_scenes() if scene["formal_scene_id"] == scene_id]
    if len(rows) != 1:
        raise KeyError("RQ2_T_CG_FORMAL_SCENE_ID_NOT_UNIQUE:" + str(scene_id))
    return rows[0]


def verify_freeze() -> Mapping[str, Any]:
    validation = _load(FREEZE / "FINAL_VALIDATION_RECEIPT.json")
    admission = _load(FREEZE / "PRE_SEED_ADMISSION_GATE_RECEIPT.json")
    manifest = _load(FREEZE / "FINAL_FORMAL_SCENE_MANIFEST.json")
    protocol = _load(FREEZE / "FORMAL_48_EPISODE_PROTOCOL.json")
    overlap = _load(FREEZE / "FORMAL_SCENE_FRESHNESS_AND_OVERLAP_AUDIT.json")
    source = _load(FREEZE / "SOURCE_FREEZE_RECEIPT.json")
    source_mismatches = [
        row["path"] for row in source["source_files"]
        if _sha(ROOT / row["path"]) != row["sha256"]
    ]
    certificates = []
    scenes = accepted_scenes()
    for scene in scenes:
        value = _load(FREEZE / "FORMAL_SCENE_CERTIFICATES" / (scene["scene_code"] + ".json"))
        certificates.append(value)
        if value["scene"]["formal_scene_digest"] != scene["formal_scene_digest"]:
            raise RuntimeError("RQ2_T_CG_FORMAL_CERTIFICATE_SCENE_MISMATCH")
        assert_no_true_intent(value)
        validate_candidate_bindings(value["scene"]["candidate_bindings"])
    checks = {
        "exact_status": validation["status"] == EXPECTED_STATUS,
        "admission": admission["admitted"] is True and admission["status"] == EXPECTED_STATUS,
        "source_freeze": source["status"] == "PASS" and not source_mismatches,
        "eight_scenes": manifest["scene_count"] == 8 == len(scenes),
        "scene_order": tuple(scene["scene_code"] for scene in scenes) == SCENE_ORDER,
        "six_seed_slots": protocol["shared_seed_slot_count"] == 6,
        "forty_eight_cells": protocol["planned_cell_count"] == 48,
        "zero_materialization": protocol["formal_seed_values_generated"] == protocol["formal_roster_rows_generated"] == 0,
        "zero_exposure": protocol["formal_scientific_exposures"] == 0,
        "certificates": len(certificates) == 8 and all(value["status"] == "PASS_STATIC_FORMAL_SCENE_CERTIFICATION" for value in certificates),
        "overlap": overlap["pass"] is True and overlap["exact_overlap_count"] == 0,
        "static_validation": (
            len({scene["formal_scene_id"] for scene in scenes}) == 8
            and len({scene["formal_scene_digest"] for scene in scenes}) == 8
            and all(
                canonical_sha256({key: value for key, value in scene.items() if key != "formal_scene_digest"})
                == scene["formal_scene_digest"]
                for scene in scenes
            )
        ),
    }
    failed = [key for key, value in checks.items() if not value]
    result = {
        "schema_version": "driveclarify.rq2_t_cg.execution.freeze_verification.v1",
        "checks": checks, "failed_checks": failed, "pass": not failed,
        "manifest_digest": manifest["manifest_digest"],
        "protocol_digest": protocol["protocol_digest"],
        "source_receipt_digest": source["receipt_digest"],
        "scene_digests": {scene["scene_code"]: scene["formal_scene_digest"] for scene in scenes},
        "formal_seed_values_generated": 0, "formal_scientific_exposures": 0,
    }
    result["verification_digest"] = canonical_sha256(result)
    return result


__all__ = ["EXPECTED_STATUS", "FREEZE", "accepted_scenes", "scene_by_code", "scene_by_id", "verify_freeze"]
