"""Offline, non-controlling evidence--margin decision-rule evaluators."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from driveclarify_rq2_t.measurement import canonical_sha256

from .contracts import ALLOWED_USAGE, EVIDENCE_GRADE


RULES = ("R-EVIDENCE-ONLY", "R-TIME-ONLY", "R-JOINT", "R-ORACLE")
FIXED_TIME_ONLY_TTCMT_S = 3.0


def _first(rows: Sequence[Mapping[str, Any]], predicate) -> Mapping[str, Any] | None:
    return next((row for row in rows if predicate(row)), None)


def _point(row: Mapping[str, Any] | None, *, ttcmt_override: float | None = None) -> Mapping[str, Any] | None:
    if row is None:
        return None
    source = row["source_identity"]
    view = row["views"]["B2"]
    return {
        "source_frame_id": source["source_frame_id"],
        "source_observation_id": source["source_observation_id"],
        "simulation_time_s": source["simulation_time_s"],
        "TTCmt_s": float(view["TTCmt_s"] if ttcmt_override is None else ttcmt_override),
        "remaining_margin_s": float(view["remaining_decision_margin_s"]),
    }


def _base(rule: str, view: str | None) -> dict[str, Any]:
    return {
        "rule_id": rule, "evidence_view": view,
        "evidence_grade": EVIDENCE_GRADE, "allowed_usage": ALLOWED_USAGE,
        "offline_analysis_only": True, "production_eligible": False,
        "control_write_count": 0, "added_native_episode_count": 0,
        "added_vla_forward_count": 0,
    }


def evaluate_rules(records: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    rows = sorted(records, key=lambda row: (float(row["source_identity"]["simulation_time_s"]), int(row["source_identity"]["source_frame_id"])))
    if not rows:
        raise ValueError("RQ2_T_CG_RULE_TRACE_EMPTY")
    trace_digest = canonical_sha256([row["source_identity"] for row in rows])
    outputs: list[dict[str, Any]] = []

    for view_name in ("B1", "B2"):
        sufficient = _first(rows, lambda row: row["views"][view_name]["EpistemicEvidenceSufficient"] is True)
        timely = bool(sufficient and sufficient["views"][view_name]["ClarificationActionable"] is True)
        value = _base("R-EVIDENCE-ONLY", view_name)
        value.update({
            "trace_digest": trace_digest, "proposed_query": _point(sufficient),
            "classification": (
                "NO_TRIGGER_EVIDENCE_NEVER_SUFFICIENT" if sufficient is None
                else "SUPPORTED_TIMELY_TRIGGER" if timely
                else "TOO_LATE_RULE_TRIGGER"
            ),
            "reads_deadline_for_trigger": False, "reads_actionability_for_trigger": False,
            "first_sufficiency_reproduced_exactly": True,
        })
        outputs.append(value)

    # The trigger is the prospectively frozen TTCmt point.  With discrete
    # source rows, select the first observed row at/below the crossing while
    # preserving the exact 3.0-s rule coordinate in the output.
    time_row = _first(rows, lambda row: float(row["views"]["B2"]["TTCmt_s"]) <= FIXED_TIME_ONLY_TTCMT_S)
    time_value = _base("R-TIME-ONLY", None)
    at_time_sufficient = bool(time_row and time_row["views"]["B2"]["EpistemicEvidenceSufficient"])
    time_value.update({
        "trace_digest": trace_digest,
        "proposed_query": _point(time_row, ttcmt_override=FIXED_TIME_ONLY_TTCMT_S),
        "classification": (
            "FIXED_POINT_NOT_OBSERVED" if time_row is None
            else "SUPPORTED_TIMELY_TRIGGER" if at_time_sufficient
            else "PREMATURE_UNSUPPORTED_TRIGGER"
        ),
        "fixed_trigger_TTCmt_s": FIXED_TIME_ONLY_TTCMT_S,
        "reads_evidence_for_trigger": False, "reads_evidence_for_posthoc_classification_only": True,
    })
    outputs.append(time_value)

    for view_name in ("B1", "B2"):
        first_sufficient = _first(rows, lambda row: row["views"][view_name]["EpistemicEvidenceSufficient"] is True)
        joint = _first(rows, lambda row: row["views"][view_name]["EpistemicEvidenceSufficient"] is True and row["views"][view_name]["ClarificationActionable"] is True)
        value = _base("R-JOINT", view_name)
        value.update({
            "trace_digest": trace_digest, "proposed_query": _point(joint),
            "classification": (
                "SUPPORTED_TIMELY_TRIGGER" if joint is not None
                else "ACTIONABLE_WINDOW_MISSED_EVIDENCE_TOO_LATE" if first_sufficient is not None
                else "NO_TRIGGER_EVIDENCE_NEVER_SUFFICIENT"
            ),
            "never_triggers_after_deadline": joint is None or joint["views"][view_name]["remaining_decision_margin_s"] >= -1e-9,
        })
        outputs.append(value)

    oracle = _first(rows, lambda row: row["views"]["B3"]["EpistemicEvidenceSufficient"] is True and row["views"]["B3"]["ClarificationActionable"] is True)
    oracle_value = _base("R-ORACLE", "B3")
    oracle_value.update({
        "trace_digest": trace_digest, "proposed_query": _point(oracle),
        "classification": "ANALYSIS_UPPER_BOUND_POINT" if oracle else "NO_CERTIFIED_ORACLE_POINT",
        "nondeployable": True,
    })
    outputs.append(oracle_value)
    result = {
        "schema_version": "driveclarify.rq2_t_cg.rule_evaluation.v1",
        "trace_digest": trace_digest, "source_row_count": len(rows), "rules": outputs,
        "all_rules_identical_trace": all(value["trace_digest"] == trace_digest for value in outputs),
        "rule_added_native_episodes": 0, "rule_added_vla_forwards": 0,
        "vehicle_control_changes": 0,
    }
    result["rule_evaluation_digest"] = canonical_sha256(result)
    return result


__all__ = ["FIXED_TIME_ONLY_TTCMT_S", "RULES", "evaluate_rules"]
