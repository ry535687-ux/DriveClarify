"""Deployable, fail-closed evidence providers for RQ2-T V2.

No provider reads authored reveal time, future commitment truth, passenger
intent, QueryNecessityGold, or full offline route truth.  A provider may turn
AVAILABLE only from evidence present in the current runtime snapshot.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping, Optional, Sequence

from driveclarify_rq2_t.measurement import canonical_sha256


FORBIDDEN_RUNTIME_KEYS = frozenset(
    {
        "true_interpretation",
        "future_reveal_time",
        "authored_reveal_time",
        "exact_future_commitment_truth",
        "query_necessity_gold",
        "provider_correctness_gold",
        "reveal_correctness_gold",
        "full_offline_topology_truth",
        "policy_outcome",
    }
)

E2_IDENTITY_CONFIDENCE_THRESHOLD = 0.80


def assert_runtime_payload_has_no_oracle(value: Any, path: str = "runtime") -> None:
    """Reject post-episode/gold keys before provider evaluation."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().casefold()
            if normalized in FORBIDDEN_RUNTIME_KEYS or normalized.endswith("_gold"):
                raise PermissionError("RQ2_T_V2_ORACLE_KEY_FORBIDDEN:" + path + "." + str(key))
            assert_runtime_payload_has_no_oracle(child, path + "." + str(key))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            assert_runtime_payload_has_no_oracle(child, path + "[{}]".format(index))


def _unknown(
    field_id: str,
    *,
    now_s: float,
    source_frame_id: Any,
    source_observation_id: str,
    owner: str,
    reasons: Sequence[str],
    dependencies: Sequence[str] = (),
) -> dict[str, Any]:
    value = {
        "field_id": field_id,
        "status": "UNKNOWN",
        "value": None,
        "units": None,
        "frame": None,
        "simulation_timestamp_s": float(now_s),
        "source_frame_id": source_frame_id,
        "source_observation_id": str(source_observation_id),
        "owner": owner,
        "evidence_grade": "UNKNOWN_PRESERVED",
        "allowed_usage_purpose": "RQ2_T_V2_EVIDENCE_METHOD_ONLY",
        "freshness_age_simulation_s": 0.0,
        "dependencies": list(dependencies),
        "reason_codes": list(reasons),
        "retention": None,
    }
    value["evidence_digest"] = canonical_sha256(value)
    return value


def _available(
    field_id: str,
    *,
    payload: Mapping[str, Any],
    now_s: float,
    source_frame_id: Any,
    source_observation_id: str,
    owner: str,
    units: str,
    frame: str,
    dependencies: Sequence[str],
) -> dict[str, Any]:
    value = {
        "field_id": field_id,
        "status": "AVAILABLE",
        "value": copy.deepcopy(dict(payload)),
        "units": units,
        "frame": frame,
        "simulation_timestamp_s": float(now_s),
        "source_frame_id": source_frame_id,
        "source_observation_id": str(source_observation_id),
        "owner": owner,
        "evidence_grade": "AVAILABLE_RUNTIME_OBSERVED",
        "allowed_usage_purpose": "RQ2_T_V2_EVIDENCE_METHOD_ONLY",
        "freshness_age_simulation_s": 0.0,
        "dependencies": list(dependencies),
        "reason_codes": [],
        "retention": None,
    }
    value["evidence_digest"] = canonical_sha256(value)
    return value


def _source_identity(
    signal: Optional[Mapping[str, Any]],
    *,
    now_s: float,
    fallback_frame_id: Any,
    fallback_observation_id: str,
) -> tuple[Any, str]:
    if not isinstance(signal, Mapping):
        return fallback_frame_id, fallback_observation_id
    frame = signal.get("source_frame_id", fallback_frame_id)
    observation = str(signal.get("source_observation_id") or fallback_observation_id)
    timestamp = signal.get("simulation_time_s", now_s)
    if abs(float(timestamp) - float(now_s)) > 1e-9:
        raise ValueError("RQ2_T_V2_SIGNAL_TIME_MISMATCH")
    return frame, observation


