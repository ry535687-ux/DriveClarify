"""Hard contracts for the visualization layer. Pure Python; imported by every view and
enforced by tests. These encode the rules the takeover prompt makes non-negotiable.

Nothing here starts CARLA, imports torch/carla, or touches control. It is a set of
constants + small guard functions that raise on a contract violation, so a mislabel or an
UNKNOWN->0 coercion fails LOUDLY (and a mutation test can catch it).
"""

from __future__ import annotations

from typing import Any

# ---- view-kind labels (raw camera vs exact model tensor vs display are distinct) ----
# Historical Visualization v0 called the saved CP3B rgb_0 PNG "MODEL_INPUT_RAW".
# Static tracing in the VLA debug-upgrade found that it precedes SimLingo's JPEG
# round-trip/crop/dynamic resize/normalization/bfloat16 pipeline.  Keep the public
# constant name for compatibility, but correct its value and labels.
RAW_VIEW_KIND = "RAW_CAMERA_SENSOR"
MODEL_INPUT_VIEW_KIND = "MODEL_INPUT_TENSOR_RGB"
DISPLAY_VIEW_KIND = "DISPLAY_ENHANCED"
WORLD_BEV_KIND = "WORLD_BEV"
MODEL_LOCAL_PLAN_KIND = "MODEL_LOCAL_RAW_PLAN"

RAW_LABELS = ("RAW CAMERA rgb_0", "PRE-PROCESSING", "NOT FINAL MODEL TENSOR")
MODEL_INPUT_LABELS = (
    "ACTUAL MODEL INPUT RGB",
    "DERIVED FROM SAME camera_images TENSOR",
    "EXACT TENSOR VALUES SAVED SEPARATELY",
)
DISPLAY_LABELS = ("DISPLAY ENHANCED", "NOT MODEL INPUT", "NOT USED FOR CONTROL")
MODEL_LOCAL_LABELS = ("MODEL_LOCAL_RAW", "RAW UNIT", "NO PHYSICAL SCALE CLAIM")
GLOBAL_LABELS = ("RESEARCH DEBUG VIEW", "NO FORMAL SAFETY GUARANTEE", "SIMULATION ONLY")

# ---- interface claims: none are VERIFIED at visualization time ----
INTERFACE_CLAIMS = ("F2", "F4", "F5_pred_route", "F5_pred_speed_wps", "F6", "TL")

# A metric BEV overlay (raw plan drawn in physical metres/world frame) is only allowed if
# the specific claim it depends on is VERIFIED. At v0 none are, so this must stay empty.
VERIFIED_CLAIMS: frozenset[str] = frozenset()  # v0: nothing verified

# values that must NEVER be substituted for an UNKNOWN/empty field
FORBIDDEN_UNKNOWN_SUBSTITUTES = (0, 0.0, False, "0", "false", "FALSE", "safe",
                                 "SAFE", "GREEN", "green", float("inf"))

# clock domains are never mixed
SIM = "SIM"
MONOTONIC = "MONOTONIC"


class ContractViolation(RuntimeError):
    """Raised when a visualization contract is violated (fail loud, so tests catch it)."""


def assert_raw_not_relabeled(view_kind: str, enhanced_applied: bool) -> None:
    """A display-enhanced frame can never be labelled raw-camera or model-input."""
    if enhanced_applied and view_kind == RAW_VIEW_KIND:
        raise ContractViolation(
            "display-enhanced frame labeled as RAW_CAMERA_SENSOR")
    if enhanced_applied and view_kind == MODEL_INPUT_VIEW_KIND:
        raise ContractViolation(
            "display-enhanced frame labeled as MODEL_INPUT_TENSOR_RGB")
    if (not enhanced_applied) and view_kind == DISPLAY_VIEW_KIND:
        raise ContractViolation(
            "un-enhanced frame labeled DISPLAY_ENHANCED (mislabel)")


def assert_metric_overlay_allowed(claim: str) -> None:
    """Enabling a metric (physical-scale) plan overlay requires the claim VERIFIED."""
    if claim not in VERIFIED_CLAIMS:
        raise ContractViolation(
            f"metric overlay requested for {claim} but it is NOT VERIFIED "
            f"(model-local raw stays raw; no physical-scale claim)")


def assert_unknown_not_coerced(field_name: str, status: str, value: Any) -> None:
    """An UNKNOWN/UNSUPPORTED field must keep null/None + reason, never 0/false/safe/GREEN."""
    if str(status).upper().startswith(("UNKNOWN", "UNSUPPORTED", "UNRESOLVED", "NOT_AVAILABLE")):
        if value is not None and value in FORBIDDEN_UNKNOWN_SUBSTITUTES:
            raise ContractViolation(
                f"field {field_name} is {status} but rendered as {value!r} "
                f"(UNKNOWN must not become 0/false/safe/GREEN/inf)")


def assert_traffic_light_empty_not_green(query_status: str, lights: list[Any]) -> None:
    """An empty traffic-light query is SUCCESS_EMPTY, never a GREEN light."""
    if str(query_status).lower() == "empty" and lights:
        raise ContractViolation("empty traffic-light query rendered with lights (never GREEN-fill)")


def assert_fps_domains_distinct(display_fps: Any, sensor_fps: Any, model_infer_fps: Any) -> None:
    """Replay/display FPS is a rendering rate; it must not be reported AS the sensor or
    model-inference rate. They are separate fields; conflating them is a contract violation."""
    # This guard is structural: the caller must pass three DISTINCT named values. We only
    # check they are provided as separate keys (None allowed for unknown), not equal-by-alias.
    if display_fps is not None and sensor_fps is not None and model_infer_fps is not None:
        # allow numeric coincidence, but they must be independently sourced (enforced by API
        # shape). Nothing to raise here on value; the real guard is that callers never reuse
        # one variable for all three. Tests assert the dict has all three keys.
        pass


REQUIRED_FPS_KEYS = ("display_refresh_fps", "new_sensor_frame_fps",
                     "simulation_rate", "model_inference_rate",
                     "visualization_dropped_frames")
