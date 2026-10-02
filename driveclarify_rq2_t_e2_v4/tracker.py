"""V4 tracker maturity measured only in detector acquisition opportunities."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from driveclarify_rq2_t_e2_v3.tracker import (
    PersistentMultiObjectTracker,
    RuntimeDetection,
    TrackState,
    iou,
)


class AcquisitionClockTracker(PersistentMultiObjectTracker):
    implementation_id = "E2_V4_DETERMINISTIC_BYTETRACK_ACQUISITION_CLOCK_V1"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.acquisition_opportunity_index = 0

    def update(
        self,
        detections: Sequence[RuntimeDetection],
        *,
        frame_id: int,
        simulation_time_s: float,
    ) -> tuple[TrackState, ...]:
        self.acquisition_opportunity_index += 1
        tracks = super().update(detections, frame_id=frame_id, simulation_time_s=simulation_time_s)
        for track in tracks:
            if not hasattr(track, "created_acquisition_index"):
                track.created_acquisition_index = self.acquisition_opportunity_index
            track.latest_acquisition_index = self.acquisition_opportunity_index
            track.age_acquisitions = (
                self.acquisition_opportunity_index - int(track.created_acquisition_index) + 1
            )
        return tracks

    def snapshot(self) -> Mapping[str, Any]:
        value = dict(super().snapshot())
        by_id = {track.track_id: track for track in self.tracks}
        rows = []
        for row in value["tracks"]:
            track = by_id[str(row["track_id"])]
            row = dict(row)
            row.update({
                "created_acquisition_index": int(getattr(track, "created_acquisition_index", 0)),
                "latest_acquisition_index": int(getattr(track, "latest_acquisition_index", 0)),
                "age_acquisitions": int(getattr(track, "age_acquisitions", 0)),
                "maturity_clock_domain": "DETECTOR_ACQUISITION_OPPORTUNITIES",
                "native_simulator_age_frames_diagnostic_only": int(track.age_frames),
            })
            rows.append(row)
        value.update({
            "schema_version": "driveclarify.e2_v4.tracker_snapshot.v1",
            "implementation_id": self.implementation_id,
            "tracks": rows,
            "acquisition_opportunity_index": self.acquisition_opportunity_index,
            "maturity_clock_domain": "DETECTOR_ACQUISITION_OPPORTUNITIES",
        })
        return value


__all__ = ["AcquisitionClockTracker", "RuntimeDetection", "TrackState", "iou"]
