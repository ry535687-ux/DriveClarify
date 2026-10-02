"""Deterministically author the M2C runtime challenge and isolated hidden gold.

This generator uses only explicit hand-authored symbolic blueprints and the
independent authoring oracle.  It never reads previous fixtures or policy results.
"""

from __future__ import annotations

import argparse
import copy
import random
from pathlib import Path
from typing import Any

from .challenge_contracts import (
    CHALLENGE_NAME,
    CHALLENGE_SEED_SHA256,
    SCHEMA_VERSION,
    stable_sha256,
    validate_hidden_case,
    validate_runtime_case,
    write_json,
)
from .independent_gold_oracle import evaluate_case


DEFAULT_REPORT_DIR = Path("reports/m2c_blind_integrated_challenge_v0")
PROVENANCE = [
    "HAND_AUTHORED_BLIND_CHALLENGE_V0",
    "OFFLINE_SYMBOLIC_ONLY",
    "NOT_PRIMARY_EVIDENCE",
    "NOT_PAPER_RESULT",
]

SUPPORTED_INSTRUCTIONS = (
    "After the pale utility van nearest the striped crossing, take the branch beside the second brick entrance.",
    "At the junction following the copper-roofed bus shelter, pull in by the marked loading bay near the narrow arch.",
    "Pass the short stone wall, then use the entrance beyond the light delivery truck closest to the zebra markings.",
    "By the second recessed doorway after the blue kiosk, follow the branch alongside the cream-colored minibus.",
    "Continue past the glass shelter and choose the access beside the later of the two numbered gates.",
    "Near the painted loading rectangle after the small roundabout, use the lane by the nearer silver shuttle.",
    "At the junction just beyond the amber canopy, take the option beside the second entrance after the parked van.",
    "Following the bus stop with the dark roof, pull in next to the marked area before the narrow service arch.",
)

OOD_INSTRUCTIONS = (
    "Take whichever hidden shortcut the next driver would have preferred before yesterday's junction.",
    "Turn both left and right at the same unnamed entrance after the crossing.",
    "Orbit the block twice, then teleport beside the third doorway.",
    "Before the stop that follows the place behind the van, use the gate beside what it refers to.",
    "Go to the missing moon-shaped kiosk and choose its invisible entrance.",
    "Use the red gate, although the scene table also declares that same gate blue and absent.",
    "After the entrance behind the stop beyond the van nearest the gate, take the one beside it.",
    "Choose the entrance, landmark, and passenger constraint that were not specified.",
    "Use either option described only as the same nearby place.",
    "Yes—repeat the unanswered question while another query is already active.",
    "Choose among the first, second, and third mutually exclusive branches.",
    "Follow the target expressed in lunar yards within an unsupported sensor frame.",
)


def _matrix(candidate_ids: list[str], loss_a_if_b: float, loss_b_if_a: float) -> dict[str, Any]:
    a, b = candidate_ids
    return {
        "candidate_ids": candidate_ids,
        "pair_relation": "TASK_CRITICAL",
        "matrix_status": "COMPLETE_KNOWN",
        "unknown_cause": "NONE",
        "cells": [
            _cell(a, a, "PASS", 0.0, False),
            _cell(a, b, "FAIL", loss_a_if_b, True),
            _cell(b, a, "FAIL", loss_b_if_a, True),
            _cell(b, b, "PASS", 0.0, False),
        ],
        "provenance": ["PREDECLARED_SYMBOLIC_TASK_LOSS_MATRIX"],
        "physical_safety_inference_performed": False,
    }


def _cell(
    action_id: str,
    hypothesis_id: str,
    outcome: str,
    cost: float | None,
    wrong_goal: bool | None,
) -> dict[str, Any]:
    return {
        "action_candidate_id": action_id,
        "hypothesis_candidate_id": hypothesis_id,
        "task_outcome": outcome,
        "evidence_status": "KNOWN_SYMBOLIC" if outcome != "UNKNOWN" else "NOT_AVAILABLE",
        "task_error_cost": cost,
        "wrong_goal_indicator": wrong_goal,
        "reason_codes": ["SYMBOLIC_TASK_MATCH" if outcome == "PASS" else "SYMBOLIC_TASK_MISMATCH"],
        "provenance": ["HAND_AUTHORED_COUNTERFACTUAL_CHALLENGE"],
    }


def _set_equivalent(case: dict[str, Any]) -> None:
    ids = case["counterfactual_runtime_inputs"]["candidate_ids"]
    case["counterfactual_runtime_inputs"].update(
        {
            "pair_relation": "TASK_EQUIVALENT",
            "matrix_status": "COMPLETE_KNOWN",
            "unknown_cause": "NONE",
            "cells": [_cell(a, h, "PASS", 0.0, False) for a in ids for h in ids],
        }
    )


def _set_unknown_cell(case: dict[str, Any], cause: str) -> None:
    matrix = case["counterfactual_runtime_inputs"]
    target = matrix["cells"][1]
    target.update(
        {
            "task_outcome": "UNKNOWN",
            "evidence_status": "NOT_AVAILABLE",
            "task_error_cost": None,
            "wrong_goal_indicator": None,
            "reason_codes": [cause],
        }
    )
    matrix["pair_relation"] = "UNKNOWN"
    matrix["matrix_status"] = "CRITICAL_CELL_UNKNOWN"
    matrix["unknown_cause"] = cause


def _set_belief(case: dict[str, Any], probability_a: float, status: str = "AVAILABLE_NORMALIZED") -> None:
    ids = case["counterfactual_runtime_inputs"]["candidate_ids"]
    case["intent_belief"] = {
        "candidate_probabilities": [
            {"candidate_id": ids[0], "probability": probability_a},
            {"candidate_id": ids[1], "probability": 1.0 - probability_a},
        ],
        "belief_status": status,
        "belief_source": "EXTERNAL_CALIBRATED" if probability_a != 0.5 else "DECLARED_UNINFORMATIVE",
        "normalization_status": "NORMALIZED_VALUES_PRESENT",
        "provenance": ["PREDECLARED_CHALLENGE_BELIEF"],
    }
    case["hard_gate_envelope"]["belief_status"] = status


def _set_question_status(case: dict[str, Any], status: str) -> None:
    channel = case["question_channel_contract"]
    channel["question_status"] = status
    if status == "QUESTION_PROPOSAL" and not channel["candidate_partition"]:
        ids = case["counterfactual_runtime_inputs"]["candidate_ids"]
        descriptions = [entity["display_name"] for entity in case["symbolic_scene_table"][:2]]
        slot = case["ambiguity_metadata_without_gold"]["unresolved_slots"][0]
        channel.update(
            {
                "query_id": f"M2C_QUERY_{case['group_id'].rsplit('-', 1)[-1]}",
                "target_slot": slot,
                "candidate_partition": [
                    {"answer_label": "OPTION_ONE", "candidate_ids": [ids[0]]},
                    {"answer_label": "OPTION_TWO", "candidate_ids": [ids[1]]},
                ],
                "option_descriptions": descriptions,
                "question_text": f"Do you mean {descriptions[0]}, or {descriptions[1]}?",
            }
        )
    elif status != "QUESTION_PROPOSAL":
        channel["query_id"] = None
        channel["target_slot"] = None
        channel["candidate_partition"] = []
        channel["option_descriptions"] = []
        channel["question_text"] = None


