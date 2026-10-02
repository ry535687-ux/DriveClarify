"""Versioned Formal Learned M1 -> existing M2B v0 offline adapter.

Only Formal M1 TRAIN/DEV records are admitted.  The rule-derived
CounterfactualOutcomeMatrix is authoritative.  Learned M1 is retained as
five-seed pair-level diagnostic evidence and is never an authorization input.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from driveclarify_decision.offline_decision_evaluation import run_runtime_record
from driveclarify_decision.query_value_policy import PolicyConfig


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSESSMENT_ROOT = REPO_ROOT / "reports/formal_learned_m1_assessment/DC-FORMAL-M1-ASSESS-20260804T072031Z"
TRAINING_ROOT = REPO_ROOT / "reports/formal_learned_m1_training/DC-FORMAL-M1-TRAINDEV-20260804T081727Z"
TEST_ROOT = REPO_ROOT / "reports/formal_learned_m1_test/DC-FORMAL-M1-TEST-20260804T091730Z"
V2_ROOT = REPO_ROOT / "reports/m1_real_dataset_expansion_v2/DC-M1-DATASET-EXP-V2-20260803T143000Z"
V3_ROOT = REPO_ROOT / "reports/m1_real_dataset_expansion_v3/DC-M1-DATASET-EXP-V3-20260804T055600Z"

ADAPTER_VERSION = "FORMAL_M1_TO_M2B_ADAPTER_V1"
PROTOCOL_VERSION = "M2B_FORMAL_OFFLINE_PROTOCOL_V1"
RUNTIME_SCHEMA = "driveclarify.m2b_formal_runtime_cases.v1"
GOLD_SCHEMA = "driveclarify.m2b_formal_evaluation_only_gold.v1"
SEEDS = (17, 29, 43, 59, 71)
LEGAL_SPLITS = frozenset({"TRAIN", "DEV"})
FORBIDDEN_RUNTIME_KEY_FRAGMENTS = (
    "true_intent",
    "latent_true",
    "oracle",
    "gold",
    "expected_decision",
    "evaluation_label",
    "test_result",
    "post_outcome",
)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _assert_no_forbidden_runtime_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower()
            if any(fragment in normalized for fragment in FORBIDDEN_RUNTIME_KEY_FRAGMENTS):
                raise ValueError(f"M2B_FORMAL_RUNTIME_LEAKAGE:{path}.{key}")
            _assert_no_forbidden_runtime_keys(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_no_forbidden_runtime_keys(item, f"{path}[{index}]")


def _dataset_sources() -> tuple[tuple[str, Path, str], ...]:
    return (
        (
            "V2",
            V2_ROOT / "combined_runtime_campaigns/DC-M1-V2-RUNTIME-C1-20260803T144700Z/stage_b/M1_REAL_DATASET_V2.json",
            "V2_NEW",
        ),
        ("V3", V3_ROOT / "M1_REAL_DATASET_V3.json", "V3_NEW"),
    )


def _unit_root(version: str, unit_id: str) -> Path:
    if version == "V2":
        return V2_ROOT / "units" / unit_id
    if version == "V3":
        return V3_ROOT / "units" / unit_id
    raise ValueError("FORMAL_M1_SOURCE_VERSION_INVALID")


def _candidate_id(unit_id: str, group: str) -> str:
    digest = hashlib.sha256(f"{ADAPTER_VERSION}:{unit_id}:{group}".encode("utf-8")).hexdigest()[:16]
    return f"M2B-CAND-{digest}"


def _case_unit_key(unit_id: str) -> str:
    return "M2B-UNIT-" + hashlib.sha256(f"{ADAPTER_VERSION}:{unit_id}".encode("utf-8")).hexdigest()[:20]


def _compact_plan(plan: Mapping[str, Any], mapping: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "repeat_id": str(plan["candidate_id"]),
        "repeat_index": int(plan["repeat_index"]),
        "route_plan": copy.deepcopy(plan["plan_points"]),
        "speed_plan": copy.deepcopy(plan["raw_speed"][0]),
        "plan_frame": plan["plan_frame"],
        "plan_unit": plan["plan_unit"],
        "route_plan_sha256": plan["plan_points_sha256"],
        "speed_plan_sha256": plan["speed_plan_hash"],
        "combined_plan_sha256": plan["combined_plan_hash"],
        "mapping_label": mapping.get("mapping_label"),
        "mapping_evidence_status": mapping.get("evidence_status"),
        "mapping_evidence_sha256": mapping.get("evidence_sha256"),
        "mapping_reason_codes": list(mapping.get("reason_codes", [])),
    }


def _sanitize_topology(topology: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": topology.get("schema_version"),
        "sha256": topology.get("sha256"),
        "decision_point": copy.deepcopy(topology.get("decision_point")),
        "evaluation_interval": copy.deepcopy(topology.get("evaluation_interval")),
        "branch_divergence_point": copy.deepcopy(topology.get("branch_divergence_point")),
        "branches": [
            {
                "semantic_role": branch.get("semantic_role"),
                "evaluation_polyline_world_xyz": copy.deepcopy(branch.get("evaluation_polyline_world_xyz")),
            }
            for branch in topology.get("branches", [])
        ],
    }


def build_adapter_units() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Build runtime-safe units, physically separate gold, and rule matrices.

    The monolithic frozen source files are parsed, but records are copied only
    when their control-plane split is TRAIN or DEV.  TEST records are never
    validated, tensorized, adapted, or returned.
    """

    units: list[dict[str, Any]] = []
    gold: list[dict[str, Any]] = []
    matrices: list[dict[str, Any]] = []
    for version, dataset_path, required_status in _dataset_sources():
        dataset = load_json(dataset_path)
        for source in dataset["records"]:
            if source.get("unit_status") != required_status or source.get("split") not in LEGAL_SPLITS:
                continue
            unit_id = str(source["unit_id"])
            root = _unit_root(version, unit_id)
            binding_path = root / "TASK_BINDING.json"
            topology_path = root / "BRANCH_TOPOLOGY_GROUND_TRUTH.json"
            binding = load_json(binding_path)
            topology = load_json(topology_path)
            mapper_path = Path(source["label_provenance"]["mapper_results_path"])
            mapper = load_json(mapper_path)
            mapper_by_plan = {str(row["candidate_id"]): row for row in mapper["per_plan"]}
            candidate_ids = {group: _candidate_id(unit_id, group) for group in ("A", "B")}
            candidates = []
            for group in ("A", "B"):
                source_binding = binding["candidate_bindings"][group]
                repeat_plans = [row for row in source["plans"] if row["candidate_group"] == group]
                if [int(row["repeat_index"]) for row in repeat_plans] != [1, 2, 3]:
                    raise ValueError("FORMAL_M1_REPEAT_SCHEDULE_INVALID")
                consensus = mapper["candidate_consensus"][group]
                candidates.append(
                    {
                        "candidate_id": candidate_ids[group],
                        "source_candidate_group": group,
                        "canonical_role": source_binding["required_branch"],
                        "canonical_task_binding": {
                            "task_family": "CANONICAL_BRANCH_TASK",
                            "symbolic_target_type": "BRANCH_TASK_EQUIVALENCE_CLASS",
                            "symbolic_target_id": binding["branch_task_equivalence_classes"][source_binding["required_branch"]],
                            "binding_status": "BOUND",
                            "binding_source": "FROZEN_CANDIDATE_SPECIFIC_TASK_BINDING",
                            "frame_or_semantic_domain": "TASK_BINDING",
                        },
                        "route_speed_repeats": [
                            _compact_plan(plan, mapper_by_plan[str(plan["candidate_id"])]) for plan in repeat_plans
                        ],
                        "rule_mapping_consensus": consensus,
                        "rule_mapping_availability": "KNOWN" if consensus in binding["known_mapping_labels"] else "UNKNOWN",
                    }
                )
            unit = {
                "schema_version": "driveclarify.formal_m1_to_m2b_adapter_unit.v1",
                "adapter_version": ADAPTER_VERSION,
                "control_plane_identity": {
                    "source_m1_unit_id": unit_id,
                    "source_version": version,
                    "development_split": source["split"],
                    "m2b_unit_key": _case_unit_key(unit_id),
                },
                "candidate_ids": [candidate_ids["A"], candidate_ids["B"]],
                "candidates": candidates,
                "frozen_topology": _sanitize_topology(topology),
                "rule_mapping_evidence": {
                    "mapper": mapper.get("mapper_name"),
                    "candidate_name_default_used": mapper.get("candidate_name_default_used"),
                    "candidate_consensus": {
                        candidate_ids[group]: mapper["candidate_consensus"][group] for group in ("A", "B")
                    },
                    "threshold_sha256": mapper.get("threshold_sha256"),
                    "mapper_results_sha256": file_sha256(mapper_path),
                    "authoritative_for_matrix": True,
                },
                "formal_learned_m1_diagnostic": {
                    "availability": "NOT_AVAILABLE_PENDING_FROZEN_CPU_INFERENCE",
                    "authorization_eligible": False,
                },
                "source_hashes": {
                    "dataset_sha256": file_sha256(dataset_path),
                    "task_binding_sha256": file_sha256(binding_path),
                    "topology_file_sha256": file_sha256(topology_path),
                    "topology_self_sha256": topology.get("sha256"),
                    "mapper_results_sha256": file_sha256(mapper_path),
                    "observation_package_manifest_sha256": source.get("observation_package", {}).get("sha256"),
                },
                "provenance": [
                    "FORMAL_M1_V2_V3_FROZEN_TRAIN_DEV",
                    "FROZEN_STATIC_BRANCH_PLAN_MAPPER_V1_EVIDENCE",
                    "FROZEN_CANDIDATE_SPECIFIC_TASK_BINDING",
                    "OFFLINE_DECISION_DEVELOPMENT",
                ],
                "availability": {
                    "plans": "AVAILABLE_3_PER_CANDIDATE",
                    "topology": "AVAILABLE_FROZEN",
                    "rule_mapping": "KNOWN" if all(c["rule_mapping_availability"] == "KNOWN" for c in candidates) else "UNKNOWN",
                    "unknown_reason": None
                    if all(c["rule_mapping_availability"] == "KNOWN" for c in candidates)
                    else "RULE_M1_CANDIDATE_MAPPING_UNKNOWN",
                },
            }
            matrix = build_rule_matrix(unit)
            units.append(unit)
            matrices.append({"m2b_unit_key": _case_unit_key(unit_id), **matrix})
            gold.append(
                {
                    "m2b_unit_key": _case_unit_key(unit_id),
                    "source_m1_unit_id": unit_id,
                    "development_split": source["split"],
                    "source_pair_task_label": source["pair_task_label"],
                    "evaluation_only": True,
                    "declared_before_runtime_evaluation": True,
                    "provenance": "FORMAL_M1_TRAIN_DEV_LABEL_FOR_CONTRACT_EVALUATION_ONLY",
                }
            )
    if len(units) != 36 or Counter(row["control_plane_identity"]["development_split"] for row in units) != Counter({"TRAIN": 25, "DEV": 11}):
        raise ValueError("FORMAL_M1_TRAIN_DEV_ADAPTER_COUNT_MISMATCH")
    if len({row["control_plane_identity"]["source_m1_unit_id"] for row in units}) != 36:
        raise ValueError("FORMAL_M1_ADAPTER_IDENTITY_DUPLICATE")
    return units, gold, matrices


