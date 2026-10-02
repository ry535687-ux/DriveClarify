"""Default-off passive native runtime for E2 V4."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from driveclarify_language_grounding_v1.visual_grounder import GroundingDinoVisualGrounder
from driveclarify_rq2_t.measurement import canonical_sha256
from driveclarify_rq2_t_v2.native_runtime import _counter_snapshot
from driveclarify_rq2_t_e2_v3.runtime import (
    E2V3NativeEvidenceRuntime,
    _atomic_json,
    _candidate_queries,
    _color_from_referent,
    _empty_association,
    _front,
    _suppress_nested_duplicate_detections,
)

from .association import (
    PRECALIBRATION_CONFIGURATION,
    AssociationConfiguration,
    AssociationThresholdsV4,
    associate_candidates_to_tracks_v4,
)
from .contracts import (
    AcquisitionSchedule,
    METHOD_ID,
    parse_certified_candidate,
    validate_candidate_set,
)
from .method import EvidenceEnabledDomainInvariantMethodV4
from .tracker import AcquisitionClockTracker, RuntimeDetection


FEATURE_FLAG = "DRIVECLARIFY_RQ2_T_E2_V4_NATIVE_EVIDENCE"
CANDIDATES_ENV = "DRIVECLARIFY_E2_V4_CERTIFIED_CANDIDATES_JSON"
CONFIGURATION_ENV = "DRIVECLARIFY_E2_V4_CONFIGURATION_JSON"
DEVICE_ENV = "DRIVECLARIFY_E2_V4_DETECTOR_DEVICE"
RGB_ARCHIVE_ENV = "DRIVECLARIFY_E2_V4_RGB_ARCHIVE_DIR"
TRACE_FILENAME = "E2_V4_RUNTIME_TRACE.jsonl"
PAIRED_FILENAME = "E2_V4_PAIRED_VIEW_EVIDENCE.jsonl"
RECEIPT_FILENAME = "E2_V4_NATIVE_RUNTIME_RECEIPT.json"


def _load_candidates() -> tuple[Any, ...]:
    raw = os.environ.get(CANDIDATES_ENV)
    if not raw:
        raise RuntimeError("E2_V4_CERTIFIED_CANDIDATES_ENV_MISSING")
    value = json.loads(raw)
    if not isinstance(value, list):
        raise ValueError("E2_V4_CERTIFIED_CANDIDATES_MUST_BE_LIST")
    specs = tuple(
        parse_certified_candidate(
            str(row["candidate_id"]), str(row["interpretation_id"]), str(row["text"]),
            overrides=row.get("overrides"),
        )
        for row in value if isinstance(row, Mapping)
    )
    validate_candidate_set(specs)
    return specs


def _load_configuration() -> AssociationConfiguration:
    raw = os.environ.get(CONFIGURATION_ENV)
    if not raw:
        return PRECALIBRATION_CONFIGURATION
    value = json.loads(raw)
    thresholds = value.get("thresholds") or value
    return AssociationConfiguration(
        str(value.get("weight_profile", "BALANCED_INVARIANT")),
        AssociationThresholdsV4(
            float(thresholds["association_score_threshold"]),
            float(thresholds["uniqueness_margin_threshold"]),
            int(thresholds["minimum_track_hits"]),
            int(thresholds["minimum_track_age_acquisitions"]),
        ),
    )


class E2V4NativeEvidenceRuntime(E2V3NativeEvidenceRuntime):
    enabled = True

    def __init__(self, base: Any, output_dir: str, raw_instruction: str) -> None:
        self.base = base
        self.agent = base.agent
        self.raw_instruction = str(raw_instruction)
        self.output_dir = Path(output_dir)
        self.trace_path = self.output_dir / TRACE_FILENAME
        self.paired_path = self.output_dir / PAIRED_FILENAME
        self.receipt_path = self.output_dir / RECEIPT_FILENAME
        self.frames_dir = self.output_dir / "E2_V4_KEY_RGB"
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        archive = os.environ.get(RGB_ARCHIVE_ENV)
        self.rgb_archive_dir = None if not archive else Path(archive)
        if self.rgb_archive_dir is not None:
            self.rgb_archive_dir.mkdir(parents=True, exist_ok=True)
        self.rgb_archive_rows = []
        self.candidates = _load_candidates()
        self.candidate_validation = validate_candidate_set(self.candidates)
        self.configuration = _load_configuration()
        self.thresholds = self.configuration.thresholds
        self.family = str(os.environ.get("DRIVECLARIFY_E2_V4_AMBIGUITY_TYPE") or "")
        self.schedule = AcquisitionSchedule()
        self.detector = GroundingDinoVisualGrounder(device=os.environ.get(DEVICE_ENV, "cpu"))
        self.tracker = AcquisitionClockTracker()
        self.b1 = EvidenceEnabledDomainInvariantMethodV4(use_temporal_memory=False, configuration=self.configuration)
        self.b2 = EvidenceEnabledDomainInvariantMethodV4(use_temporal_memory=True, configuration=self.configuration)
        self.trace_rows = []
        self.paired_rows = []
        self.errors = []
        self.detector_invocations = []
        self.duplicate_detections_suppressed = []
        self.key_frames: Dict[str, Mapping[str, Any]] = {}
        self._frame_states: Dict[int, Mapping[str, Any]] = {}
        self._latest_images: Dict[int, Any] = {}
        self._first_frame: Optional[int] = None
        self._last_invoked_frame: Optional[int] = None
        self._processed_history_count = 0
        self._observed_source_frames = set()
        self._fallback_paired_row_count = 0
        self._previous_assignment: Dict[str, str] = {}
        self._previous_context: Optional[Mapping[str, Any]] = None
        self._tracker_event_index = 0
        self._track_loss_was_active = False
        self._construction_counters = _counter_snapshot(base)
        self._persist_receipt("ACTIVE")

    def _metadata(self) -> Mapping[str, Any]:
        required = {
            "scene_id": os.environ.get("DRIVECLARIFY_E2_V4_SCENE_ID"),
            "episode_id": os.environ.get("DRIVECLARIFY_E2_V4_EPISODE_ID"),
            "seed": os.environ.get("DRIVECLARIFY_E2_V4_ENGINEERING_SEED"),
            "ambiguity_type": os.environ.get("DRIVECLARIFY_E2_V4_AMBIGUITY_TYPE"),
            "map_name": os.environ.get("DRIVECLARIFY_E2_V4_MAP"),
            "route_identity": os.environ.get("DRIVECLARIFY_E2_V4_ROUTE_IDENTITY"),
            "commitment_certificate_sha256": os.environ.get("DRIVECLARIFY_E2_V4_COMMITMENT_CERTIFICATE_SHA256"),
        }
        if any(value in (None, "") for value in required.values()):
            raise RuntimeError("E2_V4_NATIVE_METADATA_INCOMPLETE")
        result = dict(required)
        result["seed"] = int(result["seed"])
        return result

    def _invoke_detector(self, image: Any, frame_id: int, observation_id: str, now_s: float, trigger: str) -> Sequence[RuntimeDetection]:
        rows = []
        count = 0
        for phrase, object_class, color in _candidate_queries(self.candidates):
            if count >= self.schedule.maximum_detector_invocations_per_tick:
                break
            grounding = self.detector.ground(image, phrase, frame_id=frame_id, observation_id=observation_id)
            self.detector_invocations.append({
                "frame_id": int(frame_id), "simulation_time_s": float(now_s),
                "source_observation_id": observation_id, "query": phrase, "trigger": trigger,
                "detector_latency_seconds": grounding.detector_latency_seconds,
                "raw_proposal_count": len(grounding.raw_referents),
                "plausible_proposal_count": len(grounding.plausible_referents),
                "selected_proposal_count": len(grounding.selected_referents),
                "raw_detector_output": grounding.to_dict(),
            })
            count += 1
            for referent in grounding.selected_referents:
                rows.append(RuntimeDetection(
                    bbox_xyxy=tuple(float(value) for value in referent.bbox_xyxy),
                    detector_support_score=float(referent.detector_confidence),
                    object_class=object_class,
                    color=_color_from_referent(referent, color),
                    phrase=phrase,
                    source_frame_id=int(frame_id),
                    source_observation_id=observation_id,
                    source_detection_id=str(referent.local_object_id),
                    source_kind="GROUNDING_DINO_" + trigger,
                ))
        rows, suppressed = _suppress_nested_duplicate_detections(rows)
        self.duplicate_detections_suppressed.extend(suppressed)
        self._last_invoked_frame = int(frame_id)
        if rows:
            self._save_key_frame("FIRST_DETECTOR_HIT", frame_id, image)
        return rows

    def _process_frame(self, input_data: Any, tick_data: Any, timestamp: Any, frame: Any, observation_id: Any) -> None:
        del tick_data
        sensor_frame, image = _front(input_data)
        frame_id = int(frame)
        if sensor_frame != frame_id:
            raise RuntimeError("E2_V4_SENSOR_FRAME_MISMATCH")
        now_s = float(timestamp)
        self._archive_rgb_frame(frame_id, image)
        if self._first_frame is None:
            self._first_frame = frame_id
            self._save_key_frame("INITIAL_PRE_REVEAL_RUNTIME_FRAME", frame_id, image)
        self._latest_images[frame_id] = image
        while len(self._latest_images) > 4:
            self._latest_images.pop(sorted(self._latest_images)[0], None)
        track_loss_active = any(track.state in {"TEMPORARILY_LOST", "LOST"} for track in self.tracker.tracks)
        track_loss = bool(track_loss_active and not self._track_loss_was_active)
        self._track_loss_was_active = track_loss_active
        resolved = bool(self.trace_rows and self.trace_rows[-1].get("association", {}).get("unique_one_to_one_assignment"))
        due, trigger = self.schedule.should_invoke(
            frame_id=frame_id, first_frame_id=int(self._first_frame), resolved=resolved,
            track_loss_event=track_loss, already_invoked_frame=self._last_invoked_frame,
        )
        detections = []
        if due:
            detections = list(self._invoke_detector(image, frame_id, str(observation_id), now_s, trigger))
            self.tracker.update(detections, frame_id=frame_id, simulation_time_s=now_s)
        else:
            self.tracker.advance_frame(frame_id)
        association = associate_candidates_to_tracks_v4(
            self.candidates, self.tracker.tracks, family=self.family,
            configuration=self.configuration, image_width=int(image.shape[1]),
            image_height=int(image.shape[0]), invariant_observations=None,
        ) if self.tracker.tracks or self.family in {"ORDER", "UNDERSPECIFIED_CONSTRAINT"} else _empty_association(self.candidates, "NO_TRACKS")
        if self.tracker.tracks and all(track.hit_count >= self.thresholds.minimum_track_hits for track in self.tracker.tracks):
            self._save_key_frame("FIRST_STABLE_TRACK", frame_id, image)
        if association.get("unique_one_to_one_assignment"):
            self._save_key_frame("FIRST_UNIQUE_ASSOCIATION", frame_id, image)
        snapshot = self.tracker.snapshot()
        trace = {
            "schema_version": "driveclarify.e2_v4.runtime_trace.v1",
            "frame_id": frame_id, "source_observation_id": str(observation_id),
            "simulation_time_s": now_s, "detector_invoked": bool(due),
            "detector_trigger": trigger,
            "detector_acquisition_opportunity_index": self.tracker.acquisition_opportunity_index,
            "detector_invocation_count_total": len(self.detector_invocations),
            "detections": [copy.deepcopy(row.__dict__) for row in detections],
            "tracks": copy.deepcopy(snapshot["tracks"]), "association": association,
        }
        trace["trace_digest"] = canonical_sha256(trace)
        self.trace_rows.append(trace)
        self._frame_states[frame_id] = trace
        while len(self._frame_states) > 80:
            self._frame_states.pop(sorted(self._frame_states)[0], None)

    def _persist_receipt(self, status: str) -> None:
        receipt = {
            "schema_version": "driveclarify.e2_v4.native_runtime_receipt.v1",
            "status": status, "method_id": METHOD_ID,
            "historical_method": "E2_V3_TRACKED_ASSOCIATION=DEVELOPMENT_PASS_BLIND_GENERALIZATION_FAIL",
            "feature_flag": FEATURE_FLAG, "feature_flag_default": "OFF",
            "base_runtime_type": type(self.base).__module__ + "." + type(self.base).__qualname__,
            "family": self.family,
            "candidate_validation": self.candidate_validation,
            "candidate_specs": [row.to_dict() for row in self.candidates],
            "acquisition_schedule": self.schedule.to_dict(),
            "configuration": self.configuration.to_dict(),
            "configuration_provenance": os.environ.get("DRIVECLARIFY_E2_V4_CONFIGURATION_PROVENANCE", "PRECALIBRATION_FROZEN_GRID_MEMBER"),
            "detector_invocation_count": len(self.detector_invocations),
            "detector_invocations": copy.deepcopy(self.detector_invocations),
            "duplicate_detection_suppression_count": len(self.duplicate_detections_suppressed),
            "duplicate_detections_suppressed": copy.deepcopy(self.duplicate_detections_suppressed),
            "tracker": self.tracker.snapshot(),
            "trace_row_count": len(self.trace_rows), "paired_row_count": len(self.paired_rows),
            "fallback_paired_row_count": self._fallback_paired_row_count,
            "key_frames": copy.deepcopy(self.key_frames), "errors": copy.deepcopy(self.errors),
            "rgb_archive_enabled": self.rgb_archive_dir is not None,
            "rgb_archive_frame_count": len(self.rgb_archive_rows),
            "rgb_archive_rows": copy.deepcopy(self.rgb_archive_rows),
            "construction_counters": self._construction_counters,
            "latest_production_counters": _counter_snapshot(self.base),
            "observer_added_vla_forwards": 0,
            "observer_added_candidate_computations": 0,
            "observer_added_pid_instances": 0,
            "observer_control_writes": 0,
            "observer_route_planner_advances": 0,
            "oracle_input_reads": 0,
            "carla_actor_id_runtime_reads": 0,
            "reveal_time_or_frame_runtime_reads": 0,
            "raw_detector_score_used_as_identity_confidence": False,
            "identity_score_is_probability": False,
            "image_side_used_for_semantic_left_right": False,
            "maturity_clock_domain": "DETECTOR_ACQUISITION_OPPORTUNITIES",
        }
        receipt["receipt_digest"] = canonical_sha256(receipt)
        _atomic_json(self.receipt_path, receipt)

    def summary(self) -> Mapping[str, Any]:
        value = dict(self.base.summary()) if hasattr(self.base, "summary") else {}
        value["e2_domain_invariant_association_v4"] = {
            "enabled": True,
            "detector_invocations": len(self.detector_invocations),
            "tracks_created": self.tracker.snapshot()["tracks_created"],
            "paired_rows": len(self.paired_rows), "errors": len(self.errors),
        }
        return value


def _truthy(value: Any) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def build_e2_v4_native_runtime(agent: Any, raw_instruction: str, output_dir: str) -> Any:
    if not _truthy(os.environ.get(FEATURE_FLAG)):
        raise RuntimeError("E2_V4_NATIVE_FEATURE_FLAG_OFF")
    from driveclarify_persistent_ambiguity_runtime_v1.runtime import NativeCarlaRouteLocalEvidenceProvider, build_persistent_ambiguity_runtime

    provider = NativeCarlaRouteLocalEvidenceProvider.from_native_agent(agent)
    base = build_persistent_ambiguity_runtime(
        agent, str(raw_instruction), str(output_dir), route_local_evidence_provider=provider,
    )
    if hasattr(base, "_defer_full_receipt_io"):
        base._defer_full_receipt_io = True
        base._force_receipt_persist = False
    base._receipt.update({
        "e2_v4_native_factory": "driveclarify_rq2_t_e2_v4.runtime.build_e2_v4_native_runtime",
        "e2_v4_native_flag": FEATURE_FLAG,
        "e2_v4_native_flag_default": "OFF",
        "e2_v4_observational_only": True,
    })
    base._persist()
    return E2V4NativeEvidenceRuntime(base, output_dir, raw_instruction)


__all__ = ["E2V4NativeEvidenceRuntime", "FEATURE_FLAG", "build_e2_v4_native_runtime"]