def _set_channel(
    case: dict[str, Any],
    *,
    diagonal: float = 0.98,
    no_answer: float = 0.05,
    resolution: float = 0.98,
    delay: float = 0.5,
) -> None:
    ids = case["counterfactual_runtime_inputs"]["candidate_ids"]
    case["question_channel_contract"].update(
        {
            "answer_resolution_probability": resolution,
            "no_answer_probability": no_answer,
            "answer_confusion_matrix": [
                {
                    "hypothesis_candidate_id": ids[0],
                    "answer_probabilities": [
                        {"answer_label": "OPTION_ONE", "probability": diagonal},
                        {"answer_label": "OPTION_TWO", "probability": 1.0 - diagonal},
                    ],
                },
                {
                    "hypothesis_candidate_id": ids[1],
                    "answer_probabilities": [
                        {"answer_label": "OPTION_ONE", "probability": 1.0 - diagonal},
                        {"answer_label": "OPTION_TWO", "probability": diagonal},
                    ],
                },
            ],
            "delay_distribution": [{"seconds": delay, "probability": 1.0}],
        }
    )


def _set_wait(
    case: dict[str, Any],
    *,
    mode: str,
    information_probability: float,
    arrival: float | None,
    wait_cost: float,
    missed_cost: float,
    holding: str = "AVAILABLE_CONTRACT_ONLY",
    future: bool = True,
    window: str = "OPEN",
) -> None:
    case["wait_opportunity"].update(
        {
            "wait_mode": mode,
            "future_information_expected": future,
            "expected_information_arrival_monotonic": arrival,
            "holding_capability_status": holding,
            "decision_window_status": window,
            "wait_cost": wait_cost,
            "missed_opportunity_cost": missed_cost,
            "information_resolution_probability": information_probability,
        }
    )


def _base_runtime(index: int, instruction: str) -> dict[str, Any]:
    challenge_id = f"M2C-BASE-{index:03d}"
    group_id = f"M2C-GROUP-{index:03d}"
    ids = ["CAND_A", "CAND_B"]
    slot = ("reference_slot", "landmark_slot", "order_slot", "constraint_slot")[(index - 1) % 4]
    ambiguity = ("REFERENTIAL", "LANDMARK", "ORDER", "UNDERSPECIFIED_CONSTRAINT")[(index - 1) % 4]
    target_a = f"M2C_TARGET_{index:03d}_ALPHA"
    target_b = f"M2C_TARGET_{index:03d}_BETA"
    option_a = f"the striped access marker {index:03d}-alpha"
    option_b = f"the recessed access marker {index:03d}-beta"
    return {
        "challenge_id": challenge_id,
        "group_id": group_id,
        "case_type": "BASE",
        "tags": [],
        "raw_instruction": instruction,
        "symbolic_scene_table": [
            {
                "entity_id": target_a,
                "entity_type": "SYMBOLIC_ROUTE_TARGET",
                "display_name": option_a,
                "observable_attributes": {"surface": "striped", "relative_order": 1},
                "supported_slots": [slot],
                "grounding_status": "GROUNDED_SYMBOLIC",
                "frame_or_semantic_domain": "IDENTIFIER",
            },
            {
                "entity_id": target_b,
                "entity_type": "SYMBOLIC_ROUTE_TARGET",
                "display_name": option_b,
                "observable_attributes": {"surface": "recessed", "relative_order": 2},
                "supported_slots": [slot],
                "grounding_status": "GROUNDED_SYMBOLIC",
                "frame_or_semantic_domain": "IDENTIFIER",
            },
        ],
        "allowed_task_vocabulary": ["REFERENCE_GOAL", "DESTINATION_GOAL", "MANEUVER_BRANCH"],
        "ambiguity_metadata_without_gold": {
            "ambiguity_type": ambiguity,
            "unresolved_slots": [slot],
            "parser_support_regime": "SUPPORTED_COMPOSITIONAL_HOLDOUT",
            "unknown_cause_class": "PASSENGER_RESOLVABLE",
            "unknown_cause": "ANSWERABLE_CANDIDATE_DISTINCTION",
            "boundary_condition": "NONE",
            "maximum_supported_candidate_count": 2,
        },
        "candidate_input_contract": {
            "candidate_count": 2,
            "maximum_supported_candidate_count": 2,
            "time_domain": "MONOTONIC_SECONDS",
            "plan_semantic_domain": "MODEL_LOCAL_RAW",
            "plan_unit": "RAW_UNIT",
            "diagnostic_only": True,
            "authorization_eligible": False,
        },
        "candidate_plan_records": [
            {
                "candidate_id": ids[0],
                "source_observation_id": f"M2C_OBS_{index:03d}",
                "plan_frame": "MODEL_LOCAL_RAW",
                "plan_unit": "RAW_UNIT",
                "raw_route_tokens": ["origin", f"approach_{index:03d}", "alpha_branch"],
                "raw_speed_profile_tokens": ["nominal", "yield_capable"],
                "candidate_freshness_status": "FRESH",
                "cache_status": "FRESH",
                "provenance": ["SYNTHETIC_PLAN_CHALLENGE_ONLY"],
            },
            {
                "candidate_id": ids[1],
                "source_observation_id": f"M2C_OBS_{index:03d}",
                "plan_frame": "MODEL_LOCAL_RAW",
                "plan_unit": "RAW_UNIT",
                "raw_route_tokens": ["origin", f"approach_{index:03d}", "beta_branch"],
                "raw_speed_profile_tokens": ["nominal", "yield_capable"],
                "candidate_freshness_status": "FRESH",
                "cache_status": "FRESH",
                "provenance": ["SYNTHETIC_PLAN_CHALLENGE_ONLY"],
            },
        ],
        "candidate_specific_task_bindings": [
            {
                "candidate_id": ids[0],
                "source_candidate_id": ids[0],
                "task_family": "MANEUVER_BRANCH",
                "symbolic_target_type": "SYMBOLIC_ROUTE_TARGET",
                "symbolic_target_id": target_a,
                "binding_status": "BOUND",
                "frame_or_semantic_domain": "IDENTIFIER",
                "provenance": ["HAND_AUTHORED_SYMBOLIC_BINDING"],
            },
            {
                "candidate_id": ids[1],
                "source_candidate_id": ids[1],
                "task_family": "MANEUVER_BRANCH",
                "symbolic_target_type": "SYMBOLIC_ROUTE_TARGET",
                "symbolic_target_id": target_b,
                "binding_status": "BOUND",
                "frame_or_semantic_domain": "IDENTIFIER",
                "provenance": ["HAND_AUTHORED_SYMBOLIC_BINDING"],
            },
        ],
        "grounding_evidence": [
            {
                "candidate_id": ids[0],
                "entity_id": target_a,
                "grounding_status": "GROUNDED_SYMBOLIC",
                "confidence_status": "DECLARED_FIXTURE",
                "provenance": ["SYMBOLIC_GROUNDING_CHALLENGE"],
            },
            {
                "candidate_id": ids[1],
                "entity_id": target_b,
                "grounding_status": "GROUNDED_SYMBOLIC",
                "confidence_status": "DECLARED_FIXTURE",
                "provenance": ["SYMBOLIC_GROUNDING_CHALLENGE"],
            },
        ],
        "intent_belief": {
            "candidate_probabilities": [
                {"candidate_id": ids[0], "probability": 0.5},
                {"candidate_id": ids[1], "probability": 0.5},
            ],
            "belief_status": "AVAILABLE_NORMALIZED",
            "belief_source": "DECLARED_UNINFORMATIVE",
            "normalization_status": "NORMALIZED_VALUES_PRESENT",
            "provenance": ["PREDECLARED_CHALLENGE_BELIEF"],
        },
        "question_channel_contract": {
            "query_id": f"M2C_QUERY_{index:03d}",
            "target_slot": slot,
            "question_status": "QUESTION_PROPOSAL",
            "candidate_partition": [
                {"answer_label": "OPTION_ONE", "candidate_ids": [ids[0]]},
                {"answer_label": "OPTION_TWO", "candidate_ids": [ids[1]]},
            ],
            "option_descriptions": [option_a, option_b],
            "question_text": f"Do you mean {option_a}, or {option_b}?",
            "answer_resolution_probability": 0.98,
            "no_answer_probability": 0.05,
            "answer_confusion_matrix": [],
            "delay_distribution": [],
            "channel_status": "VALID",
            "source": "SYNTHETIC_QUERY_CHANNEL_CHALLENGE_ONLY",
            "provenance": ["PREDECLARED_ANSWER_CHANNEL"],
        },
        "query_budget": 1,
        "active_query_state": {
            "status": "NONE",
            "query_id": None,
            "issued_at_monotonic": None,
            "attempt_count": 0,
        },
        "monotonic_now": 100.0,
        "answer_deadline_monotonic": 105.0,
        "wait_opportunity": {
            "wait_mode": "NOT_AVAILABLE",
            "future_information_expected": False,
            "expected_information_arrival_monotonic": None,
            "active_query_pending": False,
            "holding_capability_status": "NOT_AVAILABLE",
            "decision_window_status": "OPEN",
            "missed_opportunity_cost": 0.0,
            "wait_cost": 0.0,
            "information_resolution_probability": 0.0,
            "provenance": ["PREDECLARED_WAIT_OPPORTUNITY"],
        },
        "hard_gate_envelope": {
            "hard_safety_status": "AVAILABLE_CONTRACT_ONLY",
            "hard_rule_status": "PASS",
            "evidence_gate_status": "PASS",
            "query_episode_status": "CLEAR",
            "cache_status": "FRESH",
            "candidate_freshness_status": "FRESH",
            "candidate_set_status": "COMPLETE",
            "belief_status": "AVAILABLE_NORMALIZED",
            "control_authorized": False,
            "reason_codes": [],
        },
        "counterfactual_runtime_inputs": _matrix(ids, 8.0, 8.0),
        "declared_cost_parameters": {
            "query_cost": 0.1,
            "delay_cost_per_second": 0.01,
            "no_answer_penalty": 1.0,
            "strict_value_epsilon": 1e-9,
            "unknown_task_loss_policy": "FAIL_CLOSED_NO_SCALAR_IMPUTATION",
            "cost_semantics": "SYMBOLIC_TASK_TIME_QUERY_ONLY",
        },
        "provenance": PROVENANCE,
        "schema_version": SCHEMA_VERSION,
    }


