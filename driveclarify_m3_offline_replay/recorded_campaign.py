"""Fail-closed admission helpers for recorded non-blind M2B campaigns.

This module does not infer missing identities, clocks, evidence, leases, or
initial M3 state.  It is deliberately separate from the frozen reducer.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .contracts import EpisodeRejection, ReplayEpisode
from .runner import run_episode
from .serialization import canonical_sha256, strict_json_dumps


ADMISSION_SCHEMA = "driveclarify.m3.recorded-m2b-admission.v1"
STRUCTURED_ADMISSION_REJECTION = "STRUCTURED_ADMISSION_REJECTION"
LEGAL_SPLITS = frozenset({"TRAIN", "DEV"})
LEGAL_ACTIONS = frozenset({"ACT", "ASK", "WAIT", "FALLBACK"})
EVIDENCE_GRADES = frozenset({
    "VERIFIED_FROM_CONTROLLED_PROBE",
    "SUPPORTED_BUT_INCOMPLETE",
    "UNRESOLVED_REQUIRES_ADDITIONAL_PROBE",
    "INVALIDATED_BY_CONTROLLED_PROBE",
    "NOT_CURRENTLY_AVAILABLE",
})
REQUIRED_SOURCE_FIELDS = (
    "decision_id", "source_observation_id", "source_frame_id",
    "candidate_set_id", "selected_action", "decision_monotonic_time",
    "decision_deadline_monotonic", "candidate_freshness", "evidence_grade",
    "source_policy_version", "source_record_sha256", "initial_m3_state",
)


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite(value: Any) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float)) and
            (not isinstance(value, float) or math.isfinite(value)))


def _nonempty_string(value: Any) -> bool:
    return type(value) is str and bool(value)


def source_record_sha256(value: Mapping[str, Any]) -> str:
    """Hash a source record without its self-declared hash field."""
    return canonical_sha256({key: item for key, item in value.items()
                             if key != "source_record_sha256"})


def admit_recorded_decision(value: Mapping[str, Any], *, split: str,
                            non_blind: bool,
                            artifact_sha256: str,
                            verified_artifact_sha256: str) -> dict[str, Any]:
    """Return a stable admission decision without mutating the source value."""
    reasons: list[str] = []
    if split not in LEGAL_SPLITS or non_blind is not True:
        reasons.append("PROVENANCE_NOT_CONFIRMED_NONBLIND_TRAIN_DEV")
    if artifact_sha256 != verified_artifact_sha256:
        reasons.append("SOURCE_ARTIFACT_HASH_MISMATCH")
    if not isinstance(value, Mapping):
        return {
            "schema_version": ADMISSION_SCHEMA,
            "admitted": False,
            "admission_result": STRUCTURED_ADMISSION_REJECTION,
            "reasons": ["SOURCE_RECORD_NOT_OBJECT"],
            "source_record_sha256": None,
        }
    for field in REQUIRED_SOURCE_FIELDS:
        if field not in value or value[field] is None:
            reasons.append("MISSING_" + field.upper())
    for field in ("decision_id", "source_observation_id", "source_frame_id",
                  "candidate_set_id", "source_policy_version"):
        if field in value and value[field] is not None and not _nonempty_string(value[field]):
            reasons.append("INVALID_" + field.upper())
    action = value.get("selected_action")
    if action not in LEGAL_ACTIONS:
        reasons.append("UNMAPPED_ACTION")
    for field in ("decision_monotonic_time", "decision_deadline_monotonic"):
        if field in value and value[field] is not None and not _finite(value[field]):
            reasons.append("INVALID_" + field.upper())
    answer_deadline = value.get("answer_deadline_monotonic")
    if answer_deadline is not None and not _finite(answer_deadline):
        reasons.append("INVALID_ANSWER_DEADLINE_MONOTONIC")
    query_applicable = (action == "ASK" or
                        (action == "WAIT" and
                         (value.get("query_identity") is not None or
                          value.get("query_episode_id") is not None)))
    if (query_applicable and
            (not _nonempty_string(value.get("query_identity")) or
             not _nonempty_string(value.get("query_episode_id")) or
             not _finite(answer_deadline))):
        reasons.append("MISSING_APPLICABLE_QUERY_IDENTITY_OR_DEADLINE")
    if value.get("candidate_freshness") not in {"FRESH", "STALE", "UNKNOWN"}:
        reasons.append("INVALID_CANDIDATE_FRESHNESS")
    if ("evidence_grade" in value and value.get("evidence_grade") is not None and
            value.get("evidence_grade") not in EVIDENCE_GRADES):
        reasons.append("INVALID_OR_MISSING_EVIDENCE_GRADE")
    supplied_record_sha = value.get("source_record_sha256")
    try:
        calculated_record_sha = source_record_sha256(value)
    except (TypeError, ValueError):
        calculated_record_sha = None
        reasons.append("SOURCE_RECORD_UNHASHABLE")
    if (supplied_record_sha is not None and calculated_record_sha is not None and
            supplied_record_sha != calculated_record_sha):
        reasons.append("SOURCE_RECORD_HASH_MISMATCH")
    initial_state = value.get("initial_m3_state")
    if initial_state is not None and not isinstance(initial_state, Mapping):
        reasons.append("INVALID_INITIAL_M3_STATE")
    reasons = sorted(set(reasons))
    lease = value.get("holding_lease_payload")
    return {
        "schema_version": ADMISSION_SCHEMA,
        "admitted": not reasons,
        "admission_result": ("ADMITTED_NONBLIND_" + split
                             if not reasons else STRUCTURED_ADMISSION_REJECTION),
        "reasons": reasons,
        "source_record_sha256": supplied_record_sha,
        "calculated_source_record_sha256": calculated_record_sha,
        "selected_action": action,
        "split": split,
        "wait_without_admissible_lease": action == "WAIT" and lease is None,
        "fabricated_fields": [],
    }


def legacy_m2b_projection(result: Mapping[str, Any]) -> dict[str, Any]:
    """Expose only literal legacy fields; absent M3 facts remain ``None``."""
    rule = result.get("rule_output", {})
    context = rule.get("context", {}) if isinstance(rule, Mapping) else {}
    recommendation = rule.get("recommendation", {}) if isinstance(rule, Mapping) else {}
    action = result.get("decision")
    query_identity = (recommendation.get("query_id") if action == "ASK"
                      else context.get("active_query_id") if action == "WAIT"
                      else None)
    projection = {
        "decision_id": context.get("decision_id"),
        "source_observation_id": context.get("source_observation_id"),
        "source_frame_id": None,
        "candidate_set_id": None,
        "selected_action": action,
        "selected_candidate_id": recommendation.get("selected_candidate_id"),
        "decision_monotonic_time": context.get("monotonic_now"),
        "decision_deadline_monotonic": None,
        "answer_deadline_monotonic": context.get("answer_deadline_monotonic"),
        "query_episode_id": query_identity,
        "query_identity": query_identity,
        "query_budget": context.get("query_budget"),
        "candidate_freshness": context.get("hard_gate_envelope", {}).get(
            "candidate_freshness_status"),
        "evidence_grade": None,
        "holding_lease_payload": None,
        "reason_codes": list(result.get("reason_codes", ())),
        "source_policy_version": recommendation.get("schema_version"),
        "initial_m3_state": None,
    }
    projection["source_record_sha256"] = source_record_sha256(projection)
    return projection


def audit_legacy_decision_results(document: Mapping[str, Any],
                                  split_by_unit: Mapping[str, str], *,
                                  artifact_sha256: str,
                                  verified_artifact_sha256: str,
                                  non_blind: bool = True) -> dict[str, Any]:
    results = document.get("results", ())
    if not isinstance(results, Sequence) or isinstance(results, (str, bytes)):
        raise ValueError("legacy results must be an array")
    decisions = []
    for index, result in enumerate(results):
        unit_key = result.get("m2b_unit_key") if isinstance(result, Mapping) else None
        split = split_by_unit.get(unit_key, "PROVENANCE_UNRESOLVED")
        projection = legacy_m2b_projection(result) if isinstance(result, Mapping) else result
        admission = admit_recorded_decision(
            projection, split=split, non_blind=non_blind,
            artifact_sha256=artifact_sha256,
            verified_artifact_sha256=verified_artifact_sha256)
        decisions.append({"source_index": index, "m2b_unit_key": unit_key, **admission})
    reason_counts = Counter(reason for row in decisions for reason in row["reasons"])
    action_counts = Counter(row.get("selected_action") for row in decisions)
    return {
        "schema_version": ADMISSION_SCHEMA,
        "source_record_count": len(decisions),
        "admitted_record_count": sum(row["admitted"] for row in decisions),
        "excluded_record_count": sum(not row["admitted"] for row in decisions),
        "source_action_distribution": dict(sorted(action_counts.items(), key=lambda item: str(item[0]))),
        "exclusion_reason_counts": dict(sorted(reason_counts.items())),
        "records": decisions,
    }


def deterministic_select(records: Iterable[Mapping[str, Any]],
                         maximum: int = 500) -> tuple[Mapping[str, Any], ...]:
    """Select independently of outcomes, with DEV before TRAIN and stable IDs."""
    if type(maximum) is not int or maximum <= 0:
        raise ValueError("maximum must be a positive integer")
    admitted = [row for row in records if row.get("admitted") is True]
    return tuple(sorted(admitted, key=lambda row: (
        0 if row.get("split") == "DEV" else 1,
        str(row.get("artifact_id", "")), str(row.get("decision_id", "")),
        str(row.get("source_record_sha256", ""))))[:maximum])


def deterministic_double_replay(episodes: Iterable[ReplayEpisode]) -> dict[str, Any]:
    """Run identical immutable episode inputs twice and compare all trace bytes."""
    episode_tuple = tuple(episodes)
    first = [run_episode(episode) for episode in episode_tuple]
    second = [run_episode(episode) for episode in episode_tuple]
    first_bytes = strict_json_dumps(first).encode("utf-8")
    second_bytes = strict_json_dumps(second).encode("utf-8")
    if any(isinstance(item, EpisodeRejection) for item in first + second):
        status = "REPLAY_REJECTION_PRESENT"
    else:
        status = "PASS" if first_bytes == second_bytes else "FAIL"
    return {
        "status": status,
        "episode_count": len(episode_tuple),
        "trace_byte_equality": first_bytes == second_bytes,
        "trace_sha_equality": hashlib.sha256(first_bytes).hexdigest() ==
                              hashlib.sha256(second_bytes).hexdigest(),
        "first_sha256": hashlib.sha256(first_bytes).hexdigest(),
        "second_sha256": hashlib.sha256(second_bytes).hexdigest(),
    }


def load_strict_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      parse_constant=lambda value: (_ for _ in ()).throw(
                          ValueError("nonfinite JSON constant: " + value)))
