#!/usr/bin/env python3
"""Finalize the bounded persistent-ambiguity Phase B/C/D evidence honestly."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
STAGE = "driveclarify_persistent_ambiguity_runtime_v1_continuous_completion"
REPORT = ROOT / "reports" / STAGE
ARTIFACT = ROOT / "artifacts" / STAGE
PHASE_D = ARTIFACT / "Phase_D"
STATUS = (
    "PARTIAL_PASS_RUNTIME_AND_PERSISTENCE_VALIDATED_"
    "QUERY_NECESSITY_NOT_REACHED_WITHIN_BOUNDED_SCENE"
)
NEXT_ACTION = "DRIVECLARIFY_METHOD_V1_FINAL_FREEZE_AND_PAPER_EXPERIMENT_PROTOCOL"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(str(temporary), str(path))


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def d_run(run_id: str) -> dict[str, Any]:
    root = PHASE_D / run_id
    return {
        "root": root,
        "live": load(root / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"),
        "native": load(root / "GROUNDED_LANGUAGE_V1_NATIVE_RUN_RECEIPT.json"),
        "cleanup": load(root / "GROUNDED_LANGUAGE_V1_CLEANUP_RECEIPT.json"),
        "leaderboard": load(root / "leaderboard_results.json"),
        "desktop": (
            load(root / "NATIVE_DESKTOP_VALIDATION.json")
            if (root / "NATIVE_DESKTOP_VALIDATION.json").is_file()
            else None
        ),
    }


def route_record(run: Mapping[str, Any]) -> Mapping[str, Any]:
    rows = run["leaderboard"].get("_checkpoint", {}).get("records", [])
    return rows[0] if rows else {}


def sync_phase_b_flat_evidence() -> None:
    """Bind required flat names to the accepted B1-R4 + B2 evidence set."""

    mapping = {
        "AUTHORITY_MUTATION_AUDIT.json": "AUTHORITY_MUTATION_AUDIT.json",
        "COORDINATE_CALIBRATION.json": "COORDINATE_FRAME_VALIDATION.json",
        "COORDINATE_FRAME_VALIDATION.json": "COORDINATE_FRAME_VALIDATION.json",
        "DECISION_WINDOW_EVIDENCE.json": "DECISION_WINDOW_EVIDENCE.json",
        "FORWARD_ACCOUNTING.json": "FORWARD_ACCOUNTING.json",
        "PHASE_B_EVIDENCE_RECEIPT.json": "PHASE_B_EVIDENCE_RECEIPT.json",
        "PRIVILEGED_READ_AUDIT.json": "PRIVILEGED_READ_AUDIT.json",
        "ROUTE_PROGRESS_EVIDENCE.json": "EGO_ROUTE_PROGRESS_EVIDENCE.json",
        "EGO_ROUTE_PROGRESS_EVIDENCE.json": "EGO_ROUTE_PROGRESS_EVIDENCE.json",
        "SHARED_CORRIDOR_EVIDENCE.json": "SHARED_CORRIDOR_EVIDENCE.json",
        "MANEUVER_ONSET_EVIDENCE.json": "MANEUVER_ONSET_EVIDENCE.json",
        "PLAN_COVERAGE_EVIDENCE.json": "PLAN_COVERAGE_EVIDENCE.json",
        "DECISION_POINT_EVIDENCE.json": "DECISION_POINT_EVIDENCE.json",
        "COMMITMENT_BOUNDARY_EVIDENCE.json": "COMMITMENT_BOUNDARY_EVIDENCE.json",
        "RECOVERABILITY_EVIDENCE.json": "RECOVERABILITY_EVIDENCE.json",
        "TIME_TO_DIVERGENCE_EVIDENCE.json": "TIME_TO_DIVERGENCE_EVIDENCE.json",
        "TIMING_EVIDENCE.json": "TIMING_EVIDENCE.json",
    }
    final_authority = {
        "status": "PASS_PHASE_B_DECISION_WINDOW_PHYSICAL_EVIDENCE_COMPLETE",
        "accepted_run_ids": ["B1-R4", "B2"],
        "repeat_stability_receipt": "PHASE_B_FINAL_EVIDENCE_RECEIPT.json",
    }
    for flat_name, source_name in mapping.items():
        primary = load(REPORT / "B1-R4" / source_name)
        repeat = load(REPORT / "B2" / source_name)
        primary["phase_b_final_authority"] = final_authority
        primary["mandatory_repeat_summary"] = {
            "run_id": "B2",
            "status": repeat.get("status"),
            "authorization_eligible": repeat.get("authorization_eligible"),
            "sha256": sha(REPORT / "B2" / source_name),
        }
        write_json(REPORT / flat_name, primary)

    timing = load(REPORT / "B1-R4" / "TIMING_EVIDENCE.json")
    latest = dict(timing["latest_safe_clarification"])
    latest.update(
        {
            "schema_version": "driveclarify.phase_b.latest_safe_clarification.final.v1",
            "run_id": "B1-R4",
            "phase_b_final_authority": final_authority,
            "timing_context": {
                "measured_full_bundle_latency_seconds": timing[
                    "measured_full_bundle_latency_seconds"
                ],
                "enforced_full_bundle_timeout_seconds": timing[
                    "enforced_full_bundle_timeout_seconds"
                ],
                "synchronous_wall_compute_does_not_advance_physical_simulation": timing[
                    "synchronous_wall_compute_does_not_advance_physical_simulation"
                ],
                "mandatory_repeat_sha256": sha(REPORT / "B2" / "TIMING_EVIDENCE.json"),
            },
        }
    )
    write_json(REPORT / "LATEST_SAFE_CLARIFICATION_EVIDENCE.json", latest)


def protected_audit(runs: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    simlingo = Path("/home/buaa/wrh/simlingo")
    checkpoint = (
        simlingo
        / "outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt"
    )
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=str(simlingo), text=True
    ).strip()
    diff = subprocess.run(
        ["git", "diff", "--binary"],
        cwd=str(simlingo),
        stdout=subprocess.PIPE,
        check=True,
    ).stdout
    ports_free: dict[str, bool] = {}
    for port in (2020, 2021, 8020):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", port))
            ports_free[str(port)] = True
        except OSError:
            ports_free[str(port)] = False
        finally:
            probe.close()
    processes = subprocess.check_output(
        ["ps", "-eo", "pid,args"], text=True
    )
    matching = [
        row.strip()
        for row in processes.splitlines()
        if any(
            token in row
            for token in (
                "CarlaUE4-Linux-Shipping",
                "leaderboard_evaluator.py",
                "scenario_runner.py",
            )
        )
    ]
    gpu_text = subprocess.run(
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
    e3 = load(e3_root / "E3_FINAL_RECEIPT.json")
    result = {
        "schema_version": "driveclarify.persistent_ambiguity_runtime_v1.protected_state_final.v2",
        "status": "PASS_PROTECTED_STATE_AND_CLEANUP_EXACT",
        "simlingo": {
            "head": head,
            "expected_head": "743b243afd6cf5ff51b9fa1f8cac86f22d569684",
            "protected_diff_sha256": hashlib.sha256(diff).hexdigest(),
            "expected_protected_diff_sha256": "dad60227cada5a17ba336881e583480e83eb272331e72be3c146ec96abe2d058",
            "checkpoint_sha256": sha(checkpoint),
            "expected_checkpoint_sha256": "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28",
            "modified_by_this_stage": False,
        },
        "e3": {
            "scheduled": e3["scheduled"],
            "started": e3["started"],
            "completed": e3["completed"],
            "blocked_slot": 111,
            "remaining": e3["remaining"],
            "ledger_sha256": sha(e3_root / "E3_FORMAL_TRAIN_LEDGER.json"),
            "final_receipt_sha256": sha(e3_root / "E3_FINAL_RECEIPT.json"),
            "artifact_hashes_sha256": sha(e3_root / "ARTIFACT_HASHES.json"),
        },
        "dev_test": {
            "extension_dev_attempt_count": e3["extension_dev_attempt_count"],
            "extension_test_attempt_count": e3["extension_test_attempt_count"],
            "extension_test_consumed": e3["extension_test_consumed"],
            "original_dev_test_untouched": e3["original_dev_test_untouched"],
        },
        "cleanup": {
            run_id: run["cleanup"]["status"] for run_id, run in runs.items()
        },
        "ports_free": ports_free,
        "matching_processes": matching,
        "gpu_compute_apps": gpu_text.splitlines() if gpu_text else [],
    }
    assert result["simlingo"]["head"] == result["simlingo"]["expected_head"]
    assert result["simlingo"]["protected_diff_sha256"] == result["simlingo"][
        "expected_protected_diff_sha256"
    ]
    assert result["simlingo"]["checkpoint_sha256"] == result["simlingo"][
        "expected_checkpoint_sha256"
    ]
    assert (e3["scheduled"], e3["started"], e3["completed"], e3["remaining"]) == (
        216,
        111,
        110,
        105,
    )
    assert e3["extension_dev_attempt_count"] == 0
    assert e3["extension_test_attempt_count"] == 0
    assert e3["extension_test_consumed"] is False
    assert result["e3"]["ledger_sha256"] == "9a7bba939f6865247674260dacd9c92d61561d691b3ae3ad0fb43d3b062fcc14"
    assert result["e3"]["final_receipt_sha256"] == "bd546a75126b782b547ea5633ab10272f29ade884b8fe053ffdcd6cae880f798"
    assert result["e3"]["artifact_hashes_sha256"] == "0e252efd2f1b9bc1bd9ae822d87da853fbaa1faabebc334d891e8ccbd0b99eeb"
    assert all(value == "PASS" for value in result["cleanup"].values())
    assert all(ports_free.values()) and not matching and not gpu_text
    return result


def answer(number: int, question: str, value: Any, evidence: Any = None) -> dict[str, Any]:
    row = {"number": number, "question": question, "answer": value}
    if evidence is not None:
        row["evidence"] = evidence
    return row


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    sync_phase_b_flat_evidence()
    runs = {run_id: d_run(run_id) for run_id in ("D0", "D1", "D2", "D2-R1")}
    d0, d1, d2, d2r1 = (runs[key] for key in ("D0", "D1", "D2", "D2-R1"))
    live = d2r1["live"]
    episode = live["persistent_ambiguity_episode"]
    decisions = list(live["persistent_decision_history"])
    history = list(episode["history"])
    event_counts = Counter(row["event_type"] for row in history)
    decision_counts = Counter(row["decision"] for row in decisions)
    relation_counts = Counter(row["candidate_relationship"] for row in decisions)
    accounting = live["persistent_ambiguity_episode_internal_debug"]["compute_accounting"]
    candidates = list(episode["candidates"])
    route = route_record(d2r1)
    collisions = list(route.get("infractions", {}).get("collisions_vehicle", []))
    divergent_rows = [
        row for row in decisions if row["candidate_relationship"] == "CURRENTLY_DIVERGENT"
    ]
    assert len(decisions) == 219
    assert decision_counts == {"FALLBACK": 219}
    assert len({row["source_frame_id"] for row in decisions}) == 219
    assert len({row["source_observation_id"] for row in decisions}) == 219
    assert len({row["bundle_id"] for row in decisions}) == 219
    assert len({row["decision_window_digest"] for row in decisions}) == 219
    plan_refs = [
        ref["plan_reference_digest"]
        for row in decisions
        for ref in row["candidate_plan_references"]
    ]
    assert len(plan_refs) == len(set(plan_refs)) == 438
    assert event_counts["REFRESH_BUNDLE_STARTED"] == 219
    assert event_counts["REFRESH_BUNDLE_COMPLETE_VALID"] == 219
    assert event_counts["EVIDENCE_REFRESH_DEADLINE_EXPIRED"] == 218
    assert route.get("status") == "Completed"
    assert route.get("scores", {}).get("score_route") == 100
    assert d2r1["cleanup"]["status"] == "PASS"
    assert d2r1["desktop"]["status"] == "PASS_VALIDATED_NATIVE_DESKTOP"
    assert load(REPORT / "PHASE_D_D2_R1_NATIVE_RECEIPT_RECONCILIATION.json")[
        "valid_scientific_run"
    ] is True

    old_phase_d_path = REPORT / "PHASE_D_WHITE_VAN_LIFECYCLE_RECEIPT.json"
    superseded_path = REPORT / "PHASE_D_WHITE_VAN_LIFECYCLE_RECEIPT_D2_SUPERSEDED.json"
    if old_phase_d_path.is_file() and not superseded_path.exists():
        write_json(
            superseded_path,
            {
                "schema_version": "driveclarify.phase_d.superseded_receipt.v1",
                "status": "SUPERSEDED_BY_D2_R1_AND_FINAL_INDEPENDENT_REVIEW",
                "reason": "D2 stopped after four startup frames under a weak runner predicate.",
                "preserved_original_receipt": load(old_phase_d_path),
            },
        )
    old_report_path = REPORT / "PHASE_D_WHITE_VAN_LIFECYCLE_REPORT.md"
    old_report_copy = REPORT / "PHASE_D_WHITE_VAN_LIFECYCLE_REPORT_D2_SUPERSEDED.md"
    if old_report_path.is_file() and not old_report_copy.exists():
        write_text(
            old_report_copy,
            "# Superseded D2 Phase D report\n\n"
            "This report is retained only as the pre-review D2 derivation. It was "
            "superseded because D2 contained four startup frames and no moving "
            "ACT_SHARED lifecycle.\n\n"
            + old_report_path.read_text(encoding="utf-8"),
        )

    run_rows = []
    for run_id, run in runs.items():
        row_live = run["live"]
        row_route = route_record(run)
        classification = (
            "INVALID_ENGINEERING_FAILURE" if run_id == "D0" else "VALID_SCIENTIFIC_RUN"
        )
        run_rows.append(
            {
                "run_id": run_id,
                "classification": classification,
                "raw_native_status": run["native"]["status"],
                "live_status": row_live.get("status"),
                "natural_decision": row_live.get("initial_decision"),
                "collision_count": run["native"]["vehicle_collision_count"],
                "route_completion_percent": row_route.get("scores", {}).get("score_route"),
                "route_status": row_route.get("status"),
                "decision_cycle_count": len(row_live.get("persistent_decision_history") or []),
                "dino_forward_count": row_live.get("grounding", {}).get(
                    "detector_forward_count", 0
                ),
                "normal_simlingo_forward_count": row_live.get(
                    "normal_simlingo_forward_count", 0
                ),
                "candidate_simlingo_forward_count": row_live.get(
                    "candidate_simlingo_forward_count", 0
                ),
                "desktop_status": (
                    None if run["desktop"] is None else run["desktop"].get("status")
                ),
                "cleanup_status": run["cleanup"]["status"],
                "notes": (
                    "Dashboard serialization blocked before method authority."
                    if run_id == "D0"
                    else "Four-frame startup capture; valid but not moving lifecycle."
                    if run_id in {"D1", "D2"}
                    else "Authoritative moving bounded run; raw native desktop flag reconciled transparently."
                ),
            }
        )

    timeline = {
        "schema_version": "driveclarify.phase_d.timeline.v2",
        "status": STATUS,
        "authoritative_scientific_run": "D2-R1",
        "run_history": run_rows,
        "raw_instruction": live["raw_instruction"],
        "event_counts": dict(sorted(event_counts.items())),
        "decision_counts": dict(sorted(decision_counts.items())),
        "relationship_counts": dict(sorted(relation_counts.items())),
        "progress": {
            "first_m_route": decisions[0]["current_progress_m"],
            "last_m_route": decisions[-1]["current_progress_m"],
            "maximum_m_route": max(row["current_progress_m"] for row in decisions),
        },
        "decision_cycles": decisions,
        "material_divergence_cycle": divergent_rows[0],
        "lifecycle_claim": (
            "GROUNDED_K2 -> MOVING_FRESH_BUNDLE_x219 -> NATURAL_FALLBACK_x219 -> "
            "UNRESOLVED_PERSISTED -> QUERY_CLOSURE_NOT_REACHED"
        ),
    }
    write_json(REPORT / "PHASE_D_TIMELINE.json", timeline)

    authority = {
        "schema_version": "driveclarify.phase_d.authority_sequence.v2",
        "status": "PASS_PHASE_D_FAIL_CLOSED_BASELINE_AUTHORITY_INVARIANTS",
        "run_id": "D2-R1",
        "natural_decision_sequence_rle": [{"decision": "FALLBACK", "count": 219}],
        "authority_subject_sequence_rle": [{"subject": None, "count": 219}],
        "method_authority_receipts_issued": live["shared_act"][
            "authority_receipts_issued"
        ],
        "method_authority_receipts_consumed": live["shared_act"][
            "authority_receipts_consumed"
        ],
        "shared_control_windows": live["shared_act"]["shared_control_windows"],
        "m3_act_transactions": len(live["m3_act_transactions"]),
        "existing_simlingo_pid_invocations": live["shared_act"][
            "existing_pid_invocation_count"
        ],
        "post_hoc_control_sources": {"baseline_pid": 219},
        "new_pid_count": live["new_pid_count"],
        "new_planner_count": live["new_planner_count"],
        "new_vehicle_control_writer_count": live["direct_vehicle_control_write_count"],
        "candidate_specific_control_selection_count": 0,
        "collision_count_retained": len(collisions),
        "authority_explanation": (
            "Every cycle failed closed before method authority. Both collisions and the "
            "red-light infraction occurred under the pre-existing baseline_pid and remain "
            "scientific outcomes."
        ),
    }
    write_json(REPORT / "PHASE_D_AUTHORITY_SEQUENCE.json", authority)

    valid_rows = [row for row in run_rows if row["classification"] == "VALID_SCIENTIFIC_RUN"]
    forward = {
        "schema_version": "driveclarify.phase_d.forward_accounting.v2",
        "status": "PASS_PHASE_D_NO_HIDDEN_FORWARDS",
        "per_run": run_rows,
        "authoritative_D2_R1": {
            "dino": live["grounding"]["detector_forward_count"],
            "normal_simlingo": live["normal_simlingo_forward_count"],
            "candidate_requested": accounting["candidate_forward_requested_count"],
            "candidate_attempted": accounting["candidate_forward_attempted_count"],
            "candidate_executed": accounting["candidate_forward_count"],
            "candidate_succeeded": accounting["candidate_forward_succeeded_count"],
            "candidate_skipped": accounting["candidate_forward_skipped_count"],
            "candidate_failed": accounting["candidate_forward_failed_count"],
            "visualization_extra": live["visualization_extra_forward_count"],
        },
        "valid_scientific_runs_total": {
            "dino": sum(row["dino_forward_count"] for row in valid_rows),
            "normal_simlingo": sum(row["normal_simlingo_forward_count"] for row in valid_rows),
            "candidate_simlingo": sum(row["candidate_simlingo_forward_count"] for row in valid_rows),
            "visualization_extra": 0,
        },
        "all_attempted_runs_total_including_invalid_D0": {
            "dino": sum(row["dino_forward_count"] for row in run_rows),
            "normal_simlingo": sum(row["normal_simlingo_forward_count"] for row in run_rows),
            "candidate_simlingo": sum(row["candidate_simlingo_forward_count"] for row in run_rows),
            "visualization_extra": 0,
        },
        "cadence": "ONE_NORMAL_PLUS_K2_CANDIDATES_PER_NORMAL_PLANNING_EVENT",
        "per_tick_hidden_candidate_forwarding": False,
    }
    write_json(REPORT / "PHASE_D_FORWARD_ACCOUNTING.json", forward)

    candidate_summary = []
    for index, row in enumerate(candidates):
        candidate_summary.append(
            {
                "label": "A" if index == 0 else "B",
                "candidate_id": row["candidate_id"],
                "interpretation_id": row["interpretation_id"],
                "referent_description": row["referent_lineage"]["semantic_description"],
                "target_id": row["topology_binding"]["target_id"],
                "junction_id": row["topology_binding"]["junction_id"],
                "branch_id": row["topology_binding"]["branch_id"],
                "route_order_index": row["topology_binding"]["route_order_index"],
            }
        )
    phase_d_receipt = {
        "schema_version": "driveclarify.phase_d.white_van_lifecycle.v2",
        "status": STATUS,
        "full_pass": False,
        "scientific_partial": True,
        "bounded_run_ids": ["D0", "D1", "D2", "D2-R1"],
        "valid_scientific_run_ids": ["D1", "D2", "D2-R1"],
        "invalid_engineering_run_ids": ["D0"],
        "authoritative_run_id": "D2-R1",
        "raw_instruction": live["raw_instruction"],
        "grounding": {
            "raw_k": live["grounding"]["raw_grounding_k"],
            "plausible_k": live["grounding"]["plausible_k"],
            "effective_k": live["grounding"]["effective_k"],
            "dino_forward_count": live["grounding"]["detector_forward_count"],
            "privileged_state_read_count": live["grounding"][
                "privileged_state_read_count"
            ],
        },
        "candidates": candidate_summary,
        "moving_lifecycle": {
            "source_frame_first": decisions[0]["source_frame_id"],
            "source_frame_last": decisions[-1]["source_frame_id"],
            "distinct_frames": len({row["source_frame_id"] for row in decisions}),
            "distinct_observations": len(
                {row["source_observation_id"] for row in decisions}
            ),
            "distinct_bundles": len({row["bundle_id"] for row in decisions}),
            "distinct_windows": len(
                {row["decision_window_digest"] for row in decisions}
            ),
            "distinct_candidate_plan_references": len(set(plan_refs)),
            "progress_first_m_route": decisions[0]["current_progress_m"],
            "progress_last_m_route": decisions[-1]["current_progress_m"],
            "route_completion_percent": route["scores"]["score_route"],
            "refresh_complete_count": event_counts["REFRESH_BUNDLE_COMPLETE_VALID"],
            "evidence_expiry_count": event_counts["EVIDENCE_REFRESH_DEADLINE_EXPIRED"],
        },
        "natural_policy": {
            "decision_counts": dict(decision_counts),
            "semantic_state_final": episode["semantic_state"],
            "ask_observed": False,
            "wait_observed": False,
            "act_shared_observed": False,
            "answer_injected": False,
            "resolved_candidate": None,
            "material_divergence_cycle": divergent_rows[0],
        },
        "authority": authority,
        "collisions": {
            "count": len(collisions),
            "details": collisions,
            "control_source": "baseline_pid",
            "scientific_outcome_retained": True,
        },
        "native": {
            "raw_native_status": d2r1["native"]["status"],
            "reconciliation": "PHASE_D_D2_R1_NATIVE_RECEIPT_RECONCILIATION.json",
            "desktop_validation": d2r1["desktop"]["status"],
            "desktop_runtime_binding": "NATIVE_DESKTOP_RUNTIME_BINDING.json",
            "cleanup": d2r1["cleanup"]["status"],
        },
        "partial_rationale": (
            "The frozen natural policy legitimately returned FALLBACK throughout. Moving "
            "semantic persistence and fresh reevaluation were validated, but authorization-"
            "grade current equivalence, ACT_SHARED, ASK/WAIT, and resolution were not reached."
        ),
        "independent_review_verdict": STATUS,
    }
    write_json(REPORT / "PHASE_D_WHITE_VAN_LIFECYCLE_RECEIPT.json", phase_d_receipt)

    write_text(
        REPORT / "PHASE_D_WHITE_VAN_LIFECYCLE_REPORT.md",
        "# Phase D native white-van lifecycle — final\n\n"
        f"Status: `{STATUS}`\n\n"
        "D2-R1 is the authoritative third/final valid scientific run. Grounding DINO "
        "produced raw/plausible/effective K=2/2/2 for `Turn after the white van.` and "
        "bound the visually nearer/farther vans to the first/second route-ordered right-turn "
        "targets. The run moved from approximately 0 to 75.418822 m_route and completed "
        "100% of the route.\n\n"
        "Across 219 distinct frames, observations, bundles and decision windows, the store "
        "recorded 219 complete fresh bundles and 218 expiry-to-refresh transitions. All 438 "
        "candidate plan references were distinct. Every natural decision was FALLBACK and the "
        "semantic episode remained UNRESOLVED. One cycle was CURRENTLY_DIVERGENT with full "
        "coverage, but current-action relation, recoverability and deadline were UNKNOWN, so "
        "ASK was not legal. No ACT_SHARED, ASK, WAIT, answer or resolution was manufactured.\n\n"
        "The two vehicle collisions and one red-light infraction are retained scientific "
        "baseline outcomes: all 219 controls came from baseline_pid and method authority "
        "receipts/windows/M3 transactions were zero. D2-R1's raw native BLOCKED label is "
        "preserved and reconciled only as a passive desktop-status bookkeeping false negative; "
        "the same-session screenshot and native validation are PASS, evaluator return code is "
        "0, route completion is 100%, and cleanup is PASS.\n\n"
        "This validates moving persistent ambiguity and fresh reevaluation, but not query or "
        "shared-action closure. Therefore FULL PASS is prohibited and the independent reviewer "
        "accepted the scientific partial token.\n",
    )

    protected = protected_audit(runs)
    write_json(REPORT / "PROTECTED_STATE_FINAL_AUDIT.json", protected)

    phase_b = load(REPORT / "PHASE_B_FINAL_EVIDENCE_RECEIPT.json")
    b1r4 = REPORT / "B1-R4"
    b_window = load(b1r4 / "DECISION_WINDOW_EVIDENCE.json")
    b_results = b_window["results"]
    b_corridor = load(b1r4 / "SHARED_CORRIDOR_EVIDENCE.json")
    b_commit = load(b1r4 / "COMMITMENT_BOUNDARY_EVIDENCE.json")
    b_ttd = load(b1r4 / "TIME_TO_DIVERGENCE_EVIDENCE.json")
    b_timing = load(b1r4 / "TIMING_EVIDENCE.json")
    phase_c = load(REPORT / "PHASE_C_TEST_RECEIPT.json")
    retry = load(REPORT / "RETRY_LEDGER.json")
    repair_count = sum(
        1
        for row in (REPORT / "AUTONOMOUS_REPAIR_LOG.md").read_text(
            encoding="utf-8"
        ).splitlines()
        if row.startswith("## REPAIR-")
    )
    assert repair_count == 12
    assert len(retry["entries"]) == 11

    answers = [
        answer(1, "Final status", STATUS, "INDEPENDENT_REVIEW.md"),
        answer(2, "Total automatic repair count", repair_count, "AUTONOMOUS_REPAIR_LOG.md"),
        answer(3, "Total retry ledger entry count", len(retry["entries"]), "RETRY_LEDGER.json"),
        answer(4, "Transform root cause", "carla.Transform.transform is a callable method and was consumed before .location, causing all route coordinates to be discarded."),
        answer(5, "Route detacher files changed", ["driveclarify_phase_b_evidence_completion_v1/runtime.py", "driveclarify_phase_b_evidence_completion_v1/analysis.py", "tools/run_phase_b_evidence_completion_v1.py", "tests/persistent_ambiguity_runtime_v1/test_phase_b_evidence_completion_runtime.py"]),
        answer(6, "Route detacher regression", "PASS T1-T5; callable coordinates=0; invalid interpretations=0", "ROUTE_DETACHER_REGRESSION_RECEIPT.json"),
        answer(7, "Dashboard stale-run-ID fix", "PASS; dashboard run_id is rebound to the current runtime episode and never inherited from B0-R1."),
        answer(8, "Fresh Phase B runs", ["B1-R1", "B1-R2", "B1-R3", "B1-R4", "B2"]),
        answer(9, "Accepted valid Phase B runs", phase_b["accepted_run_ids"]),
        answer(10, "Phase B collisions per run", {"B1-R1": 0, "B1-R2": 1, "B1-R3": 1, "B1-R4": 0, "B2": 0}),
        answer(11, "Route rows", 88),
        answer(12, "Topology opportunities", {"enumerated_route_right_turn_opportunities": 3, "candidate_bound_opportunities": 2}),
        answer(13, "Coordinate calibration", {"status": "AVAILABLE", "uncertainty_m": 0.3969352485356619, "repeat_uncertainty_m": 0.3942438062637364, "frame": "CARLA_WORLD_TO_ROUTE_DIRECTED_PROGRESS"}),
        answer(14, "Ego progress", {"value": 0.000007665448880537987, "unit": "m_route"}),
        answer(15, "Target A progress", {"value": 18.72, "unit": "m_route"}),
        answer(16, "Target B progress", {"value": 57.05, "unit": "m_route"}),
        answer(17, "Shared corridor", b_corridor["value"]),
        answer(18, "Maneuver onset", {"value": 28.22, "unit": "m_route"}),
        answer(19, "Plan coverage", {key: value["value"]["coverage_status"] for key, value in b_window["plan_coverage"].items()}),
        answer(20, "Decision point", b_results["decision_point"]["value"]),
        answer(21, "Commitment A", b_commit["candidate_results"]["A"]["value"]),
        answer(22, "Commitment B", b_commit["candidate_results"]["B"]["value"]),
        answer(23, "Recoverability A", "RECOVERABLE"),
        answer(24, "Recoverability B", "RECOVERABLE"),
        answer(25, "Time to divergence", b_ttd["value"]),
        answer(26, "Latest safe clarification", b_timing["latest_safe_clarification"]),
        answer(27, "Current action equivalence", b_results["current_action_relation"]["value"]),
        answer(28, "Consequence relationship", b_results["candidate_relationship"]["value"]),
        answer(29, "Phase B acceptance", phase_b["status"]),
        answer(30, "Phase C entered", True),
        answer(31, "Phase C implementation files", ["driveclarify_persistent_ambiguity_runtime_v1/runtime.py", "driveclarify_persistent_ambiguity_runtime_v1/runtime_evaluator.py", "driveclarify_persistent_ambiguity_runtime_v1/scheduler.py", "driveclarify_persistent_ambiguity_runtime_v1/m2b_adapter.py", "driveclarify_persistent_ambiguity_runtime_v1/store.py", "driveclarify_m3_runtime_shadow/shared_act_commit_v1.py", "tests/persistent_ambiguity_runtime_v1/test_g1_runtime_integration.py", "tests/persistent_ambiguity_runtime_v1/test_g2_physical_lifecycle.py"]),
        answer(32, "Default OFF", True),
        answer(33, "ACT_SHARED implementation", "IMPLEMENTED_AND_UNIT_VALIDATED_BUT_NOT_NATURALLY_OBSERVED_IN_PHASE_D"),
        answer(34, "ACT_SHARED != RESOLVED", "PASS_UNIT_AND_INTEGRATION_CONTRACT; semantic state remains UNRESOLVED and all obligations are retained."),
        answer(35, "Persistent ambiguity retained", True),
        answer(36, "Stale invalidation", "PASS; action and no-authority windows revoke/expire to REFRESH_REQUIRED."),
        answer(37, "Refresh semantics", "new observation -> old bundle stale -> fresh 1+K bundle -> fresh window -> natural decision"),
        answer(38, "Hidden forward result", "PASS_NONE; candidate forwards only at normal planning events; visualization extra=0"),
        answer(39, "G0/G1/G2 tests", phase_c["frozen_progression"]),
        answer(40, "Final affected regression", {"passed": 396, "failed": 0}),
        answer(41, "Phase C review", phase_c["independent_review_status"]),
        answer(42, "Phase D entered", True),
        answer(43, "Phase D valid runs", {"count": 3, "run_ids": ["D1", "D2", "D2-R1"], "invalid_engineering": ["D0"]}),
        answer(44, "Raw white-van instruction", live["raw_instruction"]),
        answer(45, "Raw/effective K", {"raw": 2, "plausible": 2, "effective": 2}),
        answer(46, "Candidate A", candidate_summary[0]),
        answer(47, "Candidate B", candidate_summary[1]),
        answer(48, "Target A", {key: candidate_summary[0][key] for key in ("target_id", "junction_id", "branch_id", "route_order_index")}),
        answer(49, "Target B", {key: candidate_summary[1][key] for key in ("target_id", "junction_id", "branch_id", "route_order_index")}),
        answer(50, "First valid relation", decisions[0]["candidate_relationship"]),
        answer(51, "Natural first decision", decisions[0]["decision"]),
        answer(52, "ACT_SHARED observed", False),
        answer(53, "Episode remained unresolved", True),
        answer(54, "Planning refresh count", {"complete_fresh_bundles": 219, "expiry_to_refresh": 218}),
        answer(55, "Later relation sequence", [{"relation": "UNKNOWN_OR_INSUFFICIENT_EVIDENCE", "count": 66}, {"relation": "CURRENTLY_DIVERGENT", "count": 1, "sequence": 67}, {"relation": "UNKNOWN_OR_INSUFFICIENT_EVIDENCE", "count": 152}]),
        answer(56, "ASK naturally observed", False),
        answer(57, "WAIT naturally observed", False),
        answer(58, "Answer injected", False),
        answer(59, "Answer latency", None),
        answer(60, "Stale plan invalidated", {"candidate_evidence_expiry_events": 218, "answer_path": "NOT_ENTERED"}),
        answer(61, "Fresh replan", {"normal_plans": 219, "fresh_candidate_bundles": 219, "distinct_candidate_plan_references": 438}),
        answer(62, "Final resolved candidate", None),
        answer(63, "Authority sequence", {"method": "NONE_x219", "control_source": "baseline_pid_x219"}, "PHASE_D_AUTHORITY_SEQUENCE.json"),
        answer(64, "Total DINO forwards", {"valid_scientific_runs": 3, "all_attempted_including_D0": 4}),
        answer(65, "Total baseline SimLingo forwards", {"valid_scientific_runs": 227, "all_attempted_including_D0": 448}),
        answer(66, "Candidate forwards", {"valid_scientific_runs": 454, "all_attempted_including_D0": 456}),
        answer(67, "Visualization extra forwards", 0),
        answer(68, "PID count", {"new": 0, "existing_pid_invocations_valid_runs": 227}),
        answer(69, "VehicleControl writer count", {"new": 0, "direct_method_writes": 0}),
        answer(70, "Planner count", {"new": 0}),
        answer(71, "Privileged/gold reads", 0),
        answer(72, "Collision count", {"valid_phase_d_total": 2, "D1": 0, "D2": 0, "D2-R1": 2, "invalid_D0": 2}),
        answer(73, "Route completion", {"D1": 0.0, "D2": 0.0, "D2-R1": 100, "invalid_D0": 100}),
        answer(74, "Scientific vs engineering failures", {"engineering": ["route detacher/dashboard adapters", "D0 null dashboard serialization", "D1 timeline/DINO display", "D2 weak lifecycle stop predicate", "D2-R1 passive desktop-status bookkeeping"], "scientific": ["D1/D2/D2-R1 natural FALLBACK", "D2-R1 collisions and red-light under baseline_pid"]}),
        answer(75, "E3 preservation", protected["e3"]),
        answer(76, "DEV attempts", 0),
        answer(77, "TEST attempts/consumed", {"attempts": 0, "consumed": False}),
        answer(78, "SimLingo modification count", 0),
        answer(79, "Cleanup", {**protected["cleanup"], "ports_free": protected["ports_free"], "processes": protected["matching_processes"], "gpu_compute_apps": protected["gpu_compute_apps"]}),
        answer(80, "Artifact hashes", {"manifest": "ARTIFACT_HASHES.json", "algorithm": "SHA256", "self_excluded": True}),
        answer(81, "Single recommended next action", NEXT_ACTION),
    ]
    assert [row["number"] for row in answers] == list(range(1, 82))
    final_receipt = {
        "schema_version": "driveclarify.persistent_ambiguity_runtime_v1.final_receipt.v1",
        "status": STATUS,
        "full_pass": False,
        "scientific_partial": True,
        "answers": answers,
        "single_recommended_next_action": NEXT_ACTION,
        "stop_boundary": "DO_NOT_ENTER_E3_TRAIN_DEV_OR_TEST_WITHOUT_NEW_USER_AUTHORIZATION",
    }
    write_json(REPORT / "FINAL_RECEIPT.json", final_receipt)

    write_text(
        REPORT / "FINAL_REPORT.md",
        "# DriveClarify persistent ambiguity runtime V1 — final report\n\n"
        f"Final status: `{STATUS}`\n\n"
        "Phase B passed with two accepted collision-free native runs (`B1-R4`, `B2`) "
        "and complete frozen physical decision-window evidence. Phase C passed the exact "
        "56-test progression, broad regressions, adversarial coverage attacks, and fresh "
        "independent review with the feature default OFF and no new authority path.\n\n"
        "Phase D's authoritative D2-R1 run grounded two real white-van interpretations, "
        "moved through 219 fresh cycles from ~0 to 75.418822 m_route, and completed the route. "
        "The episode stayed UNRESOLVED and every natural decision was FALLBACK. No ACT_SHARED, "
        "ASK, WAIT, answer, or resolution occurred. One cycle showed candidate-level material "
        "divergence, but current equivalence, recoverability, and deadline were not authorizable. "
        "This validates moving persistence and fresh reevaluation but not shared-action/query "
        "closure, so FULL PASS is not claimed.\n\n"
        "The two collisions and red-light infraction are retained scientific baseline outcomes. "
        "Method authority remained zero and baseline_pid supplied every control. The raw D2-R1 "
        "native BLOCKED label is preserved as a passive desktop-status bookkeeping false negative; "
        "same-session desktop validation, route completion, evaluator return code, and cleanup are "
        "independently reconciled without rewriting raw evidence.\n\n"
        "Protected E3/DEV/TEST and SimLingo state remain exact. No training or protected "
        "evaluation was entered.\n\n"
        f"Single recommended next action: `{NEXT_ACTION}` (await explicit authorization).\n",
    )

    write_text(
        REPORT / "COMMAND_LOG.md",
        "# Command log\n\n"
        "This is a concise reproducibility log; raw evaluator commands and environment keys are "
        "also preserved in each run's `GROUNDED_LANGUAGE_V1_LAUNCH_CONTRACT.json`.\n\n"
        "- Read all project handoff, frozen design, G0, prior B0/B1 failure, and user contract files.\n"
        "- Searched CARLA Transform/Location/Waypoint and route/topology adapters with `rg`; repaired callable `.transform` detachment and current-run dashboard binding.\n"
        "- Ran offline route-detacher T1-T5 and affected regressions; final route rows=88 and topology opportunities=3 (2 candidate-bound).\n"
        "- Executed append-only Phase B runs B1-R1, B1-R2, B1-R3, B1-R4 and unchanged repeat B2. Accepted B1-R4/B2; collision sequence 0/1/1/0/0.\n"
        "- Implemented Phase C persistent store, scheduler, directed physical evaluator, M2B/authority integration, stale invalidation and refresh; repaired all independent-review findings.\n"
        "- Ran frozen G0/G1/G2 56/56, package 90/90, independent 253/253, and final affected 396/396.\n"
        "- The first final focused pytest invocation was blocked before collection by an unrelated auto-loaded ROS plugin missing `lark`; reran with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, as recorded, and obtained 17/17 then 396/396 PASS.\n"
        "- Executed Phase D D0 (invalid engineering), D1 and D2 (valid four-frame startup captures), then repaired the independent-review early-stop finding.\n"
        "- Preflighted protected hashes/ports/processes and executed final D2-R1 with `--timeout-seconds 600`; it naturally completed route 100% with 219 FALLBACK cycles, 2 retained collisions, evaluator rc=0, and cleanup PASS.\n"
        "- Captured and validated the native desktop in the same live D2-R1 session after observing a passive capture-gate bookkeeping omission; preserved and reconciled the raw false-negative receipt without a fourth scientific run.\n"
        "- Re-ran compile/focused/affected tests, performed two fresh independent Phase D reviews, regenerated final Phase D/81-answer/protected/hash artifacts, and stopped before E3/DEV/TEST.\n",
    )

    reviewer_attack = "# Final reviewer attack\n\n"
    reviewer_attack += "## A — Threshold chasing?\n\nNo. `RETRY_LEDGER.json` has 11 attributable engineering/reporting entries; every entry records `threshold_changed=false`, `fixture_changed=false`, and `scientific_contract_changed=false`. Natural FALLBACK/collisions were retained.\n\n"
    reviewer_attack += "## B — Why were Phase B collisions not method failures?\n\nB1-R2's collision is retained. B1-R3's post-terminal collision exposed a runner desktop/termination overrun. Both occurred under baseline authority before Phase C/D method authority; neither is relabelled. Accepted B1-R4/B2 each had collision 0.\n\n"
    reviewer_attack += "## C — Is D2-R1 a real scientific outcome?\n\nYes. It completed route 100% with evaluator rc=0, 219 moving cycles and cleanup PASS. All controls were baseline_pid, method authority was zero, and the two collisions/red light remain in the leaderboard record. The raw native false-negative is preserved and separately reconciled.\n\n"
    reviewer_attack += "## D — Was persistence an old-plan cache?\n\nNo. There are 219 distinct observations, frames, bundles and windows, 218 expiry transitions, and 438/438 distinct candidate plan references.\n\n"
    reviewer_attack += "## E — Hidden forwards?\n\nNo. D2-R1 accounting is normal/candidate=219/438 at exactly one 1+K bundle per normal event; visualization extra=0. Valid-run totals are DINO/normal/candidate=3/227/454.\n\n"
    reviewer_attack += "## F — Privileged truth used for control?\n\nNo. Grounding privileged reads and gold/expected-label reads are zero. CARLA map topology is evaluator evidence; it never selected candidate control.\n\n"
    reviewer_attack += "## G — Why is this only partial?\n\nAll 219 decisions were FALLBACK. No authorization-grade current equivalence, ACT_SHARED, ASK, WAIT or resolution occurred. The one CURRENTLY_DIVERGENT cycle still had recoverability/deadline/current action UNKNOWN. Moving persistence was validated; query closure was not.\n\n"
    reviewer_attack += "## H — Protected state?\n\nE3 remains 216/111/110/slot111/105, DEV=0, TEST=0/unconsumed; SimLingo HEAD/diff/checkpoint hashes are exact and all Phase D cleanups/ports/process/GPU checks pass.\n"
    write_text(REPORT / "REVIEWER_ATTACK.md", reviewer_attack)

    required_hash_paths = [
        REPORT / name
        for name in (
            "AUTONOMOUS_REPAIR_LOG.md",
            "RETRY_LEDGER.json",
            "ROUTE_DETACHER_FIX_REPORT.md",
            "ROUTE_DETACHER_REGRESSION_RECEIPT.json",
            "PHASE_B_FINAL_EVIDENCE_REPORT.md",
            "PHASE_B_FINAL_EVIDENCE_RECEIPT.json",
            "AUTHORITY_MUTATION_AUDIT.json",
            "COORDINATE_CALIBRATION.json",
            "COORDINATE_FRAME_VALIDATION.json",
            "DECISION_WINDOW_EVIDENCE.json",
            "FORWARD_ACCOUNTING.json",
            "PHASE_B_EVIDENCE_RECEIPT.json",
            "PRIVILEGED_READ_AUDIT.json",
            "ROUTE_PROGRESS_EVIDENCE.json",
            "SHARED_CORRIDOR_EVIDENCE.json",
            "MANEUVER_ONSET_EVIDENCE.json",
            "PLAN_COVERAGE_EVIDENCE.json",
            "DECISION_POINT_EVIDENCE.json",
            "COMMITMENT_BOUNDARY_EVIDENCE.json",
            "RECOVERABILITY_EVIDENCE.json",
            "TIME_TO_DIVERGENCE_EVIDENCE.json",
            "TIMING_EVIDENCE.json",
            "LATEST_SAFE_CLARIFICATION_EVIDENCE.json",
            "PHASE_C_IMPLEMENTATION_REPORT.md",
            "PHASE_C_TEST_RECEIPT.json",
            "PHASE_C_INDEPENDENT_REVIEW.md",
            "PHASE_D_WHITE_VAN_LIFECYCLE_RECEIPT.json",
            "PHASE_D_WHITE_VAN_LIFECYCLE_REPORT.md",
            "PHASE_D_TIMELINE.json",
            "PHASE_D_AUTHORITY_SEQUENCE.json",
            "PHASE_D_FORWARD_ACCOUNTING.json",
            "PHASE_D_D2_R1_NATIVE_RECEIPT_RECONCILIATION.json",
            "REVIEWER_ATTACK.md",
            "INDEPENDENT_REVIEW.md",
            "PROTECTED_STATE_FINAL_AUDIT.json",
            "FINAL_RECEIPT.json",
            "FINAL_REPORT.md",
            "COMMAND_LOG.md",
        )
    ] + [
        PHASE_D / "D2-R1" / name
        for name in (
            "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json",
            "GROUNDED_LANGUAGE_V1_NATIVE_RUN_RECEIPT.json",
            "GROUNDED_LANGUAGE_V1_CLEANUP_RECEIPT.json",
            "leaderboard_results.json",
            "NATIVE_DESKTOP.png",
            "NATIVE_DESKTOP_VALIDATION.json",
            "NATIVE_DESKTOP_RUNTIME_BINDING.json",
        )
    ]
    missing = [str(path) for path in required_hash_paths if not path.is_file()]
    assert not missing, missing
    manifest = {
        "schema_version": "driveclarify.persistent_ambiguity_runtime_v1.artifact_hashes.v1",
        "status": "PASS_ALL_REQUIRED_FINAL_ARTIFACTS_PRESENT_AND_HASHED",
        "algorithm": "SHA256",
        "manifest_self_excluded": True,
        "files": {
            str(path.relative_to(ROOT)): {
                "sha256": sha(path),
                "size_bytes": path.stat().st_size,
            }
            for path in sorted(required_hash_paths)
        },
    }
    write_json(REPORT / "ARTIFACT_HASHES.json", manifest)

    # Final structural and semantic consistency checks.
    assert load(REPORT / "FINAL_RECEIPT.json")["status"] == STATUS
    assert load(REPORT / "PHASE_D_WHITE_VAN_LIFECYCLE_RECEIPT.json")[
        "status"
    ] == STATUS
    assert load(REPORT / "PROTECTED_STATE_FINAL_AUDIT.json")["status"].startswith(
        "PASS_"
    )
    assert len(load(REPORT / "FINAL_RECEIPT.json")["answers"]) == 81
    assert load(REPORT / "ARTIFACT_HASHES.json")["status"].startswith("PASS_")


if __name__ == "__main__":
    main()
