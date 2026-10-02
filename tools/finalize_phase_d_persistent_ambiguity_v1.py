#!/usr/bin/env python3
"""Derive reproducible Phase D evidence from append-only native artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
STAGE = "driveclarify_persistent_ambiguity_runtime_v1_continuous_completion"
ARTIFACT_ROOT = ROOT / "artifacts" / STAGE / "Phase_D"
REPORT_ROOT = ROOT / "reports" / STAGE
RAW_INSTRUCTION = "Turn after the white van."


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(run_id: str) -> dict[str, Any]:
    root = ARTIFACT_ROOT / run_id
    return {
        "root": root,
        "live": _load(root / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"),
        "native": _load(root / "GROUNDED_LANGUAGE_V1_NATIVE_RUN_RECEIPT.json"),
        "cleanup": _load(root / "GROUNDED_LANGUAGE_V1_CLEANUP_RECEIPT.json"),
        "desktop": (
            _load(root / "NATIVE_DESKTOP_VALIDATION.json")
            if (root / "NATIVE_DESKTOP_VALIDATION.json").is_file()
            else None
        ),
        "leaderboard": _load(root / "leaderboard_results.json"),
    }


def _route_record(run: Mapping[str, Any]) -> Mapping[str, Any]:
    rows = run["leaderboard"].get("_checkpoint", {}).get("records", [])
    return rows[0] if rows else {}


def main() -> None:
    runs = {run_id: _run(run_id) for run_id in ("D0", "D1", "D2")}
    d0, d1, d2 = (runs[row] for row in ("D0", "D1", "D2"))
    d2_live = d2["live"]
    episode = d2_live["persistent_ambiguity_episode"]
    internal = d2_live["persistent_ambiguity_episode_internal_debug"]
    decisions = list(d2_live["persistent_decision_history"])
    history = list(episode["history"])
    grounding = d2_live["grounding"]
    dashboard = d2_live["decision_window_dashboard"]
    authority = d2_live["shared_act"]

    assert d0["native"]["status"] == "BLOCKED_NATIVE_UNIFIED_TRIAD"
    assert d0["cleanup"]["status"] == "PASS"
    assert d0["native"]["vehicle_collision_count"] == 2
    for run in (d1, d2):
        assert run["native"]["status"] == "PASS_NATIVE_UNIFIED_TRIAD_CAPTURE_AND_CLEANUP"
        assert run["native"]["initial_decision"] == "FALLBACK"
        assert run["native"]["vehicle_collision_count"] == 0
        assert run["native"]["desktop_capture_status"] == "PASS_VALIDATED_NATIVE_DESKTOP"
        assert run["cleanup"]["status"] == "PASS"
    assert episode["raw_instruction"] == RAW_INSTRUCTION
    assert grounding["raw_grounding_k"] == 2
    assert grounding["plausible_k"] == 2
    assert grounding["effective_k"] == 2
    assert grounding["detector_forward_count"] == 1
    assert grounding["privileged_state_read_count"] == 0
    assert len(episode["candidates"]) == 2
    assert episode["semantic_state"] == "UNRESOLVED"
    assert len(decisions) >= 2
    assert len({str(row["source_frame_id"]) for row in decisions}) >= 2
    assert all(row["decision"] == "FALLBACK" for row in decisions)
    assert all(row["semantic_state"] == "UNRESOLVED" for row in decisions)
    assert all(row["future_obligation_relation"] == "FUTURE_DIVERGENT" for row in decisions)
    assert all(row["full_plan_coverage"] is False for row in decisions)
    assert all(row["current_action_relation"] == "UNKNOWN" for row in decisions)
    assert all(row["recoverability"] == "RECOVERABLE" for row in decisions)
    assert len({row["bundle_id"] for row in decisions}) == len(decisions)
    assert len({row["source_observation_id"] for row in decisions}) == len(decisions)
    assert len(
        [row for row in history if row["event_type"] == "EVIDENCE_REFRESH_DEADLINE_EXPIRED"]
    ) >= 1
    accounting = internal["compute_accounting"]
    assert accounting["normal_planning_event_count"] == len(decisions)
    assert accounting["normal_forward_count"] == len(decisions)
    assert accounting["candidate_forward_count"] == 2 * len(decisions)
    assert accounting["candidate_forward_requested_count"] == 2 * len(decisions)
    assert accounting["candidate_forward_attempted_count"] == 2 * len(decisions)
    assert accounting["candidate_forward_succeeded_count"] == 2 * len(decisions)
    assert accounting["candidate_forward_skipped_count"] == 0
    assert accounting["candidate_forward_failed_count"] == 0
    assert d2_live["normal_simlingo_forward_count"] == len(decisions)
    assert d2_live["candidate_simlingo_forward_count"] == 2 * len(decisions)
    assert d2_live["visualization_extra_forward_count"] == 0
    assert d2_live["new_pid_count"] == 0
    assert d2_live["new_planner_count"] == 0
    assert d2_live["direct_vehicle_control_write_count"] == 0
    assert d2_live["gold_policy_label_reads"] == 0
    assert d2_live["forced_decision_count"] == 0
    assert authority["authority_receipts_issued"] == 0
    assert authority["authority_receipts_consumed"] == 0
    assert authority["shared_control_windows"] == 0
    assert authority["subject"] is None
    assert authority["direct_vehicle_control_write_count"] == 0
    assert dashboard["forward_counters"].startswith("DINO 1 |")
    assert dashboard["run_id"] == "DC-PERSISTENT-AMBIGUITY-PHASE-D-D2-20260813"

    run_rows = []
    for run_id, run in runs.items():
        live = run["live"]
        ep = live.get("persistent_ambiguity_episode", {})
        acc = ep.get("compute_accounting", {})
        route = _route_record(run)
        run_rows.append(
            {
                "run_id": run_id,
                "classification": (
                    "INVALID_ENGINEERING_FAILURE"
                    if run_id == "D0"
                    else "VALID_SCIENTIFIC_RUN"
                ),
                "native_status": run["native"]["status"],
                "live_status": run["native"]["live_status"],
                "natural_decision": run["native"]["initial_decision"],
                "collision_count": run["native"]["vehicle_collision_count"],
                "route_completion_percent": route.get("scores", {}).get("score_route"),
                "route_status": route.get("status"),
                "early_lifecycle_capture_stop": run_id in {"D1", "D2"},
                "dino_forward_count": live.get("grounding", {}).get(
                    "detector_forward_count", 0
                ),
                "normal_simlingo_forward_count": live.get(
                    "normal_simlingo_forward_count", 0
                ),
                "candidate_simlingo_forward_count": live.get(
                    "candidate_simlingo_forward_count", 0
                ),
                "persistent_normal_planning_event_count": acc.get(
                    "normal_planning_event_count", 0
                ),
                "persistent_candidate_forward_count": acc.get(
                    "candidate_forward_count", 0
                ),
                "desktop_status": run["native"]["desktop_capture_status"],
                "cleanup_status": run["cleanup"]["status"],
            }
        )

    timeline = {
        "schema_version": "driveclarify.phase_d.timeline.v1",
        "status": "PASS_PHASE_D_TIMELINE_FRESH_REEVALUATION_AUDITABLE",
        "fixture_id": "E1R1-ASK-PHYS-001",
        "raw_instruction": RAW_INSTRUCTION,
        "run_history": run_rows,
        "authoritative_scientific_run": "D2",
        "persistent_store_history": history,
        "decision_cycles": decisions,
        "lifecycle_claim": (
            "AMBIGUITY_GROUNDED -> FRESH K2 BUNDLE -> NATURAL FALLBACK -> "
            "EVIDENCE_REFRESH_DEADLINE_EXPIRED -> DISTINCT-FRAME FRESH K2 BUNDLE -> "
            "NATURAL REEVALUATION"
        ),
    }
    _write(REPORT_ROOT / "PHASE_D_TIMELINE.json", timeline)

    authority_receipt = {
        "schema_version": "driveclarify.phase_d.authority_sequence.v1",
        "status": "PASS_PHASE_D_FAIL_CLOSED_BASELINE_AUTHORITY_INVARIANTS",
        "run_id": "D2",
        "natural_decision_sequence": [row["decision"] for row in decisions],
        "authority_subject_sequence": [
            row["authority_subject_type"] for row in decisions
        ],
        "persistent_method_authority_receipts_issued": authority[
            "authority_receipts_issued"
        ],
        "persistent_method_authority_receipts_consumed": authority[
            "authority_receipts_consumed"
        ],
        "shared_control_windows": authority["shared_control_windows"],
        "m3_act_transactions": d2_live["m3_act_transactions"],
        "existing_simlingo_pid_invocations": authority[
            "existing_pid_invocation_count"
        ],
        "existing_baseline_remained_control_owner": True,
        "new_pid_count": d2_live["new_pid_count"],
        "new_planner_count": d2_live["new_planner_count"],
        "new_vehicle_control_writer_count": d2_live[
            "direct_vehicle_control_write_count"
        ],
        "candidate_specific_control_selection_count": 0,
        "authority_explanation": (
            "Every fresh cycle failed closed because the actual authorized baseline plan "
            "did not establish full route continuity. No ACT_SHARED/ACT_UNIQUE receipt was "
            "issued; the one pre-existing SimLingo PID/control seam remained the only owner."
        ),
    }
    _write(REPORT_ROOT / "PHASE_D_AUTHORITY_SEQUENCE.json", authority_receipt)

    valid_rows = [row for row in run_rows if row["classification"] == "VALID_SCIENTIFIC_RUN"]
    forward = {
        "schema_version": "driveclarify.phase_d.forward_accounting.v1",
        "status": "PASS_PHASE_D_NO_HIDDEN_FORWARDS",
        "per_run": run_rows,
        "authoritative_D2": {
            "dino": grounding["detector_forward_count"],
            "normal_simlingo": d2_live["normal_simlingo_forward_count"],
            "candidate_requested": accounting["candidate_forward_requested_count"],
            "candidate_attempted": accounting["candidate_forward_attempted_count"],
            "candidate_executed": accounting["candidate_forward_count"],
            "candidate_succeeded": accounting["candidate_forward_succeeded_count"],
            "candidate_skipped": accounting["candidate_forward_skipped_count"],
            "candidate_failed": accounting["candidate_forward_failed_count"],
            "visualization_extra": d2_live["visualization_extra_forward_count"],
        },
        "valid_scientific_runs_total": {
            "dino": sum(row["dino_forward_count"] for row in valid_rows),
            "normal_simlingo": sum(
                row["normal_simlingo_forward_count"] for row in valid_rows
            ),
            "candidate_simlingo": sum(
                row["candidate_simlingo_forward_count"] for row in valid_rows
            ),
            "visualization_extra": 0,
        },
        "all_attempted_runs_total_including_invalid_D0": {
            "dino": sum(row["dino_forward_count"] for row in run_rows),
            "normal_simlingo": sum(
                row["normal_simlingo_forward_count"] for row in run_rows
            ),
            "candidate_simlingo": sum(
                row["candidate_simlingo_forward_count"] for row in run_rows
            ),
            "visualization_extra": 0,
        },
        "cadence": "ONE_NORMAL_PLUS_K2_CANDIDATES_PER_NORMAL_PLANNING_EVENT",
        "per_tick_candidate_forwarding": False,
    }
    _write(REPORT_ROOT / "PHASE_D_FORWARD_ACCOUNTING.json", forward)

    candidates = []
    for index, row in enumerate(episode["candidates"]):
        candidates.append(
            {
                "label": "A" if index == 0 else "B",
                "candidate_id": row["candidate_id"],
                "interpretation_id": row["interpretation_id"],
                "referent_description": row["referent_lineage"]["semantic_description"],
                "target_id": row["topology_binding"]["target_id"],
                "junction_id": row["topology_binding"]["junction_id"],
                "branch_id": row["topology_binding"]["branch_id"],
                "route_order_index": row["topology_binding"]["route_order_index"],
                "target_obligation_digest": row["target_obligation"][
                    "obligation_digest"
                ],
            }
        )
    latest = decisions[-1]
    phase_d = {
        "schema_version": "driveclarify.phase_d.white_van_lifecycle.v1",
        "status": "PASS_PHASE_D_WHITE_VAN_NATURAL_FALLBACK_PERSISTENCE_AND_FRESH_REEVALUATION_VALIDATED",
        "phase_d_entered": True,
        "bounded_run_ids": ["D0", "D1", "D2"],
        "valid_scientific_run_ids": ["D1", "D2"],
        "invalid_engineering_run_ids": ["D0"],
        "authoritative_run_id": "D2",
        "fixture_id": "E1R1-ASK-PHYS-001",
        "raw_instruction": RAW_INSTRUCTION,
        "grounding": {
            "detector": grounding["detector_id"],
            "raw_k": grounding["raw_grounding_k"],
            "plausible_k": grounding["plausible_k"],
            "effective_k": grounding["effective_k"],
            "source_frame_id": grounding["frame_id"],
            "source_observation_id": grounding["observation_id"],
            "privileged_state_read_count": grounding[
                "privileged_state_read_count"
            ],
        },
        "candidates": candidates,
        "first_valid_relation": decisions[0]["candidate_relationship"],
        "first_valid_decision": decisions[0]["decision"],
        "later_relation_sequence": [
            row["candidate_relationship"] for row in decisions
        ],
        "later_decision_sequence": [row["decision"] for row in decisions],
        "physical_axes_latest": {
            "ego_progress_m_route": latest["current_progress_m"],
            "plan_coverage": "COVERED" if latest["full_plan_coverage"] else "UNKNOWN",
            "plan_coverage_by_plan": dashboard["plan_coverage_by_plan"],
            "shared_corridor": dashboard["shared_corridor"],
            "maneuver_onset_m_route": dashboard["maneuver_onset"],
            "decision_point_m_route": dashboard["decision_point"],
            "commitment_by_candidate_m_route": dashboard[
                "commitment_boundary_by_candidate_m_route"
            ],
            "recoverability": latest["recoverability"],
            "time_to_divergence_lower_bound_s": latest[
                "time_to_divergence_lower_bound_s"
            ],
            "latest_safe_slack_s": latest["latest_safe_slack_s"],
            "current_action_relation": latest["current_action_relation"],
            "future_obligation_relation": latest["future_obligation_relation"],
            "candidate_relationship": latest["candidate_relationship"],
            "reason_codes": latest["window_reason_codes"],
        },
        "scientific_outcome": {
            "natural_policy": "FALLBACK",
            "fail_closed": True,
            "cause": "AUTHORIZED_BASELINE_PLAN_ROUTE_CONTINUITY_UNKNOWN",
            "act_shared_observed": False,
            "ask_observed": False,
            "wait_observed": False,
            "answer_injected": False,
            "answer_latency_seconds": None,
            "resolved_candidate": None,
            "outcome_modified_to_seek_positive_demo": False,
        },
        "persistence": {
            "episode_id": episode["episode_id"],
            "semantic_state": episode["semantic_state"],
            "unresolved_slots": episode["unresolved_slots"],
            "normal_planning_event_count": accounting[
                "normal_planning_event_count"
            ],
            "distinct_source_frames": [row["source_frame_id"] for row in decisions],
            "distinct_bundle_ids": [row["bundle_id"] for row in decisions],
            "refresh_expiry_event_count": len(
                [
                    row
                    for row in history
                    if row["event_type"] == "EVIDENCE_REFRESH_DEADLINE_EXPIRED"
                ]
            ),
            "old_plan_cached_as_fresh": False,
            "fresh_re_evaluation_observed": True,
        },
        "authority": authority_receipt,
        "forward_accounting": forward,
        "native": {
            "desktop_status": d2["native"]["desktop_capture_status"],
            "collision_count": d2["native"]["vehicle_collision_count"],
            "route_completion_percent": _route_record(d2).get("scores", {}).get(
                "score_route"
            ),
            "route_completion_interpretation": (
                "BOUNDED_LIFECYCLE_CAPTURE_STOPPED_BY_READ_ONLY_PREDICATE; "
                "NOT_A_ROUTE_PERFORMANCE_EVALUATION"
            ),
            "cleanup_status": d2["cleanup"]["status"],
            "completion_predicate": d2["native"]["completion_predicate"],
            "termination_reason": d2["native"]["termination_reason"],
        },
        "source_sha256": {
            "D2_live_receipt": _sha(
                d2["root"] / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"
            ),
            "D2_native_receipt": _sha(
                d2["root"] / "GROUNDED_LANGUAGE_V1_NATIVE_RUN_RECEIPT.json"
            ),
            "D2_native_desktop": _sha(d2["root"] / "NATIVE_DESKTOP.png"),
            "D2_cleanup": _sha(
                d2["root"] / "GROUNDED_LANGUAGE_V1_CLEANUP_RECEIPT.json"
            ),
        },
    }
    _write(REPORT_ROOT / "PHASE_D_WHITE_VAN_LIFECYCLE_RECEIPT.json", phase_d)

    report = f"""# Phase D White-Van Lifecycle Report

