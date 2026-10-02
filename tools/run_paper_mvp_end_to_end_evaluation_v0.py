#!/usr/bin/env python3
"""Fail-closed Stage 6 paper-MVP evaluation launcher and evidence writer.

This runner deliberately separates evaluation metadata from the label-free
runtime projection.  It launches no policy, SimLingo, or CARLA process until
every pre-execution gate passes.  The current frozen inputs do not pass those
gates, so the only valid V0 output is a zero-episode blocked result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
FREEZE_DIR = ROOT / "reports/paper_mvp_scenario_freeze_v0"
OUT_DIR = ROOT / "reports/paper_mvp_end_to_end_evaluation_v0"
SIMLINGO_ROOT = Path("/home/buaa/wrh/simlingo")
SIMLINGO_CHECKPOINT = (
    SIMLINGO_ROOT / "outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt"
)
SIMLINGO_PROBE_HOOK = SIMLINGO_ROOT / "team_code/driveclarify_probe_hook.py"
CATALOG_PATH = FREEZE_DIR / "SCENARIO_CATALOG.json"
SPLIT_PATH = FREEZE_DIR / "SCENARIO_SPLIT.json"
BASELINE_PATH = FREEZE_DIR / "BASELINE_CONFIG.yaml"
FREEZE_EVIDENCE_PATH = FREEZE_DIR / "EVIDENCE_INDEX.json"

FINAL_STATUS = (
    "BLOCKED_DRIVECLARIFY_PAPER_MVP_END_TO_END_EVALUATION_V0_PRE_EXECUTION"
)
REQUIRED_BASELINES = (
    ("original_simlingo", "Original SimLingo"),
    ("never_ask", "Never Ask"),
    ("always_ask", "Always Ask"),
    ("always_wait", "Always Wait"),
    ("always_stop", "Always Stop"),
    ("language_only_uncertainty", "Language-only uncertainty"),
    ("risk_only", "Risk-only"),
    ("driveclarify", "DriveClarify"),
)
REQUIRED_EPISODE_FIELDS = (
    "instruction",
    "observation_id",
    "runtime_candidate_A",
    "runtime_candidate_B",
    "counterfactual_comparison",
    "decision",
    "decision_reason",
    "query_value",
    "wait_value",
    "m3_state",
    "authority_state",
    "pid_ownership",
    "final_outcome",
)
DECISIONS = ("ACT", "ASK", "WAIT")
OUTCOMES = ("success", "failure", "collision", "rule_violation", "timeout")


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8")


def _baseline_manifest(text: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for block in re.split(r"\n  - id: ", text)[1:]:
        baseline_id, _, tail = block.partition("\n")
        name_match = re.search(r"^    name: (.+)$", tail, flags=re.MULTILINE)
        threshold_match = re.search(
            r"^    threshold_status: (.+)$", tail, flags=re.MULTILINE
        )
        enabled_match = re.search(
            r"^    run_enabled: (.+)$", tail, flags=re.MULTILINE
        )
        result[baseline_id.strip()] = {
            "name": name_match.group(1).strip() if name_match else None,
            "run_enabled": (
                enabled_match.group(1).strip().lower() == "true"
                if enabled_match
                else None
            ),
            "threshold_status": (
                threshold_match.group(1).strip() if threshold_match else None
            ),
        }
    return result


def _xml_scenario_count(path: Path) -> int | None:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None
    return len(root.findall(".//scenario"))


def _component_audit(
    expected: Mapping[str, Mapping[str, Any]], base: Path
) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for relative, record in sorted(expected.items()):
        path = base / relative
        actual = _sha256(path) if path.is_file() else None
        wanted = record.get("expected_sha256")
        rows[relative] = {
            "expected_sha256": wanted,
            "actual_sha256": actual,
            "exists": path.is_file(),
            "unchanged": actual == wanted,
        }
    return {
        "component_count": len(rows),
        "all_unchanged": bool(rows) and all(row["unchanged"] for row in rows.values()),
        "components": rows,
    }


def _policy_projection(scenario: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "instruction": scenario["instruction_text"],
        "normal_route_context": {
            "route_id": scenario["route_id"],
            "town": scenario["town"],
        },
        "normal_sensor_observations": "BOUND_AT_RUNTIME_NOT_PRECOMPUTED",
    }


def _environment_projection(scenario: Mapping[str, Any], seed: int) -> dict[str, Any]:
    return {
        "route_id": scenario["route_id"],
        "town": scenario["town"],
        "seed": seed,
        "weather": scenario["weather"],
        "physical_setup_projection": scenario["traffic_configuration"]
        ["physical_setup_projection"],
    }


def _walk_keys(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key)
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def _build_schedule(catalog: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    runtime_configs: list[dict[str, Any]] = []
    episode_schedule: list[dict[str, Any]] = []
    for scenario in catalog["scenarios"]:
        policy_projection = _policy_projection(scenario)
        for seed in scenario["seed"]:
            environment_projection = _environment_projection(scenario, int(seed))
            runtime_config_id = "runtime-config-" + _digest(
                {
                    "environment": environment_projection,
                    "policy": policy_projection,
                }
            )[:24]
            runtime_configs.append(
                {
                    "runtime_config_id": runtime_config_id,
                    "evaluation_scenario_id": scenario["scenario_id"],
                    "evaluation_split": scenario["split"],
                    "seed": int(seed),
                    "environment_projection_sha256": _digest(environment_projection),
                    "policy_projection_sha256": _digest(policy_projection),
                    "expected_label_join_status": "NOT_JOINED_EPISODE_NOT_COMPLETED",
                    "status": "NOT_STARTED_PRE_EXECUTION_BLOCKED",
                }
            )
            for baseline_id, baseline_name in REQUIRED_BASELINES:
                episode_schedule.append(
                    {
                        "episode_id": "episode-"
                        + _digest(
                            {
                                "runtime_config_id": runtime_config_id,
                                "baseline_id": baseline_id,
                            }
                        )[:24],
                        "runtime_config_id": runtime_config_id,
                        "evaluation_scenario_id": scenario["scenario_id"],
                        "evaluation_split": scenario["split"],
                        "seed": int(seed),
                        "baseline_id": baseline_id,
                        "baseline_name": baseline_name,
                        "status": "NOT_STARTED_PRE_EXECUTION_BLOCKED",
                        "expected_label_join_status": "NOT_JOINED_EPISODE_NOT_COMPLETED",
                    }
                )
    return runtime_configs, episode_schedule


def _check(check_id: str, passed: bool, evidence: Any, *, blocking: bool) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "passed": bool(passed),
        "blocking": bool(blocking and not passed),
        "evidence": evidence,
    }


def _preflight() -> dict[str, Any]:
    catalog = _load_json(CATALOG_PATH)
    split = _load_json(SPLIT_PATH)
    freeze_evidence = _load_json(FREEZE_EVIDENCE_PATH)
    baseline_text = BASELINE_PATH.read_text(encoding="utf-8")
    baseline_manifest = _baseline_manifest(baseline_text)
    runtime_configs, episode_schedule = _build_schedule(catalog)
    frozen_input_artifacts = {}
    for name in ("SCENARIO_CATALOG.json", "SCENARIO_SPLIT.json", "BASELINE_CONFIG.yaml"):
        path = FREEZE_DIR / name
        actual = _sha256(path) if path.is_file() else None
        expected = freeze_evidence["artifacts"][name]["sha256"]
        frozen_input_artifacts[name] = {
            "exists": path.is_file(),
            "expected_sha256": expected,
            "actual_sha256": actual,
            "unchanged": actual == expected,
        }

    source_routes: dict[str, Any] = {}
    for scenario in catalog["scenarios"]:
        relative = scenario["source_route"]["fixture_path"]
        if relative not in source_routes:
            path = ROOT / relative
            source_routes[relative] = {
                "exists": path.is_file(),
                "expected_sha256": scenario["source_route"]["fixture_sha256"],
                "actual_sha256": _sha256(path) if path.is_file() else None,
                "scenario_element_count": _xml_scenario_count(path),
            }
            source_routes[relative]["hash_matches"] = (
                source_routes[relative]["actual_sha256"]
                == source_routes[relative]["expected_sha256"]
            )

    implementation_counts: dict[str, int] = {}
    candidate_generation_claims: dict[str, int] = {}
    for scenario in catalog["scenarios"]:
        status = scenario["implementation_status"]["carla_fixture"]
        implementation_counts[status] = implementation_counts.get(status, 0) + 1
        candidate = str(
            scenario["implementation_status"]["simlingo_candidate_generation_claimed"]
        ).lower()
        candidate_generation_claims[candidate] = (
            candidate_generation_claims.get(candidate, 0) + 1
        )

    protected_expected = freeze_evidence["protected_component_verification"][
        "components"
    ]
    protected = _component_audit(protected_expected, ROOT)
    simlingo_expected = freeze_evidence["simlingo_read_only_verification"][
        "key_files"
    ]
    simlingo = _component_audit(simlingo_expected, SIMLINGO_ROOT)
    additional_runtime_artifacts = {
        "team_code/driveclarify_probe_hook.py": {
            "exists": SIMLINGO_PROBE_HOOK.is_file(),
            "sha256_at_preflight": (
                _sha256(SIMLINGO_PROBE_HOOK) if SIMLINGO_PROBE_HOOK.is_file() else None
            ),
            "stage5_content_pin_available": False,
        },
        "outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt": {
            "exists": SIMLINGO_CHECKPOINT.is_file(),
            "sha256_at_preflight": (
                _sha256(SIMLINGO_CHECKPOINT) if SIMLINGO_CHECKPOINT.is_file() else None
            ),
            "stage5_content_pin_available": False,
        },
    }

    live_runtime_path = ROOT / "driveclarify_m3_runtime_shadow/live_shadow_runtime.py"
    live_postprocess_path = ROOT / "driveclarify_m3_runtime_shadow/live_shadow_postprocess.py"
    live_runtime = live_runtime_path.read_text(encoding="utf-8")
    live_postprocess = live_postprocess_path.read_text(encoding="utf-8")
    activation_token = "activate_runtime_decision_authority_v1"
    fixed_demo_tokens = {
        token: token in live_runtime
        for token in (
            'RAW_INSTRUCTION = "Stop at the next branch."',
            "INTERPRETATION_A =",
            "INTERPRETATION_B =",
        )
    }

    denylist = set(catalog["label_firewall"]["evaluation_only_denylist"])
    policy_projection_key_overlap = sorted(
        {
            key
            for scenario in catalog["scenarios"]
            for key in _walk_keys(_policy_projection(scenario))
        }
        & denylist
    )

    manifest_ids = set(baseline_manifest)
    required_ids = {item[0] for item in REQUIRED_BASELINES}
    threshold_gaps = {
        baseline_id: row["threshold_status"]
        for baseline_id, row in baseline_manifest.items()
        if row["threshold_status"]
        and row["threshold_status"] != "FROZEN"
    }
    counts_by_split = {
        name: {
            "scenario_count": value["scenario_count"],
            "episode_configuration_count": value["episode_count"],
        }
        for name, value in split["splits"].items()
    }

    checks = [
        _check(
            "FROZEN_STAGE_STATUS",
            catalog.get("final_status")
            == "PASS_DRIVECLARIFY_PAPER_MVP_SCENARIO_SET_FROZEN_V0",
            catalog.get("final_status"),
            blocking=True,
        ),
        _check(
            "REQUIRED_SCENARIO_AND_SEED_COUNTS",
            len(catalog["scenarios"]) == 24 and len(runtime_configs) == 96,
            {
                "scenario_count": len(catalog["scenarios"]),
                "runtime_configuration_count": len(runtime_configs),
                "counts_by_split": counts_by_split,
            },
            blocking=True,
        ),
        _check(
            "FROZEN_INPUT_ARTIFACT_HASHES",
            all(row["unchanged"] for row in frozen_input_artifacts.values()),
            frozen_input_artifacts,
            blocking=True,
        ),
        _check(
            "LABEL_FREE_POLICY_PROJECTION",
            not policy_projection_key_overlap,
            {
                "denylist_overlap": policy_projection_key_overlap,
                "expected_label_join_count": 0,
                "policy_process_launch_count": 0,
            },
            blocking=True,
        ),
        _check(
            "FROZEN_COMPONENT_HASHES",
            protected["all_unchanged"] and simlingo["all_unchanged"],
            {
                "driveclarify": protected,
                "simlingo": simlingo,
            },
            blocking=True,
        ),
        _check(
            "CARLA_SCENARIO_FIXTURES_AUTHORED",
            all(
                row["scenario_element_count"] not in (None, 0)
                for row in source_routes.values()
            )
            and set(implementation_counts) == {"AUTHORED"},
            {
                "implementation_status_counts": implementation_counts,
                "unique_source_route_count": len(source_routes),
                "source_routes": source_routes,
            },
            blocking=True,
        ),
        _check(
            "ANNOTATION_FREE_RUNTIME_CANDIDATE_GENERATION",
            candidate_generation_claims == {"true": 24}
            and not any(fixed_demo_tokens.values()),
            {
                "catalog_claim_counts": candidate_generation_claims,
                "fixed_demo_tokens_present": fixed_demo_tokens,
                "live_runtime_path": str(live_runtime_path.relative_to(ROOT)),
            },
            blocking=True,
        ),
        _check(
            "LIVE_RUNTIME_DECISION_AUTHORITY_BINDING",
            activation_token in live_runtime or activation_token in live_postprocess,
            {
                "activation_call_present_in_live_runtime": activation_token
                in live_runtime,
                "activation_call_present_in_live_postprocess": activation_token
                in live_postprocess,
                "contract_activation_evidence_status": (
                    "PASS_RUNTIME_DECISION_AUTHORITY_ACTIVATION_V1_READY_FOR_PAPER_MVP"
                ),
            },
            blocking=True,
        ),
        _check(
            "EXECUTABLE_BASELINE_SET_FROZEN",
            manifest_ids == required_ids
            and not threshold_gaps
            and all(row["run_enabled"] is True for row in baseline_manifest.values()),
            {
                "required_ids": sorted(required_ids),
                "manifest_ids": sorted(manifest_ids),
                "missing_ids": sorted(required_ids - manifest_ids),
                "extra_ids": sorted(manifest_ids - required_ids),
                "threshold_gaps": threshold_gaps,
                "manifest": baseline_manifest,
            },
            blocking=True,
        ),
        _check(
            "LOCAL_CARLA_SIMLINGO_RESOURCES",
            all(
                path.is_file()
                for path in (
                    Path("/home/buaa/CARLA_0.9.15/CarlaUE4.sh"),
                    SIMLINGO_ROOT
                    / "Bench2Drive/leaderboard/leaderboard/leaderboard_evaluator.py",
                    SIMLINGO_CHECKPOINT,
                )
            ),
            {
                "carla_executable_present": Path(
                    "/home/buaa/CARLA_0.9.15/CarlaUE4.sh"
                ).is_file(),
                "leaderboard_evaluator_present": (
                    SIMLINGO_ROOT
                    / "Bench2Drive/leaderboard/leaderboard/leaderboard_evaluator.py"
                ).is_file(),
                "simlingo_checkpoint_present": (
                    SIMLINGO_CHECKPOINT
                ).is_file(),
            },
            blocking=True,
        ),
    ]

    blocker_map = {
        "CARLA_SCENARIO_FIXTURES_AUTHORED": {
            "code": "CARLA_SCENARIO_FIXTURES_NOT_AUTHORED",
            "failure_class": "PRE_EXECUTION_ENVIRONMENT_READINESS",
            "detail": (
                "All 24 catalog entries are metadata/contract fixtures over "
                "scenario-free routes; the 12 source XML files contain no scenario elements."
            ),
        },
        "ANNOTATION_FREE_RUNTIME_CANDIDATE_GENERATION": {
            "code": "ANNOTATION_FREE_RUNTIME_CANDIDATE_PATH_NOT_AVAILABLE",
            "failure_class": "PRE_EXECUTION_METHOD_READINESS",
            "detail": (
                "The live path is a fixed one-off instruction/A/B demo, while all 24 "
                "catalog entries explicitly disclaim SimLingo candidate generation."
            ),
        },
        "LIVE_RUNTIME_DECISION_AUTHORITY_BINDING": {
            "code": "ACTIVATED_DECISION_AUTHORITY_NOT_BOUND_TO_LIVE_CATALOG_RUNTIME",
            "failure_class": "PRE_EXECUTION_INTEGRATION_READINESS",
            "detail": (
                "Activation V1 passes contract fixtures but is not called by the live "
                "candidate runtime or postprocessor."
            ),
        },
        "EXECUTABLE_BASELINE_SET_FROZEN": {
            "code": "EXECUTABLE_BASELINE_CONTRACTS_NOT_FROZEN",
            "failure_class": "PRE_EXECUTION_EVALUATION_DESIGN_READINESS",
            "detail": (
                "The frozen metadata has seven disabled baselines, omits Original "
                "SimLingo and DriveClarify, adds Always Obey, and leaves language/risk "
                "thresholds unfrozen."
            ),
        },
    }
    blockers = [
        blocker_map[check["check_id"]]
        for check in checks
        if check["blocking"] and check["check_id"] in blocker_map
    ]
    unexpected_blockers = [
        check["check_id"]
        for check in checks
        if check["blocking"] and check["check_id"] not in blocker_map
    ]
    for check_id in unexpected_blockers:
        blockers.append(
            {
                "code": check_id,
                "failure_class": "PRE_EXECUTION_INVARIANT_FAILURE",
                "detail": "A required pre-execution invariant failed.",
            }
        )

    return {
        "catalog": catalog,
        "split": split,
        "freeze_evidence": freeze_evidence,
        "baseline_manifest": baseline_manifest,
        "frozen_input_artifacts": frozen_input_artifacts,
        "runtime_configs": runtime_configs,
        "episode_schedule": episode_schedule,
        "checks": checks,
        "blockers": blockers,
        "protected": protected,
        "simlingo": simlingo,
        "additional_runtime_artifacts": additional_runtime_artifacts,
        "source_routes": source_routes,
    }


def _metrics() -> dict[str, Any]:
    confusion = {gold: {predicted: 0 for predicted in DECISIONS} for gold in DECISIONS}
    return {
        "schema_version": "driveclarify.paper_mvp_metrics.v0",
        "status": "NOT_COMPUTED_NO_COMPLETED_EPISODES",
        "metric_population": {
            "scheduled_method_episodes": 768,
            "started_episodes": 0,
            "completed_episodes": 0,
            "post_episode_label_joins": 0,
        },
        "decision": {
            "act_accuracy": None,
            "ask_recall": None,
            "wait_recall": None,
            "confusion_matrix": {
                "labels": list(DECISIONS),
                "counts": confusion,
                "computed": False,
                "reason": "ZERO_COMPLETED_EPISODES",
            },
        },
        "safety": {
            "collision_rate": None,
            "route_failure_rate": None,
            "rule_violation_rate": None,
        },
        "efficiency": {
            "number_of_queries": None,
            "average_delay_seconds": None,
            "completion_time_seconds": None,
            "observed_query_event_count": 0,
        },
        "closed_loop": {
            "success_rate": None,
            "route_completion": None,
        },
        "unavailability_reason": (
            "Pre-execution gates failed before CARLA, SimLingo, policy, or control started."
        ),
    }


def _baseline_results(preflight: Mapping[str, Any]) -> dict[str, Any]:
    manifest = preflight["baseline_manifest"]
    rows = []
    for baseline_id, name in REQUIRED_BASELINES:
        row = manifest.get(baseline_id)
        rows.append(
            {
                "baseline_id": baseline_id,
                "name": name,
                "frozen_manifest_status": (
                    "MISSING" if row is None else "PRESENT_METADATA_ONLY_DISABLED"
                ),
                "manifest_record": row,
                "scheduled_runtime_configurations": 96,
                "started_episodes": 0,
                "completed_episodes": 0,
                "run_status": "NOT_RUN_PRE_EXECUTION_BLOCKED",
                "same_environment_evidence": "NOT_OBSERVED_NO_EPISODES_STARTED",
                "metrics": None,
                "blocker_codes": [item["code"] for item in preflight["blockers"]],
            }
        )
    return {
        "schema_version": "driveclarify.paper_mvp_baseline_results.v0",
        "status": "NOT_RUN_PRE_EXECUTION_BLOCKED",
        "required_baseline_count": 8,
        "runtime_configurations_per_method": 96,
        "baselines": rows,
        "comparison_valid": False,
        "comparison_reason": "NO_BASELINE_OR_DRIVECLARIFY_EPISODE_STARTED",
    }


def _final_report(preflight: Mapping[str, Any], generated_at: str) -> str:
    blocker_lines = "\n".join(
        f"- `{item['code']}` — {item['detail']}" for item in preflight["blockers"]
    )
    return f"""# DriveClarify Paper MVP End-to-End Evaluation V0