def provide_grounding_e2(
    *,
    now_s: float,
    active_candidate_ids: Sequence[str],
    source_frame_id: Any,
    source_observation_id: str,
    history_row: Optional[Mapping[str, Any]] = None,
    grounding_signal: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Provide E2 independently of future-obligation/topology availability.

    The existing V1 adapter coupled E2 to the aggregate future-obligation
    status.  V2 accepts candidate-level runtime RGB/track lineage directly,
    while still requiring complete active-candidate coverage and distinct,
    coherent identities.
    """

    assert_runtime_payload_has_no_oracle(grounding_signal)
    candidate_ids = tuple(sorted(str(value) for value in active_candidate_ids if value))
    rows: Sequence[Mapping[str, Any]] = ()
    owner = "V2_RUNTIME_CAMERA_TRACK_GROUNDING_PROVIDER"
    frame_id, observation_id = _source_identity(
        grounding_signal,
        now_s=now_s,
        fallback_frame_id=source_frame_id,
        fallback_observation_id=source_observation_id,
    )
    if isinstance(grounding_signal, Mapping):
        if grounding_signal.get("authorization_scope") != "RUNTIME_CAMERA_CURRENT_OR_PAST":
            return _unknown(
                "E2_GROUNDING",
                now_s=now_s,
                source_frame_id=frame_id,
                source_observation_id=observation_id,
                owner=owner,
                reasons=("GROUNDING_SIGNAL_NOT_RUNTIME_AUTHORIZED",),
            )
        rows = tuple(
            row
            for row in grounding_signal.get("candidate_groundings", ())
            if isinstance(row, Mapping)
        )
    elif isinstance(history_row, Mapping):
        m2b = history_row.get("m2b_inputs")
        evidence = m2b.get("evidence") if isinstance(m2b, Mapping) else None
        future = evidence.get("future_obligation") if isinstance(evidence, Mapping) else None
        if isinstance(future, Mapping):
            rows = tuple(row for row in future.get("rows", ()) if isinstance(row, Mapping))
            owner = "V2_DECOUPLED_DECISION_EVIDENCE_V3_GROUNDING_PROVIDER"

    by_candidate = {str(row.get("candidate_id")): row for row in rows if row.get("candidate_id")}
    if len(candidate_ids) < 2:
        return _unknown(
            "E2_GROUNDING",
            now_s=now_s,
            source_frame_id=frame_id,
            source_observation_id=observation_id,
            owner=owner,
            reasons=("GROUNDING_ACTIVE_CANDIDATE_SET_LT_2",),
        )
    if set(by_candidate) != set(candidate_ids):
        return _unknown(
            "E2_GROUNDING",
            now_s=now_s,
            source_frame_id=frame_id,
            source_observation_id=observation_id,
            owner=owner,
            reasons=("GROUNDING_ACTIVE_CANDIDATE_COVERAGE_INCOMPLETE",),
        )

    lineages = []
    normalized = []
    for candidate_id in candidate_ids:
        row = by_candidate[candidate_id]
        lineage = str(row.get("referent_lineage_id") or row.get("track_id") or "")
        visibility = str(row.get("visibility") or "")
        source_kinds = tuple(str(value) for value in row.get("source_kinds", ()))
        # Missing confidence is not evidence.  The detached component prototype
        # used an optimistic default here; the native path must preserve UNKNOWN
        # unless the runtime observation supplies a calibrated numeric value.
        confidence = row.get("identity_confidence")
        valid = bool(
            lineage
            and visibility == "RUNTIME_OBSERVABLE"
            and row.get("privileged") is not True
            and row.get("semantic_fresh", True) is True
            and row.get("active_unresolved", True) is True
            and isinstance(confidence, (int, float))
            and not isinstance(confidence, bool)
            and float(confidence) >= E2_IDENTITY_CONFIDENCE_THRESHOLD
            and (
                "RUNTIME_RGB_0_GROUNDING" in source_kinds
                or "RUNTIME_CAMERA_TRACK" in source_kinds
            )
        )
        if not valid:
            return _unknown(
                "E2_GROUNDING",
                now_s=now_s,
                source_frame_id=frame_id,
                source_observation_id=observation_id,
                owner=owner,
                reasons=("GROUNDING_IDENTITY_NOT_DEPLOYABLY_ESTABLISHED:" + candidate_id,),
            )
        lineages.append(lineage)
        normalized.append(
            {
                "candidate_id": candidate_id,
                "interpretation_id": row.get("interpretation_id"),
                "referent_lineage_id": lineage,
                "referent_description": row.get("referent_description"),
                "identity_confidence": float(confidence),
                "source_kinds": list(source_kinds),
            }
        )
    if len(set(lineages)) != len(lineages):
        return _unknown(
            "E2_GROUNDING",
            now_s=now_s,
            source_frame_id=frame_id,
            source_observation_id=observation_id,
            owner=owner,
            reasons=("GROUNDING_LINEAGE_NOT_DISCRIMINATIVE",),
        )
    return _available(
        "E2_GROUNDING",
        payload={
            "grounding_complete": True,
            "candidate_groundings": normalized,
            "candidate_set_digest": canonical_sha256(candidate_ids),
        },
        now_s=now_s,
        source_frame_id=frame_id,
        source_observation_id=observation_id,
        owner=owner,
        units="STRUCTURED_TRACKED_GROUNDING_IDENTITIES",
        frame="CURRENT_OR_RETAINED_RUNTIME_CAMERA_OBSERVATION",
        dependencies=("runtime camera grounding", "tracked semantic identity"),
    )


def provide_topology_e5(
    *,
    now_s: float,
    source_frame_id: Any,
    source_observation_id: str,
    route_version: Optional[str],
    environment_digest: Optional[str],
    topology_signal: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    """Expose only locally deployable route/lane/topology evidence."""

    assert_runtime_payload_has_no_oracle(topology_signal)
    owner = "V2_LIVE_CARLA_LOCAL_TOPOLOGY_PROVIDER"
    frame_id, observation_id = _source_identity(
        topology_signal,
        now_s=now_s,
        fallback_frame_id=source_frame_id,
        fallback_observation_id=source_observation_id,
    )
    if not isinstance(topology_signal, Mapping):
        return _unknown(
            "E5_ROUTE_LANE_TOPOLOGY_RELATION",
            now_s=now_s,
            source_frame_id=frame_id,
            source_observation_id=observation_id,
            owner=owner,
            reasons=("LOCAL_TOPOLOGY_SIGNAL_ABSENT",),
        )
    if (
        topology_signal.get("authorization_scope") != "LOCAL_DEPLOYABLE_ROUTE_HORIZON"
        or topology_signal.get("source_kind") != "LIVE_CARLA_HD_MAP_LOCAL_TOPOLOGY"
        or topology_signal.get("privileged") is True
    ):
        return _unknown(
            "E5_ROUTE_LANE_TOPOLOGY_RELATION",
            now_s=now_s,
            source_frame_id=frame_id,
            source_observation_id=observation_id,
            owner=owner,
            reasons=("LOCAL_TOPOLOGY_SIGNAL_NOT_RUNTIME_AUTHORIZED",),
        )
    if (
        not route_version
        or not environment_digest
        or topology_signal.get("route_version") != route_version
        or topology_signal.get("environment_digest") != environment_digest
    ):
        return _unknown(
            "E5_ROUTE_LANE_TOPOLOGY_RELATION",
            now_s=now_s,
            source_frame_id=frame_id,
            source_observation_id=observation_id,
            owner=owner,
            reasons=("LOCAL_TOPOLOGY_ROUTE_ENVIRONMENT_IDENTITY_MISMATCH",),
        )
    opportunities = tuple(
        row
        for row in topology_signal.get("qualifying_opportunities", ())
        if isinstance(row, Mapping)
    )
    ordinal = topology_signal.get("required_ordinal")
    if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 1:
        return _unknown(
            "E5_ROUTE_LANE_TOPOLOGY_RELATION",
            now_s=now_s,
            source_frame_id=frame_id,
            source_observation_id=observation_id,
            owner=owner,
            reasons=("ORDER_LANGUAGE_ORDINAL_NOT_PARSED",),
        )
    indices = [row.get("route_order_index") for row in opportunities]
    indices_valid = all(
        isinstance(value, int) and not isinstance(value, bool) for value in indices
    )
    complete = bool(
        len(opportunities) >= ordinal
        and indices_valid
        and indices == sorted(indices)
        and len(set(indices)) == len(indices)
        and all(
            bool(row.get("junction_id"))
            and isinstance(row.get("road_id"), (str, int))
            and isinstance(row.get("lane_id"), int)
            and row.get("locally_observable") is True
            for row in opportunities
        )
    )
    if not complete:
        return _unknown(
            "E5_ROUTE_LANE_TOPOLOGY_RELATION",
            now_s=now_s,
            source_frame_id=frame_id,
            source_observation_id=observation_id,
            owner=owner,
            reasons=("QUALIFYING_LOCAL_TOPOLOGY_SEQUENCE_INCOMPLETE",),
        )
    normalized = [
        {
            "junction_id": row.get("junction_id"),
            "road_id": row.get("road_id"),
            "lane_id": row.get("lane_id"),
            "route_order_index": row.get("route_order_index"),
            "maneuver_class": row.get("maneuver_class"),
        }
        for row in opportunities
    ]
    return _available(
        "E5_ROUTE_LANE_TOPOLOGY_RELATION",
        payload={
            "route_version": route_version,
            "environment_digest": environment_digest,
            "required_ordinal": ordinal,
            "qualifying_opportunities": normalized,
            "selected_opportunity_identity": normalized[ordinal - 1]["junction_id"],
            "local_horizon_end_progress_m": topology_signal.get("local_horizon_end_progress_m"),
            "topology_boundary_id": topology_signal.get("topology_boundary_id"),
        },
        now_s=now_s,
        source_frame_id=frame_id,
        source_observation_id=observation_id,
        owner=owner,
        units="ORDERED_LOCAL_ROUTE_TOPOLOGY",
        frame="ACTIVE_ROUTE_LOCAL_DEPLOYABLE_HORIZON",
        dependencies=("live local HD map", "active route identity", "parsed ordinal"),
    )


def provide_holding_safety_e7(
    *,
    now_s: float,
    source_frame_id: Any,
    source_observation_id: str,
    history_row: Optional[Mapping[str, Any]] = None,
    safety_signal: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """State safety/holding only from explicit positive or negative evidence."""

    assert_runtime_payload_has_no_oracle(safety_signal)
    owner = "V2_CURRENT_PHYSICAL_SAFETY_AND_EXISTING_HOLDING_PROVIDER"
    frame_id, observation_id = _source_identity(
        safety_signal,
        now_s=now_s,
        fallback_frame_id=source_frame_id,
        fallback_observation_id=source_observation_id,
    )
    physical = rule = holding = shared_lease = None
    holding_owner = None
    if isinstance(safety_signal, Mapping):
        if safety_signal.get("authorization_scope") != "CURRENT_RUNTIME_SAFETY_STATE":
            return _unknown(
                "E7_SAFETY_RULE_HOLDING",
                now_s=now_s,
                source_frame_id=frame_id,
                source_observation_id=observation_id,
                owner=owner,
                reasons=("SAFETY_SIGNAL_NOT_RUNTIME_AUTHORIZED",),
            )
        physical = safety_signal.get("current_physical_safety_gate")
        rule = safety_signal.get("hard_rule_gate")
        holding = safety_signal.get("safe_holding_available")
        shared_lease = safety_signal.get("shared_action_lease_valid")
        holding_owner = safety_signal.get("holding_owner")
    elif isinstance(history_row, Mapping):
        m2b = history_row.get("m2b_inputs")
        evidence = m2b.get("evidence") if isinstance(m2b, Mapping) else None
        lease = evidence.get("shared_action_lease") if isinstance(evidence, Mapping) else None
        physical = m2b.get("hard_safety_gate") if isinstance(m2b, Mapping) else None
        rule = m2b.get("hard_rule_gate") if isinstance(m2b, Mapping) else None
        holding = m2b.get("safe_holding_available") if isinstance(m2b, Mapping) else None
        shared_lease = lease.get("valid") if isinstance(lease, Mapping) else None
        holding_owner = "EXISTING_WAIT_OWNER" if holding is True else None

    explicit = all(value in (True, False) for value in (physical, rule)) and (
        holding in (True, False) or shared_lease in (True, False)
    )
    if not explicit:
        return _unknown(
            "E7_SAFETY_RULE_HOLDING",
            now_s=now_s,
            source_frame_id=frame_id,
            source_observation_id=observation_id,
            owner=owner,
            reasons=("SAFETY_RULE_OR_HOLDING_DEPENDENCY_UNKNOWN",),
        )
    safe_shared = bool(physical is True and rule is True and shared_lease is True)
    safe_holding = bool(physical is True and holding is True and holding_owner)
    return _available(
        "E7_SAFETY_RULE_HOLDING",
        payload={
            "information_action_safe": physical,
            "motion_rule_admissible": rule,
            "safe_holding_available": holding,
            "holding_owner": holding_owner,
            "shared_action_lease_valid": shared_lease,
            "safe_shared_action_available": safe_shared,
            "safe_holding_or_shared_action_admissible": bool(safe_holding or safe_shared),
            "absence_of_hazard_treated_as_safe": False,
        },
        now_s=now_s,
        source_frame_id=frame_id,
        source_observation_id=observation_id,
        owner=owner,
        units="BOOLEAN_CERTIFICATE_STATES",
        frame="CURRENT_RUNTIME_SAFETY_AND_AUTHORITY_STATE",
        dependencies=("same-frame physical safety", "rule evidence", "holding or shared lease"),
    )


__all__ = [
    "E2_IDENTITY_CONFIDENCE_THRESHOLD",
    "FORBIDDEN_RUNTIME_KEYS",
    "assert_runtime_payload_has_no_oracle",
    "provide_grounding_e2",
    "provide_holding_safety_e7",
    "provide_topology_e5",
]
