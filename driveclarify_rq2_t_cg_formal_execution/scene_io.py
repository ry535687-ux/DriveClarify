"""Prospective execution-scene loading with calibration/formal separation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from driveclarify_rq2_t.measurement import canonical_sha256
from driveclarify_rq2_t_cg.contracts import assert_no_true_intent, validate_candidate_bindings

from .specs import scene_by_code, scene_by_id


SCENE_MANIFEST_ENV = "DRIVECLARIFY_RQ2_T_CG_EXECUTION_SCENE_MANIFEST"


def _validate_external_scene(scene: Mapping[str, Any], *, engineering: bool) -> Mapping[str, Any]:
    value = dict(scene)
    execution_class = value.get("execution_class")
    if execution_class == "RQ2_T_CG_V2_CALIBRATION_DEVELOPMENT_ONLY":
        if not engineering:
            raise PermissionError("CALIBRATION_SCENE_FORBIDDEN_IN_FORMAL_CHILD")
        if value.get("calibration_only") is not True or value.get("formal_denominator_eligible") is not False:
            raise PermissionError("CALIBRATION_SCENE_EXCLUSION_FLAGS_MISSING")
    elif execution_class == "RQ2_T_CG_V2_ENGINEERING_SEAM_ONLY":
        if not engineering:
            raise PermissionError("ENGINEERING_SEAM_SCENE_FORBIDDEN_IN_FORMAL_CHILD")
        if value.get("formal_denominator_eligible") is not False or value.get("future_formal_v2_excluded") is not True:
            raise PermissionError("ENGINEERING_SEAM_SCENE_EXCLUSION_FLAGS_MISSING")
    elif execution_class == "RQ2_T_CG_FORMAL_V2_CONFIRMATORY":
        if engineering:
            raise PermissionError("FORMAL_V2_SCENE_FORBIDDEN_IN_ENGINEERING_CHILD")
        if value.get("calibration_only") is not False or value.get("formal_denominator_eligible") is not True:
            raise PermissionError("FORMAL_V2_SCENE_ADMISSION_FLAGS_INVALID")
    else:
        raise ValueError("RQ2_T_CG_EXTERNAL_EXECUTION_CLASS_INVALID")
    expected = canonical_sha256({key: item for key, item in value.items() if key != "formal_scene_digest"})
    if value.get("formal_scene_digest") != expected:
        raise ValueError("RQ2_T_CG_EXTERNAL_SCENE_DIGEST_MISMATCH")
    validate_candidate_bindings(value["candidate_bindings"])
    assert_no_true_intent(value)
    return value


def execution_scene_by_id(scene_id: str, *, engineering: bool) -> Mapping[str, Any]:
    path = os.environ.get(SCENE_MANIFEST_ENV)
    if not path:
        return scene_by_id(scene_id)
    scene = json.loads(Path(path).read_text(encoding="utf-8"))
    value = _validate_external_scene(scene, engineering=engineering)
    if str(value["formal_scene_id"]) != str(scene_id):
        raise ValueError("RQ2_T_CG_EXTERNAL_SCENE_ID_MISMATCH")
    return value


def execution_scene_for_cell(cell: Mapping[str, Any]) -> Mapping[str, Any]:
    path = cell.get("execution_scene_manifest")
    if not path:
        return scene_by_code(str(cell["scene_code"]))
    scene = json.loads(Path(str(path)).read_text(encoding="utf-8"))
    value = _validate_external_scene(
        scene, engineering=cell.get("engineering_qualification") is True,
    )
    if str(value["scene_code"]) != str(cell["scene_code"]):
        raise ValueError("RQ2_T_CG_EXTERNAL_SCENE_CODE_MISMATCH")
    return value


__all__ = ["SCENE_MANIFEST_ENV", "execution_scene_by_id", "execution_scene_for_cell"]
