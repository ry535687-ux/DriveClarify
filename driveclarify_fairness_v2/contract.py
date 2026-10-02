"""CounterfactualFairnessContractV2 的运行时中立实现。

本模块不读取 CARLA、ScenarioRunner、SimLingo 或模型对象。调用方必须先把两个候选的
证据快照持久化为普通 mapping；缺失证据保持 UNKNOWN，不能由默认值补成 PASS。
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping, Sequence


CONTRACT_SCHEMA = "driveclarify.counterfactual_fairness.v2"
PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"

STATE_IDENTITY_FIELDS = (
    "source_observation_id",
    "input_digest",
    "ego_state_digest",
    "route_state_digest",
    "actor_state_digest",
    "trigger_blackboard_digest",
    "frozen_world_frame",
    "rng_baseline_digest",
)

MODEL_FIELDS = (
    "model_instance_id",
    "model_parameter_digest_before",
    "model_parameter_digest_after",
    "model_eval_mode_before",
    "model_eval_mode_after",
    "history_state_digest_before",
    "history_state_digest_after",
    "cache_state_digest_before",
    "cache_state_digest_after",
)

CONTROL_COUNT_FIELDS = (
    "world_tick",
    "pid",
    "planner_advance",
    "control_send",
    "scenario_actor_mutation",
    "baseline_control_consumption",
)

_MISSING_SENTINELS = frozenset({"NOT_RECORDED", "MISSING", "UNKNOWN", "NOT_EVALUATED"})


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value in _MISSING_SENTINELS)


def _tri_state(missing: Sequence[str], mismatches: Sequence[str]) -> str:
    if mismatches:
        return FAIL
    if missing:
        return UNKNOWN
    return PASS


def _compare_fields(
    left: Mapping[str, Any], right: Mapping[str, Any], fields: Sequence[str]
) -> dict[str, Any]:
    missing: list[str] = []
    mismatches: list[str] = []
    for field in fields:
        left_value = left.get(field)
        right_value = right.get(field)
        if _is_missing(left_value):
            missing.append("candidate_a." + field)
        if _is_missing(right_value):
            missing.append("candidate_b." + field)
        if not _is_missing(left_value) and not _is_missing(right_value) and left_value != right_value:
            mismatches.append(field)
    verdict = _tri_state(missing, mismatches)
    reasons = ["STATE_IDENTITY_MISMATCH:" + field for field in mismatches]
    if missing:
        reasons.append("STATE_IDENTITY_EVIDENCE_MISSING")
    return {
        "verdict": verdict,
        "required_fields": list(fields),
        "missing_fields": sorted(missing),
        "mismatch_fields": sorted(mismatches),
        "reason_codes": sorted(reasons),
    }


def _model_stability(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    missing: list[str] = []
    mismatches: list[str] = []
    for prefix, candidate in (("candidate_a", left), ("candidate_b", right)):
        for field in MODEL_FIELDS:
            if _is_missing(candidate.get(field)):
                missing.append(prefix + "." + field)
        for stem in (
            "model_parameter_digest",
            "model_eval_mode",
            "history_state_digest",
            "cache_state_digest",
        ):
            before = candidate.get(stem + "_before")
            after = candidate.get(stem + "_after")
            if not _is_missing(before) and not _is_missing(after) and before != after:
                mismatches.append(prefix + "." + stem + "_CHANGED")

    for field in MODEL_FIELDS:
        left_value = left.get(field)
        right_value = right.get(field)
        if not _is_missing(left_value) and not _is_missing(right_value) and left_value != right_value:
            mismatches.append("CROSS_CANDIDATE_" + field)

    verdict = _tri_state(missing, mismatches)
    reasons = ["MODEL_OR_HISTORY_STATE_MISMATCH:" + field for field in mismatches]
    if missing:
        reasons.append("MODEL_OR_HISTORY_STATE_EVIDENCE_MISSING")
    return {
        "verdict": verdict,
        "required_fields": list(MODEL_FIELDS),
        "missing_fields": sorted(set(missing)),
        "mismatch_fields": sorted(set(mismatches)),
        "reason_codes": sorted(set(reasons)),
    }


def _control_noninterference(counts: Mapping[str, Any]) -> dict[str, Any]:
    missing: list[str] = []
    nonzero: list[str] = []
    invalid: list[str] = []
    for field in CONTROL_COUNT_FIELDS:
        value = counts.get(field)
        if _is_missing(value):
            missing.append(field)
        elif isinstance(value, bool) or not isinstance(value, int) or value < 0:
            invalid.append(field)
        elif value != 0:
            nonzero.append(field)
    failures = sorted(nonzero + invalid)
    verdict = _tri_state(missing, failures)
    reasons = ["CONTROL_OR_RUNTIME_INTERFERENCE:" + field for field in nonzero]
    reasons.extend("CONTROL_COUNT_INVALID:" + field for field in invalid)
    if missing:
        reasons.append("CONTROL_NONINTERFERENCE_EVIDENCE_MISSING")
    return {
        "verdict": verdict,
        "required_zero_count_fields": list(CONTROL_COUNT_FIELDS),
        "missing_fields": sorted(missing),
        "nonzero_fields": sorted(nonzero),
        "invalid_fields": sorted(invalid),
        "reason_codes": sorted(reasons),
    }


def _scenario_timing(evidence: Mapping[str, Any]) -> dict[str, Any]:
    lifecycle = evidence.get("scenario_tree_status")
    route_trigger = evidence.get("route_trigger_satisfied")
    physical_trigger = evidence.get("physical_event_triggered")
    actor_changed = evidence.get("actor_physical_state_changed")
    light_changed = evidence.get("traffic_light_state_changed")
    ego_influenced = evidence.get("ego_physically_influenced")
    post_event = evidence.get("post_event")
    world_frozen = evidence.get("world_frozen")

    if post_event is True:
        classification = "POST_EVENT"
        reasons = ["POST_EVENT_EXPLICIT"]
    elif any(value is True for value in (actor_changed, light_changed, ego_influenced)):
        classification = "ACTIVE_PHYSICALLY_INFLUENCED"
        reasons = ["EXPLICIT_PHYSICAL_INFLUENCE"]
    elif (
        route_trigger is False
        and physical_trigger is False
        and actor_changed is False
        and light_changed is False
        and ego_influenced is False
    ):
        classification = "PRETRIGGER"
        reasons = ["ACTIVATION_GATES_FALSE_AND_NO_PHYSICAL_CHANGE"]
    elif (
        world_frozen is True
        and lifecycle in {"ACTIVE", "RUNNING"}
        and actor_changed is False
        and light_changed is False
        and ego_influenced is False
    ):
        classification = "ACTIVE_FROZEN"
        reasons = ["BEHAVIOR_TREE_ACTIVE_BUT_SNAPSHOT_FROZEN_WITHOUT_RECORDED_PHYSICAL_INFLUENCE"]
    elif (
        world_frozen is True
        and (route_trigger is True or physical_trigger is True)
        and actor_changed is False
        and light_changed is False
        and ego_influenced is False
    ):
        classification = "ACTIVE_FROZEN"
        reasons = ["TRIGGERED_BUT_FROZEN_WITHOUT_RECORDED_PHYSICAL_INFLUENCE"]
    else:
        classification = "UNKNOWN"
        reasons = ["SCENARIO_TIMING_EVIDENCE_INCOMPLETE"]

    return {
        "classification": classification,
        "scenario_tree_status": lifecycle,
        "route_trigger_satisfied": route_trigger,
        "physical_event_triggered": physical_trigger,
        "actor_physical_state_changed": actor_changed,
        "traffic_light_state_changed": light_changed,
        "ego_physically_influenced": ego_influenced,
        "post_event": post_event,
        "world_frozen": world_frozen,
        "is_candidate_eligibility_gate": False,
        "reason_codes": reasons,
    }


def _semantic_isolation(
    left: Mapping[str, Any], right: Mapping[str, Any], observed_difference_fields: Any
) -> dict[str, Any]:
    missing: list[str] = []
    reasons: list[str] = []
    left_digest = left.get("candidate_semantic_payload_digest")
    right_digest = right.get("candidate_semantic_payload_digest")
    if _is_missing(left_digest):
        missing.append("candidate_a.candidate_semantic_payload_digest")
    if _is_missing(right_digest):
        missing.append("candidate_b.candidate_semantic_payload_digest")
    if not isinstance(observed_difference_fields, (list, tuple, set, frozenset)):
        missing.append("observed_difference_fields")
        differences: set[str] = set()
    else:
        differences = {str(item) for item in observed_difference_fields}

    failures: list[str] = []
    if not _is_missing(left_digest) and not _is_missing(right_digest) and left_digest == right_digest:
        failures.append("CANDIDATE_SEMANTIC_PAYLOAD_NOT_DIFFERENT")
    unexpected = sorted(differences - {"candidate_semantic_payload"})
    if unexpected:
        failures.extend("UNAUTHORIZED_CANDIDATE_DIFFERENCE:" + item for item in unexpected)
    if differences and "candidate_semantic_payload" not in differences:
        failures.append("CANDIDATE_SEMANTIC_PAYLOAD_NOT_DECLARED_AS_DIFFERENCE")
    if not differences and not missing:
        failures.append("NO_CANDIDATE_DIFFERENCE_DECLARED")
    verdict = _tri_state(missing, failures)
    reasons.extend(failures)
    if missing:
        reasons.append("CANDIDATE_SEMANTIC_ISOLATION_EVIDENCE_MISSING")
    return {
        "verdict": verdict,
        "allowed_difference_fields": ["candidate_semantic_payload"],
        "observed_difference_fields": sorted(differences),
        "missing_fields": sorted(missing),
        "unexpected_difference_fields": unexpected,
        "reason_codes": sorted(reasons),
    }


def _schedule_status(value: Any) -> dict[str, Any]:
    if value is True:
        verdict, reasons = PASS, []
    elif value is False:
        verdict, reasons = FAIL, ["CANDIDATE_SCHEDULE_OR_PROVENANCE_INVALID"]
    else:
        verdict, reasons = UNKNOWN, ["CANDIDATE_SCHEDULE_OR_PROVENANCE_MISSING"]
    return {"verdict": verdict, "valid": value, "reason_codes": reasons}


def evaluate_counterfactual_fairness(contract_input: Mapping[str, Any]) -> dict[str, Any]:
    """对一对候选快照作对称、确定性、fail-closed 的 v2 评价。"""

    frozen_input = copy.deepcopy(dict(contract_input))
    left_value = frozen_input.get("candidate_a")
    right_value = frozen_input.get("candidate_b")
    left = left_value if isinstance(left_value, Mapping) else {}
    right = right_value if isinstance(right_value, Mapping) else {}
    counts_value = frozen_input.get("operation_counts")
    counts = counts_value if isinstance(counts_value, Mapping) else {}
    timing_value = frozen_input.get("scenario_timing_evidence")
    timing = timing_value if isinstance(timing_value, Mapping) else {}

    state = _compare_fields(left, right, STATE_IDENTITY_FIELDS)
    model = _model_stability(left, right)
    control = _control_noninterference(counts)
    scenario = _scenario_timing(timing)
    semantic = _semantic_isolation(left, right, frozen_input.get("observed_difference_fields"))
    schedule = _schedule_status(frozen_input.get("candidate_schedule_provenance_valid"))

    gates = {
        "state_identity_fairness": state["verdict"],
        "control_noninterference": control["verdict"],
        "model_state_stability": model["verdict"],
        "candidate_semantic_isolation": semantic["verdict"],
        "candidate_schedule_provenance": schedule["verdict"],
    }
    if FAIL in gates.values():
        overall = FAIL
    elif UNKNOWN in gates.values():
        overall = UNKNOWN
    else:
        overall = PASS

    result = {
        "schema_version": CONTRACT_SCHEMA,
        "contract_name": "CounterfactualFairnessContractV2",
        "state_identity_fairness": state,
        "scenario_timing_suitability": scenario,
        "control_noninterference": control,
        "model_state_stability": model,
        "candidate_semantic_isolation": semantic,
        "candidate_schedule_provenance": schedule,
        "overall_candidate_comparison_eligibility": {
            "verdict": overall,
            "gate_verdicts": gates,
            "scenario_timing_is_not_an_automatic_gate": True,
            "reason_codes": sorted(
                reason
                for section in (state, control, model, semantic, schedule)
                for reason in section["reason_codes"]
            ),
        },
        "contract_input_digest": _digest(frozen_input),
    }
    result["evidence_sha256"] = _digest(result)
    return result
