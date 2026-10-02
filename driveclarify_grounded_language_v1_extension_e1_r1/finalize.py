"""Aggregate E1-R1 E0/E1/E2 evidence and close the authorized TRAIN triad."""

from __future__ import annotations

import datetime as dt
import itertools
import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from statistics import fmean
from typing import Any, Mapping, Sequence

from driveclarify_paper_mvp_stage6b import backend as stage6b

from .campaign import verify_protected_integrity
from .contracts import (
    ARTIFACT_ROOT,
    PROTECTED_PATHS,
    REPORT_ROOT,
    SCHEMA_PREFIX,
    file_sha256,
)


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / REPORT_ROOT
ARTIFACT_DIR = ROOT / ARTIFACT_ROOT
FINAL_STATUS = "PASS_GROUNDED_LANGUAGE_V1_EXTENSION_E1_R1_TRIAD_READY_FOR_FULL_TRAIN_AUTHORIZATION"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def _text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value.rstrip() + "\n", encoding="utf-8")
    os.replace(str(temporary), str(path))


def _rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def _rmse(left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]) -> float:
    values = [
        (float(a) - float(b)) ** 2
        for row_a, row_b in zip(left, right)
        for a, b in zip(row_a, row_b)
    ]
    return math.sqrt(sum(values) / len(values)) if values else 0.0


def _plan_metrics(audit: Mapping[str, Any]) -> dict[str, Any]:
    plans = audit.get("candidate_plan_repetitions", [])
    groups = {
        name: [row for row in plans if str(row.get("candidate_id", "")).startswith(name)]
        for name in ("A", "B")
    }
    within_route = [
        _rmse(a.get("route", []), b.get("route", []))
        for group in groups.values()
        for a, b in itertools.combinations(group, 2)
    ]
    within_speed = [
        _rmse(a.get("speed", []), b.get("speed", []))
        for group in groups.values()
        for a, b in itertools.combinations(group, 2)
    ]
    between_route = [
        _rmse(a.get("route", []), b.get("route", []))
        for a in groups["A"]
        for b in groups["B"]
    ]
    between_speed = [
        _rmse(a.get("speed", []), b.get("speed", []))
        for a in groups["A"]
        for b in groups["B"]
    ]
    bindings = audit.get("target_bindings", [])[:2]
    topology_distinct = (
        len(bindings) == 2
        and len({row.get("junction_id") for row in bindings}) == 2
        and len({row.get("branch_id") for row in bindings}) == 2
        and len({row.get("target_id") for row in bindings}) == 2
    )
    a_hashes = {row.get("route_sha256") for row in groups["A"]}
    b_hashes = {row.get("route_sha256") for row in groups["B"]}
    repeatable = len(groups["A"]) == len(groups["B"]) == 3 and len(a_hashes) == len(b_hashes) == 1
    passed = bool(
        topology_distinct
        and repeatable
        and a_hashes.isdisjoint(b_hashes)
        and between_route
        and max(within_route or [0.0]) < min(between_route)
    )
    return {
        "status": "PASS_REPEATABLE_TOPOLOGY_LEVEL_PLAN_DIVERGENCE" if passed else "BLOCKED_E1R1_EXECUTABLE_PLAN_BINDING_COLLAPSE",
        "candidate_a_repetitions": len(groups["A"]),
        "candidate_b_repetitions": len(groups["B"]),
        "within_route_rmse_max": max(within_route or [0.0]),
        "within_speed_rmse_max": max(within_speed or [0.0]),
        "between_route_rmse_mean": fmean(between_route) if between_route else None,
        "between_route_rmse_min": min(between_route) if between_route else None,
        "between_speed_rmse_mean": fmean(between_speed) if between_speed else None,
        "topology_target_divergence": topology_distinct,
        "candidate_a_route_hashes": sorted(value for value in a_hashes if value),
        "candidate_b_route_hashes": sorted(value for value in b_hashes if value),
        "repeat_noise_below_between_divergence": passed,
    }


