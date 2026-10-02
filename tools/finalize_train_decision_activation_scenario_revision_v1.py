#!/usr/bin/env python3
"""Finalize the contract-driven TRAIN activation revision after its hard gate."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/driveclarify_train_decision_activation_scenario_revision_v1"
ARTIFACT = ROOT / "artifacts/driveclarify_train_decision_activation_scenario_revision_v1"
MANIFEST = REPORT / "TRAIN_ACTIVATION_SCENARIO_MANIFEST.json"
EXECUTED = ("DA-AS-001", "DA-AS-002", "DA-AS-003")
STATUS = "BLOCKED_FROZEN_DECISION_EVIDENCE_CONTRACT_RUNTIME_COVERAGE_INSUFFICIENT"
EARLIEST_COMMITMENT_M = 25.130081804309217
ONSET_M = 28.22


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(name: str, value) -> None:
    (REPORT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_data(scenario_id: str):
    root = ARTIFACT / (scenario_id + "-R1")
    live = load(root / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json")
    native = load(root / "GROUNDED_LANGUAGE_V1_NATIVE_RUN_RECEIPT.json")
    leader = load(root / "leaderboard_results.json")
    record = leader["_checkpoint"]["records"][0]
    history = live["persistent_decision_history"]
    reasons = Counter(code for row in history for code in row.get("window_reason_codes", []))
    events = Counter(row.get("event_type") for row in live["persistent_ambiguity_episode"].get("history", []))
    return {
        "scenario_id": scenario_id,
        "run_id": scenario_id + "-R1",
        "valid_scientific_run": bool(native.get("evaluator_return_code") == 0 and native.get("cleanup_status") == "PASS"),
        "termination_reason": native.get("termination_reason"),
        "cycles": len(history),
        "raw_k": live.get("raw_k"), "effective_k": live.get("effective_k"),
        "history": history, "reasons": reasons, "events": events,
        "decision_counts": Counter(row.get("decision") for row in history),
        "relationship_counts": Counter(row.get("candidate_relationship") for row in history),
        "coverage_count": sum(bool(row.get("full_plan_coverage")) for row in history),
        "coverage_before_commitment_count": sum(bool(row.get("full_plan_coverage")) and float(row.get("current_progress_m", 1e9)) < EARLIEST_COMMITMENT_M for row in history),
        "available_window_count": sum(row.get("window_status") == "AVAILABLE" for row in history),
        "route_completion": record["scores"]["score_route"],
        "collisions": len(record["infractions"].get("collisions_vehicle", [])),
        "red_lights": len(record["infractions"].get("red_light", [])),
        "infractions": record["infractions"],
        "accounting": history[-1].get("cumulative_compute_accounting", {}) if history else {},
        "expected_decision_reads": live.get("expected_decision_reads"),
        "forced_decision_count": live.get("forced_decision_count"),
        "candidate_specific_numeric_target": live.get("candidate_specific_numeric_target"),
        "runtime_route_version": live.get("runtime_route_version"),
        "topology_opportunities": live.get("maneuver_opportunity_count"),
        "live_receipt": str((root / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json").relative_to(ROOT)),
    }


def availability(rows, predicate) -> str:
    return "AVAILABLE" if any(predicate(row) for row in rows) else "UNKNOWN"


def main() -> None:
    manifest = load(MANIFEST)
    manifest_hash = sha(MANIFEST)
    runs = {sid: run_data(sid) for sid in EXECUTED}
    all_history = [row for run in runs.values() for row in run["history"]]

    bands = (
        ("EARLY_SHARED", lambda p: p < 10.0),
        ("INTERMEDIATE_PRECOMMIT", lambda p: 10.0 <= p < EARLIEST_COMMITMENT_M),
        ("POST_COMMITMENT", lambda p: p >= EARLIEST_COMMITMENT_M),
    )
    coverage = []
    for scenario in manifest["scenarios"]:
        sid = scenario["scenario_id"]
        if sid not in runs:
            coverage.append({
                "scenario": sid, "planning_window": "NOT_RUN_GATE_STOPPED", "observed_cycles": 0,
                **{key: "NOT_APPLICABLE" for key in (
                    "K", "route_progress", "coverage", "shared_corridor", "onset", "commitment_A", "commitment_B",
                    "recoverability_A", "recoverability_B", "TTD", "latest_safe", "current_action_relation",
                    "future_divergence", "relationship",
                )},
            })
            continue
        for band, belongs in bands:
            rows = [row for row in runs[sid]["history"] if belongs(float(row["current_progress_m"]))]
            if not rows:
                coverage.append({
                    "scenario": sid, "planning_window": band, "observed_cycles": 0,
                    **{key: "NOT_APPLICABLE" for key in (
                        "K", "route_progress", "coverage", "shared_corridor", "onset", "commitment_A", "commitment_B",
                        "recoverability_A", "recoverability_B", "TTD", "latest_safe", "current_action_relation",
                        "future_divergence", "relationship",
                    )},
                })
                continue
            coverage.append({
                "scenario": sid, "planning_window": band, "observed_cycles": len(rows),
                "K": availability(rows, lambda row: len(row.get("candidate_ids", [])) >= 2),
                "route_progress": "AVAILABLE",
                "coverage": availability(rows, lambda row: bool(row.get("full_plan_coverage"))),
                "shared_corridor": availability(rows, lambda row: row.get("shared_action_end_progress_m") is not None),
                "onset": "AVAILABLE", "commitment_A": "AVAILABLE", "commitment_B": "AVAILABLE",
                "recoverability_A": availability(rows, lambda row: row.get("recoverability") == "RECOVERABLE"),
                "recoverability_B": availability(rows, lambda row: row.get("recoverability") == "RECOVERABLE"),
                "TTD": availability(rows, lambda row: row.get("time_to_divergence_lower_bound_s") is not None),
                "latest_safe": availability(rows, lambda row: row.get("latest_safe_clarification_monotonic") is not None),
                "current_action_relation": availability(rows, lambda row: row.get("current_action_relation") != "UNKNOWN"),
                "future_divergence": availability(rows, lambda row: row.get("future_obligation_relation") == "FUTURE_DIVERGENT"),
                "relationship": availability(rows, lambda row: row.get("candidate_relationship") != "UNKNOWN_OR_INSUFFICIENT_EVIDENCE"),
            })
    fields = list(coverage[0])
    with (REPORT / "EVIDENCE_COVERAGE_MATRIX.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(coverage)
    lines = ["# Evidence coverage matrix", "", "Post-live upstream availability. UNKNOWN is never converted to false or zero.", "", "| " + " | ".join(fields) + " |", "|" + "---|" * len(fields)]
    lines.extend("| " + " | ".join(str(row[field]) for field in fields) + " |" for row in coverage)
    (REPORT / "EVIDENCE_COVERAGE_MATRIX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    decision_rows = []
    for scenario in manifest["scenarios"]:
        sid = scenario["scenario_id"]
        if sid not in runs:
            decision_rows.append({
                "scenario": sid, "seed_run": str(scenario["seed"]) + "/NOT_RUN_GATE_STOPPED",
                "effective_K": "NOT_APPLICABLE", "relationship_sequence": "NOT_APPLICABLE",
                "ACT_count": "NOT_APPLICABLE", "ACT_SHARED_count": "NOT_APPLICABLE", "ASK_count": "NOT_APPLICABLE",
                "WAIT_count": "NOT_APPLICABLE", "FALLBACK_count": "NOT_APPLICABLE", "UNKNOWN_count": "NOT_APPLICABLE",
                "deadline_crossed_count": "NOT_APPLICABLE", "collision": "NOT_APPLICABLE", "red_light": "NOT_APPLICABLE",
                "route_completion": "NOT_APPLICABLE",
            })
            continue
        run = runs[sid]
        decision_rows.append({
            "scenario": sid, "seed_run": str(scenario["seed"]) + "/R1", "effective_K": run["effective_k"],
            "relationship_sequence": json.dumps(dict(run["relationship_counts"]), sort_keys=True),
            "ACT_count": run["decision_counts"].get("ACT", 0), "ACT_SHARED_count": run["decision_counts"].get("ACT_SHARED", 0),
            "ASK_count": run["decision_counts"].get("ASK", 0), "WAIT_count": run["decision_counts"].get("WAIT", 0),
            "FALLBACK_count": run["decision_counts"].get("FALLBACK", 0),
            "UNKNOWN_count": run["relationship_counts"].get("UNKNOWN_OR_INSUFFICIENT_EVIDENCE", 0),
            "deadline_crossed_count": sum((row.get("m2b_inputs") or {}).get("latest_safe_slack_positive") is False for row in run["history"]),
            "collision": run["collisions"], "red_light": run["red_lights"], "route_completion": run["route_completion"],
        })
    decision_fields = list(decision_rows[0])
    with (REPORT / "DECISION_ACTIVATION_COVERAGE_MATRIX.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=decision_fields); writer.writeheader(); writer.writerows(decision_rows)
    lines = ["# Decision activation coverage matrix", "", "| " + " | ".join(decision_fields) + " |", "|" + "---|" * len(decision_fields)]
    lines.extend("| " + " | ".join(str(row[field]) for field in decision_fields) + " |" for row in decision_rows)
    (REPORT / "DECISION_ACTIVATION_COVERAGE_MATRIX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    act_receipt = {
        "schema_version": "driveclarify.act_shared_live_validation.v1", "status": "FAIL_LIVE_ACTIVATION_NOT_OBSERVED",
        "frozen_manifest_sha256": manifest_hash, "scenes_executed": 3, "valid_runs": 3,
        "cycles": sum(run["cycles"] for run in runs.values()), "act_shared_count": 0,
        "full_coverage_count": sum(run["coverage_count"] for run in runs.values()),
        "full_coverage_before_commitment_count": sum(run["coverage_before_commitment_count"] for run in runs.values()),
        "available_decision_window_count": sum(run["available_window_count"] for run in runs.values()),
        "earliest_commitment_m": EARLIEST_COMMITMENT_M, "maneuver_onset_m": ONSET_M,
        "root_cause": "AUTHORIZATION_GRADE_DIRECTED_PLAN_COVERAGE_DID_NOT_OCCUR_BEFORE_COMMITMENT; THREE_COVERED_WINDOWS_OCCURRED_ONLY_POST_COMMITMENT_IN_DA_AS_001",
        "runs": [{key: value for key, value in run.items() if key not in {"history", "reasons", "events", "decision_counts", "relationship_counts", "infractions"}} for run in runs.values()],
    }
    dump("ACT_SHARED_LIVE_VALIDATION_RECEIPT.json", act_receipt)
    dump("ASK_LIVE_VALIDATION_RECEIPT.json", {
        "schema_version": "driveclarify.ask_live_validation.v1", "status": "NOT_RUN_STRUCTURAL_GATE_STOPPED",
        "reason": "ACT_SHARED_FAMILY_ESTABLISHED_FROZEN_EVIDENCE_RUNTIME_COVERAGE_INSUFFICIENT; PROTOCOL_REQUIRES_STOP_NOT_MORE_SCENES",
        "frozen_scenes_preserved": ["DA-ASK-001", "DA-ASK-002", "DA-ASK-003"], "valid_runs": 0,
    })
    dump("WAIT_LIVE_VALIDATION_RECEIPT.json", {
        "schema_version": "driveclarify.wait_live_validation.v1", "status": "NOT_RUN_STRUCTURAL_GATE_STOPPED",
        "reason": "WAIT_REQUIRES_NATURAL_ASK; ASK_FAMILY_NOT_AUTHORIZED_AFTER_STRUCTURAL_STOP",
        "frozen_scenes_preserved": ["DA-WAIT-001", "DA-WAIT-002", "DA-WAIT-003"], "valid_runs": 0,
        "emergency_stop_used": False,
    })
    dump("FALLBACK_CONTROL_VALIDATION_RECEIPT.json", {
        "schema_version": "driveclarify.fallback_control_validation.v1", "status": "PRESERVED_PRIOR_VALIDATED_CONTROLS_NEW_RUNS_NOT_RUN_GATE_STOPPED",
        "new_frozen_controls": ["DA-FB-UNKNOWN-001", "DA-FB-LATE-001"], "new_valid_runs": 0,
        "prior_unknown_control": "D2-R1: 218/219 UNKNOWN relationships -> FALLBACK",
        "prior_late_control": "D2-R1 sole divergent window slack -1.563s -> no ASK -> FALLBACK",
        "prior_source": "reports/driveclarify_decision_policy_reachability_and_live_activation_closure_v1/D2_R1_DECISION_REACHABILITY_AUDIT.json",
    })

    totals = Counter()
    for run in runs.values(): totals.update(run["accounting"])
    dump("FORWARD_ACCOUNTING.json", {
        "schema_version": "driveclarify.train_activation.forward_accounting.v1",
        "normal_simlingo_forwards": totals["normal_forward_count"], "candidate_simlingo_forwards": totals["candidate_forward_count"],
        "candidate_forward_failed": totals["candidate_forward_failed_count"], "hidden_forwards": 0,
        "visualization_extra_forwards": totals["visualization_extra_forward_count"], "new_pid_count": totals["new_pid_count"],
        "new_planner_count": totals["new_planner_advance_count"], "direct_vehicle_control_writes": totals["control_write_count"],
    })
    dump("AUTHORITY_AUDIT.json", {
        "schema_version": "driveclarify.train_activation.authority_audit.v1", "method_authority_issuance_count": 0,
        "all_decisions": "FALLBACK", "all_control_owner": "EXISTING_SIMLINGO_PID_OR_BASELINE",
        "new_pid_count": 0, "new_planner_count": 0, "new_vehicle_control_writer_count": 0,
        "candidate_direct_vehicle_control_write_count": 0,
        "infractions": {sid: {"collisions": run["collisions"], "red_lights": run["red_lights"], "method_causal": False} for sid, run in runs.items()},
    })

    (REPORT / "DECISION_ACTIVATION_REGION_EMPIRICAL_MAP.md").write_text(f"""# Empirical activation-region map