def build_rule_matrix(unit: Mapping[str, Any]) -> dict[str, Any]:
    """Construct the complete 2x2 matrix from mapping x candidate binding."""

    candidates = list(unit["candidates"])
    cells = []
    for action in candidates:
        mapped_branch = action["rule_mapping_consensus"]
        mapped_class = None
        if mapped_branch in {"STRAIGHT_BRANCH", "RIGHT_TURN_BRANCH"}:
            mapped_class = "STRAIGHT_TASK_CLASS" if mapped_branch == "STRAIGHT_BRANCH" else "RIGHT_TASK_CLASS"
        for hypothesis in candidates:
            hypothesis_class = hypothesis["canonical_task_binding"]["symbolic_target_id"]
            if mapped_class is None:
                outcome = "UNKNOWN"
                cost = None
                wrong = None
                evidence_status = "INSUFFICIENT"
                reasons = ["RULE_M1_MAPPING_UNKNOWN", "UNKNOWN_NULL_SCALAR_PRESERVED"]
            else:
                matches = mapped_class == hypothesis_class
                outcome = "PASS" if matches else "FAIL"
                cost = 0.0 if matches else 1.0
                wrong = not matches
                evidence_status = "SUFFICIENT_SYMBOLIC_TASK_EVIDENCE"
                reasons = [
                    "SYMBOLIC_TASK_TARGET_MATCH" if matches else "SYMBOLIC_WRONG_GOAL_TASK_TARGET_MISMATCH",
                    "NO_PHYSICAL_SAFETY_INFERENCE",
                ]
            cells.append(
                {
                    "action_candidate_id": action["candidate_id"],
                    "hypothesis_candidate_id": hypothesis["candidate_id"],
                    "task_outcome": outcome,
                    "evidence_status": evidence_status,
                    "task_error_cost": cost,
                    "wrong_goal_indicator": wrong,
                    "provenance": [
                        "FROZEN_CANDIDATE_SPECIFIC_TASK_BINDING",
                        "FROZEN_PLAN_SEMANTIC_MAPPING",
                        "RULE_M1_AUTHORITATIVE_MATRIX_SOURCE",
                    ],
                    "reason_codes": reasons,
                }
            )
    unknown = sum(row["task_outcome"] == "UNKNOWN" for row in cells)
    action_targets = {
        candidate["candidate_id"]: candidate["rule_mapping_consensus"] for candidate in candidates
    }
    known_targets = list(action_targets.values())
    relation = (
        "UNKNOWN"
        if unknown
        else "TASK_EQUIVALENT"
        if len(set(known_targets)) == 1
        else "TASK_CRITICAL"
    )
    return {
        "schema_version": "driveclarify.m2b_counterfactual_outcome_matrix.formal.v1",
        "adapter_version": ADAPTER_VERSION,
        "candidate_ids": list(unit["candidate_ids"]),
        "cells": cells,
        "matrix_status": "COMPLETE_KNOWN" if unknown == 0 else "INCOMPLETE_OR_UNKNOWN",
        "known_cell_count": len(cells) - unknown,
        "unknown_cell_count": unknown,
        "authoritative_source": "RULE_M1_CANDIDATE_BINDING_X_FROZEN_PLAN_MAPPING",
        "authoritative_pair_relation": relation,
        "learned_pair_label_used_for_cells": False,
        "symbolic_task_cost_not_physical_safety": True,
    }


