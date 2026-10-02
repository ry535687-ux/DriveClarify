"""Small prospective calibration scene family for the B2 actionability gate."""

from __future__ import annotations

import copy
import math
from typing import Any, Mapping

from driveclarify_rq2_t.measurement import canonical_sha256
from driveclarify_rq2_t_cg.contracts import assert_no_true_intent, validate_candidate_bindings
from driveclarify_rq2_t_cg_formal_execution.specs import scene_by_code
from driveclarify_rq2_t_cg_formal_freeze.scenes import SCENE_ORDER


CALIBRATION_CONFIGURATION_ID = "RQ2TCG-V2-CAL-EARLY-ASYNC-REVEAL-02"
CALIBRATION_PARAMETERS = {
    "parameter_change_count": 1,
    "changed_parameter_family": "CONTROLLED_REVEAL_EVENT_GEOMETRY",
    "async_grounding_window_route_progress_m": [0.0, 0.005],
    "async_obligation_window_route_progress_m": [0.005, 0.15],
    "async_invalidation_window_route_progress_m": [4.4, 4.6],
    "sync_joint_window_route_progress_m": [0.0, 6.999],
    "actionability_reserve_simulation_s": 1.2,
    "commitment_threshold_m": {"LMK-ASYNC": 6.5, "LMK-SYNC": 7.0},
    "field_memory_policy_changed": False,
    "evidence_availability_threshold_changed": False,
    "sampling_alignment_changed": False,
    "vehicle_control_changed": False,
}

# Canonical points [0:10] from driveclarify_rq2_t/formal_routes/ord-01.xml.
# The older formal construction started at point 10, only ~2 m before the
# turning/stop geometry, which reproducibly left the unchanged baseline PID
# fully braked.  Restoring these points is an execution-seam launch repair;
# it does not change the route owner or the downstream scientific polyline.
ORD_CANONICAL_LAUNCH_PREFIX = (
    {"x": -710.9653930664062, "y": 3512.00927734375, "z": 362.4073486328125},
    {"x": -710.9658203125, "y": 3511.00927734375, "z": 362.3857727050781},
    {"x": -710.9662475585938, "y": 3510.00927734375, "z": 362.3641662597656},
    {"x": -710.9666748046875, "y": 3509.00927734375, "z": 362.34259033203125},
    {"x": -710.9671020507812, "y": 3508.00927734375, "z": 362.3210144042969},
    {"x": -710.967529296875, "y": 3507.00927734375, "z": 362.2994384765625},
    {"x": -710.9679565429688, "y": 3506.00927734375, "z": 362.2778625488281},
    {"x": -710.9683837890625, "y": 3505.00927734375, "z": 362.2562561035156},
    {"x": -710.9688110351562, "y": 3504.00927734375, "z": 362.23468017578125},
    {"x": -710.96923828125, "y": 3503.00927734375, "z": 362.2131042480469},
)