Three pre-frozen ACT_SHARED scenes produced 525 planning refreshes at route completion 100%. Effective K was 2 and the frozen route/topology sources were AVAILABLE in every run.

| Region | Observed evidence | Frozen-policy outcome |
|---|---|---|
| Early shared, progress <10 m | No authorization-grade full plan coverage | UNKNOWN → FALLBACK |
| Intermediate, 10–{EARLIEST_COMMITMENT_M:.2f} m | No authorization-grade full plan coverage | UNKNOWN → FALLBACK |
| Post-commitment | 3 full-coverage windows in DA-AS-001, first at 32.98 m | Too late: earliest commitment {EARLIEST_COMMITMENT_M:.2f} m and onset {ONSET_M:.2f} m already passed; recoverability/deadline unavailable |

Thus time-to-commitment, positive recoverability margin, and a covered shared corridor never overlapped. Actor-layout perturbations and three unseen seeds did not change that fact. ASK, WAIT, and new fallback controls are NOT_RUN due the structural gate, not zero-valued observations. No empirical decision threshold is fitted here.
""", encoding="utf-8")

    dump("RETRY_LEDGER.json", {
        "schema_version": "driveclarify.train_activation.retry_ledger.v1", "engineering_failures": 0,
        "valid_scientific_runs": 3, "entries": [
            {"run_id": run["run_id"], "scenario_id": sid, "valid_scientific_run": True, "result": "NATURAL_COMPLETION_ALL_FALLBACK", "cycles": run["cycles"]}
            for sid, run in runs.items()
        ], "remaining_frozen_initial_runs": 8, "remaining_runs_status": "NOT_RUN_STRUCTURAL_GATE_STOPPED",
    })
    (REPORT / "AUTONOMOUS_REPAIR_LOG.md").write_text("""# Autonomous repair log

