"""Frozen Stage 6A baseline configuration and strict schema validation."""

from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import Any, Mapping

from .contracts import (
    CANDIDATE_FORWARD_BUDGETS,
    NORMAL_SIMLINGO_FORWARD_BUDGET,
    ContractError,
    METHOD_ORDER,
    require_exact_keys,
)
from .hashing import canonical_sha256, file_sha256


BASELINE_CONFIG_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "driveclarify.paper_mvp_baseline_config.v2.schema.json",
    "title": "DriveClarify paper-MVP executable baseline freeze",
    "type": "object",
    "required": sorted(_TOP_KEYS) if "_TOP_KEYS" in globals() else [
        "schema_version",
        "freeze_id",
        "status",
        "method_order",
        "common",
        "methods",
        "metric_denominators",
        "split_protocol",
        "prohibitions",
    ],
    "additionalProperties": False,
    "properties": {
        "schema_version": {"const": "driveclarify.paper_mvp_baseline_config.v2"},
        "freeze_id": {"const": "DRIVECLARIFY_PAPER_MVP_STAGE6A_BASELINES_V2"},
        "status": {"const": "STAGE6A_EXECUTABLE_CONTRACT_FROZEN"},
        "method_order": {
            "type": "array",
            "prefixItems": [{"const": item} for item in METHOD_ORDER],
            "items": False,
            "minItems": 8,
            "maxItems": 8,
        },
        "common": {"type": "object"},
        "methods": {
            "type": "array",
            "minItems": 8,
            "maxItems": 8,
            "items": {
                "type": "object",
                "required": [
                    "id",
                    "run_enabled",
                    "implementation_entrypoint",
                    "visible_runtime_fields",
                    "action_contract",
                    "compute_budget",
                    "threshold",
                    "scientific_aliases",
                ],
                "additionalProperties": False,
                "properties": {
                    "id": {"enum": list(METHOD_ORDER)},
                    "run_enabled": {"const": True},
                    "implementation_entrypoint": {"type": "string", "minLength": 3},
                    "visible_runtime_fields": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "action_contract": {"type": "string", "minLength": 1},
                    "compute_budget": {
                        "type": "object",
                        "required": [
                            "normal_simlingo_forward_count",
                            "candidate_conditioned_forward_cases",
                        ],
                        "additionalProperties": False,
                        "properties": {
                            "normal_simlingo_forward_count": {"const": 1},
                            "candidate_conditioned_forward_cases": {
                                "type": "object",
                                "minProperties": 1,
                                "additionalProperties": {
                                    "type": "integer",
                                    "minimum": 0,
                                    "maximum": 2,
                                },
                            },
                        },
                    },
                    "threshold": {"type": ["object", "null"]},
                    "scientific_aliases": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        },
        "metric_denominators": {"type": "object"},
        "split_protocol": {"type": "object"},
        "prohibitions": {"type": "array", "items": {"type": "string"}},
    },
    "x-driveclarify-exact-method-ids": list(METHOD_ORDER),
    "x-driveclarify-forbidden-method-ids": ["always_obey"],
    "x-driveclarify-threshold-fields": {
        "language_uncertainty": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "comparison": ">=",
            "tie_action": "ASK",
            "unknown_action": "FALLBACK",
        },
        "risk_divergence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "comparison": ">=",
            "tie_action": "ASK",
            "unknown_action": "FALLBACK",
        },
    },
    "x-driveclarify-prediction-vocabulary": ["ACT", "ASK", "WAIT", "STOP", "FALLBACK"],
    "x-driveclarify-scored-gold-vocabulary": ["ACT", "ASK", "WAIT"],
    "x-driveclarify-stop-and-fallback-mapping": None,
}


