#!/usr/bin/env python3
"""Replay captured real rgb_0 frames through the lightweight temporal stack."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from driveclarify_temporal_grounding_v1.contracts import (  # noqa: E402
    EventState,
    RouteTarget,
    TrackLifecycleState,
    TrackObservation,
)
from driveclarify_temporal_grounding_v1.event_estimator import (  # noqa: E402
    TemporalEventEstimator,
)
from driveclarify_temporal_grounding_v1.target_binding import (  # noqa: E402
    RuntimeRouteTargetBinder,
)
from driveclarify_temporal_grounding_v1.tracker import (  # noqa: E402
    ByteTrackAdapter,
    Detection,
    ImageMotionProposal,
)


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def _negative_controls(primary: TrackObservation, region: Any) -> Mapping[str, Any]:
    estimator = TemporalEventEstimator()

    def at(frame: int, state: TrackLifecycleState) -> TrackObservation:
        return TrackObservation(
            track_id=primary.track_id,
            source_grounding_id=primary.source_grounding_id,
            phrase=primary.phrase,
            bbox_xyxy=primary.bbox_xyxy,
            frame_id=frame,
            simulation_time=frame * 0.05,
            age_frames=frame,
            time_since_seen_frames=0 if state is TrackLifecycleState.ACTIVE else 1,
            confidence=primary.confidence,
            velocity_proxy_xy=(0.0, 0.0),
            lifecycle_state=state,
            freshness_frames=0 if state is TrackLifecycleState.ACTIVE else 1,
            association_source="T0_REAL_BOX_LIFECYCLE_INJECTION",
            association_iou=1.0,
        )

    estimator.update(at(1, TrackLifecycleState.ACTIVE), region)
    occupied = estimator.update(at(2, TrackLifecycleState.ACTIVE), region)
    missed = estimator.update(at(3, TrackLifecycleState.TEMPORARILY_OCCLUDED), region)
    lost = estimator.update(at(4, TrackLifecycleState.LOST), region)
    return {
        "source_geometry": "INITIAL_REAL_GROUNDING_DINO_BUS_BOX",
        "occupied_state": occupied.state.value,
        "temporary_miss_state": missed.state.value,
        "temporary_miss_cleared": missed.state is EventState.CLEARED,
        "lost_state": lost.state.value,
        "lost_cleared": lost.state is EventState.CLEARED,
        "track_lost_rule": "TRACK_LOST != CLEARED",
        "pass": (
            occupied.state is EventState.OCCUPYING_RELEVANT_REGION
            and missed.state is not EventState.CLEARED
            and lost.state is EventState.TRACK_LOST
        ),
    }


def replay(run_dir: Path) -> Mapping[str, Any]:
    import cv2

    run_dir = run_dir.resolve()
    receipt_path = run_dir / "TEMPORAL_GROUNDING_V1_LIVE_RECEIPT.json"
    source = json.loads(receipt_path.read_text(encoding="utf-8"))
    grounding = source["initial_grounding"]
    selected = sorted(
        grounding["selected_referents"],
        key=lambda item: (-float(item["detector_confidence"]), item["local_object_id"]),
    )
    initial = selected[0]
    target_payload = dict(source["target_binding"])
    target_payload["anchor_xy"] = tuple(target_payload["anchor_xy"])
    target = RouteTarget(**target_payload)
    region = RuntimeRouteTargetBinder.event_region(
        target,
        image_width=int(grounding["image_width"]),
        image_height=int(grounding["image_height"]),
    )
    frame_rows = list(source["rgb_identities"])
    paths = [Path(item["path"]) for item in frame_rows]
    if not paths or any(not path.is_file() for path in paths):
        raise RuntimeError("T0_REAL_RGB_SEQUENCE_INCOMPLETE")
    digest_mismatches = []
    for path, row in zip(paths, frame_rows):
        decoded = cv2.imread(str(path), cv2.IMREAD_COLOR)
        # CARLA rgb_0 is BGRA with an opaque alpha plane; the PNG stores BGR.
        import numpy as np

        reconstructed = np.concatenate(
            [
                decoded,
                np.full((*decoded.shape[:2], 1), 255, dtype=np.uint8),
            ],
            axis=2,
        )
        decoded_sha = hashlib.sha256(reconstructed.tobytes(order="C")).hexdigest()
        if decoded_sha != row["rgb_sha256"]:
            digest_mismatches.append(str(path))
    if digest_mismatches:
        raise RuntimeError("T0_REAL_RGB_DIGEST_MISMATCH")

    tracker = ByteTrackAdapter()
    motion = ImageMotionProposal()
    estimator = TemporalEventEstimator()
    first_image = cv2.imread(str(paths[0]), cv2.IMREAD_COLOR)
    first_frame = int(frame_rows[0]["frame_id"])
    tracks = tracker.update(
        [
            Detection(
                bbox_xyxy=tuple(initial["bbox_xyxy"]),
                confidence=float(initial["detector_confidence"]),
                phrase="bus",
                source_grounding_id=str(initial["local_object_id"]),
                source="GROUNDING_DINO_INITIAL_REPLAY_INPUT",
            )
        ],
        frame_id=first_frame,
        simulation_time=float(frame_rows[0]["simulation_time"]),
    )
    motion.initialize(first_image, tracks)
    primary_id = tracks[0].track_id
    initial_track = tracks[0]
    transitions = []
    lifecycle = []
    last_state = None
    event = None
    primary = tracks[0]
    for index, (path, row) in enumerate(zip(paths, frame_rows)):
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if index:
            tracks = tracker.update(
                motion.propose(image, tracks),
                frame_id=int(row["frame_id"]),
                simulation_time=float(row["simulation_time"]),
            )
        primary = next((item for item in tracks if item.track_id == primary_id), None)
        if primary is None:
            break
        event = estimator.update(primary, region)
        lifecycle.append(primary.to_dict())
        if event.state is not last_state:
            transitions.append(event.to_dict())
            last_state = event.state
        if event.state is EventState.CLEARED:
            break

    negatives = _negative_controls(initial_track, region)
    passed = bool(
        event is not None
        and event.state is EventState.CLEARED
        and event.track_id == primary_id
        and event.outside_persistence_frames >= 3
        and tracker.id_switch_count == 0
        and tracker.track_loss_count == 0
        and negatives["pass"]
    )
    result = {
        "schema_version": "driveclarify.temporal_grounding_v1.real_rgb_replay.v1",
        "status": "PASS_T0_REAL_RGB_TEMPORAL_REPLAY" if passed else "BLOCKED_T0_REAL_RGB_TEMPORAL_REPLAY",
        "observed_at_utc": _utc_now(),
        "source_run_dir": str(run_dir),
        "source_receipt_sha256": _sha(receipt_path),
        "source_frame_count": len(frame_rows),
        "processed_frame_count": len(lifecycle),
        "real_rgb_digest_mismatch_count": len(digest_mismatches),
        "detector_forward_count_during_replay": 0,
        "source_detector_invocation_count": source.get("detector_invocation_count"),
        "source_detector_latency_seconds": source.get("detector_latencies_seconds"),
        "primary_track_id": primary_id,
        "track_id_switch_count": tracker.id_switch_count,
        "track_loss_count_before_clear": tracker.track_loss_count,
        "tracker_implementation": tracker.implementation_id,
        "proposal_implementation": motion.implementation_id,
        "event_estimator_implementation": estimator.implementation_id,
        "event_region": region.to_dict(),
        "transition_timeline": transitions,
        "final_event": None if event is None else event.to_dict(),
        "negative_controls": negatives,
        "privileged_state_policy_read_count": 0,
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }
    _atomic_json(run_dir / "TEMPORAL_GROUNDING_V1_REPLAY_AUDIT.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(replay(args.run_dir), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