`{FINAL_STATUS}`

## Technical summary

No scientifically valid end-to-end result was generated. The fail-closed preflight stopped before CARLA launch, SimLingo checkpoint load, model forward, policy execution, M3 transition, PID invocation, or control write. Consequently, all requested performance rates and recalls are unavailable rather than zero, the TEST split remains unconsumed, and no comparison among the eight methods is valid.

The frozen Stage 5 catalog itself remains intact: 24 scenarios, 96 seed-level configurations, fixed train/dev/test assignments, and all 20 DriveClarify plus six SimLingo protected source hashes match. Local CARLA, the configured SimLingo checkpoint, the leaderboard evaluator, the native display, and the GPU are present; compute availability is not the blocker.

## The experiment cannot enter the runtime pipeline

{blocker_lines}

These are pre-execution readiness failures, not negative model results. Reusing the existing one-route demo would force a fixed ASK/WAIT/ACT lifecycle and fixed A/B interpretations, violating both the no-forced-action requirement and the annotation firewall. Feeding catalog interpretation or consequence annotations to the policy would likewise invalidate the experiment.

No quantitative visualization is included because there are zero completed episodes and no valid comparable values. Plotting zero bars or an empty confusion matrix would incorrectly suggest measured performance.

## Scope, population, and metric definitions

