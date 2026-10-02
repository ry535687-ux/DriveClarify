"""Evidence-only bounded qualification post-processor; no effect analysis."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from driveclarify_t_mvp.canonical import canonical_bytes, canonical_sha256
from driveclarify_t_mvp.firewall import assert_no_oracle_fields


CASES = ("ENG-TMVP-01", "ENG-TMVP-02", "ENG-TMVP-03", "ENG-TMVP-04")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_once(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    report = Path(args.report).resolve()
    runtime_cases = _load(
        report.parent.parent / "driveclarify_t_mvp_native_qualification" / "runtime_cases.json"
    )["cases"]
    rows = []
    all_pass = True
    for case_id in CASES:
        root = report / "native_runs" / case_id / "attempt_1"
        evidence = root / "agent_evidence"
        checks: dict[str, bool] = {}
        required = (
            root / "process_job" / "PROCESS_RECEIPT.json",
            root / "process_job" / "prelaunch_freeze_verification.json",
            evidence / "evaluator" / "injection_receipt.json",
            evidence / "exchange" / "runtime_update.json",
            evidence / "native_p_old.json",
            evidence / "cp0_probe.jsonl",
            evidence / "cp0_equivalence.json",
            evidence / "agent_terminal.json",
        )
        checks["required_files_present"] = all(path.exists() for path in required)
        if not checks["required_files_present"]:
            rows.append({"case_id": case_id, "checks": checks, "status": "FAIL"})
            all_pass = False
            continue
        process = _load(required[0])
        freeze = _load(required[1])
        injection = _load(required[2])
        runtime = _load(required[3])
        p_old = _load(required[4])
        cp0 = _jsonl(required[5])
        equivalence = _load(required[6])
        terminal = _load(required[7])
        assert_no_oracle_fields(runtime)
        expected = runtime_cases[case_id]
        injection_frame = int(injection["receipt"]["actual_injection_frame"])
        cp0_by_frame = {int(row["frame"]["carla_snapshot_frame"]): row for row in cp0}
        later_frames = sorted(frame for frame in cp0_by_frame if frame > injection_frame)
        first_later = None if not later_frames else later_frames[0]
        checks.update(
            {
                "source_freeze_pass": freeze.get("status") == "PASS",
                "cleanup_pass": process.get("cleanup_pass") is True,
                "injection_before_policy": injection["receipt"].get("injected_before_policy") is True,
                "update_identity_join": injection["receipt"]["update_event_id"]
                == runtime["update_event_id"],
                "frame_identity_join": injection_frame == int(runtime["actual_injection_frame"]),
                "case_identity_join": runtime["case_id"] == case_id,
                "p_old_same_boundary": int(p_old["p_old"]["source_frame"]) == injection_frame,
                "unknown_preserved": p_old["observable_commitment"]["state"] == "UNKNOWN",
                "agent_processed_once": terminal.get("update_processed") is True,
                "next_normal_forward_present": first_later is not None,
                "cp0_one_forward_each_tick": bool(equivalence.get("forward_call_count_per_tick"))
                and set(equivalence["forward_call_count_per_tick"]) == {1},
                "cp0_one_pid_each_tick": bool(equivalence.get("pid_call_count_per_tick"))
                and set(equivalence["pid_call_count_per_tick"]) == {1},
                "cp0_control_identity_preserved": equivalence.get("control_identity_preserved_ticks")
                == len(equivalence.get("forward_call_count_per_tick", [])),
                "cp0_no_identity_violations": equivalence.get("identity_violations") == [],
                "cp0_no_probe_induced_calls": all(
                    equivalence.get(name) == 0
                    for name in (
                        "probe_induced_model_calls",
                        "probe_induced_pid_calls",
                        "probe_induced_route_planner_steps",
                    )
                ),
                "expected_seed": int(expected["seed"]) == int(terminal["seed"]),
            }
        )
        if first_later is not None:
            next_record = cp0_by_frame[first_later]
            checks["next_forward_used_by_pid"] = (
                next_record["model_output"].get("used_by_baseline_pid") is True
                and next_record["model_output"].get("generated_from_same_forward") is True
                and next_record["m2b_decision"].get("decision_source") == "baseline_pid"
            )
            control_path = evidence / "controls" / f"control_{first_later:08d}.json"
            checks["wrapper_cp0_control_values_join"] = control_path.exists()
            if control_path.exists():
                wrapper_control = _load(control_path)
                cp0_control = next_record["baseline_control"]
                checks["wrapper_cp0_control_values_join"] = all(
                    float(wrapper_control[name]) == float(cp0_control[name])
                    for name in ("steer", "throttle", "brake")
                )
        if case_id == "ENG-TMVP-01":
            chain_files = (
                evidence / "t_b2_prepare_install.json",
                evidence / "t_b2_consumption.json",
                evidence / "transition_events.jsonl",
                evidence / "transition_receipt.json",
            )
            checks["t_b2_chain_files"] = all(path.exists() for path in chain_files)
            if checks["t_b2_chain_files"]:
                prepare = _load(chain_files[0])
                consume = _load(chain_files[1])
                transition_events = _jsonl(chain_files[2])
                transition = _load(chain_files[3])
                lifecycle = [row["state"] for row in consume["candidate_lifecycle"]]
                candidate_terminal = next(
                    row
                    for row in transition_events
                    if row["event_type"] == "CANDIDATE_PREPARATION_TERMINAL"
                )
                checks.update(
                    {
                        "candidate_detached_before_install": prepare["candidate"]["installation_state"]
                        == "UNINSTALLED",
                        "candidate_authority_isolated": candidate_terminal["payload"][
                            "authority_unchanged"
                        ]
                        is True,
                        "full_replan_to_exact_G": prepare.get("destination_exact_match") is True,
                        "lifecycle_exact": lifecycle
                        == [
                            "CANDIDATE_PREPARED",
                            "CANDIDATE_ADMITTED",
                            "CANDIDATE_COMMITTED",
                            "CANDIDATE_CONSUMED",
                        ],
                        "physical_active_consumed_equal": consume["physical_active_route_identity"]
                        == consume["physical_consumed_route_identity"],
                        "transition_receipt_integrity": transition["integrity_assertions"].get(
                            "candidate_isolation_proven"
                        )
                        is True,
                        "global_planner_once": consume["accounting"]["global_planner_call_count"] == 1,
                        "local_planner_once": consume["accounting"]["local_planner_call_count"] == 1,
                        "install_once": consume["accounting"]["route_installation_count"] == 1,
                    }
                )
        if case_id == "ENG-TMVP-02":
            pre_path = evidence / "t_b5_pre_forward.json"
            post_path = evidence / "t_b5_post_forward.json"
            checks["t_b5_files"] = pre_path.exists() and post_path.exists()
            if checks["t_b5_files"]:
                pre, post = _load(pre_path), _load(post_path)
                checks.update(
                    {
                        "frozen_vla_identity": pre["frozen_vla_identity"]["weights_and_decoding_frozen"]
                        is True,
                        "actual_tokenizer": pre["history_payload"]["token_count"] > 0,
                        "prompt_bound_exactly": post["custom_prompt_still_exact"] is True,
                        "prompt_consumed_next_forward": post["custom_prompt_embedded_in_inner_prompt"]
                        is True
                        and int(post["normal_forward_frame"]) > int(post["update_boundary_frame"]),
                        "one_normal_forward": post["normal_forward_count_this_wrapper_cycle"] == 1,
                        "model_object_unchanged": post["model_object_identity_unchanged"] is True,
                    }
                )
        status = "PASS" if checks and all(checks.values()) else "FAIL"
        all_pass = all_pass and status == "PASS"
        rows.append(
            {
                "case_id": case_id,
                "checks": checks,
                "injection_frame": injection_frame,
                "next_normal_forward_frame": first_later,
                "oracle_receipt_sha256": canonical_sha256(injection),
                "runtime_update_sha256": canonical_sha256(runtime),
                "status": status,
            }
        )
    summary = {
        "schema_version": "driveclarify.rq2.bounded_native_postprocess.v1",
        "engineering_only": True,
        "scientific_effect_analysis_performed": False,
        "rq2_verdict_opened": False,
        "cases": rows,
        "status": "PASS" if all_pass else "FAIL",
    }
    _write_once(report / "NATIVE_QUALIFICATION_SUMMARY.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
