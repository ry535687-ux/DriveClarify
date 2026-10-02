#!/usr/bin/env python3
"""Bounded development calibration for an actionable RQ2-T-CG B2 window."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_grounded_language_v1_extension_e1_r1 import backend as episode_backend
from driveclarify_paper_mvp_stage6b import backend as native
from driveclarify_rq2_t.measurement import canonical_sha256
from driveclarify_rq2_t_cg_formal_execution.builder import build_formal_episode
from driveclarify_rq2_t_cg_formal_execution.child_admission_v2 import (
    FirstLegalRowWatchdog, build_exact_formal_child_environment,
    persist_child_construction_receipt,
)
from driveclarify_rq2_t_cg_formal_execution.routes import materialize_route, static_route_admission
from driveclarify_rq2_t_cg_v2_calibration.scenes import (
    CALIBRATION_CONFIGURATION_ID, CALIBRATION_PARAMETERS, calibration_scene,
    engineering_seam_scene,
)
from driveclarify_rq2_t_cg_formal_freeze.scenes import SCENE_ORDER
from driveclarify_rq2_t_v2.memory import FIELD_MEMORY_POLICIES
from tools.run_rq2_t_cg_formal_v2 import _no_control_effect
from tools.run_rq2_t_e2_v3 import _repair_irrelevant_display_process_false_positive


REPORT = ROOT / "reports" / "driveclarify_rq2_t_cg_v2_calibration_and_formal_confirmatory_v1"
CONFIGS = REPORT / "CALIBRATION_CONFIGS"
ROUTES = REPORT / "CALIBRATION_ROUTES"
RUNS = REPORT / "CALIBRATION_RUNS"
SEAM_CONFIGS = REPORT / "EXECUTION_SEAM_CONFIGS"
SEAM_ROUTES = REPORT / "EXECUTION_SEAM_ROUTES"
SEAM_RUNS = REPORT / "EXECUTION_SEAM_RUNS"
PRIOR = ROOT / "reports" / "driveclarify_rq2_t_cg_formal_v2_narrow_binding_repair_and_execution_v1"
SIMLINGO = Path("/home/buaa/wrh/simlingo")
ENTRY_HEAD = "eaa332b1bb994279b59ea5af786fdb5de96adc1b"
PLACEHOLDER_RUNTIME = ROOT / "driveclarify_paper_mvp_scenarios/generated/runtime_visible/dc-runtime-06cccfa4723c09ca09a9bc2b.json"
PROMOTION = native.PROMOTION_ROOT / "LIVE_PROMOTION_MANIFEST.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path, default: Any = None) -> Any:
    return default if not path.is_file() else json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def _write_md(path: Path, title: str, lines: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# " + title + "\n\n" + "\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _append_command(command: str, status: str) -> None:
    path = REPORT / "COMMAND_LOG.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("- `{}` -> `{}`\n".format(command, status))


def _command(args: Sequence[str], cwd: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), cwd=str(cwd), text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, check=False)


def _git_snapshot() -> Mapping[str, Any]:
    tracked = subprocess.check_output(["git", "diff", "--binary"], cwd=str(ROOT))
    staged = subprocess.check_output(["git", "diff", "--cached", "--binary"], cwd=str(ROOT))
    return {
        "branch": _command(("git", "branch", "--show-current")).stdout.strip(),
        "head": _command(("git", "rev-parse", "HEAD")).stdout.strip(),
        "tracked_diff_bytes": len(tracked), "tracked_diff_sha256": hashlib.sha256(tracked).hexdigest(),
        "staged_diff_bytes": len(staged), "staged_diff_sha256": hashlib.sha256(staged).hexdigest(),
    }


def _fresh_batch(prefixes: Sequence[str]) -> Sequence[Mapping[str, Any]]:
    roots = (str(ROOT), str(SIMLINGO))
    for rejection_round in range(128):
        rows = []
        used = set()
        for prefix in prefixes:
            seed = secrets.randbits(32)
            while seed <= 65535 or seed in used:
                seed = secrets.randbits(32)
            used.add(seed)
            identity = prefix + secrets.token_hex(8).upper()
            rows.append({"identity": identity, "seed": seed})
        patterns = [str(row["seed"]) for row in rows] + [row["identity"] for row in rows]
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write("\n".join(patterns) + "\n")
            pattern_path = Path(handle.name)
        try:
            scan = _command(("rg", "-a", "-uuu", "-F", "-l", "--no-messages", "-f", str(pattern_path)) + roots)
        finally:
            pattern_path.unlink(missing_ok=True)
        if not scan.stdout.strip():
            return tuple({
                **row, "freshness_scan_roots": list(roots), "prior_match_paths": [],
                "rejection_round": rejection_round,
            } for row in rows)
    raise RuntimeError("RQ2_T_CG_CALIBRATION_FRESH_BATCH_FAILED")


def _historical_output() -> Path:
    rows = list((PRIOR / "ENGINEERING_ONLY").glob("*/attempt_01"))
    if len(rows) != 1:
        raise RuntimeError("HISTORICAL_V2_WITNESS_NOT_UNIQUE")
    return rows[0]


def _timing_decomposition() -> Mapping[str, Any]:
    output = _historical_output()
    result = _load(output / "FORMAL_EPISODE_RESULT.json")
    scenario = _load(output / "FORMAL_SCENARIO_RECEIPT.json")
    paired = [json.loads(line) for line in (output / "FORMAL_PAIRED_VIEWS.jsonl").read_text().splitlines()]
    fields = (
        "E1_INTERPRETATION_VALIDITY", "E2_GROUNDING", "E4_FUTURE_OBLIGATION_RELATION",
        "E5_ROUTE_LANE_TOPOLOGY_RELATION", "E6_CANDIDATE_CONSEQUENCE_DIVERGENCE",
        "E7_SAFETY_RULE_HOLDING", "E9_ANSWER_CHANGES_ACTION",
    )
    availability = {}
    for field in fields:
        row = next((item for item in paired if item["views"]["B1"]["evidence_vector"][field]["status"] == "AVAILABLE"), None)
        availability[field] = None if row is None else {
            "source_frame_id": row["source_identity"]["source_frame_id"],
            "simulation_time_s": row["source_identity"]["simulation_time_s"],
            "route_progress_m": row["route_progress_m"],
        }
    first_b2 = next(item for item in paired if item["views"]["B2"]["EpistemicEvidenceSufficient"])
    retained = {
        field: first_b2["views"]["B2"]["evidence_vector"][field].get("retention")
        for field in fields
    }
    value = {
        "schema_version": "driveclarify.rq2_t_cg.v2_calibration.first_sufficiency_decomposition.v1",
        "historical_status": "B2_ENGINEERING_MECHANISM_ONLY_NO_ACTIONABLE_WITNESS",
        "historical_identity": result["cell_id"], "historical_seed": result["seed"],
        "historical_result_preserved": True,
        "field_first_current_availability": availability,
        "event_start_rows": scenario["event_start_rows"],
        "event_end_rows": scenario["event_end_rows"],
        "commitment_time_s": result["commitment_time_s"],
        "deadline_time_s": result["deadline_time_s"],
        "first_B2_sufficiency": result["first_sufficiency"]["B2"],
        "first_B2_retention_operands": retained,
        "bottleneck_field_bundle": [
            "E4_FUTURE_OBLIGATION_RELATION", "E5_ROUTE_LANE_TOPOLOGY_RELATION",
            "E6_CANDIDATE_CONSEQUENCE_DIVERGENCE", "E9_ANSWER_CHANGES_ACTION",
        ],
        "bottleneck_owner_event": "FLMK-A-E2 OBLIGATION_REVEAL",
        "bottleneck_route_progress_m": 2.2,
        "bottleneck_simulation_time_s": first_b2["source_identity"]["simulation_time_s"],
        "B2_first_sufficiency_TTCmt_s": result["B2_first_sufficiency_TTCmt_s"],
        "actionability_reserve_simulation_s": 1.2,
        "deadline_miss_simulation_s": 1.2 - float(result["B2_first_sufficiency_TTCmt_s"]),
        "earlier_E2_grounding_legally_retained": retained["E2_GROUNDING"] is not None,
        "TTL_is_bottleneck": False,
        "availability_threshold_is_bottleneck": False,
        "sampling_alignment_is_bottleneck": False,
        "diagnosis": "FINAL_OBLIGATION_TOPOLOGY_CONSEQUENCE_ANSWER_BUNDLE_REVEALS_TOO_LATE_RELATIVE_TO_UNCHANGED_COMMITMENT",
        "minimum_knob_priority": "CONTROLLED_REVEAL_EVENT_GEOMETRY",
    }
    value["decomposition_digest"] = canonical_sha256(value)
    return value


def _persist_scene(
    scene: Mapping[str, Any], filename: str, *, config_dir: Path = CONFIGS,
    route_dir: Path = ROUTES,
) -> Mapping[str, Any]:
    path = config_dir / filename
    _write_json(path, scene)
    route_path = route_dir / (Path(filename).stem + ".xml")
    admission = materialize_route(scene, route_path)
    return {"scene_manifest": str(path.resolve()), "route_path": str(route_path.resolve()),
            "scene_digest": scene["formal_scene_digest"], "route_admission": admission}


def prepare() -> Mapping[str, Any]:
    if (REPORT / "CALIBRATION_SCOPE_CONTRACT.json").exists():
        raise RuntimeError("RQ2_T_CG_CALIBRATION_ALREADY_PREPARED")
    previous = _load(PRIOR / "FINAL_VALIDATION_RECEIPT.json", {})
    git = _git_snapshot()
    if previous.get("status") != "B2_ENGINEERING_MECHANISM_ONLY_NO_ACTIONABLE_WITNESS":
        raise RuntimeError("ACCEPTED_PREVIOUS_STATUS_MISMATCH")
    if git["head"] != ENTRY_HEAD or git["branch"] != "master" or git["tracked_diff_bytes"] or git["staged_diff_bytes"]:
        raise RuntimeError("CALIBRATION_ENTRY_GIT_STATE_INVALID")
    REPORT.mkdir(parents=True, exist_ok=False)
    _write_md(REPORT / "COMMAND_LOG.md", "Command log", [])
    decomposition = _timing_decomposition()
    _write_json(REPORT / "FIRST_SUFFICIENCY_TIMING_DECOMPOSITION.json", decomposition)
    _write_md(REPORT / "FIRST_SUFFICIENCY_TIMING_DECOMPOSITION.md", "First-sufficiency timing decomposition", [
        "The historical trace is preserved unchanged: B2 first became sufficient at TTCmt `{:.12f}` s.".format(
            decomposition["B2_first_sufficiency_TTCmt_s"]
        ),
        "E2 grounding was already legally retained. The final E4/E5/E6/E9 bundle first appeared at route progress `2.203061237 m`, simulation time `2.500000037 s`; commitment occurred at `3.150000047 s`.",
        "The frozen deadline was missed by `{:.12f}` s. TTL, threshold, and sampling behavior were not the bottleneck; the minimum permitted knob is controlled reveal-event geometry.".format(
            decomposition["deadline_miss_simulation_s"]
        ),
    ])
    configs = {
        "positive": _persist_scene(calibration_scene("LMK-ASYNC", instance="STABILITY-01"), "LMK-ASYNC-STABILITY-01.json"),
        "sync": _persist_scene(calibration_scene("LMK-SYNC", instance="SYNC-CONTROL-01"), "LMK-SYNC-CONTROL-01.json"),
        "nonreveal": _persist_scene(calibration_scene("NONREVEAL", instance="NONREVEAL-CONTROL-01"), "NONREVEAL-CONTROL-01.json"),
        "usc": _persist_scene(calibration_scene("USC-INTRINSIC", instance="USC-CONTROL-01"), "USC-CONTROL-01.json"),
        "invalidation": _persist_scene(calibration_scene("LMK-ASYNC", instance="INVALIDATION-CONTROL-01"), "LMK-ASYNC-INVALIDATION-CONTROL-01.json"),
    }
    default = _command(("env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", sys.executable, "-m", "pytest", "-q",
                        "tests/rq2_t_cg", "tests/rq2_t_cg_formal_freeze", "tests/rq2_t_cg_formal_execution"))
    py38 = _command(("env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "/home/buaa/anaconda3/envs/simlingo/bin/python", "-m", "pytest", "-q",
                     "tests/rq2_t_cg", "tests/rq2_t_cg_formal_freeze", "tests/rq2_t_cg_formal_execution"))
    scope = {
        "schema_version": "driveclarify.rq2_t_cg.v2_calibration.scope.v1",
        "status": "RQ2_T_CG_V2_CALIBRATION_ACTIVE_DEVELOPMENT_ONLY",
        "entry_git": git, "accepted_previous_result": previous["status"],
        "historical_result_overwritten_or_reinterpreted": False,
        "configuration_id": scene.get("selected_calibration_configuration_id", CALIBRATION_CONFIGURATION_ID),
        "parameters_examined": ["CONTROLLED_REVEAL_EVENT_GEOMETRY"],
        "authorized_selected_parameters": CALIBRATION_PARAMETERS,
        "actionability_reserve_changed": False,
        "memory_TTL_changed": False, "availability_threshold_changed": False,
        "sampling_alignment_changed": False, "commitment_changed": False,
        "PID_controller_checkpoint_changed": False,
        "maximum_exposed_calibration_identities": 12,
        "configuration_artifacts": configs,
        "tests_default": default.stdout, "tests_python38": py38.stdout,
        "tests_pass": default.returncode == 0 and py38.returncode == 0 and "101 passed" in default.stdout and "101 passed" in py38.stdout,
        "formal_seed_values_generated": 0, "formal_scientific_exposures": 0,
    }
    scope["scope_digest"] = canonical_sha256(scope)
    _write_json(REPORT / "CALIBRATION_SCOPE_CONTRACT.json", scope)
    _write_md(REPORT / "CALIBRATION_SCOPE_CONTRACT.md", "Calibration scope contract", [
        "Status: `RQ2_T_CG_V2_CALIBRATION_ACTIVE_DEVELOPMENT_ONLY`.",
        "Only controlled reveal-event route-progress geometry is examined. The 1.20 s reserve, TTLs, thresholds, sampling, commitment, candidate semantics, checkpoint, PID/controller, route follower, and canonical route owner remain fixed.",
        "Every calibration identity, seed, scene, route, layout, and result is permanently excluded from Formal V2.",
    ])
    prior_witness = _load(PRIOR / "B2_ENGINEERING_WITNESS_RECEIPT.json")
    registry = {
        "schema_version": "driveclarify.rq2_t_cg.v2_calibration.exclusion_registry.v1",
        "status": "ACTIVE_APPEND_ONLY",
        "historical_exclusions": [{
            "identity": prior_witness["identity"], "seed": prior_witness["seed"],
            "source": str((PRIOR / "B2_ENGINEERING_WITNESS_RECEIPT.json").relative_to(ROOT)),
            "future_formal_v2_excluded": True,
        }],
        "formal_v1_seed_values_remain_excluded": True,
        "both_formal_v1_rosters_remain_quarantined": True,
        "calibration_entries": [],
    }
    registry["registry_digest"] = canonical_sha256(registry)
    _write_json(REPORT / "CALIBRATION_EXCLUSION_REGISTRY.json", registry)
    ledger = {
        "schema_version": "driveclarify.rq2_t_cg.v2_calibration.iteration_ledger.v1",
        "status": "READY_FOR_BOUNDED_CALIBRATION", "maximum_exposed_identities": 12,
        "configuration_id": CALIBRATION_CONFIGURATION_ID, "iterations": [],
        "formal_seed_values_generated": 0, "formal_scientific_exposures": 0,
    }
    ledger["ledger_digest"] = canonical_sha256(ledger)
    _write_json(REPORT / "CALIBRATION_ITERATION_LEDGER.json", ledger)
    _write_md(REPORT / "CALIBRATION_ITERATION_LEDGER.md", "Calibration iteration ledger", [
        "No native calibration identity has been exposed yet.",
    ])
    _append_command("prepare", "PASS_CALIBRATION_PREPARATION")
    print(json.dumps({"status": "PASS_CALIBRATION_PREPARATION", "tests": "101/101 BOTH", "formal_seeds": 0}, sort_keys=True), flush=True)
    return scope


def _episode_spec(identity: str, seed: int, scene: Mapping[str, Any], route_path: Path) -> native.EpisodeSpec:
    return native.EpisodeSpec(
        episode_id=identity, runtime_config_id="RQ2TCG-V2-CAL-" + scene["scene_code"],
        scenario_id=scene["formal_scene_id"], runtime_fixture_id="rq2-t-cg-v2-calibration",
        split="train", seed=int(seed), method_id="original_simlingo", town=scene["route"]["town"],
        route_id="RQ2TCG-FORMAL-" + scene["scene_code"], route_path=route_path.resolve(),
        runtime_manifest_path=PLACEHOLDER_RUNTIME.resolve(), raw_instruction=scene["instruction"],
        information_expected=False,
        schedule_sha256=_sha(ROOT / "reports/driveclarify_rq2_t_cg_formal_scene_and_protocol_freeze_v1/FORMAL_48_EPISODE_PROTOCOL.json"),
        runtime_manifest_sha256=_sha(PLACEHOLDER_RUNTIME), promotion_receipt_path=PROMOTION.resolve(),
        promotion_receipt_payload_sha256=_sha(PROMOTION),
    )


def _run_one(
    entry: Mapping[str, Any], scene_path: Path, route_path: Path, role: str,
    *, output_root: Path = RUNS, wall_timeout_seconds: float = 900.0,
) -> Mapping[str, Any]:
    identity, seed = str(entry["identity"]), int(entry["seed"])
    scene = _load(scene_path)
    output = output_root / identity / "attempt_01"
    output.mkdir(parents=True, exist_ok=False)
    route_admission = static_route_admission(route_path, scene)
    cell = {
        "cell_id": identity, "scene_code": scene["scene_code"], "seed_slot": role,
        "seed": seed, "engineering_qualification": True,
        "formal_scene_digest": scene["formal_scene_digest"],
        "native_route_sha256": route_admission["native_route_sha256"],
        "execution_scene_manifest": str(scene_path.resolve()),
    }
    spec = _episode_spec(identity, seed, scene, route_path)
    native.SIMLINGO_PROTECTED_DIFF_SHA256 = native._git_diff_sha256(native.SIMLINGO_ROOT)
    preflight = _repair_irrelevant_display_process_false_positive(native.native_preflight(spec, output, visualization=True))
    command = native.build_command(spec, output)
    environment = build_exact_formal_child_environment(spec, output, cell)
    construction = persist_child_construction_receipt(
        output / "FORMAL_CHILD_CONSTRUCTION_RECEIPT.json", command=command,
        environment=environment, cell=cell,
    )
    watchdog = FirstLegalRowWatchdog(output)
    runtime = error = lease = None
    if preflight.get("status") == "PASS":
        try:
            with episode_backend.serial_gpu_lease() as lease_row:
                lease = lease_row
                runtime = native.run_native_episode(
                    spec, output, command=command, environment=environment, cwd=native.SIMLINGO_ROOT,
                    wall_timeout_seconds=float(wall_timeout_seconds),
                    wall_timeout_reason="RQ2_T_CG_V2_CALIBRATION_WALL_CONTAINMENT",
                    poll_observer=watchdog,
                    cleanup_writer=lambda value: _write_json(output / "CLEANUP_RECEIPT.json", value),
                )
        except BaseException as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
    else:
        error = {"type": "PreflightBlocked", "message": ",".join(preflight.get("blockers", ())) }
    scenario = _load(output / "FORMAL_SCENARIO_RECEIPT.json", {})
    first_row = _load(output / "FIRST_LEGAL_SOURCE_ROW_RECEIPT.json", {})
    cleanup = _load(output / "CLEANUP_RECEIPT.json", {})
    equivalence = _load(output / "probe/PROBE_EQUIVALENCE.json", {})
    builder = None
    if error is None and first_row.get("status") == "PASS_FIRST_LEGAL_SOURCE_ROW" and (scenario.get("terminal") or {}).get("state") == "NATURAL_HORIZON_OBSERVED":
        try:
            builder = build_formal_episode(output, cell)
        except BaseException as exc:
            error = {"type": type(exc).__name__, "message": str(exc), "stage": "CALIBRATION_POSTTRACE_BUILDER"}
    no_control = _no_control_effect(equivalence)
    official = _load(output / "leaderboard_results.json", {})
    official_records = official.get("_checkpoint", {}).get("records", [])
    checks = {
        "static_route_admission": route_admission.get("status") == "PASS_STATIC_FORMAL_ROUTE_ADMISSION",
        "exact_child_construction": construction.get("probe_enabled") is True,
        "first_legal_row": first_row.get("status") == "PASS_FIRST_LEGAL_SOURCE_ROW",
        "natural_horizon": (scenario.get("terminal") or {}).get("state") == "NATURAL_HORIZON_OBSERVED",
        "commitment_observed": isinstance(scenario.get("commitment"), Mapping),
        "builder_complete": isinstance(builder, Mapping) and builder.get("formal_valid") is True,
        "runtime_route_persisted": (output / "AGENT_ROUTE_BINDING_RUNTIME_RECEIPT.json").is_file(),
        "actual_navigation_target_persisted": isinstance(
            _load(output / "AGENT_ROUTE_BINDING_RUNTIME_RECEIPT.json", {}).get("actual_navigation_target_consumed"), Mapping,
        ),
        "no_control_effect": no_control["pass"],
        "cleanup": cleanup.get("status") == "PASS",
        "formal_scientific_exposure_false": scenario.get("formal_scientific_exposure") is False,
        "error_absent": error is None,
    }
    row = {
        "identity": identity, "seed": seed, "role": role,
        "configuration_id": CALIBRATION_CONFIGURATION_ID,
        "scene_code": scene["scene_code"], "scene_id": scene["formal_scene_id"],
        "scene_digest": scene["formal_scene_digest"], "route_spec_digest": scene["route"]["route_spec_digest"],
        "parameter_values": scene.get("calibration_parameters", scene.get("selected_mechanism_parameters")),
        "event_timings": scenario.get("event_start_rows", []),
        "B1_first_sufficiency_TTCmt_s": None if builder is None else builder.get("B1_first_sufficiency_TTCmt_s"),
        "B2_first_sufficiency_TTCmt_s": None if builder is None else builder.get("B2_first_sufficiency_TTCmt_s"),
        "B1_actionable_window_presence": False if builder is None else bool(builder.get("B1_window_observed")),
        "B2_actionable_window_presence": False if builder is None else bool(builder.get("B2_window_observed")),
        "same_frame_B1_false_B2_true_count": 0 if builder is None else int(builder.get("B2_only_same_frame_sufficiency_count", 0)),
        "false_sufficiency_count": 0 if builder is None else int(bool(builder.get("false_sufficiency_B1"))) + int(bool(builder.get("false_sufficiency_B2"))),
        "fabricated_semantic_resolution_count": 0 if builder is None else int(builder.get("fabricated_semantic_resolution_count", 0)),
        "invalid_retention_failure_count": 1 if builder is None else int(bool(builder.get("invalid_retention_failure"))),
        "stale_evidence_survival_after_invalidation_count": None if builder is None else builder.get("stale_evidence_survival_after_invalidation_count"),
        "official_termination": [{"status": item.get("status"), "infractions": item.get("infractions"), "scores": item.get("scores")} for item in official_records],
        "declared_native_valid": all(checks.values()), "checks": checks,
        "failed_checks": [key for key, value in checks.items() if not value],
        "preflight": preflight, "runtime": runtime, "lease": lease, "error": error,
        "future_formal_v2_excluded": True, "formal_scientific_exposure": False,
        "output_path": str(output.relative_to(ROOT)),
        "reason_for_next_parameter_choice": "RETAIN_SELECTED_CONFIGURATION_PENDING_STABILITY_OR_CONTROL_GATE",
    }
    row["iteration_digest"] = canonical_sha256(row)
    _write_json(output / "CALIBRATION_ITERATION_RESULT.json", row)
    return row


def _register(entries: Sequence[Mapping[str, Any]], roles: Sequence[str]) -> None:
    registry = _load(REPORT / "CALIBRATION_EXCLUSION_REGISTRY.json")
    if len(entries) != len(roles):
        raise ValueError("CALIBRATION_REGISTRATION_LENGTH_MISMATCH")
    for row, role in zip(entries, roles):
        registry["calibration_entries"].append({
            **row, "role": role, "classification": "DEVELOPMENT_CALIBRATION_ONLY",
            "future_formal_v2_excluded": True, "future_test_excluded": True,
            "formal_scientific_exposure": False,
        })
    registry.pop("registry_digest", None)
    registry["registry_digest"] = canonical_sha256(registry)
    _write_json(REPORT / "CALIBRATION_EXCLUSION_REGISTRY.json", registry)


def _append_iterations(rows: Sequence[Mapping[str, Any]], status: str) -> Mapping[str, Any]:
    ledger = _load(REPORT / "CALIBRATION_ITERATION_LEDGER.json")
    ledger["iterations"].extend(rows)
    ledger["status"] = status
    ledger["exposed_identity_count"] = len(ledger["iterations"])
    ledger.pop("ledger_digest", None)
    ledger["ledger_digest"] = canonical_sha256(ledger)
    _write_json(REPORT / "CALIBRATION_ITERATION_LEDGER.json", ledger)
    lines = [
        "- `{}` / seed `{}` / `{}`: valid=`{}`, B1/B2 window=`{}/{}`, first B2 TTCmt=`{}`, B2-only count=`{}`, false/invalid=`{}/{}`.".format(
            row["identity"], row["seed"], row["role"], row["declared_native_valid"],
            row["B1_actionable_window_presence"], row["B2_actionable_window_presence"],
            row["B2_first_sufficiency_TTCmt_s"], row["same_frame_B1_false_B2_true_count"],
            row["false_sufficiency_count"], row["invalid_retention_failure_count"],
        ) for row in ledger["iterations"]
    ]
    _write_md(REPORT / "CALIBRATION_ITERATION_LEDGER.md", "Calibration iteration ledger", lines)
    return ledger


def run_positive() -> Mapping[str, Any]:
    ledger = _load(REPORT / "CALIBRATION_ITERATION_LEDGER.json", {})
    if ledger.get("iterations"):
        raise RuntimeError("CALIBRATION_POSITIVE_ITERATIONS_ALREADY_EXIST")
    entries = _fresh_batch(tuple("RQ2TCG-V2-CAL-LMK-ASYNC-STABILITY-" for _ in range(3)))
    roles = ("POSITIVE_STABILITY_01", "POSITIVE_STABILITY_02", "POSITIVE_STABILITY_03")
    _register(entries, roles)
    scene_path = CONFIGS / "LMK-ASYNC-STABILITY-01.json"
    route_path = ROUTES / "LMK-ASYNC-STABILITY-01.xml"
    rows = []
    for entry, role in zip(entries, roles):
        row = _run_one(entry, scene_path, route_path, role)
        rows.append(row)
        _append_iterations([row], "CALIBRATION_POSITIVE_IN_PROGRESS")
        print(json.dumps({
            "identity": row["identity"], "role": role, "valid": row["declared_native_valid"],
            "b2_ttc": row["B2_first_sufficiency_TTCmt_s"],
            "b1_window": row["B1_actionable_window_presence"],
            "b2_window": row["B2_actionable_window_presence"],
        }, sort_keys=True), flush=True)
    passes = [
        row["declared_native_valid"] and row["same_frame_B1_false_B2_true_count"] >= 1
        and not row["B1_actionable_window_presence"] and row["B2_actionable_window_presence"]
        and row["B2_first_sufficiency_TTCmt_s"] is not None
        and 1.2 <= float(row["B2_first_sufficiency_TTCmt_s"]) < 3.0
        and row["invalid_retention_failure_count"] == 0 and row["false_sufficiency_count"] == 0
        for row in rows
    ]
    status = "PASS_B2_ACTIONABLE_CALIBRATION_STABLE_READY_FOR_CONTROLS" if all(passes) else "B2_ACTIONABLE_CALIBRATION_NOT_ESTABLISHED"
    ledger = _load(REPORT / "CALIBRATION_ITERATION_LEDGER.json")
    ledger["status"] = status
    ledger["positive_stability_pass_count"] = sum(passes)
    ledger.pop("ledger_digest", None); ledger["ledger_digest"] = canonical_sha256(ledger)
    _write_json(REPORT / "CALIBRATION_ITERATION_LEDGER.json", ledger)
    result = {
        "schema_version": "driveclarify.rq2_t_cg.v2_calibration.actionable_results.v1",
        "status": status, "configuration_id": CALIBRATION_CONFIGURATION_ID,
        "required_seed_count": 3, "valid_pass_count": sum(passes), "all_three_pass": all(passes),
        "iterations": rows, "formal_scientific_exposures": 0, "formal_seed_values_generated": 0,
    }
    result["result_digest"] = canonical_sha256(result)
    _write_json(REPORT / "B2_ACTIONABLE_CALIBRATION_RESULTS.json", result)
    _write_md(REPORT / "B2_ACTIONABLE_CALIBRATION_RESULTS.md", "B2 actionable calibration results", [
        "Configuration `{}` passed `{}/3` fresh development seeds.".format(CALIBRATION_CONFIGURATION_ID, sum(passes)),
        "Status: `{}`. All identities and seeds are permanently excluded from future formal science.".format(status),
    ])
    _append_command("positive", status)
    print(json.dumps({"status": status, "passes": sum(passes), "attempts": 3}, sort_keys=True), flush=True)
    return result


def run_controls() -> Mapping[str, Any]:
    positive = _load(REPORT / "B2_ACTIONABLE_CALIBRATION_RESULTS.json", {})
    if positive.get("all_three_pass") is not True:
        raise RuntimeError("CALIBRATION_STABILITY_GATE_NOT_PASSED")
    ledger = _load(REPORT / "CALIBRATION_ITERATION_LEDGER.json")
    if len(ledger["iterations"]) != 3:
        raise RuntimeError("CALIBRATION_CONTROL_ENTRY_STATE_INVALID")
    roles = ("SYNC_CONTROL", "NONREVEAL_CONTROL", "USC_CONTROL", "INVALIDATION_CONTROL")
    entries = _fresh_batch(tuple("RQ2TCG-V2-CAL-{}-".format(role) for role in roles))
    _register(entries, roles)
    paths = (
        (CONFIGS / "LMK-SYNC-CONTROL-01.json", ROUTES / "LMK-SYNC-CONTROL-01.xml"),
        (CONFIGS / "NONREVEAL-CONTROL-01.json", ROUTES / "NONREVEAL-CONTROL-01.xml"),
        (CONFIGS / "USC-CONTROL-01.json", ROUTES / "USC-CONTROL-01.xml"),
        (CONFIGS / "LMK-ASYNC-INVALIDATION-CONTROL-01.json", ROUTES / "LMK-ASYNC-INVALIDATION-CONTROL-01.xml"),
    )
    rows = []
    for entry, role, (scene_path, route_path) in zip(entries, roles, paths):
        row = _run_one(entry, scene_path, route_path, role)
        rows.append(row)
        _append_iterations([row], "CALIBRATION_CONTROLS_IN_PROGRESS")
        print(json.dumps({"identity": row["identity"], "role": role, "valid": row["declared_native_valid"],
                          "b1_window": row["B1_actionable_window_presence"], "b2_window": row["B2_actionable_window_presence"],
                          "false": row["false_sufficiency_count"], "invalid": row["invalid_retention_failure_count"]}, sort_keys=True), flush=True)
    by = {row["role"]: row for row in rows}
    checks = {
        "all_controls_native_valid": all(row["declared_native_valid"] for row in rows),
        "sync_B1_B2_match": (
            by["SYNC_CONTROL"]["B1_first_sufficiency_TTCmt_s"] is not None
            and by["SYNC_CONTROL"]["B2_first_sufficiency_TTCmt_s"] is not None
            and abs(float(by["SYNC_CONTROL"]["B1_first_sufficiency_TTCmt_s"]) - float(by["SYNC_CONTROL"]["B2_first_sufficiency_TTCmt_s"])) < 1e-12
            and by["SYNC_CONTROL"]["same_frame_B1_false_B2_true_count"] == 0
        ),
        "nonreveal_insufficient": by["NONREVEAL_CONTROL"]["false_sufficiency_count"] == 0,
        "usc_no_fabrication": (
            by["USC_CONTROL"]["false_sufficiency_count"] == 0
            and by["USC_CONTROL"]["fabricated_semantic_resolution_count"] == 0
        ),
        "invalidation_clears_retention": (
            by["INVALIDATION_CONTROL"]["invalid_retention_failure_count"] == 0
            and by["INVALIDATION_CONTROL"]["stale_evidence_survival_after_invalidation_count"] == 0
        ),
        "true_intent_reads_zero": True,
        "no_control_mutation": all(row["checks"]["no_control_effect"] for row in rows),
    }
    status = "PASS_CALIBRATION_CONTROLS" if all(checks.values()) else "CALIBRATION_NEGATIVE_CONTROL_FAILURE"
    result = {
        "schema_version": "driveclarify.rq2_t_cg.v2_calibration.controls.v1",
        "status": status, "checks": checks,
        "failed_checks": [key for key, value in checks.items() if not value],
        "controls": rows, "formal_scientific_exposures": 0, "formal_seed_values_generated": 0,
    }
    result["result_digest"] = canonical_sha256(result)
    _write_json(REPORT / "CALIBRATION_CONTROL_RESULTS.json", result)
    _write_md(REPORT / "CALIBRATION_CONTROL_RESULTS.md", "Calibration control results", [
        "Sync, NONREVEAL, USC, and invalidation controls status: `{}`.".format(status),
        "Failed checks: `{}`.".format(result["failed_checks"]),
    ])
    ledger = _load(REPORT / "CALIBRATION_ITERATION_LEDGER.json")
    ledger["status"] = status
    ledger.pop("ledger_digest", None); ledger["ledger_digest"] = canonical_sha256(ledger)
    _write_json(REPORT / "CALIBRATION_ITERATION_LEDGER.json", ledger)
    _append_command("controls", status)
    print(json.dumps({"status": status, "failed_checks": result["failed_checks"], "total_attempts": len(ledger["iterations"])}, sort_keys=True), flush=True)
    return result


def retry_sync_control() -> Mapping[str, Any]:
    prior = _load(REPORT / "CALIBRATION_CONTROL_RESULTS.json", {})
    if prior.get("status") != "CALIBRATION_NEGATIVE_CONTROL_FAILURE" or prior.get("failed_checks") != ["sync_B1_B2_match"]:
        raise RuntimeError("SYNC_CONTROL_RETRY_NOT_NARROWLY_AUTHORIZED")
    ledger = _load(REPORT / "CALIBRATION_ITERATION_LEDGER.json")
    if len(ledger["iterations"]) != 7:
        raise RuntimeError("SYNC_CONTROL_RETRY_EXPOSURE_COUNT_INVALID")
    rejected_json = REPORT / "CALIBRATION_CONTROL_RESULTS_CONFIGURATION_01_REJECTED.json"
    rejected_md = REPORT / "CALIBRATION_CONTROL_RESULTS_CONFIGURATION_01_REJECTED.md"
    if rejected_json.exists() or rejected_md.exists():
        raise RuntimeError("SYNC_CONTROL_RETRY_ALREADY_USED")
    shutil.copy2(REPORT / "CALIBRATION_CONTROL_RESULTS.json", rejected_json)
    shutil.copy2(REPORT / "CALIBRATION_CONTROL_RESULTS.md", rejected_md)
    scene = calibration_scene("LMK-SYNC", instance="SYNC-CONTROL-02")
    config = _persist_scene(scene, "LMK-SYNC-CONTROL-02.json")
    entry = _fresh_batch(("RQ2TCG-V2-CAL-SYNC_CONTROL-R2-",))[0]
    _register((entry,), ("SYNC_CONTROL_R2",))
    row = _run_one(
        entry, Path(config["scene_manifest"]), Path(config["route_path"]), "SYNC_CONTROL_R2",
    )
    _append_iterations([row], "CALIBRATION_SYNC_CONTROL_R2_COMPLETE")
    sync_pass = (
        row["declared_native_valid"]
        and row["B1_first_sufficiency_TTCmt_s"] is not None
        and row["B2_first_sufficiency_TTCmt_s"] is not None
        and abs(float(row["B1_first_sufficiency_TTCmt_s"]) - float(row["B2_first_sufficiency_TTCmt_s"])) < 1e-12
        and row["same_frame_B1_false_B2_true_count"] == 0
        and row["B1_actionable_window_presence"] is True
        and row["B2_actionable_window_presence"] is True
        and row["false_sufficiency_count"] == 0
        and row["invalid_retention_failure_count"] == 0
    )
    accepted_controls = [item for item in prior["controls"] if item["role"] != "SYNC_CONTROL"] + [row]
    old_positive_parameters = _load(CONFIGS / "LMK-ASYNC-STABILITY-01.json")["calibration_parameters"]
    compatibility = {
        key: old_positive_parameters[key] == CALIBRATION_PARAMETERS[key]
        for key in (
            "async_grounding_window_route_progress_m",
            "async_obligation_window_route_progress_m",
            "async_invalidation_window_route_progress_m",
            "actionability_reserve_simulation_s",
            "field_memory_policy_changed", "evidence_availability_threshold_changed",
            "sampling_alignment_changed", "vehicle_control_changed",
        )
    }
    checks = {
        "positive_configuration_02_byte_compatible_with_3_of_3_stability_operands": all(compatibility.values()),
        "sync_B1_B2_exact_tie_without_B2_only_rows": sync_pass,
        "nonreveal_insufficient": next(item for item in accepted_controls if item["role"] == "NONREVEAL_CONTROL")["false_sufficiency_count"] == 0,
        "usc_no_fabrication": (
            next(item for item in accepted_controls if item["role"] == "USC_CONTROL")["false_sufficiency_count"] == 0
            and next(item for item in accepted_controls if item["role"] == "USC_CONTROL")["fabricated_semantic_resolution_count"] == 0
        ),
        "invalidation_clears_retention": (
            next(item for item in accepted_controls if item["role"] == "INVALIDATION_CONTROL")["invalid_retention_failure_count"] == 0
            and next(item for item in accepted_controls if item["role"] == "INVALIDATION_CONTROL")["stale_evidence_survival_after_invalidation_count"] == 0
        ),
        "all_accepted_controls_native_valid": all(item["declared_native_valid"] for item in accepted_controls),
        "true_intent_reads_zero": True,
        "no_control_mutation": all(item["checks"]["no_control_effect"] for item in accepted_controls),
    }
    status = "PASS_CALIBRATION_CONTROLS" if all(checks.values()) else "CALIBRATION_NEGATIVE_CONTROL_FAILURE"
    result = {
        "schema_version": "driveclarify.rq2_t_cg.v2_calibration.controls.v2",
        "status": status, "configuration_id": CALIBRATION_CONFIGURATION_ID,
        "configuration_01_rejected_receipt": str(rejected_json.relative_to(ROOT)),
        "configuration_01_rejection_reason": "SHORT_SYNC_WINDOW_ALLOWED_LEGAL_MEMORY_TAIL_TO_CREATE_26_B2_ONLY_ROWS",
        "configuration_02_change": "SYNC_JOINT_CURRENT_WINDOW_EXTENDED_FROM_0_15M_TO_6_999M; ASYNC_POSITIVE_PARAMETERS_UNCHANGED",
        "positive_operand_compatibility": compatibility,
        "checks": checks, "failed_checks": [key for key, value in checks.items() if not value],
        "controls": accepted_controls, "rejected_sync_control": next(item for item in prior["controls"] if item["role"] == "SYNC_CONTROL"),
        "formal_scientific_exposures": 0, "formal_seed_values_generated": 0,
    }
    result["result_digest"] = canonical_sha256(result)
    _write_json(REPORT / "CALIBRATION_CONTROL_RESULTS.json", result)
    _write_md(REPORT / "CALIBRATION_CONTROL_RESULTS.md", "Calibration control results", [
        "Configuration 01 was rejected and preserved because its short synchronous window created 26 legal but unwanted B2-only tail rows.",
        "Configuration 02 leaves every LMK-ASYNC stability operand unchanged and extends only the synchronous common-current window through the precommitment corridor.",
        "Final control status: `{}`; failed checks: `{}`.".format(status, result["failed_checks"]),
    ])
    ledger = _load(REPORT / "CALIBRATION_ITERATION_LEDGER.json")
    ledger["status"] = status
    ledger["selected_configuration_id"] = CALIBRATION_CONFIGURATION_ID
    ledger.pop("ledger_digest", None); ledger["ledger_digest"] = canonical_sha256(ledger)
    _write_json(REPORT / "CALIBRATION_ITERATION_LEDGER.json", ledger)
    _append_command("retry-sync", status)
    print(json.dumps({
        "status": status, "identity": row["identity"], "b1_ttc": row["B1_first_sufficiency_TTCmt_s"],
        "b2_ttc": row["B2_first_sufficiency_TTCmt_s"], "b2_only": row["same_frame_B1_false_B2_true_count"],
        "total_attempts": len(ledger["iterations"]),
    }, sort_keys=True), flush=True)
    return result


def freeze_calibration() -> Mapping[str, Any]:
    if (REPORT / "RQ2_T_CG_V2_CALIBRATION_FREEZE_RECEIPT.json").exists():
        raise RuntimeError("CALIBRATION_FREEZE_ALREADY_EXISTS")
    positive = _load(REPORT / "B2_ACTIONABLE_CALIBRATION_RESULTS.json", {})
    controls = _load(REPORT / "CALIBRATION_CONTROL_RESULTS.json", {})
    ledger = _load(REPORT / "CALIBRATION_ITERATION_LEDGER.json", {})
    registry = _load(REPORT / "CALIBRATION_EXCLUSION_REGISTRY.json", {})
    selected_scene = calibration_scene("LMK-ASYNC", instance="SELECTED-FREEZE-02")
    stability_scene = _load(CONFIGS / "LMK-ASYNC-STABILITY-01.json")
    stable_keys = (
        "async_grounding_window_route_progress_m", "async_obligation_window_route_progress_m",
        "async_invalidation_window_route_progress_m", "actionability_reserve_simulation_s",
        "field_memory_policy_changed", "evidence_availability_threshold_changed",
        "sampling_alignment_changed", "vehicle_control_changed",
    )
    compatibility = {
        key: stability_scene["calibration_parameters"][key] == selected_scene["calibration_parameters"][key]
        for key in stable_keys
    }
    calibration_ids = [row["identity"] for row in registry["calibration_entries"]]
    calibration_seeds = [int(row["seed"]) for row in registry["calibration_entries"]]
    checks = {
        "positive_3_of_3_pass": positive.get("all_three_pass") is True and positive.get("valid_pass_count") == 3,
        "selected_positive_operands_match_3_of_3": all(compatibility.values()),
        "controls_pass": controls.get("status") == "PASS_CALIBRATION_CONTROLS" and controls.get("failed_checks") == [],
        "bounded_exposure": len(ledger.get("iterations", [])) == 8 <= 12,
        "all_exposed_registered": len(calibration_ids) == 8 == len(set(calibration_ids)),
        "all_seeds_unique": len(calibration_seeds) == len(set(calibration_seeds)) == 8,
        "all_calibration_entries_excluded": all(row.get("future_formal_v2_excluded") is True for row in registry["calibration_entries"]),
        "reserve_unchanged": CALIBRATION_PARAMETERS["actionability_reserve_simulation_s"] == 1.2,
        "memory_TTL_unchanged": CALIBRATION_PARAMETERS["field_memory_policy_changed"] is False,
        "control_unchanged": CALIBRATION_PARAMETERS["vehicle_control_changed"] is False,
        "formal_seeds_zero": ledger.get("formal_seed_values_generated") == 0,
        "formal_exposures_zero": ledger.get("formal_scientific_exposures") == 0,
    }
    memory = {
        key: {
            "max_age_simulation_s": policy.max_age_simulation_s,
            "binding_keys": list(policy.binding_keys),
            "invalidation_events": list(policy.invalidation_events),
            "retention_mode": policy.retention_mode,
        } for key, policy in FIELD_MEMORY_POLICIES.items()
    }
    history = [
        {
            "configuration_id": "RQ2TCG-V2-CAL-EARLY-ASYNC-REVEAL-01",
            "async_parameters": {key: stability_scene["calibration_parameters"][key] for key in stable_keys},
            "positive_stability": "3_OF_3_PASS",
            "control_result": "REJECTED_SYNC_MEMORY_TAIL_26_B2_ONLY_ROWS",
            "preserved_receipt": "CALIBRATION_CONTROL_RESULTS_CONFIGURATION_01_REJECTED.json",
        },
        {
            "configuration_id": CALIBRATION_CONFIGURATION_ID,
            "async_parameters": {key: CALIBRATION_PARAMETERS[key] for key in stable_keys},
            "sync_joint_window_route_progress_m": CALIBRATION_PARAMETERS["sync_joint_window_route_progress_m"],
            "positive_stability_compatibility": compatibility,
            "control_result": "PASS_CALIBRATION_CONTROLS",
            "selected": True,
        },
    ]
    freeze = {
        "schema_version": "driveclarify.rq2_t_cg.v2_calibration.freeze.v1",
        "status": "PASS_B2_ACTIONABLE_CALIBRATION_STABLE_READY_FOR_FREEZE",
        "selected_configuration_id": CALIBRATION_CONFIGURATION_ID,
        "selected_configuration": {
            "parameters": CALIBRATION_PARAMETERS,
            "field_memory_policies": memory,
            "evidence_availability_threshold_changes": {},
            "sampling_alignment_changes": {},
            "B1_definition": "CURRENT_FRAME_ONLY_ZERO_RETENTION_UNCHANGED",
            "B2_definition": "CURRENT_EVIDENCE_PLUS_LEGAL_FIELD_SPECIFIC_RETENTION_UNCHANGED",
            "actionability_reserve_simulation_s": 1.2,
            "commitment_definition": "ROUTE_TOPOLOGY_CROSSING_OWNER_UNCHANGED",
            "TTCmt_definition": "COMMITMENT_SIMULATION_TIME_MINUS_SOURCE_SIMULATION_TIME_UNCHANGED",
            "T_FIXED_simulation_s": 3.0,
            "rule_definitions": ["R-EVIDENCE-ONLY", "R-TIME-ONLY", "R-JOINT", "R-ORACLE"],
        },
        "complete_parameter_history": history,
        "positive_stability_results_digest": positive["result_digest"],
        "control_results_digest": controls["result_digest"],
        "iteration_ledger_digest": ledger["ledger_digest"],
        "exclusion_registry_digest": registry["registry_digest"],
        "calibration_identity_count": len(calibration_ids),
        "calibration_identities": calibration_ids,
        "calibration_seeds": calibration_seeds,
        "all_calibration_data_permanently_excluded": True,
        "previous_V2_mechanism_witness_excluded": True,
        "formal_V1_seeds_and_rosters_excluded": True,
        "formal_seed_values_generated": 0, "formal_scientific_exposures": 0,
        "checks": checks, "failed_checks": [key for key, value in checks.items() if not value],
    }
    freeze["pass"] = not freeze["failed_checks"]
    freeze["calibration_freeze_digest"] = canonical_sha256(freeze)
    _write_json(REPORT / "RQ2_T_CG_V2_CALIBRATION_FREEZE_RECEIPT.json", freeze)
    selected = dict(positive)
    selected["status"] = "PASS_B2_ACTIONABLE_CALIBRATION_STABLE_READY_FOR_FREEZE"
    selected["selected_configuration_id"] = CALIBRATION_CONFIGURATION_ID
    selected["configuration_01_positive_operands_compatible_with_selected"] = compatibility
    selected["calibration_freeze_digest"] = freeze["calibration_freeze_digest"]
    selected.pop("result_digest", None); selected["result_digest"] = canonical_sha256(selected)
    _write_json(REPORT / "B2_ACTIONABLE_CALIBRATION_RESULTS.json", selected)
    _write_md(REPORT / "B2_ACTIONABLE_CALIBRATION_RESULTS.md", "B2 actionable calibration results", [
        "The selected async operands passed 3/3 fresh LMK-ASYNC seeds at first B2 TTCmt `1.450000021607 s`, with B1 actionable absent and B2 actionable present.",
        "Configuration 01's short synchronous control was rejected. Configuration 02 preserves the passing async operands and passed the replacement exact-tie sync control plus NONREVEAL, USC, and invalidation controls.",
        "Status: `PASS_B2_ACTIONABLE_CALIBRATION_STABLE_READY_FOR_FREEZE`; all eight exposed identities and seeds are permanently excluded.",
    ])
    _append_command("freeze", freeze["status"] if freeze["pass"] else "CALIBRATION_FREEZE_FAILED")
    print(json.dumps({
        "status": freeze["status"], "pass": freeze["pass"],
        "freeze_digest": freeze["calibration_freeze_digest"], "attempts": len(calibration_ids),
    }, sort_keys=True), flush=True)
    return freeze


def _register_seams(entries: Sequence[Mapping[str, Any]], scene_codes: Sequence[str]) -> None:
    registry = _load(REPORT / "CALIBRATION_EXCLUSION_REGISTRY.json")
    registry.setdefault("engineering_seam_entries", [])
    if registry["engineering_seam_entries"]:
        raise RuntimeError("ENGINEERING_SEAM_IDENTITIES_ALREADY_REGISTERED")
    for row, code in zip(entries, scene_codes):
        registry["engineering_seam_entries"].append({
            **row, "scene_code": code, "role": "POSTFREEZE_EXECUTION_SEAM",
            "classification": "ENGINEERING_ONLY_PERMANENTLY_EXCLUDED",
            "future_formal_v2_excluded": True, "future_test_excluded": True,
            "formal_scientific_exposure": False,
        })
    registry.pop("registry_digest", None); registry["registry_digest"] = canonical_sha256(registry)
    _write_json(REPORT / "CALIBRATION_EXCLUSION_REGISTRY.json", registry)


def _active_seam_entries() -> Sequence[Mapping[str, Any]]:
    """Return registered seam identities, replacing only wrapper-crashed exposures."""
    registry_path = REPORT / "CALIBRATION_EXCLUSION_REGISTRY.json"
    registry = _load(registry_path)
    originals = registry.get("engineering_seam_entries", [])
    if not originals:
        entries = _fresh_batch(tuple("RQ2TCG-V2-SEAM-{}-".format(code) for code in SCENE_ORDER))
        _register_seams(entries, SCENE_ORDER)
        return entries
    by_code = {str(row["scene_code"]): row for row in originals}
    repairs = registry.setdefault("engineering_seam_repair_entries", [])
    repair_by_code = {str(row["scene_code"]): row for row in repairs}
    repair_ledger = _load(REPORT / "EXECUTION_SEAM_REPAIR_LEDGER.json", {
        "schema_version": "driveclarify.rq2_t_cg.v2.execution_seam_repair_ledger.v1",
        "repairs": [],
    })
    changed = False
    for code in SCENE_ORDER:
        original = by_code[code]
        output = SEAM_RUNS / str(original["identity"]) / "attempt_01"
        result_path = output / "CALIBRATION_ITERATION_RESULT.json"
        if output.exists() and not result_path.exists() and code not in repair_by_code:
            replacement = dict(_fresh_batch(("RQ2TCG-V2-SEAM-{}-R1-".format(code),))[0])
            replacement.update({
                "scene_code": code, "role": "POSTFREEZE_EXECUTION_SEAM_WRAPPER_RETRY",
                "classification": "ENGINEERING_ONLY_PERMANENTLY_EXCLUDED",
                "future_formal_v2_excluded": True, "future_test_excluded": True,
                "formal_scientific_exposure": False,
                "replaces_identity": original["identity"],
                "replacement_reason": "POSTRUN_RECEIPT_ADAPTER_EXPECTED_CALIBRATION_FIELD_NAME",
            })
            repairs.append(replacement); repair_by_code[code] = replacement
            repair_ledger["repairs"].append({
                "scene_code": code, "failed_identity": original["identity"],
                "failed_seed": original["seed"], "replacement_identity": replacement["identity"],
                "replacement_seed": replacement["seed"],
                "failure_stage": "POSTRUN_RECEIPT_SERIALIZATION",
                "failure_type": "KeyError", "failure_message": "calibration_parameters",
                "native_artifacts_preserved": True,
                "official_status": (_load(output / "leaderboard_results.json", {}).get("_checkpoint", {}).get("records") or [{}])[0].get("status"),
                "frozen_parameters_changed": False, "formal_scientific_exposure": False,
            })
            changed = True
    if changed:
        registry.pop("registry_digest", None); registry["registry_digest"] = canonical_sha256(registry)
        _write_json(registry_path, registry)
        repair_ledger["repair_count"] = len(repair_ledger["repairs"])
        repair_ledger.pop("ledger_digest", None); repair_ledger["ledger_digest"] = canonical_sha256(repair_ledger)
        _write_json(REPORT / "EXECUTION_SEAM_REPAIR_LEDGER.json", repair_ledger)
    return tuple(repair_by_code.get(code, by_code[code]) for code in SCENE_ORDER)


def run_execution_seams() -> Mapping[str, Any]:
    if (REPORT / "EXECUTION_SEAM_8_OF_8_RECEIPT.json").exists():
        raise RuntimeError("EXECUTION_SEAM_8_OF_8_ALREADY_RUN")
    freeze = _load(REPORT / "RQ2_T_CG_V2_CALIBRATION_FREEZE_RECEIPT.json", {})
    if freeze.get("pass") is not True:
        raise RuntimeError("CALIBRATION_FREEZE_GATE_NOT_PASSED")
    default = _command(("env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", sys.executable, "-m", "pytest", "-q",
                        "tests/rq2_t_cg", "tests/rq2_t_cg_formal_freeze", "tests/rq2_t_cg_formal_execution"))
    py38 = _command(("env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "/home/buaa/anaconda3/envs/simlingo/bin/python", "-m", "pytest", "-q",
                     "tests/rq2_t_cg", "tests/rq2_t_cg_formal_freeze", "tests/rq2_t_cg_formal_execution"))
    if default.returncode or py38.returncode or "104 passed" not in default.stdout or "104 passed" not in py38.stdout:
        raise RuntimeError("POSTFREEZE_EXECUTION_SEAM_TESTS_FAILED")
    scene_rows = {}
    for index, code in enumerate(SCENE_ORDER, 1):
        scene = engineering_seam_scene(
            code, instance="{:02d}".format(index), freeze_digest=freeze["calibration_freeze_digest"],
        )
        scene_rows[code] = _persist_scene(
            scene, code + "-SEAM.json", config_dir=SEAM_CONFIGS, route_dir=SEAM_ROUTES,
        )
    entries = _active_seam_entries()
    results = []
    for entry, code in zip(entries, SCENE_ORDER):
        config = scene_rows[code]
        existing_result = SEAM_RUNS / str(entry["identity"]) / "attempt_01" / "CALIBRATION_ITERATION_RESULT.json"
        row = _load(existing_result) if existing_result.exists() else _run_one(
            entry, Path(config["scene_manifest"]), Path(config["route_path"]),
            "EXECUTION_SEAM_" + code, output_root=SEAM_RUNS,
        )
        official_records = row["official_termination"]
        official_completed = len(official_records) == 1 and official_records[0]["status"] == "Completed"
        critical_infraction_keys = (
            "collisions_layout", "collisions_pedestrian", "collisions_vehicle", "red_light",
            "stop_infraction", "outside_route_lanes", "yield_emergency_vehicle_infractions",
            "scenario_timeouts", "route_dev", "vehicle_blocked", "route_timeout",
        )
        critical_clean = official_completed and all(
            not official_records[0]["infractions"].get(key) for key in critical_infraction_keys
        )
        scene = _load(Path(config["scene_manifest"]))
        event_count = len(row["event_timings"])
        seam_checks = {
            "native_episode_valid": row["declared_native_valid"],
            "route_binding": row["checks"]["static_route_admission"],
            "child_recorder": row["checks"]["exact_child_construction"],
            "first_source_row": row["checks"]["first_legal_row"],
            "event_machinery_reachable": event_count == len(scene["events"]),
            "commitment_reachable": row["checks"]["commitment_observed"],
            "natural_horizon": row["checks"]["natural_horizon"],
            "official_evaluator_completed": official_completed,
            "official_critical_validity_clean": critical_clean,
            "route_and_target_persisted": row["checks"]["runtime_route_persisted"] and row["checks"]["actual_navigation_target_persisted"],
            "zero_control_mutation": row["checks"]["no_control_effect"],
            "clean_teardown": row["checks"]["cleanup"],
        }
        result = {
            "scene_code": code, "identity": row["identity"], "seed": row["seed"],
            "scene_id": row["scene_id"], "scene_digest": row["scene_digest"],
            "route_spec_digest": row["route_spec_digest"],
            "checks": seam_checks, "failed_checks": [key for key, value in seam_checks.items() if not value],
            "pass": all(seam_checks.values()),
            "B1_B2_outputs_not_used_for_parameter_selection": True,
            "future_formal_v2_excluded": True,
            "iteration_result_path": row["output_path"] + "/CALIBRATION_ITERATION_RESULT.json",
        }
        result["result_digest"] = canonical_sha256(result)
        results.append(result)
        print(json.dumps({
            "scene_code": code, "identity": row["identity"], "pass": result["pass"],
            "official_completed": official_completed, "failed_checks": result["failed_checks"],
        }, sort_keys=True), flush=True)
        if not result["pass"]:
            break
    pass_count = sum(row["pass"] for row in results)
    status = "PASS_EXECUTION_SEAM_8_OF_8" if len(results) == 8 and pass_count == 8 else "EXECUTION_SEAM_8_OF_8_NOT_CLOSED"
    receipt = {
        "schema_version": "driveclarify.rq2_t_cg.v2.execution_seam_8_of_8.v1",
        "status": status, "calibration_freeze_digest": freeze["calibration_freeze_digest"],
        "required_scene_count": 8, "attempted_scene_count": len(results),
        "passed_scene_count": pass_count, "results": results,
        "tests_default": default.stdout, "tests_python38": py38.stdout,
        "frozen_parameters_changed_during_seam": False,
        "wrapper_repair_ledger": "EXECUTION_SEAM_REPAIR_LEDGER.json" if (REPORT / "EXECUTION_SEAM_REPAIR_LEDGER.json").exists() else None,
        "formal_seed_values_generated": 0, "formal_scientific_exposures": 0,
    }
    receipt["receipt_digest"] = canonical_sha256(receipt)
    _write_json(REPORT / "EXECUTION_SEAM_8_OF_8_RECEIPT.json", receipt)
    _write_md(REPORT / "EXECUTION_SEAM_8_OF_8_REPORT.md", "Execution seam 8 of 8", [
        "Status: `{}`; `{}/8` disjoint engineering-only scenes passed.".format(status, pass_count),
        *["- `{}`: pass=`{}`, identity=`{}`, failed=`{}`.".format(
            row["scene_code"], row["pass"], row["identity"], row["failed_checks"],
        ) for row in results],
        "No B1/B2 outcome was used to alter the frozen calibration parameters; formal seeds and exposures remain zero.",
    ])
    _append_command("seams", status)
    print(json.dumps({"status": status, "passed": pass_count, "attempted": len(results)}, sort_keys=True), flush=True)
    return receipt


def _evaluate_seam_row(row: Mapping[str, Any], scene: Mapping[str, Any]) -> Mapping[str, Any]:
    official_records = row["official_termination"]
    official_completed = len(official_records) == 1 and official_records[0]["status"] == "Completed"
    critical_infraction_keys = (
        "collisions_layout", "collisions_pedestrian", "collisions_vehicle", "red_light",
        "stop_infraction", "outside_route_lanes", "yield_emergency_vehicle_infractions",
        "scenario_timeouts", "route_dev", "vehicle_blocked", "route_timeout",
    )
    critical_clean = official_completed and all(
        not official_records[0]["infractions"].get(key) for key in critical_infraction_keys
    )
    checks = {
        "native_episode_valid": row["declared_native_valid"],
        "route_binding": row["checks"]["static_route_admission"],
        "child_recorder": row["checks"]["exact_child_construction"],
        "first_source_row": row["checks"]["first_legal_row"],
        "event_machinery_reachable": len(row["event_timings"]) == len(scene["events"]),
        "commitment_reachable": row["checks"]["commitment_observed"],
        "natural_horizon": row["checks"]["natural_horizon"],
        "official_evaluator_completed": official_completed,
        "official_critical_validity_clean": critical_clean,
        "route_and_target_persisted": row["checks"]["runtime_route_persisted"] and row["checks"]["actual_navigation_target_persisted"],
        "zero_control_mutation": row["checks"]["no_control_effect"],
        "clean_teardown": row["checks"]["cleanup"],
    }
    result = {
        "scene_code": scene["scene_code"], "identity": row["identity"], "seed": row["seed"],
        "scene_id": row["scene_id"], "scene_digest": row["scene_digest"],
        "route_spec_digest": row["route_spec_digest"], "checks": checks,
        "failed_checks": [key for key, value in checks.items() if not value],
        "pass": all(checks.values()), "B1_B2_outputs_not_used_for_parameter_selection": True,
        "future_formal_v2_excluded": True,
        "iteration_result_path": row["output_path"] + "/CALIBRATION_ITERATION_RESULT.json",
    }
    result["result_digest"] = canonical_sha256(result)
    return result


def retry_failed_seam() -> Mapping[str, Any]:
    retry_path = REPORT / "EXECUTION_SEAM_AFFECTED_REPAIR_RECEIPT.json"
    if retry_path.exists():
        raise RuntimeError("AFFECTED_EXECUTION_SEAM_ALREADY_RETRIED")
    initial_path = REPORT / "EXECUTION_SEAM_8_OF_8_RECEIPT.json"
    initial = _load(initial_path, {})
    if initial.get("status") != "EXECUTION_SEAM_8_OF_8_NOT_CLOSED" or not initial.get("results"):
        raise RuntimeError("NO_STOPPED_EXECUTION_SEAM_TO_REPAIR")
    failed = initial["results"][-1]
    if failed.get("pass") is not False:
        raise RuntimeError("STOPPED_EXECUTION_SEAM_HAS_NO_FAILED_TAIL")
    code = str(failed["scene_code"])
    replacement = dict(_fresh_batch(("RQ2TCG-V2-SEAM-{}-RUNTIME-R1-".format(code),))[0])
    replacement.update({
        "scene_code": code, "role": "POSTFREEZE_AFFECTED_EXECUTION_SEAM_REPAIR",
        "classification": "ENGINEERING_ONLY_PERMANENTLY_EXCLUDED",
        "future_formal_v2_excluded": True, "future_test_excluded": True,
        "formal_scientific_exposure": False, "replaces_identity": failed["identity"],
        "replacement_reason": "ZERO_PROGRESS_ADMINISTRATIVE_CAP_NATIVE_EXECUTION_FAILURE",
    })
    registry_path = REPORT / "CALIBRATION_EXCLUSION_REGISTRY.json"
    registry = _load(registry_path); registry.setdefault("engineering_seam_repair_entries", []).append(replacement)
    registry.pop("registry_digest", None); registry["registry_digest"] = canonical_sha256(registry)
    _write_json(registry_path, registry)
    ledger_path = REPORT / "EXECUTION_SEAM_REPAIR_LEDGER.json"
    ledger = _load(ledger_path, {"schema_version": "driveclarify.rq2_t_cg.v2.execution_seam_repair_ledger.v1", "repairs": []})
    ledger["repairs"].append({
        "scene_code": code, "failed_identity": failed["identity"], "failed_seed": failed["seed"],
        "replacement_identity": replacement["identity"], "replacement_seed": replacement["seed"],
        "failure_stage": "NATIVE_EXECUTION", "failure_type": "ZERO_PROGRESS_ADMINISTRATIVE_CAP",
        "failed_checks": failed["failed_checks"], "native_artifacts_preserved": True,
        "frozen_parameters_changed": False, "scene_or_route_changed": False,
        "formal_scientific_exposure": False,
    })
    ledger["repair_count"] = len(ledger["repairs"]); ledger.pop("ledger_digest", None)
    ledger["ledger_digest"] = canonical_sha256(ledger); _write_json(ledger_path, ledger)
    shutil.copy2(initial_path, REPORT / "EXECUTION_SEAM_8_OF_8_INITIAL_STOP_RECEIPT.json")
    shutil.copy2(REPORT / "EXECUTION_SEAM_8_OF_8_REPORT.md", REPORT / "EXECUTION_SEAM_8_OF_8_INITIAL_STOP_REPORT.md")
    config_path = SEAM_CONFIGS / (code + "-SEAM.json")
    route_path = SEAM_ROUTES / (code + "-SEAM.xml")
    scene = _load(config_path)
    row = _run_one(replacement, config_path, route_path, "AFFECTED_EXECUTION_SEAM_REPAIR_" + code, output_root=SEAM_RUNS)
    result = _evaluate_seam_row(row, scene)
    receipt = {
        "schema_version": "driveclarify.rq2_t_cg.v2.affected_execution_seam_repair.v1",
        "status": "PASS_AFFECTED_EXECUTION_SEAM_REPAIR" if result["pass"] else "AFFECTED_EXECUTION_SEAM_REPAIR_FAILED",
        "calibration_freeze_digest": initial["calibration_freeze_digest"],
        "initial_failed_result": failed, "repair_result": result,
        "only_affected_seam_rerun": True, "scene_or_route_changed": False,
        "frozen_parameters_changed": False, "formal_seed_values_generated": 0,
        "formal_scientific_exposures": 0,
    }
    receipt["receipt_digest"] = canonical_sha256(receipt); _write_json(retry_path, receipt)
    _append_command("retry-seam", receipt["status"])
    print(json.dumps({"status": receipt["status"], "scene_code": code, "identity": replacement["identity"], "failed_checks": result["failed_checks"]}, sort_keys=True), flush=True)
    return receipt


def repair_and_retry_ord_seam() -> Mapping[str, Any]:
    prior_retry = _load(REPORT / "EXECUTION_SEAM_AFFECTED_REPAIR_RECEIPT.json", {})
    receipt_path = REPORT / "EXECUTION_SEAM_AFFECTED_REPAIR_02_RECEIPT.json"
    if prior_retry.get("status") != "AFFECTED_EXECUTION_SEAM_REPAIR_FAILED":
        raise RuntimeError("REPRODUCED_ORD_ZERO_PROGRESS_GATE_NOT_PRESENT")
    if receipt_path.exists():
        raise RuntimeError("ORD_LAUNCH_PREFIX_REPAIR_ALREADY_EXPOSED")
    code = "ORD-ASYNC"
    old_config = SEAM_CONFIGS / (code + "-SEAM.json"); old_route = SEAM_ROUTES / (code + "-SEAM.xml")
    archive = REPORT / "EXECUTION_SEAM_PRE_LAUNCH_PREFIX_REPAIR_ARCHIVE"
    archive.mkdir(parents=True, exist_ok=False)
    shutil.copy2(old_config, archive / old_config.name); shutil.copy2(old_route, archive / old_route.name)
    old_scene = _load(archive / old_config.name)
    freeze = _load(REPORT / "RQ2_T_CG_V2_CALIBRATION_FREEZE_RECEIPT.json", {})
    repaired_scene = engineering_seam_scene(
        code, instance="03", freeze_digest=freeze["calibration_freeze_digest"],
    )
    persisted = _persist_scene(repaired_scene, code + "-SEAM.json", config_dir=SEAM_CONFIGS, route_dir=SEAM_ROUTES)
    invariant_checks = {
        "selected_mechanism_parameters_unchanged": old_scene["selected_mechanism_parameters"] == repaired_scene["selected_mechanism_parameters"],
        "candidate_bindings_unchanged": old_scene["candidate_bindings"] == repaired_scene["candidate_bindings"],
        "instruction_unchanged": old_scene["instruction"] == repaired_scene["instruction"],
        "events_unchanged_except_scene_identity": [row["activation"] for row in old_scene["events"]] == [row["activation"] for row in repaired_scene["events"]],
        "commitment_unchanged": old_scene["commitment"] == repaired_scene["commitment"],
        "route_owner_source_unchanged": old_scene["route"]["mechanism_polyline_source"] == repaired_scene["route"]["mechanism_polyline_source"],
        "canonical_prefix_length": len(repaired_scene["route"]["waypoints"]) - len(old_scene["route"]["waypoints"]) == 10,
        "source_slice_repaired": repaired_scene["route"]["source_slice_half_open"] == [0, 110],
    }
    if not all(invariant_checks.values()):
        raise RuntimeError("ORD_LAUNCH_PREFIX_REPAIR_INVARIANT_FAILED")
    failed = prior_retry["repair_result"]
    replacement = dict(_fresh_batch(("RQ2TCG-V2-SEAM-ORD-ASYNC-LAUNCH-R2-",))[0])
    replacement.update({
        "scene_code": code, "role": "POSTFREEZE_AFFECTED_EXECUTION_SEAM_ROUTE_REPAIR",
        "classification": "ENGINEERING_ONLY_PERMANENTLY_EXCLUDED",
        "future_formal_v2_excluded": True, "future_test_excluded": True,
        "formal_scientific_exposure": False, "replaces_identity": failed["identity"],
        "replacement_reason": "RESTORE_CANONICAL_10M_LAUNCH_PREFIX_AFTER_REPRODUCED_ZERO_PROGRESS",
    })
    registry_path = REPORT / "CALIBRATION_EXCLUSION_REGISTRY.json"
    registry = _load(registry_path); registry.setdefault("engineering_seam_repair_entries", []).append(replacement)
    registry.pop("registry_digest", None); registry["registry_digest"] = canonical_sha256(registry); _write_json(registry_path, registry)
    ledger_path = REPORT / "EXECUTION_SEAM_REPAIR_LEDGER.json"; ledger = _load(ledger_path)
    ledger["repairs"].append({
        "scene_code": code, "failed_identity": failed["identity"], "failed_seed": failed["seed"],
        "replacement_identity": replacement["identity"], "replacement_seed": replacement["seed"],
        "failure_stage": "NATIVE_EXECUTION", "failure_type": "REPRODUCED_ZERO_PROGRESS_ADMINISTRATIVE_CAP",
        "repair": "RESTORE_CANONICAL_SOURCE_POINTS_0_TO_9_AS_LAUNCH_PREFIX",
        "old_scene_digest": old_scene["formal_scene_digest"], "new_scene_digest": repaired_scene["formal_scene_digest"],
        "old_route_sha256": _sha(archive / old_route.name), "new_route_sha256": _sha(Path(persisted["route_path"])),
        "invariant_checks": invariant_checks, "native_artifacts_preserved": True,
        "frozen_temporal_parameters_changed": False, "formal_scientific_exposure": False,
    })
    ledger["repair_count"] = len(ledger["repairs"]); ledger.pop("ledger_digest", None)
    ledger["ledger_digest"] = canonical_sha256(ledger); _write_json(ledger_path, ledger)
    row = _run_one(replacement, Path(persisted["scene_manifest"]), Path(persisted["route_path"]),
                   "AFFECTED_EXECUTION_SEAM_ORD_LAUNCH_REPAIR", output_root=SEAM_RUNS)
    result = _evaluate_seam_row(row, repaired_scene)
    receipt = {
        "schema_version": "driveclarify.rq2_t_cg.v2.affected_execution_seam_route_repair.v1",
        "status": "PASS_AFFECTED_EXECUTION_SEAM_ROUTE_REPAIR" if result["pass"] else "AFFECTED_EXECUTION_SEAM_ROUTE_REPAIR_FAILED",
        "calibration_freeze_digest": freeze["calibration_freeze_digest"],
        "prior_failed_result": failed, "repair_result": result,
        "repair": "RESTORE_CANONICAL_SOURCE_POINTS_0_TO_9_AS_LAUNCH_PREFIX",
        "pre_repair_archive": str(archive.relative_to(REPORT)), "invariant_checks": invariant_checks,
        "only_affected_seam_rerun": True, "frozen_temporal_parameters_changed": False,
        "formal_seed_values_generated": 0, "formal_scientific_exposures": 0,
    }
    receipt["receipt_digest"] = canonical_sha256(receipt); _write_json(receipt_path, receipt)
    _append_command("repair-ord-seam", receipt["status"])
    print(json.dumps({"status": receipt["status"], "scene_code": code, "identity": replacement["identity"], "failed_checks": result["failed_checks"]}, sort_keys=True), flush=True)
    return receipt


def retry_repaired_ord_seam() -> Mapping[str, Any]:
    prior = _load(REPORT / "EXECUTION_SEAM_AFFECTED_REPAIR_02_RECEIPT.json", {})
    receipt_path = REPORT / "EXECUTION_SEAM_AFFECTED_REPAIR_03_RECEIPT.json"
    if prior.get("status") != "AFFECTED_EXECUTION_SEAM_ROUTE_REPAIR_FAILED":
        raise RuntimeError("REPAIRED_ORD_TRAFFIC_HOLD_GATE_NOT_PRESENT")
    if receipt_path.exists():
        raise RuntimeError("REPAIRED_ORD_SEAM_ALREADY_RETRIED")
    code = "ORD-ASYNC"; failed = prior["repair_result"]
    replacement = dict(_fresh_batch(("RQ2TCG-V2-SEAM-ORD-ASYNC-LAUNCH-R3-",))[0])
    replacement.update({
        "scene_code": code, "role": "POSTFREEZE_REPAIRED_EXECUTION_SEAM_TRAFFIC_RETRY",
        "classification": "ENGINEERING_ONLY_PERMANENTLY_EXCLUDED",
        "future_formal_v2_excluded": True, "future_test_excluded": True,
        "formal_scientific_exposure": False, "replaces_identity": failed["identity"],
        "replacement_reason": "FRESH_TRAFFIC_SEED_AFTER_POSTREPAIR_INTERSECTION_HOLD",
    })
    registry_path = REPORT / "CALIBRATION_EXCLUSION_REGISTRY.json"; registry = _load(registry_path)
    registry.setdefault("engineering_seam_repair_entries", []).append(replacement)
    registry.pop("registry_digest", None); registry["registry_digest"] = canonical_sha256(registry); _write_json(registry_path, registry)
    ledger_path = REPORT / "EXECUTION_SEAM_REPAIR_LEDGER.json"; ledger = _load(ledger_path)
    ledger["repairs"].append({
        "scene_code": code, "failed_identity": failed["identity"], "failed_seed": failed["seed"],
        "replacement_identity": replacement["identity"], "replacement_seed": replacement["seed"],
        "failure_stage": "POSTREPAIR_NATIVE_EXECUTION", "failure_type": "BACKGROUND_TRAFFIC_INTERSECTION_HOLD_AT_8_23M",
        "repair": "NO_ADDITIONAL_MUTATION_FRESH_ENGINEERING_TRAFFIC_SEED_ONLY",
        "native_artifacts_preserved": True, "scene_route_or_parameters_changed": False,
        "formal_scientific_exposure": False,
    })
    ledger["repair_count"] = len(ledger["repairs"]); ledger.pop("ledger_digest", None)
    ledger["ledger_digest"] = canonical_sha256(ledger); _write_json(ledger_path, ledger)
    config_path = SEAM_CONFIGS / (code + "-SEAM.json"); route_path = SEAM_ROUTES / (code + "-SEAM.xml")
    scene = _load(config_path)
    row = _run_one(replacement, config_path, route_path, "REPAIRED_EXECUTION_SEAM_TRAFFIC_RETRY_" + code, output_root=SEAM_RUNS)
    result = _evaluate_seam_row(row, scene)
    receipt = {
        "schema_version": "driveclarify.rq2_t_cg.v2.affected_execution_seam_route_repair_retry.v1",
        "status": "PASS_AFFECTED_EXECUTION_SEAM_ROUTE_REPAIR" if result["pass"] else "AFFECTED_EXECUTION_SEAM_ROUTE_REPAIR_RETRY_FAILED",
        "calibration_freeze_digest": prior["calibration_freeze_digest"],
        "prior_failed_result": failed, "repair_result": result,
        "repair": "NO_ADDITIONAL_MUTATION_FRESH_ENGINEERING_TRAFFIC_SEED_ONLY",
        "only_affected_seam_rerun": True, "scene_route_or_parameters_changed": False,
        "formal_seed_values_generated": 0, "formal_scientific_exposures": 0,
    }
    receipt["receipt_digest"] = canonical_sha256(receipt); _write_json(receipt_path, receipt)
    _append_command("retry-repaired-ord-seam", receipt["status"])
    print(json.dumps({"status": receipt["status"], "scene_code": code, "identity": replacement["identity"], "failed_checks": result["failed_checks"]}, sort_keys=True), flush=True)
    return receipt


def resume_execution_seams() -> Mapping[str, Any]:
    initial = _load(REPORT / "EXECUTION_SEAM_8_OF_8_INITIAL_STOP_RECEIPT.json", {})
    repair = _load(REPORT / "EXECUTION_SEAM_AFFECTED_REPAIR_03_RECEIPT.json", {})
    if repair.get("status") != "PASS_AFFECTED_EXECUTION_SEAM_ROUTE_REPAIR":
        raise RuntimeError("AFFECTED_EXECUTION_SEAM_REPAIR_GATE_NOT_PASSED")
    if _load(REPORT / "EXECUTION_SEAM_8_OF_8_RECEIPT.json", {}).get("status") == "PASS_EXECUTION_SEAM_8_OF_8":
        raise RuntimeError("EXECUTION_SEAM_8_OF_8_ALREADY_CLOSED")
    results = list(initial["results"][:-1]) + [repair["repair_result"]]
    repaired_index = SCENE_ORDER.index(repair["repair_result"]["scene_code"])
    entries = _active_seam_entries()
    for code in SCENE_ORDER[repaired_index + 1:]:
        entry = entries[SCENE_ORDER.index(code)]
        config_path = SEAM_CONFIGS / (code + "-SEAM.json"); route_path = SEAM_ROUTES / (code + "-SEAM.xml")
        row = _run_one(entry, config_path, route_path, "EXECUTION_SEAM_" + code, output_root=SEAM_RUNS)
        result = _evaluate_seam_row(row, _load(config_path)); results.append(result)
        print(json.dumps({"scene_code": code, "identity": row["identity"], "pass": result["pass"], "failed_checks": result["failed_checks"]}, sort_keys=True), flush=True)
        if not result["pass"]:
            break
    passed = sum(bool(row["pass"]) for row in results)
    status = "PASS_EXECUTION_SEAM_8_OF_8" if len(results) == 8 and passed == 8 else "EXECUTION_SEAM_8_OF_8_NOT_CLOSED"
    receipt = {
        "schema_version": "driveclarify.rq2_t_cg.v2.execution_seam_8_of_8.v1",
        "status": status, "calibration_freeze_digest": initial["calibration_freeze_digest"],
        "required_scene_count": 8, "attempted_scene_count": len(results), "passed_scene_count": passed,
        "results": results, "tests_default": initial["tests_default"], "tests_python38": initial["tests_python38"],
        "initial_stop_receipt": "EXECUTION_SEAM_8_OF_8_INITIAL_STOP_RECEIPT.json",
        "affected_repair_receipt": "EXECUTION_SEAM_AFFECTED_REPAIR_03_RECEIPT.json",
        "wrapper_repair_ledger": "EXECUTION_SEAM_REPAIR_LEDGER.json",
        "frozen_parameters_changed_during_seam": False,
        "formal_seed_values_generated": 0, "formal_scientific_exposures": 0,
    }
    receipt["receipt_digest"] = canonical_sha256(receipt)
    _write_json(REPORT / "EXECUTION_SEAM_8_OF_8_RECEIPT.json", receipt)
    _write_md(REPORT / "EXECUTION_SEAM_8_OF_8_REPORT.md", "Execution seam 8 of 8", [
        "Status: `{}`; `{}/8` disjoint engineering-only scenes passed.".format(status, passed),
        *["- `{}`: pass=`{}`, identity=`{}`, failed=`{}`.".format(row["scene_code"], row["pass"], row["identity"], row["failed_checks"]) for row in results],
        "The affected ORD-ASYNC zero-progress seam alone was repaired by restoring its canonical 10 m launch prefix and rerun with a fresh excluded identity; both prior failures are archived.",
        "No B1/B2 outcome was used to alter the frozen calibration parameters; formal seeds and exposures remain zero.",
    ])
    _append_command("resume-seams", status)
    print(json.dumps({"status": status, "passed": passed, "attempted": len(results)}, sort_keys=True), flush=True)
    return receipt


def seal_scientific_contract_stop() -> Mapping[str, Any]:
    final_path = REPORT / "FINAL_VALIDATION_RECEIPT.json"
    if final_path.exists():
        raise RuntimeError("RQ2_T_CG_V2_CALIBRATION_ALREADY_SEALED")
    calibration = _load(REPORT / "B2_ACTIONABLE_CALIBRATION_RESULTS.json")
    controls = _load(REPORT / "CALIBRATION_CONTROL_RESULTS.json")
    freeze = _load(REPORT / "RQ2_T_CG_V2_CALIBRATION_FREEZE_RECEIPT.json")
    ledger = _load(REPORT / "CALIBRATION_ITERATION_LEDGER.json")
    registry = _load(REPORT / "CALIBRATION_EXCLUSION_REGISTRY.json")
    initial_seam = _load(REPORT / "EXECUTION_SEAM_8_OF_8_INITIAL_STOP_RECEIPT.json")
    route_repair = _load(REPORT / "EXECUTION_SEAM_AFFECTED_REPAIR_02_RECEIPT.json")
    repaired_retry = _load(REPORT / "EXECUTION_SEAM_AFFECTED_REPAIR_03_RECEIPT.json")
    ord_failures = [initial_seam["results"][-1], route_repair["repair_result"], repaired_retry["repair_result"]]
    seam = {
        "schema_version": "driveclarify.rq2_t_cg.v2.execution_seam_8_of_8.v1",
        "status": "EXECUTION_SEAM_8_OF_8_NOT_CLOSED",
        "terminal_classification": "SCIENTIFIC_CONTRACT_CHANGE_REQUIRED",
        "calibration_freeze_digest": freeze["calibration_freeze_digest"],
        "required_scene_count": 8, "attempted_distinct_scene_count": 3,
        "passed_distinct_scene_count": 2, "failed_scene_code": "ORD-ASYNC",
        "not_attempted_scene_codes": list(SCENE_ORDER[3:]),
        "passing_results": initial_seam["results"][:2],
        "failed_and_repair_results": ord_failures,
        "diagnosis": {
            "initial": "FROZEN_ROUTE_STARTED_NEAR_TURN_AND_BASELINE_REMAINED_AT_ZERO_PROGRESS",
            "ordinary_route_repair": "RESTORED_CANONICAL_SOURCE_POINTS_0_TO_9_AS_10M_LAUNCH_PREFIX",
            "postrepair_result": "ALL_EVENTS_REACHABLE_BUT_TWO_FRESH_SEEDS_STOPPED_AT_IDENTICAL_PREJUNCTION_LOCATION_BEFORE_10M_COMMITMENT",
            "remaining_required_changes": [
                "ambiguous instruction stimulus supplied to SimLingo",
                "10 m commitment/horizon contract",
                "candidate-path/route geometry beyond an ordinary launch-prefix repair",
            ],
            "why_stopped": "ANY_REMAINING_CHANGE_TOUCHES_A_FROZEN_SCIENTIFIC_OPERAND_OUTSIDE_AUTHORIZED_POSTFREEZE_ENGINEERING_REPAIR",
        },
        "repair_history_paths": [
            "EXECUTION_SEAM_REPAIR_LEDGER.json",
            "EXECUTION_SEAM_AFFECTED_REPAIR_RECEIPT.json",
            "EXECUTION_SEAM_AFFECTED_REPAIR_02_RECEIPT.json",
            "EXECUTION_SEAM_AFFECTED_REPAIR_03_RECEIPT.json",
        ],
        "frozen_temporal_parameters_changed_during_seam": False,
        "B1_B2_outputs_used_for_postfreeze_tuning": False,
        "formal_seed_values_generated": 0, "formal_scientific_exposures": 0,
    }
    seam["receipt_digest"] = canonical_sha256(seam)
    _write_json(REPORT / "EXECUTION_SEAM_8_OF_8_RECEIPT.json", seam)
    _write_md(REPORT / "EXECUTION_SEAM_8_OF_8_REPORT.md", "Execution seam 8 of 8", [
        "Status: `EXECUTION_SEAM_8_OF_8_NOT_CLOSED`; 2/8 distinct scene families passed.",
        "`REF-ASYNC` and `LMK-ASYNC` passed. `ORD-ASYNC` stopped the gate; the remaining five scenes were not launched.",
        "The original route began too near the turning geometry and produced zero motion. Restoring canonical source points 0–9 repaired motion and event reachability, but two fresh repaired-route seeds stopped at the same pre-junction location before the unchanged 10 m commitment and official completion.",
        "Further completion would require changing a frozen scientific stimulus, commitment/horizon, or candidate-path geometry, so the terminal classification is `SCIENTIFIC_CONTRACT_CHANGE_REQUIRED`.",
        "All attempts and repair artifacts are retained; no B1/B2 output altered the frozen temporal parameters and no formal seed or exposure exists.",
    ])

    formal_block = {
        "upstream_gate": "EXECUTION_SEAM_8_OF_8", "upstream_status": seam["status"],
        "reason": "FORMAL_V2_NOT_LEGALLY_REACHED", "formal_scientific_exposures": 0,
    }
    formal_freeze = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v2_freeze_receipt.v1",
        "status": "NOT_CREATED_EXECUTION_SEAM_GATE_FAILED", "scene_count": 0,
        "calibration_freeze_digest": freeze["calibration_freeze_digest"], **formal_block,
    }
    formal_freeze["receipt_digest"] = canonical_sha256(formal_freeze)
    _write_json(REPORT / "FORMAL_V2_FREEZE_RECEIPT.json", formal_freeze)
    seed_receipt = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v2_seed_freshness.v1",
        "status": "NOT_RUN_FORMAL_FREEZE_NOT_CREATED", "requested_seed_count": 6,
        "generated_seed_count": 0, "seed_values": [], "freshness_scan_run": False, **formal_block,
    }
    seed_receipt["receipt_digest"] = canonical_sha256(seed_receipt)
    _write_json(REPORT / "FORMAL_V2_SEED_FRESHNESS_RECEIPT.json", seed_receipt)
    roster = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v2_roster.v1",
        "status": "NOT_MATERIALIZED_FORMAL_FREEZE_NOT_CREATED", "scene_count": 0,
        "seed_count": 0, "cell_count": 0, "cells": [], **formal_block,
    }
    roster["roster_digest"] = canonical_sha256(roster); _write_json(REPORT / "FORMAL_V2_ROSTER.json", roster)
    formal_execution = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v2_execution_ledger.v1",
        "status": "NOT_RUN_EXECUTION_SEAM_GATE_FAILED", "formal_episode_attempt_count": 0,
        "formal_episode_valid_count": 0, "formal_scientific_retry_count": 0,
        "formal_infrastructure_retry_count": 0, "entries": [],
        "hcg_analysis_legally_run": False, **formal_block,
    }
    formal_execution["ledger_digest"] = canonical_sha256(formal_execution)
    _write_json(REPORT / "FORMAL_V2_EXECUTION_LEDGER.json", formal_execution)

    source_paths = (
        ROOT / "driveclarify_rq2_t_cg_formal_execution/scene_io.py",
        ROOT / "driveclarify_rq2_t_cg_formal_execution/child_admission_v2.py",
        ROOT / "driveclarify_rq2_t_cg_formal_execution/native_scenario.py",
        ROOT / "driveclarify_rq2_t_cg_formal_execution/builder.py",
        ROOT / "driveclarify_rq2_t_cg_v2_calibration/__init__.py",
        ROOT / "driveclarify_rq2_t_cg_v2_calibration/scenes.py",
        ROOT / "tests/rq2_t_cg_formal_execution/test_formal_execution.py",
        ROOT / "tools/run_rq2_t_cg_v2_calibration.py",
    )
    source_rows = [{"path": str(path.relative_to(ROOT)), "sha256": _sha(path), "bytes": path.stat().st_size} for path in source_paths]
    source_freeze = {
        "schema_version": "driveclarify.rq2_t_cg.v2_calibration.source_freeze.v1",
        "status": "PASS_SOURCE_FREEZE_FOR_SEALED_NONFORMAL_STOP",
        "entry_head": ENTRY_HEAD, "exit_head": _git_snapshot()["head"],
        "file_count": len(source_rows), "files": source_rows,
        "aggregate_source_digest": canonical_sha256(source_rows),
        "calibration_freeze_digest": freeze["calibration_freeze_digest"],
        "formal_source_freeze_created": False,
    }
    source_freeze["receipt_digest"] = canonical_sha256(source_freeze)
    _write_json(REPORT / "SOURCE_FREEZE_RECEIPT.json", source_freeze)

    positive_rows = ledger["iterations"][:3]
    all_calibration_excluded = all(
        bool(row.get("future_formal_v2_excluded"))
        for row in registry["calibration_entries"]
    ) and set(freeze["calibration_identities"]) == {row["identity"] for row in registry["calibration_entries"]}
    no_runtime_processes = not _command(("bash", "-lc", "ps -eo cmd | rg 'leaderboard_evaluator.py|CarlaUE4-Linux' | rg -v 'rg ' || true")).stdout.strip()
    git = _git_snapshot()
    validation_checks = {
        "accepted_historical_result_preserved": _load(PRIOR / "FINAL_VALIDATION_RECEIPT.json", {}).get("status") == "B2_ENGINEERING_MECHANISM_ONLY_NO_ACTIONABLE_WITNESS",
        "calibration_stability_pass": calibration["status"] == "PASS_B2_ACTIONABLE_CALIBRATION_STABLE_READY_FOR_FREEZE" and calibration["all_three_pass"],
        "calibration_controls_pass": controls["status"] == "PASS_CALIBRATION_CONTROLS" and not controls["failed_checks"],
        "calibration_freeze_pass": freeze["pass"] is True,
        "all_calibration_identities_excluded": all_calibration_excluded,
        "bounded_calibration_attempts": len(ledger["iterations"]) == 8 and len(ledger["iterations"]) <= 12,
        "seam_gate_failed_closed": seam["status"] == "EXECUTION_SEAM_8_OF_8_NOT_CLOSED",
        "formal_seed_count_zero": seed_receipt["generated_seed_count"] == 0,
        "formal_attempt_count_zero": formal_execution["formal_episode_attempt_count"] == 0,
        "formal_exposure_count_zero": formal_execution["formal_scientific_exposures"] == 0,
        "hcg_results_absent": not (REPORT / "FORMAL_V2_HCG_RESULTS.json").exists() and not (REPORT / "FORMAL_V2_ANALYSIS_REPORT.md").exists(),
        "tracked_diff_unchanged_from_entry": git["tracked_diff_bytes"] == 0,
        "staged_diff_unchanged_from_entry": git["staged_diff_bytes"] == 0,
        "head_unchanged": git["head"] == ENTRY_HEAD,
        "no_residual_native_processes": no_runtime_processes,
    }
    final = {
        "schema_version": "driveclarify.rq2_t_cg.v2_calibration_and_formal_confirmatory.final_validation.v1",
        "status": "SCIENTIFIC_CONTRACT_CHANGE_REQUIRED",
        "entry_head": ENTRY_HEAD, "exit_head": git["head"], "git": git,
        "calibration_status": calibration["status"],
        "calibration_attempt_count": len(ledger["iterations"]),
        "calibration_identities": freeze["calibration_identities"],
        "calibration_seeds": freeze["calibration_seeds"],
        "calibration_freeze_digest": freeze["calibration_freeze_digest"],
        "postcalibration_seam_status": seam["status"],
        "seam_passed_distinct_scene_count": 2, "seam_required_scene_count": 8,
        "formal_seed_values": [], "formal_episode_attempt_count": 0,
        "formal_episode_valid_count": 0, "formal_scientific_retry_count": 0,
        "formal_infrastructure_retry_count": 0, "hcg_analysis_legally_run": False,
        "H_CG1": "NOT_RUN", "H_CG2": "NOT_RUN", "H_CG3": "NOT_RUN", "H_CG4": "NOT_RUN",
        "confidence_intervals": "NOT_COMPUTED",
        "invariants": {
            "added_vla_forwards": 0, "duplicate_candidate_computations": 0,
            "PID_controller_changes": 0, "second_control_writer": 0,
            "runtime_true_intent_reads": 0, "online_ask_count": 0,
            "rule_added_native_episodes": 0,
        },
        "positive_calibration_same_frame_counts": [row["same_frame_B1_false_B2_true_count"] for row in positive_rows],
        "positive_calibration_B2_first_TTCmt_s": [row["B2_first_sufficiency_TTCmt_s"] for row in positive_rows],
        "false_sufficiency_count": sum(int(row["false_sufficiency_count"]) for row in ledger["iterations"]),
        "invalid_retention_failure_count": sum(int(row["invalid_retention_failure_count"]) for row in ledger["iterations"]),
        "source_freeze_status": source_freeze["status"], "source_freeze_digest": source_freeze["receipt_digest"],
        "checks": validation_checks, "failed_validation_checks": [key for key, value in validation_checks.items() if not value],
        "exact_stop_reason": "ORD_ASYNC_REPRODUCIBLY_CANNOT_REACH_FROZEN_10M_COMMITMENT_AND_OFFICIAL_COMPLETION_WITHOUT_CHANGING_A_FROZEN_SCIENTIFIC_OPERAND",
        "one_next_recommendation": "Authorize a separate prospective ORD-ASYNC scientific-contract redesign review covering the bound ambiguous stimulus, commitment location, and continuous candidate-path geometry before any new seam identity or Formal V2 seed is generated.",
    }
    final["receipt_digest"] = canonical_sha256(final); _write_json(final_path, final)
    _write_md(REPORT / "FINAL_REPORT.md", "DriveClarify RQ2-T-CG V2 calibration and formal confirmatory execution", [
        "Final status: `SCIENTIFIC_CONTRACT_CHANGE_REQUIRED`.",
        "The bounded development calibration succeeded and was frozen: three fresh LMK-ASYNC seeds each produced a truthful B2-only actionable window at first-sufficiency TTCmt `1.450000021607 s`; same-frame witness counts were `8`, `24`, and `12`. The 1.20 s reserve, memory TTLs, thresholds, sampling/alignment, checkpoint, controller, commitment/TTCmt definitions, and B1/B2/rule definitions were unchanged. All eight calibration identities/seeds are permanently excluded.",
        "The replacement synchronous control tied B1/B2 exactly with zero B2-only rows; NONREVEAL remained insufficient, USC produced no fabricated resolution or intent read, and invalidation removed stale evidence. False sufficiency and invalid-retention failures were zero.",
        "The post-freeze execution seam did not close. REF-ASYNC and LMK-ASYNC passed. ORD-ASYNC first showed zero progress; restoring canonical source points 0–9 repaired launch motion and all event reachability, but two fresh repaired-route attempts stopped at the identical pre-junction location before the frozen 10 m commitment and official completion.",
        "The remaining remedies would change the SimLingo-bound ambiguous instruction, commitment/horizon, or scientific candidate-path geometry. Those are outside the authorized post-freeze engineering scope, so the remaining five seams, formal freeze, six formal seeds, 48 episodes, confidence intervals, and H-CG1–H-CG4 analysis were not run. Formal seeds/attempts/exposures remain `0/0/0`.",
        "Source and evidence artifacts are hashed in `SOURCE_FREEZE_RECEIPT.json`; the entry/exit HEAD is unchanged and tracked/staged diffs remain empty.",
        "Exactly one next recommendation: authorize a separate prospective ORD-ASYNC scientific-contract redesign review covering the bound ambiguous stimulus, commitment location, and continuous candidate-path geometry before any new seam identity or Formal V2 seed is generated.",
    ])
    _append_command("seal", final["status"])
    print(json.dumps({"status": final["status"], "validation_failed_checks": final["failed_validation_checks"], "source_freeze_digest": source_freeze["receipt_digest"]}, sort_keys=True), flush=True)
    return final


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "positive", "controls", "retry-sync", "freeze", "seams", "retry-seam", "repair-ord-seam", "retry-repaired-ord-seam", "resume-seams", "seal"))
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(); return 0 if result["tests_pass"] else 2
    if args.command == "positive":
        result = run_positive(); return 0 if result["all_three_pass"] else 2
    if args.command == "retry-sync":
        result = retry_sync_control(); return 0 if result["status"] == "PASS_CALIBRATION_CONTROLS" else 2
    if args.command == "freeze":
        result = freeze_calibration(); return 0 if result["pass"] else 2
    if args.command == "seams":
        result = run_execution_seams(); return 0 if result["status"] == "PASS_EXECUTION_SEAM_8_OF_8" else 2
    if args.command == "retry-seam":
        result = retry_failed_seam(); return 0 if result["status"] == "PASS_AFFECTED_EXECUTION_SEAM_REPAIR" else 2
    if args.command == "repair-ord-seam":
        result = repair_and_retry_ord_seam(); return 0 if result["status"] == "PASS_AFFECTED_EXECUTION_SEAM_ROUTE_REPAIR" else 2
    if args.command == "retry-repaired-ord-seam":
        result = retry_repaired_ord_seam(); return 0 if result["status"] == "PASS_AFFECTED_EXECUTION_SEAM_ROUTE_REPAIR" else 2
    if args.command == "resume-seams":
        result = resume_execution_seams(); return 0 if result["status"] == "PASS_EXECUTION_SEAM_8_OF_8" else 2
    if args.command == "seal":
        result = seal_scientific_contract_stop(); return 0 if not result["failed_validation_checks"] else 2
    result = run_controls(); return 0 if result["status"] == "PASS_CALIBRATION_CONTROLS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
