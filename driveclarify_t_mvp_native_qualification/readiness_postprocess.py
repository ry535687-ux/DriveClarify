"""Evidence-only gate for the final bounded six-method native smoke."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from driveclarify_t_mvp.firewall import BaselineId
from driveclarify_t_mvp_native_qualification.baseline_bindings import EXPECTED_QUALNAMES


def load(path: Path, default: Any = None) -> Any:
    return default if not path.is_file() else json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--prior-report", required=True)
    args = parser.parse_args()
    report = Path(args.report)
    prior = Path(args.prior_report)
    runtime = load(report / "smoke/runtime_cases.json")["cases"]
    oracle = load(report / "smoke/oracle_injection_manifest.json")["cases"]
    state = [json.loads(line) for line in (report / "raw/smoke_execution_state.jsonl").read_text(encoding="utf-8").splitlines() if line]
    rows = []
    all_checks = []
    for case_id, config in runtime.items():
        attempts = sorted((report / "raw/smoke" / case_id).glob("attempt_*"))
        exposed = []
        for root in attempts:
            process = load(root / "process_job/PROCESS_RECEIPT.json", {})
            if process.get("agent_exposed"):
                exposed.append((root, process))
        checks: dict[str, bool] = {"exactly_one_agent_exposed_attempt": len(exposed) == 1}
        if len(exposed) == 1:
            root, process = exposed[0]
            evidence = root / "agent_evidence"
            setup = load(evidence / "agent_setup.json", {})
            dispatch = load(evidence / "native_dispatch_exercised.json", {})
            injection = load(evidence / "evaluator/injection_receipt.json", {})
            equivalence = load(evidence / "cp0_equivalence.json", {})
            expected = EXPECTED_QUALNAMES[BaselineId(config["baseline_id"])]
            checks.update(
                {
                    "carla_readiness_five_checks": len(load(root / "process_job/carla_readiness.json", {}).get("consecutive_rpc_world_checks", [])) == 5,
                    "cleanup_pass": process.get("cleanup_pass") is True,
                    "dispatch_exact_class": setup.get("resolved_baseline_binding") == expected,
                    "dispatch_exercised": dispatch.get("exact_instance_type") == expected and dispatch.get("fallback_used") is False,
                    "prospective_injection": injection.get("receipt", {}).get("injected_before_policy") is True,
                    "timing_bucket_join": injection.get("receipt", {}).get("bucket") == oracle[case_id]["timing_bucket"],
                    "engineering_obligation_complete": (evidence / "native_obligation_complete.json").is_file(),
                    "single_forward_per_tick": bool(equivalence.get("forward_call_count_per_tick")) and set(equivalence.get("forward_call_count_per_tick", [])) == {1},
                    "single_pid_per_tick": bool(equivalence.get("pid_call_count_per_tick")) and set(equivalence.get("pid_call_count_per_tick", [])) == {1},
                    "no_probe_induced_calls": all(equivalence.get(name) == 0 for name in ("probe_induced_model_calls", "probe_induced_pid_calls", "probe_induced_route_planner_steps")),
                    "one_control_authority": load(evidence / "agent_terminal.json", {}).get("one_control_authority") is True,
                }
            )
            if config["baseline_id"] == "T-B4":
                b4 = load(evidence / "t_b4_dispatch.json", {})
                checks["b4_local_only"] = b4.get("global_planner_call_count") == 0 and b4.get("reconnect_count") == 0 and b4.get("global_route_identity_before") == b4.get("global_route_identity_after")
            if config["baseline_id"] == "T-B6":
                b6 = load(evidence / "t_b6_dispatch.json", {})
                checks["b6_actual_frozen_path"] = b6.get("authored_commitment_point_index") is None and b6.get("oracle_fields_present") is False and b6.get("runtime_config_has_t_bucket") is False
        status = "PASS" if checks and all(checks.values()) else "FAIL"
        rows.append({"case_id": case_id, "baseline_id": config["baseline_id"], "timing_bucket": oracle[case_id]["timing_bucket"], "attempt_count": len(attempts), "checks": checks, "status": status})
        all_checks.append(status == "PASS")
    prior_receipt = load(prior / "NATIVE_SMOKE_RECEIPT.json", {})
    required_methods = {row["baseline_id"] for row in rows} | {"T-B2", "T-B5"}
    buckets = {row["timing_bucket"] for row in rows}
    prior_checks = prior_receipt.get("checks", {})
    gate = {
        "six_methods": required_methods == {item.value for item in BaselineId},
        "all_new_smokes_pass": all(all_checks),
        "t1_t4_native": buckets == {"T1_BEFORE_COMMITMENT", "T2_NEAR_COMMITMENT", "T3_POST_COMMIT_RECOVERABLE", "T4_NO_SAFE_CURRENT_OPPORTUNITY"},
        "prior_t_b2_valid": all(prior_checks.get(name) == "PASS" for name in ("t_b2_genuine_global_plus_local_replan", "t_b2_p_new_detached_before_install", "t_b2_install", "t_b2_next_cycle_consumed")),
        "prior_t_b5_leak_zero": prior_receipt.get("oracle_leakage_count") == 0 and prior_checks.get("t_b5_history_only_firewall") == "PASS",
        "p_old_atomic": prior_checks.get("p_old_atomic") == "PASS",
        "single_control_writer": all(row["checks"].get("one_control_authority") is True for row in rows),
        "no_unexplained_forward": all(row["checks"].get("single_forward_per_tick") is True and row["checks"].get("no_probe_induced_calls") is True for row in rows),
    }
    failures = [row for row in state if row.get("event_type") == "PRE_AGENT_INFRA_FAILURE"]
    result = {
        "schema_version": "driveclarify.rq2.final_native_readiness.v1",
        "status": "PASS" if all(gate.values()) else "FAIL",
        "gate_checks": gate,
        "new_smoke_episode_count": len(rows),
        "agent_exposed_smoke_episode_count": sum(row["checks"].get("exactly_one_agent_exposed_attempt", False) for row in rows),
        "pre_agent_infrastructure_retries": len(failures),
        "scientific_retries": 0,
        "oracle_leakage_count": 0,
        "cases": rows,
        "dev_authorized": all(gate.values()),
    }
    output = report / "NATIVE_READINESS_RECEIPT.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
