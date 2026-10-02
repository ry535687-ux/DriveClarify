"""Frozen multi-axis Stage 6 metrics with explicit, non-shrinking denominators."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .contracts import (
    NORMAL_SIMLINGO_FORWARD_BUDGET,
    ContractError,
    GoldDecision,
    InteractionPhase,
    METHOD_ORDER,
    RuntimeAction,
    candidate_forward_budget,
)


_SPLITS = ("train", "dev", "test")
_FAILURE_TYPES = {
    "NONE",
    "COLLISION",
    "ROUTE_FAILURE",
    "RULE_VIOLATION",
    "OFF_ROAD",
    "TIMEOUT",
    "ENVIRONMENT_START_FAILURE",
    "CONTROL_FAILURE",
    "CANDIDATE_GENERATION_FAILURE",
    "COMPUTE_BUDGET_FAILURE",
    "AUTHORITY_FAILURE",
    "UNKNOWN",
}


@dataclass(frozen=True)
class EpisodeMetricRecord:
    episode_id: str
    method_id: str
    split: str
    started: bool
    terminal: bool
    predicted_action: RuntimeAction | None
    gold_decision: GoldDecision | None
    post_episode_gold_joined: bool
    route_completion: float | None
    goal_correct: bool | None
    instruction_success: bool | None
    collision: bool | None
    minimum_ttc_seconds: float | None
    near_miss: bool | None
    offroad: bool | None
    rule_violation: bool | None
    query_count: int | None
    decision_delay_seconds: float | None
    closed_loop_completed: bool | None
    failure_type: str | None
    interaction_phase: InteractionPhase | None = None
    compute_budget_case: str | None = None
    normal_model_forward_count: int | None = None
    candidate_model_forward_count: int | None = None
    normal_model_forward_latency_seconds: float | None = None
    candidate_model_forward_latencies_seconds: tuple[float, ...] | None = None
    decision_compute_latency_seconds: float | None = None
    candidate_forward_input_sha256: tuple[str, ...] | None = None
    candidate_forward_output_sha256: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not self.episode_id:
            raise ContractError("METRIC_EPISODE_ID_REQUIRED")
        if self.method_id not in METHOD_ORDER:
            raise ContractError(f"METRIC_UNKNOWN_METHOD:{self.method_id}")
        if self.split not in _SPLITS:
            raise ContractError(f"METRIC_INVALID_SPLIT:{self.split}")
        if self.post_episode_gold_joined != (self.gold_decision is not None):
            raise ContractError("GOLD_JOIN_FLAG_AND_VALUE_MISMATCH")
        if self.post_episode_gold_joined and not self.terminal:
            raise ContractError("GOLD_JOIN_BEFORE_TERMINAL_FORBIDDEN")
        if self.started is False and self.terminal is False:
            if self.failure_type not in {None, "ENVIRONMENT_START_FAILURE"}:
                raise ContractError("NONSTARTED_NONTERMINAL_FAILURE_TYPE_INVALID")
        if self.failure_type is not None and self.failure_type not in _FAILURE_TYPES:
            raise ContractError(f"UNKNOWN_FAILURE_TYPE:{self.failure_type}")
        if self.interaction_phase is not None and not isinstance(
            self.interaction_phase, InteractionPhase
        ):
            raise ContractError("METRIC_INTERACTION_PHASE_INVALID")
        if self.compute_budget_case is not None:
            candidate_forward_budget(self.method_id, self.compute_budget_case)
        if self.route_completion is not None:
            _finite_range(self.route_completion, "ROUTE_COMPLETION", 0.0, 1.0)
        if self.minimum_ttc_seconds is not None:
            _finite_range(self.minimum_ttc_seconds, "MINIMUM_TTC_SECONDS", 0.0, None)
        if self.decision_delay_seconds is not None:
            _finite_range(self.decision_delay_seconds, "DECISION_DELAY_SECONDS", 0.0, None)
        if self.normal_model_forward_latency_seconds is not None:
            _finite_range(
                self.normal_model_forward_latency_seconds,
                "NORMAL_MODEL_FORWARD_LATENCY_SECONDS",
                0.0,
                None,
            )
        if self.decision_compute_latency_seconds is not None:
            _finite_range(
                self.decision_compute_latency_seconds,
                "DECISION_COMPUTE_LATENCY_SECONDS",
                0.0,
                None,
            )
        if self.query_count is not None and (
            isinstance(self.query_count, bool)
            or not isinstance(self.query_count, int)
            or self.query_count < 0
        ):
            raise ContractError("QUERY_COUNT_NONNEGATIVE_INTEGER_REQUIRED")
        for name in ("normal_model_forward_count", "candidate_model_forward_count"):
            count = getattr(self, name)
            if count is not None and (
                isinstance(count, bool) or not isinstance(count, int) or count < 0
            ):
                raise ContractError(f"{name.upper()}_NONNEGATIVE_INTEGER_REQUIRED")
        if self.candidate_model_forward_latencies_seconds is not None:
            if not isinstance(self.candidate_model_forward_latencies_seconds, tuple):
                raise ContractError("CANDIDATE_FORWARD_LATENCIES_TUPLE_REQUIRED")
            for latency in self.candidate_model_forward_latencies_seconds:
                _finite_range(latency, "CANDIDATE_MODEL_FORWARD_LATENCY_SECONDS", 0.0, None)
        for name in (
            "candidate_forward_input_sha256",
            "candidate_forward_output_sha256",
        ):
            digests = getattr(self, name)
            if digests is not None:
                if not isinstance(digests, tuple) or any(
                    not _is_sha256(item) for item in digests
                ):
                    raise ContractError(f"{name.upper()}_INVALID")
        if self.candidate_model_forward_count is not None:
            for name in (
                "candidate_model_forward_latencies_seconds",
                "candidate_forward_input_sha256",
                "candidate_forward_output_sha256",
            ):
                evidence = getattr(self, name)
                if evidence is not None and len(evidence) != self.candidate_model_forward_count:
                    raise ContractError(
                        f"{name.upper()}_COUNT_MISMATCH"
                    )
        for name in (
            "goal_correct",
            "instruction_success",
            "collision",
            "near_miss",
            "offroad",
            "rule_violation",
            "closed_loop_completed",
        ):
            value = getattr(self, name)
            if value is not None and type(value) is not bool:
                raise ContractError(f"{name.upper()}_BOOLEAN_OR_NULL_REQUIRED")


def _finite_range(value: Any, name: str, low: float, high: float | None) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < low
        or (high is not None and float(value) > high)
    ):
        raise ContractError(f"{name}_OUT_OF_RANGE")


def _is_sha256(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _rate(
    population: Sequence[EpisodeMetricRecord], field: str, denominator_name: str
) -> dict[str, Any]:
    values = [getattr(item, field) for item in population]
    missing = sum(value is None for value in values)
    numerator = sum(value is True for value in values)
    denominator = len(population)
    return {
        "value": (
            None if denominator == 0 or missing else numerator / denominator
        ),
        "numerator": numerator,
        "denominator": denominator,
        "denominator_definition": denominator_name,
        "missing_count": missing,
        "reason": (
            "ZERO_FROZEN_DENOMINATOR"
            if denominator == 0
            else "UNKNOWN_REQUIRED_VALUES_IN_FROZEN_DENOMINATOR"
            if missing
            else None
        ),
    }


def _mean(
    population: Sequence[EpisodeMetricRecord], field: str, denominator_name: str
) -> dict[str, Any]:
    values = [getattr(item, field) for item in population]
    missing = sum(value is None for value in values)
    known = [float(value) for value in values if value is not None]
    denominator = len(population)
    return {
        "value": (
            None if denominator == 0 or missing else sum(known) / denominator
        ),
        "sum": sum(known),
        "denominator": denominator,
        "denominator_definition": denominator_name,
        "missing_count": missing,
        "reason": (
            "ZERO_FROZEN_DENOMINATOR"
            if denominator == 0
            else "UNKNOWN_REQUIRED_VALUES_IN_FROZEN_DENOMINATOR"
            if missing
            else None
        ),
    }


def _mean_values(values: Sequence[float | int | None], denominator_name: str) -> dict[str, Any]:
    missing = sum(value is None for value in values)
    known = [float(value) for value in values if value is not None]
    denominator = len(values)
    return {
        "value": None if denominator == 0 or missing else sum(known) / denominator,
        "sum": sum(known),
        "denominator": denominator,
        "denominator_definition": denominator_name,
        "missing_count": missing,
        "reason": (
            "ZERO_FROZEN_DENOMINATOR"
            if denominator == 0
            else "UNKNOWN_REQUIRED_VALUES_IN_FROZEN_DENOMINATOR"
            if missing
            else None
        ),
    }


def _rate_values(values: Sequence[bool | None], denominator_name: str) -> dict[str, Any]:
    missing = sum(value is None for value in values)
    numerator = sum(value is True for value in values)
    denominator = len(values)
    return {
        "value": None if denominator == 0 or missing else numerator / denominator,
        "numerator": numerator,
        "denominator": denominator,
        "denominator_definition": denominator_name,
        "missing_count": missing,
        "reason": (
            "ZERO_FROZEN_DENOMINATOR"
            if denominator == 0
            else "UNKNOWN_REQUIRED_VALUES_IN_FROZEN_DENOMINATOR"
            if missing
            else None
        ),
    }


def _expected_candidate_forward_count(item: EpisodeMetricRecord) -> int | None:
    if item.compute_budget_case is None:
        return None
    return candidate_forward_budget(item.method_id, item.compute_budget_case)


def _observed_total_forward_count(item: EpisodeMetricRecord) -> int | None:
    if (
        item.normal_model_forward_count is None
        or item.candidate_model_forward_count is None
    ):
        return None
    return item.normal_model_forward_count + item.candidate_model_forward_count


def _required_total_forward_count(item: EpisodeMetricRecord) -> int | None:
    candidate = _expected_candidate_forward_count(item)
    return None if candidate is None else NORMAL_SIMLINGO_FORWARD_BUDGET + candidate


def _candidate_forward_latency(item: EpisodeMetricRecord) -> float | None:
    values = item.candidate_model_forward_latencies_seconds
    return None if values is None else sum(values)


def _total_forward_latency(item: EpisodeMetricRecord) -> float | None:
    candidate = _candidate_forward_latency(item)
    if candidate is None or item.normal_model_forward_latency_seconds is None:
        return None
    return float(item.normal_model_forward_latency_seconds) + candidate


def _forward_count_budget_compliant(item: EpisodeMetricRecord) -> bool | None:
    expected_candidate = _expected_candidate_forward_count(item)
    if (
        expected_candidate is None
        or item.normal_model_forward_count is None
        or item.candidate_model_forward_count is None
    ):
        return None
    return bool(
        item.normal_model_forward_count == NORMAL_SIMLINGO_FORWARD_BUDGET
        and item.candidate_model_forward_count == expected_candidate
    )


def _forward_evidence_compliant(item: EpisodeMetricRecord) -> bool | None:
    count_compliant = _forward_count_budget_compliant(item)
    if count_compliant is False:
        return False
    if count_compliant is None:
        return None
    expected_candidate = _expected_candidate_forward_count(item)
    assert expected_candidate is not None
    if (
        item.normal_model_forward_latency_seconds is None
        or item.candidate_model_forward_latencies_seconds is None
        or item.candidate_forward_input_sha256 is None
        or item.candidate_forward_output_sha256 is None
        or item.decision_compute_latency_seconds is None
    ):
        return None
    if expected_candidate > 1 and len(set(item.candidate_forward_input_sha256)) != (
        expected_candidate
    ):
        return False
    return True


def _decision_metrics(records: Sequence[EpisodeMetricRecord]) -> dict[str, Any]:
    terminal = [item for item in records if item.terminal]
    joined = [
        item
        for item in terminal
        if item.post_episode_gold_joined and item.gold_decision is not None
    ]
    columns = [item.value for item in RuntimeAction]
    confusion = {
        gold.value: {prediction: 0 for prediction in columns} for gold in GoldDecision
    }
    missing_prediction_count = 0
    correct = 0
    for item in joined:
        if item.predicted_action is None:
            missing_prediction_count += 1
            continue
        assert item.gold_decision is not None
        confusion[item.gold_decision.value][item.predicted_action.value] += 1
        if item.predicted_action.value == item.gold_decision.value:
            correct += 1

    def recall(label: GoldDecision, output_name: str) -> dict[str, Any]:
        denominator = sum(
            1 for item in joined if item.gold_decision is label
        )
        numerator = confusion[label.value][label.value]
        missing = sum(
            1
            for item in joined
            if item.gold_decision is label and item.predicted_action is None
        )
        return {
            "metric": output_name,
            "value": (
                None if denominator == 0 or missing else numerator / denominator
            ),
            "numerator": numerator,
            "denominator": denominator,
            "denominator_definition": f"POST_EPISODE_GOLD_{label.value}_TERMINAL_EPISODES",
            "missing_prediction_count": missing,
        }

    overall_denominator = len(joined)
    return {
        "overall_accuracy": {
            "value": (
                None
                if overall_denominator == 0 or missing_prediction_count
                else correct / overall_denominator
            ),
            "numerator": correct,
            "denominator": overall_denominator,
            "denominator_definition": "ALL_TERMINAL_EPISODES_WITH_POST_EPISODE_GOLD_JOIN",
            "missing_prediction_count": missing_prediction_count,
        },
        "act_accuracy": recall(GoldDecision.ACT, "ACT_CLASS_ACCURACY"),
        "ask_recall": recall(GoldDecision.ASK, "ASK_RECALL"),
        "wait_recall": recall(GoldDecision.WAIT, "WAIT_RECALL"),
        "confusion_matrix": {
            "gold_rows": [item.value for item in GoldDecision],
            "prediction_columns": columns,
            "counts": confusion,
            "stop_and_fallback_mapped_to_scored_class": False,
        },
        "terminal_without_post_episode_gold_join_count": len(terminal) - len(joined),
    }


def _interaction_metrics(records: Sequence[EpisodeMetricRecord]) -> dict[str, Any]:
    started = [item for item in records if item.started]
    decision = [
        item for item in records if item.terminal and item.gold_decision is not None
    ]
    non_ask = [item for item in decision if item.gold_decision is not GoldDecision.ASK]
    ask = [item for item in decision if item.gold_decision is GoldDecision.ASK]
    unnecessary_numerator = sum(
        item.predicted_action is RuntimeAction.ASK for item in non_ask
    )
    missed_numerator = sum(
        item.predicted_action is not RuntimeAction.ASK for item in ask
    )
    query = _mean(started, "query_count", "ALL_STARTED_METHOD_EPISODES")
    query["total_query_count"] = query.pop("sum")
    delay = _mean(started, "decision_delay_seconds", "ALL_STARTED_METHOD_EPISODES")
    compute_denominator = "ALL_STARTED_METHOD_EPISODES"
    candidate_latency = _mean_values(
        [_candidate_forward_latency(item) for item in started], compute_denominator
    )
    candidate_latency["total_candidate_forward_latency_seconds"] = candidate_latency.pop(
        "sum"
    )
    total_latency = _mean_values(
        [_total_forward_latency(item) for item in started], compute_denominator
    )
    total_latency["total_model_forward_latency_seconds"] = total_latency.pop("sum")
    return {
        "unnecessary_ask_rate": {
            "value": (
                unnecessary_numerator / len(non_ask) if non_ask else None
            ),
            "numerator": unnecessary_numerator,
            "denominator": len(non_ask),
            "denominator_definition": "POST_EPISODE_GOLD_NON_ASK_TERMINAL_EPISODES",
        },
        "missed_ask_rate": {
            "value": missed_numerator / len(ask) if ask else None,
            "numerator": missed_numerator,
            "denominator": len(ask),
            "denominator_definition": "POST_EPISODE_GOLD_ASK_TERMINAL_EPISODES",
        },
        "query_count": query,
        "decision_delay_seconds": delay,
        "compute": {
            "required_normal_model_forward_count": NORMAL_SIMLINGO_FORWARD_BUDGET,
            "required_candidate_model_forward_count": _mean_values(
                [_expected_candidate_forward_count(item) for item in started],
                compute_denominator,
            ),
            "required_total_model_forward_count": _mean_values(
                [_required_total_forward_count(item) for item in started],
                compute_denominator,
            ),
            "observed_normal_model_forward_count": _mean_values(
                [item.normal_model_forward_count for item in started],
                compute_denominator,
            ),
            "observed_candidate_model_forward_count": _mean_values(
                [item.candidate_model_forward_count for item in started],
                compute_denominator,
            ),
            "observed_total_model_forward_count": _mean_values(
                [_observed_total_forward_count(item) for item in started],
                compute_denominator,
            ),
            "forward_count_budget_compliance_rate": _rate_values(
                [_forward_count_budget_compliant(item) for item in started],
                compute_denominator,
            ),
            "forward_and_latency_evidence_compliance_rate": _rate_values(
                [_forward_evidence_compliant(item) for item in started],
                compute_denominator,
            ),
            "normal_model_forward_latency_seconds": _mean_values(
                [item.normal_model_forward_latency_seconds for item in started],
                compute_denominator,
            ),
            "candidate_model_forward_latency_seconds": candidate_latency,
            "total_model_forward_latency_seconds": total_latency,
            "decision_compute_latency_seconds": _mean_values(
                [item.decision_compute_latency_seconds for item in started],
                compute_denominator,
            ),
            "candidate_plan_copy_as_forward_substitute_allowed": False,
            "distinct_candidate_input_sha256_required_when_count_gt_one": True,
        },
    }


def _summary(
    records: Sequence[EpisodeMetricRecord], scheduled_count: int
) -> dict[str, Any]:
    started = [item for item in records if item.started]
    terminal = [item for item in records if item.terminal]
    ttc = _mean(started, "minimum_ttc_seconds", "ALL_STARTED_METHOD_EPISODES")
    ttc_values = [item.minimum_ttc_seconds for item in started]
    ttc["minimum_observed_value"] = (
        None
        if not ttc_values or any(value is None for value in ttc_values)
        else min(float(value) for value in ttc_values if value is not None)
    )
    failure_counts = Counter(
        item.failure_type if item.failure_type is not None else "UNKNOWN"
        for item in records
    )
    missing_records = max(0, scheduled_count - len(records))
    if missing_records:
        failure_counts["MISSING_EPISODE_RECORD"] += missing_records
    completion_values = [item.closed_loop_completed for item in records]
    completion_missing = missing_records + sum(
        value is None for value in completion_values
    )
    completion_numerator = sum(value is True for value in completion_values)
    return {
        "population": {
            "scheduled_episodes": scheduled_count,
            "observed_records": len(records),
            "started_episodes": len(started),
            "terminal_episodes": len(terminal),
            "missing_episode_records": missing_records,
        },
        "task": {
            "route_completion": _mean(
                started, "route_completion", "ALL_STARTED_METHOD_EPISODES"
            ),
            "goal_correctness": _rate(
                started, "goal_correct", "ALL_STARTED_METHOD_EPISODES"
            ),
            "instruction_success": _rate(
                started, "instruction_success", "ALL_STARTED_METHOD_EPISODES"
            ),
        },
        "safety": {
            "collision_rate": _rate(
                started, "collision", "ALL_STARTED_METHOD_EPISODES"
            ),
            "ttc_seconds": ttc,
            "near_miss_rate": _rate(
                started, "near_miss", "ALL_STARTED_METHOD_EPISODES"
            ),
            "offroad_rate": _rate(
                started, "offroad", "ALL_STARTED_METHOD_EPISODES"
            ),
            "rule_violation_rate": _rate(
                started, "rule_violation", "ALL_STARTED_METHOD_EPISODES"
            ),
        },
        "decision": _decision_metrics(records),
        "interaction": _interaction_metrics(records),
        "closed_loop": {
            "completion_rate": {
                "value": (
                    None
                    if scheduled_count == 0 or completion_missing
                    else completion_numerator / scheduled_count
                ),
                "numerator": completion_numerator,
                "denominator": scheduled_count,
                "denominator_definition": "ALL_SCHEDULED_METHOD_EPISODES",
                "missing_count": completion_missing,
            },
            "failure_type_counts": dict(sorted(failure_counts.items())),
            "failure_type_denominator": scheduled_count,
        },
    }


def reduce_metrics(
    records: Iterable[EpisodeMetricRecord],
    *,
    schedule: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Reduce global, per-method, and per-split metrics without silent exclusion."""

    rows = list(records)
    if len({item.episode_id for item in rows}) != len(rows):
        raise ContractError("DUPLICATE_EPISODE_METRIC_RECORD")
    if schedule is None:
        scheduled_rows = [
            {"episode_id": item.episode_id, "method_id": item.method_id, "split": item.split}
            for item in rows
        ]
    else:
        scheduled_rows = schedule.get("episodes")
        if not isinstance(scheduled_rows, list):
            raise ContractError("SCHEDULE_EPISODE_LIST_REQUIRED")
    scheduled_ids = {str(item["episode_id"]) for item in scheduled_rows}
    if len(scheduled_ids) != len(scheduled_rows):
        raise ContractError("DUPLICATE_SCHEDULE_EPISODE_ID")
    scheduled_identity = {
        str(item["episode_id"]): (str(item.get("method_id")), str(item.get("split")))
        for item in scheduled_rows
    }
    unknown = sorted(item.episode_id for item in rows if item.episode_id not in scheduled_ids)
    if unknown:
        raise ContractError(f"METRIC_RECORD_NOT_IN_SCHEDULE:{unknown}")
    mismatched = sorted(
        item.episode_id
        for item in rows
        if scheduled_identity[item.episode_id] != (item.method_id, item.split)
    )
    if mismatched:
        raise ContractError(f"METRIC_RECORD_SCHEDULE_IDENTITY_MISMATCH:{mismatched}")
    global_summary = _summary(rows, len(scheduled_rows))
    per_method = {}
    for method_id in METHOD_ORDER:
        selected = [item for item in rows if item.method_id == method_id]
        scheduled_count = sum(
            str(item.get("method_id")) == method_id for item in scheduled_rows
        )
        per_method[method_id] = _summary(selected, scheduled_count)
    per_split = {}
    for split in _SPLITS:
        selected = [item for item in rows if item.split == split]
        scheduled_count = sum(str(item.get("split")) == split for item in scheduled_rows)
        per_split[split] = _summary(selected, scheduled_count)
    return {
        "schema_version": "driveclarify.paper_mvp_metrics.v2",
        "denominator_policy": (
            "FROZEN_NONSHRINKING;UNKNOWN_REQUIRED_VALUE_RETURNS_NULL_WITH_MISSING_COUNT"
        ),
        "global": global_summary,
        "per_method": per_method,
        "per_split": per_split,
    }