def _hidden_meta(index: int, runtime: dict[str, Any]) -> dict[str, Any]:
    ids = runtime["counterfactual_runtime_inputs"]["candidate_ids"]
    return {
        "latent_true_intent": ids[(index - 1) % len(ids)],
        "expected_language_status": "SUPPORTED_COMPOSITIONAL",
        "expected_ambiguity_type": runtime["ambiguity_metadata_without_gold"]["ambiguity_type"],
        "expected_unresolved_slots": runtime["ambiguity_metadata_without_gold"]["unresolved_slots"],
        "expected_candidate_set": ids,
        "expected_question_status": runtime["question_channel_contract"]["question_status"],
        "expected_answer_status": "NOT_APPLICABLE",
        "expected_resolved_candidate_id": None,
        "answer_arrival_status": "NOT_APPLICABLE",
        "replan_required": False,
        "expected_unknown_routing": "ASK_ELIGIBLE_PASSENGER",
        "realized_answer_event": {
            "event_type": "NOT_APPLICABLE",
            "answer_text": None,
            "arrival_monotonic": None,
            "resolved_candidate_id": None,
        },
        "gold_reason_family": "PREDECLARED_SYMBOLIC_DECISION",
    }


def _set_ood(
    runtime: dict[str, Any],
    meta: dict[str, Any],
    *,
    instruction: str,
    language_status: str,
    ambiguity_type: str = "UNSUPPORTED",
    slots: list[str] | None = None,
    cause: str,
) -> None:
    runtime["raw_instruction"] = instruction
    runtime["ambiguity_metadata_without_gold"].update(
        {
            "ambiguity_type": ambiguity_type,
            "unresolved_slots": slots or [],
            "parser_support_regime": "OUT_OF_DOMAIN_FAIL_CLOSED",
            "unknown_cause_class": "PASSENGER_UNRESOLVABLE",
            "unknown_cause": cause,
        }
    )
    _set_question_status(runtime, "QUESTION_NOT_REALIZABLE")
    runtime["tags"].extend(["out_of_domain_fail_closed", "unknown_passenger_unresolvable"])
    meta.update(
        {
            "expected_language_status": language_status,
            "expected_ambiguity_type": ambiguity_type,
            "expected_unresolved_slots": slots or [],
            "expected_candidate_set": [],
            "expected_question_status": "QUESTION_NOT_REALIZABLE",
            "expected_unknown_routing": "FALLBACK_NONPASSENGER",
        }
    )


def _set_event(
    meta: dict[str, Any],
    status: str,
    text: str | None,
    arrival: str,
    resolved: str | None,
    arrival_time: float | None,
    replan: bool,
    event_type: str,
) -> None:
    meta.update(
        {
            "expected_answer_status": status,
            "expected_resolved_candidate_id": resolved,
            "answer_arrival_status": arrival,
            "replan_required": replan,
            "realized_answer_event": {
                "event_type": event_type,
                "answer_text": text,
                "arrival_monotonic": arrival_time,
                "resolved_candidate_id": resolved,
            },
        }
    )


