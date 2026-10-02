"""词典序 ACT/ASK/WAIT/FALLBACK 规则级联。"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from .cache import check_cache, validate_cache_links
from .consequences import (
    compare_consequences,
    deterministic_tie_break,
    first_unknown_hard_reason,
    hard_feasibility,
)
from .models import (
    AskTimingStatus,
    Decision,
    DecisionRecord,
    HoldingSummary,
    PolicyState,
    TriValue,
    trace_step,
)
from .questions import check_template_question
from .schemas import validate_consequence
from .timing import apply_timing_result, compute_ask_timing_status


def _unique(values: list[str] | tuple[str, ...]) -> list[str]:
    return list(dict.fromkeys(values))


def validate_holding(
    holding: dict[str, Any] | None,
    now_monotonic_s: float,
) -> tuple[TriValue, str, list[str], list[str], HoldingSummary | None]:
    """校验 holding 并只为完整、仍有效的输入构造结构化摘要。"""

    fields = [
        "holding.holding_id",
        "holding.holding_type",
        "holding.version",
        "holding.hard_feasible",
        "holding.deadline_status",
        "holding.validity_status",
        "holding.valid_until_monotonic_s",
        "holding.can_extend_or_preserve",
        "holding.provenance",
    ]
    if holding is None:
        return TriValue.FALSE, "NO_SAFE_HOLDING", fields, [], None
    if holding.get("deadline_status") == "UNKNOWN":
        return (
            TriValue.UNKNOWN,
            "UNKNOWN_HOLDING_DEADLINE",
            fields,
            ["holding.deadline_status"],
            None,
        )
    if holding.get("validity_status") == "UNKNOWN":
        return (
            TriValue.UNKNOWN,
            "UNKNOWN_HOLDING_VALIDITY",
            fields,
            ["holding.validity_status"],
            None,
        )
    if holding.get("hard_feasible") == "UNKNOWN":
        return (
            TriValue.UNKNOWN,
            "UNKNOWN_HOLDING_SAFETY_OR_RULE",
            fields,
            ["holding.hard_feasible"],
            None,
        )
    required_text_identity = (
        "holding_id",
        "holding_type",
        "version",
        "provenance",
    )
    missing = [
        f"holding.{name}"
        for name in required_text_identity
        if not isinstance(holding.get(name), str) or not holding[name].strip()
    ]
    if holding.get("valid_until_monotonic_s") is None:
        missing.append("holding.valid_until_monotonic_s")
    if missing or not isinstance(holding.get("can_extend_or_preserve"), bool):
        return (
            TriValue.FALSE,
            "HOLDING_SUMMARY_INVALID",
            fields,
            missing or ["holding.can_extend_or_preserve"],
            None,
        )
    valid_until = holding["valid_until_monotonic_s"]
    if (
        isinstance(valid_until, bool)
        or not isinstance(valid_until, (int, float))
        or not math.isfinite(float(valid_until))
    ):
        return (
            TriValue.FALSE,
            "HOLDING_SUMMARY_INVALID",
            fields,
            ["holding.valid_until_monotonic_s"],
            None,
        )
    if (
        holding.get("deadline_status") != "KNOWN"
        or holding.get("validity_status") != "VALID"
        or holding.get("hard_feasible") is not True
        or now_monotonic_s >= float(valid_until)
    ):
        return TriValue.FALSE, "NO_SAFE_HOLDING", fields, [], None
    summary = HoldingSummary(
        holding_id=holding["holding_id"],
        holding_type=holding["holding_type"],
        version=holding["version"],
        validity_status=holding["validity_status"],
        deadline_status=holding["deadline_status"],
        valid_until_monotonic_s=float(valid_until),
        hard_feasible=True,
        can_extend_or_preserve=holding["can_extend_or_preserve"],
        provenance=holding["provenance"],
    )
    return TriValue.TRUE, "HOLDING_HARD_FEASIBLE", fields, [], summary


def _record(
    *,
    decision: Decision,
    reason_code: str,
    current_state: PolicyState,
    fields_used: list[str],
    unknown_fields: list[str],
    cache_status: str,
    timing_status: AskTimingStatus,
    trace: list[dict[str, Any]],
    selected_candidate_id: str | None = None,
    holding_summary: HoldingSummary | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> DecisionRecord:
    # 所有提前返回都经过此处，保证 trace 连续且输出字段一致。
    normalized_trace = [
        {**step, "index": index}
        for index, step in enumerate(trace, start=1)
    ]
    if decision is Decision.ACT:
        next_state = PolicyState.COMMITTED
        question_dispatch = "NONE"
        holding_required = False
    elif decision is Decision.ASK:
        next_state = PolicyState.QUESTION_SENT
        question_dispatch = "SEND_QUESTION"
        holding_required = True
    elif decision is Decision.WAIT:
        next_state = PolicyState.HOLDING
        question_dispatch = "NONE"
        holding_required = True
    else:
        next_state = PolicyState.FALLBACK
        question_dispatch = "NONE"
        holding_required = False
    return DecisionRecord(
        decision=decision,
        reason_code=reason_code,
        current_state=current_state,
        next_state=next_state,
        fields_used=_unique(fields_used),
        unknown_fields=_unique(unknown_fields),
        cache_status=cache_status,
        timing_status=timing_status,
        question_dispatch=question_dispatch,
        holding_required=holding_required,
        holding_summary=holding_summary,
        trace=normalized_trace,
        selected_candidate_id=selected_candidate_id,
        diagnostics=diagnostics or {},
    )


def evaluate_policy(case_input: dict[str, Any]) -> DecisionRecord:
    """无副作用地评估一条 Schema 完整的 synthetic record。"""
    current_state = PolicyState(case_input.get("current_state", "AMBIGUITY_ACTIVE"))
    active_episode_id = case_input["active_episode_id"]
    cache = deepcopy(case_input["candidate_cache"])
    consequences = deepcopy(case_input.get("consequences", []))
    now = float(case_input["now_monotonic_s"])
    trace: list[dict[str, Any]] = []
    fields_used: list[str] = ["active_episode_id", "now_monotonic_s"]
    unknown_fields: list[str] = []
    diagnostics = deepcopy(case_input.get("diagnostics", {}))

    # wall-clock 超限是最高优先级运行门，不能继续使用未完成结果。
    if case_input.get("slow_wall_clock_overrun") is True:
        trace.append(trace_step(1, "wall_clock_budget", "FAIL", "SLOW_WALL_CLOCK_OVERRUN"))
        return _record(
            decision=Decision.FALLBACK,
            reason_code="SLOW_WALL_CLOCK_OVERRUN",
            current_state=current_state,
            fields_used=fields_used + ["slow_wall_clock_overrun"],
            unknown_fields=[],
            cache_status=cache.get("validity_status", "UNKNOWN"),
            timing_status=AskTimingStatus.UNKNOWN,
            trace=trace,
            diagnostics=diagnostics,
        )

    if not consequences:
        trace.append(trace_step(1, "candidate_count", "FAIL", "NO_VALID_CANDIDATE"))
        return _record(
            decision=Decision.FALLBACK,
            reason_code="NO_VALID_CANDIDATE",
            current_state=current_state,
            fields_used=fields_used + ["consequences"],
            unknown_fields=[],
            cache_status=cache.get("validity_status", "UNKNOWN"),
            timing_status=AskTimingStatus.UNKNOWN,
            trace=trace,
            diagnostics=diagnostics,
        )
    trace.append(trace_step(1, "candidate_count", "PASS", f"K={len(consequences)}"))

    # timing 派生值写回副本后再过冻结 consequence Schema；不修改调用方输入。
    timing_result = compute_ask_timing_status(
        case_input["timing"],
        case_input.get("timing_metadata"),
    )
    for consequence in consequences:
        consequence["timing"].update(
            {
                key: case_input["timing"].get(key)
                for key in (
                    "branch_forward_latency_s",
                    "question_latency_s",
                    "estimated_response_latency_s",
                    "replanning_latency_s",
                    "safety_margin_s",
                    "time_to_decision_s",
                )
            }
        )
        apply_timing_result(consequence, timing_result)
        validate_consequence(consequence)
    fields_used.extend(timing_result.fields_used)

    # cache 执行门先于任何 ACT/ASK/WAIT 语义决策。
    cache_result = check_cache(cache, now, active_episode_id)
    fields_used.extend(cache_result.fields_used)
    unknown_fields.extend(cache_result.unknown_fields)
    if cache_result.executable is not TriValue.TRUE:
        trace.append(trace_step(2, "candidate_cache", "FAIL", cache_result.reason_code))
        return _record(
            decision=Decision.FALLBACK,
            reason_code=cache_result.reason_code,
            current_state=current_state,
            fields_used=fields_used,
            unknown_fields=unknown_fields,
            cache_status=cache_result.effective_status,
            timing_status=AskTimingStatus.UNKNOWN,
            trace=trace,
            diagnostics=diagnostics,
        )
    trace.append(trace_step(2, "candidate_cache", "PASS", "CACHE_EXECUTABLE"))

    links_valid, link_unknown = validate_cache_links(cache, consequences)
    fields_used.extend(
        [
            "candidate_cache.cache_id",
            "candidate_cache.episode_id",
            "candidate_cache.source_observation_id",
            "candidate_cache.candidate_ids",
            "consequence.cache_id",
            "consequence.episode_id",
            "consequence.observation_id",
            "consequence.candidate_id",
        ]
    )
    if not links_valid:
        trace.append(
            trace_step(3, "cache_consequence_links", "FAIL", "CACHE_CONSEQUENCE_ID_MISMATCH")
        )
        return _record(
            decision=Decision.FALLBACK,
            reason_code="CACHE_CONSEQUENCE_ID_MISMATCH",
            current_state=current_state,
            fields_used=fields_used,
            unknown_fields=list(link_unknown),
            cache_status=cache_result.effective_status,
            timing_status=AskTimingStatus.UNKNOWN,
            trace=trace,
            diagnostics=diagnostics,
        )
    trace.append(trace_step(3, "cache_consequence_links", "PASS", "CACHE_LINKS_VALID"))

    # UNKNOWN 硬字段必须在过滤已知 unsafe 候选之前保留并直接 fallback。
    unknown_reason, hard_unknown = first_unknown_hard_reason(consequences)
    fields_used.extend(["safety.safety_class", "rule.severe_rule_conflict"])
    if unknown_reason:
        unknown_fields.extend(hard_unknown)
        trace.append(trace_step(4, "hard_unknown_gate", "FAIL", unknown_reason))
        return _record(
            decision=Decision.FALLBACK,
            reason_code=unknown_reason,
            current_state=current_state,
            fields_used=fields_used,
            unknown_fields=unknown_fields,
            cache_status=cache_result.effective_status,
            timing_status=AskTimingStatus.UNKNOWN,
            trace=trace,
            diagnostics=diagnostics,
        )
    trace.append(trace_step(4, "hard_unknown_gate", "PASS", "HARD_FIELDS_KNOWN"))

    hard_feasible = [
        record
        for record in consequences
        if hard_feasibility(record)[0] is TriValue.TRUE
    ]
    if not hard_feasible:
        trace.append(
            trace_step(5, "hard_feasibility", "FAIL", "NO_HARD_FEASIBLE_CANDIDATE")
        )
        return _record(
            decision=Decision.FALLBACK,
            reason_code="NO_HARD_FEASIBLE_CANDIDATE",
            current_state=current_state,
            fields_used=fields_used,
            unknown_fields=unknown_fields,
            cache_status=cache_result.effective_status,
            timing_status=AskTimingStatus.UNKNOWN,
            trace=trace,
            diagnostics=diagnostics,
        )
    trace.append(
        trace_step(5, "hard_feasibility", "PASS", f"HARD_FEASIBLE={len(hard_feasible)}")
    )

    # Episode 交互状态属于所有语义动作的统一授权门。必须在 single/equivalent
    # early ACT、ASK 和 WAIT 之前验证，不能只在 decision-critical ASK 路径检查。
    history_signatures = case_input.get("history_signatures", [])
    budget = case_input.get("query_budget_remaining", 0)
    pending_question_value = case_input.get("pending_question", False)
    fields_used.extend(
        [
            "interaction_history.question_signatures",
            "query_budget_remaining",
            "pending_question",
        ]
    )
    inconsistent_query_state = (
        not isinstance(history_signatures, list)
        or isinstance(budget, bool)
        or not isinstance(budget, int)
        or budget not in (0, 1)
        or not isinstance(pending_question_value, bool)
        or (bool(history_signatures) and budget != 0)
        or (pending_question_value and not history_signatures)
    )
    if inconsistent_query_state:
        trace.append(
            trace_step(
                6,
                "query_episode_invariant",
                "FAIL",
                "QUERY_HISTORY_BUDGET_INCONSISTENT",
            )
        )
        return _record(
            decision=Decision.FALLBACK,
            reason_code="QUERY_HISTORY_BUDGET_INCONSISTENT",
            current_state=current_state,
            fields_used=fields_used,
            unknown_fields=unknown_fields,
            cache_status=cache_result.effective_status,
            timing_status=AskTimingStatus.UNKNOWN,
            trace=trace,
            diagnostics=diagnostics,
        )
    pending_question = pending_question_value
    trace.append(
        trace_step(
            6,
            "query_episode_invariant",
            "PASS",
            "QUERY_EPISODE_INVARIANT_VALID",
        )
    )

    unknown_fields.extend(timing_result.unknown_fields)
    if timing_result.status is AskTimingStatus.UNKNOWN:
        trace.append(trace_step(7, "ask_timing", "FAIL", timing_result.reason_code))
        return _record(
            decision=Decision.FALLBACK,
            reason_code=timing_result.reason_code,
            current_state=current_state,
            fields_used=fields_used,
            unknown_fields=unknown_fields,
            cache_status=cache_result.effective_status,
            timing_status=timing_result.status,
            trace=trace,
            diagnostics=diagnostics,
        )
    trace.append(trace_step(7, "ask_timing", "PASS", timing_result.reason_code))

    divergence = compare_consequences(hard_feasible)
    fields_used.extend(divergence.fields_used)
    diagnostics["consequence_divergence"] = divergence.to_dict()
    if "route_l2" in diagnostics:
        diagnostics["route_l2_role"] = "STRUCTURAL_DIAGNOSTIC_ONLY"

    # 单候选或后果等价可直接 ACT；route diagnostics 不参与该判定。
    if len(hard_feasible) == 1 or divergence.critical is TriValue.FALSE:
        selected = deterministic_tie_break(hard_feasible)
        trace.append(
            trace_step(8, "critical_consequence", "PASS", "ROBUST_OR_EQUIVALENT_ACT")
        )
        return _record(
            decision=Decision.ACT,
            reason_code="ROBUST_OR_EQUIVALENT_ACT",
            current_state=current_state,
            fields_used=fields_used,
            unknown_fields=unknown_fields,
            cache_status=cache_result.effective_status,
            timing_status=timing_result.status,
            trace=trace,
            selected_candidate_id=selected,
            diagnostics=diagnostics,
        )
    if divergence.critical is TriValue.UNKNOWN:
        unknown_fields.extend(divergence.unknown_fields)
        trace.append(
            trace_step(8, "critical_consequence", "FAIL", "UNKNOWN_CRITICAL_CONSEQUENCE")
        )
        return _record(
            decision=Decision.FALLBACK,
            reason_code="UNKNOWN_CRITICAL_CONSEQUENCE",
            current_state=current_state,
            fields_used=fields_used,
            unknown_fields=unknown_fields,
            cache_status=cache_result.effective_status,
            timing_status=timing_result.status,
            trace=trace,
            diagnostics=diagnostics,
        )
    trace.append(trace_step(8, "critical_consequence", "PASS", "CRITICAL_KNOWN_TRUE"))

    (
        holding_status,
        holding_reason,
        holding_fields,
        holding_unknown,
        holding_summary,
    ) = validate_holding(
        case_input.get("holding"),
        now,
    )
    fields_used.extend(holding_fields)
    unknown_fields.extend(holding_unknown)
    if holding_status is TriValue.UNKNOWN:
        trace.append(trace_step(9, "holding", "FAIL", holding_reason))
        return _record(
            decision=Decision.FALLBACK,
            reason_code=holding_reason,
            current_state=current_state,
            fields_used=fields_used,
            unknown_fields=unknown_fields,
            cache_status=cache_result.effective_status,
            timing_status=timing_result.status,
            trace=trace,
            diagnostics=diagnostics,
        )

    # 已有未决问题时只能 WAIT，不会再次发送语义问题。
    if pending_question:
        if holding_status is TriValue.TRUE:
            trace.append(trace_step(9, "post_question_wait", "PASS", "WAITING_FOR_ANSWER"))
            return _record(
                decision=Decision.WAIT,
                reason_code="WAITING_FOR_ANSWER",
                current_state=current_state,
                fields_used=fields_used,
                unknown_fields=unknown_fields,
                cache_status=cache_result.effective_status,
                timing_status=timing_result.status,
                trace=trace,
                holding_summary=holding_summary,
                diagnostics=diagnostics,
            )
        trace.append(trace_step(9, "post_question_wait", "FAIL", "NO_SAFE_HOLDING"))
        return _record(
            decision=Decision.FALLBACK,
            reason_code="NO_SAFE_HOLDING",
            current_state=current_state,
            fields_used=fields_used,
            unknown_fields=unknown_fields,
            cache_status=cache_result.effective_status,
            timing_status=timing_result.status,
            trace=trace,
            diagnostics=diagnostics,
        )

    # 只有 FEASIBLE 才检查 ASK；UNKNOWN 已在前面退出。
    if timing_result.status is AskTimingStatus.FEASIBLE:
        candidate_ids = [record["candidate_id"] for record in hard_feasible]
        question_check = check_template_question(
            case_input.get("question"),
            episode_id=active_episode_id,
            candidate_ids=candidate_ids,
            history_signatures=history_signatures,
        )
        fields_used.extend(question_check.fields_used)
        unknown_fields.extend(question_check.unknown_fields)
        if holding_status is not TriValue.TRUE:
            reason = "NO_SAFE_HOLDING"
        elif budget <= 0:
            reason = "QUERY_BUDGET_EXHAUSTED"
        elif not question_check.allowed:
            reason = question_check.reason_code
        else:
            ask_step = trace_step(9, "ask_gate", "PASS", "CRITICAL_ASK_TIMELY")
            # 发送成功后的预算变化直接进入确定性 trace，不能依赖外部猜测。
            ask_step.update(
                {
                    "query_budget_before": budget,
                    "query_budget_after": 0,
                    "successful_question_count_after": 1,
                }
            )
            trace.append(ask_step)
            return _record(
                decision=Decision.ASK,
                reason_code="CRITICAL_ASK_TIMELY",
                current_state=current_state,
                fields_used=fields_used,
                unknown_fields=unknown_fields,
                cache_status=cache_result.effective_status,
                timing_status=timing_result.status,
                trace=trace,
                holding_summary=holding_summary,
                diagnostics=diagnostics,
            )
        trace.append(trace_step(9, "ask_gate", "FAIL", reason))

    # 只有 INFEASIBLE_KNOWN 可以进入提问前 WAIT。
    if (
        timing_result.status is AskTimingStatus.INFEASIBLE_KNOWN
        and holding_status is TriValue.TRUE
        and case_input["holding"].get("can_extend_or_preserve") is True
    ):
        trace.append(
            trace_step(10, "pre_question_wait", "PASS", "ASK_TOO_LATE_HOLD_CAN_PRESERVE")
        )
        return _record(
            decision=Decision.WAIT,
            reason_code="ASK_TOO_LATE_HOLD_CAN_PRESERVE",
            current_state=current_state,
            fields_used=fields_used,
            unknown_fields=unknown_fields,
            cache_status=cache_result.effective_status,
            timing_status=timing_result.status,
            trace=trace,
            holding_summary=holding_summary,
            diagnostics=diagnostics,
        )

    robust_candidate = case_input.get("robust_candidate_id")
    hard_ids = {record["candidate_id"] for record in hard_feasible}
    if robust_candidate in hard_ids:
        trace.append(trace_step(11, "robust_fallback", "PASS", "ROBUST_FALLBACK_ACT"))
        return _record(
            decision=Decision.ACT,
            reason_code="ROBUST_FALLBACK_ACT",
            current_state=current_state,
            fields_used=fields_used + ["robust_candidate_id"],
            unknown_fields=unknown_fields,
            cache_status=cache_result.effective_status,
            timing_status=timing_result.status,
            trace=trace,
            selected_candidate_id=robust_candidate,
            diagnostics=diagnostics,
        )

    final_reason = (
        "NO_SAFE_HOLDING"
        if holding_status is TriValue.FALSE
        else "ASK_WAIT_AND_ROBUST_ACT_UNAVAILABLE"
    )
    trace.append(trace_step(11, "final_fallback", "FAIL", final_reason))
    return _record(
        decision=Decision.FALLBACK,
        reason_code=final_reason,
        current_state=current_state,
        fields_used=fields_used,
        unknown_fields=unknown_fields,
        cache_status=cache_result.effective_status,
        timing_status=timing_result.status,
        trace=trace,
        diagnostics=diagnostics,
    )