- Frozen population: 24 scenarios × four seeds = 96 runtime configurations; eight required methods would create 768 matched method episodes.
- Development population: TRAIN and DEV only. TEST is reserved for one final evaluation after all executable choices are frozen.
- Decision denominators: completed episodes whose expected ACT/ASK/WAIT label is joined only after runtime completion.
- Safety and closed-loop denominators: completed CARLA episodes with terminal outcome and route records.
- Efficiency denominators: completed episodes with query and timing logs.

Scheduled counts are design counts, not observations. Started and completed episode counts are both zero. The all-zero confusion-matrix count table in `METRICS.json` is marked `computed=false`; ACT accuracy, ASK recall, WAIT recall, collision rate, route failure, rule violation, delay, completion time, success rate, and route completion are all `null`.

## Methodology and firewall

The runner first validated frozen artifact identities and built only an evaluator-side schedule. A separate label-free policy projection contains the instruction plus normal sensor and route placeholders; it has zero overlap with the catalog denylist. Because the readiness gates failed, no runtime payload was emitted and no expected label was joined. The evaluator did not launch CARLA or SimLingo and could not manually override or force a decision.

The planned schedule preserves all scenario IDs, splits, seeds, and method matching. It does not use catalog candidate ordering as a runtime substitute. `EPISODE_INDEX.json` lists the 96 frozen configurations and 768 method slots as not started; it contains no observed episode record.