def _configure_base(index: int, runtime: dict[str, Any], meta: dict[str, Any]) -> str:
    """Apply one predeclared blueprint and return its declared decision label."""

    _set_channel(runtime)
    ids = runtime["counterfactual_runtime_inputs"]["candidate_ids"]
    runtime["tags"].append("integrated_blind_base")

    if 1 <= index <= 16:
        runtime["tags"].extend(["consequence_over_ambiguity", "language_compositional_holdout"])
        if index <= 4:
            _set_equivalent(runtime)
            _set_question_status(runtime, "QUESTION_NOT_REQUIRED")
            meta.update(
                expected_question_status="QUESTION_NOT_REQUIRED",
                expected_pair_relation="TASK_EQUIVALENT",
                expected_unknown_routing="NOT_APPLICABLE",
                gold_reason_family="AMBIGUITY_WITH_TASK_EQUIVALENCE",
            )
            return "ACT"
        if index <= 8:
            runtime["tags"].extend(["unknown_passenger_resolvable", "cheap_answer_channel"])
            runtime["declared_cost_parameters"]["query_cost"] = 0.02
            meta.update(
                expected_pair_relation="TASK_CRITICAL",
                expected_unknown_routing="ASK_ELIGIBLE_PASSENGER",
                gold_reason_family="TASK_CRITICAL_CHEAP_PASSENGER_QUERY",
            )
            return "ASK"
        if index <= 12:
            runtime["tags"].extend(["unknown_passenger_resolvable", "answer_too_late"])
            _set_channel(runtime, delay=9.0)
            _set_belief(runtime, 0.8 if index % 2 else 0.2)
            if index >= 11:
                _set_belief(runtime, 0.5)
            meta.update(
                expected_pair_relation="TASK_CRITICAL",
                expected_unknown_routing="ACT_OR_FALLBACK_WHEN_QUERY_LATE",
                gold_reason_family="TASK_CRITICAL_ANSWER_AFTER_DEADLINE",
            )
            return "ACT" if index <= 10 else "FALLBACK_RECOMMENDED"
        causes = [
            "MISSING_WORLD_COORDINATE",
            "MISSING_PLAN_TO_TASK_MAPPING",
            "MISSING_ARTIFACT_PROVENANCE",
            "MISSING_PHYSICAL_EVIDENCE",
        ]
        cause = causes[index - 13]
        runtime["tags"].extend(["unknown_passenger_unresolvable", "critical_matrix_unknown"])
        runtime["ambiguity_metadata_without_gold"].update(
            {"unknown_cause_class": "PASSENGER_UNRESOLVABLE", "unknown_cause": cause}
        )
        _set_unknown_cell(runtime, cause)
        meta.update(
            expected_pair_relation="UNKNOWN",
            expected_unknown_routing="FALLBACK_NONPASSENGER",
            gold_reason_family="EVIDENCE_UNAVAILABLE_FAIL_CLOSED",
        )
        return "FALLBACK_RECOMMENDED"

    if 17 <= index <= 32:
        runtime["tags"].extend(["asymmetric_wrong_goal", "candidate_selection"])
        if index <= 20:
            runtime["tags"].append("language_compositional_holdout")
        definitions: dict[int, tuple[float, float, float, str]] = {
            17: (10.0, 1.0, 0.5, "ACT"),
            18: (1.0, 10.0, 0.5, "ACT"),
            19: (8.0, 2.0, 0.5, "ACT"),
            20: (2.0, 8.0, 0.5, "ACT"),
            21: (8.0, 2.0, 0.90, "ACT"),
            22: (8.0, 2.0, 0.50, "ACT"),
            23: (8.0, 2.0, 0.75, "ACT"),
            24: (8.0, 2.0, 0.85, "ACT"),
            25: (6.0, 1.0, 6.0 / 7.0, "FALLBACK_RECOMMENDED"),
            26: (6.0, 1.0, 0.855, "ACT"),
            27: (0.0, 0.0, 0.5, "ACT"),
            28: (3.0, 0.5, 0.80, "ACT"),
            29: (10.0, 1.0, 0.93, "ACT"),
            30: (5.0, 3.0, 0.20, "ACT"),
            31: (4.0, 2.0, 2.0 / 3.0, "FALLBACK_RECOMMENDED"),
            32: (2.0, 4.0, 0.75, "ACT"),
        }
        loss_ab, loss_ba, prior_a, decision = definitions[index]
        runtime["counterfactual_runtime_inputs"] = _matrix(ids, loss_ab, loss_ba)
        _set_belief(runtime, prior_a)
        _set_question_status(runtime, "QUESTION_NOT_REALIZABLE")
        runtime["declared_cost_parameters"]["query_cost"] = 10.0
        if index in set(range(17, 27)) | {28, 29}:
            runtime["tags"].append("strong_asymmetric_loss")
        if index == 25 or index == 31:
            runtime["tags"].append("exact_expected_loss_tie")
        if index == 26:
            runtime["tags"].append("near_expected_loss_tie")
        if index == 27:
            _set_equivalent(runtime)
            _set_question_status(runtime, "QUESTION_NOT_REQUIRED")
            runtime["tags"].append("equivalence_class_act")
        meta.update(
            expected_question_status=runtime["question_channel_contract"]["question_status"],
            expected_pair_relation=runtime["counterfactual_runtime_inputs"]["pair_relation"],
            expected_unknown_routing="NOT_APPLICABLE",
            gold_reason_family="ASYMMETRIC_WRONG_GOAL_SELECTION",
        )
        return decision

    if 33 <= index <= 48:
        runtime["tags"].extend(
            ["query_cost_delay_answer_quality", "monotonic_anchor", "answer_interaction_lifecycle", "unknown_passenger_resolvable"]
        )
        runtime["counterfactual_runtime_inputs"] = _matrix(ids, 8.0, 8.0)
        _set_belief(runtime, 0.5)
        _set_channel(runtime, diagonal=0.99, no_answer=0.02, resolution=0.99, delay=0.4)
        runtime["declared_cost_parameters"].update(query_cost=0.05, delay_cost_per_second=0.01, no_answer_penalty=0.5)
        meta.update(
            expected_pair_relation="TASK_CRITICAL",
            expected_unknown_routing="ASK_ELIGIBLE_PASSENGER",
            gold_reason_family="SINGLE_FACTOR_QUERY_VALUE_ANCHOR",
        )
        events = {
            33: ("RESOLVED", "the first option", "ON_TIME", ids[0], 100.4, True, "EXACT_OPTION"),
            34: ("RESOLVED", "first", "ON_TIME", ids[0], 100.4, True, "ORDINAL_FIRST"),
            35: ("RESOLVED", "second", "ON_TIME", ids[1], 100.4, True, "ORDINAL_SECOND"),
            36: ("RESOLVED", "No, the other one.", "ON_TIME", ids[1], 100.5, True, "CORRECTION"),
            37: ("RESOLVED", "not the first", "ON_TIME", ids[1], 100.5, True, "NEGATION"),
            38: ("RESOLVED", "the recessed one", "ON_TIME", ids[1], 100.5, True, "ELLIPTICAL"),
            39: ("STILL_AMBIGUOUS", "yes", "ON_TIME", None, 100.4, False, "AMBIGUOUS_YES"),
            40: ("STILL_AMBIGUOUS", "whichever", "ON_TIME", None, 100.4, False, "WHICHEVER"),
            41: ("CONTRADICTORY", "both first and second", "ON_TIME", None, 100.4, False, "CONTRADICTORY"),
            42: ("OUT_OF_DOMAIN", "the moon gate", "ON_TIME", None, 100.4, False, "NEW_TARGET"),
            43: ("NO_ANSWER", None, "NO_ANSWER", None, None, False, "NO_ANSWER"),
            44: ("EXPIRED", "second", "LATE", None, 106.0, False, "ANSWER_AFTER_DEADLINE"),
            45: ("RESOLVED", "option two", "ON_TIME", ids[1], 100.6, True, "EXACT_OPTION"),
            46: ("NO_ANSWER", "", "NO_ANSWER", None, None, False, "EMPTY_ANSWER"),
            47: ("RESOLVED", "option one", "ON_TIME", ids[0], 101.0, True, "LATEST_OBSERVATION_REPLAN"),
            48: ("EXPIRED", "the first one", "LATE", None, 105.5, False, "LATE_ELLIPSIS"),
        }
        _set_event(meta, *events[index])
        return "ASK"

    if 49 <= index <= 64:
        runtime["tags"].append("wait_stress")
        runtime["counterfactual_runtime_inputs"] = _matrix(ids, 8.0, 8.0)
        _set_belief(runtime, 0.5)
        _set_question_status(runtime, "QUESTION_NOT_REALIZABLE")
        meta.update(
            expected_question_status="QUESTION_NOT_REALIZABLE",
            expected_pair_relation="TASK_CRITICAL",
            expected_unknown_routing="NOT_APPLICABLE",
            gold_reason_family="WAIT_FUTURE_INFORMATION_VALUE",
        )
        if index in {49, 50, 63}:
            runtime["active_query_state"] = {
                "status": "ACTIVE",
                "query_id": f"M2C_ACTIVE_{index:03d}",
                "issued_at_monotonic": 99.0,
                "attempt_count": 1,
            }
            runtime["wait_opportunity"]["active_query_pending"] = True
            runtime["tags"].append("answer_interaction_lifecycle")
        if index == 49:
            _set_wait(runtime, mode="AWAIT_PENDING_ANSWER", information_probability=0.9, arrival=101.0, wait_cost=0.1, missed_cost=0.1)
            _set_event(meta, "STILL_AMBIGUOUS", None, "PENDING", None, None, False, "ACTIVE_QUERY_PENDING")
            return "WAIT"
        if index == 50:
            _set_wait(runtime, mode="AWAIT_PENDING_ANSWER", information_probability=0.9, arrival=106.0, wait_cost=0.1, missed_cost=0.1)
            runtime["tags"].extend(["answer_expected_after_deadline", "hard_gate_boundary"])
            _set_event(meta, "EXPIRED", "second", "LATE", None, 106.0, False, "PENDING_ANSWER_EXPIRES")
            return "FALLBACK_RECOMMENDED"
        if index == 51:
            _set_wait(runtime, mode="AWAIT_EXPECTED_OBSERVATION", information_probability=0.85, arrival=102.0, wait_cost=0.1, missed_cost=0.1)
            return "WAIT"
        if index == 52:
            _set_wait(runtime, mode="AWAIT_EXPECTED_OBSERVATION", information_probability=0.0, arrival=102.0, wait_cost=0.1, missed_cost=0.1, future=False)
            runtime["hard_gate_envelope"]["candidate_set_status"] = "INCOMPLETE"
            runtime["ambiguity_metadata_without_gold"]["boundary_condition"] = "INCOMPLETE_CANDIDATE_SET"
            runtime["tags"].append("hard_gate_boundary")
            _set_ood(runtime, meta, instruction=OOD_INSTRUCTIONS[8], language_status="UNGROUNDED", cause="NON_DISCRIMINATIVE_CANDIDATE_DESCRIPTIONS")
            return "FALLBACK_RECOMMENDED"
        if index == 53:
            _set_wait(runtime, mode="DEFER_BEFORE_DECISION_WINDOW", information_probability=0.8, arrival=102.0, wait_cost=0.1, missed_cost=0.1)
            return "WAIT"
        if index == 54:
            runtime["answer_deadline_monotonic"] = 101.0
            _set_wait(runtime, mode="AWAIT_EXPECTED_OBSERVATION", information_probability=0.7, arrival=100.8, wait_cost=0.2, missed_cost=0.1, window="CLOSING")
            return "WAIT"
        if index == 55:
            runtime["query_budget"] = 0
            _set_wait(runtime, mode="DEFER_BEFORE_DECISION_WINDOW", information_probability=0.9, arrival=101.0, wait_cost=0.1, missed_cost=0.1, holding="NOT_AVAILABLE")
            runtime["ambiguity_metadata_without_gold"]["boundary_condition"] = "ZERO_QUERY_BUDGET_AND_NO_HOLDING"
            runtime["tags"].append("hard_gate_boundary")
            return "FALLBACK_RECOMMENDED"
        if index == 56:
            _set_wait(runtime, mode="AWAIT_EXPECTED_OBSERVATION", information_probability=0.9, arrival=101.0, wait_cost=5.0, missed_cost=0.1)
            runtime["hard_gate_envelope"]["evidence_gate_status"] = "MISSING_EVIDENCE_MASK"
            runtime["ambiguity_metadata_without_gold"]["boundary_condition"] = "MISSING_EVIDENCE_MASK"
            runtime["tags"].append("hard_gate_boundary")
            return "FALLBACK_RECOMMENDED"
        if index == 57:
            _set_wait(runtime, mode="AWAIT_EXPECTED_OBSERVATION", information_probability=0.8, arrival=101.0, wait_cost=0.1, missed_cost=0.02)
            runtime["tags"].append("missed_opportunity_cost_low")
            return "WAIT"
        if index == 58:
            runtime["counterfactual_runtime_inputs"] = _matrix(ids, 2.0, 8.0)
            _set_belief(runtime, 0.8)
            _set_wait(runtime, mode="AWAIT_EXPECTED_OBSERVATION", information_probability=0.8, arrival=101.0, wait_cost=0.1, missed_cost=2.0)
            runtime["tags"].append("missed_opportunity_cost_high")
            return "ACT"
        if index == 59:
            _set_question_status(runtime, "QUESTION_PROPOSAL")
            _set_channel(runtime, diagonal=0.99, no_answer=0.01, resolution=0.99, delay=0.2)
            _set_wait(runtime, mode="AWAIT_EXPECTED_OBSERVATION", information_probability=0.99, arrival=100.3, wait_cost=0.03, missed_cost=0.02)
            runtime["declared_cost_parameters"].update(query_cost=0.09, delay_cost_per_second=0.01, no_answer_penalty=0.2)
            runtime["ambiguity_metadata_without_gold"]["unknown_cause_class"] = "PASSENGER_RESOLVABLE"
            runtime["tags"].append("wait_ask_near")
            meta.update(expected_question_status="QUESTION_PROPOSAL", expected_unknown_routing="ASK_ELIGIBLE_PASSENGER")
            return "WAIT"
        if index == 60:
            runtime["counterfactual_runtime_inputs"] = _matrix(ids, 3.0, 1.0)
            _set_belief(runtime, 0.8)
            _set_wait(runtime, mode="AWAIT_EXPECTED_OBSERVATION", information_probability=0.5, arrival=101.0, wait_cost=0.21, missed_cost=0.2)
            runtime["tags"].append("wait_act_near")
            return "ACT"
        if index == 61:
            runtime["hard_gate_envelope"]["hard_rule_status"] = "BLOCKED"
            runtime["answer_deadline_monotonic"] = 99.0
            runtime["ambiguity_metadata_without_gold"]["boundary_condition"] = "HARD_RULE_BLOCKED_AND_EXPIRED_DEADLINE"
            runtime["tags"].append("hard_gate_boundary")
            _set_ood(runtime, meta, instruction=OOD_INSTRUCTIONS[1], language_status="CONTRADICTORY", cause="CONTRADICTORY_INSTRUCTION")
            return "FALLBACK_RECOMMENDED"
        if index == 62:
            runtime["hard_gate_envelope"]["hard_safety_status"] = "NOT_AVAILABLE"
            runtime["ambiguity_metadata_without_gold"]["boundary_condition"] = "HARD_SAFETY_UNAVAILABLE"
            _set_unknown_cell(runtime, "MISSING_PHYSICAL_EVIDENCE")
            runtime["tags"].append("hard_gate_boundary")
            _set_ood(runtime, meta, instruction=OOD_INSTRUCTIONS[2], language_status="UNSUPPORTED", cause="UNSUPPORTED_MANEUVER")
            meta["expected_pair_relation"] = "UNKNOWN"
            return "FALLBACK_RECOMMENDED"
        if index == 63:
            runtime["hard_gate_envelope"]["candidate_freshness_status"] = "STALE"
            runtime["ambiguity_metadata_without_gold"]["boundary_condition"] = "STALE_CANDIDATE_ACTIVE_QUERY_LATE"
            _set_wait(runtime, mode="AWAIT_PENDING_ANSWER", information_probability=0.9, arrival=108.0, wait_cost=0.1, missed_cost=0.1)
            runtime["tags"].append("hard_gate_boundary")
            _set_ood(runtime, meta, instruction=OOD_INSTRUCTIONS[3], language_status="UNSUPPORTED", cause="UNSUPPORTED_TEMPORAL_RELATION")
            return "FALLBACK_RECOMMENDED"
        runtime["hard_gate_envelope"].update(cache_status="STALE", evidence_gate_status="MISSING_EVIDENCE_MASK")
        runtime["ambiguity_metadata_without_gold"]["boundary_condition"] = "STALE_CACHE_AND_MISSING_EVIDENCE"
        runtime["tags"].append("hard_gate_boundary")
        _set_ood(runtime, meta, instruction=OOD_INSTRUCTIONS[7], language_status="INCOMPLETE", ambiguity_type="UNSUPPORTED", slots=["reference_slot", "landmark_slot", "constraint_slot"], cause="MULTIPLE_UNRESOLVED_SLOTS")
        return "FALLBACK_RECOMMENDED"

    runtime["tags"].append("hard_gate_boundary")
    meta.update(
        expected_pair_relation="TASK_CRITICAL",
        expected_unknown_routing="FALLBACK_NONPASSENGER",
        gold_reason_family="HARD_GATE_OR_LANGUAGE_BOUNDARY",
    )
    if index == 65:
        _set_channel(runtime, diagonal=1.0, no_answer=0.0, resolution=1.0, delay=0.2)
        for row in runtime["question_channel_contract"]["answer_confusion_matrix"]:
            row["answer_probabilities"] = [
                {"answer_label": "OPTION_ONE", "probability": 1.0},
                {"answer_label": "OPTION_TWO", "probability": 0.0},
            ]
        runtime["ambiguity_metadata_without_gold"]["boundary_condition"] = "ZERO_LIKELIHOOD_ANSWER"
        _set_ood(runtime, meta, instruction=OOD_INSTRUCTIONS[4], language_status="UNGROUNDED", cause="MISSING_SYMBOLIC_ENTITY")
    elif index == 66:
        _set_belief(runtime, 0.5, status="INVALID_UPSTREAM_NORMALIZATION")
        runtime["ambiguity_metadata_without_gold"]["boundary_condition"] = "INVALID_BELIEF_NORMALIZATION"
        _set_ood(runtime, meta, instruction=OOD_INSTRUCTIONS[5], language_status="CONTRADICTORY", cause="CONFLICTING_GROUNDING")
    elif index == 67:
        _set_question_status(runtime, "QUESTION_NOT_REALIZABLE")
        runtime["ambiguity_metadata_without_gold"]["boundary_condition"] = "EXACT_UTILITY_TIE"
        _set_ood(runtime, meta, instruction=OOD_INSTRUCTIONS[6], language_status="UNSUPPORTED", cause="NESTED_REFERENCE")
    elif index == 68:
        runtime["counterfactual_runtime_inputs"] = _matrix(ids, 1.0000000004, 1.0)
        _set_belief(runtime, 0.5)
        _set_question_status(runtime, "QUESTION_NOT_REALIZABLE")
        runtime["ambiguity_metadata_without_gold"].update(
            boundary_condition="NEAR_FLOATING_POINT_BOUNDARY",
            unknown_cause_class="NOT_APPLICABLE",
            unknown_cause="NONE",
        )
        runtime["tags"].append("near_floating_point_boundary")
        meta.update(
            expected_question_status="QUESTION_NOT_REALIZABLE",
            expected_unknown_routing="NOT_APPLICABLE",
            gold_reason_family="NEAR_BOUNDARY_UNIQUE_ACT",
        )
        return "ACT"
    elif index == 69:
        runtime["ambiguity_metadata_without_gold"]["boundary_condition"] = "QUESTION_PARTITION_INCOMPLETE"
        _set_ood(runtime, meta, instruction=OOD_INSTRUCTIONS[8], language_status="INCOMPLETE", cause="NON_DISCRIMINATIVE_CANDIDATE_DESCRIPTIONS")
    elif index == 70:
        runtime["active_query_state"] = {
            "status": "ACTIVE",
            "query_id": "M2C_ACTIVE_070",
            "issued_at_monotonic": 98.0,
            "attempt_count": 2,
        }
        runtime["hard_gate_envelope"]["query_episode_status"] = "ACTIVE_QUERY_CONFLICT"
        runtime["ambiguity_metadata_without_gold"]["boundary_condition"] = "ACTIVE_QUERY_CONFLICT_REPEATED_ATTEMPT"
        runtime["tags"].append("answer_interaction_lifecycle")
        _set_ood(runtime, meta, instruction=OOD_INSTRUCTIONS[9], language_status="UNSUPPORTED", cause="REPEATED_QUERY_WITH_ACTIVE_QUERY")
        _set_event(meta, "STILL_AMBIGUOUS", "yes", "ON_TIME", None, 100.1, False, "REPEATED_QUERY_ATTEMPT")
    elif index == 71:
        third = "CAND_C"
        runtime["candidate_input_contract"]["candidate_count"] = 3
        runtime["candidate_plan_records"].append(
            {
                "candidate_id": third,
                "source_observation_id": "M2C_OBS_071",
                "plan_frame": "MODEL_LOCAL_RAW",
                "plan_unit": "RAW_UNIT",
                "raw_route_tokens": ["origin", "approach_071", "gamma_branch"],
                "raw_speed_profile_tokens": ["nominal", "yield_capable"],
                "candidate_freshness_status": "FRESH",
                "cache_status": "FRESH",
                "provenance": ["SYNTHETIC_PLAN_CHALLENGE_ONLY"],
            }
        )
        runtime["candidate_specific_task_bindings"].append(
            {
                "candidate_id": third,
                "source_candidate_id": third,
                "task_family": "MANEUVER_BRANCH",
                "symbolic_target_type": "SYMBOLIC_ROUTE_TARGET",
                "symbolic_target_id": "M2C_TARGET_071_GAMMA",
                "binding_status": "BOUND",
                "frame_or_semantic_domain": "IDENTIFIER",
                "provenance": ["HAND_AUTHORED_SYMBOLIC_BINDING"],
            }
        )
        runtime["grounding_evidence"].append(
            {
                "candidate_id": third,
                "entity_id": "M2C_TARGET_071_GAMMA",
                "grounding_status": "GROUNDED_SYMBOLIC",
                "confidence_status": "DECLARED_FIXTURE",
                "provenance": ["SYMBOLIC_GROUNDING_CHALLENGE"],
            }
        )
        runtime["symbolic_scene_table"].append(
            {
                "entity_id": "M2C_TARGET_071_GAMMA",
                "entity_type": "SYMBOLIC_ROUTE_TARGET",
                "display_name": "the third mutually exclusive access marker",
                "observable_attributes": {"surface": "matte", "relative_order": 3},
                "supported_slots": ["order_slot"],
                "grounding_status": "GROUNDED_SYMBOLIC",
                "frame_or_semantic_domain": "IDENTIFIER",
            }
        )
        runtime["intent_belief"]["candidate_probabilities"] = [
            {"candidate_id": "CAND_A", "probability": 1.0 / 3.0},
            {"candidate_id": "CAND_B", "probability": 1.0 / 3.0},
            {"candidate_id": "CAND_C", "probability": 1.0 / 3.0},
        ]
        runtime["counterfactual_runtime_inputs"]["candidate_ids"] = ["CAND_A", "CAND_B", "CAND_C"]
        runtime["counterfactual_runtime_inputs"]["cells"] = [
            _cell(a, h, "PASS" if a == h else "FAIL", 0.0 if a == h else 4.0, a != h)
            for a in ["CAND_A", "CAND_B", "CAND_C"]
            for h in ["CAND_A", "CAND_B", "CAND_C"]
        ]
        runtime["counterfactual_runtime_inputs"]["matrix_status"] = "COMPLETE_KNOWN_OUTSIDE_K"
        runtime["ambiguity_metadata_without_gold"]["boundary_condition"] = "CANDIDATE_COUNT_GREATER_THAN_K"
        _set_ood(runtime, meta, instruction=OOD_INSTRUCTIONS[10], language_status="UNSUPPORTED", ambiguity_type="UNSUPPORTED", slots=["order_slot"], cause="CANDIDATE_COUNT_GREATER_THAN_K")
    else:
        runtime["candidate_plan_records"][0].update(plan_frame="UNSUPPORTED_SENSOR_FRAME", plan_unit="LUNAR_YARD")
        runtime["hard_gate_envelope"]["evidence_gate_status"] = "UNSUPPORTED_FRAME_OR_UNIT"
        runtime["ambiguity_metadata_without_gold"]["boundary_condition"] = "UNSUPPORTED_FRAME_OR_UNIT"
        _set_unknown_cell(runtime, "UNSUPPORTED_FRAME_OR_UNIT")
        _set_ood(runtime, meta, instruction=OOD_INSTRUCTIONS[11], language_status="UNSUPPORTED", cause="UNSUPPORTED_FRAME_OR_UNIT")
        meta["expected_pair_relation"] = "UNKNOWN"
    return "FALLBACK_RECOMMENDED"


