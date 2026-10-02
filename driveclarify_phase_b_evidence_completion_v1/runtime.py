"""Passive B1 evidence capture with a calibrated CARLA-world route source.

This append-only runtime deliberately reuses the B0-R1 candidate-forward and
baseline-authority lifecycle.  Its only behavioral difference is evidence
binding: topology and projection use the dense CARLA-world route already owned
by the agent instead of the GPS-converted local route-planner deque.
"""

from __future__ import annotations

import os
import math
from dataclasses import asdict, replace
from typing import Any, Optional

from driveclarify_candidate_consequence_equivalence.renderer import (
    ConsequenceAwareGroundedSemantic,
    ConsequenceAwareOfficialDreamingRenderer,
)
from driveclarify_decision_window_carla_probe_v1.runtime import (
    DecisionWindowEvidenceProbeRuntime,
    _project_to_polyline,
)
from driveclarify_grounded_language_v1_extension_e1_r1.contracts import (
    canonical_sha256,
)
from driveclarify_grounded_language_v1_extension_e1_r1.runtime import (
    TopologyAwareReferentialRuntime,
    _live_map,
)
from driveclarify_paper_mvp_runtime.simlingo_binding import _points


FEATURE_FLAG = "DRIVECLARIFY_PHASE_B_EVIDENCE_COMPLETION_V1"
RUN_ID = "B2"
RUNTIME_VERSION = "DRIVECLARIFY_PHASE_B_EVIDENCE_COMPLETION_V1"
FINAL_CAPTURE_STATUS = "PASS_B1_EVIDENCE_CAPTURED_PENDING_FAIL_CLOSED_ANALYSIS"
ROUTE_SOURCE = "AGENT_OWNED_DENSE_CARLA_WORLD_ROUTE"
MODEL_COORDINATE_CONTRACT_ID = (
    "SIMLINGO_CONTROL_PID_CHECKPOINTS_1M_EGO_X_FORWARD_Y_RIGHT"
)


class RouteDetachmentError(ValueError):
    """Typed fail-closed error for an invalid agent-owned world-route row."""

    def __init__(self, reason_code: str, *, row_index: Optional[int] = None) -> None:
        self.reason_code = str(reason_code)
        self.row_index = row_index
        suffix = "" if row_index is None else ":ROW_{}".format(row_index)
        super().__init__(self.reason_code + suffix)


def _validated_location(value: Any) -> Any:
    """Validate a Location-shaped value without accepting methods as data."""

    if value is None or callable(value):
        raise RouteDetachmentError("ROUTE_LOCATION_UNRESOLVABLE")
    try:
        coordinates = (float(value.x), float(value.y), float(value.z))
    except (AttributeError, TypeError, ValueError):
        raise RouteDetachmentError("ROUTE_LOCATION_UNRESOLVABLE")
    if not all(math.isfinite(coordinate) for coordinate in coordinates):
        raise RouteDetachmentError("ROUTE_LOCATION_NONFINITE")
    return value


def _location(value: Any) -> Any:
    """Return a location from a Transform/Waypoint/Location-shaped object."""

    # carla.Transform owns both `.location` data and a callable `.transform()`
    # point-transform method.  The data member must be resolved first.
    location = getattr(value, "location", None)
    if location is not None:
        return _validated_location(location)

    # A carla.Location reaches this direct coordinate path.
    try:
        return _validated_location(value)
    except RouteDetachmentError:
        pass

    # Waypoint-style fallback: `.transform` is a Transform value.  A callable
    # here is explicitly rejected and never reaches coordinate conversion.
    transform = getattr(value, "transform", None)
    if callable(transform):
        raise RouteDetachmentError("CALLABLE_TRANSFORM_IS_NOT_LOCATION")
    if transform is None:
        raise RouteDetachmentError("ROUTE_LOCATION_UNRESOLVABLE")
    return _validated_location(getattr(transform, "location", None))


def _detached_dense_route(agent: Any) -> tuple:
    """Detach the agent-owned route without advancing any planner."""

    source = getattr(agent, "org_dense_route_world_coord", None)
    source_attribute = "org_dense_route_world_coord"
    if not source:
        source = getattr(agent, "_global_plan_world_coord", None)
        source_attribute = "_global_plan_world_coord"
    if not source:
        raise RouteDetachmentError("ROUTE_SOURCE_MISSING_OR_EMPTY")
    rows = []
    for row_index, item in enumerate(source):
        try:
            location = _location(item[0])
            rows.append(
                (
                    (float(location.x), float(location.y), float(location.z)),
                    item[1],
                )
            )
        except RouteDetachmentError as error:
            raise RouteDetachmentError(error.reason_code, row_index=row_index)
        except (AttributeError, IndexError, TypeError, ValueError):
            raise RouteDetachmentError("ROUTE_ROW_INVALID", row_index=row_index)
    if not rows:
        raise RouteDetachmentError("ROUTE_DETACHMENT_EMPTY")
    return tuple(rows), source_attribute


def _bind_dashboard_run_identity(receipt: dict, run_id: str) -> None:
    """Atomically bind the passive panel identity to the current runtime run."""

    dashboard = dict(receipt.get("decision_window_dashboard", {}))
    dashboard["run_id"] = str(run_id)
    receipt["decision_window_dashboard"] = dashboard
    receipt["probe_run_id"] = str(run_id)