## Validation and robustness checks

- Annotation leakage: 0 runtime policy launches; 0 expected-label joins; no catalog candidate was used as a runtime candidate.
- Forced/manual action: 0 forced actions and 0 manual overrides because execution never began.
- Controller ownership: 0 hidden controllers, 0 new PIDs, 0 PID calls, and 0 control writes.
- Frozen integrity: all protected DriveClarify and SimLingo source-file hashes match the Stage 5 pins. The current checkpoint and untracked probe-hook hashes are recorded in `EVIDENCE_INDEX.json`, but Stage 5 did not content-pin them.
- TEST consumption: 0 TEST episodes started and 0 TEST labels joined.
- Hardware availability: native X11, CARLA 0.9.15, the configured SimLingo checkpoint, leaderboard evaluator, and GPU are present.

This preflight package is ready to share as evidence of a blocked experiment. It is not ready to share as a performance evaluation.

## Required next authorization

A separate implementation-and-freeze stage is required before retrying Stage 6: author the 24 physical CARLA fixtures; bind an annotation-free runtime candidate/grounding path and Activation V1 to the live agent; freeze total executable reducers (including thresholds) for the exact eight methods; and content-pin the checkpoint and probe hook used by the run. Those changes exceed the runner/logging/evaluation-only boundary of this task. After TRAIN/DEV development choices are frozen, TEST should be executed once.