def _realized_task_loss(runtime: dict[str, Any], meta: dict[str, Any], result: Any) -> float | None:
    if result.decision == "ACT" and result.act_target_type == "EQUIVALENCE_CLASS":
        return 0.0
    action_id = result.selected_candidate_id
    if result.decision in {"ASK", "WAIT"}:
        action_id = meta["expected_resolved_candidate_id"]
    if action_id is None:
        return None
    latent = meta["latent_true_intent"]
    for cell in runtime["counterfactual_runtime_inputs"]["cells"]:
        if cell["action_candidate_id"] == action_id and cell["hypothesis_candidate_id"] == latent:
            return cell["task_error_cost"]
    return None


def _finalize_hidden(
    runtime: dict[str, Any],
    meta: dict[str, Any],
    declared_decision: str,
    *,
    companion_of: str | None = None,
    metamorphic_relation: str | None = None,
) -> dict[str, Any]:
    result = evaluate_case(runtime)
    if result.decision != declared_decision:
        raise ValueError(
            f"AUTHOR_DECLARATION_ORACLE_MISMATCH:{runtime['challenge_id']}:{declared_decision}:{result.decision}:{result.reason}"
        )
    pair_relation = runtime["counterfactual_runtime_inputs"]["pair_relation"]
    hidden = {
        "challenge_id": runtime["challenge_id"],
        "group_id": runtime["group_id"],
        **meta,
        "expected_pair_relation": meta.get("expected_pair_relation", pair_relation),
        "expected_decision": declared_decision,
        "expected_act_target_type": result.act_target_type,
        "expected_selected_candidate_id": result.selected_candidate_id,
        "expected_wait_mode": result.wait_mode,
        "realized_task_loss": _realized_task_loss(runtime, meta, result),
        "oracle_best_runtime_action": result.runtime_action,
        "oracle_expected_loss": result.expected_loss,
        "oracle_consistency_reason": result.reason,
        "companion_of": companion_of,
        "metamorphic_relation": metamorphic_relation,
        "provenance": [
            "EVALUATION_ONLY_HAND_AUTHORED_GOLD",
            "INDEPENDENT_BRUTE_FORCE_CONSISTENCY_CHECKED",
            "NOT_RUNTIME_ACCESSIBLE",
        ],
        "schema_version": SCHEMA_VERSION,
    }
    validate_hidden_case(hidden, runtime)
    return hidden


