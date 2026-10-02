"""Default-off real-rgb_0 temporal grounding, WAIT, and fresh replan runtime."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from driveclarify_language_grounding_v1.visual_grounder import (
    GroundingDinoVisualGrounder,
)
from driveclarify_m3_live_authority import CandidateLiveActAuthorityResolverV0
from driveclarify_m3_minimal_core import (
    EventType,
    MinimalM3Event,
    ProcessingResult,
    reduce_event,
)
from driveclarify_m3_runtime_shadow.limited_act_commit_v0 import (
    LimitedActCommitV0,
    _tensor_digest,
)
from driveclarify_m3_runtime_shadow.physical_wait_v0 import (
    PhysicalWaitExecutorV0,
    build_bounded_wait_pilot_binding,
)
from driveclarify_paper_mvp_runtime.contracts import RuntimeCandidate
from driveclarify_paper_mvp_runtime.simlingo_binding import (
    SimLingoCandidateForwardProvider,
    _points,
    _scalar,
)

from .contracts import (
    EventState,
    FEATURE_FLAG,
    TemporalCandidate,
    TemporalInformationUpdate,
    TrackLifecycleState,
    canonical_sha256,
)
from .event_estimator import TemporalEventEstimator
from .target_binding import RuntimeRouteTargetBinder
from .tracker import ByteTrackAdapter, Detection, ImageMotionProposal
from .visualization import render_temporal_panel


OUTPUT_ENV = "DRIVECLARIFY_TEMPORAL_GROUNDING_V1_OUTPUT_DIR"
RAW_INSTRUCTION_ENV = "DRIVECLARIFY_TEMPORAL_GROUNDING_V1_RAW_INSTRUCTION"
DEVICE_ENV = "DRIVECLARIFY_TEMPORAL_GROUNDING_V1_DETECTOR_DEVICE"
CONTROL_ENV = "DRIVECLARIFY_TEMPORAL_GROUNDING_V1_CONTROL"
RECEIPT_FILENAME = "TEMPORAL_GROUNDING_V1_LIVE_RECEIPT.json"
WAIT_LEASE_DURATION_SECONDS = 180.0


def _truthy(value: Any) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def _front(input_data: Any) -> Tuple[int, Any]:
    import numpy as np

    entry = input_data["rgb_0"]
    frame = int(entry[0]) if isinstance(entry, (tuple, list)) else -1
    image = entry[1] if isinstance(entry, (tuple, list)) else entry
    value = np.asarray(image)
    if value.ndim != 3 or value.shape[2] < 3:
        raise ValueError("TEMPORAL_GROUNDING_RGB_0_INVALID")
    return frame, np.ascontiguousarray(value).copy()


def _xy(value: Any) -> Optional[Tuple[float, float, float]]:
    try:
        sequence = value.detach().cpu().reshape(-1).tolist() if hasattr(value, "detach") else value
        if hasattr(sequence, "tolist"):
            sequence = sequence.tolist()
        if isinstance(sequence, (tuple, list)) and len(sequence) >= 2:
            return (float(sequence[0]), float(sequence[1]), 0.0)
    except Exception:
        return None
    return None


def _control_values(control: Any) -> Dict[str, Any]:
    return {
        name: getattr(control, name)
        for name in (
            "steer",
            "throttle",
            "brake",
            "hand_brake",
            "reverse",
            "manual_gear_shift",
            "gear",
        )
        if hasattr(control, name)
    }


class TemporalGroundingV1Runtime:
    enabled = True

    def __init__(self, agent: Any, output_dir: str) -> None:
        self.agent = agent
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.frames_dir = self.output_dir / "rgb_sequence"
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self.control_enabled = _truthy(os.environ.get(CONTROL_ENV))
        self.raw_instruction = (
            os.environ.get(RAW_INSTRUCTION_ENV)
            or os.environ.get("DRIVECLARIFY_PAPER_MVP_STAGE6B_RAW_INSTRUCTION")
            or "Turn after the bus clears."
        )
        self.instruction_id = "instruction-" + canonical_sha256(self.raw_instruction)[:20]
        self.detector = GroundingDinoVisualGrounder(
            device=os.environ.get(DEVICE_ENV, "cpu")
        )
        self.tracker = ByteTrackAdapter()
        self.motion = ImageMotionProposal()
        self.estimator = TemporalEventEstimator()
        self.target_binder = RuntimeRouteTargetBinder()
        self.forward_provider = SimLingoCandidateForwardProvider(agent)
        self.wait = PhysicalWaitExecutorV0(enabled=True)
        self.limited_act = LimitedActCommitV0(
            self.output_dir,
            authority_resolver=CandidateLiveActAuthorityResolverV0(
                enabled=self.control_enabled
            ),
        )
        self.agent.custom_prompt = self.raw_instruction
        self.agent.user_flag = 1
        self._grounded = False
        self._target = None
        self._region = None
        self._tracks = ()
        self._primary_track_id: Optional[str] = None
        self._trail: List[Tuple[float, float]] = []
        self._event = None
        self._candidate_pre: Optional[TemporalCandidate] = None
        self._candidate_post: Optional[TemporalCandidate] = None
        self._wait_binding = None
        self._pre_forward = None
        self._post_forward = None
        self._information: Optional[TemporalInformationUpdate] = None
        self._old_invalidated = False
        self._replan_pending = False
        self._m3_replan = None
        self._latest_frame: Optional[int] = None
        self._latest_observation_id: Optional[str] = None
        self._latest_simulation_time = 0.0
        self._latest_speed = 0.0
        self._latest_position: Optional[Tuple[float, float, float]] = None
        self._latest_image = None
        self._latest_image_sha = None
        self._normal_forwards = 0
        self._candidate_forwards = 0
        self._pid_invocations = 0
        self._control_observations = 0
        self._detector_invocation_frames: List[int] = []
        self._detector_latencies: List[float] = []
        self._reground_count = 0
        self._frame_receipts: List[Dict[str, Any]] = []
        self._track_lifecycle: List[Dict[str, Any]] = []
        self._event_timeline: List[Dict[str, Any]] = []
        self._rgb_identities: List[Dict[str, Any]] = []
        self._errors: List[Dict[str, Any]] = []
        self._terminal = False
        self._closed = False
        self._receipt: Dict[str, Any] = {
            "schema_version": "driveclarify.temporal_grounding_v1.live_receipt.v1",
            "status": "WAITING_FOR_REAL_RGB_0",
            "level": 0,
            "feature_flag": FEATURE_FLAG,
            "feature_flag_default": "OFF",
            "control_mode": "BOUNDED_CLOSED_LOOP" if self.control_enabled else "NO_CONTROL_SHADOW",
            "parallel_train_diagnostic": True,
            "part_of_frozen_24_scenarios": False,
            "frozen_stage6b_replaced": False,
            "raw_instruction": self.raw_instruction,
            "instruction_id": self.instruction_id,
            "policy_runtime_inputs": [
                "real_rgb_0",
                "Grounding_DINO_boxes",
                "ByteTrack_history",
                "runtime_route_deque",
                "simulation_frame_and_time",
                "existing_ego_state",
            ],
            "privileged_state_policy_read_count": 0,
            "label_firewall": {
                "gold_cleared_timestamp_reads": 0,
                "carla_actor_id_reads": 0,
                "carla_actor_transform_reads": 0,
                "carla_actor_velocity_reads": 0,
                "simulator_bbox_ground_truth_reads": 0,
                "scenario_internal_trigger_reads": 0,
                "dev_reads": 0,
                "test_reads": 0,
            },
            "candidate_direct_vehicle_control_write_count": 0,
            "m3_direct_vehicle_control_write_count": 0,
            "new_pid_count": 0,
            "visualization_induced_detector_forward_count": 0,
            "visualization_induced_simlingo_forward_count": 0,
            "visualization_induced_pid_count": 0,
            "errors": self._errors,
            "wait_lease_calibration": {
                "duration_seconds": WAIT_LEASE_DURATION_SECONDS,
                "clock_domain": "WALL_MONOTONIC",
                "source": (
                    "TRAIN diagnostic T1 frame 2510->2644 measured 6.70 simulation "
                    "seconds over about 70 wall seconds after grounding; 180 seconds "
                    "is a bounded lease with more than 2x observed margin"
                ),
                "dev_or_test_used": False,
            },
            "event_region_calibration": {
                "normalized_xyxy": [0.35, 0.25, 0.80, 0.75],
                "source": (
                    "TRAIN T1 real-rgb_0 replay: initial DINO bus box is fully inside; "
                    "right-turn corridor exit occurs while bt-0001 remains ACTIVE for "
                    "three consecutive frames before FOV-edge loss"
                ),
                "outcome_label_used": False,
                "dev_or_test_used": False,
            },
        }
        self._persist()

    def _persist(self) -> None:
        self._receipt.update(
            {
                "detector_invocation_count": len(self._detector_invocation_frames),
                "detector_invocation_frames": list(self._detector_invocation_frames),
                "detector_latencies_seconds": list(self._detector_latencies),
                "reground_count": self._reground_count,
                "tracker_latency_seconds": list(self.tracker.latencies_seconds),
                "event_estimator_latency_seconds": list(self.estimator.latencies_seconds),
                "track_id_switch_count": self.tracker.id_switch_count,
                "track_loss_count": self.tracker.track_loss_count,
                "reacquisition_count": self.tracker.reacquisition_count,
                "track_lifecycle": list(self._track_lifecycle),
                "event_timeline": list(self._event_timeline),
                "rgb_identities": list(self._rgb_identities),
                "normal_simlingo_forward_count": self._normal_forwards,
                "candidate_simlingo_forward_count": self._candidate_forwards,
                "existing_pid_invocation_count": self._pid_invocations,
                "control_observation_count": self._control_observations,
                "wait": self.wait.summary(current_monotonic_time=time.monotonic()),
                "limited_act": dict(self.limited_act.summary()),
                "information_update": (
                    None if self._information is None else self._information.to_dict()
                ),
                "old_candidate_invalidated": self._old_invalidated,
                "m3_fresh_replan_lifecycle": self._m3_replan,
                "candidate_before": (
                    None if self._candidate_pre is None else self._candidate_pre.to_dict()
                ),
                "candidate_after": (
                    None if self._candidate_post is None else self._candidate_post.to_dict()
                ),
                "pre_event_plan": self._forward_receipt(self._pre_forward),
                "post_event_plan": self._forward_receipt(self._post_forward),
                "dev_attempt_count": 0,
                "test_attempt_count": 0,
                "test_consumed": False,
            }
        )
        _atomic_json(self.output_dir / RECEIPT_FILENAME, self._receipt)

    @staticmethod
    def _forward_receipt(value: Any) -> Optional[Dict[str, Any]]:
        if value is None:
            return None
        route = _points(value.raw_route)
        speed = _points(value.raw_speed)
        return {
            "candidate_id": value.plan.candidate_id,
            "source_observation_id": value.plan.source_observation_id,
            "source_frame_id": value.plan.source_frame_id,
            "route": route,
            "speed": speed,
            "route_sha256": canonical_sha256(route),
            "speed_sha256": canonical_sha256(speed),
            "raw_route_tensor_sha256": _tensor_digest(value.raw_route),
            "raw_speed_tensor_sha256": _tensor_digest(value.raw_speed),
            "latency_seconds": value.plan.latency_s,
        }

    def on_tick(
        self,
        input_data: Any,
        tick_data: Any,
        timestamp: Any,
        frame: Any,
        observation_id: Any,
    ) -> None:
        try:
            sensor_frame, image = _front(input_data)
            frame_id = int(frame)
            if sensor_frame != frame_id:
                raise RuntimeError("TEMPORAL_SENSOR_FRAME_MISMATCH")
            self._latest_frame = frame_id
            self._latest_observation_id = str(observation_id)
            self._latest_simulation_time = float(timestamp)
            self._latest_speed = float(_scalar(tick_data.get("speed")) or 0.0)
            self._latest_position = _xy(tick_data.get("gps"))
            self._latest_image = image
            self._latest_image_sha = hashlib.sha256(image.tobytes(order="C")).hexdigest()
            actual = _control_values(getattr(self.agent, "control", None))
            self.limited_act.observe_tick(
                frame=frame_id,
                snapshot={
                    "ego_position": self._latest_position,
                    "ego_speed_mps": self._latest_speed,
                    "actual_control": actual,
                },
            )
            if self._latest_frame is not None and actual:
                self.wait.observe_actual_carla_control(
                    source_frame=frame_id - 1, actual_control=actual
                )
            if self._terminal:
                self._update_terminal_status()
                self._persist()
                return
            if self._target is None:
                planner = getattr(self.agent, "_route_planner", None)
                self._target = self.target_binder.bind(getattr(planner, "route", None))
                if self._target is None:
                    self._receipt.update(
                        {
                            "status": "BLOCKED_TEMPORAL_TARGET_BINDING_COLLAPSE",
                            "level": 0,
                            "blocker": "TARGET_UNRESOLVED_NO_EXECUTABLE_RUNTIME_TURN",
                        }
                    )
                    self._terminal = True
                    self._persist()
                    return
                self._region = self.target_binder.event_region(
                    self._target,
                    image_width=int(image.shape[1]),
                    image_height=int(image.shape[0]),
                )
                self._receipt["target_binding"] = self._target.to_dict()
                self._receipt["event_region"] = self._region.to_dict()
            if not self._grounded:
                self._initial_ground(image, frame_id, str(observation_id))
            else:
                self._track_frame(image, frame_id)
            self._record_rgb(image, frame_id)
            self._update_wait_and_event(frame_id)
            self._render()
            self._persist()
        except Exception as exc:
            self._errors.append(
                {
                    "stage": "ON_TICK",
                    "frame": self._latest_frame,
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
            )
            self._receipt["status"] = "BLOCKED_TEMPORAL_EVENT_ESTIMATOR"
            self._terminal = True
            self._persist()

    def _initial_ground(self, image: Any, frame_id: int, observation_id: str) -> None:
        grounding = self.detector.ground(
            image,
            "bus",
            frame_id=frame_id,
            observation_id=observation_id,
            captured_monotonic=time.monotonic(),
        )
        self._detector_invocation_frames.append(frame_id)
        self._detector_latencies.append(grounding.detector_latency_seconds)
        self._receipt["initial_grounding"] = grounding.to_dict()
        if not grounding.selected_referents:
            blocker = (
                "BLOCKED_LOW_CONFIDENCE_VISUAL_REFERENT"
                if grounding.raw_referents
                else "BLOCKED_TEMPORAL_REFERENT_TRACKING"
            )
            self._receipt.update({"status": blocker, "blocker": grounding.reason_codes})
            self._terminal = True
            return
        detections = tuple(
            Detection(
                bbox_xyxy=item.bbox_xyxy,
                confidence=item.detector_confidence,
                phrase=item.phrase,
                source_grounding_id=item.local_object_id,
                source="GROUNDING_DINO_INITIAL",
            )
            for item in grounding.selected_referents
        )
        self._tracks = self.tracker.update(
            detections,
            frame_id=frame_id,
            simulation_time=self._latest_simulation_time,
        )
        active = [
            item for item in self._tracks if item.lifecycle_state is TrackLifecycleState.ACTIVE
        ]
        if len(active) != 1:
            self._receipt.update(
                {
                    "status": "BLOCKED_TEMPORAL_REFERENT_TRACKING",
                    "blocker": "PILOT_REQUIRES_EXACTLY_ONE_PLAUSIBLE_BUS",
                    "plausible_bus_count": len(active),
                }
            )
            self._terminal = True
            return
        self._primary_track_id = active[0].track_id
        self.motion.initialize(image, self._tracks)
        self._grounded = True
        self._receipt.update(
            {
                "status": "BYTETRACK_REAL_RGB_IDENTITY_ACTIVE",
                "level": 1,
                "grounded_referent_id": active[0].source_grounding_id,
                "track_id": active[0].track_id,
                "plausibility_decision": "ONE_DETECTION_PASSED_FROZEN_LABEL_FREE_GATE",
            }
        )
        self._observe_event(active[0])
        self._create_pre_candidate(active[0])

    def _track_frame(self, image: Any, frame_id: int) -> None:
        proposals = self.motion.propose(image, self._tracks)
        self._tracks = self.tracker.update(
            proposals,
            frame_id=frame_id,
            simulation_time=self._latest_simulation_time,
        )
        primary = next(
            (item for item in self._tracks if item.track_id == self._primary_track_id),
            None,
        )
        if primary is None:
            self._receipt.update(
                {
                    "status": "BLOCKED_TEMPORAL_REFERENT_TRACKING",
                    "blocker": "PRIMARY_TRACK_EXPIRED",
                }
            )
            self._terminal = True
            return
        if primary.lifecycle_state is TrackLifecycleState.LOST:
            self._reground(image, frame_id)
            primary = next(
                (item for item in self._tracks if item.track_id == self._primary_track_id),
                primary,
            )
        self._observe_event(primary)

    def _reground(self, image: Any, frame_id: int) -> None:
        self._reground_count += 1
        grounding = self.detector.ground(
            image,
            "bus",
            frame_id=frame_id,
            observation_id=str(self._latest_observation_id),
            captured_monotonic=time.monotonic(),
        )
        self._detector_invocation_frames.append(frame_id)
        self._detector_latencies.append(grounding.detector_latency_seconds)
        detections = tuple(
            Detection(
                bbox_xyxy=item.bbox_xyxy,
                confidence=item.detector_confidence,
                phrase=item.phrase,
                source_grounding_id=item.local_object_id,
                source="GROUNDING_DINO_REACQUISITION",
            )
            for item in grounding.selected_referents
        )
        before = self._primary_track_id
        self._tracks = self.tracker.update(
            detections,
            frame_id=frame_id,
            simulation_time=self._latest_simulation_time,
        )
        matched = next(
            (
                item
                for item in self._tracks
                if item.track_id == before
                and item.lifecycle_state is TrackLifecycleState.ACTIVE
            ),
            None,
        )
        if matched is None:
            self._receipt.update(
                {
                    "status": "BLOCKED_TEMPORAL_TRACK_ID_SWITCH",
                    "blocker": "REACQUISITION_DID_NOT_ASSOCIATE_TO_ORIGINAL_TRACK",
                }
            )
            self._terminal = True
        else:
            self.motion.initialize(image, self._tracks)

    def _observe_event(self, track: Any) -> None:
        assert self._region is not None
        previous = None if self._event is None else self._event.state
        self._event = self.estimator.update(track, self._region)
        self._trail.append(track.centroid_xy)
        self._track_lifecycle.append(track.to_dict())
        if previous is not self._event.state:
            self._event_timeline.append(self._event.to_dict())
        if (
            self._event.state is EventState.CLEARED
            and previous is not EventState.CLEARED
            and self._candidate_pre is not None
        ):
            self._information = TemporalInformationUpdate.create(
                event_id=self._event.event_id,
                old_state=previous or EventState.UNKNOWN,
                new_state=EventState.CLEARED,
                source_frames=tuple(frame for _, frame in self._event.transition_frames),
                candidate_affected=self._candidate_pre.candidate_id,
                target_affected=self._candidate_pre.target_id,
                emitted_frame=self._event.frame_id,
                emitted_simulation_time=self._event.simulation_time,
            )
            self._old_invalidated = True
            self._replan_pending = True
            self._receipt.update(
                {
                    "status": "TEMPORAL_INFORMATION_UPDATE_EMITTED",
                    "level": 4,
                    "invalidated_candidate_id": self._candidate_pre.candidate_id,
                    "invalidated_at_frame": self._event.frame_id,
                }
            )

    def _create_pre_candidate(self, track: Any) -> None:
        assert self._target is not None and self._event is not None
        prompt = (
            "Wait while the tracked bus still occupies the forward conflict corridor; "
            "after its full bounding box clears, take the upcoming {} branch {}."
        ).format(self._target.road_option.casefold(), self._target.branch_id)
        self._candidate_pre = TemporalCandidate.create(
            instruction_id=self.instruction_id,
            raw_instruction=self.raw_instruction,
            prompt_text=prompt,
            referent_id=track.track_id,
            event_id=self._event.event_id,
            event_state=self._event.state,
            target=self._target,
            relation="AFTER_FULL_BBOX_CLEARS_RUNTIME_REGION",
            maneuver="TURN_" + self._target.road_option,
            generation_frame=track.frame_id,
            source_observation_id=str(self._latest_observation_id),
        )
        binding = build_bounded_wait_pilot_binding(
            run_id=self.instruction_id,
            source_observation_id=str(self._latest_observation_id),
            source_frame_id=str(track.frame_id),
            candidate_set_id=self._candidate_pre.candidate_set_id,
            start_monotonic_time=time.monotonic(),
            duration_s=WAIT_LEASE_DURATION_SECONDS,
            source_simulation_time=self._latest_simulation_time,
        )
        self._wait_binding = binding
        entered = self.wait.enter(
            binding,
            current_monotonic_time=time.monotonic(),
            frame=track.frame_id,
            ego_position=self._latest_position,
            ego_speed_mps=self._latest_speed,
            baseline_control_path_healthy=True,
            no_control_ownership_conflict=True,
            native_carla_session_available=True,
            source_identity_recordable=True,
            environment_digest=self._latest_image_sha,
        )
        if not entered:
            self._receipt.update(
                {
                    "status": "BLOCKED_WAIT_INFORMATION_UPDATE_BINDING",
                    "blocker": self.wait.summary().get("entry_rejection"),
                }
            )
            self._terminal = True
        else:
            self._receipt.update(
                {
                    "status": "WAIT_ACTIVE_TRACKING_TEMPORAL_CONDITION",
                    "level": 3,
                    "wait_entry_frame": track.frame_id,
                    "pre_event_decision": "WAIT",
                    "wait_stationary_or_moving_reason": (
                        "MOVING_OR_STATIONARY_AS_PRODUCED_BY_CURRENT_VALID_BASELINE_PLAN; "
                        "TEMPORAL_BRANCH DOES_NOT_ZERO_SPEED_OR_WRITE_BRAKE"
                    ),
                }
            )

    def _update_wait_and_event(self, frame_id: int) -> None:
        if not self.wait.active:
            return
        information_changed = self._information is not None
        self.wait.observe_tick(
            frame=frame_id,
            current_monotonic_time=time.monotonic(),
            ego_position=self._latest_position,
            ego_speed_mps=self._latest_speed,
            baseline_control_path_healthy=True,
            source_runtime_valid=not information_changed,
            environment_digest=self._latest_image_sha,
            source_simulation_time=self._latest_simulation_time,
        )
        if information_changed:
            self._receipt.update(
                {
                    "wait_exit_frame": frame_id,
                    "wait_exit_reason": self.wait.summary().get("exit_reason"),
                    "replan_required": True,
                }
            )

    def on_model_output(
        self, baseline_route: Any, baseline_speed: Any, model_start: float, model_end: float
    ) -> None:
        del model_start, model_end
        self._normal_forwards += 1
        try:
            if self._terminal:
                self._persist()
                return
            if self._candidate_pre is not None and self._pre_forward is None:
                self._pre_forward = self._forward(self._candidate_pre)
                self._receipt["pre_event_plan_captured_frame"] = self._latest_frame
            if self._replan_pending and self._candidate_post is None:
                self._run_fresh_replan()
            self._persist()
        except Exception as exc:
            self._errors.append(
                {
                    "stage": "ON_MODEL_OUTPUT",
                    "frame": self._latest_frame,
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
            )
            self._receipt["status"] = "BLOCKED_TEMPORAL_REPLAN_NOT_TRIGGERED"
            self._terminal = True
            self._persist()

    def _forward(self, candidate: TemporalCandidate) -> Any:
        runtime = RuntimeCandidate(
            candidate_id=candidate.candidate_id,
            interpretation_id=candidate.interpretation_id,
            prompt_text=candidate.prompt_text,
            visual_track_id=candidate.referent_id,
            visual_anchor_digest=canonical_sha256(
                {"track_id": candidate.referent_id, "event_id": candidate.event_id}
            ),
            candidate_semantic_digest=candidate.semantic_sha256,
            candidate_input_digest=canonical_sha256(candidate.to_dict()),
            source_observation_id=candidate.source_observation_id,
            source_frame_id=candidate.generation_frame,
            generator_rule="TEMPORAL_REFERENT_EVENT_TARGET_BINDING_V1",
        )
        self.forward_provider.begin_event()
        episode = type(
            "TemporalEpisode",
            (),
            {
                "ego_state": type("Ego", (), {"speed_mps": self._latest_speed})(),
                "vision_observation": type(
                    "Vision",
                    (),
                    {
                        "observation_id": candidate.source_observation_id,
                        "frame_id": candidate.generation_frame,
                    },
                )(),
            },
        )()
        result = self.forward_provider(episode, runtime)
        self._candidate_forwards += 1
        return result

    def _run_fresh_replan(self) -> None:
        assert self._event is not None and self._target is not None
        prompt = (
            "The same tracked bus has now fully cleared the forward conflict corridor. "
            "Take the upcoming {} branch {}."
        ).format(self._target.road_option.casefold(), self._target.branch_id)
        self._candidate_post = TemporalCandidate.create(
            instruction_id=self.instruction_id,
            raw_instruction=self.raw_instruction,
            prompt_text=prompt,
            referent_id=self._event.track_id,
            event_id=self._event.event_id,
            event_state=EventState.CLEARED,
            target=self._target,
            relation="AFTER_CONFIRMED_CLEARANCE",
            maneuver="TURN_" + self._target.road_option,
            generation_frame=int(self._latest_frame),
            source_observation_id=str(self._latest_observation_id),
        )
        self._post_forward = self._forward(self._candidate_post)
        self._continue_m3_replan()
        self._replan_pending = False
        self._receipt.update(
            {
                "status": "SHADOW_FRESH_TEMPORAL_REPLAN_PASS",
                "level": 4,
                "fresh_replan_id": self._candidate_post.candidate_set_id,
                "fresh_planning_frame": self._latest_frame,
                "post_event_decision": "ACT",
                "candidate_identity_preserved_across_state_change": (
                    self._candidate_pre is not None
                    and self._candidate_pre.referent_id == self._candidate_post.referent_id
                    and self._candidate_pre.event_id == self._candidate_post.event_id
                    and self._candidate_pre.target_id == self._candidate_post.target_id
                ),
            }
        )
        if self.control_enabled:
            self._arm_closed_loop()
        else:
            self._terminal = True

    def _continue_m3_replan(self) -> None:
        state = self.wait.state
        if state is None:
            raise RuntimeError("M3_WAIT_STATE_MISSING_AT_REPLAN")
        now = time.monotonic()
        events = []
        results = []
        for event_type, payload in (
            (EventType.REVALIDATION_PASSED, {}),
            (
                EventType.REPLAN_COMPLETE,
                {
                    "candidate_freshness": "FRESH",
                    "candidate_set_id": self._candidate_post.candidate_set_id,
                },
            ),
        ):
            event = MinimalM3Event.create(
                event_id="{}:{}:{}".format(
                    self.instruction_id, event_type.value.casefold(), self._latest_frame
                ),
                event_type=event_type,
                query_episode_id=(
                    None
                    if self._wait_binding is None
                    else self._wait_binding.query_episode_id
                ),
                source_component="TEMPORAL_GROUNDING_V1_REPLAN",
                observed_monotonic_time=now,
                source_simulation_time=self._latest_simulation_time,
                payload=payload,
            )
            reduced = reduce_event(state, event, now)
            if reduced.processing_results != (ProcessingResult.PROCESSED,):
                raise RuntimeError("M3_TEMPORAL_REPLAN_TRANSITION_REJECTED")
            state = reduced.state
            events.append(event.to_dict())
            results.append(reduced.to_dict())
        self._m3_replan = {
            "events": events,
            "results": results,
            "final_state": state.to_dict(),
            "expected_transition_chain": ["MC-T017", "MC-T019"],
        }

    def _arm_closed_loop(self) -> None:
        assert self._candidate_post is not None and self._post_forward is not None
        post = self._forward_receipt(self._post_forward)
        old = self._forward_receipt(self._pre_forward)
        if post is None:
            raise RuntimeError("POST_EVENT_PLAN_MISSING")
        replan = {
            "fresh_model_computation": True,
            "source_observation_id": self._candidate_post.source_observation_id,
            "source_frame_id": str(self._candidate_post.generation_frame),
            "route_digest": post["raw_route_tensor_sha256"],
            "speed_digest": post["raw_speed_tensor_sha256"],
            "plan_id": self._candidate_post.candidate_set_id,
            "resolved_interpretation_id": self._candidate_post.interpretation_id,
            "resolved_instruction_text": self._candidate_post.prompt_text,
            # LimitedAct materializes these values with ``baseline.new_tensor``.
            # Preserve the ordinary SimLingo batch dimension required by its
            # existing PID: [1, 20, 2] route and [1, 10, 2] speed.
            "route": [post["route"]],
            "speed": [post["speed"]],
            "model_route_digest": post["route_sha256"],
            "model_speed_digest": post["speed_sha256"],
            "model_output_dtype": str(getattr(self._post_forward.raw_route, "dtype", "UNKNOWN")),
            "execution_plan_dtype": str(getattr(self._post_forward.raw_route, "dtype", "UNKNOWN")),
            "execution_projection": (
                "IDENTITY_FROM_FRESH_SIMLINGO_CANDIDATE_FORWARD_WITH_ORIGINAL_BATCH_DIM"
            ),
        }
        old_candidate = None
        if self._candidate_pre is not None and old is not None:
            old_candidate = {
                "candidate_id": self._candidate_pre.candidate_id,
                "candidate_set_id": self._candidate_pre.candidate_set_id,
                "resolved_interpretation_id": self._candidate_pre.interpretation_id,
                "source_observation_id": self._candidate_pre.source_observation_id,
                "source_frame_id": str(self._candidate_pre.generation_frame),
                "route_digest": old["raw_route_tensor_sha256"],
                "speed_digest": old["raw_speed_tensor_sha256"],
            }
        armed = self.limited_act.arm_from_replan(
            replan,
            old_candidate=old_candidate,
            natural_m2b_action="ACT",
            current_frame=self._latest_frame,
            current_observation_id=self._latest_observation_id,
            current_snapshot={
                "ego_position": self._latest_position,
                "ego_speed_mps": self._latest_speed,
                "actual_control": _control_values(getattr(self.agent, "control", None)),
            },
            active_query=False,
            active_wait=False,
            safety_active=False,
        )
        if not armed:
            raise RuntimeError("BLOCKED_TEMPORAL_CLOSED_LOOP_AUTHORITY")
        self._receipt.update(
            {
                "status": "FRESH_REPLAN_AUTHORITY_ARMED",
                "level": 5,
                "closed_loop_candidate_plan_selection_pending": True,
            }
        )

    def select_plan_source(
        self, baseline_route: Any, baseline_speed: Any, current_monotonic: float
    ) -> Tuple[Any, Any]:
        if not self.control_enabled:
            return baseline_route, baseline_speed
        return self.limited_act.select_plan_source(
            baseline_route,
            baseline_speed,
            frame=self._latest_frame,
            observation_id=self._latest_observation_id,
            current_monotonic=current_monotonic,
            active_query=False,
            active_wait=self.wait.active,
            safety_active=False,
        )

    def on_pid_invocation(self, current_monotonic: float) -> None:
        self._pid_invocations += 1
        self.limited_act.on_pid_invocation(
            frame=self._latest_frame, current_monotonic=current_monotonic
        )

    def on_control(self, control: Any, gt_velocity: Any, current_monotonic: float) -> None:
        del gt_velocity
        self._control_observations += 1
        if self.wait.active:
            self.wait.observe_baseline_control(
                control, frame=self._latest_frame, ready_time=current_monotonic
            )
        self.limited_act.on_control(
            control,
            frame=self._latest_frame,
            ready_time=current_monotonic,
        )
        self._update_terminal_status()
        self._persist()

    def _update_terminal_status(self) -> None:
        if not self.control_enabled or self._candidate_post is None:
            return
        limited = self.limited_act.summary()
        if str(limited.get("status", "")).startswith("PASS_LIMITED_CLOSED_LOOP"):
            self._receipt.update(
                {
                    "status": "BOUNDED_WAIT_TO_REPLAN_CLOSED_LOOP_PASS",
                    "level": 5,
                    "closed_loop_candidate_plan_selection_pending": False,
                    "post_event_decision": "ACT",
                    "control_owner": "EXISTING_SIMLINGO_PID_AFTER_M3_AUTHORITY",
                }
            )
            self._terminal = True
        elif str(limited.get("status", "")).startswith("BLOCKED_"):
            self._receipt["status"] = "BLOCKED_TEMPORAL_REPLAN_NOT_TRIGGERED"
            self._receipt["blocker"] = limited.get("status")
            self._terminal = True

    def _record_rgb(self, image: Any, frame_id: int) -> None:
        if not self._grounded or len(self._rgb_identities) >= 180:
            return
        import cv2

        path = self.frames_dir / "frame_{:06d}.png".format(frame_id)
        cv2.imwrite(str(path), image[:, :, :3])
        self._rgb_identities.append(
            {
                "frame_id": frame_id,
                "simulation_time": self._latest_simulation_time,
                "rgb_sha256": self._latest_image_sha,
                "path": str(path),
                "stored_png_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )

    def _render(self) -> None:
        if self._event is None or self._target is None or self._region is None:
            return
        track = next(
            (item for item in self._tracks if item.track_id == self._primary_track_id),
            None,
        )
        if track is None:
            return
        render_temporal_panel(
            self.output_dir,
            self._latest_image,
            instruction=self.raw_instruction,
            track=track,
            trail=self._trail,
            event=self._event,
            region=self._region,
            target=self._target,
            information_changed=self._information is not None,
            replan_required=self._replan_pending,
            decision=(
                "ACT"
                if self._candidate_post is not None
                else "WAIT"
                if self.wait.active
                else "UNKNOWN"
            ),
        )

    def commit(self) -> None:
        self._persist()

    def close(self) -> None:
        self._closed = True
        self._receipt["closed"] = True
        self._persist()

    def summary(self) -> Dict[str, Any]:
        return {
            "enabled": True,
            "status": self._receipt.get("status"),
            "level": self._receipt.get("level"),
            "detector_forward_count": len(self._detector_invocation_frames),
            "tracker_forward_count": len(self.tracker.latencies_seconds),
            "candidate_forward_count": self._candidate_forwards,
            "candidate_direct_vehicle_control_writes": 0,
            "m3_direct_vehicle_control_writes": 0,
            "new_pid": 0,
        }


def build_temporal_grounding_v1_runtime(agent: Any) -> TemporalGroundingV1Runtime:
    if not _truthy(os.environ.get(FEATURE_FLAG)):
        raise RuntimeError("TEMPORAL_GROUNDING_V1_FEATURE_FLAG_OFF")
    output = os.environ.get(OUTPUT_ENV) or os.environ.get("DRIVECLARIFY_SHADOW_OUTPUT_DIR")
    if not output:
        raise RuntimeError("TEMPORAL_GROUNDING_V1_OUTPUT_DIR_MISSING")
    return TemporalGroundingV1Runtime(agent, output)


__all__ = [
    "CONTROL_ENV",
    "DEVICE_ENV",
    "OUTPUT_ENV",
    "RAW_INSTRUCTION_ENV",
    "RECEIPT_FILENAME",
    "TemporalGroundingV1Runtime",
    "build_temporal_grounding_v1_runtime",
]
