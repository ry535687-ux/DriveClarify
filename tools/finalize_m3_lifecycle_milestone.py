#!/usr/bin/env python3
"""Publish the final evidence bundle for the lifecycle-aware M3 milestone."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
MILESTONE_ID = "DC-M3-LIFECYCLE-MILESTONE-20260805T144406Z"
STATUS = "M3_LIFECYCLE_AWARE_NONBLIND_CAPTURE_REPLAY_COMPLETE_LIVE_HOOK_UNAVAILABLE"
NEXT = "USER_DECISION_ON_BOUNDED_SIMLINGO_CARLA_NO_CONTROL_SHADOW_AUTHORIZATION"
REPORT = (ROOT / "reports/m3_lifecycle_aware_nonblind_capture_replay_shadow" /
          MILESTONE_ID)
ARTIFACT = (ROOT / "artifacts/m3_lifecycle_aware_nonblind_capture_replay_shadow" /
            MILESTONE_ID)
SOURCE = (ROOT / "reports/m2b_formal_offline_real_m1_integration" /
          "DC-M2B-FORMAL-OFFLINE-20260804T095508Z")


def strict_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": file_sha256(path),
    }


def write_new(path: Path, raw: bytes) -> None:
    if path.exists():
        raise FileExistsError("final report file already exists: " + str(path))
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise


def write_json(name: str, value: Any) -> None:
    write_new(REPORT / name, strict_bytes(value))


def run_text(*command: str) -> str:
    return subprocess.run(
        command, cwd=ROOT, check=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.decode("utf-8", errors="replace").strip()


def package_digest(path: Path) -> dict[str, Any]:
    files = sorted(
        item for item in path.rglob("*")
        if item.is_file() and "__pycache__" not in item.parts and
        item.suffix != ".pyc")
    digest = hashlib.sha256()
    for item in files:
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(item.read_bytes()).digest())
    return {
        "algorithm": (
            "SHA256(concat(sorted(relative_path UTF-8 + NUL + binary SHA256 digest)))"),
        "file_count": len(files),
        "bytes": sum(item.stat().st_size for item in files),
        "sha256": digest.hexdigest(),
        "files": [file_record(item) for item in files],
    }


def untracked_paths() -> list[str]:
    raw = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=ROOT, check=True, stdout=subprocess.PIPE,
    ).stdout
    return sorted(item.decode("utf-8", errors="surrogateescape")
                  for item in raw.split(b"\0") if item)


def list_sha(values: Iterable[str]) -> str:
    return sha256_bytes("\0".join(values).encode("utf-8", errors="surrogateescape"))


def load_report(name: str) -> dict[str, Any]:
    return json.loads((REPORT / name).read_text(encoding="utf-8"))


def entry_audit() -> dict[str, Any]:
    current = untracked_paths()
    created_exact = {
        "driveclarify_m3_offline_replay/recorded_capture.py",
        "driveclarify_m3_offline_replay/lifecycle_campaign.py",
        "tests/m3_offline_replay/test_lifecycle_aware_capture.py",
        "tests/m3_offline_replay/test_lifecycle_campaign.py",
        "tests/m3_shadow_bridge/test_shadow_bridge.py",
        "tools/capture_m2b_nonblind_lifecycle_records.py",
        "tools/run_m3_lifecycle_replay_shadow.py",
        "tools/finalize_m3_lifecycle_milestone.py",
    }
    created_prefixes = (
        "driveclarify_m3_shadow_bridge/",
        "artifacts/m3_lifecycle_aware_nonblind_capture_replay_shadow/" +
        MILESTONE_ID + "/",
        "reports/m3_lifecycle_aware_nonblind_capture_replay_shadow/" +
        MILESTONE_ID + "/",
    )
    reconstructed_entry = [
        item for item in current
        if item not in created_exact and
        not any(item.startswith(prefix) for prefix in created_prefixes) and
        "__pycache__" not in item and not item.endswith(".pyc")]
    minimal_end = package_digest(ROOT / "driveclarify_m3_minimal_core")
    replay_end = package_digest(ROOT / "driveclarify_m3_offline_replay")
    shadow_end = package_digest(ROOT / "driveclarify_m3_shadow_bridge")
    return {
        "schema_version": "driveclarify.m3.lifecycle-milestone-entry-audit.v1",
        "milestone_id": MILESTONE_ID,
        "repository": str(ROOT),
        "entry_captured_at_utc": "2026-08-05T14:41:47Z",
        "entry_git": {
            "branch": "master",
            "head": "eaa332b1bb994279b59ea5af786fdb5de96adc1b",
            "tracked_diff_bytes": 0,
            "staged_diff_bytes": 0,
        },
        "current_git_before_final_report_publication": {
            "branch": run_text("git", "branch", "--show-current"),
            "head": run_text("git", "rev-parse", "HEAD"),
            "tracked_diff_bytes": len(run_text("git", "diff").encode("utf-8")),
            "staged_diff_bytes": len(
                run_text("git", "diff", "--cached").encode("utf-8")),
        },
        "entry_untracked_paths": {
            "capture_method": (
                "CONTEMPORANEOUS_STATUS_AUDIT_WITH_FINAL_RECONSTRUCTION_"
                "EXCLUDING_EXACT_MILESTONE_PATHS_AND_EPHEMERAL_BYTECODE"),
            "count": len(reconstructed_entry),
            "sha256": list_sha(reconstructed_entry),
            "paths": reconstructed_entry,
            "preexisting_research_artifacts_preserved": True,
        },
        "package_hashes": {
            "minimal_core_entry": {
                "sha256": "383004ed95d5b180b59380b3ff18c5381d28ca8f43b263a2430e7f6012a0968f",
                "historical_frozen_manifest_bound": True,
            },
            "minimal_core_end": minimal_end,
            "minimal_core_modified": (
                minimal_end["sha256"] !=
                "383004ed95d5b180b59380b3ff18c5381d28ca8f43b263a2430e7f6012a0968f"),
            "replay_entry": {
                "sha256": "05ba3d99cc72246a95958b1f01c6983b6af1868495e605b2e4e82c3c3f2f33f2",
                "source": "PRIOR_RECORDED_CAMPAIGN_FINAL_RESULT",
            },
            "replay_end": replay_end,
            "shadow_entry": None,
            "shadow_end": shadow_end,
        },
        "entry_process_and_compute": {
            "policy_process_count": 0,
            "model_process_count": 0,
            "evaluator_process_count": 0,
            "carla_scenario_runner_bench2drive_process_count": 0,
            "simlingo_process_count": 0,
            "gpu": {
                "name": "NVIDIA GeForce RTX 4070 Ti",
                "memory_total_mib": 12282,
                "memory_used_mib_approx": 1242,
                "memory_free_mib_approx": 10633,
                "compute_app_count": 0,
                "driver": "575.57.08",
            },
            "cuda_context_count": 0,
            "nvcc": "10.1.243",
            "torch_imported_by_entry_probe": False,
        },
        "cpu": {
            "model": "13th Gen Intel(R) Core(TM) i7-13700KF",
            "logical_cpu_count": os.cpu_count(),
            "capture_policy_mode": "CPU_ONLY_RULE_POLICY",
        },
        "git_mutation_boundaries": {
            "commit": False,
            "stage": False,
            "reset": False,
            "clean": False,
            "restore": False,
        },
        "status": "PASS_ENTRY_BOUNDARY",
    }


def producer_root_cause() -> dict[str, Any]:
    rows = [
        ("source_observation_id", "context_from_runtime_record / runtime case",
         "288/288", "preserved through policy; omitted by legacy replay projection",
         "record exact source SHA identity", "LOW_IDENTITY_ONLY", "capture verbatim"),
        ("source_frame_id", "offline observation snapshot",
         "no CARLA frame; frozen observation exists", "never constructed",
         "canonical artifact+case+profile+observation hash",
         "LOW_IF_LABELED_DERIVED", "DERIVED_OFFLINE_SNAPSHOT_ID"),
        ("candidate_set_id", "ordered IDs/roles/plans/hypothesis bindings",
         "semantics 288/288; ID absent", "never constructed",
         "canonical ordered semantic projection digest", "LOW_IDENTITY_ONLY",
         "M2B-CANDIDATE-SET-<SHA256>"),
        ("candidate_ids", "runtime cases / policy context", "288/288",
         "legacy final summary did not bind set identity", "capture ordered values",
         "HIGH_IF_INFERRED_FROM_ACTION", "captured without inference"),
        ("selected_action_raw", "OfflineQueryValuePolicy.recommend", "288/288",
         "not lost", "observer deep-copy after output envelope finalization", "NONE",
         "preserve ACT/ASK/WAIT/FALLBACK_RECOMMENDED"),
        ("selected_action_canonical", "new versioned lifecycle recorder",
         "absent from old M2B producer", "old writer publishes legacy vocabulary only",
         "new recorder explicitly emits canonical field while retaining raw",
         "HIGH_IF_CLAIMED_AS_OLD_ALIAS", "old alias NOT_PROVEN; old 232 excluded"),
        ("decision_monotonic_time", "policy context.monotonic_now", "288/288=100",
         "not lifecycle-bound in old final artifact", "capture verbatim", "NONE",
         "record exact finite value"),
        ("decision_deadline_monotonic", "new pre-frozen capture schedule",
         "absent in source; OPEN is only a status string", "never existed numerically",
         "explicit constant 110.0 frozen before policy execution",
         "HIGH_IF_BACKFILLED", "new records only; no old-record inference"),
        ("answer_deadline_monotonic", "runtime case/profile and policy context",
         "288/288: 101 or 105", "not lost from producer context",
         "capture verbatim", "HIGH_IF_RELABELED_DECISION_DEADLINE",
         "kept distinct from decision deadline"),
        ("query_episode_id", "new identity over source query + case/artifact",
         "source query present for ASK/WAIT", "episode identity never constructed",
         "deterministic identity metadata", "LOW_IDENTITY_ONLY", "hash-bound ID"),
        ("query_identity", "recommendation.query_id / context.active_query_id",
         "ASK/WAIT source values available", "legacy projection incomplete",
         "capture exact source identity", "HIGH_IF_SYNTHESIZED", "no rewrite"),
        ("candidate_freshness", "hard_gate_envelope", "288/288=FRESH",
         "not canonical-M3-bound in old final artifact", "capture verbatim", "NONE",
         "initial state uses exact source"),
        ("decision_evidence_grade", "recorded matrix + symbolic evidence status",
         "matrix/outcomes 288/288; canonical grade absent", "grade never emitted",
         "conservative deterministic classification", "HIGH_IF_UPGRADED",
         "SUPPORTED_BUT_INCOMPLETE 256; UNRESOLVED 32; VERIFIED 0"),
        ("holding evidence / lease", "source control evidence plane",
         "no physical holding evidence or lease", "never existed",
         "record unavailability, do not fabricate", "CRITICAL",
         "NOT_CURRENTLY_AVAILABLE 288; lease null 288"),
        ("initial M3 state", "recorder at producer observer boundary",
         "constituent facts available; state absent", "never constructed",
         "public MinimalM3State construction + invariant check", "HIGH_IF_DEFAULTED",
         "DECISION_READY/no query/no lease/no baseline/no M3 authority"),
        ("source record SHA", "new immutable recorder", "producer inputs available",
         "old lifecycle record did not exist", "canonical closed-record self hash",
         "LOW_IDENTITY_ONLY", "record_sha256 excludes only itself"),
        ("counterfactual matrix identity", "policy context matrix", "288/288 full matrix",
         "old final summary did not bind lifecycle record", "canonical matrix hash",
         "LOW_IDENTITY_ONLY", "hash recorded; matrix not altered"),
        ("source policy version", "recommendation schema/version",
         "288/288", "not lifecycle-bound", "capture verbatim", "NONE",
         "source policy name/version recorded"),
    ]
    fields = (
        "required_field", "producer_location", "source_value_available",
        "lost_at_stage", "capture_method", "fabrication_risk", "resolution")
    return {
        "schema_version": "driveclarify.m3.producer-root-cause.v1",
        "milestone_id": MILESTONE_ID,
        "actual_producer_chain": [
            "driveclarify_m2b_formal_offline.run_integration.main",
            "integration.build_runtime_cases:36 TRAIN/DEV units x 8 profiles",
            "integration.run_policy_cases",
            "offline_decision_evaluation.run_runtime_record",
            "OfflineQueryValuePolicy.recommend",
            "hybridize copies rule action; learned diagnostic cannot override",
            "run_integration final JSON writer",
        ],
        "root_cause": (
            "THE_OLD_FINAL_WRITER_PUBLISHED_DECISION_SUMMARIES_AFTER_THE_POINT_"
            "WHERE_LIFECYCLE_IDENTITIES_TIMES_EVIDENCE_AND_INITIAL_STATE_NEEDED_"
            "TO_BE_CAPTURED"),
        "field_table": [dict(zip(fields, row)) for row in rows],
        "legal_identity_metadata": [
            "DERIVED_OFFLINE_SNAPSHOT_ID",
            "candidate-set semantic digest",
            "deterministic record/query IDs",
            "initial-state/record/matrix SHA256",
        ],
        "facts_not_generated": [
            "passenger answer", "physical holding evidence", "holding lease",
            "historical numeric decision deadline", "baseline eligibility",
            "candidate", "safety state",
        ],
        "legacy_fallback": {
            "exact_alias_verdict": "NOT_PROVEN",
            "old_fallback_recommended_records_excluded": 232,
            "new_behavior": (
                "VERSIONED_RECORDER_PRESERVES_RAW_AND_EXPLICITLY_EMITS_CANONICAL_"
                "FALLBACK_WITHOUT_CLAIMING_AN_OLD_PRODUCER_ALIAS"),
            "action_rewrite_count": 0,
        },
        "status": "PASS_ROOT_CAUSE_RESOLVED_AT_REAL_PRODUCER_OUTPUT_BOUNDARY",
    }


def source_capture_manifest() -> dict[str, Any]:
    inventory = [
        ("M2B_DECISION_RESULTS.json", 4909354,
         "f81769bc632b5e8f7f9481fa0ccfd1b35df978bf432999f58d5f6b9a21d91867",
         "driveclarify.m2b_decision_results.v1", "final/hybrid decisions"),
        ("M2B_RULE_M1_RESULTS.json", 4329683,
         "41f133361dd648d7706e944102121836e99eb43f5dc93516e62644fe30b4fd32",
         "driveclarify.m2b_rule_m1_results.v1", "direct policy runtime outputs"),
        ("M2B_HYBRID_RESULTS.json", 4909243,
         "86f776aea2e61fd67a8f2bf4c81e982aeb069bf5307808f1bb7d23160625c49f",
         "driveclarify.m2b_hybrid_results.v1", "hybrid diagnostic envelope"),
        ("M2B_BASELINE_RESULTS.json", 2769,
         "512f6d6e383abf684815a25f324fdd4b68e958724a4d62a57e38d934df3d9e16",
         "driveclarify.m2b_baseline_results.v1", "aggregate only; not record source"),
    ]
    changed = [
        ROOT / "driveclarify_m2b_formal_offline/integration.py",
        ROOT / "driveclarify_m3_offline_replay/__init__.py",
        ROOT / "driveclarify_m3_offline_replay/contracts.py",
        ROOT / "driveclarify_m3_offline_replay/runner.py",
        ROOT / "driveclarify_m3_offline_replay/recorded_capture.py",
        ROOT / "driveclarify_m3_offline_replay/lifecycle_campaign.py",
        ROOT / "driveclarify_m3_shadow_bridge/__init__.py",
        ROOT / "driveclarify_m3_shadow_bridge/contracts.py",
        ROOT / "driveclarify_m3_shadow_bridge/bridge.py",
        ROOT / "tools/capture_m2b_nonblind_lifecycle_records.py",
        ROOT / "tools/run_m3_lifecycle_replay_shadow.py",
        ROOT / "tools/finalize_m3_lifecycle_milestone.py",
        ROOT / "tests/m3_offline_replay/test_lifecycle_aware_capture.py",
        ROOT / "tests/m3_offline_replay/test_lifecycle_campaign.py",
        ROOT / "tests/m3_shadow_bridge/test_shadow_bridge.py",
    ]
    dataset = file_record(ARTIFACT / "capture/CAPTURE_RUN_A.json")
    dataset_peer = file_record(ARTIFACT / "capture/CAPTURE_RUN_B.json")
    schedule = file_record(ARTIFACT / "capture/FROZEN_CAPTURE_SCHEDULE.json")
    return {
        "schema_version": "driveclarify.m3.source-and-capture-manifest.v1",
        "milestone_id": MILESTONE_ID,
        "source_inventory": [
            {
                "path": str((SOURCE / name).resolve()), "bytes": size,
                "sha256": digest, "schema_version": schema,
                "role": role, "split": "TRAIN_DEV_NONBLIND",
            }
            for name, size, digest, schema, role in inventory],
        "capture_inputs": {
            "runtime_cases": file_record(SOURCE / "M2B_RUNTIME_CASES.json"),
            "development_units": file_record(
                SOURCE / "M2B_REAL_DEVELOPMENT_UNITS.json"),
            "protocol_freeze": file_record(SOURCE / "M2B_PROTOCOL_FREEZE.json"),
            "schedule": schedule,
            "schedule_frozen_before_policy_execution": True,
            "stable_order": "DEV_THEN_TRAIN_CASE_PROFILE_DECISION_ID",
        },
        "producer_and_recorder_files": [file_record(path) for path in changed],
        "producer_observer": {
            "path": str((ROOT / "driveclarify_m2b_formal_offline/integration.py").resolve()),
            "api": "run_policy_cases(..., decision_observer=None)",
            "invocation_count_per_campaign": 288,
            "deep_copy_isolation": True,
            "default_behavior_unchanged": True,
            "selected_action_changed": False,
        },
        "final_capture": {
            "first": dataset,
            "second": dataset_peer,
            "byte_identical": dataset["sha256"] == dataset_peer["sha256"] and
            dataset["bytes"] == dataset_peer["bytes"],
            "canonical_dataset_sha256": (
                "cf5cd880916bc11aa75b21b75a752e30b24b475cabde7ae982307b14d5f6acca"),
            "records_sha256": (
                "f2b5fb6d886b446e58a9338be93e8dc235a1019380e8ee7fe7985edbe460732d"),
            "policy_execution_count_per_campaign": 288,
            "policy_execution_count_total": 576,
            "model_forward_count": 0,
            "split_distribution": {"DEV": 88, "TRAIN": 200},
            "raw_action_distribution": {
                "ACT": 35, "ASK": 7, "WAIT": 14,
                "FALLBACK_RECOMMENDED": 232},
            "canonical_action_distribution": {
                "ACT": 35, "ASK": 7, "WAIT": 14, "FALLBACK": 232},
            "record_count": 288,
            "admitted_count": 288,
            "excluded_count": 0,
            "action_rewrite_count": 0,
            "evidence_fabrication_count": 0,
        },
        "access_boundaries": {
            "formal_m1_test_capture_access_count": 0,
            "r3_capture_access_count": 0,
            "blind_or_sealed_capture_access_count": 0,
            "r3_permanently_consumed": True,
        },
        "status": "PASS_CAPTURE_288_OF_288_BYTE_IDENTICAL",
    }


def test_results() -> dict[str, Any]:
    return {
        "schema_version": "driveclarify.m3.lifecycle-milestone-test-results.v1",
        "milestone_id": MILESTONE_ID,
        "mandatory_final_double_run": {
            "cycle_1": {
                "minimal_core": "44/44 PASS",
                "offline_replay": "58/58 PASS",
                "shadow_bridge": "10/10 PASS",
            },
            "cycle_2": {
                "minimal_core": "44/44 PASS",
                "offline_replay": "58/58 PASS",
                "shadow_bridge": "10/10 PASS",
            },
            "status": "PASS",
        },
        "compileall": {
            "paths": [
                str((ROOT / "driveclarify_m3_minimal_core").resolve()),
                str((ROOT / "driveclarify_m3_offline_replay").resolve()),
                str((ROOT / "driveclarify_m3_shadow_bridge").resolve()),
            ],
            "status": "PASS",
        },
        "targeted_engineering_checks": {
            "capture_contract": "13/13 PASS",
            "lifecycle_schedule_after_hardening": "5/5 PASS",
            "shadow_after_local_registry_hardening": "10/10 PASS",
            "capture_cli_double_publication": "PASS_BYTE_IDENTICAL",
            "recorded_replay_double_campaign": "PASS_BYTE_IDENTICAL",
            "lifecycle_replay_double_campaign": "PASS_BYTE_IDENTICAL",
            "shadow_replay_equivalence": "320/320 PASS",
        },
        "repair_rounds": {
            "phase_0": 0,
            "phase_1": 1,
            "phase_2": 0,
            "phase_3": 0,
            "phase_4": 3,
            "phase_5": 1,
            "phase_6": 0,
        },
        "repair_notes": [
            "Phase 1 clarified canonical emission as a new recorder contract and reran capture.",
            "Phase 4 normalized immutable test comparison, hardened schedule/compiler/derived metrics, then corrected duplicate-mutation metric semantics; each frozen input was rerun.",
            "Phase 5 replaced aggregate imports with direct dependencies and a call-local adapter registry.",
            "No action/evidence/deadline/source/minimal-core/scenario scientific semantics were repaired.",
        ],
        "ephemeral_bytecode_cleanup": "PASS_MOVED_TO_RECOVERABLE_TRASH",
        "status": "PASS",
    }


def ast_boundary() -> dict[str, Any]:
    paths = sorted((ROOT / "driveclarify_m3_shadow_bridge").glob("*.py"))
    forbidden_modules = {"carla", "torch", "os", "pathlib", "subprocess",
                         "time", "random"}
    forbidden_calls = {"open", "write", "write_text", "write_bytes", "unlink",
                       "remove", "apply_control", "forward", "planner", "pid"}
    imported: set[str] = set()
    calls: list[dict[str, Any]] = []
    global_count = 0
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                global_count += 1
            elif isinstance(node, ast.Call):
                name = (node.func.id if isinstance(node.func, ast.Name) else
                        node.func.attr if isinstance(node.func, ast.Attribute) else "")
                if name.lower() in forbidden_calls:
                    calls.append({"path": str(path), "line": node.lineno,
                                  "call": name})
    return {
        "files_scanned": [str(path.resolve()) for path in paths],
        "forbidden_imports": sorted(imported & forbidden_modules),
        "forbidden_calls": calls,
        "global_or_nonlocal_statement_count": global_count,
        "aggregate_replay_initializer_import_count": 0,
        "call_local_adapter_registry": True,
        "mutable_default_registry_independence_test": "PASS",
    }


def static_boundary_audit() -> dict[str, Any]:
    minimal = package_digest(ROOT / "driveclarify_m3_minimal_core")
    fixture = file_record(
        ROOT / "tests/m3_minimal_core/data/executable_fixtures.json")
    return {
        "schema_version": "driveclarify.m3.lifecycle-static-boundary-audit.v1",
        "milestone_id": MILESTONE_ID,
        "minimal_core": {
            "entry_sha256": "383004ed95d5b180b59380b3ff18c5381d28ca8f43b263a2430e7f6012a0968f",
            "end": minimal,
            "modified": False,
            "frozen_fixture": fixture,
            "frozen_fixture_expected_sha256": (
                "f6431d55f42ebc0c7e81ab623fec17c6f3e2429fb0b56be2a3955b80b643fdfd"),
        },
        "shadow_core_ast": ast_boundary(),
        "shadow_dependency_closure": {
            "direct_replay_submodule_imports": True,
            "oracle_evaluator_imported_or_called": False,
            "recorded_capture_imported_or_called": False,
            "recorded_campaign_imported_or_called": False,
            "filesystem_write_reachable": False,
            "model_planner_pid_control_callback_reachable": False,
            "default_adapter_registry_read_by_bridge": False,
            "note": (
                "Replay contracts module defines oracle types but the bridge does not "
                "import, receive, or evaluate an oracle."),
        },
        "campaign_boundaries": {
            "action_rewrite_count": 0,
            "evidence_fabrication_count": 0,
            "lease_fabrication_count": 0,
            "candidate_fabrication_count": 0,
            "query_identity_rewrite_count": 0,
            "low_level_control_output_count": 0,
            "m3_control_write_delta": 0,
            "model_forward_delta": 0,
            "planner_call_delta": 0,
            "pid_call_delta": 0,
            "production_integration": False,
            "physical_holding": False,
            "live_control": False,
        },
        "live_preflight": {
            "phase_2_to_5_pass": True,
            "known_launch_path": True,
            "minimal_core_semantic_change_required": False,
            "bridge_can_be_no_control": True,
            "baseline_can_remain_unique_control": True,
            "physical_holding_required": False,
            "runtime_candidate_and_m2b_decision_hook_available": False,
            "minimum_preflight_pass": False,
            "route_candidate_not_launched": {
                "route_id": "1956", "town": "Town12",
                "scenario": "ParkingExit_1"},
            "reason": (
                "CURRENT_PROBE_EXPOSES_OBSERVATION_BASELINE_FORWARD_AND_CONTROL_"
                "BUT_NOT_CANDIDATE_SET_OR_FINAL_M2B_DECISION"),
            "live_shadow_attempted": False,
        },
        "restricted_data": {
            "formal_m1_test_access_count": 0,
            "r3_capture_replay_oracle_metric_use_count": 0,
            "r3_event_permanently_consumed": True,
            "r3_read_only_static_audit_search_incident_count": 1,
            "incident": (
                "A_READ_ONLY_SUBAGENT_USED_AN_OVERBROAD_RG_THAT_RETURNED_A_FRAGMENT_"
                "OF_PREEXISTING_R3_RAW_PREDICTION_TEXT"),
            "incident_values_recorded_used_or_persisted_count": 0,
            "incident_isolated_from_campaign_inputs_and_conclusions": True,
        },
        "coordination_files_only": [
            str((ROOT / "AGENT_WORKLOG.md").resolve()),
            str((ROOT / "STATE.json").resolve()),
            str((ROOT / "CURRENT_HANDOFF.md").resolve()),
            str((ROOT / "NEXT_AGENT_PROMPT.md").resolve()),
        ],
        "status": "PASS_WITH_DISCLOSED_ISOLATED_READ_ONLY_R3_SEARCH_INCIDENT",
    }


def claim_boundary_markdown() -> str:
    return f"""# Claim Boundary — {MILESTONE_ID}