def _swap_candidate_id(candidate_id: str | None) -> str | None:
    return {"CAND_A": "CAND_B", "CAND_B": "CAND_A"}.get(candidate_id, candidate_id)


def _swap_runtime(base: dict[str, Any], number: int) -> dict[str, Any]:
    result = copy.deepcopy(base)
    result["challenge_id"] = f"M2C-SWAP-{number:03d}"
    result["case_type"] = "SWAP_COMPANION"
    result["tags"] = sorted(set(result["tags"] + ["candidate_swap_metamorphic"]))
    result["metamorphic_link"] = {
        "source_challenge_id": base["challenge_id"],
        "transform": "CANDIDATE_LABEL_AND_ORDER_SWAP",
    }
    for key in ("candidate_plan_records", "candidate_specific_task_bindings", "grounding_evidence"):
        transformed = []
        for record in reversed(result[key]):
            record["candidate_id"] = _swap_candidate_id(record.get("candidate_id"))
            if "source_candidate_id" in record:
                record["source_candidate_id"] = _swap_candidate_id(record["source_candidate_id"])
            transformed.append(record)
        result[key] = transformed

    belief = result["intent_belief"]["candidate_probabilities"]
    result["intent_belief"]["candidate_probabilities"] = [
        {"candidate_id": "CAND_A", "probability": belief[1]["probability"]},
        {"candidate_id": "CAND_B", "probability": belief[0]["probability"]},
    ]
    matrix = result["counterfactual_runtime_inputs"]
    transformed_cells = []
    for cell in matrix["cells"]:
        cell["action_candidate_id"] = _swap_candidate_id(cell["action_candidate_id"])
        cell["hypothesis_candidate_id"] = _swap_candidate_id(cell["hypothesis_candidate_id"])
        transformed_cells.append(cell)
    order = {"CAND_A": 0, "CAND_B": 1}
    matrix["cells"] = sorted(
        transformed_cells,
        key=lambda item: (order[item["action_candidate_id"]], order[item["hypothesis_candidate_id"]]),
    )
    matrix["candidate_ids"] = ["CAND_A", "CAND_B"]

    channel = result["question_channel_contract"]
    rows = channel["answer_confusion_matrix"]
    transformed_rows = []
    for row in reversed(rows):
        row["hypothesis_candidate_id"] = _swap_candidate_id(row["hypothesis_candidate_id"])
        probabilities = {item["answer_label"]: item["probability"] for item in row["answer_probabilities"]}
        row["answer_probabilities"] = [
            {"answer_label": "OPTION_ONE", "probability": probabilities["OPTION_TWO"]},
            {"answer_label": "OPTION_TWO", "probability": probabilities["OPTION_ONE"]},
        ]
        transformed_rows.append(row)
    channel["answer_confusion_matrix"] = transformed_rows
    if channel["question_status"] == "QUESTION_PROPOSAL":
        channel["option_descriptions"] = list(reversed(channel["option_descriptions"]))
        channel["candidate_partition"] = [
            {"answer_label": "OPTION_ONE", "candidate_ids": ["CAND_A"]},
            {"answer_label": "OPTION_TWO", "candidate_ids": ["CAND_B"]},
        ]
        channel["question_text"] = (
            f"Do you mean {channel['option_descriptions'][0]}, or {channel['option_descriptions'][1]}?"
        )
    validate_runtime_case(result)
    return result


