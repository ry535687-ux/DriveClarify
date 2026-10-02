"""Immutable persistent-ambiguity episode reducer.

The store retains semantic obligations and audit provenance.  It deliberately
has no route tensor, planner, PID, authority resolver, or VehicleControl API.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Mapping, Optional, Tuple

from .contracts import canonical_sha256


class StrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class SemanticState(StrEnum):
    DETECTED = "DETECTED"
    GROUNDED = "GROUNDED"
    UNRESOLVED = "UNRESOLVED"
    RESOLVED = "RESOLVED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"


class ConsequenceState(StrEnum):
    UNKNOWN = "UNKNOWN"
    CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT = "CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT"
    CURRENTLY_DIVERGENT = "CURRENTLY_DIVERGENT"
    NO_MATERIAL_DIVERGENCE = "NO_MATERIAL_DIVERGENCE"
    CURRENT_ACTION_SHARED_FUTURE_UNKNOWN = "CURRENT_ACTION_SHARED_FUTURE_UNKNOWN"


class QueryState(StrEnum):
    NONE = "NONE"
    REQUIRED = "REQUIRED"
    ACTIVE = "ACTIVE"
    ANSWER_RECEIVED = "ANSWER_RECEIVED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


class EvidenceState(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    REFRESH_REQUIRED = "REFRESH_REQUIRED"
    REFRESHING = "REFRESHING"
    INVALID = "INVALID"


class CandidateStatus(StrEnum):
    ACTIVE_UNRESOLVED = "ACTIVE_UNRESOLVED"
    RESOLVED_SELECTED = "RESOLVED_SELECTED"
    IRRELEVANT_CONVERGED = "IRRELEVANT_CONVERGED"
    REJECTED_BY_EVIDENCE = "REJECTED_BY_EVIDENCE"
    EXPIRED = "EXPIRED"
    INVALID = "INVALID"


@dataclass(frozen=True)
class CandidateIdentity:
    candidate_id: str
    interpretation_id: str
    semantic_sha256: str
    referent_lineage_id: str
    referent_description: str
    target_obligation_id: str
    target_obligation_digest: str
    topology_target_id: Optional[str]
    topology_junction_id: Optional[str]
    topology_branch_id: Optional[str]
    topology_route_order: Optional[int]
    source_observation_id: str
    source_frame_id: Any
    obligation_type: str
    maneuver: str
    event_relation: Optional[str]
    semantic_target_id: str
    referent_track_id: Optional[str] = None
    referent_identity_status: str = "SUPPORTED_CURRENT"
    status: CandidateStatus = CandidateStatus.ACTIVE_UNRESOLVED
    provenance: Tuple[str, ...] = ()


@dataclass(frozen=True)
class FreshnessMetadata:
    latest_source_observation_id: str
    latest_source_frame_id: Any
    last_refresh_monotonic_time: Optional[float]
    freshness_deadline_monotonic: Optional[float]
    expiry_monotonic_time: Optional[float]
    route_version: Optional[str]
    environment_digest: Optional[str]


@dataclass(frozen=True)
class ComputeAccounting:
    normal_planning_event_count: int = 0
    normal_forward_count: int = 0
    candidate_forward_requested_count: int = 0
    candidate_forward_attempted_count: int = 0
    candidate_forward_count: int = 0
    candidate_forward_succeeded_count: int = 0
    detector_forward_count: int = 0
    candidate_forward_skipped_count: int = 0
    candidate_forward_failed_count: int = 0
    visualization_extra_forward_count: int = 0
    new_planner_advance_count: int = 0
    new_pid_count: int = 0
    control_write_count: int = 0


@dataclass(frozen=True)
class SharedActionReference:
    action_class: str
    shared_action_window_id: str
    source_observation_id: str
    source_frame_id: Any
    plan_reference_digest: str
    valid_until_monotonic: float
    active_member_ids: Tuple[str, ...]
    preserve_unresolved_semantics: bool = True


@dataclass(frozen=True)
class QueryRecord:
    query_id: str
    candidate_set_id: str
    issued_monotonic_time: float
    answer_deadline_monotonic: float
    question_digest: str
    status: QueryState
    answer_digest: Optional[str] = None
    lifecycle_owner: str = "WALL_DEADLINE_V1"


@dataclass(frozen=True)
class HistoryEvent:
    sequence: int
    event_id: str
    event_type: str
    observed_monotonic_time: float
    source_frame_id: Any
    reason_code: str
    previous_audit_digest: Optional[str]
    event_digest: str

    def to_dict(self) -> dict:
        return {
            "sequence": self.sequence,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "observed_monotonic_time": self.observed_monotonic_time,
            "source_frame_id": self.source_frame_id,
            "reason_code": self.reason_code,
            "previous_audit_digest": self.previous_audit_digest,
            "event_digest": self.event_digest,
        }


@dataclass(frozen=True)
class PersistentAmbiguityEpisode:
    episode_id: str
    instruction_id: str
    raw_instruction: str
    instruction_sha256: str
    created_monotonic_time: float
    updated_monotonic_time: float
    first_source_observation_id: str
    original_candidate_set_id: str
    candidate_set_id: str
    candidates: Tuple[CandidateIdentity, ...]
    unresolved_slots: Tuple[str, ...]
    semantic_state: SemanticState
    consequence_state: ConsequenceState
    query_state: QueryState
    evidence_state: EvidenceState
    current_candidate_relationship: str
    current_shared_executable_action: Optional[SharedActionReference]
    future_divergence_evidence_digest: Optional[str]
    decision_window_evidence_digest: Optional[str]
    decision_deadline_evidence_digest: Optional[str]
    freshness: FreshnessMetadata
    active_query: Optional[QueryRecord]
    query_history: Tuple[QueryRecord, ...]
    invalidation_history: Tuple[str, ...]
    reason_codes: Tuple[str, ...]
    compute_accounting: ComputeAccounting
    provenance: Tuple[str, ...]
    history: Tuple[HistoryEvent, ...]
    audit_digest: str

    @property
    def active_candidate_ids(self) -> Tuple[str, ...]:
        return tuple(
            candidate.candidate_id
            for candidate in self.candidates
            if candidate.status is CandidateStatus.ACTIVE_UNRESOLVED
        )

    @staticmethod
    def _evidence_reference(
        evidence_id: str,
        digest: Optional[str],
        *,
        status: str,
        source_observation_id: str,
        source_frame_id: Any,
        observed_monotonic_time: float,
    ) -> Optional[dict[str, Any]]:
        if digest is None:
            return None
        return {
            "evidence_id": evidence_id,
            "evidence_sha256": digest,
            "status": status,
            "source_observation_id": source_observation_id,
            "source_frame_id": source_frame_id,
            "observed_monotonic_time": float(observed_monotonic_time),
        }

    def to_contract_dict(self) -> dict[str, Any]:
        """Project the internal reducer state onto the frozen lifecycle schema.

        Internal bookkeeping intentionally has a richer shape.  Runtime receipts
        expose this explicit, lossless semantic projection as the authoritative
        contract value instead of serializing implementation dataclasses.
        """

        source_observation_id = self.freshness.latest_source_observation_id
        source_frame_id = self.freshness.latest_source_frame_id
        observed_at = (
            self.freshness.last_refresh_monotonic_time
            if self.freshness.last_refresh_monotonic_time is not None
            else self.updated_monotonic_time
        )
        evidence_status = (
            "AVAILABLE"
            if self.evidence_state is EvidenceState.FRESH
            else "STALE"
            if self.evidence_state in (EvidenceState.STALE, EvidenceState.REFRESH_REQUIRED)
            else "INVALID"
            if self.evidence_state is EvidenceState.INVALID
            else "UNKNOWN"
        )
        candidates = []
        for row in self.candidates:
            topology = None
            if all(
                value is not None
                for value in (
                    row.topology_target_id,
                    row.topology_junction_id,
                    row.topology_branch_id,
                    row.topology_route_order,
                    self.freshness.route_version,
                )
            ):
                topology_payload = {
                    "target_id": row.topology_target_id,
                    "junction_id": row.topology_junction_id,
                    "branch_id": row.topology_branch_id,
                    "route_order_index": row.topology_route_order,
                    "route_version_id": self.freshness.route_version,
                    "binding_status": (
                        "AVAILABLE"
                        if self.evidence_state in (EvidenceState.FRESH, EvidenceState.REFRESHING)
                        else "STALE"
                        if self.evidence_state in (EvidenceState.STALE, EvidenceState.REFRESH_REQUIRED)
                        else "INVALID"
                    ),
                }
                topology = {
                    **topology_payload,
                    "binding_digest": canonical_sha256(topology_payload),
                }
            identity_status = row.referent_identity_status
            if any(reason == "REFERENT_IDENTITY_LOST" for reason in self.invalidation_history):
                identity_status = "LOST"
            elif self.evidence_state in (EvidenceState.STALE, EvidenceState.INVALID):
                identity_status = "STALE"
            candidates.append(
                {
                    "candidate_id": row.candidate_id,
                    "interpretation_id": row.interpretation_id,
                    "semantic_sha256": row.semantic_sha256,
                    "referent_lineage": {
                        "lineage_id": row.referent_lineage_id,
                        "semantic_description": row.referent_description,
                        "origin_observation_id": row.source_observation_id,
                        "origin_frame_id": row.source_frame_id,
                        "track_id": row.referent_track_id,
                        "identity_status": identity_status,
                    },
                    "target_obligation": {
                        "obligation_id": row.target_obligation_id,
                        "obligation_type": row.obligation_type,
                        "maneuver": row.maneuver,
                        "event_relation": row.event_relation,
                        "semantic_target_id": row.semantic_target_id,
                        "obligation_digest": row.target_obligation_digest,
                    },
                    "topology_binding": topology,
                    "status": row.status.value,
                    "source_observation_id": row.source_observation_id,
                    "source_frame_id": row.source_frame_id,
                    "evidence_provenance": list(row.provenance),
                }
            )
        action = self.current_shared_executable_action
        action_value = None if action is None else {
            "action_class": action.action_class,
            "shared_action_window_id": action.shared_action_window_id,
            "source_observation_id": action.source_observation_id,
            "source_frame_id": action.source_frame_id,
            "plan_reference_digest": action.plan_reference_digest,
            "valid_until_monotonic": action.valid_until_monotonic,
            "preserve_unresolved_semantics": action.preserve_unresolved_semantics,
        }
        query = self.active_query
        query_value = None if query is None else {
            "query_id": query.query_id,
            "issued_monotonic_time": query.issued_monotonic_time,
            "answer_deadline_monotonic": query.answer_deadline_monotonic,
            "candidate_set_id": query.candidate_set_id,
            "question_digest": query.question_digest,
        }
        answer_status = {
            QueryState.NONE: "NONE",
            QueryState.REQUIRED: "NONE",
            QueryState.ACTIVE: "PENDING",
            QueryState.ANSWER_RECEIVED: "RESOLVED",
            QueryState.TIMED_OUT: "TIMED_OUT",
            QueryState.CANCELLED: "CANCELLED",
        }[self.query_state]
        history = [row.to_dict() for row in self.history]
        return {
            "schema_version": "driveclarify.persistent_ambiguity_episode.v1",
            "episode_id": self.episode_id,
            "instruction_id": self.instruction_id,
            "raw_instruction": self.raw_instruction,
            "instruction_sha256": self.instruction_sha256,
            "created_monotonic_time": self.created_monotonic_time,
            "updated_monotonic_time": self.updated_monotonic_time,
            "first_source_observation_id": self.first_source_observation_id,
            "latest_source_observation_id": source_observation_id,
            "latest_source_frame_id": source_frame_id,
            "semantic_state": self.semantic_state.value,
            "consequence_state": self.consequence_state.value,
            "query_state": self.query_state.value,
            "evidence_state": self.evidence_state.value,
            "original_candidate_set_id": self.original_candidate_set_id,
            "candidate_set_id": self.candidate_set_id,
            "candidates": candidates,
            "unresolved_slots": list(self.unresolved_slots),
            "current_candidate_relationship": self.current_candidate_relationship,
            "current_shared_executable_action": action_value,
            "future_divergence_evidence": self._evidence_reference(
                "future-divergence-" + self.episode_id,
                self.future_divergence_evidence_digest,
                status=evidence_status,
                source_observation_id=source_observation_id,
                source_frame_id=source_frame_id,
                observed_monotonic_time=observed_at,
            ),
            "last_refresh_monotonic_time": self.freshness.last_refresh_monotonic_time,
            "freshness_deadline_monotonic": self.freshness.freshness_deadline_monotonic,
            "expiry_monotonic_time": self.freshness.expiry_monotonic_time,
            "route_version": self.freshness.route_version,
            "environment_digest": self.freshness.environment_digest,
            "decision_window": self._evidence_reference(
                "decision-window-" + self.episode_id,
                self.decision_window_evidence_digest,
                status=evidence_status,
                source_observation_id=source_observation_id,
                source_frame_id=source_frame_id,
                observed_monotonic_time=observed_at,
            ),
            "decision_deadline_evidence": self._evidence_reference(
                "decision-deadline-" + self.episode_id,
                self.decision_deadline_evidence_digest,
                status=evidence_status,
                source_observation_id=source_observation_id,
                source_frame_id=source_frame_id,
                observed_monotonic_time=observed_at,
            ),
            "active_query": query_value,
            "answer_status": answer_status,
            "invalidation_reasons": list(dict.fromkeys(self.invalidation_history)),
            "reason_codes": list(dict.fromkeys(self.reason_codes)),
            "history": history,
            "compute_accounting": {
                "normal_planning_event_count": self.compute_accounting.normal_planning_event_count,
                "normal_forward_count": self.compute_accounting.normal_forward_count,
                "candidate_forward_count": self.compute_accounting.candidate_forward_count,
                "detector_forward_count": self.compute_accounting.detector_forward_count,
                "new_planner_advance_count": self.compute_accounting.new_planner_advance_count,
                "new_pid_count": self.compute_accounting.new_pid_count,
                "control_write_count": self.compute_accounting.control_write_count,
            },
            "audit_sequence": len(history),
            "audit_digest": canonical_sha256(history),
        }


@dataclass(frozen=True)
class StoreEvent:
    episode_id: str
    event_id: str
    event_type: str
    observed_monotonic_time: float
    source_frame_id: Any
    reason_code: str
    payload: Mapping[str, Any]


def _append_history(
    episode: PersistentAmbiguityEpisode,
    event: StoreEvent,
    *,
    event_type: Optional[str] = None,
    reason_code: Optional[str] = None,
    event_id_suffix: str = "",
) -> PersistentAmbiguityEpisode:
    previous = episode.audit_digest if episode.history else None
    sequence = len(episode.history) + 1
    body = {
        "sequence": sequence,
        "event_id": event.event_id + event_id_suffix,
        "event_type": event_type or event.event_type,
        "observed_monotonic_time": float(event.observed_monotonic_time),
        "source_frame_id": event.source_frame_id,
        "reason_code": reason_code or event.reason_code,
        "previous_audit_digest": previous,
    }
    history_event = HistoryEvent(event_digest=canonical_sha256(body), **body)
    history = episode.history + (history_event,)
    return replace(
        episode,
        updated_monotonic_time=float(event.observed_monotonic_time),
        history=history,
        audit_digest=canonical_sha256([row.to_dict() for row in history]),
    )


def _revoke_then_record(
    episode: PersistentAmbiguityEpisode, event: StoreEvent
) -> PersistentAmbiguityEpisode:
    revoked = _append_history(
        replace(episode, current_shared_executable_action=None),
        event,
        event_type="AUTHORIZATION_REVOKED",
        reason_code="OLD_SHARED_ACTION_ELIGIBILITY_REVOKED",
        event_id_suffix="-revoke",
    )
    return _append_history(revoked, event)


@dataclass(frozen=True)
class PersistentAmbiguityStore:
    episodes: Tuple[PersistentAmbiguityEpisode, ...] = ()

    def get(self, episode_id: str) -> PersistentAmbiguityEpisode:
        matches = [episode for episode in self.episodes if episode.episode_id == episode_id]
        if len(matches) != 1:
            raise KeyError("AMBIGUITY_EPISODE_NOT_FOUND:" + episode_id)
        return matches[0]

    def _put(self, episode: PersistentAmbiguityEpisode) -> "PersistentAmbiguityStore":
        remaining = tuple(row for row in self.episodes if row.episode_id != episode.episode_id)
        return PersistentAmbiguityStore(episodes=remaining + (episode,))

    def apply(self, event: StoreEvent) -> "PersistentAmbiguityStore":
        if event.event_type == "AMBIGUITY_GROUNDED":
            return self._create(event)
        episode = self.get(event.episode_id)
        if any(row.event_id == event.event_id for row in episode.history):
            return self
        updated = self._reduce(episode, event)
        return self._put(updated)

    def _create(self, event: StoreEvent) -> "PersistentAmbiguityStore":
        if any(row.episode_id == event.episode_id for row in self.episodes):
            raise ValueError("AMBIGUITY_EPISODE_ALREADY_EXISTS")
        candidates = tuple(event.payload["candidates"])
        unresolved_slots = tuple(event.payload["unresolved_slots"])
        if len(candidates) < 2 or len({row.semantic_sha256 for row in candidates}) < 2:
            raise ValueError("UNRESOLVED_EPISODE_REQUIRES_DISTINCT_K2")
        if not unresolved_slots:
            raise ValueError("UNRESOLVED_EPISODE_REQUIRES_UNRESOLVED_SLOTS")
        observation_id = str(event.payload["source_observation_id"])
        episode = PersistentAmbiguityEpisode(
            episode_id=event.episode_id,
            instruction_id=str(event.payload["instruction_id"]),
            raw_instruction=str(event.payload["raw_instruction"]),
            instruction_sha256=canonical_sha256(str(event.payload["raw_instruction"])),
            created_monotonic_time=float(event.observed_monotonic_time),
            updated_monotonic_time=float(event.observed_monotonic_time),
            first_source_observation_id=observation_id,
            original_candidate_set_id=str(event.payload["candidate_set_id"]),
            candidate_set_id=str(event.payload["candidate_set_id"]),
            candidates=candidates,
            unresolved_slots=unresolved_slots,
            semantic_state=SemanticState.UNRESOLVED,
            consequence_state=ConsequenceState.UNKNOWN,
            query_state=QueryState.NONE,
            evidence_state=EvidenceState.REFRESH_REQUIRED,
            current_candidate_relationship="UNKNOWN_OR_INSUFFICIENT_EVIDENCE",
            current_shared_executable_action=None,
            future_divergence_evidence_digest=None,
            decision_window_evidence_digest=None,
            decision_deadline_evidence_digest=None,
            freshness=FreshnessMetadata(
                latest_source_observation_id=observation_id,
                latest_source_frame_id=event.source_frame_id,
                last_refresh_monotonic_time=None,
                freshness_deadline_monotonic=None,
                expiry_monotonic_time=event.payload.get("expiry_monotonic_time"),
                route_version=event.payload.get("route_version"),
                environment_digest=event.payload.get("environment_digest"),
            ),
            active_query=None,
            query_history=(),
            invalidation_history=(),
            reason_codes=(event.reason_code,),
            compute_accounting=ComputeAccounting(),
            provenance=tuple(event.payload.get("provenance", ())),
            history=(),
            audit_digest=canonical_sha256([]),
        )
        return self._put(_append_history(episode, event))

    def _reduce(
        self, episode: PersistentAmbiguityEpisode, event: StoreEvent
    ) -> PersistentAmbiguityEpisode:
        kind = event.event_type
        if kind == "REFRESH_BUNDLE_STARTED":
            if episode.evidence_state is EvidenceState.REFRESHING:
                raise ValueError("REFRESH_BUNDLE_ALREADY_ACTIVE")
            return _append_history(replace(episode, evidence_state=EvidenceState.REFRESHING), event)

        if kind == "REFRESH_BUNDLE_COMPLETE_VALID":
            active = tuple(sorted(episode.active_candidate_ids))
            received = tuple(sorted(event.payload["active_candidate_ids"]))
            if received != active:
                raise ValueError("REFRESH_BUNDLE_CANDIDATE_SET_MISMATCH")
            route_version = str(event.payload["route_version"])
            if episode.freshness.route_version not in (None, route_version):
                invalid = _revoke_then_record(episode, replace(event, event_type="MATERIAL_INVALIDATION", reason_code="ROUTE_CONTEXT_CHANGED"))
                return replace(invalid, evidence_state=EvidenceState.STALE)
            freshness = replace(
                episode.freshness,
                latest_source_observation_id=str(event.payload["source_observation_id"]),
                latest_source_frame_id=event.source_frame_id,
                last_refresh_monotonic_time=float(event.observed_monotonic_time),
                freshness_deadline_monotonic=float(event.payload["freshness_deadline_monotonic"]),
                route_version=route_version,
                environment_digest=event.payload.get("environment_digest"),
            )
            accounting = replace(
                episode.compute_accounting,
                normal_planning_event_count=episode.compute_accounting.normal_planning_event_count + 1,
                normal_forward_count=episode.compute_accounting.normal_forward_count + int(event.payload.get("normal_forward_count", 1)),
                candidate_forward_requested_count=episode.compute_accounting.candidate_forward_requested_count + int(event.payload.get("candidate_forward_requested_count", 0)),
                candidate_forward_attempted_count=episode.compute_accounting.candidate_forward_attempted_count + int(event.payload.get("candidate_forward_attempted_count", 0)),
                candidate_forward_count=episode.compute_accounting.candidate_forward_count + int(event.payload.get("candidate_forward_count", 0)),
                candidate_forward_succeeded_count=episode.compute_accounting.candidate_forward_succeeded_count + int(event.payload.get("candidate_forward_succeeded_count", 0)),
                detector_forward_count=episode.compute_accounting.detector_forward_count + int(event.payload.get("detector_forward_count", 0)),
                candidate_forward_skipped_count=episode.compute_accounting.candidate_forward_skipped_count + int(event.payload.get("candidate_forward_skipped_count", 0)),
                candidate_forward_failed_count=episode.compute_accounting.candidate_forward_failed_count + int(event.payload.get("candidate_forward_failed_count", 0)),
            )
            updated = replace(
                episode,
                candidate_set_id=str(event.payload.get("candidate_set_id", episode.candidate_set_id)),
                evidence_state=EvidenceState.FRESH,
                consequence_state=ConsequenceState(event.payload.get("consequence_state", episode.consequence_state.value)),
                current_candidate_relationship=str(event.payload.get("relationship", episode.current_candidate_relationship)),
                future_divergence_evidence_digest=event.payload.get("future_divergence_evidence_digest"),
                decision_window_evidence_digest=event.payload.get("decision_window_evidence_digest"),
                decision_deadline_evidence_digest=event.payload.get("decision_deadline_evidence_digest"),
                current_shared_executable_action=None,
                freshness=freshness,
                compute_accounting=accounting,
            )
            return _append_history(updated, event)

        if kind in ("REFRESH_BUNDLE_PARTIAL", "REFRESH_BUNDLE_CROSS_VERSION"):
            accounting = replace(
                episode.compute_accounting,
                normal_planning_event_count=episode.compute_accounting.normal_planning_event_count + 1,
                normal_forward_count=episode.compute_accounting.normal_forward_count + int(event.payload.get("normal_forward_count", 1)),
                candidate_forward_requested_count=episode.compute_accounting.candidate_forward_requested_count + int(event.payload.get("candidate_forward_requested_count", 0)),
                candidate_forward_attempted_count=episode.compute_accounting.candidate_forward_attempted_count + int(event.payload.get("candidate_forward_attempted_count", 0)),
                candidate_forward_count=episode.compute_accounting.candidate_forward_count + int(event.payload.get("candidate_forward_count", 0)),
                candidate_forward_succeeded_count=episode.compute_accounting.candidate_forward_succeeded_count + int(event.payload.get("candidate_forward_succeeded_count", 0)),
                detector_forward_count=episode.compute_accounting.detector_forward_count + int(event.payload.get("detector_forward_count", 0)),
                candidate_forward_skipped_count=episode.compute_accounting.candidate_forward_skipped_count + int(event.payload.get("candidate_forward_skipped_count", 0)),
                candidate_forward_failed_count=episode.compute_accounting.candidate_forward_failed_count + int(event.payload.get("candidate_forward_failed_count", 0)),
            )
            updated = replace(
                episode,
                evidence_state=EvidenceState.REFRESH_REQUIRED,
                consequence_state=ConsequenceState.UNKNOWN,
                current_candidate_relationship="UNKNOWN_OR_INSUFFICIENT_EVIDENCE",
                current_shared_executable_action=None,
                reason_codes=episode.reason_codes + (event.reason_code,),
                compute_accounting=accounting,
            )
            return _append_history(updated, event)

        if kind == "DECISION_ACT_SHARED":
            if not (
                episode.semantic_state is SemanticState.UNRESOLVED
                and episode.evidence_state is EvidenceState.FRESH
                and episode.consequence_state
                in (
                    ConsequenceState.CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT,
                    ConsequenceState.NO_MATERIAL_DIVERGENCE,
                    ConsequenceState.CURRENT_ACTION_SHARED_FUTURE_UNKNOWN,
                )
                and episode.query_state is QueryState.NONE
            ):
                raise ValueError("ACT_SHARED_EPISODE_GATES_NOT_MET")
            action = event.payload["shared_action"]
            if not isinstance(action, SharedActionReference):
                raise TypeError("ACT_SHARED_REQUIRES_SHARED_ACTION_REFERENCE")
            if tuple(sorted(action.active_member_ids)) != tuple(sorted(episode.active_candidate_ids)):
                raise ValueError("ACT_SHARED_MEMBER_SET_MISMATCH")
            updated = replace(episode, current_shared_executable_action=action)
            return _append_history(updated, event)

        if kind == "SHARED_WINDOW_CONSUMED":
            updated = replace(
                episode,
                current_shared_executable_action=None,
                evidence_state=EvidenceState.REFRESH_REQUIRED,
                reason_codes=episode.reason_codes + (event.reason_code,),
            )
            return _append_history(updated, event)

        if kind == "SHARED_WINDOW_EXPIRED_UNUSED":
            revoked = _revoke_then_record(episode, event)
            return replace(
                revoked,
                current_shared_executable_action=None,
                evidence_state=EvidenceState.REFRESH_REQUIRED,
                reason_codes=episode.reason_codes + (event.reason_code,),
            )

        if kind == "EVIDENCE_REFRESH_DEADLINE_EXPIRED":
            updated = replace(
                episode,
                current_shared_executable_action=None,
                evidence_state=EvidenceState.REFRESH_REQUIRED,
                consequence_state=ConsequenceState.UNKNOWN,
                current_candidate_relationship=(
                    "UNKNOWN_OR_INSUFFICIENT_EVIDENCE"
                ),
                reason_codes=episode.reason_codes + (event.reason_code,),
            )
            return _append_history(updated, event)

        if kind in (
            "MATERIAL_INVALIDATION", "ROUTE_OR_ENV_OR_WORLD_CHANGE",
            "REFERENT_IDENTITY_LOST", "TARGET_BINDING_STALE",
        ):
            updated = _revoke_then_record(episode, event)
            return replace(
                updated,
                evidence_state=EvidenceState.STALE,
                consequence_state=ConsequenceState.UNKNOWN,
                current_candidate_relationship="UNKNOWN_OR_INSUFFICIENT_EVIDENCE",
                invalidation_history=episode.invalidation_history + (event.reason_code,),
                reason_codes=episode.reason_codes + (event.reason_code,),
            )

        if kind == "MATERIAL_DIVERGENCE":
            revoked = _revoke_then_record(episode, event)
            updated = replace(
                revoked,
                consequence_state=ConsequenceState.CURRENTLY_DIVERGENT,
                current_candidate_relationship="CURRENTLY_DIVERGENT",
                current_shared_executable_action=None,
                evidence_state=EvidenceState.STALE,
            )
            return updated

        if kind == "ASK_ISSUED":
            if episode.query_state is not QueryState.NONE or episode.active_query is not None:
                raise ValueError("SINGLE_ACTIVE_QUERY_GUARD")
            candidate_set_id = str(
                event.payload.get("candidate_set_id", episode.candidate_set_id)
            )
            if candidate_set_id != episode.candidate_set_id:
                raise ValueError("QUERY_CANDIDATE_SET_IDENTITY_MISMATCH")
            query = QueryRecord(
                query_id=str(event.payload["query_id"]),
                candidate_set_id=candidate_set_id,
                issued_monotonic_time=float(event.observed_monotonic_time),
                answer_deadline_monotonic=float(event.payload["answer_deadline_monotonic"]),
                question_digest=str(event.payload["question_digest"]),
                status=QueryState.ACTIVE,
                lifecycle_owner=str(
                    event.payload.get("lifecycle_owner", "WALL_DEADLINE_V1")
                ),
            )
            return _append_history(replace(episode, query_state=QueryState.ACTIVE, active_query=query), event)

        if kind == "ANSWER_ARRIVED_VALID":
            if episode.active_query is None or event.payload.get("query_id") != episode.active_query.query_id:
                raise ValueError("QUERY_IDENTITY_MISMATCH")
            if (
                episode.active_query.lifecycle_owner != "SEMANTIC_COMMITMENT_V2_7"
                and float(event.observed_monotonic_time) > float(
                    episode.active_query.answer_deadline_monotonic
                )
            ):
                raise ValueError("ANSWER_TOO_LATE")
            revoked = _revoke_then_record(episode, event)
            answered = replace(
                episode.active_query,
                status=QueryState.ANSWER_RECEIVED,
                answer_digest=str(event.payload["answer_digest"]),
            )
            return replace(
                revoked,
                query_state=QueryState.ANSWER_RECEIVED,
                active_query=None,
                query_history=episode.query_history + (answered,),
                evidence_state=EvidenceState.REFRESH_REQUIRED,
                current_shared_executable_action=None,
            )

        if kind == "CANDIDATE_REJECTED_BY_EVIDENCE":
            rejected_ids = tuple(sorted(str(value) for value in event.payload["candidate_ids"]))
            if not rejected_ids or any(value not in episode.active_candidate_ids for value in rejected_ids):
                raise ValueError("EVIDENCE_REJECTION_CANDIDATE_INVALID")
            candidates = tuple(
                replace(
                    candidate,
                    status=(
                        CandidateStatus.REJECTED_BY_EVIDENCE
                        if candidate.candidate_id in rejected_ids
                        else candidate.status
                    ),
                )
                for candidate in episode.candidates
            )
            remaining = tuple(
                row.candidate_id
                for row in candidates
                if row.status is CandidateStatus.ACTIVE_UNRESOLVED
            )
            updated = _revoke_then_record(episode, event)
            if not remaining:
                return replace(
                    updated,
                    candidates=candidates,
                    semantic_state=SemanticState.INVALIDATED,
                    evidence_state=EvidenceState.INVALID,
                    consequence_state=ConsequenceState.UNKNOWN,
                    current_candidate_relationship=(
                        "UNKNOWN_OR_INSUFFICIENT_EVIDENCE"
                    ),
                    current_shared_executable_action=None,
                    reason_codes=episode.reason_codes + (event.reason_code,),
                )
            return replace(
                updated,
                candidates=candidates,
                semantic_state=SemanticState.UNRESOLVED,
                evidence_state=EvidenceState.REFRESH_REQUIRED,
                consequence_state=ConsequenceState.UNKNOWN,
                current_candidate_relationship="UNKNOWN_OR_INSUFFICIENT_EVIDENCE",
                current_shared_executable_action=None,
                reason_codes=episode.reason_codes + (event.reason_code,),
            )

        if kind == "LATEST_REPLAN_STILL_AMBIGUOUS_VALID":
            updated = replace(
                episode,
                semantic_state=SemanticState.UNRESOLVED,
                query_state=QueryState.NONE,
                evidence_state=EvidenceState.FRESH,
                current_shared_executable_action=None,
            )
            return _append_history(updated, event)

        if kind in ("LATEST_REPLAN_FAILED", "LATEST_REPLAN_CANDIDATE_MISSING_UNEXPLAINED"):
            convergence_failure = bool(
                len(episode.active_candidate_ids) == 1
                and "RUNTIME_EVIDENCE_CANDIDATE_REJECTED" in episode.reason_codes
            )
            candidates = (
                tuple(
                    replace(
                        candidate,
                        status=(
                            CandidateStatus.INVALID
                            if candidate.status is CandidateStatus.ACTIVE_UNRESOLVED
                            else candidate.status
                        ),
                    )
                    for candidate in episode.candidates
                )
                if convergence_failure
                else episode.candidates
            )
            updated = replace(
                episode,
                candidates=candidates,
                semantic_state=(
                    SemanticState.INVALIDATED
                    if convergence_failure
                    else SemanticState.UNRESOLVED
                ),
                query_state=QueryState.NONE,
                evidence_state=EvidenceState.INVALID,
                current_shared_executable_action=None,
                reason_codes=episode.reason_codes + (event.reason_code,),
            )
            return _append_history(updated, event)

        if kind == "LATEST_REPLAN_UNIQUE":
            if episode.query_state is not QueryState.ANSWER_RECEIVED:
                raise ValueError("UNIQUE_REPLAN_REQUIRES_MATCHED_ANSWER")
            selected_id = str(event.payload["selected_candidate_id"])
            if selected_id not in episode.active_candidate_ids:
                raise ValueError("UNIQUE_REPLAN_SELECTED_CANDIDATE_INVALID")
            candidates = tuple(
                replace(
                    candidate,
                    status=(
                        CandidateStatus.RESOLVED_SELECTED
                        if candidate.candidate_id == selected_id
                        else CandidateStatus.REJECTED_BY_EVIDENCE
                    ),
                )
                for candidate in episode.candidates
            )
            freshness = replace(
                episode.freshness,
                latest_source_observation_id=str(event.payload["source_observation_id"]),
                latest_source_frame_id=event.source_frame_id,
                last_refresh_monotonic_time=float(event.observed_monotonic_time),
                freshness_deadline_monotonic=float(event.payload["freshness_deadline_monotonic"]),
            )
            updated = replace(
                episode,
                candidates=candidates,
                unresolved_slots=(),
                semantic_state=SemanticState.RESOLVED,
                query_state=QueryState.NONE,
                evidence_state=EvidenceState.FRESH,
                current_shared_executable_action=None,
                decision_window_evidence_digest=str(event.payload["fresh_replan_digest"]),
                freshness=freshness,
            )
            return _append_history(updated, event)

        if kind == "LATEST_REPLAN_UNIQUE_FROM_EVIDENCE":
            if episode.query_state is not QueryState.NONE:
                raise ValueError("EVIDENCE_UNIQUE_REPLAN_REQUIRES_NO_ACTIVE_QUERY")
            active_ids = episode.active_candidate_ids
            selected_id = str(event.payload["selected_candidate_id"])
            if active_ids != (selected_id,):
                raise ValueError("EVIDENCE_UNIQUE_REPLAN_SELECTED_CANDIDATE_INVALID")
            if episode.evidence_state is not EvidenceState.REFRESH_REQUIRED:
                raise ValueError("EVIDENCE_UNIQUE_REPLAN_REQUIRES_INVALIDATED_BUNDLE")
            candidates = tuple(
                replace(
                    candidate,
                    status=(
                        CandidateStatus.RESOLVED_SELECTED
                        if candidate.candidate_id == selected_id
                        else candidate.status
                    ),
                )
                for candidate in episode.candidates
            )
            freshness = replace(
                episode.freshness,
                latest_source_observation_id=str(event.payload["source_observation_id"]),
                latest_source_frame_id=event.source_frame_id,
                last_refresh_monotonic_time=float(event.observed_monotonic_time),
                freshness_deadline_monotonic=float(event.payload["freshness_deadline_monotonic"]),
            )
            accounting = replace(
                episode.compute_accounting,
                normal_planning_event_count=(
                    episode.compute_accounting.normal_planning_event_count + 1
                ),
                normal_forward_count=(
                    episode.compute_accounting.normal_forward_count
                    + int(event.payload.get("normal_forward_count", 1))
                ),
                candidate_forward_requested_count=(
                    episode.compute_accounting.candidate_forward_requested_count
                    + int(event.payload.get("candidate_forward_requested_count", 1))
                ),
                candidate_forward_attempted_count=(
                    episode.compute_accounting.candidate_forward_attempted_count
                    + int(event.payload.get("candidate_forward_attempted_count", 1))
                ),
                candidate_forward_count=(
                    episode.compute_accounting.candidate_forward_count
                    + int(event.payload.get("candidate_forward_count", 1))
                ),
                candidate_forward_succeeded_count=(
                    episode.compute_accounting.candidate_forward_succeeded_count
                    + int(event.payload.get("candidate_forward_succeeded_count", 1))
                ),
            )
            updated = replace(
                episode,
                candidates=candidates,
                candidate_set_id=str(
                    event.payload.get("candidate_set_id", episode.candidate_set_id)
                ),
                unresolved_slots=(),
                semantic_state=SemanticState.RESOLVED,
                evidence_state=EvidenceState.FRESH,
                current_shared_executable_action=None,
                decision_window_evidence_digest=str(event.payload["fresh_replan_digest"]),
                freshness=freshness,
                compute_accounting=accounting,
            )
            return _append_history(updated, event)

        if kind == "QUERY_TIMEOUT":
            updated = _revoke_then_record(episode, event)
            query_history = episode.query_history
            if episode.active_query is not None:
                query_history += (replace(episode.active_query, status=QueryState.TIMED_OUT),)
            return replace(
                updated,
                semantic_state=SemanticState.UNRESOLVED,
                query_state=QueryState.TIMED_OUT,
                active_query=None,
                query_history=query_history,
                evidence_state=EvidenceState.STALE,
            )

        if kind == "EPISODE_TTL_EXPIRED":
            updated = _revoke_then_record(episode, event)
            return replace(
                updated,
                semantic_state=SemanticState.EXPIRED,
                evidence_state=EvidenceState.INVALID,
                current_shared_executable_action=None,
                reason_codes=episode.reason_codes + (event.reason_code,),
            )

        if kind == "TRACK_LOST":
            updated = _revoke_then_record(episode, event)
            return replace(
                updated,
                semantic_state=SemanticState.UNRESOLVED,
                evidence_state=EvidenceState.STALE,
                current_shared_executable_action=None,
                invalidation_history=episode.invalidation_history + (event.reason_code,),
                reason_codes=episode.reason_codes + (event.reason_code,),
            )

        if kind == "NEW_INSTRUCTION_CONFLICT":
            updated = _revoke_then_record(episode, event)
            return replace(
                updated,
                semantic_state=SemanticState.UNRESOLVED,
                evidence_state=EvidenceState.REFRESH_REQUIRED,
                current_shared_executable_action=None,
                invalidation_history=episode.invalidation_history + (event.reason_code,),
                reason_codes=episode.reason_codes + (event.reason_code,),
            )

        if kind in ("LMDRIVE_COMPLETED", "RUNTIME_TERMINAL", "ROUTE_PLANNER_IS_LAST"):
            return _append_history(
                replace(episode, semantic_state=SemanticState.UNRESOLVED), event
            )
        raise ValueError("UNKNOWN_PERSISTENT_AMBIGUITY_EVENT:" + kind)