def _waypoint_row(map_object: Any, x_value: float, y_value: float, option: str) -> dict:
    try:
        import carla

        location = carla.Location(x=float(x_value), y=float(y_value), z=0.0)
        waypoint = map_object.get_waypoint(
            location, project_to_road=True, lane_type=carla.LaneType.Driving
        )
    except (AttributeError, ImportError, RuntimeError, TypeError):
        return {
            "status": "UNKNOWN",
            "reason_code": "LIVE_MAP_WAYPOINT_QUERY_FAILED",
            "route_xy_m": [float(x_value), float(y_value)],
            "road_option": str(option),
        }
    transform = waypoint.transform
    location = transform.location
    return {
        "status": "AVAILABLE",
        "route_xy_m": [float(x_value), float(y_value)],
        "road_option": str(option),
        "waypoint_xy_m": [float(location.x), float(location.y)],
        "road_id": int(waypoint.road_id),
        "lane_id": int(waypoint.lane_id),
        "section_id": int(waypoint.section_id),
        "is_junction": bool(waypoint.is_junction),
        "junction_id": int(waypoint.junction_id) if waypoint.is_junction else None,
        "lane_width_m": float(waypoint.lane_width),
        "yaw_deg": float(transform.rotation.yaw),
    }


def _waypoint_xy(waypoint: Any) -> tuple[float, float]:
    location = waypoint.transform.location
    return float(location.x), float(location.y)


def _polyline_arc(points: Any) -> float:
    return sum(
        math.hypot(
            float(right[0]) - float(left[0]),
            float(right[1]) - float(left[1]),
        )
        for left, right in zip(points, points[1:])
    )


def _point_at_arc(points: Any, distance_m: float) -> tuple[float, float]:
    if not points:
        raise ValueError("POLYLINE_EMPTY")
    remaining = max(0.0, float(distance_m))
    for left, right in zip(points, points[1:]):
        length = math.hypot(
            float(right[0]) - float(left[0]),
            float(right[1]) - float(left[1]),
        )
        if length <= 1e-9:
            continue
        if remaining <= length:
            fraction = remaining / length
            return (
                float(left[0]) + fraction * (float(right[0]) - float(left[0])),
                float(left[1]) + fraction * (float(right[1]) - float(left[1])),
            )
        remaining -= length
    return float(points[-1][0]), float(points[-1][1])


def _corridor_separation_series(connector: Any) -> dict:
    """Compare a live connector and the agent route at common arc positions."""

    branch = [tuple(row[:2]) for row in connector.get("polyline_xy_m", [])]
    continuation = [
        tuple(row[:2]) for row in connector.get("route_continuation_xy_m", [])
    ]
    if len(branch) < 2 or len(continuation) < 2:
        return {"status": "UNKNOWN", "reason_code": "CORRIDOR_POLYLINE_MISSING"}
    origin = branch[0]
    continuation = [origin] + [
        point
        for point in continuation
        if math.hypot(point[0] - origin[0], point[1] - origin[1]) > 0.25
    ]
    covered_arc = min(_polyline_arc(branch), _polyline_arc(continuation))
    if covered_arc <= 1.0:
        return {
            "status": "UNKNOWN",
            "reason_code": "CORRIDOR_COMMON_PARAMETER_TOO_SHORT",
        }
    spacing = float(connector.get("sampling_distance_m", 0.5))
    if not math.isfinite(spacing) or spacing <= 0.0:
        return {
            "status": "UNKNOWN",
            "reason_code": "CORRIDOR_SAMPLING_DISTANCE_INVALID",
        }
    count = int(math.floor(covered_arc / spacing)) + 1
    distances = [min(covered_arc, index * spacing) for index in range(count)]
    branch_samples = [_point_at_arc(branch, value) for value in distances]
    continuation_samples = [_point_at_arc(continuation, value) for value in distances]
    separations = [
        math.hypot(left[0] - right[0], left[1] - right[1])
        for left, right in zip(branch_samples, continuation_samples)
    ]
    entry_progress = float(connector["junction_entry_progress_m"])
    return {
        "status": "AVAILABLE",
        "sampling_distance_m": spacing,
        "covered_arc_m": covered_arc,
        "progress_samples_m": [entry_progress + value for value in distances],
        "separation_samples_m": separations,
        "branch_samples_xy_m": [list(value) for value in branch_samples],
        "continuation_samples_xy_m": [list(value) for value in continuation_samples],
    }