def _swap_meta(base_meta: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base_meta)
    result["latent_true_intent"] = _swap_candidate_id(result["latent_true_intent"])
    result["expected_candidate_set"] = [
        _swap_candidate_id(item) for item in reversed(result["expected_candidate_set"])
    ]
    result["expected_resolved_candidate_id"] = _swap_candidate_id(result["expected_resolved_candidate_id"])
    result["realized_answer_event"]["resolved_candidate_id"] = _swap_candidate_id(
        result["realized_answer_event"]["resolved_candidate_id"]
    )
    return result


def _make_monotonic(
    base: dict[str, Any],
    number: int,
    factor: str,
) -> dict[str, Any]:
    result = copy.deepcopy(base)
    result["challenge_id"] = f"M2C-MONO-{number:03d}"
    result["case_type"] = "MONOTONIC_COMPANION"
    result["tags"] = sorted(set(result["tags"] + ["cost_channel_monotonic_companion", f"monotonic_{factor}"]))
    result["metamorphic_link"] = {
        "source_challenge_id": base["challenge_id"],
        "transform": "SINGLE_DECLARED_FACTOR_CHANGE",
        "changed_factor": factor,
    }
    if factor == "query_cost":
        result["declared_cost_parameters"]["query_cost"] = 5.0
    elif factor == "answer_delay":
        result["question_channel_contract"]["delay_distribution"] = [{"seconds": 9.0, "probability": 1.0}]
    elif factor == "no_answer_probability":
        result["question_channel_contract"]["no_answer_probability"] = 0.95
    elif factor == "answer_informativeness":
        for row in result["question_channel_contract"]["answer_confusion_matrix"]:
            row["answer_probabilities"] = [
                {"answer_label": "OPTION_ONE", "probability": 0.5},
                {"answer_label": "OPTION_TWO", "probability": 0.5},
            ]
    elif factor == "deadline":
        result["answer_deadline_monotonic"] = 100.1
    else:
        raise ValueError(f"UNKNOWN_MONOTONIC_FACTOR:{factor}")
    validate_runtime_case(result)
    return result


def _monotonic_relation(factor: str) -> str:
    return {
        "query_cost": "QUERY_COST_INCREASE_ASK_VALUE_NONINCREASING",
        "answer_delay": "ANSWER_DELAY_INCREASE_ASK_VALUE_NONINCREASING",
        "no_answer_probability": "NO_ANSWER_PROBABILITY_INCREASE_ASK_VALUE_NONINCREASING",
        "answer_informativeness": "ANSWER_INFORMATIVENESS_DECREASE_ASK_VALUE_NONINCREASING",
        "deadline": "DEADLINE_TIGHTENING_ASK_VALUE_NONINCREASING",
    }[factor]


