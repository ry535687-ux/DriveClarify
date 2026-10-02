"""Prospective split protection and strong calibration-only T-FIXED selection.

The selector only reads post-episode calibration classification rows. It
cannot inspect a learned method, T-ACCUM, 2A/2B, task success, collisions, or
control output.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .measurement import canonical_sha256


SPLITS = (
    "PRIOR_SCIENTIFIC",
    "OLD_RQ2",
    "ENGINEERING",
    "CALIBRATION",
    "RQ2_T_2A",
    "RQ2_T_2B",
    "TEST",
)
PRIMARY_CLARIFICATION_DEADLINE_RESERVE_S = 1.20
T_FIXED_CANDIDATES_S = (1.5, 2.0, 2.5, 3.0)
T_FIXED_TOO_LATE_STRESS_CONTROL_S = 1.0
TIE_BREAKS = (
    "LOWER_UNNECESSARY_QUERY_RATE",
    "LARGER_TTCMT_EARLIER_FIXED_TRIGGER",
)
FORBIDDEN_CALIBRATION_FIELDS = frozenset(
    {
        "method_id",
        "policy_outcome",
        "t_accum",
        "t_accum_decision",
        "rq2_t_2a",
        "rq2_t_2b",
        "2a_outcome",
        "2b_outcome",
        "correct_goal_completion",
        "downstream_task_success",
        "wrong_goal_execution",
        "collision_outcome",
        "test_outcome",
    }
)


def calibration_protocol() -> dict[str, Any]:
    """Return the immutable protocol payload frozen before first exposure."""

    payload: dict[str, Any] = {
        "schema_version": "driveclarify.rq2_t.t_fixed_calibration_protocol.v2",
        "split": "CALIBRATION",
        "candidate_ttcmt_s": list(T_FIXED_CANDIDATES_S),
        "optional_too_late_stress_control_ttcmt_s": T_FIXED_TOO_LATE_STRESS_CONTROL_S,
        "primary_clarification_deadline_reserve_s": PRIMARY_CLARIFICATION_DEADLINE_RESERVE_S,
        "trigger_semantics": (
            "ISSUE_ONE_PRIMARY_QUERY_ON_FIRST_CARLA_TICK_WITH_TTCMT_LE_THRESHOLD_"
            "IFF_CLARIFICATION_ACTIONABLE;NEVER_QUERY_AFTER_PRIMARY_DEADLINE"
        ),
        "gold_owner": "INDEPENDENT_POST_EPISODE_GOLD_ADJUDICATOR",
        "gold_visibility": "POST_EPISODE_ANALYSIS_ONLY",
        "objective": "MAXIMIZE_TIMELY_NECESSARY_QUERY_F1",
        "first_tie_break": TIE_BREAKS[0],
        "second_tie_break": TIE_BREAKS[1],
        "minimum_episodes": 16,
        "target_episodes": 36,
        "maximum_episodes": 48,
        "planned_episode_count": 32,
        "planned_episode_count_reason": (
            "BALANCED_COMPLETE_8_SCENE_X_4_FRESH_SEED_BLOCK_BELOW_TARGET"
        ),
        "forbidden_inputs": sorted(FORBIDDEN_CALIBRATION_FIELDS),
        "outcome_exposure_started": False,
    }
    payload["protocol_digest"] = canonical_sha256(payload)
    return payload


def _walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            keys.add(str(key).lower())
            keys.update(_walk_keys(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            keys.update(_walk_keys(child))
    return keys


def validate_seed_registry(registry: Mapping[str, Sequence[int]]) -> dict[str, Any]:
    """Fail closed if any protected identity is duplicated across splits."""

    normalized: dict[str, set[int]] = {}
    for split in SPLITS:
        values = tuple(registry.get(split, ()))
        if any(not isinstance(value, int) or isinstance(value, bool) for value in values):
            raise ValueError("RQ2_T_SEED_REGISTRY_NONINTEGER:" + split)
        normalized[split] = set(values)
        if len(normalized[split]) != len(values):
            raise ValueError("RQ2_T_SEED_DUPLICATE_WITHIN_SPLIT:" + split)
    for index, left in enumerate(SPLITS):
        for right in SPLITS[index + 1 :]:
            if normalized[left].intersection(normalized[right]):
                raise ValueError("RQ2_T_SEED_SPLIT_OVERLAP:" + left + ":" + right)
    result = {
        "status": "PASS_MUTUALLY_PROTECTED",
        "split_counts": {key: len(normalized[key]) for key in SPLITS},
        "calibration_permanently_excluded_from": ["RQ2_T_2A", "RQ2_T_2B", "TEST"],
        "engineering_permanently_excluded_from": [
            "CALIBRATION",
            "RQ2_T_2A",
            "RQ2_T_2B",
            "TEST",
        ],
        "formal_2a_2b_seed_values_exposed": bool(
            normalized["RQ2_T_2A"] or normalized["RQ2_T_2B"]
        ),
    }
    result["registry_validation_digest"] = canonical_sha256(result)
    return result


def assert_protocol_immutable_after_exposure(
    protocol: Mapping[str, Any], *, frozen_protocol_digest: str, exposure_started: bool
) -> None:
    copy = dict(protocol)
    recorded = str(copy.pop("protocol_digest", ""))
    actual = canonical_sha256(copy)
    if recorded != actual:
        raise ValueError("RQ2_T_CALIBRATION_PROTOCOL_SELF_DIGEST_INVALID")
    if exposure_started and recorded != frozen_protocol_digest:
        raise PermissionError("RQ2_T_CALIBRATION_PROTOCOL_MUTATION_AFTER_EXPOSURE")


def _f1(tp: int, fp: int, fn: int) -> float:
    denominator = 2 * tp + fp + fn
    return 0.0 if denominator == 0 else (2.0 * tp) / denominator


def select_t_fixed_calibration_only(
    calibration_episodes: Sequence[Mapping[str, Any]],
    *,
    minimum_episode_count: int,
    frozen_protocol: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Select the strongest fixed timing using independent query gold only."""

    protocol = dict(frozen_protocol or calibration_protocol())
    protocol_digest = str(protocol.get("protocol_digest", ""))
    assert_protocol_immutable_after_exposure(
        protocol,
        frozen_protocol_digest=protocol_digest,
        exposure_started=True,
    )
    if len(calibration_episodes) < minimum_episode_count:
        raise ValueError("RQ2_T_T_FIXED_CALIBRATION_MINIMUM_NOT_MET")
    if any(row.get("split") != "CALIBRATION" for row in calibration_episodes):
        raise PermissionError("RQ2_T_T_FIXED_DEV_TEST_OR_OTHER_SPLIT_READ_FORBIDDEN")
    all_keys: set[str] = set()
    for row in calibration_episodes:
        all_keys.update(_walk_keys(row))
    forbidden = FORBIDDEN_CALIBRATION_FIELDS.intersection(all_keys)
    if forbidden:
        raise PermissionError(
            "RQ2_T_T_FIXED_POLICY_OUTCOME_FIELD_FORBIDDEN:" + sorted(forbidden)[0]
        )
    if any(
        str(row.get("protocol_digest", protocol_digest)) != protocol_digest
        for row in calibration_episodes
    ):
        raise PermissionError("RQ2_T_CALIBRATION_PROTOCOL_DIGEST_MISMATCH_AFTER_EXPOSURE")

    candidates = tuple(float(value) for value in protocol["candidate_ttcmt_s"])
    if any(value < PRIMARY_CLARIFICATION_DEADLINE_RESERVE_S for value in candidates):
        raise ValueError("RQ2_T_PRIMARY_T_FIXED_CANDIDATE_AFTER_DEADLINE")
    if T_FIXED_TOO_LATE_STRESS_CONTROL_S in candidates:
        raise ValueError("RQ2_T_1P0_PRIMARY_SELECTION_FORBIDDEN")

    scores: dict[str, dict[str, Any]] = {}
    eligible: list[float] = []
    for candidate in candidates:
        key = f"{candidate:.1f}"
        tp = fp = fn = tn = queries = late = 0
        for row in calibration_episodes:
            label = row.get("query_necessity_gold")
            evaluation = row.get("candidate_evaluations", {}).get(key)
            if label not in {"QUERY_NECESSARY", "QUERY_NOT_NECESSARY"}:
                raise ValueError("RQ2_T_CALIBRATION_GOLD_UNKNOWN_OR_INVALID")
            if not isinstance(evaluation, Mapping):
                raise ValueError("RQ2_T_CALIBRATION_CANDIDATE_EVALUATION_MISSING:" + key)
            issued = evaluation.get("query_issued") is True
            timely = evaluation.get("query_timely") is True
            deliberate_late = evaluation.get("deliberately_after_primary_deadline") is True
            late += int(deliberate_late)
            predicted = issued and timely and not deliberate_late
            positive = label == "QUERY_NECESSARY"
            queries += int(issued)
            tp += int(predicted and positive)
            fp += int(predicted and not positive)
            fn += int(not predicted and positive)
            tn += int(not predicted and not positive)
        score = {
            "timely_necessary_query_f1": _f1(tp, fp, fn),
            "unnecessary_query_rate": 0.0 if queries == 0 else fp / queries,
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "true_negative": tn,
            "query_count": queries,
            "deliberately_after_primary_deadline_count": late,
            "eligible": late == 0,
        }
        scores[key] = score
        if late == 0:
            eligible.append(candidate)
    if not eligible:
        raise ValueError("RQ2_T_T_FIXED_NO_DEADLINE_ELIGIBLE_CANDIDATE")
    selected = min(
        eligible,
        key=lambda value: (
            -scores[f"{value:.1f}"]["timely_necessary_query_f1"],
            scores[f"{value:.1f}"]["unnecessary_query_rate"],
            -value,
        ),
    )
    result = {
        "schema_version": "driveclarify.rq2_t.t_fixed_calibration_selection.v2",
        "selected_ttcmt_s": selected,
        "candidate_set_s": list(candidates),
        "optional_too_late_stress_control_ttcmt_s": T_FIXED_TOO_LATE_STRESS_CONTROL_S,
        "selection_metric": "MAXIMIZE_TIMELY_NECESSARY_QUERY_F1",
        "first_tie_break": TIE_BREAKS[0],
        "second_tie_break": TIE_BREAKS[1],
        "episode_count": len(calibration_episodes),
        "candidate_scores": scores,
        "protocol_digest": protocol_digest,
        "policy_outcomes_read": False,
        "t_accum_outcomes_read": False,
        "rq2_t_2a_2b_outcomes_read": False,
    }
    result["selection_digest"] = canonical_sha256(result)
    return result


__all__ = [
    "FORBIDDEN_CALIBRATION_FIELDS",
    "PRIMARY_CLARIFICATION_DEADLINE_RESERVE_S",
    "SPLITS",
    "T_FIXED_CANDIDATES_S",
    "T_FIXED_TOO_LATE_STRESS_CONTROL_S",
    "assert_protocol_immutable_after_exposure",
    "calibration_protocol",
    "select_t_fixed_calibration_only",
    "validate_seed_registry",
]