def build_operating_profiles() -> list[dict[str, Any]]:
    """Return the eight predeclared profiles in their frozen order."""

    base = {
        "intent_belief": {"source": "RULE_DERIVED", "probabilities_by_role": {"STRAIGHT_BRANCH": 0.7, "RIGHT_TURN_BRANCH": 0.3}},
        "answer_channel": {
            "correct_probability": 0.94,
            "ambiguous_probability": 0.02,
            "contradictory_probability": 0.01,
            "no_answer_probability": 0.03,
            "delay_distribution": [[0.5, 1.0]],
            "answer_resolution_probability": 1.0,
            "status": "AVAILABLE",
        },
        "query_cost": 0.05,
        "delay_cost_per_second": 0.01,
        "no_answer_penalty": 0.1,
        "now": 100.0,
        "deadline": 105.0,
        "query_budget": 1,
        "active_query": False,
        "wait": {"mode": "NOT_AVAILABLE", "future_information_expected": False, "arrival": None, "resolution_probability": 0.0, "wait_cost": 0.0, "missed_cost": 0.0},
        "unknown_cause": "PASSENGER_INTENT_AMBIGUITY",
        "passenger_resolvable": True,
    }
    definitions = [
        (
            "LOW_QUERY_COST_HIGH_ANSWER_QUALITY_ON_TIME",
            {
                "intent_belief": {"source": "DECLARED_UNINFORMATIVE", "probabilities_by_role": {"STRAIGHT_BRANCH": 0.5, "RIGHT_TURN_BRANCH": 0.5}},
                "query_cost": 0.01,
                "answer_channel": {**base["answer_channel"], "correct_probability": 0.97, "ambiguous_probability": 0.01, "contradictory_probability": 0.0, "no_answer_probability": 0.02, "delay_distribution": [[0.25, 1.0]]},
            },
        ),
        ("HIGH_QUERY_COST", {"query_cost": 0.8}),
        ("HIGH_DELAY_NEAR_DEADLINE", {"deadline": 101.0, "delay_cost_per_second": 0.25, "answer_channel": {**base["answer_channel"], "delay_distribution": [[4.0, 1.0]]}}),
        ("HIGH_NO_ANSWER_PROBABILITY", {"answer_channel": {**base["answer_channel"], "correct_probability": 0.17, "ambiguous_probability": 0.02, "contradictory_probability": 0.01, "no_answer_probability": 0.8}}),
        ("UNINFORMATIVE_ANSWER_CHANNEL", {"answer_channel": {**base["answer_channel"], "correct_probability": 0.485, "ambiguous_probability": 0.02, "contradictory_probability": 0.01, "no_answer_probability": 0.03, "answer_resolution_probability": 0.0}}),
        (
            "ACTIVE_QUERY_PENDING_ANSWER",
            {
                "intent_belief": {"source": "DECLARED_UNINFORMATIVE", "probabilities_by_role": {"STRAIGHT_BRANCH": 0.5, "RIGHT_TURN_BRANCH": 0.5}},
                "active_query": True,
                "query_budget": 0,
                "wait": {"mode": "AWAIT_PENDING_ANSWER", "future_information_expected": True, "arrival": 100.5, "resolution_probability": 0.95, "wait_cost": 0.005, "missed_cost": 0.005},
            },
        ),
        (
            "EXPECTED_FUTURE_OBSERVATION_AVAILABLE",
            {
                "intent_belief": {"source": "DECLARED_UNINFORMATIVE", "probabilities_by_role": {"STRAIGHT_BRANCH": 0.5, "RIGHT_TURN_BRANCH": 0.5}},
                "query_cost": 0.8,
                "wait": {"mode": "AWAIT_EXPECTED_OBSERVATION", "future_information_expected": True, "arrival": 101.0, "resolution_probability": 0.95, "wait_cost": 0.01, "missed_cost": 0.01},
            },
        ),
        ("NO_FUTURE_INFORMATION_OR_UNRESOLVABLE_CAUSE", {"query_cost": 0.8, "unknown_cause": "PHYSICAL_EVIDENCE_MISSING", "passenger_resolvable": False}),
    ]
    profiles = []
    for index, (name, override) in enumerate(definitions, 1):
        profile = copy.deepcopy(base)
        for key, value in override.items():
            profile[key] = copy.deepcopy(value)
        profile.update(
            {
                "profile_id": name,
                "frozen_order": index,
                "schema_version": "driveclarify.m2b_operating_profile.v1",
                "provenance": "SYNTHETIC_PREDECLARED_OPERATING_CONDITION_PROFILE",
                "hand_authored_not_real_user": True,
            }
        )
        profiles.append(profile)
    if [row["profile_id"] for row in profiles] != [row[0] for row in definitions]:
        raise AssertionError("OPERATING_PROFILE_ORDER_DRIFT")
    return profiles