_COMMON = {
    "runtime_input": "LABEL_FIREWALL_RUNTIME_ALLOWLIST_ONLY",
    "candidate_source": "RUNTIME_GENERATED_ONLY",
    "candidate_tie_break": "MIN_SHA256_EPISODE_ID_PLUS_RUNTIME_CANDIDATE_ID",
    "catalog_candidate_order_visible": False,
    "unknown_policy": "PROPAGATE_TO_FALLBACK_WITH_REASON",
    "stop_maps_to_scored_decision": False,
    "fallback_maps_to_scored_decision": False,
    "existing_pid_only": True,
    "model_forward_accounting": {
        "normal_simlingo_forward_per_control_tick": NORMAL_SIMLINGO_FORWARD_BUDGET,
        "candidate_forward_unit": (
            "ONE_MODEL_INVOCATION_WITH_ONE_RUNTIME_CANDIDATE_CONDITIONED_INPUT"
        ),
        "candidate_plan_copy_policy": (
            "FORBIDDEN_AS_A_SUBSTITUTE_FOR_A_REQUIRED_CANDIDATE_FORWARD"
        ),
        "distinct_candidate_input_sha256_required_when_count_gt_one": True,
        "candidate_output_digest_equality_policy": (
            "ALLOWED_ONLY_AS_AN_OBSERVED_MODEL_RESULT_AFTER_DISTINCT_INPUT_FORWARDS"
        ),
        "latency_accounting": {
            "normal_model_forward_latency_seconds": "ONE_NORMAL_FORWARD_WALL_TIME",
            "candidate_model_forward_latencies_seconds": (
                "ONE_WALL_TIME_VALUE_PER_CANDIDATE_FORWARD"
            ),
            "decision_compute_latency_seconds": (
                "RUNTIME_CANDIDATE_GENERATION_THROUGH_METHOD_DECISION"
            ),
            "metric_denominator": "ALL_STARTED_METHOD_EPISODES",
            "missing_policy": "KEEP_DENOMINATOR_AND_RETURN_NULL",
        },
        "scope_note": (
            "CANDIDATE_FORWARDS_ARE_ADDITIONAL_TO_THE_ONE_NORMAL_SIMLINGO_FORWARD;"
            "THEY_ARE_BUDGETED_PER_METHOD_DECISION_CASE"
        ),
    },
    "test_threshold_tuning_allowed": False,
}


def _method(
    method_id: str,
    *,
    entrypoint: str,
    visible_fields: list[str],
    action_contract: str,
    threshold: Mapping[str, Any] | None = None,
    scientific_aliases: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": method_id,
        "run_enabled": True,
        "implementation_entrypoint": entrypoint,
        "visible_runtime_fields": visible_fields,
        "action_contract": action_contract,
        "compute_budget": {
            "normal_simlingo_forward_count": NORMAL_SIMLINGO_FORWARD_BUDGET,
            "candidate_conditioned_forward_cases": copy.deepcopy(
                CANDIDATE_FORWARD_BUDGETS[method_id]
            ),
        },
        "threshold": None if threshold is None else dict(threshold),
        "scientific_aliases": list(scientific_aliases or []),
    }