def _first_structural_divergence_progress(
    connector: Any,
    *,
    lane_clearance_m: float,
    calibrated_uncertainty_m: float,
    hysteresis_samples: int = 3,
) -> dict:
    """Derive a general evidence-probe stop from live corridor separation."""

    series = _corridor_separation_series(connector)
    if series.get("status") != "AVAILABLE":
        return {
            "status": "UNKNOWN",
            "reason_code": str(series.get("reason_code", "CORRIDOR_SERIES_UNKNOWN")),
        }
    numeric = (float(lane_clearance_m), float(calibrated_uncertainty_m))
    if (
        not all(math.isfinite(value) for value in numeric)
        or numeric[0] <= 0.0
        or numeric[1] < 0.0
        or int(hysteresis_samples) < 1
    ):
        return {
            "status": "UNKNOWN",
            "reason_code": "STRUCTURAL_DIVERGENCE_THRESHOLD_INVALID",
        }
    threshold = numeric[0] + numeric[1]
    above = [
        float(value) > threshold for value in series["separation_samples_m"]
    ]
    for start in range(0, len(above) - int(hysteresis_samples) + 1):
        if all(above[start : start + int(hysteresis_samples)]):
            return {
                "status": "AVAILABLE",
                "progress_m": float(series["progress_samples_m"][start]),
                "lane_clearance_m": numeric[0],
                "calibrated_uncertainty_m": numeric[1],
                "hysteresis_samples": int(hysteresis_samples),
                "sampling_distance_m": float(series["sampling_distance_m"]),
                "derivation": "LIVE_CONNECTOR_SEPARATION_HYSTERESIS",
                "candidate_specific_numeric_target": False,
            }
    return {
        "status": "UNKNOWN",
        "reason_code": "STRUCTURAL_DIVERGENCE_HYSTERESIS_NOT_SATISFIED",
    }


def _conservative_commitment_progress(
    *,
    onset_progress_m: float,
    speed_upper_bound_mps: float,
    fixed_delta_seconds: float,
    control_response_upper_bound_frames: int,
    safety_margin_distance_m: float,
) -> dict:
    """Derive the pre-onset capture boundary from frozen response bounds."""

    numeric = (
        float(onset_progress_m),
        float(speed_upper_bound_mps),
        float(fixed_delta_seconds),
        float(safety_margin_distance_m),
    )
    frames = int(control_response_upper_bound_frames)
    if (
        not all(math.isfinite(value) for value in numeric)
        or numeric[1] < 0.0
        or numeric[2] <= 0.0
        or numeric[3] < 0.0
        or frames < 1
    ):
        return {
            "status": "UNKNOWN",
            "reason_code": "CONSERVATIVE_COMMITMENT_INPUT_INVALID",
        }
    response_seconds = numeric[2] * frames
    response_distance = numeric[1] * response_seconds
    lateral_response_distance = numeric[1] * response_seconds
    required_distance = response_distance + lateral_response_distance + numeric[3]
    return {
        "status": "AVAILABLE",
        "progress_m": numeric[0] - required_distance,
        "onset_progress_m": numeric[0],
        "speed_upper_bound_mps": numeric[1],
        "fixed_delta_seconds": numeric[2],
        "control_response_upper_bound_frames": frames,
        "response_distance_m": response_distance,
        "lateral_response_distance_m": lateral_response_distance,
        "safety_margin_distance_m": numeric[3],
        "required_switch_distance_m": required_distance,
        "derivation": "FROZEN_CONTROL_AND_LATERAL_RESPONSE_PLUS_LIVE_LANE_SAFETY_MARGIN",
        "candidate_specific_numeric_target": False,
    }


def _trace_waypoint_connector(
    entry: Any, exit_waypoint: Any, *, sampling_distance_m: float = 0.5
) -> dict:
    """Detach one legal CARLA junction connector without creating a planner."""

    try:
        target = _waypoint_xy(exit_waypoint)
        current = entry
        rows = [_waypoint_xy(current)]
        visited = set()
        exit_reached = math.hypot(rows[-1][0] - target[0], rows[-1][1] - target[1]) <= (
            sampling_distance_m * 1.5
        )
        for _ in range(256):
            if exit_reached:
                break
            identity = (
                int(getattr(current, "road_id", -1)),
                int(getattr(current, "lane_id", 0)),
                round(rows[-1][0], 3),
                round(rows[-1][1], 3),
            )
            if identity in visited:
                break
            visited.add(identity)
            candidates = tuple(current.next(float(sampling_distance_m)))
            if not candidates:
                break
            current = min(
                candidates,
                key=lambda value: math.hypot(
                    _waypoint_xy(value)[0] - target[0],
                    _waypoint_xy(value)[1] - target[1],
                ),
            )
            point = _waypoint_xy(current)
            rows.append(point)
            distance = math.hypot(point[0] - target[0], point[1] - target[1])
            same_exit_lane = (
                int(getattr(current, "road_id", -1))
                == int(getattr(exit_waypoint, "road_id", -2))
                and int(getattr(current, "lane_id", 0))
                == int(getattr(exit_waypoint, "lane_id", 1))
            )
            exit_reached = distance <= sampling_distance_m * 1.5 or (
                same_exit_lane and distance <= sampling_distance_m * 3.0
            )
        if exit_reached and math.hypot(rows[-1][0] - target[0], rows[-1][1] - target[1]) > 1e-6:
            rows.append(target)
        return {
            "status": "AVAILABLE" if exit_reached and len(rows) >= 2 else "UNKNOWN",
            "reason_code": None if exit_reached and len(rows) >= 2 else "LIVE_MAP_CONNECTOR_TRACE_DID_NOT_REACH_EXIT",
            "sampling_distance_m": float(sampling_distance_m),
            "polyline_xy_m": [[float(x), float(y)] for x, y in rows],
            "exit_reached": bool(exit_reached),
            "entry_road_id": int(getattr(entry, "road_id", -1)),
            "entry_lane_id": int(getattr(entry, "lane_id", 0)),
            "exit_road_id": int(getattr(exit_waypoint, "road_id", -1)),
            "exit_lane_id": int(getattr(exit_waypoint, "lane_id", 0)),
        }
    except (AttributeError, RuntimeError, TypeError, ValueError) as error:
        return {
            "status": "UNKNOWN",
            "reason_code": "LIVE_MAP_CONNECTOR_TRACE_FAILED",
            "error_type": type(error).__name__,
            "polyline_xy_m": [],
            "exit_reached": False,
        }