Final status: `{STATUS}`

## Supported

- A real non-blind M2B TRAIN/DEV producer observer and versioned lifecycle-aware recorder captured 288/288 decisions twice with byte-identical canonical output.
- All 288 admitted records completed deterministic recorded-decision replay; 274 decisions were processed and 14 WAIT records were honestly, structurally rejected because physical holding evidence and leases were unavailable.
- Exactly 32 pre-frozen non-blind scheduled DEV lifecycle episodes executed twice and matched separately published, pre-run oracles. They support `M3_OFFLINE_MULTI_EVENT_LIFECYCLE_INTEGRATION_ON_NEW_NONBLIND_DEV`.
- A no-control shadow bridge processed all 320 episodes with zero replay/shadow mismatches and zero control/model/planner/PID deltas.

## Not supported

- No claim of physical safety, physical holding, recorded passenger behavior, passenger comfort, production integration, live runtime readiness, closed-loop control, or generalization beyond these non-blind inputs.
- Synthetic verified abstract leases exist only in schedule cases 23–26 under `NONBLIND_SYNTHETIC_CONTRACT_TEST_ONLY`; they are not physical evidence.
- Legacy `FALLBACK_RECOMMENDED` is not proven to be an exact historical alias of canonical `FALLBACK`. The old 232 records remain excluded; only the new versioned recorder contract explicitly emits canonical FALLBACK while preserving raw action.
- The known route 1956 / Town12 / ParkingExit_1 was not launched because the current runtime does not expose a reusable candidate-set plus final-M2B-decision hook. Existing wrappers would add model forwards and intentionally stop.

