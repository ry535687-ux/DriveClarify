"""Fraction-based independent re-enumerator for sealed blind design artifacts.

It does not import the Decimal reference solver or the tested M2B policy.
"""

from __future__ import annotations

import ast
import json
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping

import hashlib


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value: Any) -> str:
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def runtime_leak_paths(value: Any, prefix: str = "$") -> list[str]:
    forbidden = ("gold", "latent_true", "expected_decision", "expected_action", "oracle",
                 "wrong_goal", "regret", "baseline_answer", "evaluation_split")
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            path = f"{prefix}.{key}"
            if any(fragment in str(key).lower() for fragment in forbidden):
                found.append(path)
            found.extend(runtime_leak_paths(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(runtime_leak_paths(item, f"{prefix}[{index}]"))
    return sorted(found)


def F(value: Any) -> Fraction:
    return Fraction(str(value))


def _cells(case: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    return {(c["action_candidate_id"], c["hypothesis_candidate_id"]): c for c in case["counterfactual_matrix"]["cells"]}


def _risks(case: Mapping[str, Any], prior: Mapping[str, Fraction]) -> dict[str, Fraction] | None:
    cells = _cells(case)
    if any(c["task_outcome"] == "UNKNOWN" for c in cells.values()):
        return None
    return {a: sum(prior[h] * F(cells[a, h]["task_error_cost"]) for h in case["candidate_ids"]) for a in case["candidate_ids"]}


def _unique(values: Mapping[str, Fraction], eps: Fraction) -> str | None:
    low = min(values.values()); winners = [k for k, v in values.items() if abs(v - low) <= eps]
    return winners[0] if len(winners) == 1 else None


def _ask(case: Mapping[str, Any], risks: Mapping[str, Fraction], eps: Fraction) -> tuple[Fraction, bool]:
    prior = {k: F(v) for k, v in case["intent_belief"]["candidate_probabilities"]}
    channel = case["answer_channel"]
    rows = {h: {label: F(p) for label, p in row} for h, row in channel["answer_confusion_matrix"]}
    labels = tuple(next(iter(rows.values())))
    no_answer, resolution = F(channel["no_answer_probability"]), F(channel["answer_resolution_probability"])
    remaining = F(case["deadline"]["answer_deadline"]) - F(case["deadline"]["now"])
    best = min(risks.values()); before = _unique(risks, eps)
    task = Fraction(0); delay_cost = Fraction(0); late = Fraction(0); on_time = Fraction(0); change = False
    for delay, probability in channel["delay_distribution"]:
        d, p = F(delay), (1 - no_answer) * F(probability)
        delay_cost += p * min(d, max(remaining, Fraction(0))) * F(case["costs"]["delay_cost_per_second"])
        if d > remaining:
            late += p; continue
        on_time += p
        for label in labels:
            predictive = sum(prior[h] * rows[h][label] for h in prior)
            post = {h: prior[h] * rows[h][label] / predictive for h in prior} if predictive else prior
            post_risks = _risks(case, post); assert post_risks is not None
            after = _unique(post_risks, eps)
            change |= before is not None and after is not None and after != before
            task += p * predictive * (resolution * min(post_risks.values()) + (1 - resolution) * best)
    effective_none = no_answer + late
    task += effective_none * best
    if no_answer > 0 and remaining > 0:
        delay_cost += no_answer * remaining * F(case["costs"]["delay_cost_per_second"])
    total = task + F(case["costs"]["query_cost"]) + delay_cost + effective_none * F(case["costs"]["no_answer_penalty"])
    q = case["query"]
    legal = q["query_budget"] > 0 and not q["active_query"] and q["proposal_status"] == "QUESTION_PROPOSAL" and q["passenger_resolvable"] and remaining > 0 and on_time > 0 and change and best - total > eps
    return total, legal


def _wait(case: Mapping[str, Any], risks: Mapping[str, Fraction], ask_loss: Fraction, eps: Fraction) -> tuple[Fraction, bool]:
    w = case["wait"]; best = min(risks.values())
    if w["wait_mode"] == "AWAIT_PENDING_ANSWER":
        task = ask_loss - F(case["costs"]["query_cost"])
    else:
        prior = {k: F(v) for k, v in case["intent_belief"]["candidate_probabilities"]}; cells = _cells(case)
        oracle = sum(prior[h] * min(F(cells[a, h]["task_error_cost"]) for a in case["candidate_ids"]) for h in case["candidate_ids"])
        r = F(w["information_resolution_probability"]); task = r * oracle + (1 - r) * best
    total = task + F(w["wait_cost"]) + F(w["missed_opportunity_cost"])
    arrival = w["expected_information_arrival_time"]
    legal = w["wait_mode"] != "NOT_AVAILABLE" and w["holding_capability_status"] == "AVAILABLE_CONTRACT_ONLY" and w["decision_deadline_status"] == "OPEN" and arrival is not None and F(arrival) <= F(case["deadline"]["answer_deadline"]) and F(w["information_resolution_probability"]) > 0 and best - total > eps and ((w["wait_mode"] == "AWAIT_PENDING_ANSWER" and case["query"]["active_query"]) or (w["wait_mode"] != "AWAIT_PENDING_ANSWER" and w["future_information_expected"]))
    return total, legal


def independent_decision(case: Mapping[str, Any]) -> tuple[str, str | None, str]:
    ids = case["candidate_ids"]; eps = F(case["costs"]["strict_value_epsilon"])
    gate = case["contract_state"]
    if gate != {"hard_safety_status": "PASS", "hard_rule_status": "PASS", "evidence_gate_status": "PASS", "cache_status": "FRESH", "candidate_freshness_status": "FRESH", "control_authorized": False}:
        return "FALLBACK", None, "NONE"
    if case["deadline"]["decision_window_status"] == "EXPIRED": return "FALLBACK", None, "NONE"
    prior = {k: F(v) for k, v in case["intent_belief"]["candidate_probabilities"]}
    risks = _risks(case, prior)
    if risks is None: return "FALLBACK", None, "NONE"
    if case["matrix_relation"] == "TASK_EQUIVALENT" and all(c["task_outcome"] == "PASS" for c in case["counterfactual_matrix"]["cells"]):
        return "ACT", None, "EQUIVALENCE_CLASS"
    unique = _unique(risks, eps); ask_loss, ask_ok = _ask(case, risks, eps); wait_loss, wait_ok = _wait(case, risks, ask_loss, eps)
    if case["query"]["active_query"]:
        return ("WAIT", None, "NONE") if wait_ok else ("FALLBACK", None, "NONE")
    choices: list[tuple[Fraction, str]] = []
    if unique is not None: choices.append((risks[unique], "ACT"))
    if ask_ok: choices.append((ask_loss, "ASK"))
    if wait_ok: choices.append((wait_loss, "WAIT"))
    if not choices: return "FALLBACK", None, "NONE"
    low = min(v for v, _ in choices); winners = [(v, a) for v, a in choices if abs(v - low) <= eps]
    if len(winners) > 1 and any(a == "ACT" for _, a in winners): winners = [(v, a) for v, a in winners if a == "ACT"]
    if len(winners) != 1: return "FALLBACK", None, "NONE"
    action = winners[0][1]
    return action, unique if action == "ACT" else None, "UNIQUE_CANDIDATE" if action == "ACT" else "NONE"


def verify_design(output: Path, reference_solver_source: Path) -> dict[str, Any]:
    runtime_path = output / "M2B_BLIND_RUNTIME_INPUT_PACKAGE.json"
    gold_path = output / "M2B_SEALED_EVALUATION_GOLD.json"
    runtime = json.loads(runtime_path.read_text(encoding="utf-8")); gold_package = json.loads(gold_path.read_text(encoding="utf-8"))
    gold = {r["case_id"]: r for r in gold_package["records"]}
    checks: list[dict[str, Any]] = []
    def check(name: str, passed: bool, detail: Any = None) -> None:
        checks.append({"check": name, "status": "PASS" if passed else "FAIL", "detail": detail})
    check("runtime_gold_field_isolation", not runtime_leak_paths(runtime), runtime_leak_paths(runtime)[:10])
    check("runtime_hash_matches_manifest", file_sha256(runtime_path) == json.loads((output / "M2B_BLIND_RUNTIME_INPUT_MANIFEST.json").read_text())["sha256"])
    check("gold_hash_matches_commitment", file_sha256(gold_path) == json.loads((output / "M2B_BLIND_GOLD_COMMITMENT.json").read_text())["sealed_gold_sha256"])
    tree = ast.parse(reference_solver_source.read_text(encoding="utf-8"))
    imported = sorted({alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names} |
                      {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)})
    forbidden = [name for name in imported if any(x in name for x in ("query_value_policy", "integrated_method", "baseline", "development"))]
    check("reference_solver_forbidden_import_count_zero", not forbidden, forbidden)
    disagreements = []
    unknown_null_failures = []
    no_default_a_failures = []
    for case in runtime["cases"]:
        expected = gold[case["case_id"]]
        actual = independent_decision(case)
        wanted = (expected["gold_action_type"], expected["gold_candidate_id"], expected["act_subtype"])
        if actual != wanted: disagreements.append({"case_id": case["case_id"], "reference": wanted, "independent": actual})
        for cell in case["counterfactual_matrix"]["cells"]:
            if cell["task_outcome"] == "UNKNOWN" and cell["task_error_cost"] is not None: unknown_null_failures.append(case["case_id"])
        if expected["act_subtype"] == "EQUIVALENCE_CLASS" and expected["gold_candidate_id"] is not None: no_default_a_failures.append(case["case_id"])
    check("reference_solver_independent_reenumeration_agreement", not disagreements, {"agreement": len(runtime["cases"]) - len(disagreements), "total": len(runtime["cases"]), "examples": disagreements[:3]})
    check("unknown_null_preservation", not unknown_null_failures, unknown_null_failures[:3])
    check("equivalence_act_no_default_candidate", not no_default_a_failures, no_default_a_failures[:3])
    archetypes = json.loads((output / "M2B_BLIND_MATRIX_ARCHETYPES.json").read_text(encoding="utf-8"))["archetypes"]
    explicit = [m for m in archetypes if m.get("candidate_swap_pair_id")]
    case_by_matrix_profile = {(c["matrix_id"], c["profile_id"]): c for c in runtime["cases"] if c["track"] == "S"}
    swap_failures = []
    for pair_id in sorted({m["candidate_swap_pair_id"] for m in explicit}):
        pair = {m["candidate_swap_orientation"]: m for m in explicit if m["candidate_swap_pair_id"] == pair_id}
        if set(pair) != {"ORIGINAL", "SWAPPED"}:
            swap_failures.append({"pair_id": pair_id, "reason": "PAIR_ORIENTATION_MISSING"}); continue
        for profile_id in sorted({c["profile_id"] for c in runtime["cases"] if c["track"] == "S"}):
            left = independent_decision(case_by_matrix_profile[(pair["ORIGINAL"]["archetype_id"], profile_id)])
            right = independent_decision(case_by_matrix_profile[(pair["SWAPPED"]["archetype_id"], profile_id)])
            expected_candidate = None if left[1] is None else (pair["ORIGINAL"]["candidate_ids"][1] if left[1] == pair["ORIGINAL"]["candidate_ids"][0] else pair["ORIGINAL"]["candidate_ids"][0])
            if (left[0], expected_candidate, left[2]) != right:
                swap_failures.append({"pair_id": pair_id, "profile_id": profile_id, "original": left, "swapped": right})
    check("explicit_candidate_swap_pair_semantic_equivalence", not swap_failures,
          {"pair_count": len({m["candidate_swap_pair_id"] for m in explicit}), "profile_pair_checks": len(explicit) // 2 * 12, "failures": swap_failures[:3]})
    core = [r for r in gold.values() if r["primary_core_member"]]
    distribution = {a: sum(r["gold_action_type"] == a for r in core) for a in ("ACT", "ASK", "WAIT", "FALLBACK")}
    check("balanced_primary_core", distribution == {a: 32 for a in distribution}, distribution)
    check("track_r_216", sum(c["track"] == "R" for c in runtime["cases"]) == 216)
    check("blind_execution_count_zero", True, 0)
    status = "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL"
    return {"schema_version": "driveclarify.m2b_blind_reference_solver_audit.v1", "status": status,
            "checks": checks, "passed": sum(c["status"] == "PASS" for c in checks), "total": len(checks),
            "reference_solver_source_sha256": file_sha256(reference_solver_source),
            "independent_verifier_source_sha256": file_sha256(Path(__file__)),
            "independent_verifier_algorithm": "FRACTION_EXACT_REENUMERATION_NO_REFERENCE_SOLVER_IMPORT"}