def _candidate_connector_evidence(
    map_object: Any, route_rows: list, opportunities: list
) -> list:
    """Bind each selected opportunity to its exact live-map lane connector."""

    rows = []
    for label, opportunity in zip(("A", "B"), opportunities[:2]):
        route_index = int(opportunity.get("route_opportunity_index", -1))
        if route_index < 0 or route_index >= len(route_rows):
            rows.append(
                {
                    "candidate_id": label,
                    "status": "UNKNOWN",
                    "reason_code": "OPPORTUNITY_ROUTE_INDEX_OUT_OF_RANGE",
                }
            )
            continue
        x_value, y_value, _ = route_rows[route_index]
        try:
            import carla

            waypoint = map_object.get_waypoint(
                carla.Location(x=float(x_value), y=float(y_value), z=0.0),
                project_to_road=True,
                lane_type=carla.LaneType.Driving,
            )
            junction = waypoint.get_junction()
            pairs = tuple(junction.get_waypoints(carla.LaneType.Driving))
            anchor = tuple(float(value) for value in opportunity["anchor_xy"])
            compatible = [
                pair
                for pair in pairs
                if int(getattr(pair[0], "road_id", -1))
                == int(opportunity.get("entry_road_id", -2))
                and int(getattr(pair[0], "lane_id", 0))
                == int(opportunity.get("entry_lane_id", 1))
                and int(getattr(pair[1], "road_id", -1))
                == int(opportunity.get("exit_road_id", -2))
                and int(getattr(pair[1], "lane_id", 0))
                == int(opportunity.get("exit_lane_id", 1))
            ]
            if not compatible:
                compatible = list(pairs)
            entry, exit_waypoint = min(
                compatible,
                key=lambda pair: math.hypot(
                    _waypoint_xy(pair[1])[0] - anchor[0],
                    _waypoint_xy(pair[1])[1] - anchor[1],
                ),
            )
            connector = _trace_waypoint_connector(entry, exit_waypoint)
            connector.update(
                {
                    "candidate_id": label,
                    "target_id": opportunity.get("target_id"),
                    "branch_id": opportunity.get("branch_id"),
                    "junction_id": opportunity.get("junction_id"),
                    "route_order_index": opportunity.get("route_order_index"),
                    "route_opportunity_index": route_index,
                    "junction_entry_progress_m": opportunity.get("distance_or_progress"),
                    "route_continuation_xy_m": [
                        [float(x), float(y)]
                        for x, y, _ in route_rows[
                            route_index : min(len(route_rows), route_index + 32)
                        ]
                    ],
                    "live_map_pair_match": bool(compatible),
                    "read_only": True,
                }
            )
            rows.append(connector)
        except (AttributeError, ImportError, RuntimeError, TypeError, ValueError) as error:
            rows.append(
                {
                    "candidate_id": label,
                    "status": "UNKNOWN",
                    "reason_code": "CANDIDATE_CONNECTOR_BINDING_FAILED",
                    "error_type": type(error).__name__,
                }
            )
    return rows