def _answer_contract(profile: Mapping[str, Any], candidate_by_role: Mapping[str, str]) -> dict[str, Any]:
    channel = profile["answer_channel"]
    correct = float(channel["correct_probability"])
    ambiguous = float(channel["ambiguous_probability"])
    contradictory = float(channel["contradictory_probability"])
    wrong = 1.0 - correct - ambiguous - contradictory
    if wrong < -1e-12:
        raise ValueError("ANSWER_CHANNEL_PROBABILITY_INVALID")
    straight = candidate_by_role["STRAIGHT_BRANCH"]
    right = candidate_by_role["RIGHT_TURN_BRANCH"]
    rows = [
        [straight, [["OPTION_ONE", correct], ["OPTION_TWO", wrong], ["AMBIGUOUS", ambiguous], ["CONTRADICTORY", contradictory]]],
        [right, [["OPTION_ONE", wrong], ["OPTION_TWO", correct], ["AMBIGUOUS", ambiguous], ["CONTRADICTORY", contradictory]]],
    ]
    deadline = float(profile["deadline"])
    late_probability = sum(float(probability) for delay, probability in channel["delay_distribution"] if float(profile["now"]) + float(delay) > deadline)
    return {
        "P_answer_label_given_hypothesis": rows,
        "answer_correctness_confusion": {"correct": correct, "wrong": max(0.0, wrong)},
        "NO_ANSWER_probability": float(channel["no_answer_probability"]),
        "AMBIGUOUS_probability_given_answer": ambiguous,
        "CONTRADICTORY_probability_given_answer": contradictory,
        "LATE_probability_given_answer": late_probability,
        "delay_distribution_seconds": copy.deepcopy(channel["delay_distribution"]),
        "candidate_option_mapping": {"OPTION_ONE": straight, "OPTION_TWO": right},
        "deadline_monotonic": deadline,
        "channel_status": channel["status"],
        "provenance": ["SYNTHETIC_PREDECLARED_ANSWER_CHANNEL", profile["profile_id"]],
    }


