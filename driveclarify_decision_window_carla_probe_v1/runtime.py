"""Passive white-van evidence acquisition on the existing planning/control path.

The probe performs one DINO grounding event and one official-compatible
SimLingo forward per active candidate.  It never selects a candidate plan and
always returns the existing baseline route/speed to the existing PID.
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, replace
from typing import Any, Mapping, Optional, Sequence, Tuple

from driveclarify_candidate_consequence_equivalence.renderer import (
    ConsequenceAwareGroundedSemantic,
    ConsequenceAwareOfficialDreamingRenderer,
)
from driveclarify_grounded_language_v1_extension_e1_r1.runtime import (
    TopologyAwareReferentialRuntime,
)
from driveclarify_grounded_language_v1_extension_e1_r1.contracts import (
    canonical_sha256,
)
from driveclarify_official_dreaming_adapter.adapter import (
    OfficialDreamingCandidateForwardProvider,
)
from driveclarify_paper_mvp_runtime.simlingo_binding import _points
from driveclarify_probe.world_state import extract_ego, extract_map_waypoint


FEATURE_FLAG = "DRIVECLARIFY_PERSISTENT_AMBIGUITY_EVIDENCE_PROBE"
RUNTIME_VERSION = "DRIVECLARIFY_DECISION_WINDOW_CARLA_EVIDENCE_PROBE_V1"
RUN_ID = "B0-R1"
FINAL_CAPTURE_STATUS = "PASS_PHASE_B_DECISION_WINDOW_EVIDENCE_CAPTURED_PENDING_ANALYSIS"
EXPECTED_TARGETS = (
    ("junction-map-103", "branch-right-7a425318aab0cfdf", "target-fe1c68fc8e82db3cc852"),
    ("junction-map-82", "branch-right-fce7554c04b1c542", "target-39e5ae59afb35e7a8a07"),
)


def _vector(value: Any) -> Optional[list]:
    try:
        return [float(value.x), float(value.y), float(value.z)]
    except (AttributeError, TypeError, ValueError):
        return None


def _snapshot(world: Any) -> Mapping[str, Any]:
    try:
        value = world.get_snapshot()
        timestamp = value.timestamp
        return {
            "status": "AVAILABLE",
            "frame": int(value.frame),
            "elapsed_seconds": float(timestamp.elapsed_seconds),
            "delta_seconds": float(timestamp.delta_seconds),
            "platform_timestamp": float(timestamp.platform_timestamp),
        }
    except (AttributeError, RuntimeError, TypeError, ValueError) as error:
        return {
            "status": "UNKNOWN",
            "value": None,
            "reason_code": "CARLA_SNAPSHOT_UNAVAILABLE",
            "error_type": type(error).__name__,
        }


def _project_to_polyline(
    point_xy: Sequence[float], route_rows: Sequence[Sequence[Any]]
) -> Mapping[str, Any]:
    if len(route_rows) < 2:
        return {"status": "UNKNOWN", "value": None, "reason_code": "ROUTE_POLYLINE_TOO_SHORT"}
    px, py = float(point_xy[0]), float(point_xy[1])
    best = None
    cumulative = 0.0
    for index, (left, right) in enumerate(zip(route_rows, route_rows[1:])):
        x0, y0 = float(left[0]), float(left[1])
        x1, y1 = float(right[0]), float(right[1])
        dx, dy = x1 - x0, y1 - y0
        length_sq = dx * dx + dy * dy
        if length_sq <= 1e-12:
            continue
        fraction = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / length_sq))
        qx, qy = x0 + fraction * dx, y0 + fraction * dy
        distance = math.hypot(px - qx, py - qy)
        segment_length = math.sqrt(length_sq)
        candidate = {
            "status": "AVAILABLE",
            "progress_m": cumulative + fraction * segment_length,
            "projection_error_m": distance,
            "segment_index": index,
            "segment_fraction": fraction,
            "projected_xy_m": [qx, qy],
        }
        if best is None or (distance, index) < (best[0], best[1]["segment_index"]):
            best = (distance, candidate)
        cumulative += segment_length
    if best is None:
        return {"status": "UNKNOWN", "value": None, "reason_code": "ROUTE_PROJECTION_FAILED"}
    return best[1]


def _arc_length(route: Sequence[Sequence[Any]]) -> float:
    return sum(
        math.hypot(
            float(right[0]) - float(left[0]),
            float(right[1]) - float(left[1]),
        )
        for left, right in zip(route, route[1:])
    )


class DecisionWindowEvidenceProbeRuntime(TopologyAwareReferentialRuntime):
    """One bounded, evidence-only P1 probe with baseline authority unchanged."""

    minimum_post_plan_samples = 20

    def __init__(self, agent: Any, output_dir: str, *, raw_instruction: str) -> None:
        super().__init__(agent, output_dir, raw_instruction=raw_instruction)
        self.forward_provider = OfficialDreamingCandidateForwardProvider(agent)
        self._physical_samples = []
        self._baseline_forward_events = []
        self._baseline_selection_count = 0
        self._plan_source_frame = None
        self._plan_source_observation_id = None
        self._control_events = []
        self._receipt.update(
            {
                "schema_version": "driveclarify.decision_window_carla_probe.live_receipt.v1",
                "runtime_version": RUNTIME_VERSION,
                "probe_run_id": RUN_ID,
                "probe_feature_flag": FEATURE_FLAG,
                "probe_feature_flag_default": "OFF",
                "evidence_only": True,
                "passive_diagnostic": True,
                "persistent_runtime_integration": False,
                "policy_decision_executed": False,
                "initial_decision": "UNKNOWN",
                "decision_reason": "EVIDENCE_ONLY_PASSIVE_NO_POLICY_DECISION",
                "control_authority": "EXISTING_BASELINE",
                "candidate_plan_selected_for_control": False,
                "new_pid_count": 0,
                "new_planner_count": 0,
                "direct_vehicle_control_write_count": 0,
                "visualization_extra_forward_count": 0,
                "online_candidate_repeat_count": 0,
                "decision_window_dashboard": {
                    "run_id": RUN_ID,
                    "ego_route_progress": "UNKNOWN",
                    "plan_A_arc_m": "UNKNOWN",
                    "plan_B_arc_m": "UNKNOWN",
                    "plan_coverage": "UNKNOWN",
                    "shared_corridor": "UNKNOWN",
                    "maneuver_onset": "UNKNOWN",
                    "decision_point": "UNKNOWN",
                    "commitment_boundary": "UNKNOWN",
                    "recoverability": "UNKNOWN",
                    "time_to_divergence": "UNKNOWN",
                    "latest_safe_clarification": "UNKNOWN",
                    "current_relation": "UNKNOWN",
                    "reason_code": "EVIDENCE_ACQUISITION_PENDING",
                    "source_frame": None,
                    "forward_counters": "DINO 0 | base 0 | A 0 | B 0 | viz 0",
                },
            }
        )
        if self.control_enabled:
            self._receipt["status"] = "BLOCKED_PHASE_B_PROBE_MUST_BE_BASELINE_AUTHORITY"
            self._terminal = True
        self._persist()

    def _ground(self, image: Any) -> None:
        super()._ground(image)
        self._receipt["dino_event_triggered_forward_count"] = int(
            self.detector.forward_count
        )
        if self._terminal or self._candidate_set is None:
            return
        if self._candidate_set.effective_k != 2 or len(self._bound_candidates) != 2:
            self._receipt["status"] = "BLOCKED_PHASE_B_WHITE_VAN_EFFECTIVE_K_NOT_TWO"
            self._terminal = True
            self._persist()
            return
        actual_targets = tuple(
            (row["junction_id"], row["branch_id"], row["target_id"])
            for row in self._bound_candidates
        )
        self._receipt["frozen_fixture_identity"] = {
            "raw_instruction": self.raw_instruction,
            "expected_targets": [list(row) for row in EXPECTED_TARGETS],
            "actual_targets": [list(row) for row in actual_targets],
            "exact_match": actual_targets == EXPECTED_TARGETS,
        }
        if self.raw_instruction != "Turn after the white van." or actual_targets != EXPECTED_TARGETS:
            self._receipt["status"] = "BLOCKED_PHASE_B_FROZEN_WHITE_VAN_FIXTURE_MISMATCH"
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
            current_behavior = (
                "TURN_AT_UPCOMING_OPPORTUNITY"
                if index == 1
                else "CONTINUE_TO_LATER_OPPORTUNITY"
            )
            semantic = ConsequenceAwareGroundedSemantic(
                relation="AFTER",
                referring_expression=candidate.referring_expression,
                maneuver_direction=str(target["maneuver_direction"]),
                route_order_index=int(target["route_order_index"]),
                current_behavior=current_behavior,
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
            self._bound_candidates[index - 1]["prompt_text"] = prompt
            self._bound_candidates[index - 1]["conditioning_hash"] = canonical_sha256(prompt)
            self._bound_candidates[index - 1]["current_behavior"] = current_behavior
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
        self._persist()

    def _capture_physical_sample(self) -> None:
        try:
            from srunner.scenariomanager.carla_data_provider import CarlaDataProvider

            hero = CarlaDataProvider.get_hero_actor()
            world = CarlaDataProvider.get_world()
        except (AttributeError, ImportError, RuntimeError) as error:
            self._physical_samples.append(
                {
                    "status": "UNKNOWN",
                    "value": None,
                    "reason_code": "CARLA_WORLD_OR_HERO_UNAVAILABLE",
                    "error_type": type(error).__name__,
                    "hook_frame": self._latest_frame,
                }
            )
            return
        ego = extract_ego(hero)
        waypoint = extract_map_waypoint(world, hero)
        route_rows = self.topology_enumerator._route_rows(self._route())
        route_digest = canonical_sha256(
            [[round(float(x), 6), round(float(y), 6), str(option)] for x, y, option in route_rows]
        )
        location = ego.get("location_xyz")
        projection = (
            _project_to_polyline(location[:2], route_rows)
            if isinstance(location, list) and len(location) >= 2
            else {"status": "UNKNOWN", "value": None, "reason_code": "EGO_LOCATION_UNAVAILABLE"}
        )
        self._physical_samples.append(
            {
                "status": "AVAILABLE",
                "hook_frame": self._latest_frame,
                "source_observation_id": self._latest_observation_id,
                "hook_simulation_time": self._latest_simulation_time,
                "captured_monotonic_time": time.monotonic(),
                "snapshot": _snapshot(world),
                "ego": ego,
                "map_waypoint": waypoint,
                "route_version_digest": route_digest,
                "route_row_count": len(route_rows),
                "route_reference_origin_xy_m": (
                    [float(route_rows[0][0]), float(route_rows[0][1])]
                    if route_rows else None
                ),
                "ego_route_projection": projection,
            }
        )
        dashboard = dict(self._receipt.get("decision_window_dashboard", {}))
        dashboard.update(
            {
                "ego_route_progress": (
                    projection.get("progress_m")
                    if projection.get("status") == "AVAILABLE"
                    else "UNKNOWN"
                ),
                "ego_route_progress_unit": "m_route",
                "road_lane_junction": "{}/{}/{}".format(
                    waypoint.get("road_id"),
                    waypoint.get("lane_id"),
                    waypoint.get("is_junction"),
                ),
                "source_frame": self._latest_frame,
                "forward_counters": "DINO {} | base {} | A {} | B {} | viz 0".format(
                    self._receipt.get("dino_event_triggered_forward_count", 0),
                    self._receipt.get("normal_simlingo_forward_count", 0),
                    1 if len(self._plan_rows) >= 1 else 0,
                    1 if len(self._plan_rows) >= 2 else 0,
                ),
            }
        )
        self._receipt["decision_window_dashboard"] = dashboard

    def on_tick(
        self, input_data: Any, tick_data: Any, timestamp: Any, frame: Any, observation_id: Any
    ) -> None:
        super().on_tick(input_data, tick_data, timestamp, frame, observation_id)
        self._capture_physical_sample()
        self._receipt["physical_runtime_samples"] = self._physical_samples
        if self._plan_source_frame is not None:
            post_samples = [
                row for row in self._physical_samples
                if row.get("status") == "AVAILABLE"
                and isinstance(row.get("hook_frame"), int)
                and row["hook_frame"] >= self._plan_source_frame
            ]
            if len(post_samples) >= self.minimum_post_plan_samples:
                self._receipt.update(
                    {
                        "status": FINAL_CAPTURE_STATUS,
                        "post_plan_physical_sample_count": len(post_samples),
                        "baseline_plan_source_selection_count": self._baseline_selection_count,
                        "physical_runtime_evidence_captured": True,
                        "policy_decision_executed": False,
                        "natural_decision": "UNKNOWN_NOT_EVALUATED_IN_PASSIVE_PROBE",
                    }
                )
                self._terminal = True
        self._persist()

    def on_model_output(
        self,
        baseline_route: Any,
        baseline_speed: Any,
        model_start: float,
        model_end: float,
    ) -> None:
        self._baseline_forward_events.append(
            {
                "source_frame_id": self._latest_frame,
                "source_observation_id": self._latest_observation_id,
                "started_monotonic": float(model_start),
                "ended_monotonic": float(model_end),
                "latency_seconds": float(model_end) - float(model_start),
            }
        )
        self._receipt["baseline_forward_events"] = self._baseline_forward_events
        super().on_model_output(baseline_route, baseline_speed, model_start, model_end)

    def _run_initial_candidate_plans(self, baseline_route: Any, baseline_speed: Any) -> None:
        assert self._candidate_set is not None
        if len(self._candidate_set.candidates) != 2:
            raise RuntimeError("PHASE_B_CANDIDATE_COUNT_NOT_TWO")
        rows = []
        for label, candidate in zip(("A", "B"), self._candidate_set.candidates):
            result = self._forward(candidate, label)
            receipt = self._plan_receipt(result, label)
            receipt["raw_route"] = result.forward_evidence.get("raw_route")
            receipt["equal_spaced_route"] = result.forward_evidence.get("equal_spaced_route")
            rows.append(receipt)
        self._plan_rows = rows
        self._plans_ready = True
        self._plan_source_frame = int(self._latest_frame)
        self._plan_source_observation_id = str(self._latest_observation_id)
        self._receipt.update(
            {
                "status": "PHASE_B_EVIDENCE_CAPTURED_WAITING_FOR_BASELINE_RESPONSE_SAMPLES",
                "candidate_plans": rows,
                "plan_source_frame": self._plan_source_frame,
                "plan_source_observation_id": self._plan_source_observation_id,
                "candidate_forward_requested": 2,
                "candidate_forward_executed": 2,
                "candidate_forward_skipped": 0,
                "candidate_forward_failed": 0,
                "candidate_simlingo_forward_count": 2,
                "online_candidate_repeat_count": 0,
                "visualization_extra_forward_count": 0,
                "baseline_route_at_source": _points(baseline_route),
                "baseline_speed_at_source": _points(baseline_speed),
                "initial_decision": "UNKNOWN",
                "decision_reason": "EVIDENCE_ONLY_PASSIVE_NO_POLICY_DECISION",
            }
        )
        dashboard = dict(self._receipt.get("decision_window_dashboard", {}))
        dashboard.update(
            {
                "plan_A_arc_m": _arc_length(rows[0]["equal_spaced_route"]),
                "plan_B_arc_m": _arc_length(rows[1]["equal_spaced_route"]),
                "plan_coverage": "UNKNOWN",
                "shared_corridor": "UNKNOWN",
                "maneuver_onset": "UNKNOWN",
                "decision_point": "UNKNOWN",
                "commitment_boundary": "UNKNOWN",
                "recoverability": "UNKNOWN",
                "time_to_divergence": "UNKNOWN",
                "latest_safe_clarification": "UNKNOWN",
                "current_relation": "UNKNOWN",
                "reason_code": "CONTROLLED_PROBE_ANALYSIS_PENDING_FAIL_CLOSED",
                "source_frame": self._plan_source_frame,
                "forward_counters": "DINO {} | base {} | A 1 | B 1 | viz 0".format(
                    self._receipt.get("dino_event_triggered_forward_count", 0),
                    self._receipt.get("normal_simlingo_forward_count", 0),
                ),
            }
        )
        self._receipt["decision_window_dashboard"] = dashboard
        self._persist()

    def select_plan_source(
        self, baseline_route: Any, baseline_speed: Any, current_monotonic: float
    ) -> Tuple[Any, Any]:
        del current_monotonic
        self._baseline_selection_count += 1
        return baseline_route, baseline_speed

    def on_control(self, control: Any, gt_velocity: Any, current_monotonic: float) -> None:
        self._control_events.append(
            {
                "source_frame_id": self._latest_frame,
                "observed_monotonic": float(current_monotonic),
                "baseline_speed_input": str(gt_velocity),
                "authority": "EXISTING_BASELINE",
            }
        )
        self._receipt["baseline_control_events"] = self._control_events
        super().on_control(control, gt_velocity, current_monotonic)


__all__ = [
    "DecisionWindowEvidenceProbeRuntime",
    "FEATURE_FLAG",
    "FINAL_CAPTURE_STATUS",
    "RUN_ID",
    "RUNTIME_VERSION",
]