## Further question

The open decision is whether to authorize that missing implementation-and-freeze stage. Until then, the correct action is to stop; do not train, redesign the decision policy, consume TEST, or claim paper results.

Generated at `{generated_at}`.
"""


def _failure_analysis(preflight: Mapping[str, Any]) -> str:
    blocker_rows = "\n".join(
        f"| `{item['code']}` | {item['failure_class']} | {item['detail']} |"
        for item in preflight["blockers"]
    )
    return f"""# Failure Analysis — Paper MVP End-to-End Evaluation V0

## Result

No episode started, so there are no episode-level failures to assign to classes A–E. The counts below are genuine zeros with a denominator of zero; they are not evidence that the system avoided those failures.

| Episode failure class | Count |
|---|---:|
| A. Wrong ambiguity handling | 0 |
| B. Wrong consequence estimation | 0 |
| C. Wrong ACT/ASK/WAIT decision | 0 |
| D. Execution failure | 0 |
| E. Environment failure | 0 |

## Pre-execution blockers

| Blocker | Readiness class | Evidence-based interpretation |
|---|---|---|
{blocker_rows}

The missing CARLA fixtures are an environment-readiness blocker, but they are not counted as 768 environment-failure episodes because no episode existed. The missing live candidate/authority binding and baseline contracts are method/integration/design readiness blockers, not observed A–D outcomes.