def _coverage(runtime_cases: list[dict[str, Any]]) -> dict[str, Any]:
    categories = {
        "consequence_over_ambiguity": [],
        "asymmetric_wrong_goal": [],
        "strong_asymmetric_loss": [],
        "query_cost_delay_answer_quality": [],
        "wait_stress": [],
        "unknown_passenger_resolvable": [],
        "unknown_passenger_unresolvable": [],
        "language_compositional_holdout": [],
        "out_of_domain_fail_closed": [],
        "answer_interaction_lifecycle": [],
        "hard_gate_boundary": [],
        "candidate_swap_metamorphic": [],
        "cost_channel_monotonic_companion": [],
    }
    for case in runtime_cases:
        tags = set(case["tags"])
        for category in categories:
            if category in tags:
                categories[category].append(case["challenge_id"])
    return {
        "schema_version": SCHEMA_VERSION,
        "challenge_name": CHALLENGE_NAME,
        "counting_note": "Companions share group_id with their base and are not independent base samples.",
        "minimum_requirements": {
            "runtime_cases": 112,
            "base_cases": 72,
            "swap_companions": 24,
            "monotonic_companions": 16,
            "consequence_over_ambiguity_base": 16,
            "asymmetric_wrong_goal_base": 16,
            "strong_asymmetric_loss_base": 12,
            "query_cost_channel_base": 16,
            "wait_stress_all_case_types": 20,
            "unknown_passenger_resolvable": 10,
            "unknown_passenger_unresolvable": 10,
            "language_compositional_holdout": 20,
            "out_of_domain_fail_closed": 12,
            "answer_interaction_lifecycle": 16,
            "hard_gate_boundary": 16,
        },
        "category_case_ids": categories,
        "category_counts": {key: len(value) for key, value in categories.items()},
        "monotonic_factor_counts": {
            factor: sum(f"monotonic_{factor}" in case["tags"] for case in runtime_cases)
            for factor in ("query_cost", "answer_delay", "no_answer_probability", "answer_informativeness", "deadline")
        },
        "case_type_counts": {
            case_type: sum(case["case_type"] == case_type for case in runtime_cases)
            for case_type in ("BASE", "SWAP_COMPANION", "MONOTONIC_COMPANION")
        },
        "group_count": len({case["group_id"] for case in runtime_cases}),
    }


def build_challenge() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    rng = random.Random(int(CHALLENGE_SEED_SHA256, 16))
    instruction_order = list(SUPPORTED_INSTRUCTIONS)
    rng.shuffle(instruction_order)

    runtime_cases: list[dict[str, Any]] = []
    hidden_cases: list[dict[str, Any]] = []
    base_records: dict[int, tuple[dict[str, Any], dict[str, Any], str]] = {}
    for index in range(1, 73):
        instruction = instruction_order[(index - 1) % len(instruction_order)]
        runtime = _base_runtime(index, instruction)
        meta = _hidden_meta(index, runtime)
        declared = _configure_base(index, runtime, meta)
        runtime["tags"] = sorted(set(runtime["tags"]))
        validate_runtime_case(runtime)
        hidden = _finalize_hidden(runtime, meta, declared)
        runtime_cases.append(runtime)
        hidden_cases.append(hidden)
        base_records[index] = (runtime, meta, declared)

    for number, base_index in enumerate(list(range(17, 33)) + list(range(49, 57)), start=1):
        base_runtime, base_meta, declared = base_records[base_index]
        runtime = _swap_runtime(base_runtime, number)
        meta = _swap_meta(base_meta)
        hidden = _finalize_hidden(
            runtime,
            meta,
            declared,
            companion_of=base_runtime["challenge_id"],
            metamorphic_relation="DECISION_INVARIANT_AND_SELECTED_ID_EXCHANGED_WHEN_UNIQUE_ACT",
        )
        runtime_cases.append(runtime)
        hidden_cases.append(hidden)

    factor_sequence = (
        ["query_cost"] * 4
        + ["answer_delay"] * 3
        + ["no_answer_probability"] * 3
        + ["answer_informativeness"] * 3
        + ["deadline"] * 3
    )
    for number, (base_index, factor) in enumerate(zip(range(33, 49), factor_sequence), start=1):
        base_runtime, base_meta, _ = base_records[base_index]
        runtime = _make_monotonic(base_runtime, number, factor)
        meta = copy.deepcopy(base_meta)
        hidden = _finalize_hidden(
            runtime,
            meta,
            "FALLBACK_RECOMMENDED",
            companion_of=base_runtime["challenge_id"],
            metamorphic_relation=_monotonic_relation(factor),
        )
        hidden["expected_monotonic_relation"] = _monotonic_relation(factor)
        hidden_cases.append(hidden)
        runtime_cases.append(runtime)

    runtime_cases.sort(key=lambda case: case["challenge_id"])
    hidden_cases.sort(key=lambda case: case["challenge_id"])
    runtime_document = {
        "schema_version": SCHEMA_VERSION,
        "challenge_name": CHALLENGE_NAME,
        "challenge_seed_sha256": CHALLENGE_SEED_SHA256,
        "runtime_observable_only": True,
        "control_authorized": False,
        "cases": runtime_cases,
    }
    hidden_document = {
        "schema_version": SCHEMA_VERSION,
        "challenge_name": CHALLENGE_NAME,
        "evaluation_only": True,
        "runtime_import_forbidden": True,
        "cases": hidden_cases,
    }
    coverage = _coverage(runtime_cases)
    provenance = {
        "schema_version": SCHEMA_VERSION,
        "challenge_name": CHALLENGE_NAME,
        "challenge_seed_sha256": CHALLENGE_SEED_SHA256,
        "seed_source_formula": "SHA256(DRIVECLARIFY_M2C_V0 + M1 inventory hash + M2A inventory hash + M2B policy hash + real S1 hash)",
        "seed_selected_once": True,
        "multiple_seed_search_performed": False,
        "gold_derived_from_policy_output": False,
        "m2b_policy_opened_or_imported_during_authoring": False,
        "old_fixture_content_opened_during_authoring": False,
        "authoring_method": "HAND_AUTHORED_FACTOR_BLUEPRINTS_WITH_INDEPENDENT_BRUTE_FORCE_CONSISTENCY_CHECK",
        "evidence_designation": ["FROZEN_TWO_SESSION_PROCEDURAL_BLIND", "NOT_EXTERNAL_HUMAN_REVIEW", "NOT_PAPER_RESULT"],
        "runtime_hidden_physically_separate": True,
        "m2c_b_evaluated": False,
    }
    return runtime_document, hidden_document, coverage, provenance


def write_challenge(report_dir: Path) -> dict[str, str]:
    runtime, hidden, coverage, provenance = build_challenge()
    paths = {
        "runtime": report_dir / "runtime_challenge.json",
        "hidden": report_dir / "evaluation_only_hidden.json",
        "coverage": report_dir / "COVERAGE_MATRIX.json",
        "provenance": report_dir / "AUTHORING_PROVENANCE.json",
    }
    write_json(paths["runtime"], runtime)
    write_json(paths["hidden"], hidden)
    write_json(paths["coverage"], coverage)
    write_json(paths["provenance"], provenance)
    return {key: stable_sha256(value) for key, value in {"runtime": runtime, "hidden": hidden, "coverage": coverage, "provenance": provenance}.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_REPORT_DIR)
    args = parser.parse_args()
    digests = write_challenge(args.output_dir)
    print(f"AUTHORED cases=112 seed={CHALLENGE_SEED_SHA256}")
    print("DOCUMENT_DIGESTS " + " ".join(f"{key}={value}" for key, value in sorted(digests.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