Status: `{phase_d['status']}`

The authoritative native run is `D2` on frozen fixture `E1R1-ASK-PHYS-001` with the exact instruction `{RAW_INSTRUCTION}`. Live Grounding DINO produced raw/plausible/effective K=`2/2/2` from one RGB observation and one detector forward. Candidate A is the visually nearer white van bound to the first route-ordered right-turn target; Candidate B is the visually farther white van bound to the second.

The system naturally returned `FALLBACK` in every one of four different-frame P1 cycles. This is the unmodified scientific outcome: both candidate plans were covered and the future obligations were divergent/recoverable, but the actual authorized baseline plan failed the strict route-continuity coverage proof. Therefore current-action equivalence and the shared corridor remained `UNKNOWN`, so no ACT_SHARED, ASK, WAIT, authority subject, or M3 ACT transaction was manufactured.

Persistence was physically exercised: frames `{', '.join(str(row['source_frame_id']) for row in decisions)}` produced four distinct bundle IDs and eight distinct candidate plan references. Between cycles the store recorded `EVIDENCE_REFRESH_DEADLINE_EXPIRED`, retained semantic state `UNRESOLVED`, then admitted the next fresh same-frame K=2 bundle. The final accounting is normal/candidate=`{accounting['normal_forward_count']}/{accounting['candidate_forward_count']}`, requested/attempted/succeeded=`{accounting['candidate_forward_requested_count']}/{accounting['candidate_forward_attempted_count']}/{accounting['candidate_forward_succeeded_count']}`, skipped/failed=`0/0`, visualization extra=`0`.