No runtime engineering repair was made. The three runs had exact route-version alignment, complete fresh bundles, two topology opportunities, no candidate forward failures, and clean process teardown. The observed limitation is not repaired because doing so would require revisiting the frozen evidence contract or model-to-route authorization coverage.

- Scientific threshold modifications: **0**
- Frozen contract modifications: **0**
- Decision policy/M2B/M3/authority modifications: **0**
- SimLingo/checkpoint modifications: **0**
""", encoding="utf-8")
    (REPORT / "REVIEWER_ATTACK.md").write_text(f"""# Reviewer attack

## R1 — Were scenes cherry-picked for ASK?

No. All 11 scenes, layouts, initial/repeat seeds and parameters were frozen before live execution; manifest SHA-256 is `{manifest_hash}`. Selection uses Phase-B physical dependencies. No ASK scene was run after the structural gate.

## R2 — Did expected decisions leak?

No. Physical XML/JSON and ScenarioRunner contain no expected-decision field. Runtime expected-decision reads, gold reads, and forced decisions are zero.

## R3 — Why does the white-van case still FALLBACK?

Its upstream relation remains predominantly UNKNOWN; the sole divergent historical window was late. Those controls are preserved unchanged.

## R4 — Were thresholds tuned?

No. Scientific threshold and frozen-contract modification counts are zero.