FROZEN_BASELINE_CONFIG: dict[str, Any] = {
    "schema_version": "driveclarify.paper_mvp_baseline_config.v2",
    "freeze_id": "DRIVECLARIFY_PAPER_MVP_STAGE6A_BASELINES_V2",
    "status": "STAGE6A_EXECUTABLE_CONTRACT_FROZEN",
    "method_order": list(METHOD_ORDER),
    "common": copy.deepcopy(_COMMON),
    "methods": [
        _method(
            "original_simlingo",
            entrypoint="driveclarify_paper_mvp_evaluation.baselines:original_simlingo",
            visible_fields=[
                "observation_id",
                "original_simlingo_plan_available",
                "hard_safety_status",
                "hard_rule_status",
            ],
            action_contract=(
                "IGNORE_DRIVECLARIFY_AMBIGUOUS_UTTERANCE_AND_CONTINUE_THE_SINGLE_"
                "ORIGINAL_SIMLINGO_PLAN;IMPLICIT_ACT_FOR_DECISION_SCORING"
            ),
            scientific_aliases=["ignore_language"],
        ),
        _method(
            "driveclarify",
            entrypoint="driveclarify_paper_mvp_evaluation.baselines:driveclarify",
            visible_fields=[
                "authoritative_driveclarify_action",
                "authoritative_driveclarify_candidate_id",
                "authority_resolver_applied",
                "hard_safety_status",
                "hard_rule_status",
            ],
            action_contract="PRESERVE_LIVE_AUTHORITY_RESOLVER_OUTPUT_FAIL_CLOSED",
        ),
        _method(
            "always_ask",
            entrypoint="driveclarify_paper_mvp_evaluation.baselines:always_ask",
            visible_fields=[
                "phase",
                "answer_candidate_id",
                "holding_verified",
                "future_information_before_deadline",
                "hard_safety_status",
                "hard_rule_status",
            ],
            action_contract=(
                "INITIAL_ASK_ONCE;PENDING_QUERY_WAIT_ONLY_WITH_VERIFIED_HOLDING;"
                "ANSWER_RECEIVED_ACT_ON_ANSWER_SELECTED_RUNTIME_CANDIDATE;"
                "NO_SECOND_QUERY;OTHERWISE_FALLBACK"
            ),
        ),
        _method(
            "always_stop",
            entrypoint="driveclarify_paper_mvp_evaluation.baselines:always_stop",
            visible_fields=["observation_id"],
            action_contract=(
                "REQUEST_BASELINE_STOP_BEHAVIOR_USING_EXISTING_PID;STOP_IS_NOT_WAIT_"
                "AND_HAS_NO_ACT_ASK_WAIT_SCORE_ALIAS"
            ),
        ),
        _method(
            "always_wait",
            entrypoint="driveclarify_paper_mvp_evaluation.baselines:always_wait",
            visible_fields=[
                "phase",
                "future_information_candidate_id",
                "holding_verified",
                "future_information_before_deadline",
                "hard_safety_status",
                "hard_rule_status",
            ],
            action_contract=(
                "WAIT_ONLY_WITH_VERIFIED_HOLDING_AND_ON_TIME_INFORMATION;"
                "ACT_AFTER_INFORMATION_ARRIVAL;OTHERWISE_FALLBACK;WAIT_IS_NOT_STOP"
            ),
        ),
        _method(
            "never_ask",
            entrypoint="driveclarify_paper_mvp_evaluation.baselines:never_ask",
            visible_fields=[
                "candidates.candidate_id",
                "candidates.rank_score",
                "hard_safety_status",
                "hard_rule_status",
            ],
            action_contract=(
                "NEVER_QUERY;ACT_ON_HIGHEST_RUNTIME_RANK_WITH_OPAQUE_HASH_TIE_BREAK;"
                "UNKNOWN_OR_FAILED_GATE_TO_FALLBACK"
            ),
        ),
        _method(
            "language_only_uncertainty",
            entrypoint=(
                "driveclarify_paper_mvp_evaluation.baselines:"
                "language_only_uncertainty"
            ),
            visible_fields=[
                "phase",
                "language_uncertainty",
                "answer_candidate_id",
                "candidates.candidate_id",
                "candidates.rank_score",
                "holding_verified",
                "future_information_before_deadline",
                "hard_safety_status",
                "hard_rule_status",
            ],
            action_contract=(
                "ASK_IFF_LANGUAGE_UNCERTAINTY_GTE_FROZEN_THRESHOLD;"
                "OTHERWISE_ACT_ON_RUNTIME_RANK;UNKNOWN_TO_FALLBACK"
            ),
            threshold={
                "field": "language_uncertainty",
                "value": 0.5,
                "comparison": ">=",
                "equal_action": "ASK",
                "unknown_action": "FALLBACK",
                "selection_source": "PREREGISTERED_SCALE_MIDPOINT_NO_TEST_TUNING",
                "freeze_split": "DEV_ATTESTATION_WITHOUT_VALUE_CHANGE",
            },
        ),
        _method(
            "risk_only",
            entrypoint="driveclarify_paper_mvp_evaluation.baselines:risk_only",
            visible_fields=[
                "phase",
                "answer_candidate_id",
                "candidates.candidate_id",
                "candidates.risk_score",
                "holding_verified",
                "future_information_before_deadline",
                "hard_safety_status",
                "hard_rule_status",
            ],
            action_contract=(
                "ASK_IFF_RUNTIME_CANDIDATE_RISK_DIVERGENCE_GTE_FROZEN_THRESHOLD;"
                "OTHERWISE_ACT_ON_MINIMUM_RUNTIME_RISK;UNKNOWN_TO_FALLBACK"
            ),
            threshold={
                "field": "risk_divergence",
                "value": 0.25,
                "comparison": ">=",
                "equal_action": "ASK",
                "unknown_action": "FALLBACK",
                "selection_source": "PREREGISTERED_NORMALIZED_SCALE_QUARTER_NO_TEST_TUNING",
                "freeze_split": "DEV_ATTESTATION_WITHOUT_VALUE_CHANGE",
            },
        ),
    ],
    "metric_denominators": {
        "task": "ALL_STARTED_METHOD_EPISODES",
        "safety": "ALL_STARTED_METHOD_EPISODES",
        "decision": "ALL_TERMINAL_EPISODES_WITH_POST_EPISODE_GOLD_JOIN",
        "interaction": "ALL_STARTED_METHOD_EPISODES",
        "interaction_compute_and_latency": "ALL_STARTED_METHOD_EPISODES",
        "closed_loop": "ALL_SCHEDULED_METHOD_EPISODES",
        "missing_value_policy": (
            "KEEP_FROZEN_DENOMINATOR_AND_RETURN_NULL_IF_ANY_REQUIRED_VALUE_IS_UNKNOWN"
        ),
        "environment_start_failure_policy": (
            "COUNT_IN_CLOSED_LOOP_FAILURE_TYPE_AND_COMPLETION_DENOMINATOR;"
            "DO_NOT_FABRICATE_TASK_OR_SAFETY_VALUES"
        ),
    },
    "split_protocol": {
        "order": ["train", "dev", "test"],
        "threshold_freeze_after": "DEV_COMPLETE",
        "test_execution": "ONE_SHOT_AFTER_THRESHOLD_HASH_AND_EXPLICIT_AUTHORIZATION",
        "partial_test_attempt_consumes_one_shot": True,
        "test_rerun_allowed": False,
    },
    "prohibitions": [
        "NO_ALWAYS_OBEY_METHOD",
        "NO_CATALOG_CANDIDATE_ORDER",
        "NO_EXPECTED_LABEL_AT_RUNTIME",
        "NO_STOP_TO_WAIT_MAPPING",
        "NO_FALLBACK_TO_ACT_ASK_WAIT_MAPPING",
        "NO_TEST_THRESHOLD_TUNING",
        "NO_UNBUDGETED_MODEL_FORWARD",
        "NO_CANDIDATE_PLAN_COPY_AS_FORWARD_SUBSTITUTE",
        "NO_SILENT_FORWARD_OR_LATENCY_ACCOUNTING_GAP",
        "NO_NEW_PID_OR_HIDDEN_CONTROLLER",
    ],
}


