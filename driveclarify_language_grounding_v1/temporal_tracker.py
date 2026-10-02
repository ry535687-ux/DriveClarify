"""Fail-closed gate for temporal cases; ByteTrack is not used by the V1 static pilot."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from .contracts import AmbiguityKind, AmbiguityStatus, ParsedSlots, SCHEMA_VERSION


class TemporalEventState(str, Enum):
    APPROACHING = "APPROACHING"
    OCCUPYING = "OCCUPYING"
    CLEARING = "CLEARING"
    CLEARED = "CLEARED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class TemporalTrackingDecision:
    status: AmbiguityStatus
    tracker_required: bool
    tracker_enabled: bool
    track_id: Optional[str]
    event_state: TemporalEventState
    reason_codes: Tuple[str, ...]
    schema_version: str = SCHEMA_VERSION


class TemporalTrackingGate:
    """Route temporal instructions to tracking without guessing an event state."""

    def evaluate(
        self, parsed: ParsedSlots, *, tracker_enabled: bool = False
    ) -> TemporalTrackingDecision:
        if parsed.ambiguity_kind is not AmbiguityKind.TEMPORAL:
            return TemporalTrackingDecision(
                status=AmbiguityStatus.UNKNOWN,
                tracker_required=False,
                tracker_enabled=False,
                track_id=None,
                event_state=TemporalEventState.UNKNOWN,
                reason_codes=("TEMPORAL_TRACKING_NOT_REQUIRED",),
            )
        if not tracker_enabled:
            return TemporalTrackingDecision(
                status=AmbiguityStatus.TRACK_ID_UNCERTAIN,
                tracker_required=True,
                tracker_enabled=False,
                track_id=None,
                event_state=TemporalEventState.UNKNOWN,
                reason_codes=("BLOCKED_TEMPORAL_REFERENT_TRACKING", "BYTETRACK_NOT_ENABLED"),
            )
        # The gate cannot manufacture tracks or semantic events.  A concrete
        # image-space tracker/event estimator must supply both in a later pilot.
        return TemporalTrackingDecision(
            status=AmbiguityStatus.TRACK_ID_UNCERTAIN,
            tracker_required=True,
            tracker_enabled=True,
            track_id=None,
            event_state=TemporalEventState.UNKNOWN,
            reason_codes=("TRACKER_ADAPTER_PRESENT_BUT_NO_TRACK_OBSERVATION",),
        )


__all__ = ["TemporalEventState", "TemporalTrackingDecision", "TemporalTrackingGate"]