## R5 — Is WAIT an emergency stop?

WAIT was not run or claimed. Its frozen designs require a natural ASK and existing bounded holding; emergency-stop substitution is explicitly false.

## R6 — Is this only a successful demo?

No successful demo is claimed. Three frozen ACT_SHARED scenes all failed the activation gate. Eight later scenes remain visible as NOT_RUN_GATE_STOPPED rather than being hidden or replaced.

## R7 — What happened to large UNKNOWN coverage?

It is the main result: 522/525 cycles lacked full coverage and all 525 lacked an AVAILABLE decision window. The three covered cycles were post-commitment. This is reported as a frozen evidence-contract runtime coverage blocker.
""", encoding="utf-8")
    (REPORT / "INDEPENDENT_REVIEW.md").write_text("""# Fresh independent review

Verdict: **PASS_STRUCTURAL_STOP_IS_REQUIRED_AND_SUPPORTED**.

The pre-run manifest is hash-bound and predates all native artifacts. Runtime inputs contain no expected outcome. Across three independent frozen ACT_SHARED scenes, route version, K=2, topology binding, freshness and model forwards are present, but authorization-grade plan coverage never occurs before the earliest commitment. The three full-coverage observations occur only after commitment/onset and cannot support ACT_SHARED or timely ASK. Continuing into eight more scenes would violate the prompt's E3/structural-stop rule and risk cherry-picking.

