"""Freeze the E1 population and all pre-execution contracts before TRAIN."""

from __future__ import annotations

import datetime as dt
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .contracts import (
    ARTIFACT_ROOT,
    CATALOG_PATH,
    EXPERIMENT_ID,
    EXTENSION_METHODS,
    FEATURE_FLAG,
    FIREWALL_PATH,
    FREEZE_PATH,
    LEDGER_PATH,
    METHODS_PATH,
    PROTECTED_HASHES,
    PROTECTED_PATHS,
    REPORT_ROOT,
    SCHEMA_PREFIX,
    SEEDS_PATH,
    SPLIT_PATH,
    assert_exact_methods,
    assert_runtime_projection_clean,
    canonical_sha256,
    file_sha256,
)
from .population import build_population


ROOT = Path(__file__).resolve().parents[1]
SOURCE_MANIFEST = (
    ROOT / "driveclarify_paper_mvp_scenarios/generated/STAGE6A_SCENARIO_MANIFEST.json"
)
SOURCE_GENERATED = ROOT / "driveclarify_paper_mvp_scenarios/generated"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(temporary), str(path))


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(temporary), str(path))


def _relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def _source_bindings(population: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    source = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    records = {
        str(row["scenario_id"]): row
        for row in source["records"]
        if str(row.get("split", "")).casefold() == "train"
    }
    required = sorted({str(row["source_train_scenario_id"]) for row in population})
    if any(item not in records for item in required):
        raise RuntimeError("EXTENSION_SOURCE_NOT_ORIGINAL_TRAIN")
    result = {}
    for scenario_id in required:
        row = records[scenario_id]
        runtime_path = SOURCE_GENERATED / str(row["runtime_manifest_path"])
        route_path = SOURCE_GENERATED / str(row["derived_route_path"])
        if not runtime_path.is_file() or not route_path.is_file():
            raise RuntimeError("EXTENSION_SOURCE_ASSET_MISSING:" + scenario_id)
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        result[scenario_id] = {
            "source_split": "train",
            "runtime_fixture_id": row["runtime_fixture_id"],
            "runtime_manifest_path": _relative(runtime_path),
            "runtime_manifest_sha256": file_sha256(runtime_path),
            "route_path": _relative(route_path),
            "route_sha256": file_sha256(route_path),
            "allowed_seed_values": list(runtime["allowed_seed_values"]),
            "town": runtime["route_binding"]["town"],
            "route_id": runtime["route_binding"]["route_id"],
        }
    return result


def _runtime_row(row: Mapping[str, Any], binding: Mapping[str, Any]) -> dict[str, Any]:
    value = {
        key: row[key]
        for key in (
            "scenario_id",
            "split",
            "raw_instruction",
            "ambiguity_type",
            "source_train_scenario_id",
            "source_runtime_fixture_id",
            "town",
            "route_id",
            "carla_seeds",
            "source_asset_scope",
            "original_dev_or_test_asset_used",
            "runtime_projection_sha256",
        )
    }
    value["source_binding"] = dict(binding)
    assert_runtime_projection_clean(value)
    return value


def _initial_ledger(runtime_train: list[Mapping[str, Any]]) -> dict[str, Any]:
    rows = []
    sequence = 0
    for scenario in runtime_train:
        for seed_index, seed in enumerate(scenario["carla_seeds"], start=1):
            for method in EXTENSION_METHODS:
                sequence += 1
                slot = {
                    "sequence": sequence,
                    "scenario_id": scenario["scenario_id"],
                    "seed_index": seed_index,
                    "carla_seed": int(seed),
                    "method_id": method,
                    "split": "train",
                }
                slot["episode_id"] = "DC-GLV1-E1-EP-" + canonical_sha256(slot)[:24]
                slot.update(
                    {
                        "status": "SCHEDULED_NOT_STARTED",
                        "attempt_count": 0,
                        "started_at_utc": None,
                        "ended_at_utc": None,
                        "artifact_dir": None,
                        "episode_result_sha256": None,
                        "failure_reason": None,
                    }
                )
                rows.append(slot)
    assert len(rows) == 216
    return {
        "schema_version": SCHEMA_PREFIX + ".train_ledger.v1",
        "experiment_id": EXPERIMENT_ID,
        "created_at_utc": utc_now(),
        "split": "train",
        "schedule_frozen": True,
        "scheduled_episode_count": 216,
        "extension_dev_attempt_count": 0,
        "extension_test_attempt_count": 0,
        "extension_test_consumed": False,
        "rows": rows,
    }


def _protocol() -> str:
    return """# Grounded Language V1 Extension Experiment E1 Protocol

Status: `PREEXECUTION_FROZEN`  
Scope: `TRAIN ONLY — NOT A FINAL PAPER RESULT`

## Purpose

Evaluate whether the default-OFF Grounded Language V1 runtime naturally discovers
scene-grounded ambiguity and executes ACT, ASK, or WAIT across an independent,
frozen controlled distribution.  This extension does not replace or modify frozen
Stage6B-R0 and does not consume original or extension DEV/TEST.

## Population and execution

- 24 new language-scene pair identities: ACT-oriented 8, ASK-oriented 8,
  WAIT-oriented 8.
- Extension split: TRAIN 12, DEV 6, TEST 6; each split is mechanism-balanced.
- Three CARLA seeds per scenario.
- Exact methods: `original_simlingo`, `never_ask`, `always_ask`, `always_wait`,
  `driveclarify_r0`, `driveclarify_grounded_v1`.
- Planned episodes: 432 total; this stage authorizes exactly 216 TRAIN episodes.

All physical scenes reuse promoted original Stage6A **TRAIN** assets read-only.  No
original DEV or TEST route, runtime fixture, evaluator label, or episode is used.
This is a controlled two-town distribution (Town03/Town05) over the two mechanism
families already proven with real runtime evidence: REFERENTIAL white-van cases and
TEMPORAL bus-clearance cases.  It does not claim arbitrary language, landmark,
spatial-order, object-class, town, or weather generalization.

## Runtime/evaluator separation

The policy manifest contains raw instruction, source TRAIN physical binding, seed,
route, and method only.  Expected mechanism, gold referents, targets, answers, and
candidate identities live only in the evaluator catalog and are joined after an
episode terminates.  Every execution receipt reports all gold/privileged counters.

## Gates

1. E0: static population/freeze validation.
2. E1: one ACT + one ASK + one WAIT scenario across six methods (18 episodes).
3. E2: Grounded V1 reaches at least three completed natural ACT, ASK, and WAIT
   episodes with zero forced decisions.
4. E3: complete/account for the remaining frozen TRAIN schedule to 216 episodes.

The stage stops after TRAIN.  DEV attempts remain zero; TEST attempts remain zero
and TEST remains unconsumed.

## Claims boundary

All output is engineering diagnosis labeled TRAIN ONLY.  No H1/H2/H3 verdict,
significance claim, universal language-understanding claim, or real-time DINO claim
is permitted before separately authorized DEV/TEST execution.
"""


def materialize(*, overwrite_ledger: bool = False) -> Mapping[str, Any]:
    report_root = ROOT / REPORT_ROOT
    artifact_root = ROOT / ARTIFACT_ROOT
    report_root.mkdir(parents=True, exist_ok=True)
    artifact_root.mkdir(parents=True, exist_ok=True)
    population = build_population()
    sources = _source_bindings(population)
    runtime_rows = [
        _runtime_row(row, sources[str(row["source_train_scenario_id"])])
        for row in population
    ]
    runtime_train = [row for row in runtime_rows if row["split"] == "train"]
    assert len(runtime_train) == 12
    assert_exact_methods(EXTENSION_METHODS)

    catalog = {
        "schema_version": SCHEMA_PREFIX + ".scenario_catalog.v1",
        "experiment_id": EXPERIMENT_ID,
        "population_frozen": True,
        "scenario_count": 24,
        "controlled_distribution": True,
        "scope": {
            "ambiguity_types": ["REFERENTIAL", "TEMPORAL"],
            "referent_phrases": ["white van", "bus"],
            "towns": ["Town03", "Town05"],
            "universal_language_understanding_claimed": False,
        },
        "records": population,
    }
    split = {
        "schema_version": SCHEMA_PREFIX + ".split.v1",
        "experiment_id": EXPERIMENT_ID,
        "counts": {split: sum(row["split"] == split for row in population) for split in ("train", "dev", "test")},
        "mechanism_balance": {
            split: dict(
                Counter(
                    row["evaluator_labels"]["expected_mechanism"]
                    for row in population
                    if row["split"] == split
                )
            )
            for split in ("train", "dev", "test")
        },
        "scenario_ids": {
            split: [row["scenario_id"] for row in population if row["split"] == split]
            for split in ("train", "dev", "test")
        },
        "dev_payload_runtime_access": "SEALED_RUNNER_REJECTS_NON_TRAIN",
        "test_payload_runtime_access": "SEALED_RUNNER_REJECTS_NON_TRAIN",
        "extension_dev_attempt_count": 0,
        "extension_test_attempt_count": 0,
        "extension_test_consumed": False,
    }
    methods = {
        "schema_version": SCHEMA_PREFIX + ".methods.v1",
        "exact_method_count": 6,
        "method_order": list(EXTENSION_METHODS),
        "frozen_baselines_reused_without_retuning": True,
        "capability_disclosure": {
            "original_simlingo": "raw instruction and ordinary SimLingo inputs; no Grounded V1 resolution",
            "never_ask": "frozen Stage6B-R0 never_ask adapter",
            "always_ask": "frozen Stage6B-R0 always_ask adapter",
            "always_wait": "frozen Stage6B-R0 always_wait adapter",
            "driveclarify_r0": "frozen Stage6B-R0 driveclarify adapter with its original inputs",
            "driveclarify_grounded_v1": "raw instruction + real rgb_0 + legal ego/route context; default-OFF feature flag",
        },
        "only_method_policy_differs_within_scenario_seed": True,
    }
    seeds = {
        "schema_version": SCHEMA_PREFIX + ".seeds.v1",
        "seed_count_per_scenario": 3,
        "scenario_seed_configuration_count": 72,
        "seed_records": [
            {
                "scenario_id": row["scenario_id"],
                "seed_index_to_carla_seed": {
                    str(index): int(seed)
                    for index, seed in enumerate(row["carla_seeds"], start=1)
                },
            }
            for row in runtime_rows
        ],
    }
    metric_contract = {
        "schema_version": SCHEMA_PREFIX + ".metric_contract.v1",
        "unknown_policy": "UNKNOWN_IS_NULL_WITH_REASON_AND_NEVER_SAFE_OR_ZERO",
        "metric_families": {
            "automatic_ambiguity": ["precision", "recall", "false_ambiguity_rate", "missed_ambiguity_rate", "multi_referent_discovery_rate", "single_referent_correctness", "candidate_collapse_rate"],
            "grounding": ["referent_grounding_accuracy", "referent_detection_recall", "false_referent_rate", "top1_grounding_accuracy", "multi_referent_recall"],
            "candidate": ["raw_k", "effective_k", "exact_duplicate", "semantic_duplicate", "grounding_duplicate", "target_duplicate", "material_consequence_divergence"],
            "target_binding": ["success_rate", "unknown_rate", "collapse_rate", "referent_identity", "event_identity", "target_identity", "branch_identity", "maneuver"],
            "temporal": ["track_continuity", "id_switch", "track_loss", "reacquisition", "cleared_precision", "cleared_recall", "premature_cleared", "missed_cleared", "event_latency", "wait_exit_success", "wait_to_replan_success"],
            "decision": ["act_precision", "act_recall", "ask_precision", "ask_recall", "wait_precision", "wait_recall", "macro_f1", "confusion_matrix_3x3"],
            "task": ["route_completion", "goal_correctness", "wrong_goal_execution", "instruction_success"],
            "safety": ["collision", "offroad", "wrong_lane", "red_light", "stop_sign", "minimum_ttc", "near_miss"],
            "interaction": ["query_rate", "unnecessary_query", "missed_query", "answer_resolution", "answer_delay", "wait_duration", "wait_distance", "wait_resolution", "replan_count", "interaction_added_time"],
            "compute": ["grounding_dino_latency", "dino_invocations", "bytetrack_latency", "event_estimator_latency", "language_ambiguity_latency", "candidate_construction_latency", "simlingo_forwards", "decision_latency", "m3_authority_overhead"],
        },
        "material_divergence_logic": "REUSE_FROZEN_CANDIDATE_SENSITIVITY_AND_RMSE_EVIDENCE; NUMERICAL_NONIDENTITY_ALONE_IS_NOT_MATERIAL",
        "train_only_claim_boundary": True,
    }
    firewall = {
        "schema_version": SCHEMA_PREFIX + ".label_firewall.v1",
        "runtime_manifest": _relative(report_root / "EXTENSION_RUNTIME_TRAIN_MANIFEST.json"),
        "evaluator_catalog": _relative(ROOT / CATALOG_PATH),
        "runtime_forbidden_fields": sorted(
            {
                "expected_mechanism", "gold_referents", "gold_target", "gold_candidate", "gold_answer_index"
            }
        ),
        "post_episode_join_only": True,
        "policy_expected_mechanism_reads": 0,
        "policy_gold_referent_reads": 0,
        "policy_gold_target_reads": 0,
        "policy_gold_candidate_reads": 0,
        "policy_gold_answer_index_reads": 0,
        "runtime_privileged_actor_reads": 0,
        "extension_dev_attempt_count": 0,
        "extension_test_attempt_count": 0,
        "extension_test_consumed": False,
    }
    visualization = {
        "schema_version": SCHEMA_PREFIX + ".visualization_contract.v1",
        "title": "DriveClarify Grounded V1 Runtime",
        "native_physical_display_required_for_representatives": True,
        "carla_native_window_simultaneous": True,
        "display": ":1",
        "headless_forbidden": True,
        "xvfb_forbidden": True,
        "vnc_forbidden": True,
        "render_off_screen_forbidden": True,
        "carla_no_rendering_mode": False,
        "representatives": ["ACT", "ASK", "WAIT"],
        "passive": {
            "visualization_induced_grounding_dino_forwards": 0,
            "visualization_induced_simlingo_forwards": 0,
            "extra_pid": 0,
            "extra_planner_advance": 0,
            "extra_vehicle_control_mutation": 0,
        },
        "required_questions": [
            "WHAT DOES THE CAR SEE?", "WHAT ARE THE POSSIBLE MEANINGS?", "HOW WOULD EACH MEANING CHANGE THE DRIVE?", "WHY ACT / ASK / WAIT?", "WHO IS ACTUALLY CONTROLLING THE CAR?"
        ],
    }
    static_audit = {
        "schema_version": SCHEMA_PREFIX + ".static_population_audit.v1",
        "status": "PASS_STATIC_POPULATION_CONTRACT",
        "counts": {
            "scenarios": len(population),
            "train": len(runtime_train),
            "dev": sum(row["split"] == "dev" for row in population),
            "test": sum(row["split"] == "test" for row in population),
            "act": sum(row["evaluator_labels"]["expected_mechanism"] == "ACT" for row in population),
            "ask": sum(row["evaluator_labels"]["expected_mechanism"] == "ASK" for row in population),
            "wait": sum(row["evaluator_labels"]["expected_mechanism"] == "WAIT" for row in population),
        },
        "ambiguity_types": dict(Counter(row["ambiguity_type"] for row in population)),
        "towns": dict(Counter(row["town"] for row in population)),
        "referent_classes": {"white van": 12, "bus": 12},
        "target_divergent_ask_scenarios": 8,
        "single_referent_act_scenarios": 4,
        "ambiguous_equivalent_act_scenarios": 4,
        "limitations": [
            "Only Town03 and Town05 are included because only their promoted TRAIN physical scenes have real mechanism evidence for Grounded V1.",
            "Object classes are limited to white van and bus; unsupported diversity was not manufactured.",
            "Several scenarios share a physical fixture but differ in frozen runtime language and evaluator mechanism identity.",
        ],
        "original_dev_or_test_asset_count": 0,
    }

    atomic_text(report_root / "EXTENSION_EXPERIMENT_PROTOCOL.md", _protocol())
    atomic_json(ROOT / CATALOG_PATH, catalog)
    atomic_json(ROOT / SPLIT_PATH, split)
    atomic_json(ROOT / METHODS_PATH, methods)
    atomic_json(ROOT / SEEDS_PATH, seeds)
    atomic_json(report_root / "EXTENSION_METRIC_CONTRACT.json", metric_contract)
    atomic_json(ROOT / FIREWALL_PATH, firewall)
    atomic_json(report_root / "EXTENSION_VISUALIZATION_CONTRACT.json", visualization)
    atomic_json(
        report_root / "EXTENSION_RUNTIME_TRAIN_MANIFEST.json",
        {
            "schema_version": SCHEMA_PREFIX + ".runtime_train_manifest.v1",
            "split": "train",
            "record_count": 12,
            "records": runtime_train,
            "evaluator_label_fields_present": False,
            "extension_dev_attempt_count": 0,
            "extension_test_attempt_count": 0,
            "extension_test_consumed": False,
        },
    )
    atomic_json(report_root / "EXTENSION_STATIC_POPULATION_AUDIT.json", static_audit)

    ledger_path = ROOT / LEDGER_PATH
    if overwrite_ledger or not ledger_path.exists():
        atomic_json(ledger_path, _initial_ledger(runtime_train))
    else:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        if ledger.get("scheduled_episode_count") != 216:
            raise RuntimeError("EXTENSION_EXISTING_LEDGER_INVALID")

    protected_actual = {
        name: file_sha256(ROOT / path)
        for name, path in PROTECTED_PATHS.items()
        if name in PROTECTED_HASHES
    }
    if any(protected_actual.get(name) != digest for name, digest in PROTECTED_HASHES.items()):
        raise RuntimeError("EXTENSION_PROTECTED_HASH_MISMATCH")

    prefreeze_files = [
        report_root / "EXTENSION_EXPERIMENT_PROTOCOL.md",
        ROOT / CATALOG_PATH,
        ROOT / SPLIT_PATH,
        ROOT / METHODS_PATH,
        ROOT / SEEDS_PATH,
        report_root / "EXTENSION_METRIC_CONTRACT.json",
        ROOT / FIREWALL_PATH,
        report_root / "EXTENSION_VISUALIZATION_CONTRACT.json",
        report_root / "EXTENSION_RUNTIME_TRAIN_MANIFEST.json",
        report_root / "EXTENSION_STATIC_POPULATION_AUDIT.json",
        ledger_path,
    ]
    freeze_payload = {
        "schema_version": SCHEMA_PREFIX + ".preexecution_freeze.v1",
        "experiment_id": EXPERIMENT_ID,
        "status": "PASS_EXTENSION_E0_POPULATION_AND_CONTRACTS_FROZEN",
        "frozen_at_utc": utc_now(),
        "population": {"scenarios": 24, "train": 12, "dev": 6, "test": 6},
        "methods": list(EXTENSION_METHODS),
        "seeds_per_scenario": 3,
        "planned_episodes": {"all": 432, "train_authorized": 216, "dev_forbidden": 108, "test_forbidden": 108},
        "source_bindings": sources,
        "frozen_file_sha256": {_relative(path): file_sha256(path) for path in prefreeze_files},
        "protected_expected": PROTECTED_HASHES,
        "protected_actual": protected_actual,
        "original_stage6b_integrity": "PASS_UNCHANGED",
        "original_dev_attempt_count": 0,
        "original_test_attempt_count": 0,
        "original_test_consumed": False,
        "extension_dev_attempt_count": 0,
        "extension_test_attempt_count": 0,
        "extension_test_consumed": False,
        "feature_flag": FEATURE_FLAG + "=1 only in grounded extension runner",
        "feature_flag_default": "OFF",
    }
    freeze_payload["freeze_payload_sha256"] = canonical_sha256(freeze_payload)
    atomic_json(ROOT / FREEZE_PATH, freeze_payload)

    # Required result/audit paths exist from E0 onward and say exactly what has
    # not yet run.  Finalization replaces these payloads, never the frozen inputs.
    pending = {
        "schema_version": SCHEMA_PREFIX + ".pending_train_artifact.v1",
        "status": "NOT_RUN_PREEXECUTION_FROZEN",
        "train_only": True,
        "scheduled": 216,
        "started": 0,
        "completed": 0,
        "extension_dev_attempt_count": 0,
        "extension_test_attempt_count": 0,
        "extension_test_consumed": False,
    }
    for name in (
        "EXTENSION_TRAIN_RESULTS.json",
        "EXTENSION_TRAIN_DATA_QUALITY_AUDIT.json",
        "EXTENSION_GROUNDING_AUDIT.json",
        "EXTENSION_AMBIGUITY_AUDIT.json",
        "EXTENSION_TARGET_BINDING_AUDIT.json",
        "EXTENSION_ACT_ASK_WAIT_AUDIT.json",
        "EXTENSION_COMPUTE_AUDIT.json",
    ):
        path = report_root / name
        if not path.exists():
            atomic_json(path, {**pending, "artifact": name})
    report_path = report_root / "EXTENSION_TRAIN_REPORT.md"
    if not report_path.exists():
        atomic_text(
            report_path,
            "# Grounded Language V1 Extension E1 — TRAIN Report\n\n"
            "Status: `NOT_RUN_PREEXECUTION_FROZEN`\n\n"
            "TRAIN ONLY. NOT A FINAL PAPER RESULT.\n",
        )

    hashes = {
        _relative(path): {"sha256": file_sha256(path), "bytes": path.stat().st_size}
        for path in sorted(report_root.iterdir())
        if path.is_file() and path.name != "ARTIFACT_HASHES.json"
    }
    atomic_json(
        report_root / "ARTIFACT_HASHES.json",
        {
            "schema_version": SCHEMA_PREFIX + ".artifact_hashes.v1",
            "generated_at_utc": utc_now(),
            "artifacts": hashes,
        },
    )
    return freeze_payload


__all__ = ["atomic_json", "atomic_text", "materialize", "utc_now"]