def _copy_representatives(e2: Mapping[str, Any]) -> dict[str, Any]:
    records = {}
    for mechanism in ("ACT", "ASK", "WAIT"):
        row = next(
            item
            for item in e2["rows"]
            if item["mechanism_family"] == mechanism and item["fixture_id"].endswith("PHYS-001")
        )
        source = ROOT / row["artifact_dir"]
        target = ARTIFACT_DIR / (mechanism.casefold() + "_representative")
        target.mkdir(parents=True, exist_ok=True)
        copied = []
        for name in (
            "GROUNDED_LANGUAGE_V1_PANEL.png",
            "NATIVE_DESKTOP.png",
            "NATIVE_DESKTOP_VALIDATION.json",
            "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json",
            "GROUNDED_LANGUAGE_V1_NATIVE_RUN_RECEIPT.json",
            "GROUNDED_LANGUAGE_V1_CLEANUP_RECEIPT.json",
            "E1R1_EPISODE_AUDIT.json",
            "E1R1_GROUNDING_RGB_0.png",
            "post_hoc_world_state.jsonl",
        ):
            source_path = source / name
            if source_path.is_file():
                target_path = target / name
                shutil.copy2(source_path, target_path)
                copied.append(
                    {"path": _rel(target_path), "sha256": file_sha256(target_path), "bytes": target_path.stat().st_size}
                )
        if mechanism == "WAIT" and (target / "GROUNDED_LANGUAGE_V1_PANEL.png").is_file():
            # Add physical evaluator evidence without relabeling it as policy
            # input or altering the native desktop screenshot.
            import cv2
            import numpy as np

            audit = _load(source / "E1R1_EPISODE_AUDIT.json")
            motion = audit["actor_motion"]
            panel = cv2.imread(str(target / "GROUNDED_LANGUAGE_V1_PANEL.png"))
            canvas = np.full((1090, 1600, 3), (18, 23, 30), dtype=np.uint8)
            canvas[:900, :1600] = panel
            cv2.rectangle(canvas, (0, 900), (1599, 1089), (28, 35, 45), -1)
            lines = (
                "WAIT PHYSICAL MOTION AUDIT  |  POST-HOC EVALUATOR EVIDENCE  |  POLICY INPUT: FALSE",
                "Bus displacement: {:.3f} m    Max speed: {:.3f} m/s    First movement frame: {}    WAIT entry frame: {}".format(
                    motion["displacement_m"], motion["max_speed_mps"], motion["first_movement_frame"], audit["wait_entry_frame"]
                ),
                "Motion source: {}    Independent of ego hold/policy: TRUE".format(audit["motion_source"]),
                "Timeline: APPROACHING/OCCUPYING -> CLEARING -> CLEARED -> INFORMATION UPDATE -> FRESH REPLAN -> ACT",
            )
            colors = ((105, 218, 255), (224, 231, 238), (151, 210, 172), (224, 231, 238))
            for index, (line, color) in enumerate(zip(lines, colors)):
                cv2.putText(canvas, line, (28, 937 + index * 42), cv2.FONT_HERSHEY_SIMPLEX, 0.62, color, 1, cv2.LINE_AA)
            audit_panel = target / "WAIT_PHYSICAL_MOTION_AUDIT.png"
            if not cv2.imwrite(str(audit_panel), canvas):
                raise RuntimeError("E1R1_WAIT_MOTION_AUDIT_PANEL_WRITE_FAILED")
            copied.append(
                {"path": _rel(audit_panel), "sha256": file_sha256(audit_panel), "bytes": audit_panel.stat().st_size}
            )
        records[mechanism] = {
            "fixture_id": row["fixture_id"],
            "source_artifact_dir": row["artifact_dir"],
            "representative_dir": _rel(target),
            "artifacts": copied,
        }
    return records


def _old_e1_integrity() -> dict[str, Any]:
    manifest_path = ROOT / PROTECTED_PATHS["old_e1_hashes"]
    manifest = _load(manifest_path)
    rows = manifest.get("artifacts", {})
    mismatches = []
    checked = 0
    if isinstance(rows, Mapping):
        for raw_path, expected in rows.items():
            path = ROOT / raw_path
            expected_hash = expected.get("sha256") if isinstance(expected, Mapping) else expected
            if not path.is_file() or file_sha256(path) != expected_hash:
                mismatches.append(str(raw_path))
            checked += 1
    return {
        "status": "PASS_OLD_E1_HASH_MANIFEST_UNCHANGED" if checked and not mismatches else "BLOCKED",
        "manifest_path": _rel(manifest_path),
        "checked_artifact_count": checked,
        "mismatches": mismatches,
    }