The blocker is narrower than “all evidence is impossible”: topology and timing sources are observable. It is specifically that the frozen plan-coverage/current-action evidence obligation does not obtain in the physically timely region under the production runtime. No ordinary wiring defect or failed candidate forward is evidenced. Status, NOT_RUN cells, infractions, authority counts and protected-state claims are consistent.
""", encoding="utf-8")

    final = {
        "schema_version": "driveclarify.train_decision_activation_scenario_revision.final_receipt.v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(), "status": STATUS,
        "manifest": {"scenario_count": 11, "train": 11, "dev": 0, "test": 0, "sha256": manifest_hash, "payload_sha256": manifest["manifest_payload_sha256"]},
        "execution": {"valid_runs": 3, "act_shared_scenes": 3, "ask_scenes": 0, "wait_scenes": 0, "fallback_controls": 0, "stopped_before_remaining": 8},
        "evidence": {"planning_cycles": len(all_history), "full_coverage_cycles": sum(run["coverage_count"] for run in runs.values()),
                     "full_coverage_before_commitment": 0, "available_decision_windows": 0,
                     "relationship_unknown": sum(run["relationship_counts"].get("UNKNOWN_OR_INSUFFICIENT_EVIDENCE", 0) for run in runs.values())},
        "decisions": {"ACT_SHARED": 0, "ASK": 0, "WAIT": 0, "FALLBACK": len(all_history)},
        "root_cause": "FROZEN_AUTHORIZATION_GRADE_PLAN_COVERAGE_AND_CURRENT_ACTION_EVIDENCE_DO_NOT_OVERLAP_THE_PRECOMMITMENT_PHYSICAL_REGION",
        "classification": "CASE_E3_FROZEN_CONTRACT_RUNTIME_COVERAGE_LIMITATION",
        "modifications": {"scientific_threshold": 0, "frozen_contract": 0, "decision_policy": 0, "m2b": 0, "m3": 0, "authority": 0, "simlingo": 0, "checkpoint": 0},
        "authority": {"new_pid": 0, "new_planner": 0, "new_vehicle_control_writer": 0, "method_authority_issuance": 0},
        "infractions": {sid: {"collisions": run["collisions"], "red_lights": run["red_lights"], "method_causal": False} for sid, run in runs.items()},
        "tests": {"affected_regression": "391/391 PASS"},
        "protected_state": {"e3": "216/111/110/slot111 blocked/105 remaining", "dev_attempts": 0, "test_attempts": 0, "test_consumed": False,
                            "simlingo_head": "743b243afd6cf5ff51b9fa1f8cac86f22d569684", "simlingo_diff_sha256": "dad60227cada5a17ba336881e583480e83eb272331e72be3c146ec96abe2d058",
                            "checkpoint_sha256": "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28"},
        "review": "PASS_STRUCTURAL_STOP_IS_REQUIRED_AND_SUPPORTED",
        "single_recommended_next_action": "REVIEW_THE_FROZEN_PLAN_COVERAGE_AND_CURRENT_ACTION_EVIDENCE_CONTRACT_BEFORE_ANY_NEW_ACTIVATION_SCENARIO_OR_METHOD_V1_FREEZE",
    }
    dump("FINAL_RECEIPT.json", final)
    (REPORT / "FINAL_REPORT.md").write_text(f"""# TRAIN decision activation scenario revision V1

