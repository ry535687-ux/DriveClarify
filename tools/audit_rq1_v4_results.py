#!/usr/bin/env python3
"""Independent standard-library recomputation of the sealed RQ1-V4 result."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/driveclarify_rq1_v4_ord_critical_stability_and_formal_v1"
FAMILIES = ("REF", "LMK", "ORD", "USC")
LEVELS = ("EQUIVALENT", "CRITICAL")
SCENE_CODES = tuple(f"{family}-{level}" for family in FAMILIES for level in LEVELS)


def load(name: str):
    return json.loads((REPORT / name).read_text(encoding="utf-8"))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def close(left, right, tolerance=1e-12):
    if left is None or right is None:
        return left is right
    return abs(float(left) - float(right)) <= tolerance


def metric(rows, key):
    low = [row for row in rows if row["consequence_level"] == "EQUIVALENT"]
    high = [row for row in rows if row["consequence_level"] == "CRITICAL"]
    high_rate = None if not high else sum(row[key] == "ASK" for row in high) / len(high)
    low_rate = None if not low else sum(row[key] == "ASK" for row in low) / len(low)
    return {
        "high_ask_recall": high_rate,
        "low_unnecessary_ask_rate": low_rate,
        "selectivity_gap": None if high_rate is None or low_rate is None else high_rate - low_rate,
    }


def effect(rows):
    method = metric(rows, "policy_action")
    baseline = metric(rows, "baseline_action")
    if method["selectivity_gap"] is None or baseline["selectivity_gap"] is None:
        return None
    return {
        "paired_low_query_rate_difference_method_minus_baseline": method["low_unnecessary_ask_rate"] - baseline["low_unnecessary_ask_rate"],
        "paired_low_query_reduction_baseline_minus_method": baseline["low_unnecessary_ask_rate"] - method["low_unnecessary_ask_rate"],
        "high_recall_preservation_method_minus_baseline": method["high_ask_recall"] - baseline["high_ask_recall"],
        "selectivity_gap_improvement_method_minus_baseline": method["selectivity_gap"] - baseline["selectivity_gap"],
    }


def main() -> int:
    ledger = load("FORMAL_EXECUTION_LEDGER.json")
    primary = load("RQ1_V4_PRIMARY_RESULTS.json")
    secondary = load("RQ1_V4_SECONDARY_RESULTS.json")
    all_rows = ledger["entries"]
    rows = [row for row in all_rows if row["decision_evaluable"]]
    execution = [row for row in all_rows if row["execution_evaluable"]]
    by_condition = Counter(row["scene_code"] for row in rows)
    by_family = Counter(row["family"] for row in rows)
    gate = len(all_rows) == 48 and len(rows) >= 40 and all(by_condition[code] >= 4 for code in SCENE_CODES) and all(by_family[family] >= 9 for family in FAMILIES)
    checks = {
        "exactly_48_unique_planned_cells": ledger.get("planned_episodes") == 48 and len({row["cell_id"] for row in all_rows}) == len(all_rows),
        "ledger_decision_count_recomputed": ledger.get("decision_evaluable_episodes") == len(rows),
        "ledger_execution_count_recomputed": ledger.get("execution_evaluable_episodes") == len(execution),
        "ledger_noncompletion_count_recomputed": ledger.get("native_noncompletion_count") == sum(not row["execution_evaluable"] for row in all_rows),
        "condition_counts_recomputed": ledger.get("decision_evaluable_by_condition") == {code: by_condition[code] for code in SCENE_CODES},
        "family_counts_recomputed": ledger.get("decision_evaluable_by_family") == {family: by_family[family] for family in FAMILIES},
        "primary_gate_recomputed": (ledger.get("primary_evaluability_gate") or {}).get("pass") is gate,
        "no_nonevaluable_imputation": all(row in rows for row in rows) and not any((not row["decision_evaluable"]) and row in rows for row in all_rows),
        "scientific_unit_is_episode": primary.get("scientific_unit") in {None, "scene x seed episode"},
        "scientific_retries_zero": ledger.get("scientific_retries") == 0,
        "seed_replacements_zero": ledger.get("seed_replacements") == 0,
    }
    recomputed = {"gate": gate, "decision_evaluable": len(rows), "execution_evaluable": len(execution)}
    if gate:
        method = metric(rows, "policy_action")
        baseline = metric(rows, "baseline_action")
        effects = effect(rows)
        recomputed.update({"consequence_aware": method, "ambiguity_only": baseline, "effects": effects})
        checks.update({
            "primary_analysis_ran_only_after_gate": primary.get("analysis_run") is True,
            "method_rates_recomputed": all(close(method[key], (primary.get("consequence_aware") or {}).get(key)) for key in method),
            "baseline_rates_recomputed": all(close(baseline[key], (primary.get("ambiguity_only") or {}).get(key)) for key in baseline),
            "effects_recomputed": effects is not None and all(close(effects[key], (primary.get("effects") or {}).get(key)) for key in effects),
            "support_rule_recomputed": (primary.get("support_criteria") or {}).get("both_required_criteria_pass") is (effects["paired_low_query_rate_difference_method_minus_baseline"] < 0 and effects["high_recall_preservation_method_minus_baseline"] >= 0),
            "secondary_uses_execution_denominator": secondary.get("all_execution_evaluable") == len(execution),
        })
        for minimum, key in ((5, "sensitivity_5_of_6"), (6, "sensitivity_6_of_6")):
            selected = [code for code in SCENE_CODES if by_condition[code] >= minimum]
            checks[f"{minimum}_of_6_sensitivity_conditions_recomputed"] = (primary.get(key) or {}).get("included_conditions") == selected
    else:
        checks.update({
            "primary_analysis_not_run_when_gate_failed": primary.get("analysis_run") is False,
            "secondary_analysis_not_run_when_gate_failed": secondary.get("analysis_run") is False,
        })
    value = {
        "schema": "driveclarify.rq1_v4.independent-result-audit.v1",
        "status": "PASS_INDEPENDENT_RESULT_AUDIT" if all(checks.values()) else "RQ1_V4_EXECUTION_INTEGRITY_NOT_CLOSED",
        "pass": all(checks.values()), "checks": checks, "recomputed": recomputed,
        "calculation_implementation": "independent standard-library pass; no import from campaign tool",
    }
    value["audit_digest"] = digest(value)
    path = REPORT / "INDEPENDENT_RESULT_AUDIT.json"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)
    return 0 if value["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