def _replace_prefix(value: Any, old: str, new: str) -> Any:
    if isinstance(value, str):
        return new + value[len(old):] if value.startswith(old) else value
    if isinstance(value, list):
        return [_replace_prefix(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: _replace_prefix(item, old, new) for key, item in value.items()}
    return value


def _event_digest(event: Mapping[str, Any]) -> Mapping[str, Any]:
    value = {key: copy.deepcopy(item) for key, item in event.items() if key != "event_digest"}
    value["event_digest"] = canonical_sha256(value)
    return value


def calibration_scene(scene_code: str, *, instance: str) -> Mapping[str, Any]:
    """Return one disjoint calibration-only scene under the selected config."""
    if scene_code not in ("LMK-ASYNC", "LMK-SYNC", "NONREVEAL", "USC-INTRINSIC"):
        raise ValueError("RQ2_T_CG_CALIBRATION_SCENE_UNSUPPORTED:" + scene_code)
    source = copy.deepcopy(dict(scene_by_code(scene_code)))
    old_id = str(source["formal_scene_id"])
    new_id = "RQ2TCG-V2-CAL-{}-{}".format(scene_code, instance)
    scene = _replace_prefix(source, old_id, new_id)
    scene["formal_scene_id"] = new_id
    scene["schema_version"] = "driveclarify.rq2_t_cg.v2_calibration_scene.v1"
    scene["execution_class"] = "RQ2_T_CG_V2_CALIBRATION_DEVELOPMENT_ONLY"
    scene["calibration_only"] = True
    scene["formal_denominator_eligible"] = False
    scene["future_formal_v2_excluded"] = True
    scene["calibration_configuration_id"] = CALIBRATION_CONFIGURATION_ID
    scene["calibration_parameters"] = copy.deepcopy(CALIBRATION_PARAMETERS)
    scene["calibration_instance"] = instance
    scene["formal_seed_values"] = []
    scene["formal_seed_values_generated"] = 0
    scene["formal_episode_count"] = 0
    scene["formal_scientific_exposures"] = 0
    scene["engineering_scene_promoted"] = False

    route = copy.deepcopy(scene["route"])
    route["calibration_route_instance_id"] = "{}-ROUTE".format(new_id)
    route["formal_denominator_eligible"] = False
    route.pop("route_spec_digest", None)
    route["route_spec_digest"] = canonical_sha256(route)
    scene["route"] = route

    layout = []
    for index, row in enumerate(scene["actor_or_task_layout"]):
        item = copy.deepcopy(row)
        item["layout_id"] = "{}-LAYOUT-{:02d}".format(new_id, index + 1)
        item["formal_denominator_eligible"] = False
        if scene_code == "LMK-ASYNC":
            item["route_progress_m"] = (0.02, 0.12)[index]
        layout.append(item)
    scene["actor_or_task_layout"] = layout

    events = [copy.deepcopy(row) for row in scene["events"]]
    for index, event in enumerate(events):
        event["event_id"] = "{}-EVENT-{:02d}".format(new_id, index + 1)
    if scene_code == "LMK-ASYNC":
        events[0]["activation"]["start_inclusive_m"] = 0.0
        events[0]["activation"]["end_exclusive_m"] = 0.005
        events[1]["activation"]["start_inclusive_m"] = 0.005
        events[1]["activation"]["end_exclusive_m"] = 0.15
    elif scene_code == "LMK-SYNC":
        events[0]["activation"]["start_inclusive_m"] = 0.0
        events[0]["activation"]["end_exclusive_m"] = 6.999
    scene["events"] = [_event_digest(event) for event in events]
    scene["calibration_change_disclosure"] = {
        "baseline_scene_id": old_id,
        "baseline_scene_digest": source["formal_scene_digest"],
        "changed": [
            "LMK controlled reveal window route-progress geometry",
            "calibration-only scene/route/layout/event identities",
        ] if scene_code == "LMK-ASYNC" else [
            "calibration-only scene/route/layout/event identities",
            "synchronous joint window aligned to selected calibration timing band",
        ] if scene_code == "LMK-SYNC" else [
            "calibration-only scene/route/layout/event identities",
        ],
        "unchanged": [
            "candidate interpretations and certified binding semantics",
            "field-specific memory TTLs and invalidation rules",
            "B1/B2 definitions and V1 sufficiency authority",
            "1.20 s actionability reserve",
            "commitment and TTCmt definitions",
            "checkpoint, PID, controller, route follower, and canonical route owner",
        ],
    }
    scene.pop("formal_scene_digest", None)
    scene["formal_scene_digest"] = canonical_sha256(scene)
    validate_candidate_bindings(scene["candidate_bindings"])
    assert_no_true_intent(scene)
    return scene


def _postcalibration_scene(
    scene_code: str, *, instance: str, execution_class: str,
    formal_denominator_eligible: bool, freeze_digest: str, layout_variant_m: float,
) -> Mapping[str, Any]:
    if scene_code not in SCENE_ORDER:
        raise ValueError("RQ2_T_CG_POSTCALIBRATION_SCENE_UNSUPPORTED:" + scene_code)
    source = copy.deepcopy(dict(scene_by_code(scene_code)))
    old_id = str(source["formal_scene_id"])
    new_id = "RQ2TCG-V2-{}-{}-{}".format(
        "SEAM" if execution_class.endswith("ENGINEERING_SEAM_ONLY") else "FORMAL",
        scene_code, instance,
    )
    scene = _replace_prefix(source, old_id, new_id)
    scene["formal_scene_id"] = new_id
    scene["schema_version"] = "driveclarify.rq2_t_cg.v2_postcalibration_scene.v1"
    scene["execution_class"] = execution_class
    scene["calibration_only"] = False
    scene["formal_denominator_eligible"] = bool(formal_denominator_eligible)
    scene["future_formal_v2_excluded"] = not bool(formal_denominator_eligible)
    scene["selected_calibration_configuration_id"] = CALIBRATION_CONFIGURATION_ID
    scene["selected_calibration_freeze_digest"] = str(freeze_digest)
    scene["selected_mechanism_parameters"] = copy.deepcopy(CALIBRATION_PARAMETERS)
    scene["execution_instance"] = instance
    scene["formal_seed_values"] = []
    scene["formal_seed_values_generated"] = 0
    scene["formal_episode_count"] = 0
    scene["formal_scientific_exposures"] = 0
    scene["engineering_scene_promoted"] = False

    route = copy.deepcopy(scene["route"])
    if scene_code == "ORD-ASYNC":
        route["waypoints"] = [copy.deepcopy(row) for row in ORD_CANONICAL_LAUNCH_PREFIX] + route["waypoints"]
        route["source_slice_half_open"] = [0, 110]
        route["route_length_m"] = sum(
            math.dist(
                (float(a["x"]), float(a["y"]), float(a["z"])),
                (float(b["x"]), float(b["y"]), float(b["z"])),
            )
            for a, b in zip(route["waypoints"], route["waypoints"][1:])
        )
        route["engineering_launch_prefix_repair"] = {
            "status": "RESTORED_CANONICAL_SOURCE_POINTS_0_TO_9",
            "prior_source_slice_half_open": [10, 110],
            "repaired_source_slice_half_open": [0, 110],
            "reason": "REPRODUCIBLE_ZERO_PROGRESS_AT_NEAR_TURN_SPAWN",
            "route_owner_changed": False,
        }
    route["v2_execution_route_instance_id"] = new_id + "-ROUTE"
    route["calibration_scene_overlap"] = False
    route.pop("route_spec_digest", None)
    route["route_spec_digest"] = canonical_sha256(route)
    scene["route"] = route

    layout = []
    for index, row in enumerate(scene["actor_or_task_layout"]):
        item = copy.deepcopy(row)
        item["layout_id"] = "{}-LAYOUT-{:02d}".format(new_id, index + 1)
        item["calibration_layout_overlap"] = False
        item["lateral_offset_m"] = float(item["lateral_offset_m"]) + (
            layout_variant_m if index == 0 else -layout_variant_m
        )
        if scene_code == "LMK-ASYNC":
            item["route_progress_m"] = (
                0.02 + layout_variant_m,
                0.12 + layout_variant_m,
            )[index]
        layout.append(item)
    scene["actor_or_task_layout"] = layout

    events = [copy.deepcopy(row) for row in scene["events"]]
    for index, event in enumerate(events):
        event["event_id"] = "{}-EVENT-{:02d}".format(new_id, index + 1)
    if scene_code == "LMK-ASYNC":
        events[0]["activation"].update({"start_inclusive_m": 0.0, "end_exclusive_m": 0.005})
        events[1]["activation"].update({"start_inclusive_m": 0.005, "end_exclusive_m": 0.15})
    elif scene_code == "LMK-SYNC":
        events[0]["activation"].update({"start_inclusive_m": 0.0, "end_exclusive_m": 6.999})
    scene["events"] = [_event_digest(event) for event in events]
    scene["postcalibration_change_disclosure"] = {
        "baseline_scene_id": old_id,
        "calibration_scene_identity_reused": False,
        "calibration_route_identity_reused": False,
        "calibration_layout_identity_reused": False,
        "candidate_interpretations_and_binding_semantics_changed": False,
        "selected_mechanism_parameters_changed_after_freeze": False,
        "ordinary_execution_seam_repair": (
            "RESTORE_ORD_CANONICAL_LAUNCH_PREFIX" if scene_code == "ORD-ASYNC" else None
        ),
    }
    scene.pop("formal_scene_digest", None)
    scene["formal_scene_digest"] = canonical_sha256(scene)
    validate_candidate_bindings(scene["candidate_bindings"])
    assert_no_true_intent(scene)
    return scene


def engineering_seam_scene(scene_code: str, *, instance: str, freeze_digest: str) -> Mapping[str, Any]:
    return _postcalibration_scene(
        scene_code, instance=instance,
        execution_class="RQ2_T_CG_V2_ENGINEERING_SEAM_ONLY",
        formal_denominator_eligible=False, freeze_digest=freeze_digest,
        layout_variant_m=0.005,
    )


def future_formal_scene(scene_code: str, *, instance: str, freeze_digest: str) -> Mapping[str, Any]:
    return _postcalibration_scene(
        scene_code, instance=instance,
        execution_class="RQ2_T_CG_FORMAL_V2_CONFIRMATORY",
        formal_denominator_eligible=True, freeze_digest=freeze_digest,
        layout_variant_m=0.01,
    )


__all__ = [
    "CALIBRATION_CONFIGURATION_ID", "CALIBRATION_PARAMETERS", "calibration_scene",
    "engineering_seam_scene", "future_formal_scene",
]