def _runtime_record(unit: Mapping[str, Any], matrix: Mapping[str, Any], profile: Mapping[str, Any]) -> dict[str, Any]:
    by_role = {row["canonical_role"]: row for row in unit["candidates"]}
    ids = list(unit["candidate_ids"])
    candidate_by_role = {role: row["candidate_id"] for role, row in by_role.items()}
    probabilities = profile["intent_belief"]["probabilities_by_role"]
    answer_contract = _answer_contract(profile, candidate_by_role)
    channel_rows = answer_contract["P_answer_label_given_hypothesis"]
    symbolic_plans = {}
    hypothesis_bindings = {}
    for candidate in unit["candidates"]:
        candidate_id = candidate["candidate_id"]
        consensus = candidate["rule_mapping_consensus"]
        target = None
        if consensus == "STRAIGHT_BRANCH":
            target = "STRAIGHT_TASK_CLASS"
        elif consensus == "RIGHT_TURN_BRANCH":
            target = "RIGHT_TASK_CLASS"
        symbolic_plans[candidate_id] = {
            "mapped_symbolic_target_type": "BRANCH_TASK_EQUIVALENCE_CLASS" if target else None,
            "mapped_symbolic_target_id": target,
            "plan_frame": "SYMBOLIC_PLAN_TARGET" if target else None,
            "plan_unit": "IDENTIFIER" if target else None,
            "provenance": ["REAL_RECORDED_UNLABELED", "FORMAL_M1_TO_M2B_ADAPTER_V1", "FROZEN_RULE_MAPPING"],
            "source_artifacts": [unit["source_hashes"]["mapper_results_sha256"]],
            "reason_codes": ["RULE_M1_AUTHORITATIVE_MAPPING"] if target else ["RULE_M1_MAPPING_UNKNOWN"],
        }
        binding = candidate["canonical_task_binding"]
        hypothesis_bindings[candidate_id] = {
            **binding,
            "required_slots": ["canonical_branch_task"],
            "provenance": ["REAL_RECORDED_UNLABELED", "FROZEN_CANDIDATE_SPECIFIC_TASK_BINDING"],
            "reason_codes": ["RUNTIME_CANDIDATE_HYPOTHESIS_NOT_LATENT_GOLD"],
        }
    wait = profile["wait"]
    active_query_id = f"ACTIVE-{profile['profile_id']}" if profile["active_query"] else None
    case_id = f"{unit['control_plane_identity']['m2b_unit_key']}::{profile['frozen_order']:02d}"
    record = {
        "schema_version": "driveclarify.decision_runtime_fixture.v0",
        "formal_adapter_version": ADAPTER_VERSION,
        "decision_id": "DECISION-" + hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:20],
        "episode_id": case_id,
        "source_observation_id": unit["source_hashes"]["observation_package_manifest_sha256"],
        "m2b_unit_key": unit["control_plane_identity"]["m2b_unit_key"],
        "operating_profile_id": profile["profile_id"],
        "candidate_ids": ids,
        "candidate_roles": {row["candidate_id"]: row["canonical_role"] for row in unit["candidates"]},
        "candidate_pair_relation": matrix["authoritative_pair_relation"],
        "symbolic_plans": symbolic_plans,
        "hypothesis_bindings": hypothesis_bindings,
        "declared_wrong_goal_costs": {candidate_id: 1.0 for candidate_id in ids},
        "intent_belief": {
            "candidate_probabilities": [[row["candidate_id"], float(probabilities[row["canonical_role"]])] for row in unit["candidates"]],
            "belief_status": "AVAILABLE",
            "belief_source": profile["intent_belief"]["source"],
            "normalization_status": "NORMALIZED_PREDECLARED_PROFILE",
            "provenance": ["SYNTHETIC_PREDECLARED_OPERATING_CONDITION_PROFILE"],
        },
        "query_proposal": {
            "proposal_status": "QUESTION_PROPOSAL",
            "query_id": "QUERY-" + hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:16],
            "template_id": "CANONICAL_STRAIGHT_RIGHT_TEMPLATE_V1",
            "question_text": "Should I continue straight or take the right branch?",
            "candidate_partition": [["OPTION_ONE", [candidate_by_role["STRAIGHT_BRANCH"]]], ["OPTION_TWO", [candidate_by_role["RIGHT_TURN_BRANCH"]]]],
            "natural_language_generalization_claimed": False,
        },
        "answer_channel_contract_v1": answer_contract,
        "answer_channel": {
            "answer_resolution_probability": float(profile["answer_channel"]["answer_resolution_probability"]),
            "no_answer_probability": float(profile["answer_channel"]["no_answer_probability"]),
            "answer_confusion_matrix": channel_rows,
            "delay_distribution": copy.deepcopy(profile["answer_channel"]["delay_distribution"]),
            "channel_status": profile["answer_channel"]["status"],
            "source": "SYNTHETIC_PREDECLARED_ANSWER_CHANNEL",
            "provenance": ["SYNTHETIC_QUERY_CHANNEL_TEST_ONLY", profile["profile_id"]],
        },
        "query_budget": int(profile["query_budget"]),
        "active_query_id": active_query_id,
        "monotonic_now": float(profile["now"]),
        "answer_deadline_monotonic": float(profile["deadline"]),
        "time_to_decision_status": "OPEN" if float(profile["deadline"]) > float(profile["now"]) else "EXPIRED",
        "wait_opportunity": {
            "wait_mode": wait["mode"],
            "future_information_expected": bool(wait["future_information_expected"]),
            "expected_information_arrival_time": wait["arrival"],
            "active_query_pending": bool(profile["active_query"]),
            "holding_capability_status": "AVAILABLE_CONTRACT_ONLY" if wait["mode"] != "NOT_AVAILABLE" else "NOT_AVAILABLE",
            "decision_deadline_status": "OPEN",
            "missed_opportunity_cost": float(wait["missed_cost"]),
            "wait_cost": float(wait["wait_cost"]),
            "information_resolution_probability": float(wait["resolution_probability"]),
            "provenance": ["SYNTHETIC_PREDECLARED_FUTURE_INFORMATION_PROFILE"],
        },
        "hard_gate_envelope": {
            "hard_safety_status": "PASS",
            "hard_rule_status": "PASS",
            "evidence_gate_status": "PASS",
            "query_episode_status": "ACTIVE_QUERY_PENDING" if active_query_id else "NO_ACTIVE_QUERY",
            "cache_status": "FRESH",
            "candidate_freshness_status": "FRESH",
            "control_authorized": False,
        },
        "evidence_masks": {
            "unknown_causes": [profile["unknown_cause"]],
            "passenger_resolvable_unknown": bool(profile["passenger_resolvable"]),
            "ambiguity_present": True,
            "rule_matrix_authoritative": True,
            "learned_authorization_eligible": False,
        },
        "operating_parameters": {
            "query_cost": float(profile["query_cost"]),
            "delay_cost_per_second": float(profile["delay_cost_per_second"]),
            "no_answer_penalty": float(profile["no_answer_penalty"]),
            "unknown_task_loss_policy": "FAIL_CLOSED_NO_SCALAR_IMPUTATION",
            "strict_value_epsilon": 1e-9,
            "provenance": ["SYNTHETIC_PREDECLARED_OPERATING_CONDITION_PROFILE"],
        },
        "source_hashes": copy.deepcopy(unit["source_hashes"]),
        "provenance": ["FORMAL_M1_TRAIN_DEV_REAL_PLAN_ADAPTER", "M2B_V0_POLICY_INPUT", "DIAGNOSTIC_ONLY"],
    }
    _assert_no_forbidden_runtime_keys(record)
    return record


