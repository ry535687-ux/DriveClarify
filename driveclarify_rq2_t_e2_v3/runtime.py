"""Default-off native E2 V3 runtime observer around the unchanged driving path."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from driveclarify_language_grounding_v1.visual_grounder import GroundingDinoVisualGrounder
from driveclarify_rq2_t.measurement import adapt_production_history_row, canonical_sha256
from driveclarify_rq2_t_v2.native_runtime import LOCAL_HORIZON_M, _counter_snapshot, _live_map, _safety_context
from driveclarify_rq2_t_v2.providers import assert_runtime_payload_has_no_oracle

from .association import AssociationThresholds, PRECALIBRATION_THRESHOLDS, associate_candidates_to_tracks
from .contracts import AcquisitionSchedule, CandidateObjectSpec, UNSPECIFIED, parse_certified_candidate, validate_candidate_set
from .method import EvidenceEnabledTrackedAssociationMethodV3
from .tracker import PersistentMultiObjectTracker, RuntimeDetection


FEATURE_FLAG = "DRIVECLARIFY_RQ2_T_E2_V3_NATIVE_EVIDENCE"
CANDIDATES_ENV = "DRIVECLARIFY_E2_V3_CERTIFIED_CANDIDATES_JSON"
THRESHOLDS_ENV = "DRIVECLARIFY_E2_V3_THRESHOLDS_JSON"
DEVICE_ENV = "DRIVECLARIFY_E2_V3_DETECTOR_DEVICE"
TRACE_FILENAME = "E2_V3_RUNTIME_TRACE.jsonl"
PAIRED_FILENAME = "E2_V3_PAIRED_VIEW_EVIDENCE.jsonl"
RECEIPT_FILENAME = "E2_V3_NATIVE_RUNTIME_RECEIPT.json"
RGB_ARCHIVE_ENV = "DRIVECLARIFY_E2_V3_RGB_ARCHIVE_DIR"


_TOPOLOGY_ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}


def _parse_topology_ordinal_v3(raw_instruction: str) -> Optional[int]:
    """Parse an ordinal with an optional explicit maneuver modifier."""

    text_value = str(raw_instruction).casefold()
    noun = r"(?:opening|junction|intersection|turn|branch|exit)"
    modifier = r"(?:(?:left|right|straight)\s+)?"
    for word, value in _TOPOLOGY_ORDINAL_WORDS.items():
        if re.search(r"\b" + word + r"\s+" + modifier + noun + r"\b", text_value):
            return value
    match = re.search(r"\b(10|[1-9])(?:st|nd|rd|th)?\s+" + modifier + noun + r"\b", text_value)
    return None if match is None else int(match.group(1))


def _topology_signal_v3(
    base: Any, history_row: Mapping[str, Any], now_s: float,
    route_version: str, environment_digest: str,
) -> Optional[Mapping[str, Any]]:
    ordinal = _parse_topology_ordinal_v3(str(base.raw_instruction))
    if ordinal is None:
        return None
    route_rows = base.topology_enumerator._route_rows(base._route())
    if len(route_rows) < 2:
        return None
    opportunities = base.topology_enumerator.enumerate(base._route(), _live_map())
    local = [row for row in opportunities if float(row.distance_or_progress) <= LOCAL_HORIZON_M + 1e-9]
    values = [
        {
            "junction_id": row.junction_id, "road_id": row.entry_road_id,
            "lane_id": row.entry_lane_id, "route_order_index": row.route_order_index,
            "maneuver_class": row.maneuver_direction, "locally_observable": True,
        }
        for row in local
    ]
    boundary = values[0]["junction_id"] if values else "NO_LOCAL_JUNCTION"
    return {
        "authorization_scope": "LOCAL_DEPLOYABLE_ROUTE_HORIZON",
        "source_kind": "LIVE_CARLA_HD_MAP_LOCAL_TOPOLOGY", "privileged": False,
        "source_frame_id": history_row.get("source_frame_id"),
        "source_observation_id": str(history_row.get("source_observation_id") or ""),
        "simulation_time_s": float(now_s), "route_version": route_version,
        "environment_digest": environment_digest, "required_ordinal": ordinal,
        "qualifying_opportunities": values, "topology_boundary_id": boundary,
        "local_horizon_end_progress_m": min(
            LOCAL_HORIZON_M,
            sum(
                ((route_rows[index][0] - route_rows[index - 1][0]) ** 2
                 + (route_rows[index][1] - route_rows[index - 1][1]) ** 2) ** 0.5
                for index in range(1, len(route_rows))
            ),
        ),
    }


def _anonymous_cross_traffic_hazard(base: Any) -> Mapping[str, Any]:
    """Observe a cross-traffic corridor without reading or emitting actor IDs."""

    provider = base._hard_gate_evidence_provider
    world = provider._world
    hero = provider._hero
    hero_transform = hero.get_transform()
    hero_location = hero_transform.location
    hero_forward = hero_transform.get_forward_vector()
    hero_right = hero_transform.get_right_vector()
    hazard_count = 0
    minimum_distance = None
    evaluated_count = 0
    for actor in world.get_actors():
        if actor is hero or not str(getattr(actor, "type_id", "")).startswith("vehicle."):
            continue
        try:
            transform = actor.get_transform()
            location = transform.location
            forward = transform.get_forward_vector()
            dx, dy = float(location.x - hero_location.x), float(location.y - hero_location.y)
            longitudinal = dx * float(hero_forward.x) + dy * float(hero_forward.y)
            lateral = dx * float(hero_right.x) + dy * float(hero_right.y)
            distance = math.hypot(dx, dy)
            heading_dot = float(forward.x) * float(hero_forward.x) + float(forward.y) * float(hero_forward.y)
        except (AttributeError, TypeError, ValueError):
            continue
        evaluated_count += 1
        if -4.0 <= longitudinal <= 15.0 and abs(lateral) <= 3.5 and distance <= 15.0 and abs(heading_dot) <= 0.50:
            hazard_count += 1
            minimum_distance = distance if minimum_distance is None else min(minimum_distance, distance)
    return {
        "anonymous_vehicle_count_evaluated": evaluated_count,
        "cross_traffic_hazard_count": hazard_count,
        "minimum_cross_traffic_distance_m": minimum_distance,
        "carla_actor_id_reads": 0,
        "hazard_observed": hazard_count > 0,
    }


def _safety_signal_v3(
    base: Any, *, frame_id: int, observation_id: str,
    route_version: str, environment_digest: str,
) -> Optional[Mapping[str, Any]]:
    """Join the existing hard-gate authority to an anonymous hazard check."""

    try:
        envelope = base._hard_gate_evidence_provider.resolve_hard_gate_evidence(
            source_observation_id=str(observation_id), source_frame_id=int(frame_id),
            route_version=route_version, environment_digest=environment_digest,
        )
        physical_status = str(envelope.physical_safety.status.value)
        rule_status = str(envelope.route_local_hard_rule.status.value)
        if physical_status not in {"VERIFIED_PHYSICAL_SAFETY_PASS", "VERIFIED_PHYSICAL_SAFETY_BLOCKED"}:
            return None
        if rule_status not in {"VERIFIED_ROUTE_LOCAL_PASS", "VERIFIED_ROUTE_LOCAL_BLOCKED"}:
            return None
        anonymous = _anonymous_cross_traffic_hazard(base)
        physical_pass = physical_status == "VERIFIED_PHYSICAL_SAFETY_PASS" and not anonymous["hazard_observed"]
        return {
            "authorization_scope": "CURRENT_RUNTIME_SAFETY_STATE",
            "source_frame_id": int(frame_id), "source_observation_id": str(observation_id),
            "current_physical_safety_gate": physical_pass,
            "hard_rule_gate": rule_status == "VERIFIED_ROUTE_LOCAL_PASS",
            "safe_holding_available": False, "shared_action_lease_valid": False,
            "holding_owner": None,
            "physical_certificate_sha256": envelope.physical_safety.certificate_sha256,
            "route_rule_certificate_sha256": envelope.route_local_hard_rule.certificate_sha256,
            "producer_id": envelope.producer_id,
            "anonymous_cross_traffic_observation": anonymous,
            "absence_of_hazard_treated_as_safe": False,
        }
    except Exception:
        return None


def _safety_state_digest(signal: Mapping[str, Any]) -> str:
    """Hash semantic safety state, excluding frame-varying provenance IDs."""

    anonymous = signal.get("anonymous_cross_traffic_observation") or {}
    return canonical_sha256({
        "current_physical_safety_gate": signal.get("current_physical_safety_gate"),
        "hard_rule_gate": signal.get("hard_rule_gate"),
        "safe_holding_available": signal.get("safe_holding_available"),
        "shared_action_lease_valid": signal.get("shared_action_lease_valid"),
        "anonymous_cross_traffic_hazard_observed": anonymous.get("hazard_observed"),
    })


def _truthy(value: Any) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(dict(value), handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _atomic_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _front(input_data: Any) -> Tuple[int, Any]:
    import numpy as np

    entry = input_data["rgb_0"]
    frame = int(entry[0]) if isinstance(entry, (tuple, list)) else -1
    image = entry[1] if isinstance(entry, (tuple, list)) else entry
    value = np.asarray(image)
    if value.ndim != 3 or value.shape[2] < 3:
        raise ValueError("E2_V3_RGB_0_INVALID")
    return frame, np.ascontiguousarray(value[:, :, :3]).copy()


def _load_candidates() -> Tuple[CandidateObjectSpec, ...]:
    raw = os.environ.get(CANDIDATES_ENV)
    if not raw:
        raise RuntimeError("E2_V3_CERTIFIED_CANDIDATES_ENV_MISSING")
    value = json.loads(raw)
    if not isinstance(value, list):
        raise ValueError("E2_V3_CERTIFIED_CANDIDATES_MUST_BE_LIST")
    specs = tuple(
        parse_certified_candidate(
            str(row["candidate_id"]), str(row["interpretation_id"]), str(row["text"]),
            overrides=row.get("overrides"),
        )
        for row in value if isinstance(row, Mapping)
    )
    validate_candidate_set(specs)
    return specs


def _load_thresholds() -> AssociationThresholds:
    raw = os.environ.get(THRESHOLDS_ENV)
    if not raw:
        return PRECALIBRATION_THRESHOLDS
    value = json.loads(raw)
    return AssociationThresholds(
        float(value["association_score_threshold"]),
        float(value["uniqueness_margin_threshold"]),
        int(value["minimum_track_hits"]),
        int(value["minimum_track_age_frames"]),
    )


def _candidate_queries(candidates: Sequence[CandidateObjectSpec]) -> Tuple[Tuple[str, str, str], ...]:
    rows = []
    for candidate in candidates:
        object_class = candidate.object_class
        color = candidate.color
        if object_class == UNSPECIFIED:
            continue
        phrase = ((color + " ") if color != UNSPECIFIED else "") + object_class
        rows.append((phrase.strip(), object_class, color))
    return tuple(dict.fromkeys(rows))


def _nested_containment(left: RuntimeDetection, right: RuntimeDetection) -> float:
    a, b = left.bbox_xyxy, right.bbox_xyxy
    intersection = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[3], b[3]) - max(a[1], b[1])
    )
    left_area = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    right_area = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    denominator = min(left_area, right_area)
    return 0.0 if denominator <= 0.0 else intersection / denominator


def _suppress_nested_duplicate_detections(
    detections: Sequence[RuntimeDetection], *, containment_threshold: float = 0.80,
) -> Tuple[Tuple[RuntimeDetection, ...], Tuple[Mapping[str, Any], ...]]:
    """Suppress nested same-query boxes before they can create two tracks."""

    kept = []
    suppressed = []
    for detection in detections:
        duplicate_index = next((
            index for index, existing in enumerate(kept)
            if existing.phrase == detection.phrase
            and existing.object_class == detection.object_class
            and existing.color == detection.color
            and _nested_containment(existing, detection) >= containment_threshold
        ), None)
        if duplicate_index is None:
            kept.append(detection)
            continue
        existing = kept[duplicate_index]
        if detection.detector_support_score > existing.detector_support_score:
            kept[duplicate_index] = detection
            removed, retained = existing, detection
        else:
            removed, retained = detection, existing
        suppressed.append({
            "source_frame_id": detection.source_frame_id,
            "phrase": detection.phrase,
            "removed_source_detection_id": removed.source_detection_id,
            "retained_source_detection_id": retained.source_detection_id,
            "containment_fraction": _nested_containment(existing, detection),
            "reason": "SAME_QUERY_NESTED_PROPOSAL_NOT_DISTINCT_OBJECT",
        })
    return tuple(kept), tuple(suppressed)


def _color_from_referent(referent: Any, fallback: str) -> str:
    attributes = dict(getattr(referent, "appearance_attributes", ()) or ())
    return str(attributes.get("requested_color") or fallback or UNSPECIFIED)


def _empty_association(candidates: Sequence[CandidateObjectSpec], reason: str) -> Mapping[str, Any]:
    candidate_ids = [row.candidate_id for row in candidates]
    value = {
        "schema_version": "driveclarify.e2_v3.candidate_track_matrix.v1",
        "candidate_ids": candidate_ids, "track_ids": [],
        "matrix": {candidate_id: {} for candidate_id in candidate_ids},
        "pair_details": {candidate_id: {} for candidate_id in candidate_ids},
        "selected_assignment": {},
        "per_candidate": {
            candidate_id: {
                "selected_track_id": None, "top1_track_id": None,
                "top1_score": None, "top2_score": None, "uniqueness_margin": None,
                "qualified": False, "reason_codes": [reason],
            }
            for candidate_id in candidate_ids
        },
        "unique_one_to_one_assignment": False,
        "qualification_reason_codes": [reason],
        "score_is_probability": False, "score_name": "identity_association_score",
    }
    value["matrix_digest"] = canonical_sha256(value)
    return value


def _conservative_v1_history_row(frame_id: int, observation_id: str) -> Mapping[str, Any]:
    """Create a B0-only source row when production emitted no decision window.

    This row deliberately contains no certified candidate or V3 association
    fact.  It therefore adapts to the same nine-field V1 schema with UNKNOWN
    preserved throughout.  It is local to the observer and is never appended
    to the production runtime's persistent decision history.
    """

    reason = ["NO_PRODUCTION_DECISION_WINDOW_AT_SOURCE_OBSERVATION"]
    return {
        "source_frame_id": int(frame_id),
        "source_observation_id": str(observation_id),
        "candidate_ids": [],
        "current_planning_ambiguity_active": False,
        "planning_effective_k": None,
        "candidate_relationship": None,
        "_e2_v3_conservative_fallback": True,
        "m2b_inputs": {
            "semantic_state": None,
            "active_candidate_count": 0,
            "multiple_plausible_interpretations": False,
            "answer_changes_decision": None,
            "hard_safety_gate": None,
            "hard_rule_gate": None,
            "safe_holding_available": None,
            "evidence": {
                "source": {
                    "source_frame_id": int(frame_id),
                    "source_observation_id": str(observation_id),
                },
                "current_action": {"availability": "UNKNOWN", "reason_codes": reason},
                "future_obligation": {
                    "availability": "UNKNOWN", "rows": [], "reason_codes": reason,
                },
                "recoverability": {
                    "availability": "UNKNOWN", "status": "UNKNOWN", "reason_codes": reason,
                },
                "shared_action_lease": {"valid": False},
            },
        },
    }


def _deployable_route_context(base: Any, map_name: str) -> Tuple[str, str]:
    """Read a stable route/environment binding without initializing V1 state.

    The legacy runtime normally materializes these identities only after it
    creates an ambiguity decision window.  E2 V3 must also support scenes in
    which that window is absent, so the passive observer derives the same
    route version directly from the already-owned dense route.  No attribute
    on ``base`` is written.
    """

    route_version = getattr(base, "_runtime_route_version", None)
    if not route_version:
        from driveclarify_persistent_ambiguity_runtime_v1.evidence_adapter import (
            DecisionWindowEvidenceAdapter,
        )

        route_rows = base.topology_enumerator._route_rows(base._route())
        if len(route_rows) < 2:
            raise ValueError("E2_V3_DEPLOYABLE_ROUTE_BINDING_UNAVAILABLE")
        route_version = DecisionWindowEvidenceAdapter.route_version_id(
            [[float(x), float(y)] for x, y, _ in route_rows],
            source="AGENT_OWNED_DENSE_CARLA_WORLD_ROUTE",
        )
    environment_digest = getattr(base, "_runtime_environment_digest", None)
    if not environment_digest:
        environment_digest = canonical_sha256(
            {"map": str(map_name), "route_version": str(route_version)}
        )
    return str(route_version), str(environment_digest)


class E2V3NativeEvidenceRuntime:
    enabled = True

    def __init__(self, base: Any, output_dir: str, raw_instruction: str) -> None:
        self.base = base
        self.agent = base.agent
        self.raw_instruction = str(raw_instruction)
        self.output_dir = Path(output_dir)
        self.trace_path = self.output_dir / TRACE_FILENAME
        self.paired_path = self.output_dir / PAIRED_FILENAME
        self.receipt_path = self.output_dir / RECEIPT_FILENAME
        self.frames_dir = self.output_dir / "E2_V3_KEY_RGB"
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        archive = os.environ.get(RGB_ARCHIVE_ENV)
        self.rgb_archive_dir = None if not archive else Path(archive)
        if self.rgb_archive_dir is not None:
            self.rgb_archive_dir.mkdir(parents=True, exist_ok=True)
        self.rgb_archive_rows = []
        self.candidates = _load_candidates()
        self.candidate_validation = validate_candidate_set(self.candidates)
        self.thresholds = _load_thresholds()
        self.schedule = AcquisitionSchedule()
        self.detector = GroundingDinoVisualGrounder(device=os.environ.get(DEVICE_ENV, "cpu"))
        self.tracker = PersistentMultiObjectTracker()
        self.b1 = EvidenceEnabledTrackedAssociationMethodV3(use_temporal_memory=False, thresholds=self.thresholds)
        self.b2 = EvidenceEnabledTrackedAssociationMethodV3(use_temporal_memory=True, thresholds=self.thresholds)
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
            "scene_id": os.environ.get("DRIVECLARIFY_E2_V3_SCENE_ID"),
            "episode_id": os.environ.get("DRIVECLARIFY_E2_V3_EPISODE_ID"),
            "seed": os.environ.get("DRIVECLARIFY_E2_V3_ENGINEERING_SEED"),
            "ambiguity_type": os.environ.get("DRIVECLARIFY_E2_V3_AMBIGUITY_TYPE"),
            "map_name": os.environ.get("DRIVECLARIFY_E2_V3_MAP"),
            "route_identity": os.environ.get("DRIVECLARIFY_E2_V3_ROUTE_IDENTITY"),
            "commitment_certificate_sha256": os.environ.get("DRIVECLARIFY_E2_V3_COMMITMENT_CERTIFICATE_SHA256"),
        }
        if any(value in (None, "") for value in required.values()):
            raise RuntimeError("E2_V3_NATIVE_METADATA_INCOMPLETE")
        result = dict(required)
        result["seed"] = int(result["seed"])
        return result

    def _save_key_frame(self, label: str, frame_id: int, image: Any) -> None:
        if label in self.key_frames:
            return
        import cv2

        path = self.frames_dir / ("{:02d}_{}_frame_{:06d}.png".format(len(self.key_frames) + 1, label, int(frame_id)))
        cv2.imwrite(str(path), image[:, :, :3])
        row = {
            "label": label, "frame_id": int(frame_id), "path": str(path),
            "rgb_sha256": hashlib.sha256(image.tobytes(order="C")).hexdigest(),
            "stored_png_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        self.key_frames[label] = row

    def _archive_rgb_frame(self, frame_id: int, image: Any) -> None:
        """Persist every synchronized frame without knowing reveal timing."""

        if self.rgb_archive_dir is None:
            return
        import cv2

        path = self.rgb_archive_dir / "rgb_0_frame_{:06d}.png".format(int(frame_id))
        if not cv2.imwrite(str(path), image[:, :, :3]):
            raise OSError("E2_V3_RGB_ARCHIVE_WRITE_FAILED:" + str(path))
        self.rgb_archive_rows.append({
            "frame_id": int(frame_id), "path": str(path),
            "rgb_sha256": hashlib.sha256(image.tobytes(order="C")).hexdigest(),
            "stored_png_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })

    def _invoke_detector(self, image: Any, frame_id: int, observation_id: str, now_s: float, trigger: str) -> Sequence[RuntimeDetection]:
        rows = []
        invocations_this_tick = 0
        for phrase, object_class, color in _candidate_queries(self.candidates):
            if invocations_this_tick >= self.schedule.maximum_detector_invocations_per_tick:
                break
            started = time.monotonic()
            grounding = self.detector.ground(
                image, phrase, frame_id=frame_id, observation_id=observation_id,
                captured_monotonic=started,
            )
            elapsed = time.monotonic() - started
            invocation = {
                "frame_id": int(frame_id), "simulation_time_s": float(now_s),
                "source_observation_id": observation_id, "query": phrase, "trigger": trigger,
                "detector_latency_seconds": grounding.detector_latency_seconds,
                "total_call_latency_seconds": elapsed,
                "raw_proposal_count": len(grounding.raw_referents),
                "plausible_proposal_count": len(grounding.plausible_referents),
                "selected_proposal_count": len(grounding.selected_referents),
                "raw_detector_output": grounding.to_dict(),
            }
            self.detector_invocations.append(invocation)
            invocations_this_tick += 1
            # The frozen plausibility layer already selects at most K=2 by a
            # label-free apparent-size rule. Tracking every permissive raw
            # proposal creates duplicate/background lineages.
            for referent in grounding.selected_referents:
                rows.append(RuntimeDetection(
                    bbox_xyxy=tuple(float(value) for value in referent.bbox_xyxy),
                    detector_support_score=float(referent.detector_confidence),
                    object_class=object_class, color=_color_from_referent(referent, color),
                    phrase=phrase, source_frame_id=int(frame_id),
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

    def _new_invalidation_events(self, association: Mapping[str, Any], context: Mapping[str, Any]) -> Tuple[str, ...]:
        events = []
        tracker_events = self.tracker.events[self._tracker_event_index:]
        self._tracker_event_index = len(self.tracker.events)
        for row in tracker_events:
            event = str(row.get("event"))
            if event == "ID_SWITCH_CONFLICT":
                events.append("ID_SWITCH_CONFLICT")
            elif event == "DUPLICATE_TRACK_BIRTH_REJECTED":
                events.append("DUPLICATE_TRACK_AMBIGUITY")
            elif event == "TRACK_DELETED":
                events.append("LOSS_GRACE_EXPIRED")
        current_assignment = {str(key): str(value) for key, value in (association.get("selected_assignment") or {}).items()}
        if self._previous_assignment and current_assignment and self._previous_assignment != current_assignment:
            events.append("INCOMPATIBLE_REACQUISITION")
        if current_assignment:
            self._previous_assignment = current_assignment
        if self._previous_context is not None:
            if self._previous_context.get("route_version") != context.get("route_version"):
                events.append("ROUTE_CHANGED")
            if self._previous_context.get("environment_digest") != context.get("environment_digest"):
                events.append("ENVIRONMENT_CHANGED")
            if self._previous_context.get("topology_boundary_id") != context.get("topology_boundary_id"):
                events.append("TOPOLOGY_BOUNDARY_PASSED")
            if self._previous_context.get("safety_state_digest") != context.get("safety_state_digest"):
                events.append("SAFETY_STATE_CHANGED")
            if self._previous_context.get("holding_lease_id") != context.get("holding_lease_id"):
                events.append("HOLDING_LEASE_REVOKED")
        self._previous_context = dict(context)
        return tuple(dict.fromkeys(events))

    def _process_frame(
        self,
        input_data: Any,
        tick_data: Any,
        timestamp: Any,
        frame: Any,
        observation_id: Any,
    ) -> None:
        del tick_data  # The V3 observer is camera-only and must not mutate base tick state.
        sensor_frame, image = _front(input_data)
        frame_id = int(frame)
        if sensor_frame != frame_id:
            raise RuntimeError("E2_V3_SENSOR_FRAME_MISMATCH")
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
        association = associate_candidates_to_tracks(
            self.candidates, self.tracker.tracks, thresholds=self.thresholds,
            image_width=int(image.shape[1]), image_height=int(image.shape[0]),
        ) if self.tracker.tracks else _empty_association(self.candidates, "NO_TRACKS")
        if all(track.hit_count >= self.thresholds.minimum_track_hits for track in self.tracker.tracks) and self.tracker.tracks:
            self._save_key_frame("FIRST_STABLE_TRACK", frame_id, image)
        if association.get("unique_one_to_one_assignment"):
            self._save_key_frame("FIRST_UNIQUE_ASSOCIATION", frame_id, image)
        trace = {
            "schema_version": "driveclarify.e2_v3.runtime_trace.v1",
            "frame_id": frame_id, "source_observation_id": str(observation_id),
            "simulation_time_s": now_s, "detector_invoked": bool(due),
            "detector_trigger": trigger, "detector_invocation_count_total": len(self.detector_invocations),
            "detections": [copy.deepcopy(row.__dict__) for row in detections],
            "tracks": [track.to_dict() for track in self.tracker.tracks],
            "association": association,
        }
        trace["trace_digest"] = canonical_sha256(trace)
        self.trace_rows.append(trace)
        self._frame_states[frame_id] = trace
        while len(self._frame_states) > 80:
            self._frame_states.pop(sorted(self._frame_states)[0], None)

    def on_tick(self, *args: Any, **kwargs: Any) -> None:
        self.base.on_tick(*args, **kwargs)
        try:
            self._process_frame(*args, **kwargs)
        except Exception as error:
            self.errors.append({
                "stage": "E2_V3_ON_TICK", "type": type(error).__name__,
                "message": str(error), "frame": None if len(args) < 4 else args[3],
            })
            self._persist_receipt("EVIDENCE_FAIL_CLOSED")

    def _observe_history_row(self, history_row: Mapping[str, Any]) -> None:
        metadata = self._metadata()
        now_s = float(getattr(self.base, "_latest_simulation_time"))
        b0 = adapt_production_history_row(history_row, simulation_time_s=now_s, **metadata)
        frame_id = int(b0["source_frame_id"])
        state = self._frame_states.get(frame_id)
        if state is None:
            association = _empty_association(self.candidates, "NO_EXACT_FRAME_BOUND_E2_V3_STATE")
            tracker_snapshot = self.tracker.snapshot()
        else:
            association = state["association"]
            tracker_snapshot = {
                "schema_version": "driveclarify.e2_v3.tracker_snapshot.v1",
                "tracks": copy.deepcopy(state["tracks"]),
                "events": [],
                "tracks_created": self.tracker.snapshot()["tracks_created"],
                "id_switch_count": self.tracker.id_switch_count,
                "reacquisition_count": self.tracker.reacquisition_count,
                "duplicate_conflict_count": self.tracker.duplicate_conflict_count,
            }
        route_version, environment_digest = _deployable_route_context(
            self.base, str(metadata["map_name"])
        )
        topology = _topology_signal_v3(
            self.base, history_row, now_s, route_version, environment_digest
        )
        safety = _safety_signal_v3(
            self.base, frame_id=frame_id, observation_id=str(b0["source_observation_id"]),
            route_version=route_version, environment_digest=environment_digest,
        )
        if safety is None:
            safety_digest, lease_id = _safety_context(history_row)
        else:
            safety_digest, lease_id = _safety_state_digest(safety), None
        context = {
            "route_version": route_version,
            "environment_digest": environment_digest,
            "topology_boundary_id": None if topology is None else topology.get("topology_boundary_id"),
            "candidate_set_digest": self.candidate_validation["candidate_set_digest"],
            "instruction_digest": canonical_sha256(self.raw_instruction),
            "safety_state_digest": safety_digest, "holding_lease_id": lease_id,
            "actor_binding_digest": canonical_sha256(association.get("selected_assignment") or {}),
        }
        signals = {**context, "grounding": None, "topology": topology, "safety": safety}
        assert_runtime_payload_has_no_oracle(signals)
        invalidations = self._new_invalidation_events(association, context)
        before = _counter_snapshot(self.base)
        b1 = self.b1.observe(
            b0, candidates=self.candidates, association=association,
            tracker_snapshot=tracker_snapshot, runtime_signals=signals,
            history_row=history_row, invalidation_events=invalidations,
        )
        b2 = self.b2.observe(
            b0, candidates=self.candidates, association=association,
            tracker_snapshot=tracker_snapshot, runtime_signals=signals,
            history_row=history_row, invalidation_events=invalidations,
        )
        after = _counter_snapshot(self.base)
        if before != after:
            raise RuntimeError("E2_V3_OBSERVER_MUTATED_PRODUCTION_COUNTER_OR_PLANNER")
        source = {
            "source_frame_id": b0["source_frame_id"],
            "source_observation_id": b0["source_observation_id"],
            "simulation_time_s": b0["simulation_time_s"],
        }
        record = {
            "schema_version": "driveclarify.e2_v3.native_paired_views.v1",
            "episode_id": metadata["episode_id"], "scene_id": metadata["scene_id"],
            "source_identity": source, "views": {"B0": b0, "B1": b1, "B2": b2},
            "source_history_kind": (
                "CONSERVATIVE_V1_UNKNOWN_FALLBACK"
                if history_row.get("_e2_v3_conservative_fallback") is True
                else "PRODUCTION_PERSISTENT_DECISION_HISTORY"
            ),
            "association": copy.deepcopy(association),
            "tracker_snapshot": tracker_snapshot,
            "invalidation_events": list(invalidations),
            "fairness_counters_before": before, "fairness_counters_after": after,
        }
        record["record_digest"] = canonical_sha256(record)
        self.paired_rows.append(record)
        self._observed_source_frames.add(frame_id)
        if history_row.get("_e2_v3_conservative_fallback") is True:
            self._fallback_paired_row_count += 1
        image = self._latest_images.get(frame_id)
        if image is not None and (
            b1["evidence_vector"]["E2_GROUNDING"]["status"] == "AVAILABLE"
            or b2["evidence_vector"]["E2_GROUNDING"]["status"] == "AVAILABLE"
        ):
            self._save_key_frame("FIRST_E2_AVAILABLE", frame_id, image)

    def _consume_new_history(self) -> None:
        history = getattr(self.base, "_persistent_decision_history", ())
        while self._processed_history_count < len(history):
            row = history[self._processed_history_count]
            self._processed_history_count += 1
            try:
                self._observe_history_row(row)
            except Exception as error:
                self.errors.append({
                    "stage": "E2_V3_HISTORY_OBSERVATION",
                    "history_index": self._processed_history_count - 1,
                    "type": type(error).__name__, "message": str(error),
                })

    def prepare_model_input(self, model_input: Any) -> Any:
        return self.base.prepare_model_input(model_input)

    def on_model_output(self, *args: Any, **kwargs: Any) -> None:
        self.base.on_model_output(*args, **kwargs)
        self._consume_new_history()
        frame_id = getattr(self.base, "_latest_frame", None)
        observation_id = getattr(self.base, "_latest_observation_id", None)
        if frame_id is not None and observation_id and int(frame_id) not in self._observed_source_frames:
            try:
                self._observe_history_row(
                    _conservative_v1_history_row(int(frame_id), str(observation_id))
                )
            except Exception as error:
                self.errors.append({
                    "stage": "E2_V3_CONSERVATIVE_HISTORY_OBSERVATION",
                    "frame": int(frame_id), "type": type(error).__name__,
                    "message": str(error),
                })

    def select_plan_source(self, *args: Any, **kwargs: Any) -> Tuple[Any, Any]:
        return self.base.select_plan_source(*args, **kwargs)

    def on_pid_invocation(self, *args: Any, **kwargs: Any) -> None:
        self.base.on_pid_invocation(*args, **kwargs)

    def on_control(self, *args: Any, **kwargs: Any) -> None:
        self.base.on_control(*args, **kwargs)

    def commit(self) -> None:
        self.base.commit()

    def _persist_receipt(self, status: str) -> None:
        receipt = {
            "schema_version": "driveclarify.e2_v3.native_runtime_receipt.v1",
            "status": status, "method_id": "E2_TRACKED_ASSOCIATION_V3",
            "historical_method": "E2_V2_RAW_DETECTOR_SCORE_METHOD=CLOSED_NOT_QUALIFIED",
            "feature_flag": FEATURE_FLAG, "feature_flag_default": "OFF",
            "base_runtime_type": type(self.base).__module__ + "." + type(self.base).__qualname__,
            "candidate_validation": self.candidate_validation,
            "candidate_specs": [row.to_dict() for row in self.candidates],
            "acquisition_schedule": self.schedule.to_dict(),
            "thresholds": self.thresholds.to_dict(),
            "threshold_provenance": os.environ.get("DRIVECLARIFY_E2_V3_THRESHOLD_PROVENANCE", "PRECALIBRATION_DEVELOPMENT_GRID_MEMBER"),
            "detector_invocation_count": len(self.detector_invocations),
            "detector_invocations": copy.deepcopy(self.detector_invocations),
            "duplicate_detection_suppression_count": len(self.duplicate_detections_suppressed),
            "duplicate_detections_suppressed": copy.deepcopy(self.duplicate_detections_suppressed),
            "grounding_detector_compute_seconds_total": sum(float(row["total_call_latency_seconds"]) for row in self.detector_invocations),
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
        }
        receipt["receipt_digest"] = canonical_sha256(receipt)
        _atomic_json(self.receipt_path, receipt)

    def close(self) -> None:
        try:
            self._consume_new_history()
            if self._latest_images:
                frame_id = max(self._latest_images)
                self._save_key_frame("FINAL_FRAME", frame_id, self._latest_images[frame_id])
            self.base.close()
        finally:
            _atomic_jsonl(self.trace_path, self.trace_rows)
            _atomic_jsonl(self.paired_path, self.paired_rows)
            self._persist_receipt("CLOSED" if not self.errors else "EVIDENCE_FAIL_CLOSED")

    def summary(self) -> Mapping[str, Any]:
        value = dict(self.base.summary()) if hasattr(self.base, "summary") else {}
        value["e2_tracked_association_v3"] = {
            "enabled": True, "detector_invocations": len(self.detector_invocations),
            "tracks_created": self.tracker.snapshot()["tracks_created"],
            "paired_rows": len(self.paired_rows), "errors": len(self.errors),
        }
        return value

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)


def build_e2_v3_native_runtime(agent: Any, raw_instruction: str, output_dir: str) -> Any:
    if not _truthy(os.environ.get(FEATURE_FLAG)):
        raise RuntimeError("E2_V3_NATIVE_FEATURE_FLAG_OFF")
    from driveclarify_persistent_ambiguity_runtime_v1.runtime import NativeCarlaRouteLocalEvidenceProvider, build_persistent_ambiguity_runtime

    provider = NativeCarlaRouteLocalEvidenceProvider.from_native_agent(agent)
    base = build_persistent_ambiguity_runtime(
        agent, str(raw_instruction), str(output_dir), route_local_evidence_provider=provider,
    )
    if hasattr(base, "_defer_full_receipt_io"):
        base._defer_full_receipt_io = True
        base._force_receipt_persist = False
    base._receipt.update({
        "e2_v3_native_factory": "driveclarify_rq2_t_e2_v3.runtime.build_e2_v3_native_runtime",
        "e2_v3_native_flag": FEATURE_FLAG, "e2_v3_native_flag_default": "OFF",
        "e2_v3_observational_only": True,
    })
    base._persist()
    return E2V3NativeEvidenceRuntime(base, output_dir, raw_instruction)


__all__ = ["E2V3NativeEvidenceRuntime", "FEATURE_FLAG", "build_e2_v3_native_runtime"]
