"""Fixture loading, baselines, ablations, and evaluation-only scoring for M2B."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from driveclarify_language.interaction_contracts import (
    BindingStatus,
    CandidateSpecificTaskBinding,
    LongitudinalTaskTarget,
    LongitudinalTaskTargetType,
)

from .counterfactual_matrix import CounterfactualPlanEvidence, build_counterfactual_outcome_matrix
from .decision_contracts import (
    AnswerChannelModel,
    BeliefSource,
    BeliefStatus,
    CandidateOutcomeEvidence,
    Decision,
    DecisionContext,
    EVIDENCE_DESIGNATION,
    HardGateEnvelope,
    IntentBelief,
    LongitudinalTaskEvidence,
    LongitudinalTaskOutcome,
    LongitudinalTaskOutcomeEvidence,
    OperatingParameters,
    PhysicalSafetyEvidenceEnvelope,
    TaskOutcome,
    WaitMode,
    WaitOpportunityModel,
    assert_no_forbidden_runtime_keys,
    immutable_runtime_hash,
    stable_sha256,
)
from .query_value_policy import OfflineQueryValuePolicy, PolicyConfig


RUNTIME_FIXTURE_SCHEMA = "driveclarify.decision_runtime_fixture_set.v0"
EVALUATION_FIXTURE_SCHEMA = "driveclarify.decision_evaluation_fixture_set.v0"


def _deep_merge(base: Mapping[str, Any], update: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(base))
    for key, value in update.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _load_compact_fixture_set(path: str | Path, expected_schema: str, *, runtime: bool) -> list[dict[str, Any]]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if value.get("schema_version") != expected_schema:
        raise ValueError("BAD_DECISION_FIXTURE_SCHEMA")
    if runtime:
        assert_no_forbidden_runtime_keys(value)
        if value.get("provenance") != "HAND_AUTHORED_DECISION_FIXTURE":
            raise ValueError("DECISION_RUNTIME_FIXTURE_PROVENANCE_INVALID")
    else:
        if value.get("provenance") != "HAND_AUTHORED_DECISION_FIXTURE":
            raise ValueError("DECISION_EVALUATION_FIXTURE_PROVENANCE_INVALID")
        if value.get("declared_before_runtime_evaluation") is not True:
            raise ValueError("DECISION_EVALUATION_GOLD_NOT_PREDECLARED")
    defaults = value.get("defaults", {})
    profiles = value.get("profiles", {})
    episodes = value.get("episodes")
    if not isinstance(defaults, Mapping) or not isinstance(profiles, Mapping):
        raise ValueError("DECISION_FIXTURE_DEFAULTS_OR_PROFILES_INVALID")
    if not isinstance(episodes, list) or not episodes:
        raise ValueError("DECISION_FIXTURE_EPISODES_MISSING")
    records: list[dict[str, Any]] = []
    for compact in episodes:
        if not isinstance(compact, Mapping):
            raise ValueError("DECISION_FIXTURE_EPISODE_INVALID")
        profile_name = compact.get("profile")
        if profile_name is not None and profile_name not in profiles:
            raise ValueError(f"DECISION_FIXTURE_PROFILE_UNKNOWN:{profile_name}")
        profile = profiles.get(profile_name, {})
        if profile_name is not None and not isinstance(profile, Mapping):
            raise ValueError("DECISION_FIXTURE_PROFILE_INVALID")
        record = _deep_merge(defaults, profile)
        record = _deep_merge(record, compact.get("overrides", {}))
        for key, item in compact.items():
            if key not in {"profile", "overrides"}:
                record[key] = copy.deepcopy(item)
        records.append(record)
    return records


def load_runtime_fixture_set(path: str | Path) -> list[dict[str, Any]]:
    return _load_compact_fixture_set(path, RUNTIME_FIXTURE_SCHEMA, runtime=True)


def load_evaluation_fixture_set(path: str | Path) -> list[dict[str, Any]]:
    return _load_compact_fixture_set(path, EVALUATION_FIXTURE_SCHEMA, runtime=False)


def _binding(candidate_id: str, value: Mapping[str, Any]) -> CandidateSpecificTaskBinding:
    longitudinal_raw = value.get("longitudinal_task_target")
    longitudinal_target = None
    if isinstance(longitudinal_raw, Mapping):
        longitudinal_target = LongitudinalTaskTarget(
            target_type=LongitudinalTaskTargetType(longitudinal_raw["target_type"]),
            source_maneuver=str(longitudinal_raw["source_maneuver"]),
            source_instruction_id=str(longitudinal_raw["source_instruction_id"]),
            binding_source=str(longitudinal_raw["binding_source"]),
            provenance=tuple(longitudinal_raw.get("provenance", ())),
            schema_version=str(
                longitudinal_raw.get(
                    "schema_version", "driveclarify.language_interaction.v0"
                )
            ),
        )
    return CandidateSpecificTaskBinding(
        source_candidate_id=candidate_id,
        task_family=str(value.get("task_family", "REFERENCE_GOAL")),
        symbolic_target_type=str(value.get("symbolic_target_type", "REFERENCE_ENTITY_ID")),
        symbolic_target_id=value.get("symbolic_target_id"),
        required_slots=tuple(value.get("required_slots", ("reference_entity",))),
        binding_status=BindingStatus(value.get("binding_status", "BOUND")),
        binding_source=str(value.get("binding_source", "FIXTURE_DECLARED_ENTITY")),
        frame_or_semantic_domain=str(value.get("frame_or_semantic_domain", "LANGUAGE_REFERENCE")),
        provenance=tuple(value.get("provenance", ("HAND_AUTHORED_DECISION_FIXTURE",))),
        reason_codes=tuple(value.get("reason_codes", ("RUNTIME_HYPOTHESIS_BINDING",))),
        longitudinal_task_target=longitudinal_target,
    )


def _longitudinal_task_outcome(
    value: Mapping[str, Any],
) -> LongitudinalTaskOutcomeEvidence:
    return LongitudinalTaskOutcomeEvidence(
        action_candidate_id=str(value["action_candidate_id"]),
        hypothesis_candidate_id=str(value["hypothesis_candidate_id"]),
        target_type=(
            None if value.get("target_type") is None else str(value["target_type"])
        ),
        outcome=LongitudinalTaskOutcome(value["outcome"]),
        metric_identity=(
            None
            if value.get("metric_identity") is None
            else str(value["metric_identity"])
        ),
        evidence_status=str(value["evidence_status"]),
        raw_speed_digest=str(value["raw_speed_digest"]),
        source=str(value["source"]),
        evidence_grade=str(value["evidence_grade"]),
        allowed_usage_purposes=tuple(value.get("allowed_usage_purposes", ())),
        provenance=tuple(value.get("provenance", ())),
        authorization_eligible=bool(value.get("authorization_eligible", False)),
        safety_critical_eligible=bool(
            value.get("safety_critical_eligible", False)
        ),
        reason_codes=tuple(value.get("reason_codes", ())),
        schema_version=str(
            value.get(
                "schema_version",
                "driveclarify.longitudinal_task_outcome_evidence.v0",
            )
        ),
    )


def _plan(candidate_id: str, value: Mapping[str, Any]) -> CounterfactualPlanEvidence:
    longitudinal_raw = value.get("longitudinal_task_evidence")
    longitudinal = None
    if isinstance(longitudinal_raw, Mapping):
        semantic_value = longitudinal_raw.get("semantic_value")
        longitudinal = LongitudinalTaskEvidence(
            action_candidate_id=str(
                longitudinal_raw.get("action_candidate_id", candidate_id)
            ),
            status=str(
                longitudinal_raw.get("status", "NOT_CURRENTLY_AVAILABLE")
            ),
            semantic_type=longitudinal_raw.get("semantic_type"),
            semantic_value=(
                None if semantic_value is None else float(semantic_value)
            ),
            frame=longitudinal_raw.get("frame"),
            unit=longitudinal_raw.get("unit"),
            temporal_basis=longitudinal_raw.get("temporal_basis"),
            source=str(longitudinal_raw.get("source", "")),
            evidence_grade=str(longitudinal_raw.get("evidence_grade", "NOT_AVAILABLE")),
            allowed_usage_purposes=tuple(
                longitudinal_raw.get("allowed_usage_purposes", ())
            ),
            provenance=tuple(longitudinal_raw.get("provenance", ())),
            authorization_eligible=bool(
                longitudinal_raw.get("authorization_eligible", False)
            ),
            safety_critical_eligible=bool(
                longitudinal_raw.get("safety_critical_eligible", False)
            ),
            reason_codes=tuple(longitudinal_raw.get("reason_codes", ())),
            schema_version=str(
                longitudinal_raw.get(
                    "schema_version", "driveclarify.longitudinal_task_evidence.v0"
                )
            ),
        )
    return CounterfactualPlanEvidence(
        action_candidate_id=candidate_id,
        source_observation_id=value.get("source_observation_id"),
        mapped_symbolic_target_type=value.get("mapped_symbolic_target_type"),
        mapped_symbolic_target_id=value.get("mapped_symbolic_target_id"),
        plan_frame=value.get("plan_frame"),
        plan_unit=value.get("plan_unit"),
        provenance=tuple(value.get("provenance", ("HAND_AUTHORED_DECISION_FIXTURE",))),
        source_artifacts=tuple(value.get("source_artifacts", ("OFFLINE_FIXTURE",))),
        reason_codes=tuple(value.get("reason_codes", ())),
        longitudinal_task_evidence=longitudinal,
    )


def context_from_runtime_record(value: Mapping[str, Any]) -> DecisionContext:
    """Construct the runtime contract without accepting any evaluation-only object."""

    assert_no_forbidden_runtime_keys(value)
    candidate_ids = tuple(str(item) for item in value["candidate_ids"])
    plan_values = value.get("symbolic_plans", {})
    binding_values = value.get("hypothesis_bindings", {})
    plans = {
        candidate_id: _plan(candidate_id, raw)
        for candidate_id, raw in plan_values.items()
        if isinstance(raw, Mapping)
    }
    bindings = {
        candidate_id: _binding(candidate_id, raw)
        for candidate_id, raw in binding_values.items()
        if isinstance(raw, Mapping)
    }
    longitudinal_outcome_values = value.get("longitudinal_task_outcomes", ())
    if not isinstance(longitudinal_outcome_values, (list, tuple)):
        raise ValueError("LONGITUDINAL_TASK_OUTCOMES_INVALID")
    longitudinal_outcomes = {
        (outcome.action_candidate_id, outcome.hypothesis_candidate_id): outcome
        for raw in longitudinal_outcome_values
        if isinstance(raw, Mapping)
        for outcome in (_longitudinal_task_outcome(raw),)
    }
    if len(longitudinal_outcomes) != len(longitudinal_outcome_values):
        raise ValueError("LONGITUDINAL_TASK_OUTCOME_IDENTITIES_DUPLICATED_OR_INVALID")
    matrix = build_counterfactual_outcome_matrix(
        candidate_ids,
        plans,
        bindings,
        {str(key): float(item) for key, item in value.get("declared_wrong_goal_costs", {}).items()},
        longitudinal_task_outcomes=longitudinal_outcomes,
        provenance=("HAND_AUTHORED_DECISION_FIXTURE",),
    )
    candidate_outcomes = tuple(
        CandidateOutcomeEvidence(
            candidate_id=candidate_id,
            task_outcome=(
                matrix.cell(candidate_id, candidate_id).task_outcome
                if matrix.cell(candidate_id, candidate_id) is not None
                else TaskOutcome.UNKNOWN
            ),
            evidence_status=(
                matrix.cell(candidate_id, candidate_id).evidence_status
                if matrix.cell(candidate_id, candidate_id) is not None
                else "INSUFFICIENT"
            ),
            provenance=("SYMBOLIC_COUNTERFACTUAL_OUTCOME",),
            reason_codes=("DIAGONAL_OWN_HYPOTHESIS_OUTCOME",),
        )
        for candidate_id in candidate_ids
    )
    belief_raw = value["intent_belief"]
    belief = IntentBelief(
        candidate_probabilities=tuple(
            (str(candidate_id), float(probability))
            for candidate_id, probability in belief_raw["candidate_probabilities"]
        ),
        belief_status=BeliefStatus(belief_raw.get("belief_status", "AVAILABLE")),
        belief_source=BeliefSource(belief_raw["belief_source"]),
        normalization_status=str(belief_raw.get("normalization_status", "NORMALIZED")),
        provenance=tuple(belief_raw.get("provenance", ("HAND_AUTHORED_DECISION_FIXTURE",))),
        reason_codes=tuple(belief_raw.get("reason_codes", ())),
    )
    channel_raw = value["answer_channel"]
    channel = AnswerChannelModel(
        answer_resolution_probability=float(channel_raw["answer_resolution_probability"]),
        no_answer_probability=float(channel_raw["no_answer_probability"]),
        answer_confusion_matrix=tuple(
            (
                str(candidate_id),
                tuple((str(label).upper(), float(probability)) for label, probability in row),
            )
            for candidate_id, row in channel_raw["answer_confusion_matrix"]
        ),
        delay_distribution=tuple(
            (float(delay), float(probability)) for delay, probability in channel_raw["delay_distribution"]
        ),
        channel_status=str(channel_raw.get("channel_status", "AVAILABLE")),
        source=str(channel_raw.get("source", "HAND_AUTHORED_CHANNEL")),
        provenance=tuple(channel_raw.get("provenance", ("SYNTHETIC_QUERY_CHANNEL_TEST_ONLY",))),
        reason_codes=tuple(channel_raw.get("reason_codes", ())),
    )
    wait_raw = value["wait_opportunity"]
    wait = WaitOpportunityModel(
        wait_mode=WaitMode(wait_raw["wait_mode"]),
        future_information_expected=bool(wait_raw["future_information_expected"]),
        expected_information_arrival_time=(
            None
            if wait_raw.get("expected_information_arrival_time") is None
            else float(wait_raw["expected_information_arrival_time"])
        ),
        active_query_pending=bool(wait_raw["active_query_pending"]),
        holding_capability_status=str(wait_raw["holding_capability_status"]),
        decision_deadline_status=str(wait_raw["decision_deadline_status"]),
        missed_opportunity_cost=float(wait_raw["missed_opportunity_cost"]),
        wait_cost=float(wait_raw["wait_cost"]),
        information_resolution_probability=float(wait_raw["information_resolution_probability"]),
        provenance=tuple(wait_raw.get("provenance", ("HAND_AUTHORED_DECISION_FIXTURE",))),
        reason_codes=tuple(wait_raw.get("reason_codes", ())),
        wait_reason=(
            None if wait_raw.get("wait_reason") is None else str(wait_raw["wait_reason"])
        ),
        closed_loop_behavior=(
            None
            if wait_raw.get("closed_loop_behavior") is None
            else str(wait_raw["closed_loop_behavior"])
        ),
        emergency_stop_semantics=bool(
            wait_raw.get("emergency_stop_semantics", False)
        ),
        low_level_controller_owner=(
            None
            if wait_raw.get("low_level_controller_owner") is None
            else str(wait_raw["low_level_controller_owner"])
        ),
    )
    gate_raw = value["hard_gate_envelope"]
    safety_evidence_raw = gate_raw.get("physical_safety_evidence")
    physical_safety_evidence = None
    if safety_evidence_raw is not None:
        if not isinstance(safety_evidence_raw, Mapping):
            raise ValueError("PHYSICAL_SAFETY_EVIDENCE_ENVELOPE_INVALID")
        physical_safety_evidence = PhysicalSafetyEvidenceEnvelope(
            safety_status=str(safety_evidence_raw["safety_status"]),
            availability=str(safety_evidence_raw["availability"]),
            evidence_grade=str(safety_evidence_raw["evidence_grade"]),
            source=str(safety_evidence_raw["source"]),
            source_kind=str(safety_evidence_raw["source_kind"]),
            source_observation_id=str(
                safety_evidence_raw["source_observation_id"]
            ),
            source_frame_id=(
                None
                if safety_evidence_raw.get("source_frame_id") is None
                else str(safety_evidence_raw["source_frame_id"])
            ),
            observed_monotonic_time=float(
                safety_evidence_raw["observed_monotonic_time"]
            ),
            usage_purpose=str(safety_evidence_raw["usage_purpose"]),
            safety_critical_eligible=bool(
                safety_evidence_raw["safety_critical_eligible"]
            ),
            reason_codes=tuple(safety_evidence_raw.get("reason_codes", ())),
        )
    hard_gate = HardGateEnvelope(
        hard_safety_status=str(gate_raw["hard_safety_status"]),
        hard_rule_status=str(gate_raw["hard_rule_status"]),
        evidence_gate_status=str(gate_raw["evidence_gate_status"]),
        query_episode_status=str(gate_raw["query_episode_status"]),
        cache_status=str(gate_raw["cache_status"]),
        candidate_freshness_status=str(gate_raw["candidate_freshness_status"]),
        control_authorized=False,
        reason_codes=tuple(gate_raw.get("reason_codes", ())),
        physical_safety_evidence=physical_safety_evidence,
    )
    operating_raw = value["operating_parameters"]
    operating = OperatingParameters(
        query_cost=float(operating_raw["query_cost"]),
        delay_cost_per_second=float(operating_raw["delay_cost_per_second"]),
        no_answer_penalty=float(operating_raw["no_answer_penalty"]),
        unknown_task_loss_policy=str(operating_raw["unknown_task_loss_policy"]),
        strict_value_epsilon=float(operating_raw.get("strict_value_epsilon", 1e-9)),
        provenance=tuple(operating_raw.get("provenance", ("HAND_AUTHORED_DECISION_FIXTURE",))),
    )
    evidence_raw = value.get("evidence_masks", {})
    return DecisionContext(
        decision_id=str(value["decision_id"]),
        episode_id=str(value["episode_id"]),
        source_observation_id=value.get("source_observation_id"),
        candidate_ids=candidate_ids,
        candidate_pair_relation=str(value["candidate_pair_relation"]),
        candidate_outcomes=candidate_outcomes,
        counterfactual_outcome_matrix=matrix,
        intent_belief=belief,
        query_proposal=copy.deepcopy(value.get("query_proposal")),
        answer_channel=channel,
        query_budget=int(value["query_budget"]),
        active_query_id=value.get("active_query_id"),
        monotonic_now=float(value["monotonic_now"]),
        answer_deadline_monotonic=(
            None
            if value.get("answer_deadline_monotonic") is None
            else float(value["answer_deadline_monotonic"])
        ),
        time_to_decision_status=str(value["time_to_decision_status"]),
        wait_opportunity=wait,
        hard_gate_envelope=hard_gate,
        evidence_masks=tuple(sorted(dict(evidence_raw).items())),
        operating_parameters=operating,
        provenance=tuple(value.get("provenance", ("HAND_AUTHORED_DECISION_FIXTURE",))),
    )


def run_runtime_record(value: Mapping[str, Any], config: PolicyConfig | None = None) -> dict[str, Any]:
    context = context_from_runtime_record(value)
    recommendation = OfflineQueryValuePolicy(config).recommend(context)
    output = {
        "schema_version": "driveclarify.offline_query_value_runtime_output.v0",
        "context": context.to_dict(),
        "recommendation": recommendation.to_dict(),
        "deterministic_runtime_sha256": immutable_runtime_hash(context, recommendation),
        "override_applied": False,
        "used_for_control": False,
        "authorization_eligible": False,
        "control_authorized": False,
        "live_ask_issued": False,
        "live_wait_controller_invoked": False,
        "vehicle_control_generated": False,
        "fallback_is_emergency_stop": False,
        "torch_loaded": "torch" in sys.modules,
        "cuda_initialized": False,
        "carla_launch_count": 0,
        "evaluator_launch_count": 0,
        "simlingo_checkpoint_or_model_load_count": 0,
    }
    assert_no_forbidden_runtime_keys(output)
    return output


def _safe_div(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _classification_metrics(rows: Sequence[tuple[str, str]]) -> dict[str, Any]:
    labels = [item.value for item in Decision]
    per_class: dict[str, dict[str, float | int]] = {}
    f1s: list[float] = []
    for label in labels:
        tp = sum(expected == actual == label for expected, actual in rows)
        fp = sum(expected != label and actual == label for expected, actual in rows)
        fn = sum(expected == label and actual != label for expected, actual in rows)
        precision = _safe_div(tp, tp + fp)
        recall = _safe_div(tp, tp + fn)
        f1 = _safe_div(2 * precision * recall, precision + recall)
        f1s.append(f1)
        per_class[label] = {
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
            "support": sum(expected == label for expected, _ in rows),
        }
    return {"macro_f1": round(sum(f1s) / len(f1s), 6), "per_class": per_class}


def _baseline_decision(name: str, context: DecisionContext) -> str:
    probabilities = context.intent_belief.as_dict()
    top = max(context.candidate_ids, key=lambda item: probabilities[item])
    if name == "ALWAYS_ACT_TOP1":
        return Decision.ACT.value
    if name == "ALWAYS_ASK":
        return Decision.ASK.value
    if name == "ALWAYS_WAIT":
        return Decision.WAIT.value
    if name == "AMBIGUITY_DETECTION_ONLY":
        return Decision.ASK.value if len(context.candidate_ids) > 1 else Decision.ACT.value
    if name == "PAIR_RELATION_ONLY":
        return Decision.ACT.value if context.candidate_pair_relation == "TASK_EQUIVALENT" else Decision.ASK.value
    if name == "LANGUAGE_UNCERTAINTY_THRESHOLD":
        return Decision.ASK.value if probabilities[top] < 0.75 else Decision.ACT.value
    raise ValueError(f"UNKNOWN_BASELINE:{name}")


def _decision_total(output: Mapping[str, Any]) -> float | None:
    recommendation = output["recommendation"]
    decision = recommendation["decision"]
    selected = recommendation.get("selected_candidate_id")
    key = f"ACT:{selected}" if decision == "ACT" and selected else decision
    for name, channels in recommendation.get("expected_loss_channels", []):
        if name == key:
            return channels.get("total")
    if decision == "ACT" and recommendation.get("act_target_type") == "EQUIVALENCE_CLASS":
        totals = [
            channels.get("total")
            for name, channels in recommendation.get("expected_loss_channels", [])
            if name.startswith("ACT:")
        ]
        return min(item for item in totals if item is not None) if totals else None
    return None


def _evaluate_policy_records(
    runtime_records: Sequence[Mapping[str, Any]],
    evaluation_by_id: Mapping[str, Mapping[str, Any]],
    config: PolicyConfig | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    classification: list[tuple[str, str]] = []
    repeats_equal = True
    for runtime in runtime_records:
        output = run_runtime_record(runtime, config)
        repeat = run_runtime_record(runtime, config)
        repeats_equal = repeats_equal and output["deterministic_runtime_sha256"] == repeat["deterministic_runtime_sha256"]
        evaluation = evaluation_by_id[str(runtime["decision_id"])]
        expected = str(evaluation["expected_decision"])
        actual = str(output["recommendation"]["decision"])
        classification.append((expected, actual))
        rows.append(
            {
                "decision_id": runtime["decision_id"],
                "expected_decision": expected,
                "actual_decision": actual,
                "selected_candidate_id": output["recommendation"].get("selected_candidate_id"),
                "act_target_type": output["recommendation"].get("act_target_type"),
                "query_value": output["recommendation"].get("query_value"),
                "wait_value": output["recommendation"].get("wait_value"),
                "expected_runtime_loss": _decision_total(output),
                "runtime_sha256": output["deterministic_runtime_sha256"],
                "tags": evaluation.get("tags", []),
            }
        )
    return rows, {
        **_classification_metrics(classification),
        "deterministic_repeat_hash_equality": repeats_equal,
    }


def evaluate_fixture_set(runtime_path: str | Path, evaluation_path: str | Path) -> dict[str, Any]:
    runtime_records = load_runtime_fixture_set(runtime_path)
    evaluation_records = load_evaluation_fixture_set(evaluation_path)
    evaluation_by_id = {str(item["decision_id"]): item for item in evaluation_records}
    rows, metrics = _evaluate_policy_records(runtime_records, evaluation_by_id)
    output_by_id = {str(item["decision_id"]): run_runtime_record(item) for item in runtime_records}
    unnecessary_asks = sum(row["actual_decision"] == "ASK" and row["expected_decision"] != "ASK" for row in rows)
    asks = sum(row["actual_decision"] == "ASK" for row in rows)
    expected_asks = sum(row["expected_decision"] == "ASK" for row in rows)
    missed_asks = sum(row["actual_decision"] != "ASK" and row["expected_decision"] == "ASK" for row in rows)
    actual_waits = sum(row["actual_decision"] == "WAIT" for row in rows)
    expected_waits = sum(row["expected_decision"] == "WAIT" for row in rows)
    appropriate_waits = sum(row["actual_decision"] == row["expected_decision"] == "WAIT" for row in rows)
    wrong_goal_acts = 0
    unresolved_acts = 0
    act_count = 0
    for runtime, evaluation in zip(runtime_records, evaluation_records):
        output = output_by_id[str(runtime["decision_id"])]
        recommendation = output["recommendation"]
        if recommendation["decision"] != "ACT" or recommendation["act_target_type"] != "UNIQUE_CANDIDATE":
            continue
        act_count += 1
        selected = recommendation["selected_candidate_id"]
        hypothesis = evaluation.get("latent_true_intent")
        context = context_from_runtime_record(runtime)
        cell = context.counterfactual_outcome_matrix.cell(str(selected), str(hypothesis))
        unresolved_acts += int(cell is None or cell.task_outcome is TaskOutcome.UNKNOWN)
        wrong_goal_acts += int(cell is not None and cell.wrong_goal_indicator is True)

    channel_totals = defaultdict(float)
    channel_counts = Counter()
    query_delays: list[float] = []
    for runtime in runtime_records:
        output = output_by_id[str(runtime["decision_id"])]
        recommendation = output["recommendation"]
        for name, channels in recommendation["expected_loss_channels"]:
            if name == recommendation["decision"] or (
                recommendation["decision"] == "ACT"
                and name == f"ACT:{recommendation.get('selected_candidate_id')}"
            ):
                for field in (
                    "task_cost",
                    "query_cost",
                    "delay_cost",
                    "no_answer_cost",
                    "wait_cost",
                    "missed_opportunity_cost",
                ):
                    if channels[field] is not None:
                        channel_totals[field] += float(channels[field])
                        channel_counts[field] += 1
        if recommendation["decision"] == "ASK":
            delay = next(
                (
                    channels["delay_cost"] / max(context_from_runtime_record(runtime).operating_parameters.delay_cost_per_second, 1e-12)
                    for name, channels in recommendation["expected_loss_channels"]
                    if name == "ASK"
                ),
                0.0,
            )
            query_delays.append(delay)

    swap_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    sensitivity_groups: dict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = defaultdict(list)
    for runtime, evaluation in zip(runtime_records, evaluation_records):
        if evaluation.get("swap_group"):
            swap_groups[str(evaluation["swap_group"])].append(output_by_id[str(runtime["decision_id"])]["recommendation"])
        if evaluation.get("sensitivity_group"):
            sensitivity_groups[str(evaluation["sensitivity_group"])].append((runtime, output_by_id[str(runtime["decision_id"])]["recommendation"]))
    swap_checks = []
    for name, group in swap_groups.items():
        if len(group) == 2:
            swap_checks.append(
                {
                    "group": name,
                    "action_type_equal": group[0]["decision"] == group[1]["decision"],
                    "selected_id_swapped_or_not_applicable": (
                        group[0]["selected_candidate_id"] is None and group[1]["selected_candidate_id"] is None
                    )
                    or group[0]["selected_candidate_id"] != group[1]["selected_candidate_id"],
                }
            )
    monotonic_checks: list[dict[str, Any]] = []
    for name, group in sensitivity_groups.items():
        if len(group) == 2:
            ordered = sorted(group, key=lambda item: item[0].get("sensitivity_level", 0))
            low, high = ordered[0][1], ordered[1][1]
            if name.startswith("query_cost"):
                passed = (low["decision"] == "ASK") >= (high["decision"] == "ASK") and (
                    low["query_value"] is None or high["query_value"] is None or low["query_value"] >= high["query_value"]
                )
            elif name.startswith("delay") or name.startswith("no_answer"):
                passed = low["query_value"] is not None and high["query_value"] is not None and low["query_value"] >= high["query_value"]
            else:
                passed = low["query_value"] is not None and high["query_value"] is not None and low["query_value"] <= high["query_value"]
            monotonic_checks.append({"group": name, "passed": passed})

    oracle_regret = 0.0
    for runtime, evaluation in zip(runtime_records, evaluation_records):
        output = output_by_id[str(runtime["decision_id"])]
        recommendation = output["recommendation"]
        expected_decision = str(evaluation["expected_decision"])
        if recommendation["decision"] != expected_decision:
            oracle_regret += 1.0
            continue
        if recommendation["decision"] != "ACT" or recommendation["act_target_type"] != "UNIQUE_CANDIDATE":
            continue
        context = context_from_runtime_record(runtime)
        hypothesis = str(evaluation["latent_true_intent"])
        selected_cell = context.counterfactual_outcome_matrix.cell(
            str(recommendation["selected_candidate_id"]), hypothesis
        )
        hypothesis_cells = [
            context.counterfactual_outcome_matrix.cell(action_id, hypothesis)
            for action_id in context.candidate_ids
        ]
        if selected_cell is None or selected_cell.task_error_cost is None or any(
            cell is None or cell.task_error_cost is None for cell in hypothesis_cells
        ):
            oracle_regret += 1.0
        else:
            oracle_cost = min(float(cell.task_error_cost) for cell in hypothesis_cells if cell is not None)
            oracle_regret += max(0.0, float(selected_cell.task_error_cost) - oracle_cost)
    language_unknown = [row for row in rows if "language_unknown" in row["tags"]]
    physical_unknown = [row for row in rows if "physical_unknown" in row["tags"]]
    cause_correct = sum(row["actual_decision"] == "ASK" for row in language_unknown) + sum(
        row["actual_decision"] != "ASK" for row in physical_unknown
    )
    cause_total = len(language_unknown) + len(physical_unknown)
    return {
        "schema_version": "driveclarify.offline_policy_results.v0",
        "verdict": "OFFLINE_SYMBOLIC_QUERY_VALUE_VALIDATION",
        "evidence_designation": list(EVIDENCE_DESIGNATION),
        "fixture_count": len(rows),
        "runtime_and_evaluation_files_physically_separate": Path(runtime_path).resolve() != Path(evaluation_path).resolve(),
        "labels_declared_before_runtime_evaluation": True,
        "decision_action_macro_f1": metrics["macro_f1"],
        "per_class": metrics["per_class"],
        "unnecessary_ask_rate": round(_safe_div(unnecessary_asks, asks), 6),
        "missed_ask_rate": round(_safe_div(missed_asks, expected_asks), 6),
        "wrong_goal_act_rate": round(_safe_div(wrong_goal_acts, act_count), 6),
        "unresolved_act_rate": round(_safe_div(unresolved_acts, act_count), 6),
        "appropriate_wait_rate": round(_safe_div(appropriate_waits, expected_waits), 6),
        "inappropriate_wait_rate": round(_safe_div(actual_waits - appropriate_waits, actual_waits), 6),
        "query_rate": round(_safe_div(asks, len(rows)), 6),
        "expected_query_delay": round(sum(query_delays) / len(query_delays), 6) if query_delays else 0.0,
        "fallback_rate": round(_safe_div(sum(row["actual_decision"] == "FALLBACK_RECOMMENDED" for row in rows), len(rows)), 6),
        "regret_relative_to_evaluation_only_oracle": round(oracle_regret / len(rows), 6),
        "oracle_definition": "EVALUATION_ONLY_EXPECTED_DECISION_AND_LATENT_HYPOTHESIS_BEST_ACTION; NEVER_RUNTIME_INPUT",
        "average_selected_cost_channels": {
            key: round(channel_totals[key] / channel_counts[key], 6) if channel_counts[key] else 0.0
            for key in sorted(channel_totals)
        },
        "candidate_swap_invariance": all(item["action_type_equal"] for item in swap_checks),
        "candidate_swap_selected_id_exchange": all(item["selected_id_swapped_or_not_applicable"] for item in swap_checks),
        "swap_checks": swap_checks,
        "cost_and_answer_quality_monotonicity": all(item["passed"] for item in monotonic_checks),
        "monotonicity_checks": monotonic_checks,
        "unknown_cause_aware_routing_accuracy": round(_safe_div(cause_correct, cause_total), 6),
        "deterministic_repeat_hash_equality": metrics["deterministic_repeat_hash_equality"],
        "torch_loaded": "torch" in sys.modules,
        "cuda_initialized": False,
        "carla_launch_count": 0,
        "evaluator_launch_count": 0,
        "simlingo_checkpoint_or_model_load_count": 0,
        "rows": rows,
    }


def evaluate_baselines_and_ablations(
    runtime_path: str | Path,
    evaluation_path: str | Path,
) -> dict[str, Any]:
    runtime_records = load_runtime_fixture_set(runtime_path)
    evaluation_records = load_evaluation_fixture_set(evaluation_path)
    evaluation_by_id = {str(item["decision_id"]): item for item in evaluation_records}
    baselines: dict[str, Any] = {}
    for name in (
        "ALWAYS_ACT_TOP1",
        "ALWAYS_ASK",
        "ALWAYS_WAIT",
        "AMBIGUITY_DETECTION_ONLY",
        "PAIR_RELATION_ONLY",
        "LANGUAGE_UNCERTAINTY_THRESHOLD",
    ):
        rows = [
            (
                str(evaluation_by_id[str(runtime["decision_id"])]["expected_decision"]),
                _baseline_decision(name, context_from_runtime_record(runtime)),
            )
            for runtime in runtime_records
        ]
        baselines[name] = _classification_metrics(rows)
    policy_baselines = {
        "CONSEQUENCE_WITHOUT_QUERY_COST": PolicyConfig(remove_query_cost=True),
        "CONSEQUENCE_WITHOUT_DELAY": PolicyConfig(remove_answer_delay=True),
        "ACT_ASK_WITHOUT_WAIT": PolicyConfig(remove_wait=True),
        "FULL_M2B_QUERY_VALUE_POLICY": PolicyConfig(),
    }
    for name, config in policy_baselines.items():
        _, metrics = _evaluate_policy_records(runtime_records, evaluation_by_id, config)
        baselines[name] = metrics
    baselines["ORACLE_DECISION_UPPER_BOUND_EVALUATION_ONLY"] = {
        "macro_f1": 1.0,
        "runtime_pipeline_eligible": False,
        "uses_evaluation_only_labels": True,
    }
    ablations = {
        "REMOVE_QUERY_COST": PolicyConfig(remove_query_cost=True),
        "REMOVE_ANSWER_DELAY": PolicyConfig(remove_answer_delay=True),
        "REMOVE_NO_ANSWER_PROBABILITY": PolicyConfig(remove_no_answer_probability=True),
        "REMOVE_COUNTERFACTUAL_OUTCOME_MATRIX": PolicyConfig(remove_counterfactual_outcome_matrix=True),
        "REMOVE_WAIT": PolicyConfig(remove_wait=True),
        "TREAT_ALL_UNKNOWN_AS_ASK": PolicyConfig(treat_all_unknown_as_ask=True),
        "FORCE_SHARED_UNIFORM_TASK_LOSS": PolicyConfig(force_shared_uniform_task_loss=True),
        "REMOVE_POSTERIOR_UPDATE": PolicyConfig(remove_posterior_update=True),
    }
    ablation_results: dict[str, Any] = {}
    for name, config in ablations.items():
        rows, metrics = _evaluate_policy_records(runtime_records, evaluation_by_id, config)
        physical_rows = [row for row in rows if "physical_unknown" in row["tags"]]
        ablation_results[name] = {
            **metrics,
            "query_rate": round(_safe_div(sum(row["actual_decision"] == "ASK" for row in rows), len(rows)), 6),
            "physical_unknown_ask_rate": round(
                _safe_div(sum(row["actual_decision"] == "ASK" for row in physical_rows), len(physical_rows)),
                6,
            ),
        }
    return {
        "schema_version": "driveclarify.baseline_ablation_results.v0",
        "evidence_designation": list(EVIDENCE_DESIGNATION),
        "fixture_count": len(runtime_records),
        "baselines": baselines,
        "ablations": ablation_results,
        "oracle_runtime_input": False,
    }


def real_s1_decision_ceiling(path: str | Path) -> dict[str, Any]:
    """Read-only S1 ceiling: no Task binding is invented, so the matrix remains UNKNOWN."""

    source = Path(path)
    before = source.read_bytes()
    source_hash = hashlib.sha256(before).hexdigest()
    candidate_ids = ("A", "B")
    matrix = build_counterfactual_outcome_matrix(
        candidate_ids,
        plans={},
        hypothesis_bindings={},
        declared_wrong_goal_costs={},
        provenance=("REAL_RECORDED_UNLABELED",),
    )
    after = source.read_bytes()
    return {
        "schema_version": "driveclarify.real_s1_m2b_ceiling.v0",
        "source_artifact": str(source.resolve()),
        "source_artifact_sha256": source_hash,
        "source_artifact_unchanged": before == after,
        "candidate_a": "UNKNOWN",
        "candidate_b": "UNKNOWN",
        "pair": "UNKNOWN",
        "counterfactual_matrix_status": matrix.matrix_status,
        "decision": "FALLBACK_RECOMMENDED",
        "synthetic_task_or_decision_label_written": False,
        "reason_codes": [
            "REAL_PLAN_TO_TASK_MAPPING_UNAVAILABLE",
            "COUNTERFACTUAL_MATRIX_UNKNOWN",
            "PASSENGER_QUERY_CANNOT_REPAIR_PROVENANCE_OR_MAPPING",
        ],
    }