def build_runtime_cases(
    units: Sequence[Mapping[str, Any]],
    matrices: Sequence[Mapping[str, Any]],
    profiles: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    matrix_by_unit = {row["m2b_unit_key"]: row for row in matrices}
    cases = [
        _runtime_record(unit, matrix_by_unit[unit["control_plane_identity"]["m2b_unit_key"]], profile)
        for unit in units
        for profile in profiles
    ]
    if len(cases) != 288 or len({row["episode_id"] for row in cases}) != 288:
        raise ValueError("M2B_FORMAL_CASE_CROSS_PRODUCT_MISMATCH")
    _assert_no_forbidden_runtime_keys(cases)
    return cases


def _load_checkpoint(path: Path) -> tuple[Any, Mapping[str, Any]]:
    import torch
    from driveclarify_learned_m1.checkpoint import state_dict_sha256
    from driveclarify_learned_m1.model import LearnedM1

    try:
        payload = torch.load(str(path), map_location="cpu", weights_only=False)
    except TypeError:
        payload = torch.load(str(path), map_location="cpu")
    model = LearnedM1(**payload["model_kwargs"])
    model.load_state_dict(payload["model_state_dict"], strict=True)
    if state_dict_sha256(model.state_dict()) != payload["model_state_sha256"]:
        raise ValueError("FROZEN_CHECKPOINT_STATE_HASH_MISMATCH")
    model.eval()
    return model, payload


def run_frozen_learned_diagnostics(units: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run exactly 36 x 5 CPU single-unit forwards, with no TEST entrypoint."""

    if os.environ.get("CUDA_VISIBLE_DEVICES") != "":
        raise RuntimeError("CUDA_VISIBLE_DEVICES_MUST_BE_EMPTY")
    import torch
    from driveclarify_learned_m1.formal_data import MODEL_INPUT_KEYS, load_formal_train_dev_records, tensorize_formal_records

    if torch.cuda.is_initialized():
        raise RuntimeError("CUDA_CONTEXT_ALREADY_INITIALIZED")
    records = load_formal_train_dev_records()
    if len(records) != 36 or any(row.get("split") not in LEGAL_SPLITS for row in records):
        raise RuntimeError("LEARNED_DIAGNOSTIC_TRAIN_DEV_SCOPE_VIOLATION")
    unit_order = [row["control_plane_identity"]["source_m1_unit_id"] for row in units]
    record_by_id = {str(row["unit_id"]): row for row in records}
    ordered_records = [record_by_id[unit_id] for unit_id in unit_order]
    bundle = tensorize_formal_records(ordered_records, device=torch.device("cpu"))
    selected = load_json(TRAINING_ROOT / "DEV_SELECTED_CHECKPOINTS.json")
    selection_by_seed = {int(row["seed"]): row["selection"] for row in selected}
    per_unit: dict[str, list[dict[str, Any]]] = {key: [] for key in unit_order}
    forward_count = 0
    checkpoint_load_count = 0
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(2)
    for seed in SEEDS:
        selection = selection_by_seed[seed]
        checkpoint_path = Path(selection["selected_checkpoint_path"])
        if file_sha256(checkpoint_path) != selection["selected_checkpoint_sha256"]:
            raise RuntimeError("SELECTED_CHECKPOINT_FILE_HASH_MISMATCH")
        model, payload = _load_checkpoint(checkpoint_path)
        checkpoint_load_count += 1
        threshold = float(selection["threshold"])
        for index, unit_id in enumerate(unit_order):
            single = {name: bundle["model_inputs"][name][index : index + 1] for name in MODEL_INPUT_KEYS}
            with torch.inference_mode():
                output = model(single)
                task_probability = torch.softmax(output["task_logits"], dim=-1)[0]
                unknown_probability = float(torch.softmax(output["unknown_logits"], dim=-1)[0, 1].item())
            forward_count += 1
            task_index = int(task_probability.argmax().item())
            task_relation = ("TASK_EQUIVALENT", "TASK_CRITICAL")[task_index]
            final_relation = "UNKNOWN" if unknown_probability >= threshold else task_relation
            per_unit[unit_id].append(
                {
                    "seed": seed,
                    "selected_epoch": int(selection["epoch"]),
                    "unknown_threshold": threshold,
                    "checkpoint_sha256": selection["selected_checkpoint_sha256"],
                    "state_dict_sha256": payload["model_state_sha256"],
                    "task_logits": [float(value) for value in output["task_logits"][0].tolist()],
                    "task_probabilities": {
                        "TASK_EQUIVALENT": float(task_probability[0].item()),
                        "TASK_CRITICAL": float(task_probability[1].item()),
                    },
                    "unknown_probability": unknown_probability,
                    "task_relation": task_relation,
                    "final_pair_relation": final_relation,
                    "authorization_eligible": False,
                    "used_for_control": False,
                }
            )
    if forward_count != 180 or checkpoint_load_count != 5:
        raise RuntimeError("LEARNED_DIAGNOSTIC_FORWARD_BUDGET_MISMATCH")
    diagnostics = []
    for unit, source_unit_id in zip(units, unit_order):
        seeds = per_unit[source_unit_id]
        final_counts = Counter(row["final_pair_relation"] for row in seeds)
        task_counts = Counter(row["task_relation"] for row in seeds)
        diagnostics.append(
            {
                "m2b_unit_key": unit["control_plane_identity"]["m2b_unit_key"],
                "source_m1_unit_id": source_unit_id,
                "mode": "LEARNED_M1_DIAGNOSTIC",
                "seed_outputs": seeds,
                "seed_agreement": {
                    "task_relation_unanimous": len(task_counts) == 1,
                    "final_relation_unanimous": len(final_counts) == 1,
                    "task_relation_counts": dict(sorted(task_counts.items())),
                    "final_relation_counts": dict(sorted(final_counts.items())),
                },
                "authorization_eligible": False,
                "direct_decision_allowed": False,
                "best_seed_selected": False,
                "ensemble_used": False,
            }
        )
    audit = {
        "mode": "LEARNED_M1_DIAGNOSTIC",
        "unit_count": 36,
        "seed_count": 5,
        "model_forward_count": forward_count,
        "maximum_allowed_forward_count": 180,
        "checkpoint_load_count": checkpoint_load_count,
        "test_forward_count": 0,
        "test_tensorization_count": 0,
        "optimizer_step_count": 0,
        "backward_count": 0,
        "training_count": 0,
        "cpu_only": True,
        "cuda_context_count": 0,
        "gpu_compute_count": 0,
        "authorization_eligible": False,
    }
    return diagnostics, audit


def attach_diagnostics(units: Sequence[Mapping[str, Any]], diagnostics: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_key = {row["m2b_unit_key"]: row for row in diagnostics}
    result = copy.deepcopy(list(units))
    for unit in result:
        key = unit["control_plane_identity"]["m2b_unit_key"]
        unit["formal_learned_m1_diagnostic"] = copy.deepcopy(by_key[key])
        unit["formal_learned_m1_diagnostic"]["availability"] = "AVAILABLE_FIVE_FROZEN_SEEDS"
    return result


def run_policy_cases(
    cases: Sequence[Mapping[str, Any]],
    config: PolicyConfig | None = None,
    *,
    decision_observer: Callable[[Mapping[str, Any], Mapping[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Run the existing policy and optionally observe each completed output.

    The additive observer receives deep copies after the legacy output envelope
    is complete.  It cannot mutate the returned policy output or input case;
    with the default ``None`` this function is byte-for-byte behaviorally
    equivalent to the original producer path.
    """
    outputs = []
    for case in cases:
        output = run_runtime_record(case, config)
        output["episode_id"] = case["episode_id"]
        output["m2b_unit_key"] = case["m2b_unit_key"]
        output["operating_profile_id"] = case["operating_profile_id"]
        output["control_authorized"] = False
        output["override_applied"] = False
        output["authorization_eligible"] = False
        if decision_observer is not None:
            decision_observer(copy.deepcopy(case), copy.deepcopy(output))
        outputs.append(output)
    _assert_no_forbidden_runtime_keys(outputs)
    return outputs


def hybridize(
    rule_outputs: Sequence[Mapping[str, Any]],
    diagnostics: Sequence[Mapping[str, Any]],
    matrices: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    diag_by_key = {row["m2b_unit_key"]: row for row in diagnostics}
    matrix_by_key = {row["m2b_unit_key"]: row for row in matrices}
    results = []
    for output in rule_outputs:
        key = output["m2b_unit_key"]
        diagnostic = diag_by_key[key]
        rule_relation = matrix_by_key[key]["authoritative_pair_relation"]
        learned_relations = [row["final_pair_relation"] for row in diagnostic["seed_outputs"]]
        conflict = any(relation != rule_relation for relation in learned_relations)
        results.append(
            {
                "schema_version": "driveclarify.m2b_hybrid_conservative_result.v1",
                "episode_id": output["episode_id"],
                "m2b_unit_key": key,
                "operating_profile_id": output["operating_profile_id"],
                "mode": "HYBRID_CONSERVATIVE",
                "precedence": ["HARD_CONTRACTS", "AUTHORITATIVE_RULE_MATRIX", "LEARNED_DIAGNOSTIC_ONLY"],
                "rule_pair_relation": rule_relation,
                "learned_final_relations": learned_relations,
                "rule_learned_conflict": conflict,
                "seed_disagreement": not diagnostic["seed_agreement"]["final_relation_unanimous"],
                "rule_output": copy.deepcopy(output),
                "decision": output["recommendation"]["decision"],
                "selected_candidate_id": output["recommendation"]["selected_candidate_id"],
                "learned_overrode_rule": False,
                "rule_unknown_upgraded_by_learned": False,
                "authorization_eligible": False,
                "used_for_control": False,
                "control_authorized": False,
                "override_applied": False,
                "reason_codes": [
                    "RULE_LEARNED_CONFLICT_RECORDED_NO_OVERRIDE" if conflict else "RULE_LEARNED_CONSISTENT",
                    "HYBRID_DIAGNOSTIC_RECOMMENDATION_ONLY",
                ],
            }
        )
    return results


def decision_distribution(outputs: Sequence[Mapping[str, Any]], *, hybrid: bool = False) -> dict[str, int]:
    if hybrid:
        counts = Counter(str(row["decision"]) for row in outputs)
    else:
        counts = Counter(str(row["recommendation"]["decision"]) for row in outputs)
    return {name: int(counts.get(name, 0)) for name in ("ACT", "ASK", "WAIT", "FALLBACK_RECOMMENDED")}


def selected_task_cost(output: Mapping[str, Any], true_candidate_id: str) -> float | None:
    recommendation = output["recommendation"]
    if recommendation["decision"] != "ACT" or recommendation["selected_candidate_id"] is None:
        return None
    matrix = output["context"]["counterfactual_outcome_matrix"]
    for cell in matrix["cells"]:
        if cell["action_candidate_id"] == recommendation["selected_candidate_id"] and cell["hypothesis_candidate_id"] == true_candidate_id:
            return cell["task_error_cost"]
    return None


def action_macro_f1(expected_actual: Iterable[tuple[str, str]]) -> float:
    pairs = list(expected_actual)
    labels = ("ACT", "ASK", "WAIT", "FALLBACK_RECOMMENDED")
    f1 = []
    for label in labels:
        tp = sum(expected == actual == label for expected, actual in pairs)
        fp = sum(expected != label and actual == label for expected, actual in pairs)
        fn = sum(expected == label and actual != label for expected, actual in pairs)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return sum(f1) / len(f1)