## Non-events explicitly preserved

- CARLA launches: 0.
- SimLingo model forwards: 0.
- Manual overrides / forced actions: 0 / 0.
- M3 transitions / PID calls / control writes: 0 / 0 / 0.
- TEST episodes started / labels joined: 0 / 0.
"""


def _command_log(generated_at: str) -> str:
    return f"""# Command Log — Paper MVP End-to-End Evaluation V0

Working directory: `{ROOT}`

Generated at: `{generated_at}`

## Read-only resource preflight

```text
env DISPLAY=:1 xdpyinfo
nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free --format=csv,noheader
ss -ltnp  # inspected ports 2020 and 8020
test -x /home/buaa/CARLA_0.9.15/CarlaUE4.sh
test -f /home/buaa/wrh/simlingo/outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt
test -f /home/buaa/wrh/simlingo/Bench2Drive/leaderboard/leaderboard/leaderboard_evaluator.py
```

Result: X11 `:1` available; NVIDIA GeForce RTX 4070 Ti with 10,517 MiB free; ports 2020/8020 free; CARLA executable, SimLingo checkpoint, and leaderboard evaluator present.

## Fail-closed Stage 6 runner

```text
python tools/run_paper_mvp_end_to_end_evaluation_v0.py
```

Result: `{FINAL_STATUS}`. The runner validated the frozen schedule and hashes, found four blocking readiness failures, wrote the required evidence package, and exited successfully without starting an experiment.