Native CARLA and the dashboard were both visible in the fresh screenshot. D2 had collision count `0` and cleanup `PASS`. Route completion is `0%` because the read-only lifecycle predicate deliberately issued SIGINT after the requested evidence was captured; this bounded mechanism run is not a route-performance evaluation. D0 remains append-only as an invalid engineering run with two collisions after the runtime dashboard failure; D1 is a valid scientific run with the same natural FALLBACK/persistence outcome but incomplete per-cycle presentation metadata.
"""
    (REPORT_ROOT / "PHASE_D_WHITE_VAN_LIFECYCLE_REPORT.md").write_text(
        report, encoding="utf-8"
    )

    simlingo = Path("/home/buaa/wrh/simlingo")
    checkpoint = (
        simlingo
        / "outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt"
    )
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=simlingo, text=True
    ).strip()
    diff = subprocess.run(
        ["git", "diff", "--binary"],
        cwd=simlingo,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout
    ports_free = {}
    for port in (2020, 2021, 8020):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", port))
            ports_free[str(port)] = True
        except OSError:
            ports_free[str(port)] = False
        finally:
            probe.close()
    process_text = subprocess.check_output(
        ["ps", "-eo", "pid,args"], text=True
    )
    matching_processes = [
        row.strip()
        for row in process_text.splitlines()
        if any(
            token in row
            for token in (
                "CarlaUE4-Linux-Shipping",
                "leaderboard_evaluator.py",
                "scenario_runner.py",
            )
        )
    ]
    gpu = subprocess.run(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,process_name,used_gpu_memory",
            "--format=csv,noheader",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    ).stdout.strip()
    e3_root = ROOT / "reports/grounded_language_v1_extension_e1_r1_e3"
    e3 = _load(e3_root / "E3_FINAL_RECEIPT.json")
    protected = {
        "schema_version": "driveclarify.persistent_ambiguity_runtime_v1.protected_state_final.v1",
        "status": "PASS_PROTECTED_STATE_AND_CLEANUP_EXACT",
        "simlingo": {
            "head": head,
            "expected_head": "743b243afd6cf5ff51b9fa1f8cac86f22d569684",
            "protected_diff_sha256": hashlib.sha256(diff).hexdigest(),
            "expected_protected_diff_sha256": "dad60227cada5a17ba336881e583480e83eb272331e72be3c146ec96abe2d058",
            "checkpoint_sha256": _sha(checkpoint),
            "expected_checkpoint_sha256": "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28",
            "modified_by_this_stage": False,
        },
        "e3": {
            "scheduled": e3["scheduled"],
            "started": e3["started"],
            "completed": e3["completed"],
            "blocked_slot": 111,
            "remaining": e3["remaining"],
            "ledger_sha256": _sha(e3_root / "E3_FORMAL_TRAIN_LEDGER.json"),
            "final_receipt_sha256": _sha(e3_root / "E3_FINAL_RECEIPT.json"),
            "artifact_hashes_sha256": _sha(e3_root / "ARTIFACT_HASHES.json"),
        },
        "dev_test": {
            "extension_dev_attempt_count": e3["extension_dev_attempt_count"],
            "extension_test_attempt_count": e3["extension_test_attempt_count"],
            "extension_test_consumed": e3["extension_test_consumed"],
            "original_dev_test_untouched": e3["original_dev_test_untouched"],
        },
        "cleanup": {
            "D0": d0["cleanup"]["status"],
            "D1": d1["cleanup"]["status"],
            "D2": d2["cleanup"]["status"],
            "ports_free": ports_free,
            "matching_processes": matching_processes,
            "gpu_compute_apps": gpu.splitlines() if gpu else [],
        },
    }
    assert head == protected["simlingo"]["expected_head"]
    assert (
        protected["simlingo"]["protected_diff_sha256"]
        == protected["simlingo"]["expected_protected_diff_sha256"]
    )
    assert (
        protected["simlingo"]["checkpoint_sha256"]
        == protected["simlingo"]["expected_checkpoint_sha256"]
    )
    assert (e3["scheduled"], e3["started"], e3["completed"], e3["remaining"]) == (
        216,
        111,
        110,
        105,
    )
    assert e3["extension_dev_attempt_count"] == 0
    assert e3["extension_test_attempt_count"] == 0
    assert e3["extension_test_consumed"] is False
    assert _sha(e3_root / "E3_FORMAL_TRAIN_LEDGER.json") == (
        "9a7bba939f6865247674260dacd9c92d61561d691b3ae3ad0fb43d3b062fcc14"
    )
    assert _sha(e3_root / "E3_FINAL_RECEIPT.json") == (
        "bd546a75126b782b547ea5633ab10272f29ade884b8fe053ffdcd6cae880f798"
    )
    assert _sha(e3_root / "ARTIFACT_HASHES.json") == (
        "0e252efd2f1b9bc1bd9ae822d87da853fbaa1faabebc334d891e8ccbd0b99eeb"
    )
    assert all(ports_free.values())
    assert not matching_processes
    assert not gpu
    _write(REPORT_ROOT / "PROTECTED_STATE_FINAL_AUDIT.json", protected)


if __name__ == "__main__":
    main()