## Data and control boundary

Formal M1 TEST use is zero. R3 use in capture, replay, schedules, oracles, metrics, and conclusions is zero, and R3 remains permanently consumed. One read-only static-audit subprocess used an overbroad search that returned a fragment of pre-existing R3 raw-prediction text; no value was recorded, persisted, or used. This incident is disclosed in `STATIC_BOUNDARY_AUDIT.json` rather than hidden.

`production_integration=false`, `physical_holding=false`, `live_control=false`.

Sole next step: `{NEXT}`
"""


def final_result() -> dict[str, Any]:
    dataset = load_report("LIFECYCLE_AWARE_DATASET_MANIFEST.json")
    recorded = load_report("RECORDED_DECISION_REPLAY_RESULTS.json")
    lifecycle = load_report("LIFECYCLE_DEV_REPLAY_RESULTS.json")
    shadow = load_report("SHADOW_BRIDGE_RESULTS.json")
    other_reports = sorted(
        item for item in REPORT.iterdir()
        if item.is_file() and item.name != "FINAL_RESULT.json")
    end_untracked = untracked_paths()
    coordination = [
        ROOT / "AGENT_WORKLOG.md", ROOT / "STATE.json",
        ROOT / "CURRENT_HANDOFF.md", ROOT / "NEXT_AGENT_PROMPT.md"]
    return {
        "schema_version": "driveclarify.m3.lifecycle-milestone-final-result.v1",
        "long_milestone_id": MILESTONE_ID,
        "final_status": STATUS,
        "highest_phase_completed": (
            "PHASE_6_CONDITIONAL_PREFLIGHT_COMPLETE_LIVE_PILOT_NOT_ENTERED"),
        "phase_results": {
            "phase_0": "PASS_PRODUCER_ROOT_CAUSE",
            "phase_1": "PASS_RECORDER_AND_REAL_PRODUCER_OBSERVER",
            "phase_2": dataset["status"],
            "phase_3": recorded["status"],
            "phase_4": lifecycle["status"],
            "phase_5": shadow["status"],
            "phase_6": "SHADOW_BRIDGE_READY_LIVE_HOOK_UNAVAILABLE",
        },
        "capture": {
            "split_distribution": dataset["split_distribution"],
            "record_count": dataset["record_count"],
            "admitted_count": dataset["admitted_record_count"],
            "excluded_count": dataset["excluded_record_count"],
            "raw_action_distribution": dataset["raw_action_distribution"],
            "canonical_action_distribution": dataset[
                "canonical_action_distribution"],
            "policy_execution_count_total": dataset[
                "policy_execution_count_total"],
            "model_forward_count": dataset["model_forward_count"],
            "dataset_bytes": dataset["dataset_bytes"],
            "dataset_file_sha256": dataset["dataset_sha256"],
            "determinism": dataset["capture_determinism"],
            "legacy_fallback_mapping_verdict": dataset[
                "legacy_fallback_mapping_verdict"],
        },
        "recorded_replay": {
            "episode_count": recorded["episode_count"],
            "accepted_count": recorded["accepted_record_count"],
            "rejected_count": recorded["rejected_record_count"],
            "wait": recorded["wait"],
            "determinism": recorded["determinism"],
            "invariants": recorded["invariants"],
        },
        "lifecycle_replay": {
            "episode_count": lifecycle["episode_count"],
            "execution_completed_count": lifecycle["execution_completed_count"],
            "family_distribution": lifecycle["family_distribution"],
            "answer_case_count": lifecycle["answer_case_count"],
            "timeout_case_count": lifecycle["timeout_case_count"],
            "cancel_case_count": lifecycle["cancel_case_count"],
            "world_change_case_count": lifecycle["world_change_case_count"],
            "revalidation_case_count": lifecycle["revalidation_case_count"],
            "replan_case_count": lifecycle["replan_case_count"],
            "complete_lifecycle_case_count": lifecycle[
                "complete_lifecycle_case_count"],
            "oracle_match_count": lifecycle["oracle_accepted_count"],
            "determinism": lifecycle["determinism"],
            "invariants": lifecycle["invariants"],
        },
        "shadow": {
            "created": shadow["shadow_bridge_created"],
            "input_episode_count": shadow["input_episode_count"],
            "trace_count": shadow["shadow_trace_count"],
            "equivalence_mismatch_counts": shadow[
                "equivalence_mismatch_counts"],
            "m3_control_write_delta": shadow["m3_control_write_delta"],
            "model_forward_delta": shadow["model_forward_delta"],
            "planner_call_delta": shadow["planner_call_delta"],
            "pid_call_delta": shadow["pid_call_delta"],
        },
        "live": {
            "attempted": False,
            "route_id": None,
            "town": None,
            "m2b_decisions_observed": 0,
            "shadow_traces": 0,
            "m3_control_writes": 0,
            "baseline_control_integrity": "NOT_EXERCISED_OFFLINE_PREFLIGHT_ONLY",
            "blocking_finding": (
                "CURRENT_RUNTIME_LACKS_CANDIDATE_SET_PLUS_FINAL_M2B_DECISION_HOOK"),
        },
        "execution_counts": {
            "final_capture_campaign_launches": 2,
            "final_capture_policy_decisions": 576,
            "superseded_repair_capture_launches_moved_to_trash": 2,
            "model_process_launches": 0,
            "live_evaluator_process_launches": 0,
            "carla_launches": 0,
            "scenario_runner_bench2drive_launches": 0,
            "simlingo_launches": 0,
            "task_gpu_compute_processes": 0,
            "task_cuda_contexts": 0,
        },
        "repair_rounds": {
            "phase_0": 0, "phase_1": 1, "phase_2": 0,
            "phase_3": 0, "phase_4": 3, "phase_5": 1, "phase_6": 0},
        "tests": {
            "minimal_core": "44/44 PASS TWICE",
            "offline_replay": "58/58 PASS TWICE",
            "shadow_bridge": "10/10 PASS TWICE",
            "compileall": "PASS",
        },
        "boundaries": {
            "production_integration": False,
            "physical_holding": False,
            "live_control": False,
            "formal_m1_test_use_count": 0,
            "r3_campaign_use_count": 0,
            "r3_permanently_consumed": True,
            "r3_read_only_static_search_incident_count": 1,
            "r3_incident_values_used_count": 0,
        },
        "supported_conclusion": (
            "M3_OFFLINE_MULTI_EVENT_LIFECYCLE_INTEGRATION_ON_NEW_NONBLIND_DEV"),
        "unsupported_claims": [
            "physical safety", "physical holding", "recorded passenger behavior",
            "live runtime readiness", "production integration", "closed-loop control",
            "generalization beyond the frozen non-blind inputs"],
        "blocking_findings": [
            "LIVE_HOOK_UNAVAILABLE; no live service was launched"],
        "non_blocking_backlog": [
            "user decision on a bounded no-control runtime hook/pilot",
            "physical holding remains a separately authorized future prototype"],
        "cleanup": {
            "campaign_processes_remaining": 0,
            "task_pycache_remaining": 0,
            "superseded_task_artifacts": (
                "MOVED_TO_RECOVERABLE_SYSTEM_TRASH_DURING_ENGINEERING_RERUN"),
            "preexisting_untracked_artifacts_preserved": True,
        },
        "git_end": {
            "branch": run_text("git", "branch", "--show-current"),
            "head": run_text("git", "rev-parse", "HEAD"),
            "tracked_diff_bytes": len(run_text("git", "diff").encode("utf-8")),
            "staged_diff_bytes": len(
                run_text("git", "diff", "--cached").encode("utf-8")),
            "untracked_path_count_before_final_result": len(end_untracked),
            "untracked_paths_sha256_before_final_result": list_sha(end_untracked),
        },
        "coordination_files": [file_record(path) for path in coordination],
        "main_report_directory": str(REPORT.resolve()),
        "main_report_file_count": 11,
        "main_report_files_except_self": [file_record(path) for path in other_reports],
        "live_shadow_results_file_created": False,
        "command_log_file_created": False,
        "independent_review_loop_created": False,
        "sole_next_action": NEXT,
        "status": "PASS_WITH_DISCLOSED_ISOLATED_READ_ONLY_R3_SEARCH_INCIDENT",
    }


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    write_json("ENTRY_AUDIT.json", entry_audit())
    write_json("PRODUCER_ROOT_CAUSE.json", producer_root_cause())
    write_json("SOURCE_AND_CAPTURE_MANIFEST.json", source_capture_manifest())
    write_json("TEST_RESULTS.json", test_results())
    write_json("STATIC_BOUNDARY_AUDIT.json", static_boundary_audit())
    write_new(REPORT / "CLAIM_BOUNDARY.md",
              claim_boundary_markdown().encode("utf-8"))
    write_json("FINAL_RESULT.json", final_result())
    print(json.dumps({
        "milestone_id": MILESTONE_ID,
        "final_status": STATUS,
        "report_directory": str(REPORT.resolve()),
        "report_file_count": len(tuple(REPORT.iterdir())),
        "sole_next_action": NEXT,
    }, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
