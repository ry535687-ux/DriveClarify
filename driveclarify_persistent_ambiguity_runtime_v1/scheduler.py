"""Fresh K-way bundle for every unresolved normal-planning observation.

The scheduler owns accounting and atomic publication only.  It has no sensor
tick loop, planner advance, PID, control writer, or visualization callback.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence, Tuple

from .contracts import canonical_sha256


@dataclass(frozen=True)
class RefreshCandidate:
    candidate_id: str
    interpretation_id: str
    semantic_sha256: str
    target_obligation_digest: str
    payload: Any


@dataclass(frozen=True)
class CandidatePlanEvidence:
    candidate_id: str
    interpretation_id: str
    source_observation_id: str
    source_frame_id: Any
    route_digest: str
    speed_digest: str
    plan_reference_digest: str
    result: Any
    forward_start_monotonic_s: Optional[float] = None
    forward_end_monotonic_s: Optional[float] = None
    forward_latency_s: Optional[float] = None


@dataclass(frozen=True)
class CandidateForwardFailure:
    """Why one candidate forward did not produce evidence.

    Recorded so a partial bundle can be adjudicated as an engineering exception
    versus a scientific outcome.  Evidence only: the scheduler's accounting and
    atomicity are unchanged by its presence.
    """

    candidate_id: str
    interpretation_id: str
    error_type: str
    reason_code: str
    detail: str
    forward_start_monotonic_s: Optional[float] = None
    forward_end_monotonic_s: Optional[float] = None
    forward_latency_s: Optional[float] = None


@dataclass(frozen=True)
class CandidateEvidenceRefreshBundle:
    bundle_id: str
    normal_planning_event_id: str
    source_observation_id: str
    source_frame_id: Any
    candidate_set_digest: str
    requested_candidate_ids: Tuple[str, ...]
    evidence: Tuple[CandidatePlanEvidence, ...]
    requested_count: int
    attempted_count: int
    executed_count: int
    succeeded_count: int
    skipped_count: int
    failed_count: int
    normal_forward_count: int
    detector_forward_count: int
    visualization_extra_forward_count: int
    new_planner_advance_count: int
    new_pid_count: int
    control_write_count: int
    latency_seconds: float
    complete: bool
    reason_code: str
    # One row per failed candidate forward.  Defaulted so every existing
    # construction site and every frozen field order stays valid.
    failures: Tuple[CandidateForwardFailure, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "normal_planning_event_id": self.normal_planning_event_id,
            "source_observation_id": self.source_observation_id,
            "source_frame_id": self.source_frame_id,
            "candidate_set_digest": self.candidate_set_digest,
            "requested_candidate_ids": list(self.requested_candidate_ids),
            "evidence": [
                {
                    "candidate_id": row.candidate_id,
                    "interpretation_id": row.interpretation_id,
                    "source_observation_id": row.source_observation_id,
                    "source_frame_id": row.source_frame_id,
                    "route_digest": row.route_digest,
                    "speed_digest": row.speed_digest,
                    "plan_reference_digest": row.plan_reference_digest,
                    "forward_start_monotonic_s": row.forward_start_monotonic_s,
                    "forward_end_monotonic_s": row.forward_end_monotonic_s,
                    "forward_latency_s": row.forward_latency_s,
                }
                for row in self.evidence
            ],
            "requested_count": self.requested_count,
            "attempted_count": self.attempted_count,
            "executed_count": self.executed_count,
            "succeeded_count": self.succeeded_count,
            "skipped_count": self.skipped_count,
            "failed_count": self.failed_count,
            "normal_forward_count": self.normal_forward_count,
            "detector_forward_count": self.detector_forward_count,
            "visualization_extra_forward_count": self.visualization_extra_forward_count,
            "new_planner_advance_count": self.new_planner_advance_count,
            "new_pid_count": self.new_pid_count,
            "control_write_count": self.control_write_count,
            "latency_seconds": self.latency_seconds,
            "complete": self.complete,
            "reason_code": self.reason_code,
            "failures": [
                {
                    "candidate_id": row.candidate_id,
                    "interpretation_id": row.interpretation_id,
                    "error_type": row.error_type,
                    "reason_code": row.reason_code,
                    "detail": row.detail,
                }
                for row in self.failures
            ],
        }


class CandidateEvidenceRefreshScheduler:
    """Run every active semantic candidate exactly once in one P1 event."""

    def __init__(self) -> None:
        self._published_event_ids: set[str] = set()

    def refresh(
        self,
        *,
        normal_planning_event_id: str,
        source_observation_id: str,
        source_frame_id: Any,
        candidates: Sequence[RefreshCandidate],
        forward: Callable[[RefreshCandidate, str], Any],
        route_digest: Callable[[Any], Optional[str]],
        speed_digest: Callable[[Any], Optional[str]],
        max_candidate_forwards: Optional[int] = None,
        detector_forward_count: int = 0,
    ) -> CandidateEvidenceRefreshBundle:
        if normal_planning_event_id in self._published_event_ids:
            raise ValueError("NORMAL_PLANNING_EVENT_ALREADY_PUBLISHED")
        if not normal_planning_event_id or not source_observation_id:
            raise ValueError("REFRESH_SOURCE_IDENTITY_MISSING")
        identifiers = tuple(row.candidate_id for row in candidates)
        if len(identifiers) < 2 or len(set(identifiers)) != len(identifiers):
            raise ValueError("REFRESH_REQUIRES_DISTINCT_K2")
        if detector_forward_count not in (0, 1):
            raise ValueError("DETECTOR_FORWARD_ACCOUNTING_INVALID")
        budget = len(candidates) if max_candidate_forwards is None else max(0, int(max_candidate_forwards))
        started = time.monotonic()
        evidence = []
        failures: list[CandidateForwardFailure] = []
        executed = 0
        failed = 0
        skipped = 0
        for index, candidate in enumerate(candidates):
            if index >= budget:
                skipped += 1
                continue
            forward_started = time.monotonic()
            try:
                result = forward(candidate, "PERSISTENT-{}".format(candidate.candidate_id))
                forward_ended = time.monotonic()
                executed += 1
                route_hash = route_digest(result)
                speed_hash = speed_digest(result)
                if not route_hash or not speed_hash:
                    raise ValueError("CANDIDATE_PLAN_DIGEST_UNAVAILABLE")
                evidence.append(
                    CandidatePlanEvidence(
                        candidate_id=candidate.candidate_id,
                        interpretation_id=candidate.interpretation_id,
                        source_observation_id=str(source_observation_id),
                        source_frame_id=source_frame_id,
                        route_digest=route_hash,
                        speed_digest=speed_hash,
                        plan_reference_digest=canonical_sha256(
                            {
                                "candidate_id": candidate.candidate_id,
                                "interpretation_id": candidate.interpretation_id,
                                "source_observation_id": source_observation_id,
                                "source_frame_id": source_frame_id,
                                "route_digest": route_hash,
                                "speed_digest": speed_hash,
                            }
                        ),
                        result=result,
                        forward_start_monotonic_s=forward_started,
                        forward_end_monotonic_s=forward_ended,
                        forward_latency_s=forward_ended - forward_started,
                    )
                )
            except Exception as error:
                # Still counted as one failure and still non-fatal, but no longer
                # discarded: without the reason a partial bundle cannot be
                # adjudicated as an engineering exception versus a scientific
                # outcome, and the cause is unrecoverable after the fact.
                forward_ended = time.monotonic()
                failed += 1
                failures.append(
                    CandidateForwardFailure(
                        candidate_id=candidate.candidate_id,
                        interpretation_id=candidate.interpretation_id,
                        error_type=type(error).__name__,
                        reason_code=(
                            str(error.args[0])
                            if error.args and isinstance(error.args[0], str)
                            else type(error).__name__
                        ),
                        detail=str(error)[:500],
                        forward_start_monotonic_s=forward_started,
                        forward_end_monotonic_s=forward_ended,
                        forward_latency_s=forward_ended - forward_started,
                    )
                )
        complete = len(evidence) == len(candidates) and not failed and not skipped
        reason = "REFRESH_BUNDLE_COMPLETE_VALID" if complete else "REFRESH_BUNDLE_PARTIAL"
        member_rows = [
            {
                "candidate_id": row.candidate_id,
                "interpretation_id": row.interpretation_id,
                "semantic_sha256": row.semantic_sha256,
                "target_obligation_digest": row.target_obligation_digest,
            }
            for row in candidates
        ]
        identity = {
            "normal_planning_event_id": normal_planning_event_id,
            "source_observation_id": source_observation_id,
            "source_frame_id": source_frame_id,
            "members": member_rows,
            "evidence": [row.plan_reference_digest for row in evidence],
        }
        bundle = CandidateEvidenceRefreshBundle(
            bundle_id="refresh-bundle-" + canonical_sha256(identity)[:24],
            normal_planning_event_id=normal_planning_event_id,
            source_observation_id=str(source_observation_id),
            source_frame_id=source_frame_id,
            candidate_set_digest=canonical_sha256(member_rows),
            requested_candidate_ids=identifiers,
            evidence=tuple(evidence),
            requested_count=len(candidates),
            attempted_count=min(len(candidates), budget),
            executed_count=executed,
            succeeded_count=len(evidence),
            skipped_count=skipped,
            failed_count=failed,
            normal_forward_count=1,
            detector_forward_count=detector_forward_count,
            visualization_extra_forward_count=0,
            new_planner_advance_count=0,
            new_pid_count=0,
            control_write_count=0,
            latency_seconds=time.monotonic() - started,
            complete=complete,
            reason_code=reason,
            failures=tuple(failures),
        )
        self._published_event_ids.add(normal_planning_event_id)
        return bundle


__all__ = [
    "CandidateEvidenceRefreshBundle",
    "CandidateEvidenceRefreshScheduler",
    "CandidatePlanEvidence",
    "RefreshCandidate",
]