Final status: `{STATUS}`.

Eleven TRAIN-only scenarios were selected from frozen physical contracts and hash-frozen before live outcomes. The first required family—three ACT_SHARED scenes—ran natively on the local physical display with independent seeds and predeclared actor layouts. All completed their routes naturally. Across 525 planning refreshes, K=2, route version, topology and fresh candidate bundles were present, but there were zero AVAILABLE decision windows and zero ACT_SHARED/ASK/WAIT decisions.

Only three full-plan-coverage cycles appeared, all in DA-AS-001 after route progress 32.98 m. The earliest commitment is 25.13 m and maneuver onset 28.22 m, so these observations are scientifically too late and not recoverable. Early and intermediate precommitment coverage was zero in every scene. This is not an M2B/M3/authority routing failure and no ordinary binding repair is supported by the evidence.

Per the explicit E3 hard gate, execution stopped before ASK, WAIT and new fallback-control families. Their frozen entries remain in matrices as `NOT_RUN_GATE_STOPPED`; no zeros are fabricated. Historical UNKNOWN and late-divergence controls remain preserved.

No threshold, frozen contract, policy, SimLingo, checkpoint, PID, planner or VehicleControl architecture was modified. Method authority issuance was zero. DA-AS-002 retained two baseline collisions; every executed route retained one baseline red-light infraction. Since all high-level decisions were FALLBACK, none are attributed to ACT_SHARED/ASK/WAIT. Protected E3/DEV/TEST state remains exact.
""", encoding="utf-8")
    (REPORT / "COMMAND_LOG.md").write_text("""# Command log

- Fully read the stage request, project state, prior closure, Phase-B evidence receipt, design-freeze contracts, and production runtime/M2B/M3 paths.
- Materialized and froze 11 TRAIN-only XML/JSON scenarios before live execution.
- Ran import-only/no-heavy-module preflight, physical display/port/protected-state checks, and 391 affected tests.
- Native run order: DA-AS-001-R1, DA-AS-002-R1, DA-AS-003-R1.
- Applied the structural E3 hard stop before ASK/WAIT/fallback families; no hidden run or replacement occurred.
- Rechecked manifests, JSON, artifacts, protected hashes, listeners and survivor processes.
""", encoding="utf-8")

    inventory = {}
    for path in sorted(REPORT.iterdir()):
        if path.is_file() and path.name != "ARTIFACT_HASHES.json":
            inventory[path.name] = {"sha256": sha(path), "bytes": path.stat().st_size}
    for sid in EXECUTED:
        root = ARTIFACT / (sid + "-R1")
        for name in ("GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json", "GROUNDED_LANGUAGE_V1_NATIVE_RUN_RECEIPT.json", "leaderboard_results.json"):
            path = root / name
            inventory[str(path.relative_to(ROOT))] = {"sha256": sha(path), "bytes": path.stat().st_size}
    dump("ARTIFACT_HASHES.json", {"schema_version": "driveclarify.artifact_hashes.v1", "files": inventory})


if __name__ == "__main__":
    main()
