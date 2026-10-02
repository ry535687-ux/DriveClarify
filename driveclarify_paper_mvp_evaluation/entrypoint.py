"""JSON-compatible entrypoint for one frozen method decision.

This entrypoint is intentionally policy-only.  It neither starts CARLA nor
loads a model/checkpoint, and it rejects evaluation annotation fields.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from .baselines import execute_method
from .contracts import (
    BaselineRuntimeInput,
    ContractError,
    GateStatus,
    InteractionPhase,
    MethodDecision,
    RuntimeAction,
    RuntimeCandidate,
    require_exact_keys,
)
from .freeze import FROZEN_BASELINE_CONFIG, baseline_freeze_hash


_RUNTIME_KEYS = {
    "episode_id",
    "raw_instruction",
    "observation_id",
    "candidates",
    "phase",
    "language_uncertainty",
    "answer_candidate_id",
    "future_information_candidate_id",
    "holding_verified",
    "future_information_before_deadline",
    "hard_safety_status",
    "hard_rule_status",
    "original_simlingo_plan_available",
    "authoritative_driveclarify_action",
    "authoritative_driveclarify_candidate_id",
    "authority_resolver_applied",
    "runtime_provenance",
}
_CANDIDATE_KEYS = {"candidate_id", "rank_score", "risk_score", "source"}
_EVALUATION_DENYLIST = {
    "scenario_id",
    "split",
    "expected_decision",
    "ground_truth_reason",
    "candidate_interpretations",
    "candidate_order_by_seed",
    "candidate_consequence_summary",
    "query_value_expectation",
    "wait_value_expectation",
}


def _walk_keys(value: Any):
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key)
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def runtime_input_from_mapping(value: Mapping[str, Any]) -> BaselineRuntimeInput:
    overlap = set(_walk_keys(value)) & _EVALUATION_DENYLIST
    if overlap:
        raise ContractError(f"EVALUATION_ANNOTATION_FIELD_REJECTED:{sorted(overlap)}")
    require_exact_keys(value, _RUNTIME_KEYS, "BASELINE_RUNTIME_INPUT")
    candidates_raw = value["candidates"]
    if not isinstance(candidates_raw, list):
        raise ContractError("RUNTIME_CANDIDATE_LIST_REQUIRED")
    candidates = []
    for index, item in enumerate(candidates_raw):
        if not isinstance(item, Mapping):
            raise ContractError(f"RUNTIME_CANDIDATE_{index}_OBJECT_REQUIRED")
        require_exact_keys(item, _CANDIDATE_KEYS, f"RUNTIME_CANDIDATE_{index}")
        candidates.append(RuntimeCandidate(**dict(item)))
    raw_action = value["authoritative_driveclarify_action"]
    return BaselineRuntimeInput(
        episode_id=str(value["episode_id"]),
        raw_instruction=str(value["raw_instruction"]),
        observation_id=str(value["observation_id"]),
        candidates=tuple(candidates),
        phase=InteractionPhase(str(value["phase"])),
        language_uncertainty=value["language_uncertainty"],
        answer_candidate_id=value["answer_candidate_id"],
        future_information_candidate_id=value["future_information_candidate_id"],
        holding_verified=value["holding_verified"],
        future_information_before_deadline=value[
            "future_information_before_deadline"
        ],
        hard_safety_status=GateStatus(str(value["hard_safety_status"])),
        hard_rule_status=GateStatus(str(value["hard_rule_status"])),
        original_simlingo_plan_available=value["original_simlingo_plan_available"],
        authoritative_driveclarify_action=(
            None if raw_action is None else RuntimeAction(str(raw_action))
        ),
        authoritative_driveclarify_candidate_id=value[
            "authoritative_driveclarify_candidate_id"
        ],
        authority_resolver_applied=value["authority_resolver_applied"],
        runtime_provenance=dict(value["runtime_provenance"]),
    )


def method_decision_to_dict(value: MethodDecision) -> dict[str, Any]:
    return {
        "schema_version": "driveclarify.paper_mvp_method_decision.v2",
        "method_id": value.method_id,
        "action": value.action.value,
        "scored_decision": value.scored_decision,
        "selected_candidate_id": value.selected_candidate_id,
        "reason_codes": list(value.reason_codes),
        "query_requested": value.query_requested,
        "holding_requested": value.holding_requested,
        "stop_requested": value.stop_requested,
        "existing_pid_only": value.existing_pid_only,
        "interaction_phase": value.interaction_phase.value,
        "compute_budget_case": value.compute_budget_case,
        "normal_model_forward_budget": value.normal_model_forward_budget,
        "candidate_model_forward_budget": value.candidate_model_forward_budget,
        "total_model_forward_budget": value.total_model_forward_budget,
        "candidate_forward_budget_scope": (
            "ADDITIONAL_TO_ONE_NORMAL_SIMLINGO_FORWARD_PER_CONTROL_TICK;"
            "ONE_DISTINCT_CANDIDATE_CONDITIONED_INPUT_PER_CANDIDATE_FORWARD"
        ),
        "candidate_plan_copy_as_forward_substitute_allowed": False,
        "catalog_annotation_read_count": value.catalog_annotation_read_count,
        "baseline_freeze_sha256": baseline_freeze_hash(FROZEN_BASELINE_CONFIG),
    }


def execute_method_from_mapping(method_id: str, value: Mapping[str, Any]) -> dict[str, Any]:
    return method_decision_to_dict(
        execute_method(method_id, runtime_input_from_mapping(value))
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Execute one label-free frozen Stage 6A method decision."
    )
    parser.add_argument("--method", required=True)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args(argv)
    value = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ContractError("BASELINE_RUNTIME_INPUT_OBJECT_REQUIRED")
    print(
        json.dumps(
            execute_method_from_mapping(args.method, value),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
