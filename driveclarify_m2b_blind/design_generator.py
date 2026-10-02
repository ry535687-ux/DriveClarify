"""Generate the frozen blind design without invoking the tested M2B policy."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from . import DESIGN_VERSION, REFERENCE_SOLVER_VERSION
from .contracts import atomic_replace, canonical_bytes, file_sha256, runtime_leak_paths, stable_hash
from .reference_solver import solve_case


REPO = Path(__file__).resolve().parents[1]
DEVELOPMENT_RUN = REPO / "reports/m2b_formal_offline_real_m1_integration/DC-M2B-FORMAL-OFFLINE-20260804T095508Z"
CORE_EPSILON = "0.005"


def _load(name: str) -> Any:
    return json.loads((DEVELOPMENT_RUN / name).read_text(encoding="utf-8"))


def _hash_id(prefix: str, value: Any) -> str:
    return f"{prefix}-{stable_hash(value)[:20]}"


def _profile(
    profile_id: str, *, prior=(0.5, 0.5), correct=(0.94, 0.94), ambiguous=0.02,
    contradictory=0.0, no_answer=0.04, resolution=1.0, delays=((0.4, 1.0),),
    now=200.0, deadline=205.0, query_cost=0.03, delay_cost=0.01,
    no_answer_penalty=0.1, query_budget=1, active=False, passenger=True,
    wait_mode="NOT_AVAILABLE", future=False, arrival=None, wait_resolution=0.0,
    wait_cost=0.0, missed=0.0, window="OPEN", boundary=False,
) -> dict[str, Any]:
    wrong_a = 1.0 - correct[0] - ambiguous - contradictory
    wrong_b = 1.0 - correct[1] - ambiguous - contradictory
    if min(wrong_a, wrong_b) < 0:
        raise ValueError("PROFILE_CONFUSION_INVALID")
    return {
        "profile_id": profile_id,
        "intent_prior": [prior[0], prior[1]],
        "answer_channel": {
            "answer_resolution_probability": resolution,
            "no_answer_probability": no_answer,
            "rows_by_role": [
                [["OPTION_ONE", correct[0]], ["OPTION_TWO", wrong_a], ["AMBIGUOUS", ambiguous], ["CONTRADICTORY", contradictory]],
                [["OPTION_ONE", wrong_b], ["OPTION_TWO", correct[1]], ["AMBIGUOUS", ambiguous], ["CONTRADICTORY", contradictory]],
            ],
            "delay_distribution": [list(x) for x in delays],
        },
        "deadline": {"now": now, "answer_deadline": deadline, "decision_window_status": window},
        "costs": {"query_cost": query_cost, "delay_cost_per_second": delay_cost,
                  "no_answer_penalty": no_answer_penalty, "strict_value_epsilon": CORE_EPSILON,
                  "unknown_task_loss_policy": "FAIL_CLOSED_NO_SCALAR_IMPUTATION"},
        "query": {"query_budget": query_budget, "active_query": active,
                  "proposal_status": "QUESTION_PROPOSAL", "passenger_resolvable": passenger},
        "wait": {"wait_mode": wait_mode, "future_information_expected": future,
                 "expected_information_arrival_time": arrival,
                 "holding_capability_status": "AVAILABLE_CONTRACT_ONLY" if wait_mode != "NOT_AVAILABLE" else "NOT_AVAILABLE",
                 "decision_deadline_status": window, "information_resolution_probability": wait_resolution,
                 "wait_cost": wait_cost, "missed_opportunity_cost": missed},
        "contract_state": {"hard_safety_status": "PASS", "hard_rule_status": "PASS",
                           "evidence_gate_status": "PASS", "cache_status": "FRESH",
                           "candidate_freshness_status": "FRESH", "control_authorized": False},
        "design_classification": "BOUNDARY_STRESS" if boundary else "CORE_NONBOUNDARY",
        "predeclared_not_observed_user_distribution": True,
    }


def track_r_profiles() -> list[dict[str, Any]]:
    return [
        _profile("R_ASYMMETRIC_CONFUSION_MODERATE_COST", prior=(0.58, 0.42), correct=(0.91, 0.73), ambiguous=0.04, contradictory=0.02,
                 no_answer=0.16, delays=((0.35, 0.55), (1.4, 0.45)), query_cost=0.12, delay_cost=0.025, no_answer_penalty=0.18),
        _profile("R_MODERATE_COST_MODERATE_NO_ANSWER", prior=(0.46, 0.54), correct=(0.86, 0.82), ambiguous=0.07, contradictory=0.03,
                 no_answer=0.27, delays=((0.7, 0.7), (2.2, 0.3)), query_cost=0.18, delay_cost=0.04, no_answer_penalty=0.22),
        _profile("R_SHORT_NONZERO_DELAY_NEAR_DEADLINE", prior=(0.52, 0.48), correct=(0.93, 0.88), ambiguous=0.03, contradictory=0.01,
                 no_answer=0.09, delays=((0.18, 0.65), (0.62, 0.35)), now=300.0, deadline=300.55, query_cost=0.045, delay_cost=0.08, no_answer_penalty=0.15),
        _profile("R_HIGH_VALUE_FUTURE_OBSERVATION_FINITE_MISS", prior=(0.5, 0.5), correct=(0.76, 0.78), ambiguous=0.12, contradictory=0.04,
                 no_answer=0.22, delays=((1.0, 1.0),), query_cost=0.24, delay_cost=0.05, wait_mode="AWAIT_EXPECTED_OBSERVATION",
                 future=True, arrival=404.0, now=400.0, deadline=408.0, wait_resolution=0.91, wait_cost=0.025, missed=0.035),
        _profile("R_CONTRADICTORY_AMBIGUOUS_MIXTURE", prior=(0.67, 0.33), correct=(0.63, 0.69), ambiguous=0.17, contradictory=0.09,
                 no_answer=0.13, delays=((0.6, 0.4), (1.8, 0.6)), query_cost=0.09, delay_cost=0.03, no_answer_penalty=0.27),
        _profile("R_ACTIVE_QUERY_EXPIRED_WINDOW_CONTRACT", prior=(0.5, 0.5), correct=(0.9, 0.84), ambiguous=0.05, contradictory=0.01,
                 no_answer=0.12, delays=((0.3, 1.0),), now=500.0, deadline=500.0, query_cost=0.07, delay_cost=0.04,
                 active=True, wait_mode="AWAIT_PENDING_ANSWER", future=False, arrival=500.0, wait_resolution=0.8,
                 wait_cost=0.01, missed=0.02, window="EXPIRED", boundary=True),
    ]


def track_s_profiles() -> list[dict[str, Any]]:
    return [
        _profile("S_ASYMMETRIC_HIGH_QUALITY_LOW_COST", prior=(0.5, 0.5), correct=(0.98, 0.84), ambiguous=0.01,
                 no_answer=0.02, delays=((0.2, 1.0),), query_cost=0.005, delay_cost=0.002, no_answer_penalty=0.03),
        _profile("S_BALANCED_HIGH_QUALITY_LOW_COST", prior=(0.5, 0.5), correct=(0.97, 0.97), ambiguous=0.01,
                 no_answer=0.01, delays=((0.1, 1.0),), query_cost=0.004, delay_cost=0.001, no_answer_penalty=0.02),
        _profile("S_MODERATE_COST_NO_ANSWER", prior=(0.55, 0.45), correct=(0.86, 0.8), ambiguous=0.06, contradictory=0.03,
                 no_answer=0.32, delays=((0.5, 0.7), (2.0, 0.3)), query_cost=0.2, delay_cost=0.04, no_answer_penalty=0.25),
        _profile("S_PRIOR_A_IMBALANCED_HIGH_QUERY_COST", prior=(0.82, 0.18), correct=(0.9, 0.88), ambiguous=0.03,
                 no_answer=0.12, query_cost=0.42, delay_cost=0.06, no_answer_penalty=0.3),
        _profile("S_PRIOR_B_IMBALANCED_NO_BUDGET", prior=(0.21, 0.79), correct=(0.91, 0.85), ambiguous=0.04,
                 no_answer=0.1, query_cost=0.08, query_budget=0),
        _profile("S_FUTURE_INFORMATION_HIGH_VALUE", prior=(0.5, 0.5), correct=(0.68, 0.66), ambiguous=0.14, contradictory=0.08,
                 no_answer=0.3, query_cost=0.28, delay_cost=0.05, now=600.0, deadline=610.0,
                 wait_mode="AWAIT_EXPECTED_OBSERVATION", future=True, arrival=604.0, wait_resolution=0.96,
                 wait_cost=0.01, missed=0.015),
        _profile("S_ACTIVE_QUERY_PENDING_VALUABLE_WAIT", prior=(0.5, 0.5), correct=(0.95, 0.92), ambiguous=0.02,
                 no_answer=0.08, query_cost=0.01, active=True, now=700.0, deadline=706.0,
                 wait_mode="AWAIT_PENDING_ANSWER", arrival=701.0, wait_resolution=0.98, wait_cost=0.005),
        _profile("S_UNRESOLVABLE_INFORMATION", prior=(0.5, 0.5), correct=(0.52, 0.52), ambiguous=0.24, contradictory=0.2,
                 no_answer=0.45, query_cost=0.03, passenger=False, wait_mode="NOT_AVAILABLE"),
        _profile("S_EXACT_TIE_CHANNEL", prior=(0.5, 0.5), correct=(0.5, 0.5), ambiguous=0.25, contradictory=0.25,
                 no_answer=0.0, query_cost=0.0, delay_cost=0.0, no_answer_penalty=0.0, boundary=True),
        _profile("S_DEADLINE_EXACT_BOUNDARY", prior=(0.5, 0.5), correct=(0.99, 0.99), ambiguous=0.005,
                 no_answer=0.0, now=800.0, deadline=800.0, query_cost=0.0, window="EXPIRED", boundary=True),
        _profile("S_ZERO_INFORMATION_WAIT", prior=(0.5, 0.5), correct=(0.75, 0.75), ambiguous=0.1,
                 no_answer=0.25, wait_mode="AWAIT_EXPECTED_OBSERVATION", future=True, arrival=904.0,
                 now=900.0, deadline=910.0, wait_resolution=0.0, wait_cost=0.0, boundary=True),
        _profile("S_ACTIVE_QUERY_STATE_CONFLICT", prior=(0.5, 0.5), correct=(0.9, 0.9), ambiguous=0.04,
                 no_answer=0.05, active=True, wait_mode="NOT_AVAILABLE", boundary=True),
    ]


PATTERNS = (
    "FULLY_TASK_EQUIVALENT", "UNIQUE_A_DOMINANT", "UNIQUE_B_DOMINANT", "CROSSED_TASK_CRITICAL",
    "A_SAFE_BOTH", "B_SAFE_BOTH", "ONE_DIAGONAL_UNKNOWN", "ONE_CROSS_UNKNOWN",
    "MULTIPLE_UNKNOWN", "ALL_UNKNOWN", "NO_UNIQUE_ACT", "TIED_EXPECTED_ACT_LOSS",
    "ASK_CAN_RESOLVE", "ASK_CANNOT_RESOLVE", "WAIT_CAN_RESOLVE", "WAIT_CANNOT_RESOLVE",
)


def _base_costs(kind: str, scale: float) -> list[list[float | None]]:
    table: dict[str, list[list[float | None]]] = {
        "FULLY_TASK_EQUIVALENT": [[0, 0], [0, 0]],
        "UNIQUE_A_DOMINANT": [[0.02, 0.08], [0.8, 1.2]],
        "UNIQUE_B_DOMINANT": [[0.9, 1.1], [0.03, 0.07]],
        "CROSSED_TASK_CRITICAL": [[0, 1.1], [1.3, 0]],
        "A_SAFE_BOTH": [[0, 0.04], [1.0, 0.7]],
        "B_SAFE_BOTH": [[0.9, 1.1], [0.05, 0]],
        "ONE_DIAGONAL_UNKNOWN": [[None, 1.0], [0.8, 0]],
        "ONE_CROSS_UNKNOWN": [[0, None], [1.2, 0]],
        "MULTIPLE_UNKNOWN": [[None, 1.0], [None, 0]],
        "ALL_UNKNOWN": [[None, None], [None, None]],
        "NO_UNIQUE_ACT": [[0.2, 0.8], [0.8, 0.2]],
        "TIED_EXPECTED_ACT_LOSS": [[0.1, 0.9], [0.7, 0.3]],
        "ASK_CAN_RESOLVE": [[0, 1.5], [1.0, 0]],
        "ASK_CANNOT_RESOLVE": [[0.35, 0.65], [0.65, 0.35]],
        "WAIT_CAN_RESOLVE": [[0, 1.8], [1.1, 0]],
        "WAIT_CANNOT_RESOLVE": [[0.45, 0.55], [0.55, 0.45]],
    }
    return [[None if v is None else round(v * scale, 6) for v in row] for row in table[kind]]


def matrix_archetypes() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for variant in range(5):
        # Variant 4 is the structured, all-pattern expansion triggered before
        # any tested-policy execution because the initial exact-solver pool had
        # fewer than 32 ASK records.  It changes every cell consequence scale,
        # not only the underrepresented action class.
        scale = (0.75, 1.0, 1.35, 1.7, 2.1)[variant]
        for kind in PATTERNS:
            ids = [f"SYN-{kind[:8]}-{variant}-A", f"SYN-{kind[:8]}-{variant}-B"]
            values = _base_costs(kind, scale)
            swap = variant in {2, 3}
            if swap:
                values = [[values[1 - a][1 - h] for h in range(2)] for a in range(2)]
            cells = []
            for ai, action in enumerate(ids):
                for hi, hypothesis in enumerate(ids):
                    cost = values[ai][hi]
                    cells.append({
                        "action_candidate_id": action, "hypothesis_candidate_id": hypothesis,
                        "task_outcome": "UNKNOWN" if cost is None else ("PASS" if cost == 0 else "FAIL"),
                        "task_error_cost": cost,
                        "evidence_status": "INSUFFICIENT" if cost is None else "SUFFICIENT_SYMBOLIC_TASK_EVIDENCE",
                        "task_binding": {"action_symbolic_target": f"TARGET_{ai}", "hypothesis_symbolic_target": f"TARGET_{hi}",
                                         "candidate_specific": True},
                    })
            relation = "TASK_EQUIVALENT" if kind == "FULLY_TASK_EQUIVALENT" else ("UNKNOWN" if any(c["task_outcome"] == "UNKNOWN" for c in cells) else "TASK_CRITICAL")
            body = {"candidate_ids": ids, "cells": cells, "matrix_relation": relation,
                    "structure": kind, "consequence_variant": variant, "candidate_swap_member": swap,
                    "not_derived_from_learned_pair_label": True, "symbolic_not_physical_safety": True}
            body["archetype_id"] = _hash_id("MATRIX-S", body)
            body["matrix_sha256"] = stable_hash({k: v for k, v in body.items() if k != "matrix_sha256"})
            result.append(body)
    # Explicit same-scale candidate-swap pairs.  Both orientations are frozen
    # together and can be checked profile-by-profile without invoking policy.
    for pair_index, kind in enumerate(PATTERNS[:8]):
        pair_id = f"CANDIDATE_SWAP_PAIR_{pair_index:02d}_{kind}"
        original = _base_costs(kind, 1.55)
        for orientation in ("ORIGINAL", "SWAPPED"):
            ids = [f"SYN-PAIR-{pair_index:02d}-A", f"SYN-PAIR-{pair_index:02d}-B"]
            values = original if orientation == "ORIGINAL" else [[original[1 - a][1 - h] for h in range(2)] for a in range(2)]
            cells = []
            for ai, action in enumerate(ids):
                for hi, hypothesis in enumerate(ids):
                    cost = values[ai][hi]
                    cells.append({"action_candidate_id": action, "hypothesis_candidate_id": hypothesis,
                        "task_outcome": "UNKNOWN" if cost is None else ("PASS" if cost == 0 else "FAIL"),
                        "task_error_cost": cost, "evidence_status": "INSUFFICIENT" if cost is None else "SUFFICIENT_SYMBOLIC_TASK_EVIDENCE",
                        "task_binding": {"action_symbolic_target": f"PAIR_TARGET_{ai}", "hypothesis_symbolic_target": f"PAIR_TARGET_{hi}", "candidate_specific": True}})
            relation = "TASK_EQUIVALENT" if kind == "FULLY_TASK_EQUIVALENT" else ("UNKNOWN" if any(c["task_outcome"] == "UNKNOWN" for c in cells) else "TASK_CRITICAL")
            body = {"candidate_ids": ids, "cells": cells, "matrix_relation": relation, "structure": kind,
                    "consequence_variant": "EXPLICIT_SWAP_PAIR_1.55X", "candidate_swap_member": True,
                    "candidate_swap_pair_id": pair_id, "candidate_swap_orientation": orientation,
                    "not_derived_from_learned_pair_label": True, "symbolic_not_physical_safety": True}
            body["archetype_id"] = _hash_id("MATRIX-S", body)
            body["matrix_sha256"] = stable_hash({k: v for k, v in body.items() if k != "matrix_sha256"})
            result.append(body)
    return sorted(result, key=lambda x: x["archetype_id"])


def _case(matrix: Mapping[str, Any], profile: Mapping[str, Any], *, track: str, unit_id: str | None = None,
          source_hashes: Mapping[str, Any] | None = None) -> dict[str, Any]:
    ids = list(matrix["candidate_ids"])
    prior_values = list(profile["intent_prior"])
    role_rows = copy.deepcopy(profile["answer_channel"]["rows_by_role"])
    if matrix.get("candidate_swap_orientation") == "SWAPPED":
        prior_values.reverse()
        swapped_rows = []
        for row in reversed(role_rows):
            probabilities = {label: value for label, value in row}
            swapped_rows.append([["OPTION_ONE", probabilities["OPTION_TWO"]],
                                 ["OPTION_TWO", probabilities["OPTION_ONE"]],
                                 ["AMBIGUOUS", probabilities["AMBIGUOUS"]],
                                 ["CONTRADICTORY", probabilities["CONTRADICTORY"]]])
        role_rows = swapped_rows
    rows = []
    for candidate_id, row in zip(ids, role_rows):
        rows.append([candidate_id, copy.deepcopy(row)])
    runtime_profile = {k: copy.deepcopy(v) for k, v in profile.items() if k not in {"design_classification", "predeclared_not_observed_user_distribution"}}
    body = {
        "track": track,
        "unit_id": unit_id,
        "matrix_id": matrix.get("archetype_id", matrix.get("m2b_unit_key")),
        "profile_id": profile["profile_id"],
        "candidate_ids": ids,
        "matrix_relation": matrix["matrix_relation"],
        "counterfactual_matrix": {"cells": copy.deepcopy(matrix["cells"]), "matrix_status": matrix.get("matrix_status", "FROZEN")},
        "intent_belief": {"candidate_probabilities": [[ids[0], prior_values[0]], [ids[1], prior_values[1]]],
                          "belief_status": "AVAILABLE", "belief_source": "PREDECLARED_PROFILE"},
        "answer_channel": {**copy.deepcopy(profile["answer_channel"]), "answer_confusion_matrix": rows},
        "query": copy.deepcopy(profile["query"]), "wait": copy.deepcopy(profile["wait"]),
        "deadline": copy.deepcopy(profile["deadline"]), "costs": copy.deepcopy(profile["costs"]),
        "contract_state": copy.deepcopy(profile["contract_state"]),
        "provenance_hashes": {"matrix_sha256": matrix["matrix_sha256"], "profile_sha256": stable_hash(runtime_profile),
                              **(dict(source_hashes or {}))},
        "authorization_eligible": False, "used_for_control": False, "control_authorized": False,
    }
    body["answer_channel"].pop("rows_by_role", None)
    body["case_id"] = _hash_id(f"CASE-{track}", body)
    return body


def _real_matrix(raw: Mapping[str, Any]) -> dict[str, Any]:
    cells = [{k: copy.deepcopy(v) for k, v in cell.items() if k != "wrong_goal_indicator"} for cell in raw["cells"]]
    body = {"m2b_unit_key": raw["m2b_unit_key"], "candidate_ids": copy.deepcopy(raw["candidate_ids"]),
            "cells": cells, "matrix_relation": raw["authoritative_pair_relation"],
            "matrix_status": raw["matrix_status"], "authoritative_source": raw["authoritative_source"],
            "track_name": "REAL_MATRIX_PROFILE_HOLDOUT"}
    body["matrix_sha256"] = stable_hash(body)
    return body


def _gold_extension(case: Mapping[str, Any], solution: Mapping[str, Any], classification: str) -> dict[str, Any]:
    record = dict(solution)
    selector = int(hashlib.sha256(case["case_id"].encode()).hexdigest(), 16) % 2
    latent = case["candidate_ids"][selector]
    cell = next(c for c in case["counterfactual_matrix"]["cells"]
                if c["action_candidate_id"] == (solution["gold_candidate_id"] or case["candidate_ids"][0])
                and c["hypothesis_candidate_id"] == latent)
    record.update({
        "latent_true_intent": latent,
        "wrong_goal_outcome": (cell["task_error_cost"] > 0) if solution["act_subtype"] == "UNIQUE_CANDIDATE" else None,
        "regret_reference": solution["expected_loss_by_legal_action"],
        "matrix_sha256": case["provenance_hashes"]["matrix_sha256"],
        "profile_sha256": case["provenance_hashes"]["profile_sha256"],
        "boundary_classification": classification,
    })
    return record


def generate(design_id: str, output: Path) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("BLIND_DESIGN_OUTPUT_MUST_BE_NEW_AND_EMPTY")
    output.mkdir(parents=True, exist_ok=True)
    development_profiles = _load("M2B_OPERATING_PROFILES.json")["profiles"]
    old_numeric = {stable_hash({k: v for k, v in row.items() if k not in {"profile_id", "frozen_order"}}) for row in development_profiles}
    r_profiles = track_r_profiles()
    s_profiles = track_s_profiles()
    for p in r_profiles:
        if stable_hash({k: v for k, v in p.items() if k not in {"profile_id", "design_classification"}}) in old_numeric:
            raise ValueError("TRACK_R_PROFILE_DUPLICATES_DEVELOPMENT_NUMERIC_COMBINATION")
    raw_matrices = _load("M2B_COUNTERFACTUAL_OUTCOME_MATRICES.json")["matrices"]
    units = {u["control_plane_identity"]["m2b_unit_key"]: u for u in _load("M2B_REAL_DEVELOPMENT_UNITS.json")["units"]}
    real_matrices = [_real_matrix(m) for m in raw_matrices]
    r_cases = [_case(m, p, track="R", unit_id=m["m2b_unit_key"], source_hashes=units[m["m2b_unit_key"]]["source_hashes"])
               for m in real_matrices for p in r_profiles]
    archetypes = matrix_archetypes()
    s_cases = [_case(m, p, track="S") for m in archetypes for p in s_profiles]
    all_cases = sorted((*r_cases, *s_cases), key=lambda x: x["case_id"])
    if len(r_cases) != 216 or len(archetypes) < 32:
        raise AssertionError("BLIND_TRACK_SIZE_CONTRACT_FAILED")
    if runtime_leak_paths({"cases": all_cases}):
        raise AssertionError("RUNTIME_PACKAGE_LEAKAGE")
    classification_by_profile = {p["profile_id"]: p["design_classification"] for p in (*r_profiles, *s_profiles)}
    gold = [_gold_extension(case, solve_case(case), classification_by_profile[case["profile_id"]]) for case in all_cases]
    s_core_candidates = [g for g in gold if g["track"] == "S" and g["boundary_classification"] == "CORE_NONBOUNDARY"]
    by_action: dict[str, list[dict[str, Any]]] = {a: [] for a in ("ACT", "ASK", "WAIT", "FALLBACK")}
    for row in sorted(s_core_candidates, key=lambda x: x["case_id"]):
        by_action[row["gold_action_type"]].append(row)
    shortages = {a: 32 - len(rows) for a, rows in by_action.items() if len(rows) < 32}
    if shortages:
        raise RuntimeError("STRUCTURED_POOL_EXPANSION_REQUIRED:" + json.dumps(shortages, sort_keys=True))
    core_ids = {row["case_id"] for action in by_action for row in by_action[action][:32]}
    for row in gold:
        row["primary_core_member"] = row["case_id"] in core_ids
    gold = sorted(gold, key=lambda x: x["case_id"])
    runtime = {"schema_version": "driveclarify.m2b_blind_runtime.v1", "design_id": design_id,
               "design_version": DESIGN_VERSION, "case_count": len(all_cases), "cases": all_cases,
               "authorization_eligible": False, "used_for_control": False, "control_authorized": False}
    gold_package = {"schema_version": "driveclarify.m2b_sealed_evaluation_gold.v1", "design_id": design_id,
                    "reference_solver_version": REFERENCE_SOLVER_VERSION, "record_count": len(gold), "records": gold}
    runtime_bytes, gold_bytes = canonical_bytes(runtime), canonical_bytes(gold_package)
    runtime_path, gold_path = output / "M2B_BLIND_RUNTIME_INPUT_PACKAGE.json", output / "M2B_SEALED_EVALUATION_GOLD.json"
    atomic_replace(runtime_path, runtime_bytes); atomic_replace(gold_path, gold_bytes)
    runtime_manifest = {"filename": runtime_path.name, "sha256": file_sha256(runtime_path), "bytes": runtime_path.stat().st_size,
                        "case_count": len(all_cases), "canonical_order": "case_id_lexicographic", "gold_field_count": 0}
    gold_manifest = {"filename": gold_path.name, "sha256": file_sha256(gold_path), "bytes": gold_path.stat().st_size,
                     "record_count": len(gold), "canonical_order": "case_id_lexicographic", "sealed": True}
    atomic_replace(output / "M2B_BLIND_RUNTIME_INPUT_MANIFEST.json", canonical_bytes(runtime_manifest))
    atomic_replace(output / "M2B_SEALED_GOLD_MANIFEST.json", canonical_bytes(gold_manifest))
    atomic_replace(output / "M2B_BLIND_RUNTIME_INPUT_SHA256.txt", (runtime_manifest["sha256"] + "\n").encode())
    atomic_replace(output / "M2B_SEALED_GOLD_SHA256.txt", (gold_manifest["sha256"] + "\n").encode())
    atomic_replace(output / "M2B_BLIND_GOLD_COMMITMENT.json", canonical_bytes({
        "design_id": design_id, "commitment_scheme": "SHA256_CANONICAL_BYTES", "sealed_gold_sha256": gold_manifest["sha256"],
        "sealed_gold_bytes": gold_manifest["bytes"], "record_count": len(gold),
        "unseal_condition": "AFTER_FIRST_RAW_PREDICTION_BYTES_ATOMIC_PUBLICATION", "current_unsealed_count": 0,
    }))
    atomic_replace(output / "M2B_BLIND_MATRIX_ARCHETYPES.json", canonical_bytes({
        "schema_version": "driveclarify.m2b_blind_matrix_archetypes.v1", "archetype_count": len(archetypes),
        "structured_pre_policy_expansion": {"initial_archetype_count": 64, "initial_ask_core_candidates": 27,
            "reason": "INITIAL_EXACT_REFERENCE_SOLVER_POOL_BELOW_32_ASK",
            "rule": "ADD_ONE_2.1X_CONSEQUENCE_VARIANT_FOR_EACH_OF_ALL_16_STRUCTURES",
            "tested_policy_execution_count_before_expansion": 0},
        "explicit_candidate_swap_reseal": {"pair_count": 8, "archetype_count": 16,
            "rule": "ADD_ORIGINAL_AND_SWAPPED_ORIENTATIONS_AT_IDENTICAL_1.55X_COST_SCALE_FOR_FIRST_8_STRUCTURES",
            "reason": "REQUIRE_PROFILE_BY_PROFILE_SEMANTIC_SWAP_VERIFICATION",
            "blind_policy_execution_count_before_reseal": 0},
        "archetypes": archetypes,
    }))
    atomic_replace(output / "M2B_BLIND_PROFILES.json", canonical_bytes({
        "schema_version": "driveclarify.m2b_blind_profiles.v1", "frozen_before_policy_execution": True,
        "development_profile_exact_numeric_duplicate_count": 0, "track_r_profiles": r_profiles, "track_s_profiles": s_profiles,
    }))
    summary = {
        "design_id": design_id, "track_r_case_count": len(r_cases), "track_s_pool_count": len(s_cases),
        "track_s_primary_core_count": len(core_ids), "archetype_count": len(archetypes),
        "track_r_profile_count": len(r_profiles), "track_s_profile_count": len(s_profiles),
        "core_distribution": dict(Counter(g["gold_action_type"] for g in gold if g["primary_core_member"])),
        "natural_distribution": dict(Counter(g["gold_action_type"] for g in gold if g["track"] == "S")),
        "boundary_count": sum(g["boundary_classification"] == "BOUNDARY_STRESS" for g in gold),
        "runtime_sha256": runtime_manifest["sha256"], "gold_sha256": gold_manifest["sha256"],
    }
    atomic_replace(output / "DESIGN_GENERATION_SUMMARY.json", canonical_bytes(summary))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--design-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(generate(args.design_id, args.output), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