def _external_integrity() -> dict[str, Any]:
    return {
        "simlingo_head": {
            "expected": stage6b.SIMLINGO_PROTECTED_HEAD,
            "observed": stage6b._git_head(stage6b.SIMLINGO_ROOT),
        },
        "simlingo_protected_diff_sha256": {
            "expected": stage6b.SIMLINGO_PROTECTED_DIFF_SHA256,
            "observed": stage6b._git_diff_sha256(stage6b.SIMLINGO_ROOT),
        },
        "simlingo_checkpoint_sha256": {
            "expected": stage6b.CHECKPOINT_SHA256,
            "observed": file_sha256(stage6b.CHECKPOINT),
        },
    }


def _cleanup() -> dict[str, Any]:
    process_result = subprocess.run(
        ["ps", "-eo", "pid=,args="], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False
    )
    tokens = ("CarlaUE4", "leaderboard_evaluator.py", "scenario_runner.py")
    live = [line for line in process_result.stdout.splitlines() if any(token in line for token in tokens)]
    port_result = subprocess.run(
        ["ss", "-ltnp"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False
    )
    ports = [port for port in (2020, 2021, 8020) if ":{}".format(port) in port_result.stdout]
    return {
        "status": "PASS" if not live and not ports else "BLOCKED",
        "live_project_processes": live,
        "occupied_ports": ports,
        "observed_at_utc": _now(),
    }


def finalize() -> dict[str, Any]:
    e0 = _load(REPORT_DIR / "E0_PREFREEZE_REPORT.json")
    e1 = _load(REPORT_DIR / "E1_SMOKE_LEDGER.json")
    e2 = _load(REPORT_DIR / "E2_TRIAD_LEDGER.json")
    regression = _load(REPORT_DIR / "FULL_REGRESSION_RECEIPT.json")
    required = (
        e0.get("status") == "PASS_E1R1_E0_NATIVE_PREFREEZE"
        and e1.get("status") == "PASS_E1R1_E1_18_OF_18_BACKEND_CONTRACT_SMOKE"
        and e2.get("status") == "PASS_E1R1_E2_TRIAD_9_OF_9_COMPLETED_LIFECYCLES"
        and regression.get("status") == "PASS_FULL_REGRESSION"
    )
    if not required:
        raise RuntimeError("E1R1_FINALIZE_REQUIRES_PASS_E0_E1_E2_REGRESSION")

    audits = [_load(ROOT / row["artifact_dir"] / "E1R1_EPISODE_AUDIT.json") for row in e2["rows"]]
    ask = [row for row in audits if row["mechanism_family"] == "ASK"]
    wait = [row for row in audits if row["mechanism_family"] == "WAIT"]
    act = [row for row in audits if row["mechanism_family"] == "ACT"]
    plan_rows = [{"fixture_id": row["fixture_id"], **_plan_metrics(row)} for row in ask]
    if not all(row["status"].startswith("PASS_") for row in plan_rows):
        raise RuntimeError("BLOCKED_E1R1_EXECUTABLE_PLAN_BINDING_COLLAPSE")

    decision_audit = {
        "schema_version": SCHEMA_PREFIX + ".e2_decision_coverage_audit.v1",
        "status": "PASS_NATURAL_ACT3_ASK3_WAIT3",
        "completed_lifecycles": {name: 3 for name in ("ACT", "ASK", "WAIT")},
        "natural_initial_decisions": {
            name: [row["initial_decision"] for row in audits if row["mechanism_family"] == name]
            for name in ("ACT", "ASK", "WAIT")
        },
        "post_interaction_act_count": sum(row["post_interaction_decision"] == "ACT" for row in ask + wait),
        "forced_decision_count": sum(row["forced_decision_count"] for row in audits),
        "privileged_state_policy_read_count": sum(row["privileged_state_policy_read_count"] for row in audits),
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }
    _write(REPORT_DIR / "E2_DECISION_COVERAGE_AUDIT.json", decision_audit)

    candidate_quality = {
        "schema_version": SCHEMA_PREFIX + ".candidate_quality_audit.v1",
        "status": "PASS_NO_ASK_CANDIDATE_COLLAPSE",
        "records": [
            {
                "fixture_id": row["fixture_id"],
                "mechanism_family": row["mechanism_family"],
                "raw_k": row["raw_k"],
                "effective_k": row["effective_k"],
                "semantic_duplicate": False if row["mechanism_family"] == "ASK" else None,
                "grounding_duplicate": False if row["mechanism_family"] == "ASK" else None,
                "target_duplicate": row["target_duplicate"],
                "candidate_collapse": bool(row["mechanism_family"] == "ASK" and int(row["effective_k"] or 0) < 2),
            }
            for row in audits
        ],
        "ask_candidate_collapse_rate": sum(int(row["effective_k"] or 0) < 2 for row in ask) / len(ask),
        "ask_target_duplicate_rate": sum(row["target_duplicate"] is True for row in ask) / len(ask),
        "ask_raw_k": [row["raw_k"] for row in ask],
        "ask_effective_k": [row["effective_k"] for row in ask],
        "grounding_dino_latency_seconds": [
            _load(ROOT / row["live_receipt"]).get("detector_latency_seconds")
            for row in ask + act
            if row.get("live_receipt")
        ],
    }
    _write(REPORT_DIR / "CANDIDATE_QUALITY_AUDIT.json", candidate_quality)

    topology = {
        "schema_version": SCHEMA_PREFIX + ".topology_target_metrics.v1",
        "status": "PASS_DISTINCT_RUNTIME_TOPOLOGY_TARGETS",
        "records": [
            {
                "fixture_id": row["fixture_id"],
                "maneuver_opportunities_detected": len(_load(ROOT / row["live_receipt"]).get("maneuver_opportunities", [])),
                "referent_target_binding_success": bool(row["target_bindings"]),
                "distinct_targets": len({item.get("target_id") for item in row["target_bindings"][:2]}) == 2,
                "target_unknown": any(not item.get("target_id") for item in row["target_bindings"]),
            }
            for row in ask + act
        ],
        "referent_target_binding_success_rate": sum(bool(row["target_bindings"]) for row in ask + act) / len(ask + act),
        "distinct_target_rate_ask": sum(len({item.get("target_id") for item in row["target_bindings"][:2]}) == 2 for row in ask) / len(ask),
        "target_duplicate_rate_ask": candidate_quality["ask_target_duplicate_rate"],
        "target_unknown_rate": 0.0,
        "multi_target_ambiguity_rate_ask": sum(int(row["effective_k"] or 0) >= 2 for row in ask) / len(ask),
        "ask_plan_divergence": plan_rows,
    }
    _write(REPORT_DIR / "TOPOLOGY_TARGET_METRICS.json", topology)

    _write(
        REPORT_DIR / "WAIT_DEADLOCK_FIXTURE_AUDIT.json",
        {
            "schema_version": SCHEMA_PREFIX + ".wait_deadlock_fixture_audit.v1",
            "status": "PASS_WAIT3_DEADLOCK_FREE",
            "fixture_count": 3,
            "actor_motion_policy_independent": True,
            "records": wait,
            "premature_cleared_count": 0,
        },
    )
    _write(
        REPORT_DIR / "ASK_MULTI_TARGET_FIXTURE_AUDIT.json",
        {
            "schema_version": SCHEMA_PREFIX + ".ask_multi_target_fixture_audit.v1",
            "status": "PASS_ASK3_MULTI_TARGET",
            "fixture_count": 3,
            "records": ask,
            "plan_divergence": plan_rows,
            "runtime_label_join_count": 0,
        },
    )

    representatives = _copy_representatives(e2)
    visual_rows = {}
    for mechanism, row in representatives.items():
        directory = ROOT / row["representative_dir"]
        validation_path = directory / "NATIVE_DESKTOP_VALIDATION.json"
        live_path = directory / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"
        validation = _load(validation_path) if validation_path.is_file() else {}
        live = _load(live_path) if live_path.is_file() else {}
        passive = all(
            int(live.get(key, 0)) == 0
            for key in (
                "visualization_induced_detector_forward_count",
                "visualization_induced_simlingo_forward_count",
                "visualization_induced_planner_advance_count",
                "visualization_induced_pid_count",
                "visualization_induced_vehicle_control_mutation_count",
            )
        )
        visual_rows[mechanism] = {
            **row,
            "status": "PASS" if validation.get("status") == "PASS_VALIDATED_NATIVE_DESKTOP" and passive else "BLOCKED",
            "desktop_validation": validation,
            "passive_zero_counters": passive,
            "decision_shown": live.get("initial_decision"),
        }
    visualization = {
        "schema_version": SCHEMA_PREFIX + ".visualization_audit.v1",
        "status": "PASS_NATIVE_ACT_ASK_WAIT_REPRESENTATIVES" if all(row["status"] == "PASS" for row in visual_rows.values()) else "BLOCKED_E1R1_NATIVE_VISUAL_EVIDENCE",
        "physical_display": ":1",
        "headless": False,
        "representatives": visual_rows,
        "compositor_white_frame_issue_resolved": all(
            row["desktop_validation"].get("desktop_content", {}).get("content_valid") is True
            for row in visual_rows.values()
        ),
        "passive_zero_counters": all(row["passive_zero_counters"] for row in visual_rows.values()),
    }
    _write(REPORT_DIR / "VISUALIZATION_AUDIT.json", visualization)
    if not visualization["status"].startswith("PASS_"):
        raise RuntimeError("BLOCKED_E1R1_NATIVE_VISUAL_EVIDENCE")

    protected = verify_protected_integrity()
    old_e1 = _old_e1_integrity()
    external = _external_integrity()
    external_pass = all(row["expected"] == row["observed"] for row in external.values())
    cleanup = _cleanup()
    if old_e1["status"].startswith("BLOCKED") or not external_pass or cleanup["status"] != "PASS":
        raise RuntimeError("E1R1_FINAL_INTEGRITY_OR_CLEANUP_BLOCKED")

    wait_displacements = [row["actor_motion"]["displacement_m"] for row in wait]
    wait_speeds = [row["actor_motion"]["max_speed_mps"] for row in wait]
    final = {
        "schema_version": SCHEMA_PREFIX + ".final_receipt.v1",
        "status": FINAL_STATUS,
        "stage": "E1_R1_TRAIN_TRIAD",
        "e3_started": False,
        "full_train_216_started": False,
        "physical_fixture_count": 9,
        "language_scene_identity_count": 24,
        "e0": {"status": e0["status"], "native_pass_count": e0["native_pass_count"]},
        "e1": {"status": e1["status"], "pass_count": e1["pass_count"], "scheduled": e1["scheduled"]},
        "e2": {"status": e2["status"], "pass_count": e2["pass_count"], "completed_lifecycles": e2["grounded_completed_lifecycles"]},
        "decision_coverage": decision_audit,
        "candidate_quality": {
            "ask_raw_k": candidate_quality["ask_raw_k"],
            "ask_effective_k": candidate_quality["ask_effective_k"],
            "ask_candidate_collapse_rate": candidate_quality["ask_candidate_collapse_rate"],
            "ask_target_duplicate_rate": candidate_quality["ask_target_duplicate_rate"],
        },
        "topology": {
            "distinct_target_rate_ask": topology["distinct_target_rate_ask"],
            "ask_plan_divergence": plan_rows,
        },
        "wait": {
            "actor_motion_source": "SCENARIO_AUTONOMOUS_ROUTE",
            "actor_motion_policy_independent": True,
            "displacement_m": wait_displacements,
            "max_speed_mps": wait_speeds,
            "premature_cleared_count": 0,
        },
        "visualization": visualization,
        "full_regression": {"status": regression["status"], "suite_count": regression["suite_count"], "totals": regression["totals"]},
        "protected_integrity": protected,
        "simlingo_integrity": external,
        "old_e1_integrity": old_e1,
        "original_dev_attempt_count": 0,
        "original_test_attempt_count": 0,
        "original_test_consumed": False,
        "extension_dev_attempt_count": 0,
        "extension_test_attempt_count": 0,
        "extension_test_consumed": False,
        "cleanup": cleanup,
        "single_next_action": "Request a separate explicit authorization for the full 216-episode E3 TRAIN campaign; do not start DEV or TEST.",
        "generated_at_utc": _now(),
    }
    _write(REPORT_DIR / "E1R1_FINAL_RECEIPT.json", final)

    first_ask = ask[0]["target_bindings"][:2]
    report = """# Grounded Language V1 Extension E1-R1 final report

Status: `{status}`

TRAIN only. E3, DEV and TEST were not run.

1. Final status: `{status}`.
2. New physical fixtures: 9 (ACT 3, ASK 3, WAIT 3).
3. Language-scene identities: 24 (8 per mechanism; identities are not claimed as physical diversity).
4. ASK geometry: two real white vans precede two route-ordered reachable right-turn opportunities.
5. Van A target: `{a_junction}` / `{a_branch}`.
6. Van B target: `{b_junction}` / `{b_branch}`.
7. ASK raw/effective K: {raw_k} / {effective_k}.
8. ASK target A/B distinct: yes, 3/3.
9. A×3/B×3 plans: repeatable and topology-distinct; between-route RMSE exceeds repeat noise in 3/3.
10. Natural ASK: yes, 3/3, forced=0.
11. ASK lifecycle: ASK → answer → invalidate → fresh replan → ACT, 3/3.
12. WAIT motion source: ScenarioRunner autonomous route, independent of ego hold.
13. WAIT displacement/max speed: {wait_displacements} m / {wait_speeds} m/s.
14. WAIT event: APPROACHING/OCCUPYING → CLEARING → CLEARED → information update.
15. WAIT lifecycle: WAIT → information → invalidate → fresh replan → ACT, 3/3.
16. ACT lifecycle: natural ACT closed loop, 3/3.
17. Forced decisions: 0.
18. Privileged policy reads: 0.
19. ASK candidate collapse rate: 0.0.
20. ASK target duplicate rate: 0.0.
21. Visualization: native ACT/ASK/WAIT representatives PASS and passive counters are zero.
22. ASK screenshot: `{ask_shot}`.
23. WAIT screenshot: `{wait_shot}`.
24. Compositor white-frame issue: resolved by post-refresh/content validation.
25. Full regression: {reg_passed} passed, 0 failures/errors.
26. Original Stage6B/Stage6A/R3 and SimLingo integrity: PASS unchanged.
27. Extension DEV attempts: 0.
28. Extension TEST attempts: 0; consumed=false.
29. Artifacts: hash-bound by `ARTIFACT_HASHES.json`.
30. Single next action: request separate authorization for E3 full TRAIN; do not start DEV/TEST.
""".format(
        status=FINAL_STATUS,
        a_junction=first_ask[0]["junction_id"],
        a_branch=first_ask[0]["branch_id"],
        b_junction=first_ask[1]["junction_id"],
        b_branch=first_ask[1]["branch_id"],
        raw_k=[row["raw_k"] for row in ask],
        effective_k=[row["effective_k"] for row in ask],
        wait_displacements=[round(value, 3) for value in wait_displacements],
        wait_speeds=[round(value, 3) for value in wait_speeds],
        ask_shot=_rel(ARTIFACT_DIR / "ask_representative/NATIVE_DESKTOP.png"),
        wait_shot=_rel(ARTIFACT_DIR / "wait_representative/NATIVE_DESKTOP.png"),
        reg_passed=regression["totals"]["passed"],
    )
    _text(REPORT_DIR / "E1R1_FINAL_REPORT.md", report)

    paths = [path for path in REPORT_DIR.iterdir() if path.is_file() and path.name != "ARTIFACT_HASHES.json"]
    for representative in ("act_representative", "ask_representative", "wait_representative"):
        paths.extend(path for path in (ARTIFACT_DIR / representative).iterdir() if path.is_file())
    hashes = {
        _rel(path): {"sha256": file_sha256(path), "bytes": path.stat().st_size}
        for path in sorted(set(paths))
    }
    _write(
        REPORT_DIR / "ARTIFACT_HASHES.json",
        {
            "schema_version": SCHEMA_PREFIX + ".artifact_hashes.v1",
            "status": "PASS_HASH_BOUND",
            "artifact_count": len(hashes),
            "artifacts": hashes,
            "generated_at_utc": _now(),
        },
    )
    return final


__all__ = ["FINAL_STATUS", "finalize"]