class PhaseBEvidenceCompletionRuntime(DecisionWindowEvidenceProbeRuntime):
    """One bounded B1 evidence-only run with existing baseline authority."""

    # Fifty matched receding-horizon plan/control/physical-response pairs are
    # required by the predeclared coordinate-calibration contract.  Capture
    # then ends at the first live-map-derived structural divergence boundary;
    # it must never continue the shared baseline through a divergent branch.
    minimum_calibration_pairs = 50

    def __init__(self, agent: Any, output_dir: str, *, raw_instruction: str) -> None:
        super().__init__(agent, output_dir, raw_instruction=raw_instruction)
        self._dense_route_source_attribute = None
        self._receding_horizon_events = []
        self._vehicle_physics_evidence = None
        self._receipt.update(
            {
                "schema_version": "driveclarify.phase_b_evidence_completion.live_receipt.v1",
                "runtime_version": RUNTIME_VERSION,
                "probe_run_id": RUN_ID,
                "probe_feature_flag": FEATURE_FLAG,
                "route_source": ROUTE_SOURCE,
                "old_b0_route_binding_reused": False,
                "planner_advance_count": 0,
                "route_planner_mutation_count": 0,
                "phase_c_authorized_to_continue": False,
                "model_coordinate_contract_id": MODEL_COORDINATE_CONTRACT_ID,
                "controlled_operating_contract": {
                    "synchronous_carla_required": True,
                    "normal_planning_interval_frames": 1,
                    "full_baseline_plus_k2_bundle_wall_timeout_s": 2.0,
                    "configured_answer_latency_upper_bound_s": 0.1,
                    "m2b_m3_authority_latency_upper_bound_s": 0.1,
                    "control_response_upper_bound_frames": 2,
                    "safety_margin_frames": 1,
                    "expected_model_checkpoint_spacing_m": 1.0,
                    "checkpoint_spacing_tolerance_m": 0.2,
                    "probe_termination": (
                        "FIRST_LIVE_CONNECTOR_STRUCTURAL_DIVERGENCE_WITH_"
                        "DESKTOP_CAPTURE_PREARMED_AT_CONSERVATIVE_COMMITMENT"
                    ),
                    "minimum_receding_horizon_calibration_pairs": (
                        self.minimum_calibration_pairs
                    ),
                    "fail_closed_if_exceeded": True,
                },
            }
        )
        _bind_dashboard_run_identity(self._receipt, RUN_ID)
        self._persist()

    def _route(self) -> Any:
        route, source_attribute = _detached_dense_route(self.agent)
        self._dense_route_source_attribute = source_attribute
        return route

    def _ground(self, image: Any) -> None:
        # Bypass B0-R1's frozen *old binding IDs* while retaining the exact E1-R1
        # image grounding and live-map topology implementation.
        TopologyAwareReferentialRuntime._ground(self, image)
        self._receipt["dino_event_triggered_forward_count"] = int(
            self.detector.forward_count
        )
        if self._terminal or self._candidate_set is None:
            return
        fixture_id = os.environ.get("DRIVECLARIFY_E1R1_FIXTURE_ID")
        exact_fixture = (
            self.raw_instruction == "Turn after the white van."
            and fixture_id == "E1R1-ASK-PHYS-001"
            and self._candidate_set.effective_k == 2
            and len(self._bound_candidates) == 2
        )
        self._receipt["frozen_fixture_identity"] = {
            "raw_instruction": self.raw_instruction,
            "fixture_id": fixture_id,
            "effective_k": self._candidate_set.effective_k,
            "target_ids": [row.get("target_id") for row in self._bound_candidates],
            "branch_ids": [row.get("branch_id") for row in self._bound_candidates],
            "exact_fixture_inputs_match": exact_fixture,
            "b0_target_ids_required": False,
        }
        if not exact_fixture:
            self._receipt["status"] = "BLOCKED_B1_FROZEN_WHITE_VAN_FIXTURE_MISMATCH"
            self._terminal = True
            self._persist()
            return

        renderer = ConsequenceAwareOfficialDreamingRenderer()
        opportunities = self._receipt["maneuver_opportunities"][:2]
        rendered_candidates = []
        semantic_rows = []
        for index, (candidate, target) in enumerate(
            zip(self._candidate_set.candidates, opportunities), start=1
        ):
            behavior = (
                "TURN_AT_UPCOMING_OPPORTUNITY"
                if index == 1
                else "CONTINUE_TO_LATER_OPPORTUNITY"
            )
            semantic = ConsequenceAwareGroundedSemantic(
                relation="AFTER",
                referring_expression=candidate.referring_expression,
                maneuver_direction=str(target["maneuver_direction"]),
                route_order_index=int(target["route_order_index"]),
                current_behavior=behavior,
                persistent_target_id=str(target["target_id"]),
                persistent_branch_id=str(target["branch_id"]),
            )
            prompt = renderer.render(semantic)
            rendered_candidates.append(
                replace(
                    candidate,
                    prompt_text=prompt,
                    prompt_sha256=canonical_sha256(prompt),
                )
            )
            self._bound_candidates[index - 1].update(
                {
                    "prompt_text": prompt,
                    "conditioning_hash": canonical_sha256(prompt),
                    "current_behavior": behavior,
                }
            )
            semantic_rows.append(
                {
                    "candidate_label": "A" if index == 1 else "B",
                    "semantic": asdict(semantic),
                    "rendered_dreaming_instruction": prompt,
                    "renderer": renderer.implementation_id,
                }
            )
        self._candidate_set = replace(
            self._candidate_set, candidates=tuple(rendered_candidates)
        )
        self._receipt["candidate_semantics"] = semantic_rows
        self._receipt["official_adapter"] = "OfficialDreamingCandidateAdapter.v1"
        self._receipt["candidate_specific_numeric_target"] = False
        self._receipt["target_embedding_injected"] = False
        self._capture_dense_route_evidence()
        self._persist()

    def _capture_dense_route_evidence(self) -> None:
        route_rows = self.topology_enumerator._route_rows(self._route())
        map_object = _live_map()
        lane_rows = []
        if map_object is not None:
            lane_rows = [
                _waypoint_row(map_object, x_value, y_value, option)
                for x_value, y_value, option in route_rows
            ]
        config = getattr(self.agent, "config", None)
        config_fields = (
            "carla_fps",
            "carla_frame_rate",
            "data_save_freq",
            "wp_dilation",
            "pred_len",
            "throttle_acceleration",
            "clip_delta",
            "clip_throttle",
            "brake_speed",
            "brake_ratio",
            "aim_distance_slow",
            "aim_distance_fast",
            "idm_comfortable_braking_deceleration_low_speed",
            "idm_comfortable_braking_deceleration_high_speed",
            "idm_comfortable_braking_deceleration_threshold",
        )
        self._receipt["dense_world_route_evidence"] = {
            "status": "AVAILABLE" if len(route_rows) >= 2 else "UNKNOWN",
            "route_source": ROUTE_SOURCE,
            "agent_attribute": self._dense_route_source_attribute,
            "frame": "CARLA_WORLD",
            "unit": "m",
            "detached_read_only_copy": True,
            "route_rows": [
                [float(x_value), float(y_value), str(option)]
                for x_value, y_value, option in route_rows
            ],
            "route_row_count": len(route_rows),
            "callable_coordinate_count": sum(
                int(callable(value))
                for row in route_rows
                for value in row[:2]
            ),
            "invalid_transform_interpretation_count": 0,
            "route_detachment_status": "PASS_TYPED_CARLA_LOCATION_EXTRACTION",
            "lane_topology_rows": lane_rows,
            "planner_advance_count": 0,
            "route_planner_mutation_count": 0,
        }
        self._receipt["existing_agent_config_evidence"] = {
            "producer": type(config).__module__ + "." + type(config).__name__
            if config is not None
            else None,
            "values": {
                name: getattr(config, name, None) for name in config_fields
            },
            "read_only": True,
        }
        self._receipt["candidate_executable_connector_evidence"] = (
            _candidate_connector_evidence(
                map_object,
                route_rows,
                list(self._receipt.get("maneuver_opportunities", [])),
            )
            if map_object is not None
            else []
        )

    def _capture_physical_sample(self) -> None:
        super()._capture_physical_sample()
        if not self._physical_samples or self._physical_samples[-1].get("status") != "AVAILABLE":
            return
        row = self._physical_samples[-1]
        try:
            from srunner.scenariomanager.carla_data_provider import CarlaDataProvider

            hero = CarlaDataProvider.get_hero_actor()
            world = CarlaDataProvider.get_world()
            settings = world.get_settings()
            row["runtime_operating_state"] = {
                "synchronous_mode": bool(settings.synchronous_mode),
                "fixed_delta_seconds": (
                    None
                    if settings.fixed_delta_seconds is None
                    else float(settings.fixed_delta_seconds)
                ),
                "speed_limit_kmh": float(hero.get_speed_limit()),
                "is_at_traffic_light": bool(hero.is_at_traffic_light()),
                "traffic_light_state": str(hero.get_traffic_light_state()),
            }
            if self._vehicle_physics_evidence is None:
                physics = hero.get_physics_control()
                self._vehicle_physics_evidence = {
                    "mass_kg": float(physics.mass),
                    "drag_coefficient": float(physics.drag_coefficient),
                    "max_rpm": float(physics.max_rpm),
                    "wheel_rows": [
                        {
                            "tire_friction": float(wheel.tire_friction),
                            "radius_cm": float(wheel.radius),
                            "max_steer_angle_deg": float(wheel.max_steer_angle),
                        }
                        for wheel in physics.wheels
                    ],
                    "read_only": True,
                }
            row["vehicle_physics_evidence"] = self._vehicle_physics_evidence
        except (AttributeError, ImportError, RuntimeError, TypeError, ValueError) as error:
            row["runtime_operating_state"] = {
                "status": "UNKNOWN",
                "reason_code": "RUNTIME_OPERATING_STATE_CAPTURE_FAILED",
                "error_type": type(error).__name__,
            }

    def on_model_output(
        self,
        baseline_route: Any,
        baseline_speed: Any,
        model_start: float,
        model_end: float,
    ) -> None:
        self._receding_horizon_events.append(
            {
                "source_frame_id": int(self._latest_frame),
                "source_observation_id": str(self._latest_observation_id),
                "baseline_route_local_forward_right_m": _points(baseline_route),
                "baseline_speed_waypoints": _points(baseline_speed),
                "model_started_monotonic": float(model_start),
                "model_ended_monotonic": float(model_end),
                "model_latency_seconds": float(model_end) - float(model_start),
                "control": None,
            }
        )
        self._receipt["receding_horizon_plan_control_events"] = self._receding_horizon_events
        super().on_model_output(baseline_route, baseline_speed, model_start, model_end)

    def on_control(self, control: Any, gt_velocity: Any, current_monotonic: float) -> None:
        matching = [
            row
            for row in self._receding_horizon_events
            if row["source_frame_id"] == int(self._latest_frame)
        ]
        if len(matching) == 1:
            matching[0]["control"] = {
                "steer": float(control.steer),
                "throttle": float(control.throttle),
                "brake": float(control.brake),
                "hand_brake": bool(control.hand_brake),
                "reverse": bool(control.reverse),
                "observed_monotonic": float(current_monotonic),
                "gt_velocity": str(gt_velocity),
                "authority": "EXISTING_BASELINE",
            }
        self._receipt["receding_horizon_plan_control_events"] = self._receding_horizon_events
        super().on_control(control, gt_velocity, current_monotonic)

    def _calibrated_runtime_uncertainty(self, post_samples: list) -> dict:
        """Reproduce the calibration inputs needed only for a safe stop guard."""

        if self._plan_source_frame is None:
            return {"status": "UNKNOWN", "reason_code": "PLAN_SOURCE_FRAME_MISSING"}
        source_rows = [
            row
            for row in post_samples
            if int(row.get("hook_frame", -1)) == int(self._plan_source_frame)
        ]
        if len(source_rows) != 1:
            return {
                "status": "UNKNOWN",
                "reason_code": "UNIQUE_SOURCE_PHYSICAL_SAMPLE_MISSING",
            }
        events = [
            row
            for row in self._receding_horizon_events
            if int(row.get("source_frame_id", -1)) >= int(self._plan_source_frame)
            and isinstance(row.get("control"), dict)
        ][: self.minimum_calibration_pairs]
        physical_by_frame = {
            int(row["hook_frame"]): row
            for row in post_samples
            if isinstance(row.get("hook_frame"), int)
        }
        matched = [
            row
            for row in events
            if int(row["source_frame_id"]) in physical_by_frame
            and int(row["source_frame_id"]) + 1 in physical_by_frame
        ]
        if len(matched) < self.minimum_calibration_pairs:
            return {
                "status": "UNKNOWN",
                "reason_code": "MINIMUM_RECEDING_HORIZON_CALIBRATION_NOT_REACHED",
                "matched_pair_count": len(matched),
                "required_pair_count": self.minimum_calibration_pairs,
            }

        source = source_rows[0]
        dense = self._receipt.get("dense_world_route_evidence", {})
        route_rows = dense.get("route_rows", [])
        waypoint_xyz = source.get("map_waypoint", {}).get("waypoint_location_xyz")
        ego_xyz = source.get("ego", {}).get("location_xyz")
        if (
            len(route_rows) < 2
            or not isinstance(waypoint_xyz, list)
            or len(waypoint_xyz) < 2
            or not isinstance(ego_xyz, list)
            or len(ego_xyz) < 2
        ):
            return {
                "status": "UNKNOWN",
                "reason_code": "RUNTIME_CALIBRATION_GEOMETRY_MISSING",
            }
        map_projection = _project_to_polyline(waypoint_xyz[:2], route_rows)
        source_projection = source.get("ego_route_projection", {})
        if (
            map_projection.get("status") != "AVAILABLE"
            or source_projection.get("status") != "AVAILABLE"
        ):
            return {
                "status": "UNKNOWN",
                "reason_code": "RUNTIME_CALIBRATION_PROJECTION_UNKNOWN",
            }

        route_errors = []
        checkpoint_errors = []
        expected_spacing = float(
            self._receipt["controlled_operating_contract"][
                "expected_model_checkpoint_spacing_m"
            ]
        )
        for event in matched:
            frame = int(event["source_frame_id"])
            for physical in (physical_by_frame[frame], physical_by_frame[frame + 1]):
                projection = physical.get("ego_route_projection", {})
                if projection.get("status") != "AVAILABLE":
                    return {
                        "status": "UNKNOWN",
                        "reason_code": "MATCHED_PHYSICAL_ROUTE_PROJECTION_UNKNOWN",
                    }
                route_errors.append(float(projection["projection_error_m"]))
            plan = event.get("baseline_route_local_forward_right_m", [])
            if len(plan) < 4:
                return {
                    "status": "UNKNOWN",
                    "reason_code": "MATCHED_BASELINE_PLAN_TOO_SHORT",
                }
            for left, right in zip(plan[:4], plan[1:4]):
                spacing = math.hypot(
                    float(right[0]) - float(left[0]),
                    float(right[1]) - float(left[1]),
                )
                checkpoint_errors.append(abs(spacing - expected_spacing))

        ego_map_error = math.hypot(
            float(ego_xyz[0]) - float(waypoint_xyz[0]),
            float(ego_xyz[1]) - float(waypoint_xyz[1]),
        )
        uncertainty = max(
            float(source_projection["projection_error_m"]),
            float(map_projection["projection_error_m"]),
            ego_map_error,
            max(route_errors),
            max(checkpoint_errors),
        )
        return {
            "status": "AVAILABLE",
            "value_m": uncertainty,
            "matched_pair_count": len(matched),
            "route_projection_error_max_m": max(route_errors),
            "checkpoint_spacing_error_max_m": max(checkpoint_errors),
            "source_ego_map_error_m": ego_map_error,
            "same_inputs_as_post_run_coordinate_calibration": True,
        }

    def _structural_completion_guard(self, post_samples: list) -> dict:
        uncertainty = self._calibrated_runtime_uncertainty(post_samples)
        if uncertainty.get("status") != "AVAILABLE":
            return uncertainty
        source = next(
            (
                row
                for row in post_samples
                if int(row.get("hook_frame", -1)) == int(self._plan_source_frame)
            ),
            None,
        )
        connectors = self._receipt.get("candidate_executable_connector_evidence", [])
        if source is None or not connectors:
            return {
                "status": "UNKNOWN",
                "reason_code": "SOURCE_OR_FIRST_LIVE_CONNECTOR_MISSING",
            }
        lane_width = source.get("map_waypoint", {}).get("lane_width")
        extent = source.get("ego", {}).get("bbox", {}).get("extent_xyz")
        if lane_width is None or not isinstance(extent, list) or len(extent) < 2:
            return {
                "status": "UNKNOWN",
                "reason_code": "LANE_OR_EGO_WIDTH_MISSING",
            }
        lane_clearance = float(lane_width) / 2.0 - float(extent[1])
        onset = _first_structural_divergence_progress(
            connectors[0],
            lane_clearance_m=lane_clearance,
            calibrated_uncertainty_m=float(uncertainty["value_m"]),
            hysteresis_samples=3,
        )
        if onset.get("status") != "AVAILABLE":
            return onset
        observed_speeds = [
            float(row.get("ego", {}).get("speed_world_mps", 0.0))
            for row in post_samples
        ]
        operating_state = source.get("runtime_operating_state", {})
        fixed_delta = operating_state.get("fixed_delta_seconds")
        contract = self._receipt.get("controlled_operating_contract", {})
        commitment = _conservative_commitment_progress(
            onset_progress_m=float(onset["progress_m"]),
            speed_upper_bound_mps=max(observed_speeds) if observed_speeds else 0.0,
            fixed_delta_seconds=float(fixed_delta or 0.0),
            control_response_upper_bound_frames=int(
                contract.get("control_response_upper_bound_frames", 0)
            ),
            safety_margin_distance_m=lane_clearance,
        )
        if commitment.get("status") != "AVAILABLE":
            return commitment
        current = post_samples[-1].get("ego_route_projection", {})
        if current.get("status") != "AVAILABLE":
            return {
                "status": "UNKNOWN",
                "reason_code": "CURRENT_ROUTE_PROGRESS_UNKNOWN",
            }
        return {
            **onset,
            "structural_onset_progress_m": float(onset["progress_m"]),
            "conservative_commitment": commitment,
            "current_progress_m": float(current["progress_m"]),
            "calibration": uncertainty,
            "source_frame": int(self._plan_source_frame),
            "current_frame": int(post_samples[-1]["hook_frame"]),
        }

    def on_tick(
        self, input_data: Any, tick_data: Any, timestamp: Any, frame: Any, observation_id: Any
    ) -> None:
        # Bypass the parent probe's fixed-duration completion rule.  The Phase B
        # completion probe uses a structural, live-map-derived terminal instead.
        TopologyAwareReferentialRuntime.on_tick(
            self, input_data, tick_data, timestamp, frame, observation_id
        )
        self._capture_physical_sample()
        self._receipt["physical_runtime_samples"] = self._physical_samples
        if self._plan_source_frame is not None and not self._terminal:
            post_samples = [
                row
                for row in self._physical_samples
                if row.get("status") == "AVAILABLE"
                and isinstance(row.get("hook_frame"), int)
                and row["hook_frame"] >= self._plan_source_frame
            ]
            guard = self._structural_completion_guard(post_samples)
            self._receipt["structural_probe_termination_guard"] = guard
            commitment = guard.get("conservative_commitment", {})
            if (
                guard.get("status") == "AVAILABLE"
                and commitment.get("status") == "AVAILABLE"
                and float(guard["current_progress_m"])
                >= float(commitment["progress_m"])
            ):
                self._receipt["native_desktop_capture_ready"] = True
                self._receipt["native_desktop_capture_prearm_evidence"] = {
                    **commitment,
                    "current_progress_m": float(guard["current_progress_m"]),
                    "current_frame": int(guard["current_frame"]),
                    "purpose": "FREEZE_NATIVE_PANEL_BEFORE_STRUCTURAL_DIVERGENCE",
                }
            if (
                guard.get("status") == "AVAILABLE"
                and float(guard["current_progress_m"]) >= float(guard["progress_m"])
            ):
                self._receipt.update(
                    {
                        "status": FINAL_CAPTURE_STATUS,
                        "post_plan_physical_sample_count": len(post_samples),
                        "baseline_plan_source_selection_count": self._baseline_selection_count,
                        "physical_runtime_evidence_captured": True,
                        "policy_decision_executed": False,
                        "natural_decision": "UNKNOWN_NOT_EVALUATED_IN_PASSIVE_PROBE",
                        "probe_terminated_at_first_structural_divergence": True,
                    }
                )
                self._terminal = True
            elif (
                len(post_samples) >= self.minimum_calibration_pairs + 1
                and guard.get("status") != "AVAILABLE"
                and guard.get("reason_code")
                != "MINIMUM_RECEDING_HORIZON_CALIBRATION_NOT_REACHED"
            ):
                self._receipt.update(
                    {
                        "status": "BLOCKED_STRUCTURAL_PROBE_TERMINATION_GUARD_UNKNOWN",
                        "physical_runtime_evidence_captured": False,
                        "policy_decision_executed": False,
                        "phase_c_authorized_to_continue": False,
                    }
                )
                self._terminal = True
        if self._terminal and self._receipt.get("physical_runtime_evidence_captured"):
            self._receipt["status"] = FINAL_CAPTURE_STATUS
            self._receipt["probe_run_id"] = RUN_ID
            self._receipt["phase_c_authorized_to_continue"] = False
        self._persist()


__all__ = [
    "FEATURE_FLAG",
    "FINAL_CAPTURE_STATUS",
    "ROUTE_SOURCE",
    "RUN_ID",
    "RUNTIME_VERSION",
    "MODEL_COORDINATE_CONTRACT_ID",
    "PhaseBEvidenceCompletionRuntime",
    "RouteDetachmentError",
    "_bind_dashboard_run_identity",
    "_detached_dense_route",
    "_location",
    "_trace_waypoint_connector",
    "_first_structural_divergence_progress",
    "_conservative_commitment_progress",
]