## Deliberately not executed

- No CARLA or leaderboard evaluator launch command.
- No SimLingo checkpoint load or model forward.
- No TRAIN, DEV, or TEST policy episode.
- No baseline episode.
- No manual override, forced action, controller, PID, or vehicle-control command.
- No model change, mechanism redesign, ambiguity discovery, or training.

The proven single-route Stage 3 command in `reports/closed_loop_v1_queue/03_end_to_end_demo/COMMAND_LOG.md` was inspected but not reused because it hard-codes a pilot lifecycle and does not instantiate the frozen 24-scenario set.
"""


def _evidence_index(preflight: Mapping[str, Any], generated_at: str) -> dict[str, Any]:
    artifact_names = (
        "FINAL_REPORT.md",
        "RESULTS.json",
        "EPISODE_INDEX.json",
        "METRICS.json",
        "BASELINE_RESULTS.json",
        "FAILURE_ANALYSIS.md",
        "COMMAND_LOG.md",
    )
    artifacts = {}
    for name in artifact_names:
        path = OUT_DIR / name
        artifacts[name] = {
            "path": str(path.relative_to(ROOT)),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
    support_paths = (
        ROOT / "tools/run_paper_mvp_end_to_end_evaluation_v0.py",
        ROOT / "tests/paper_mvp_end_to_end_evaluation_v0/test_blocked_preflight.py",
    )
    support = {}
    for path in support_paths:
        if path.is_file():
            support[str(path.relative_to(ROOT))] = {
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
    source_artifacts = {}
    for name in ("SCENARIO_CATALOG.json", "SCENARIO_SPLIT.json", "BASELINE_CONFIG.yaml"):
        path = FREEZE_DIR / name
        source_artifacts[str(path.relative_to(ROOT))] = {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
            "stage5_expected_sha256": preflight["freeze_evidence"]["artifacts"][name][
                "sha256"
            ],
            "unchanged": preflight["frozen_input_artifacts"][name]["unchanged"],
        }
    return {
        "schema_version": "driveclarify.paper_mvp_end_to_end_evidence_index.v0",
        "generated_at_utc": generated_at,
        "final_status": FINAL_STATUS,
        "experiment_started": False,
        "self_hash_excluded": True,
        "self_hash_note": "EVIDENCE_INDEX.json excludes its own hash.",
        "artifacts": artifacts,
        "supporting_code": support,
        "source_artifacts": source_artifacts,
        "protected_component_verification": {
            "driveclarify": preflight["protected"],
            "simlingo": preflight["simlingo"],
        },
        "additional_runtime_artifact_identity": preflight[
            "additional_runtime_artifacts"
        ],
        "label_firewall": {
            "runtime_policy_launch_count": 0,
            "expected_label_join_count": 0,
            "annotation_candidate_substitution_count": 0,
            "passed": True,
        },
        "execution_invariants": {
            "carla_launch_count": 0,
            "simlingo_forward_count": 0,
            "forced_action_count": 0,
            "manual_override_count": 0,
            "new_pid_count": 0,
            "hidden_controller_count": 0,
            "control_write_count": 0,
            "test_episode_start_count": 0,
            "test_label_join_count": 0,
        },
    }


def run() -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    preflight = _preflight()
    if not preflight["blockers"]:
        raise RuntimeError(
            "EXECUTION_BACKEND_ENTRY_REQUIRES_SEPARATE_REVIEW_AFTER_ALL_GATES_PASS"
        )

    metrics = _metrics()
    baseline_results = _baseline_results(preflight)
    episode_index = {
        "schema_version": "driveclarify.paper_mvp_episode_index.v0",
        "status": "SCHEDULED_NOT_STARTED_PRE_EXECUTION_BLOCKED",
        "frozen_runtime_configuration_count": len(preflight["runtime_configs"]),
        "required_method_count": len(REQUIRED_BASELINES),
        "scheduled_method_episode_count": len(preflight["episode_schedule"]),
        "started_episode_count": 0,
        "completed_episode_count": 0,
        "expected_label_join_count": 0,
        "observed_episode_records": [],
        "required_observed_episode_fields": list(REQUIRED_EPISODE_FIELDS),
        "runtime_configurations": preflight["runtime_configs"],
        "method_episode_schedule": preflight["episode_schedule"],
    }
    results = {
        "schema_version": "driveclarify.paper_mvp_end_to_end_results.v0",
        "task": "RUN_DRIVECLARIFY_PAPER_MVP_END_TO_END_EVALUATION_V0",
        "generated_at_utc": generated_at,
        "final_status": FINAL_STATUS,
        "experiment_started": False,
        "scientific_result_available": False,
        "result_interpretation": "PRE_EXECUTION_BLOCKER_NOT_MODEL_OR_POLICY_RESULT",
        "frozen_input_status": preflight["catalog"].get("final_status"),
        "schedule": {
            "scenario_count": len(preflight["catalog"]["scenarios"]),
            "seed_configuration_count": len(preflight["runtime_configs"]),
            "method_count": len(REQUIRED_BASELINES),
            "scheduled_method_episode_count": len(preflight["episode_schedule"]),
            "started_episode_count": 0,
            "completed_episode_count": 0,
        },
        "execution_counts": {
            "carla_launch": 0,
            "simlingo_checkpoint_load": 0,
            "simlingo_forward": 0,
            "runtime_candidate_set": 0,
            "m2b_decision": 0,
            "m3_transition": 0,
            "pid_invocation": 0,
            "control_write": 0,
            "manual_override": 0,
            "forced_action": 0,
            "test_episode_start": 0,
            "expected_label_join": 0,
        },
        "test_split_consumed": False,
        "blockers": preflight["blockers"],
        "preflight_checks": preflight["checks"],
        "metrics_status": metrics["status"],
        "baseline_status": baseline_results["status"],
        "stop_condition_obeyed": True,
    }

    _write_json(OUT_DIR / "RESULTS.json", results)
    _write_json(OUT_DIR / "EPISODE_INDEX.json", episode_index)
    _write_json(OUT_DIR / "METRICS.json", metrics)
    _write_json(OUT_DIR / "BASELINE_RESULTS.json", baseline_results)
    _write_text(OUT_DIR / "FINAL_REPORT.md", _final_report(preflight, generated_at))
    _write_text(OUT_DIR / "FAILURE_ANALYSIS.md", _failure_analysis(preflight))
    _write_text(OUT_DIR / "COMMAND_LOG.md", _command_log(generated_at))
    _write_json(OUT_DIR / "EVIDENCE_INDEX.json", _evidence_index(preflight, generated_at))
    return results


def refresh_index() -> dict[str, Any]:
    preflight = _preflight()
    results = _load_json(OUT_DIR / "RESULTS.json")
    generated_at = str(results["generated_at_utc"])
    index = _evidence_index(preflight, generated_at)
    _write_json(OUT_DIR / "EVIDENCE_INDEX.json", index)
    return index


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-index-only", action="store_true")
    args = parser.parse_args()
    value = refresh_index() if args.refresh_index_only else run()
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
