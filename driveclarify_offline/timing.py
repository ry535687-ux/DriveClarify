"""严格不等式下的三值 ASK 时序计算。"""

from __future__ import annotations

import math
from typing import Any

from .models import AskTimingStatus, TimingResult


REQUIRED_TIMING_FIELDS: tuple[tuple[str, str], ...] = (
    ("time_to_decision_s", "UNKNOWN_DECISION_DEADLINE"),
    ("branch_forward_latency_s", "UNKNOWN_BRANCH_LATENCY"),
    ("question_latency_s", "UNKNOWN_QUESTION_LATENCY"),
    ("estimated_response_latency_s", "UNKNOWN_RESPONSE_LATENCY"),
    ("replanning_latency_s", "UNKNOWN_REPLAN_LATENCY"),
    ("safety_margin_s", "UNKNOWN_SAFETY_MARGIN"),
)
ALLOWED_OFFLINE_TIMING_SOURCES = frozenset({"SYNTHETIC_TEST_ONLY"})
TIMING_METADATA_FIELDS = (
    "timing_metadata.source",
    "timing_metadata.unit",
    "timing_metadata.trusted",
    "timing_metadata.fresh",
)


def _missing(value: object) -> bool:
    return value is None or value == "UNKNOWN"


def compute_ask_timing_status(
    timing: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> TimingResult:
    """按冻结优先级返回 FEASIBLE、INFEASIBLE_KNOWN 或 UNKNOWN。"""

    timing_fields = tuple(name for name, _ in REQUIRED_TIMING_FIELDS)
    fields_used = timing_fields + TIMING_METADATA_FIELDS

    # 离线 evaluator 不能从缺失 metadata 猜测来源、单位或新鲜度。
    if metadata is None:
        return TimingResult(
            AskTimingStatus.UNKNOWN,
            "TIMING_METADATA_MISSING",
            None,
            None,
            fields_used,
            ("timing_metadata",),
        )

    source = metadata.get("source")
    if source in (None, ""):
        return TimingResult(
            AskTimingStatus.UNKNOWN,
            "TIMING_SOURCE_MISSING",
            None,
            None,
            fields_used,
            ("timing_metadata.source",),
        )
    if source not in ALLOWED_OFFLINE_TIMING_SOURCES:
        return TimingResult(
            AskTimingStatus.UNKNOWN,
            "TIMING_SOURCE_UNKNOWN",
            None,
            None,
            fields_used,
            ("timing_metadata.source",),
        )
    if metadata.get("unit") != "s":
        return TimingResult(
            AskTimingStatus.UNKNOWN,
            "TIMING_UNIT_INVALID",
            None,
            None,
            fields_used,
            ("timing_metadata.unit",),
        )
    # 禁止 get(..., True)：可信度和新鲜度都必须显式为 true。
    if metadata.get("trusted") is not True:
        return TimingResult(
            AskTimingStatus.UNKNOWN,
            "TIMING_SOURCE_UNTRUSTED",
            None,
            None,
            fields_used,
            ("timing_metadata.trusted",),
        )
    if metadata.get("fresh") is not True:
        return TimingResult(
            AskTimingStatus.UNKNOWN,
            "TIMING_STALE",
            None,
            None,
            fields_used,
            ("timing_metadata.fresh",),
        )

    # 类型、NaN、无穷和负值属于 INVALID，不属于“已知不可行”。
    for name, _ in REQUIRED_TIMING_FIELDS:
        value = timing.get(name)
        if _missing(value):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return TimingResult(
                AskTimingStatus.UNKNOWN,
                "TIMING_INVALID",
                None,
                None,
                fields_used,
                (name,),
            )
        if not math.isfinite(float(value)) or float(value) < 0:
            return TimingResult(
                AskTimingStatus.UNKNOWN,
                "TIMING_INVALID",
                None,
                None,
                fields_used,
                (name,),
            )

    for name, reason_code in REQUIRED_TIMING_FIELDS:
        if _missing(timing.get(name)):
            return TimingResult(
                AskTimingStatus.UNKNOWN,
                reason_code,
                None,
                None,
                fields_used,
                (name,),
            )

    # consequence latency 按冻结合同由 slow budget 单独审计，不在此重复相加。
    t_decision = float(timing["time_to_decision_s"])
    latency_names = tuple(name for name, _ in REQUIRED_TIMING_FIELDS[1:])
    t_clarify = sum(float(timing[name]) for name in latency_names)
    slack = t_decision - t_clarify
    # 严格大于才可行；相等明确落入 INFEASIBLE_KNOWN。
    if t_decision > t_clarify:
        return TimingResult(
            AskTimingStatus.FEASIBLE,
            "TIMING_FEASIBLE",
            t_clarify,
            slack,
            fields_used,
            (),
        )
    return TimingResult(
        AskTimingStatus.INFEASIBLE_KNOWN,
        "ASK_TOO_LATE_OR_EQUAL",
        t_clarify,
        slack,
        fields_used,
        (),
    )


def apply_timing_result(record: dict[str, Any], result: TimingResult) -> dict[str, Any]:
    timing = record["timing"]
    timing["clarification_slack_s"] = result.slack_s
    timing["ask_timing_status"] = result.status.value
    timing["ask_timing_reason_code"] = result.reason_code
    return record