_TOP_KEYS = {
    "schema_version",
    "freeze_id",
    "status",
    "method_order",
    "common",
    "methods",
    "metric_denominators",
    "split_protocol",
    "prohibitions",
}
_METHOD_KEYS = {
    "id",
    "run_enabled",
    "implementation_entrypoint",
    "visible_runtime_fields",
    "action_contract",
    "compute_budget",
    "threshold",
    "scientific_aliases",
}
_THRESHOLD_KEYS = {
    "field",
    "value",
    "comparison",
    "equal_action",
    "unknown_action",
    "selection_source",
    "freeze_split",
}


def validate_baseline_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the whole frozen configuration and return a detached copy."""

    require_exact_keys(config, _TOP_KEYS, "BASELINE_CONFIG")
    if config.get("schema_version") != "driveclarify.paper_mvp_baseline_config.v2":
        raise ContractError("BASELINE_SCHEMA_VERSION_MISMATCH")
    if config.get("status") != "STAGE6A_EXECUTABLE_CONTRACT_FROZEN":
        raise ContractError("BASELINE_CONFIG_NOT_FROZEN")
    order = config.get("method_order")
    if order != list(METHOD_ORDER):
        raise ContractError("METHOD_ORDER_OR_ID_SET_MISMATCH")
    methods = config.get("methods")
    if not isinstance(methods, list) or len(methods) != 8:
        raise ContractError("EXACTLY_EIGHT_METHODS_REQUIRED")
    actual: list[str] = []
    for index, method in enumerate(methods):
        if not isinstance(method, Mapping):
            raise ContractError(f"METHOD_{index}_OBJECT_REQUIRED")
        require_exact_keys(method, _METHOD_KEYS, f"METHOD_{index}")
        method_id = method.get("id")
        if not isinstance(method_id, str):
            raise ContractError(f"METHOD_{index}_ID_REQUIRED")
        actual.append(method_id)
        if method.get("run_enabled") is not True:
            raise ContractError(f"METHOD_DISABLED:{method_id}")
        entrypoint = method.get("implementation_entrypoint")
        if not isinstance(entrypoint, str) or ":" not in entrypoint:
            raise ContractError(f"METHOD_ENTRYPOINT_INVALID:{method_id}")
        fields = method.get("visible_runtime_fields")
        if not isinstance(fields, list) or not all(
            isinstance(item, str) and item for item in fields
        ):
            raise ContractError(f"METHOD_VISIBLE_FIELDS_INVALID:{method_id}")
        if not isinstance(method.get("action_contract"), str) or not method[
            "action_contract"
        ]:
            raise ContractError(f"METHOD_ACTION_CONTRACT_REQUIRED:{method_id}")
        compute_budget = method.get("compute_budget")
        if not isinstance(compute_budget, Mapping) or set(compute_budget) != {
            "normal_simlingo_forward_count",
            "candidate_conditioned_forward_cases",
        }:
            raise ContractError(f"METHOD_COMPUTE_BUDGET_INVALID:{method_id}")
        if (
            compute_budget.get("normal_simlingo_forward_count")
            != NORMAL_SIMLINGO_FORWARD_BUDGET
            or compute_budget.get("candidate_conditioned_forward_cases")
            != CANDIDATE_FORWARD_BUDGETS.get(method_id)
        ):
            raise ContractError(f"METHOD_COMPUTE_BUDGET_DRIFT:{method_id}")
        candidate_budgets = compute_budget["candidate_conditioned_forward_cases"]
        if not candidate_budgets or any(
            type(count) is not int or count not in {0, 1, 2}
            for count in candidate_budgets.values()
        ):
            raise ContractError(f"METHOD_CANDIDATE_FORWARD_BUDGET_INVALID:{method_id}")
        aliases = method.get("scientific_aliases")
        if not isinstance(aliases, list) or not all(isinstance(item, str) for item in aliases):
            raise ContractError(f"METHOD_ALIASES_INVALID:{method_id}")
        threshold = method.get("threshold")
        if method_id in {"language_only_uncertainty", "risk_only"}:
            if not isinstance(threshold, Mapping):
                raise ContractError(f"THRESHOLD_REQUIRED:{method_id}")
            require_exact_keys(threshold, _THRESHOLD_KEYS, f"THRESHOLD_{method_id}")
            value = threshold.get("value")
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0
            ):
                raise ContractError(f"THRESHOLD_VALUE_INVALID:{method_id}")
            if (
                threshold.get("comparison") != ">="
                or threshold.get("equal_action") != "ASK"
                or threshold.get("unknown_action") != "FALLBACK"
                or threshold.get("freeze_split")
                != "DEV_ATTESTATION_WITHOUT_VALUE_CHANGE"
            ):
                raise ContractError(f"THRESHOLD_SEMANTICS_DRIFT:{method_id}")
        elif threshold is not None:
            raise ContractError(f"UNEXPECTED_THRESHOLD:{method_id}")
    if actual != list(METHOD_ORDER) or len(set(actual)) != 8:
        raise ContractError("METHOD_LIST_DRIFT_OR_DUPLICATE")
    if "always_obey" in actual:
        raise ContractError("ALWAYS_OBEY_FORBIDDEN")
    original = methods[0]
    if original.get("scientific_aliases") != ["ignore_language"]:
        raise ContractError("ORIGINAL_SIMLINGO_IGNORE_LANGUAGE_ALIAS_REQUIRED")
    common = config.get("common")
    if not isinstance(common, Mapping) or dict(common) != _COMMON:
        raise ContractError("COMMON_METHOD_CONTRACT_DRIFT")
    denominators = config.get("metric_denominators")
    if not isinstance(denominators, Mapping) or set(denominators) != {
        "task",
        "safety",
        "decision",
        "interaction",
        "interaction_compute_and_latency",
        "closed_loop",
        "missing_value_policy",
        "environment_start_failure_policy",
    }:
        raise ContractError("METRIC_DENOMINATORS_INCOMPLETE")
    protocol = config.get("split_protocol")
    if not isinstance(protocol, Mapping) or (
        protocol.get("order") != ["train", "dev", "test"]
        or protocol.get("threshold_freeze_after") != "DEV_COMPLETE"
        or protocol.get("partial_test_attempt_consumes_one_shot") is not True
        or protocol.get("test_rerun_allowed") is not False
    ):
        raise ContractError("SPLIT_OR_ONE_SHOT_PROTOCOL_DRIFT")
    prohibitions = config.get("prohibitions")
    if not isinstance(prohibitions, list) or "NO_ALWAYS_OBEY_METHOD" not in prohibitions:
        raise ContractError("BASELINE_PROHIBITIONS_INCOMPLETE")
    if "NO_EXTRA_MODEL_FORWARD" in prohibitions:
        raise ContractError("OBSOLETE_ZERO_FORWARD_PROHIBITION_FORBIDDEN")
    for required in (
        "NO_UNBUDGETED_MODEL_FORWARD",
        "NO_CANDIDATE_PLAN_COPY_AS_FORWARD_SUBSTITUTE",
        "NO_SILENT_FORWARD_OR_LATENCY_ACCOUNTING_GAP",
    ):
        if required not in prohibitions:
            raise ContractError("BASELINE_COMPUTE_PROHIBITIONS_INCOMPLETE")
    return copy.deepcopy(dict(config))


def baseline_freeze_hash(config: Mapping[str, Any] = FROZEN_BASELINE_CONFIG) -> str:
    return canonical_sha256(validate_baseline_config(config))


def method_config_hash(method_id: str, config: Mapping[str, Any] = FROZEN_BASELINE_CONFIG) -> str:
    validated = validate_baseline_config(config)
    method = next((item for item in validated["methods"] if item["id"] == method_id), None)
    if method is None:
        raise ContractError(f"UNKNOWN_METHOD_ID:{method_id}")
    return canonical_sha256(
        {
            "freeze_id": validated["freeze_id"],
            "common": validated["common"],
            "method": method,
            "metric_denominators": validated["metric_denominators"],
        }
    )


def build_baseline_hash_manifest(
    config: Mapping[str, Any] = FROZEN_BASELINE_CONFIG,
) -> dict[str, Any]:
    """Build the content-addressed inputs for ``BASELINE_HASHES.json``."""

    validated = validate_baseline_config(config)
    package = Path(__file__).resolve().parent
    source_names = (
        "contracts.py",
        "baselines.py",
        "freeze.py",
        "hashing.py",
        "entrypoint.py",
        "scheduler.py",
        "metrics.py",
        "display_preflight.py",
    )
    sources = {
        name: {
            "bytes": (package / name).stat().st_size,
            "sha256": file_sha256(package / name),
        }
        for name in source_names
    }
    body = {
        "baseline_freeze_sha256": baseline_freeze_hash(validated),
        "config_schema_sha256": canonical_sha256(BASELINE_CONFIG_SCHEMA),
        "method_config_sha256": {
            method_id: method_config_hash(method_id, validated)
            for method_id in METHOD_ORDER
        },
        "implementation_sources": sources,
    }
    return {
        "schema_version": "driveclarify.paper_mvp_baseline_hashes.v1",
        **body,
        "aggregate_sha256": canonical_sha256(body),
    }


# Import-time self-check is pure stdlib and makes accidental config drift fail closed.
validate_baseline_config(FROZEN_BASELINE_CONFIG)
