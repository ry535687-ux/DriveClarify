#!/usr/bin/env python3
"""Execute the prospective RQ2 actionable-window robustness extension.

Legal order: prepare/freeze templates, generate fresh seeds, execute each cell
once on the accepted controlled native interface, analyze the paired traces,
finalize, and stop.  Historical RQ2 artifacts are read-only dependencies.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import math
import os
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_rq2_actionable_window_robustness_v1.scenes import (
    HISTORICAL_FREEZE_DIGEST,
    HISTORICAL_REPORT,
    SCENE_ORDER,
    build_scenes,
    validate_scenes,
)
from driveclarify_rq2_t.measurement import canonical_sha256
from driveclarify_rq2_t_cg.contracts import assert_no_true_intent
from driveclarify_rq2_t_cg_formal_execution.routes import materialize_route, static_route_admission
from driveclarify_rq2_t_cg_formal_v3.evaluability import (
    EVALUABLE,
    INTEGRITY_INVALID,
    NON_EVALUABLE_NATIVE_NONCOMPLETION,
    ZERO_EXPOSURE_INFRASTRUCTURE_FAILURE,
)
from tools import run_rq2_t_cg_formal_v3 as accepted_v3
from tools.run_rq2_t_cg_v2_calibration import _fresh_batch


STAGE = "RQ2_ACTIONABLE_WINDOW_ROBUSTNESS_EXTENSION_V1"
REPORT = ROOT / "reports" / "driveclarify_rq2_actionable_window_robustness_extension_v1"
SCENES = REPORT / "SCENES"
ROUTES = REPORT / "ROUTES"
RUNS = REPORT / "RUNS"
HISTORICAL_STATUS = "PASS_RQ2_T_CG_FORMAL_V3_B2_SUPPORTED"
HISTORICAL_SEEDS = (2673132990, 3943202574, 1334264660, 4031630664, 3995035356, 2460943988)
CHECKPOINT = Path("/home/buaa/wrh/simlingo/outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt")
REQUIRED_RELEVANT_FIELDS = (
    "E1_INTERPRETATION_VALIDITY",
    "E2_GROUNDING",
    "E4_FUTURE_OBLIGATION_RELATION",
    "E5_ROUTE_LANE_TOPOLOGY_RELATION",
    "E6_CANDIDATE_CONSEQUENCE_DIVERGENCE",
    "E7_SAFETY_RULE_HOLDING",
    "E9_ANSWER_CHANGES_ACTION",
)
FROZEN_SOURCE_PATHS = (
    "driveclarify_rq2_t/measurement.py",
    "driveclarify_rq2_t_cg/contracts.py",
    "driveclarify_rq2_t_cg/interface.py",
    "driveclarify_rq2_t_cg/memory.py",
    "driveclarify_rq2_t_cg/rules.py",
    "driveclarify_rq2_t_v2/memory.py",
    "driveclarify_rq2_t_cg_formal_execution/builder.py",
    "driveclarify_rq2_t_cg_formal_execution/child_admission_v2.py",
    "driveclarify_rq2_t_cg_formal_execution/native_scenario.py",
    "driveclarify_rq2_t_cg_formal_execution/route_binding_v2.py",
    "driveclarify_rq2_t_cg_formal_execution/routes.py",
    "driveclarify_rq2_t_cg_formal_execution/scene_io.py",
    "driveclarify_rq2_t_cg_background_traffic/policy.py",
    "driveclarify_rq2_t_cg_formal_v3/evaluability.py",
    "tools/run_rq2_t_cg_formal_v3.py",
    "tools/run_rq2_actionable_window_robustness_extension_v1.py",
    "driveclarify_rq2_actionable_window_robustness_v1/scenes.py",
)
FINAL_STATUSES = (
    "PASS_RQ2_ACTIONABLE_WINDOW_ROBUSTNESS_EXTENSION_COMPLETE",
    "RQ2_EXTENSION_RETENTION_REPLICATED_ACTIONABLE_GENERALIZATION_NOT_SUPPORTED",
    "RQ2_EXTENSION_ACTIONABLE_WINDOW_SUPPORTED_WITH_FAMILY_HETEROGENEITY",
    "BLOCKED_RQ2_EXTENSION_PRIMARY_EVALUABILITY_FAILURE",
    "BLOCKED_RQ2_EXTENSION_SOURCE_OR_INTEGRITY_FAILURE",
)


def _load(path: Path, default: Any = None) -> Any:
    return default if not path.is_file() else json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(temporary), str(path))


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(text.rstrip() + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(temporary), str(path))


def _append_command(command: str, status: str) -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    with (REPORT / "COMMAND_LOG.md").open("a", encoding="utf-8") as handle:
        handle.write(f"- `{command}` -> `{status}`\n")


def _tree_fingerprint(path: Path) -> Mapping[str, Any]:
    rows = []
    total = 0
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        size = item.stat().st_size
        total += size
        rows.append({"path": str(item.relative_to(path)), "bytes": size, "sha256": _sha(item)})
    return {
        "root": str(path),
        "file_count": len(rows),
        "total_bytes": total,
        "manifest_digest": canonical_sha256(rows),
    }


def _source_snapshot() -> Mapping[str, Any]:
    files = [
        {"path": path, "bytes": (ROOT / path).stat().st_size, "sha256": _sha(ROOT / path)}
        for path in FROZEN_SOURCE_PATHS
    ]
    accepted_source = _load(HISTORICAL_REPORT / "SOURCE_FREEZE_RECEIPT.json", {})
    accepted_file_hashes = {row["path"]: row["sha256"] for row in accepted_source.get("files", [])}
    unchanged_overlap = {
        row["path"]: accepted_file_hashes[row["path"]] == row["sha256"]
        for row in files if row["path"] in accepted_file_hashes
    }
    value = {
        "files": files,
        "checkpoint_path": str(CHECKPOINT),
        "checkpoint_sha256": _sha(CHECKPOINT),
        "accepted_v3_checkpoint_sha256": accepted_source.get("checkpoint_sha256"),
        "accepted_v3_overlap_unchanged": unchanged_overlap,
        "all_accepted_v3_overlap_unchanged": all(unchanged_overlap.values()),
    }
    value["source_contract_digest"] = canonical_sha256(value)
    return value


def _verify_source_snapshot(expected: Mapping[str, Any]) -> Mapping[str, Any]:
    drift = []
    for row in expected.get("files", []):
        path = ROOT / row["path"]
        if not path.is_file() or _sha(path) != row["sha256"]:
            drift.append(str(path))
    if not CHECKPOINT.is_file() or _sha(CHECKPOINT) != expected.get("checkpoint_sha256"):
        drift.append(str(CHECKPOINT))
    return {"pass": not drift, "drift_paths": drift}


def _verify_freeze() -> Mapping[str, Any]:
    freeze = _load(REPORT / "RQ2_EXTENSION_FREEZE_RECEIPT.json", {})
    failures = []
    for row in freeze.get("frozen_artifacts", []):
        path = REPORT / row["path"]
        if not path.is_file() or _sha(path) != row["sha256"]:
            failures.append(row["path"])
    source = _verify_source_snapshot(freeze.get("source_snapshot", {}))
    failures.extend(source["drift_paths"])
    return {"pass": not failures, "failures": failures, "freeze_digest": freeze.get("freeze_digest")}


def _contract() -> Mapping[str, Any]:
    value = {
        "schema_version": "driveclarify.rq2.actionable_window_robustness.contract.v1",
        "stage": STAGE,
        "study_class": "NEW_SUPPORTING_PROSPECTIVE_ROBUSTNESS_EXTENSION",
        "historical_dependency": {
            "status": HISTORICAL_STATUS,
            "freeze_digest": HISTORICAL_FREEZE_DIGEST,
            "unchanged": True,
            "historical_report_tree_writable": False,
        },
        "scientific_question": (
            "Does retained evidence produce and correctly time actionable clarification windows "
            "across new asynchronous REF, LMK, and ORD templates?"
        ),
        "key_distinction": "EVIDENCE_SUFFICIENT_IS_NOT_EQUIVALENT_TO_CLARIFICATION_ACTIONABLE",
        "views": {
            "B1": "accepted current-frame controlled evidence only; no retention",
            "B2": "same current evidence plus accepted legal field-specific retention, TTL, binding, freshness, and invalidation",
            "B3": "historical offline upper-bound semantics unchanged; diagnostic only",
        },
        "mechanism_semantics_changed": False,
        "deadline": {
            "answer_simulation_s": 0.50,
            "answer_to_action_simulation_s": 0.55,
            "control_reserve_simulation_s": 0.15,
            "total_reserved_simulation_s": 1.20,
            "changed": False,
        },
        "firewall": {
            "runtime_forbidden_before_ask": [
                "true passenger intent", "correct answer", "selected candidate",
                "QueryNecessityGold", "privileged future information",
            ],
            "scene_metadata_privilege_use": "controlled-condition certification only",
            "automatic_free_form_grounding_claimed": False,
        },
        "templates": list(SCENE_ORDER),
        "design": {
            "template_count": 6,
            "fresh_seeds_per_template": 4,
            "planned_episode_count": 24,
            "scientific_unit": "scene-template x seed episode",
            "paired_same_trace_B1_B2": True,
            "scientific_retries": 0,
            "seed_replacements": 0,
            "random_background_traffic": False,
            "model_retraining": False,
            "controller_changes": False,
        },
        "analyses": {
            "A_retention": "all 24; episode-paired B1 versus B2 precommitment sufficiency",
            "B_actionable": "12 ACTIONABLE episodes; pooled and REF/LMK/ORD B1 versus B2 windows",
            "C_too_late": "12 TOO-LATE episodes; sufficiency, windows, TTCmt, margins, and late triggers",
            "baselines": ["R-EVIDENCE-ONLY(B2)", "R-TIME-ONLY(NONE)", "R-JOINT(B2)"],
            "uncertainty": "exact paired McNemar plus episode-level risk difference and template-cluster bootstrap",
            "frame_rows_are_independent_samples": False,
        },
        "prospective_interpretation_rules": {
            "retention_replicated": "B2 sufficiency rate > B1 and two-sided exact paired p < 0.05",
            "actionable_generalization_supported": "pooled B2 actionable rate > B1 and B2 actionable windows occur in every REF, LMK, and ORD ACTIONABLE template",
            "family_heterogeneity": "actionable generalization occurs in at least two families including REF or ORD, but family B2 rates are not equal",
            "too_late_distinguished": "B2 becomes sufficient in every TOO-LATE family, evidence-only has at least one too-late trigger, and joint has zero too-late triggers",
            "primary_complete": "24 planned, 24 executed, 24 evaluable, no integrity failure",
            "status_order": [
                "source/integrity failure -> BLOCKED_RQ2_EXTENSION_SOURCE_OR_INTEGRITY_FAILURE",
                "primary evaluability failure -> BLOCKED_RQ2_EXTENSION_PRIMARY_EVALUABILITY_FAILURE",
                "retention replicated but actionable generalization absent -> RQ2_EXTENSION_RETENTION_REPLICATED_ACTIONABLE_GENERALIZATION_NOT_SUPPORTED",
                "actionable supported with heterogeneous family rates -> RQ2_EXTENSION_ACTIONABLE_WINDOW_SUPPORTED_WITH_FAMILY_HETEROGENEITY",
                "otherwise legally complete -> PASS_RQ2_ACTIONABLE_WINDOW_ROBUSTNESS_EXTENSION_COMPLETE",
            ],
        },
        "no_result_driven_template_tuning": True,
        "no_seed_replacement": True,
        "claim_scope": "CONTROLLED_CERTIFIED_GROUNDING_STUDY_ONLY",
    }
    value["contract_digest"] = canonical_sha256(value)
    return value


def prepare() -> Mapping[str, Any]:
    if REPORT.exists():
        raise RuntimeError("RQ2_EXTENSION_REPORT_ALREADY_EXISTS")
    REPORT.mkdir(parents=True, exist_ok=False)
    _write_text(REPORT / "COMMAND_LOG.md", "# Command log\n")
    historical_report = (HISTORICAL_REPORT / "FINAL_REPORT.md").read_text(encoding="utf-8")
    if HISTORICAL_STATUS not in historical_report or HISTORICAL_FREEZE_DIGEST not in historical_report:
        raise RuntimeError("RQ2_EXTENSION_HISTORICAL_DEPENDENCY_MISMATCH")
    historical_before = _tree_fingerprint(HISTORICAL_REPORT)
    source = _source_snapshot()
    scenes = build_scenes()
    certification = validate_scenes(scenes)
    if not certification["status"].startswith("PASS_"):
        raise RuntimeError("RQ2_EXTENSION_TEMPLATE_CERTIFICATION_FAILED")

    SCENES.mkdir()
    ROUTES.mkdir()
    scene_rows = []
    for scene in scenes:
        scene_path = SCENES / (scene["scene_code"] + ".json")
        route_path = ROUTES / (scene["scene_code"] + ".xml")
        _write_json(scene_path, scene)
        admission = materialize_route(scene, route_path)
        if admission.get("status") != "PASS_STATIC_FORMAL_ROUTE_ADMISSION":
            raise RuntimeError("RQ2_EXTENSION_ROUTE_ADMISSION_FAILED:" + scene["scene_code"])
        scene_rows.append({
            "scene_code": scene["scene_code"],
            "scene_id": scene["formal_scene_id"],
            "ambiguity_family": scene["scene_family"],
            "timing_stratum": scene["timing_stratum"],
            "map_route_identity": {
                "town": scene["route"]["town"],
                "route_spec_digest": scene["route"]["route_spec_digest"],
                "native_route_sha256": _sha(route_path),
            },
            "instruction": scene["instruction"],
            "candidate_interpretations": [row["interpretation_text"] for row in scene["candidate_bindings"]],
            "controlled_evidence_fields": scene["relevant_controlled_evidence_fields"],
            "evidence_arrival_mechanism": scene["evidence_arrival_mechanism"],
            "expected_evidence_order_structure": scene["expected_evidence_order_structure"],
            "commitment_point": scene["commitment"],
            "route_owner_binding": scene["route_owner_binding"],
            "scientific_rationale": scene["prospective_timing_rationale"],
            "formal_scene_digest": scene["formal_scene_digest"],
            "scene_path": str(scene_path.relative_to(REPORT)),
            "route_path": str(route_path.relative_to(REPORT)),
            "route_admission": admission,
        })

    contract = _contract()
    manifest = {
        "schema_version": "driveclarify.rq2.actionable_window_robustness.scene_manifest.v1",
        "stage": STAGE,
        "scene_count": 6,
        "scene_order": list(SCENE_ORDER),
        "seeds_generated": 0,
        "scientific_exposures": 0,
        "scenes": scene_rows,
    }
    manifest["manifest_digest"] = canonical_sha256(manifest)
    certification = dict(certification)
    certification["route_admission_pass_count"] = sum(
        row["route_admission"]["status"] == "PASS_STATIC_FORMAL_ROUTE_ADMISSION" for row in scene_rows
    )
    certification["template_certification_digest"] = canonical_sha256(certification)
    assert_no_true_intent({"contract": contract, "manifest": manifest, "certification": certification})
    _write_json(REPORT / "RQ2_EXTENSION_SCIENTIFIC_CONTRACT.json", contract)
    _write_json(REPORT / "RQ2_EXTENSION_SCENE_MANIFEST.json", manifest)
    _write_json(REPORT / "RQ2_EXTENSION_TEMPLATE_CERTIFICATION.json", certification)

    _write_text(REPORT / "RQ2_EXTENSION_SCIENTIFIC_CONTRACT.md", """# RQ2 actionable-window robustness extension scientific contract

This is a new supporting prospective controlled/certified-grounding experiment. It does not reopen, replace, rerun, or reinterpret the accepted historical RQ2 result.

- Historical dependency: `PASS_RQ2_T_CG_FORMAL_V3_B2_SUPPORTED` / unchanged.
- Historical freeze digest: `113843acf014be1db402b78f5618a92a0e5505a35d20079806c879080e4d17d3`.
- Design: six new asynchronous templates × four fresh extension-only seeds = 24 paired B1/B2 episode traces.
- Scientific unit: scene-template × seed episode; frames are diagnostic only.
- B1/B2 evidence semantics, TTL, freshness, invalidation, commitment, TTCmt, and the 1.20 s reserve remain unchanged.
- Scientific distinction: evidence sufficient does not imply clarification actionable.
- Scientific retries and seed replacements are forbidden.
- Runtime true-intent, correct-answer, selected-candidate, QueryNecessityGold, and privileged-future reads are forbidden.
- Claims remain limited to controlled/certified grounding; automatic free-form grounding is not claimed.
""")
    lines = [
        "# RQ2 extension prospective template certification",
        "",
        "All six timing strata were assigned and frozen before extension seed generation or formal exposure.",
        "",
        "| Template | Family | Stratum | Instruction | Commitment (m) |",
        "|---|---|---|---|---:|",
    ]
    for row in scene_rows:
        lines.append(
            f"| {row['scene_code']} | {row['ambiguity_family']} | {row['timing_stratum']} | "
            f"{row['instruction']} | {row['commitment_point']['threshold_m']:.6f} |"
        )
    lines.extend([
        "",
        "`ACTIONABLE` and `TOO-LATE` are prospective timing opportunities, not post-hoc outcome labels. Route/event owners do not read B1, B2, outcomes, or passenger intent. ORD templates retain certified J1/J2 ordering and forward-connectivity evidence.",
    ])
    _write_text(REPORT / "RQ2_EXTENSION_TEMPLATE_CERTIFICATION.md", "\n".join(lines))

    frozen_names = (
        "RQ2_EXTENSION_SCIENTIFIC_CONTRACT.md",
        "RQ2_EXTENSION_SCIENTIFIC_CONTRACT.json",
        "RQ2_EXTENSION_SCENE_MANIFEST.json",
        "RQ2_EXTENSION_TEMPLATE_CERTIFICATION.md",
        "RQ2_EXTENSION_TEMPLATE_CERTIFICATION.json",
    )
    frozen_artifacts = [
        {"path": name, "bytes": (REPORT / name).stat().st_size, "sha256": _sha(REPORT / name)}
        for name in frozen_names
    ] + [
        {"path": str(path.relative_to(REPORT)), "bytes": path.stat().st_size, "sha256": _sha(path)}
        for path in sorted(itertools.chain(SCENES.glob("*.json"), ROUTES.glob("*.xml")))
    ]
    freeze = {
        "schema_version": "driveclarify.rq2.actionable_window_robustness.freeze_receipt.v1",
        "status": "PASS_RQ2_EXTENSION_PROSPECTIVE_CONTRACT_AND_SIX_TEMPLATES_FROZEN",
        "stage": STAGE,
        "historical_dependency": HISTORICAL_STATUS,
        "historical_freeze_digest": HISTORICAL_FREEZE_DIGEST,
        "historical_tree_before": historical_before,
        "contract_digest": contract["contract_digest"],
        "manifest_digest": manifest["manifest_digest"],
        "template_certification_digest": certification["template_certification_digest"],
        "source_snapshot": source,
        "frozen_artifacts": frozen_artifacts,
        "scene_digests": {row["scene_code"]: row["formal_scene_digest"] for row in scene_rows},
        "formal_seed_values_generated_at_freeze": 0,
        "formal_scientific_exposures_at_freeze": 0,
        "scientific_semantics_changed": False,
        "reserve_changed": False,
    }
    freeze["freeze_digest"] = canonical_sha256(freeze)
    _write_json(REPORT / "RQ2_EXTENSION_FREEZE_RECEIPT.json", freeze)
    source_receipt = {
        "schema_version": "driveclarify.rq2.actionable_window_robustness.source_integrity.v1",
        "status": "PRE_FORMAL_SOURCE_INTEGRITY_FROZEN",
        "source_snapshot": source,
        "historical_tree_before": historical_before,
        "historical_tree_after": None,
        "historical_tree_unchanged": None,
        "true_intent_firewall_violations": 0,
    }
    source_receipt["receipt_digest"] = canonical_sha256(source_receipt)
    _write_json(REPORT / "RQ2_EXTENSION_SOURCE_INTEGRITY_RECEIPT.json", source_receipt)
    _append_command("prepare-and-freeze", freeze["status"])
    return freeze


def generate_seeds() -> Mapping[str, Any]:
    if (REPORT / "RQ2_EXTENSION_SEED_FRESHNESS_RECEIPT.json").exists():
        raise RuntimeError("RQ2_EXTENSION_SEEDS_ALREADY_GENERATED")
    verification = _verify_freeze()
    if not verification["pass"]:
        raise RuntimeError("RQ2_EXTENSION_FREEZE_DRIFT:" + ",".join(verification["failures"]))
    if RUNS.exists():
        raise RuntimeError("RQ2_EXTENSION_EXPOSURE_PRECEDED_SEEDS")
    prefixes = [
        f"RQ2EXT-V1-{code}-S{slot:02d}-"
        for slot in range(1, 5) for code in SCENE_ORDER
    ]
    witnesses = list(_fresh_batch(tuple(prefixes)))
    seeds = [int(row["seed"]) for row in witnesses]
    if len(seeds) != 24 or len(set(seeds)) != 24 or set(seeds) & set(HISTORICAL_SEEDS):
        raise RuntimeError("RQ2_EXTENSION_FRESH_SEED_INVARIANT_FAILED")
    cells = []
    for sequence, (prefix, witness) in enumerate(zip(prefixes, witnesses), start=1):
        code = prefix.split("RQ2EXT-V1-", 1)[1].rsplit("-S", 1)[0]
        slot = prefix.rsplit("-S", 1)[1].split("-", 1)[0]
        scene_path = SCENES / (code + ".json")
        route_path = ROUTES / (code + ".xml")
        scene = _load(scene_path)
        cells.append({
            "run_sequence": sequence,
            "cell_id": witness["identity"],
            "scene_code": code,
            "seed_slot": "S" + slot,
            "seed": int(witness["seed"]),
            "engineering_qualification": False,
            "formal_scene_id": scene["formal_scene_id"],
            "formal_scene_digest": scene["formal_scene_digest"],
            "route_spec_digest": scene["route"]["route_spec_digest"],
            "native_route_sha256": _sha(route_path),
            "execution_scene_manifest": str(scene_path.resolve()),
            "native_route_path": str(route_path.resolve()),
            "scientific_retry_allowed_after_exposure": False,
            "seed_substitution_allowed": False,
            "b1_b2_share_underlying_episode_trace": True,
        })
    category_audit = {
        "historical_RQ1_formal": "covered by recursive literal scan of repository and SimLingo roots",
        "historical_RQ2_formal": {"explicit_exclusions": list(HISTORICAL_SEEDS), "covered_by_scan": True},
        "historical_RQ3_formal": "covered by recursive literal scan of repository and SimLingo roots",
        "P0_and_engineering": "covered by recursive literal scan of repository and SimLingo roots",
        "postmortem": "covered by recursive literal scan of repository and SimLingo roots",
        "calibration_and_development": "covered by recursive literal scan of repository and SimLingo roots",
    }
    value = {
        "schema_version": "driveclarify.rq2.actionable_window_robustness.seed_freshness.v1",
        "status": "PASS_24_COMPLETELY_FRESH_EXTENSION_SEEDS",
        "freeze_digest": verification["freeze_digest"],
        "freeze_preceded_seed_generation": True,
        "generated_seed_count": 24,
        "unique_seed_count": len(set(seeds)),
        "fresh_seed_values": seeds,
        "historical_rq2_seed_overlap": sorted(set(seeds) & set(HISTORICAL_SEEDS)),
        "prior_occurrence_count": sum(len(row.get("prior_match_paths", [])) for row in witnesses),
        "generation_witnesses": witnesses,
        "freshness_audit_categories": category_audit,
        "scan_roots": witnesses[0]["freshness_scan_roots"],
        "four_seeds_per_template": {
            code: [row["seed"] for row in cells if row["scene_code"] == code] for code in SCENE_ORDER
        },
        "cells": cells,
        "scientific_retries_permitted": 0,
        "seed_replacements_permitted": 0,
    }
    value["receipt_digest"] = canonical_sha256(value)
    ledger = {
        "schema_version": "driveclarify.rq2.actionable_window_robustness.execution_ledger.v1",
        "status": "READY_FOR_24_FORMAL_EXTENSION_EPISODES",
        "freeze_digest": verification["freeze_digest"],
        "seed_receipt_digest": value["receipt_digest"],
        "planned_episode_count": 24,
        "formal_episode_attempt_count": 0,
        "formal_scientific_exposures": 0,
        "formal_primary_evaluable_count": 0,
        "formal_native_noncompletion_count": 0,
        "formal_integrity_invalid_count": 0,
        "formal_zero_exposure_infrastructure_failure_count": 0,
        "scientific_retry_count": 0,
        "seed_replacement_count": 0,
        "entries": [],
    }
    ledger["ledger_digest"] = canonical_sha256(ledger)
    _write_json(REPORT / "RQ2_EXTENSION_SEED_FRESHNESS_RECEIPT.json", value)
    _write_json(REPORT / "RQ2_EXTENSION_EXECUTION_LEDGER.json", ledger)
    _append_command("generate-and-audit-fresh-seeds", value["status"])
    return value


def _update_ledger(ledger: dict[str, Any]) -> None:
    entries = ledger["entries"]
    ledger["formal_episode_attempt_count"] = len(entries)
    ledger["formal_scientific_exposures"] = sum(bool(row["exposed"]) for row in entries)
    ledger["formal_primary_evaluable_count"] = sum(row["classification"] == EVALUABLE for row in entries)
    ledger["formal_native_noncompletion_count"] = sum(
        row["classification"] == NON_EVALUABLE_NATIVE_NONCOMPLETION for row in entries
    )
    ledger["formal_integrity_invalid_count"] = sum(row["classification"] == INTEGRITY_INVALID for row in entries)
    ledger["formal_zero_exposure_infrastructure_failure_count"] = sum(
        row["classification"] == ZERO_EXPOSURE_INFRASTRUCTURE_FAILURE for row in entries
    )
    ledger["ledger_digest"] = canonical_sha256({key: value for key, value in ledger.items() if key != "ledger_digest"})


def _finalize_blocked(status: str, reason: str) -> Mapping[str, Any]:
    if status not in FINAL_STATUSES or not status.startswith("BLOCKED_"):
        raise ValueError("RQ2_EXTENSION_INVALID_BLOCKED_STATUS")
    ledger = _load(REPORT / "RQ2_EXTENSION_EXECUTION_LEDGER.json", {})
    ledger["status"] = status
    ledger["mandatory_stop_reason"] = reason
    _update_ledger(ledger)
    _write_json(REPORT / "RQ2_EXTENSION_EXECUTION_LEDGER.json", ledger)
    final = {
        "status": status,
        "reason": reason,
        "freeze_digest": _load(REPORT / "RQ2_EXTENSION_FREEZE_RECEIPT.json", {}).get("freeze_digest"),
        "historical_dependency": HISTORICAL_STATUS + " / unchanged",
        "planned_episodes": 24,
        "executed_episodes": ledger.get("formal_episode_attempt_count", 0),
        "evaluable_episodes": ledger.get("formal_primary_evaluable_count", 0),
        "scientific_retries": ledger.get("scientific_retry_count", 0),
        "seed_replacements": ledger.get("seed_replacement_count", 0),
    }
    _write_text(REPORT / "FINAL_REPORT.md", "# RQ2 extension mandatory stop\n\n" + "\n".join(
        f"- {key}: `{value}`" for key, value in final.items()
    ))
    _append_command("mandatory-stop", status)
    return final


def run_formal(wall_timeout_s: float) -> Mapping[str, Any]:
    verification = _verify_freeze()
    if not verification["pass"]:
        return _finalize_blocked(
            "BLOCKED_RQ2_EXTENSION_SOURCE_OR_INTEGRITY_FAILURE",
            "PRE_EXECUTION_FREEZE_OR_SOURCE_DRIFT:" + ",".join(verification["failures"]),
        )
    seed_receipt = _load(REPORT / "RQ2_EXTENSION_SEED_FRESHNESS_RECEIPT.json", {})
    ledger_path = REPORT / "RQ2_EXTENSION_EXECUTION_LEDGER.json"
    ledger = _load(ledger_path, {})
    if seed_receipt.get("status") != "PASS_24_COMPLETELY_FRESH_EXTENSION_SEEDS":
        return _finalize_blocked(
            "BLOCKED_RQ2_EXTENSION_SOURCE_OR_INTEGRITY_FAILURE", "SEED_FRESHNESS_RECEIPT_INVALID"
        )
    if ledger.get("entries"):
        return _finalize_blocked(
            "BLOCKED_RQ2_EXTENSION_SOURCE_OR_INTEGRITY_FAILURE", "FORMAL_EXTENSION_RESUME_WOULD_CONSTITUTE_RETRY"
        )
    accepted_v3.V3_RUNS = RUNS
    for cell in seed_receipt["cells"]:
        attempt_path = RUNS / cell["cell_id"] / "attempt_01"
        if attempt_path.exists():
            return _finalize_blocked(
                "BLOCKED_RQ2_EXTENSION_SOURCE_OR_INTEGRITY_FAILURE", "UNLEDGERED_FORMAL_ATTEMPT_EXISTS"
            )
        try:
            result = accepted_v3._run_one(cell, wall_timeout_s)
        except BaseException as exc:
            return _finalize_blocked(
                "BLOCKED_RQ2_EXTENSION_SOURCE_OR_INTEGRITY_FAILURE",
                f"EXECUTION_OR_EVALUATOR_EXCEPTION:{type(exc).__name__}:{exc}",
            )
        ledger["entries"].append({
            "run_sequence": cell["run_sequence"],
            "cell_id": result["cell_id"],
            "scene_code": result["scene_code"],
            "seed_slot": result["seed_slot"],
            "seed": result["seed"],
            "classification": result["classification"],
            "reason_code": result["reason_code"],
            "primary_evaluable": result["primary_evaluable"],
            "exposed": result["exposed"],
            "commitment_reached": result["commitment_reached"],
            "route_completion_percent": result["route_completion_percent"],
            "scientific_retry": False,
            "seed_replacement": False,
            "record_digest": result["record_digest"],
            "output_path": result["output_path"],
        })
        _update_ledger(ledger)
        ledger["status"] = "FORMAL_EXTENSION_IN_PROGRESS"
        _write_json(ledger_path, ledger)
        if result["classification"] == INTEGRITY_INVALID:
            return _finalize_blocked(
                "BLOCKED_RQ2_EXTENSION_SOURCE_OR_INTEGRITY_FAILURE",
                "FORMAL_EPISODE_INTEGRITY_INVALID:" + result["cell_id"],
            )
        if result["classification"] == ZERO_EXPOSURE_INFRASTRUCTURE_FAILURE:
            return _finalize_blocked(
                "BLOCKED_RQ2_EXTENSION_SOURCE_OR_INTEGRITY_FAILURE",
                "ZERO_EXPOSURE_INFRASTRUCTURE_FAILURE_NO_RETRY:" + result["cell_id"],
            )
    _update_ledger(ledger)
    if len(ledger["entries"]) != 24 or ledger["formal_primary_evaluable_count"] != 24:
        ledger["status"] = "BLOCKED_RQ2_EXTENSION_PRIMARY_EVALUABILITY_FAILURE"
        _write_json(ledger_path, ledger)
        return _finalize_blocked(
            "BLOCKED_RQ2_EXTENSION_PRIMARY_EVALUABILITY_FAILURE",
            "ONE_OR_MORE_PLANNED_EPISODES_NOT_PRIMARY_EVALUABLE",
        )
    ledger["status"] = "PASS_24_OF_24_EXTENSION_EPISODES_PRIMARY_EVALUABLE"
    _write_json(ledger_path, ledger)
    _append_command("execute-24-formal-extension-episodes", ledger["status"])
    return ledger


def _jsonl(path: Path) -> Sequence[Mapping[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _numeric_summary(values: Sequence[Any]) -> Mapping[str, Any]:
    rows = [float(value) for value in values if value is not None]
    return {
        "count": len(rows),
        "values": rows,
        "minimum": min(rows) if rows else None,
        "median": statistics.median(rows) if rows else None,
        "mean": statistics.fmean(rows) if rows else None,
        "maximum": max(rows) if rows else None,
    }


def _mcnemar(rows: Sequence[Mapping[str, Any]], left: str, right: str) -> Mapping[str, Any]:
    pairs = [(bool(row[left]), bool(row[right])) for row in rows]
    n00 = sum(not a and not b for a, b in pairs)
    n01 = sum(not a and b for a, b in pairs)
    n10 = sum(a and not b for a, b in pairs)
    n11 = sum(a and b for a, b in pairs)
    discordant = n01 + n10
    if discordant == 0:
        p = 1.0
    else:
        tail = sum(math.comb(discordant, k) for k in range(min(n01, n10) + 1)) / (2 ** discordant)
        p = min(1.0, 2.0 * tail)
    return {
        "paired_discordance_table": {"B1_false_B2_false": n00, "B1_false_B2_true": n01, "B1_true_B2_false": n10, "B1_true_B2_true": n11},
        "discordant_total": discordant,
        "two_sided_exact_p": p,
    }


def _percentile(values: Sequence[float], probability: float) -> Any:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    fraction = position - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def _template_cluster_interval(
    rows: Sequence[Mapping[str, Any]], left: str, right: str
) -> Mapping[str, Any]:
    by_template: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_template[row["scene_code"]].append(float(bool(row[right])) - float(bool(row[left])))
    templates = sorted(by_template)
    distribution = []
    for sample in itertools.product(range(len(templates)), repeat=len(templates)):
        values = [value for index in sample for value in by_template[templates[index]]]
        distribution.append(statistics.fmean(values))
    observed = [value for values in by_template.values() for value in values]
    leave_one_out = {
        template: statistics.fmean(
            value for name, values in by_template.items() if name != template for value in values
        )
        for template in templates if len(templates) > 1
    }
    return {
        "method": "EXHAUSTIVE_TEMPLATE_CLUSTER_BOOTSTRAP_WITH_REPLACEMENT",
        "cluster_count": len(templates),
        "resample_count": len(distribution),
        "estimate": statistics.fmean(observed) if observed else None,
        "percentile_95_interval": [_percentile(distribution, 0.025), _percentile(distribution, 0.975)],
        "template_episode_counts": {key: len(value) for key, value in by_template.items()},
        "leave_one_template_out_effects": leave_one_out,
    }


def _endpoint(rows: Sequence[Mapping[str, Any]], left: str, right: str) -> Mapping[str, Any]:
    total = len(rows)
    b1 = sum(bool(row[left]) for row in rows)
    b2 = sum(bool(row[right]) for row in rows)
    return {
        "B1": {"numerator": b1, "denominator": total, "rate": b1 / total if total else None},
        "B2": {"numerator": b2, "denominator": total, "rate": b2 / total if total else None},
        "paired_risk_difference_B2_minus_B1": (b2 - b1) / total if total else None,
        "exact_paired_test": _mcnemar(rows, left, right),
        "template_cluster_sensitivity": _template_cluster_interval(rows, left, right),
    }


def _retention_state(field: Mapping[str, Any]) -> Mapping[str, Any]:
    retention = field.get("retention") if isinstance(field.get("retention"), Mapping) else None
    return {
        "status": field.get("status"),
        "freshness_age_simulation_s": field.get("freshness_age_simulation_s"),
        "source_frame_id": field.get("source_frame_id"),
        "source_observation_id": field.get("source_observation_id"),
        "retention": retention,
    }


def _episode_diagnostics(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    output = ROOT / entry["output_path"]
    builder = _load(output / "FORMAL_EPISODE_RESULT.json")
    records = _jsonl(output / "FORMAL_PAIRED_VIEWS.jsonl")
    first_suff = {}
    first_actionable = {}
    for view in ("B1", "B2"):
        sufficient = next((row for row in records if row["views"][view]["EpistemicEvidenceSufficient"]), None)
        opportunity = next((row for row in records if row["views"][view]["ClarificationOpportunity"]), None)
        first_suff[view] = None if sufficient is None else {
            "source_frame_id": sufficient["source_identity"]["source_frame_id"],
            "simulation_time_s": sufficient["source_identity"]["simulation_time_s"],
            "TTCmt_s": sufficient["views"][view]["TTCmt_s"],
            "remaining_margin_s": sufficient["views"][view]["remaining_decision_margin_s"],
            "actionable": sufficient["views"][view]["ClarificationActionable"],
        }
        first_actionable[view] = None if opportunity is None else {
            "source_frame_id": opportunity["source_identity"]["source_frame_id"],
            "simulation_time_s": opportunity["source_identity"]["simulation_time_s"],
            "TTCmt_s": opportunity["views"][view]["TTCmt_s"],
            "remaining_margin_s": opportunity["views"][view]["remaining_decision_margin_s"],
        }
    witnesses = [
        row for row in records
        if row["views"]["B1"]["EpistemicEvidenceSufficient"] is False
        and row["views"]["B2"]["EpistemicEvidenceSufficient"] is True
        and float(row["views"]["B2"]["TTCmt_s"]) > 0.0
    ]
    first_difference = witnesses[0] if witnesses else None
    b2_first_row = next((row for row in records if row["views"]["B2"]["EpistemicEvidenceSufficient"]), None)
    field_states = {} if b2_first_row is None else {
        field_id: _retention_state(b2_first_row["views"]["B2"]["evidence_vector"][field_id])
        for field_id in REQUIRED_RELEVANT_FIELDS
    }
    retained_fields = [
        field_id for field_id, state in field_states.items()
        if isinstance(state.get("retention"), Mapping)
        and state["retention"].get("state") == "RETAINED"
    ]
    provenance = None if first_difference is None else {
        "witness_source_frame_id": first_difference["source_identity"]["source_frame_id"],
        "current_facts": first_difference["current_facts"],
        "B2_field_source_frames": {
            field_id: first_difference["views"]["B2"]["evidence_vector"][field_id]["source_frame_id"]
            for field_id in REQUIRED_RELEVANT_FIELDS
        },
        "B2_field_retention_states": {
            field_id: (
                first_difference["views"]["B2"]["evidence_vector"][field_id].get("retention") or {}
            ).get("state")
            for field_id in REQUIRED_RELEVANT_FIELDS
        },
    }
    rules = builder["rule_results"]
    return {
        "cell_id": entry["cell_id"],
        "scene_code": entry["scene_code"],
        "family": builder["family"],
        "timing_stratum": _load(SCENES / (entry["scene_code"] + ".json"))["timing_stratum"],
        "seed_slot": entry["seed_slot"],
        "seed": entry["seed"],
        "evaluable": entry["classification"] == EVALUABLE,
        "source_row_count": builder["source_row_count"],
        "commitment_time_s": builder["commitment_time_s"],
        "deadline_time_s": builder["deadline_time_s"],
        "B1_precommitment_sufficiency": builder["B1_precommitment_sufficiency"],
        "B2_precommitment_sufficiency": builder["B2_precommitment_sufficiency"],
        "B1_first_sufficiency": first_suff["B1"],
        "B2_first_sufficiency": first_suff["B2"],
        "B1_actionable_window": builder["B1_window_observed"],
        "B2_actionable_window": builder["B2_window_observed"],
        "B1_first_actionable_frame": first_actionable["B1"],
        "B2_first_actionable_frame": first_actionable["B2"],
        "B1_actionable_window_duration_s": builder["B1_window_duration_s"],
        "B2_actionable_window_duration_s": builder["B2_window_duration_s"],
        "B2_retained_evidence_fields_used_at_first_sufficiency": retained_fields,
        "B2_freshness_TTL_state_at_first_sufficiency": field_states,
        "invalidation_observed": builder["invalidation_observed"],
        "invalid_retention_failure": builder["invalid_retention_failure"],
        "B1_false_B2_true_same_frame_witness_count": len(witnesses),
        "first_B2_difference_from_B1": provenance,
        "decision_timing": {
            "evidence_only": rules["R-EVIDENCE-ONLY(B2)"],
            "time_only": rules["R-TIME-ONLY(NONE)"],
            "joint": rules["R-JOINT(B2)"],
        },
        "same_source_identity_all_views": builder["same_source_identity_all_views"],
        "runtime_true_intent_reads": builder["runtime_true_intent_reads"],
        "online_ask_count": builder["online_ask_count"],
        "PID_controller_changes": builder["PID_controller_changes"],
        "scientific_retry": False,
        "seed_replacement": False,
        "output_path": entry["output_path"],
        "episode_result_digest": builder["episode_result_digest"],
    }


def _family_rows(rows: Sequence[Mapping[str, Any]], family: str, stratum: str | None = None) -> list[Mapping[str, Any]]:
    return [
        row for row in rows
        if row["family"] == family and (stratum is None or row["timing_stratum"] == stratum)
    ]


def analyze() -> Mapping[str, Any]:
    ledger = _load(REPORT / "RQ2_EXTENSION_EXECUTION_LEDGER.json", {})
    if ledger.get("status") != "PASS_24_OF_24_EXTENSION_EPISODES_PRIMARY_EVALUABLE":
        return _finalize_blocked(
            "BLOCKED_RQ2_EXTENSION_PRIMARY_EVALUABILITY_FAILURE", "ANALYSIS_REQUIRES_24_PRIMARY_EVALUABLE_EPISODES"
        )
    freeze_verification = _verify_freeze()
    if not freeze_verification["pass"]:
        return _finalize_blocked(
            "BLOCKED_RQ2_EXTENSION_SOURCE_OR_INTEGRITY_FAILURE", "PRE_ANALYSIS_SOURCE_DRIFT"
        )
    rows = [_episode_diagnostics(entry) for entry in ledger["entries"]]
    if len(rows) != 24 or not all(row["evaluable"] for row in rows):
        return _finalize_blocked(
            "BLOCKED_RQ2_EXTENSION_PRIMARY_EVALUABILITY_FAILURE", "EPISODE_RESULT_COVERAGE_INCOMPLETE"
        )
    actionable = [row for row in rows if row["timing_stratum"] == "ACTIONABLE"]
    too_late = [row for row in rows if row["timing_stratum"] == "TOO-LATE"]
    retention = {
        "schema_version": "driveclarify.rq2.actionable_window_robustness.retention_analysis.v1",
        "population": "all 24 new asynchronous extension episodes",
        **_endpoint(rows, "B1_precommitment_sufficiency", "B2_precommitment_sufficiency"),
    }
    retention["retention_replicated"] = bool(
        retention["paired_risk_difference_B2_minus_B1"] > 0
        and retention["exact_paired_test"]["two_sided_exact_p"] < 0.05
    )
    retention["analysis_digest"] = canonical_sha256(retention)

    actionable_analysis = {
        "schema_version": "driveclarify.rq2.actionable_window_robustness.actionable_analysis.v1",
        "population": "three prospectively ACTIONABLE templates x four seeds",
        **_endpoint(actionable, "B1_actionable_window", "B2_actionable_window"),
        "per_family": {
            family: _endpoint(_family_rows(actionable, family), "B1_actionable_window", "B2_actionable_window")
            for family in ("REF", "LMK", "ORD")
        },
        "B2_window_duration_s": _numeric_summary([row["B2_actionable_window_duration_s"] for row in actionable]),
    }
    actionable_analysis["actionable_generalization_supported"] = bool(
        actionable_analysis["paired_risk_difference_B2_minus_B1"] > 0
        and all(actionable_analysis["per_family"][family]["B2"]["numerator"] > 0 for family in ("REF", "LMK", "ORD"))
    )
    family_b2_rates = [actionable_analysis["per_family"][family]["B2"]["rate"] for family in ("REF", "LMK", "ORD")]
    actionable_analysis["family_heterogeneity_observed"] = len(set(family_b2_rates)) > 1
    actionable_analysis["analysis_digest"] = canonical_sha256(actionable_analysis)

    evidence_only_classes = Counter(row["decision_timing"]["evidence_only"]["classification"] for row in rows)
    time_only_classes = Counter(row["decision_timing"]["time_only"]["classification"] for row in rows)
    joint_classes = Counter(row["decision_timing"]["joint"]["classification"] for row in rows)
    baseline = {
        "schema_version": "driveclarify.rq2.actionable_window_robustness.baseline_analysis.v1",
        "population": "same 24 extension traces; no added native episodes or VLA forwards",
        "evidence_only": {
            "classification_counts": dict(evidence_only_classes),
            "premature_unsupported_triggers": evidence_only_classes["PREMATURE_UNSUPPORTED_TRIGGER"],
            "too_late_triggers": evidence_only_classes["TOO_LATE_RULE_TRIGGER"],
        },
        "time_only": {
            "classification_counts": dict(time_only_classes),
            "premature_unsupported_triggers": time_only_classes["PREMATURE_UNSUPPORTED_TRIGGER"],
        },
        "joint": {
            "classification_counts": dict(joint_classes),
            "premature_unsupported_triggers": joint_classes["PREMATURE_UNSUPPORTED_TRIGGER"],
            "too_late_triggers": joint_classes["TOO_LATE_RULE_TRIGGER"],
            "missed_windows_evidence_only_sufficient_too_late": joint_classes["ACTIONABLE_WINDOW_MISSED_EVIDENCE_TOO_LATE"],
        },
        "rule_added_native_episodes": 0,
        "rule_added_vla_forwards": 0,
    }
    baseline["analysis_digest"] = canonical_sha256(baseline)

    too_late_analysis = {
        "schema_version": "driveclarify.rq2.actionable_window_robustness.too_late_analysis.v1",
        "population": "three prospectively TOO-LATE templates x four seeds",
        "B1_sufficiency": {"numerator": sum(row["B1_precommitment_sufficiency"] for row in too_late), "denominator": 12},
        "B2_sufficiency": {"numerator": sum(row["B2_precommitment_sufficiency"] for row in too_late), "denominator": 12},
        "B1_actionable": {"numerator": sum(row["B1_actionable_window"] for row in too_late), "denominator": 12},
        "B2_actionable": {"numerator": sum(row["B2_actionable_window"] for row in too_late), "denominator": 12},
        "first_B2_sufficiency_TTCmt_s": _numeric_summary([
            row["B2_first_sufficiency"]["TTCmt_s"] if row["B2_first_sufficiency"] else None for row in too_late
        ]),
        "remaining_margin_at_first_B2_sufficiency_s": _numeric_summary([
            row["B2_first_sufficiency"]["remaining_margin_s"] if row["B2_first_sufficiency"] else None for row in too_late
        ]),
        "evidence_only_too_late_triggers": sum(
            row["decision_timing"]["evidence_only"]["classification"] == "TOO_LATE_RULE_TRIGGER" for row in too_late
        ),
        "joint_too_late_triggers": sum(
            row["decision_timing"]["joint"]["classification"] == "TOO_LATE_RULE_TRIGGER" for row in too_late
        ),
        "joint_missed_windows": sum(
            row["decision_timing"]["joint"]["classification"] == "ACTIONABLE_WINDOW_MISSED_EVIDENCE_TOO_LATE" for row in too_late
        ),
        "per_family": {
            family: {
                "B1_sufficiency": sum(row["B1_precommitment_sufficiency"] for row in _family_rows(too_late, family)),
                "B2_sufficiency": sum(row["B2_precommitment_sufficiency"] for row in _family_rows(too_late, family)),
                "B1_actionable": sum(row["B1_actionable_window"] for row in _family_rows(too_late, family)),
                "B2_actionable": sum(row["B2_actionable_window"] for row in _family_rows(too_late, family)),
                "denominator": 4,
            }
            for family in ("REF", "LMK", "ORD")
        },
    }
    too_late_analysis["too_late_distinguished"] = bool(
        all(too_late_analysis["per_family"][family]["B2_sufficiency"] > 0 for family in ("REF", "LMK", "ORD"))
        and too_late_analysis["evidence_only_too_late_triggers"] > 0
        and too_late_analysis["joint_too_late_triggers"] == 0
    )
    too_late_analysis["analysis_digest"] = canonical_sha256(too_late_analysis)

    first_suff_distributions = {}
    window_distributions = {}
    for stratum in ("ACTIONABLE", "TOO-LATE"):
        first_suff_distributions[stratum] = {}
        window_distributions[stratum] = {}
        for family in ("REF", "LMK", "ORD"):
            subset = [row for row in rows if row["timing_stratum"] == stratum and row["family"] == family]
            first_suff_distributions[stratum][family] = {
                view: _numeric_summary([
                    row[f"{view}_first_sufficiency"]["TTCmt_s"] if row[f"{view}_first_sufficiency"] else None
                    for row in subset
                ])
                for view in ("B1", "B2")
            }
            window_distributions[stratum][family] = _numeric_summary([
                row["B2_actionable_window_duration_s"] for row in subset
            ])

    family = {
        "schema_version": "driveclarify.rq2.actionable_window_robustness.family_heterogeneity.v1",
        "families": {},
    }
    for family_name in ("REF", "LMK", "ORD"):
        act = _family_rows(actionable, family_name)
        late = _family_rows(too_late, family_name)
        family["families"][family_name] = {
            "actionable_template": [row["scene_code"] for row in act][0],
            "actionable_B1_windows": sum(row["B1_actionable_window"] for row in act),
            "actionable_B2_windows": sum(row["B2_actionable_window"] for row in act),
            "actionable_denominator": 4,
            "too_late_template": [row["scene_code"] for row in late][0],
            "too_late_B1_sufficiency": sum(row["B1_precommitment_sufficiency"] for row in late),
            "too_late_B2_sufficiency": sum(row["B2_precommitment_sufficiency"] for row in late),
            "too_late_B1_actionable": sum(row["B1_actionable_window"] for row in late),
            "too_late_B2_actionable": sum(row["B2_actionable_window"] for row in late),
            "too_late_denominator": 4,
            "B1_false_B2_true_witnesses": sum(row["B1_false_B2_true_same_frame_witness_count"] for row in _family_rows(rows, family_name)),
            "interpretation": (
                "Retained evidence produced an actionable window and the paired late template separated sufficiency from actionability."
                if sum(row["B2_actionable_window"] for row in act) > 0
                and sum(row["B2_precommitment_sufficiency"] for row in late) > 0
                and sum(row["B2_actionable_window"] for row in late) == 0
                else "Observed family behavior is reported without a universal generalization claim."
            ),
        }
    family["actionable_B2_rate_range"] = [min(family_b2_rates), max(family_b2_rates)]
    family["heterogeneity_observed"] = actionable_analysis["family_heterogeneity_observed"]
    family["analysis_digest"] = canonical_sha256(family)

    results = {
        "schema_version": "driveclarify.rq2.actionable_window_robustness.results.v1",
        "stage": STAGE,
        "historical_dependency": HISTORICAL_STATUS + " / unchanged",
        "freeze_digest": freeze_verification["freeze_digest"],
        "planned_episodes": 24,
        "executed_episodes": 24,
        "evaluable_episodes": 24,
        "episodes": rows,
        "overall": {
            "B1_sufficiency": retention["B1"],
            "B2_sufficiency": retention["B2"],
            "paired_sufficiency_effect": retention["paired_risk_difference_B2_minus_B1"],
            "paired_sufficiency_test": retention["exact_paired_test"],
            "actionable_B1_windows": actionable_analysis["B1"],
            "actionable_B2_windows": actionable_analysis["B2"],
            "too_late_B1_sufficiency": too_late_analysis["B1_sufficiency"],
            "too_late_B2_sufficiency": too_late_analysis["B2_sufficiency"],
            "too_late_B1_actionable": too_late_analysis["B1_actionable"],
            "too_late_B2_actionable": too_late_analysis["B2_actionable"],
            "same_frame_B1_false_B2_true_witness_count": sum(row["B1_false_B2_true_same_frame_witness_count"] for row in rows),
        },
        "first_sufficiency_TTCmt_distributions_by_stratum_and_family": first_suff_distributions,
        "B2_actionable_window_duration_distributions": window_distributions,
        "scientific_retries": 0,
        "seed_replacements": 0,
        "RQ2_frozen_semantics_changed": False,
        "reserve_1_20_s_changed": False,
        "true_intent_firewall_violations": sum(row["runtime_true_intent_reads"] for row in rows),
    }
    results["results_digest"] = canonical_sha256(results)
    _write_json(REPORT / "RQ2_EXTENSION_RESULTS.json", results)
    _write_json(REPORT / "RQ2_EXTENSION_RETENTION_ANALYSIS.json", retention)
    _write_json(REPORT / "RQ2_EXTENSION_ACTIONABLE_ANALYSIS.json", actionable_analysis)
    _write_json(REPORT / "RQ2_EXTENSION_TOO_LATE_ANALYSIS.json", too_late_analysis)
    _write_json(REPORT / "RQ2_EXTENSION_BASELINE_ANALYSIS.json", baseline)
    _write_json(REPORT / "RQ2_EXTENSION_FAMILY_HETEROGENEITY.json", family)
    ledger["pre_frozen_analyses_executed"] = True
    ledger["analysis_input_results_digest"] = results["results_digest"]
    ledger["status"] = "PASS_PRE_FROZEN_EXTENSION_ANALYSES_COMPLETE"
    _update_ledger(ledger)
    _write_json(REPORT / "RQ2_EXTENSION_EXECUTION_LEDGER.json", ledger)
    _append_command("run-pre-frozen-analyses", ledger["status"])
    return results


def _build_notebook() -> Mapping[str, Any]:
    import nbformat
    from nbclient import NotebookClient

    path = REPORT / "RQ2_EXTENSION_ANALYSIS.ipynb"
    notebook = nbformat.v4.new_notebook()
    notebook["cells"] = [
        nbformat.v4.new_markdown_cell("## tl;dr\n\nReproducible checks for the frozen 24-episode RQ2 robustness extension."),
        nbformat.v4.new_markdown_cell("## Context & Methods\n\n### Key Assumptions\n\nThe episode is the statistical unit; B1/B2 are paired on one trace; templates are the clustering unit."),
        nbformat.v4.new_markdown_cell("## Data"),
        nbformat.v4.new_code_cell(
            "import json\nfrom pathlib import Path\n"
            "report = Path('.')\n"
            "results = json.loads((report / 'RQ2_EXTENSION_RESULTS.json').read_text())\n"
            "retention = json.loads((report / 'RQ2_EXTENSION_RETENTION_ANALYSIS.json').read_text())\n"
            "actionable = json.loads((report / 'RQ2_EXTENSION_ACTIONABLE_ANALYSIS.json').read_text())\n"
            "too_late = json.loads((report / 'RQ2_EXTENSION_TOO_LATE_ANALYSIS.json').read_text())\n"
            "len(results['episodes'])"
        ),
        nbformat.v4.new_markdown_cell("## Results"),
        nbformat.v4.new_code_cell(
            "assert results['planned_episodes'] == results['executed_episodes'] == results['evaluable_episodes'] == 24\n"
            "assert retention['B1']['denominator'] == retention['B2']['denominator'] == 24\n"
            "assert actionable['B1']['denominator'] == actionable['B2']['denominator'] == 12\n"
            "assert too_late['B1_sufficiency']['denominator'] == too_late['B2_sufficiency']['denominator'] == 12\n"
            "{'B1_sufficiency': retention['B1'], 'B2_sufficiency': retention['B2'], "
            "'paired_effect': retention['paired_risk_difference_B2_minus_B1'], "
            "'exact_p': retention['exact_paired_test']['two_sided_exact_p']}"
        ),
        nbformat.v4.new_markdown_cell("## Takeaways\n\nThe executed outputs above are the authoritative numerical basis; claims remain bounded to controlled/certified grounding."),
    ]
    nbformat.write(notebook, path)
    client = NotebookClient(notebook, timeout=120, kernel_name="python3", resources={"metadata": {"path": str(REPORT)}})
    executed = client.execute()
    nbformat.write(executed, path)
    return {"path": str(path), "executed_top_to_bottom": True, "cell_count": len(executed["cells"]), "sha256": _sha(path)}


def finalize() -> Mapping[str, Any]:
    results = _load(REPORT / "RQ2_EXTENSION_RESULTS.json", {})
    retention = _load(REPORT / "RQ2_EXTENSION_RETENTION_ANALYSIS.json", {})
    actionable = _load(REPORT / "RQ2_EXTENSION_ACTIONABLE_ANALYSIS.json", {})
    late = _load(REPORT / "RQ2_EXTENSION_TOO_LATE_ANALYSIS.json", {})
    baseline = _load(REPORT / "RQ2_EXTENSION_BASELINE_ANALYSIS.json", {})
    family = _load(REPORT / "RQ2_EXTENSION_FAMILY_HETEROGENEITY.json", {})
    ledger = _load(REPORT / "RQ2_EXTENSION_EXECUTION_LEDGER.json", {})
    if not results or ledger.get("formal_primary_evaluable_count") != 24:
        return _finalize_blocked(
            "BLOCKED_RQ2_EXTENSION_PRIMARY_EVALUABILITY_FAILURE", "FINALIZATION_REQUIRES_COMPLETE_ANALYSIS"
        )
    verification = _verify_freeze()
    source_receipt = _load(REPORT / "RQ2_EXTENSION_SOURCE_INTEGRITY_RECEIPT.json")
    historical_after = _tree_fingerprint(HISTORICAL_REPORT)
    historical_unchanged = historical_after == source_receipt["historical_tree_before"]
    firewall_violations = int(results["true_intent_firewall_violations"])
    source_pass = verification["pass"] and historical_unchanged and firewall_violations == 0
    source_receipt.update({
        "status": "PASS_SOURCE_AND_INTEGRITY" if source_pass else "FAIL_SOURCE_OR_INTEGRITY",
        "historical_tree_after": historical_after,
        "historical_tree_unchanged": historical_unchanged,
        "frozen_source_verification": verification,
        "true_intent_firewall_violations": firewall_violations,
        "RQ2_frozen_semantics_changed": False,
        "reserve_1_20_s_changed": False,
    })
    source_receipt["receipt_digest"] = canonical_sha256({key: value for key, value in source_receipt.items() if key != "receipt_digest"})
    _write_json(REPORT / "RQ2_EXTENSION_SOURCE_INTEGRITY_RECEIPT.json", source_receipt)
    if not source_pass:
        return _finalize_blocked(
            "BLOCKED_RQ2_EXTENSION_SOURCE_OR_INTEGRITY_FAILURE", "FINAL_SOURCE_OR_HISTORICAL_TREE_INTEGRITY_FAILURE"
        )
    actionable_positive_families = sum(
        actionable["per_family"][name]["B2"]["numerator"] > actionable["per_family"][name]["B1"]["numerator"]
        for name in ("REF", "LMK", "ORD")
    )
    if retention["retention_replicated"] and not actionable["actionable_generalization_supported"] and actionable_positive_families < 2:
        status = "RQ2_EXTENSION_RETENTION_REPLICATED_ACTIONABLE_GENERALIZATION_NOT_SUPPORTED"
    elif actionable_positive_families >= 2 and actionable["family_heterogeneity_observed"]:
        status = "RQ2_EXTENSION_ACTIONABLE_WINDOW_SUPPORTED_WITH_FAMILY_HETEROGENEITY"
    else:
        status = "PASS_RQ2_ACTIONABLE_WINDOW_ROBUSTNESS_EXTENSION_COMPLETE"

    seeds = _load(REPORT / "RQ2_EXTENSION_SEED_FRESHNESS_RECEIPT.json")
    freeze = _load(REPORT / "RQ2_EXTENSION_FREEZE_RECEIPT.json")
    notebook = _build_notebook()
    artifacts = [
        "RQ2_EXTENSION_SCIENTIFIC_CONTRACT.md",
        "RQ2_EXTENSION_SCIENTIFIC_CONTRACT.json",
        "RQ2_EXTENSION_SCENE_MANIFEST.json",
        "RQ2_EXTENSION_TEMPLATE_CERTIFICATION.md",
        "RQ2_EXTENSION_TEMPLATE_CERTIFICATION.json",
        "RQ2_EXTENSION_FREEZE_RECEIPT.json",
        "RQ2_EXTENSION_SEED_FRESHNESS_RECEIPT.json",
        "RQ2_EXTENSION_EXECUTION_LEDGER.json",
        "RQ2_EXTENSION_RESULTS.json",
        "RQ2_EXTENSION_RETENTION_ANALYSIS.json",
        "RQ2_EXTENSION_ACTIONABLE_ANALYSIS.json",
        "RQ2_EXTENSION_TOO_LATE_ANALYSIS.json",
        "RQ2_EXTENSION_BASELINE_ANALYSIS.json",
        "RQ2_EXTENSION_FAMILY_HETEROGENEITY.json",
        "RQ2_EXTENSION_SOURCE_INTEGRITY_RECEIPT.json",
        "RQ2_EXTENSION_ANALYSIS.ipynb",
        "FINAL_REPORT.md",
        "COMMAND_LOG.md",
    ]
    b1_suff, b2_suff = retention["B1"], retention["B2"]
    act_b1, act_b2 = actionable["B1"], actionable["B2"]
    report_lines = [
        "# DriveClarify RQ2 actionable-window robustness extension final report",
        "",
        f"1. **exact_final_extension_status**: `{status}`",
        f"2. **extension_freeze_digest**: `{freeze['freeze_digest']}`",
        f"3. **historical_RQ2_dependency**: `{HISTORICAL_STATUS} / unchanged`",
        "4. **six_new_template_identities**: " + json.dumps({row["scene_code"]: row["scene_id"] for row in _load(REPORT / "RQ2_EXTENSION_SCENE_MANIFEST.json")["scenes"]}, sort_keys=True),
        "5. **timing_strata**: " + json.dumps({row["scene_code"]: row["timing_stratum"] for row in _load(REPORT / "RQ2_EXTENSION_SCENE_MANIFEST.json")["scenes"]}, sort_keys=True),
        "6. **24_fresh_seed_identities**: " + json.dumps([{"cell_id": row["cell_id"], "scene": row["scene_code"], "seed": row["seed"]} for row in seeds["cells"]]),
        f"7. **planned_executed_evaluable_episodes**: `24 / 24 / 24`",
        f"8. **B1_sufficiency_overall**: `{b1_suff['numerator']}/{b1_suff['denominator']} ({b1_suff['rate']:.6f})`",
        f"9. **B2_sufficiency_overall**: `{b2_suff['numerator']}/{b2_suff['denominator']} ({b2_suff['rate']:.6f})`",
        "10. **B1_vs_B2_paired_sufficiency_effect_test**: " + json.dumps({"risk_difference": retention["paired_risk_difference_B2_minus_B1"], "exact_McNemar": retention["exact_paired_test"], "template_cluster_95_interval": retention["template_cluster_sensitivity"]["percentile_95_interval"]}, sort_keys=True),
        f"11. **actionable_stratum_B1_actionable_windows**: `{act_b1['numerator']}/12`",
        f"12. **actionable_stratum_B2_actionable_windows**: `{act_b2['numerator']}/12`",
        "13. **per_family_actionable_results**: " + json.dumps({name: {"B1": value["B1"]["numerator"], "B2": value["B2"]["numerator"], "denominator": 4} for name, value in actionable["per_family"].items()}, sort_keys=True),
        f"14. **too_late_stratum_B1_sufficiency**: `{late['B1_sufficiency']['numerator']}/12`",
        f"15. **too_late_stratum_B2_sufficiency**: `{late['B2_sufficiency']['numerator']}/12`",
        f"16. **too_late_stratum_B1_actionable**: `{late['B1_actionable']['numerator']}/12`",
        f"17. **too_late_stratum_B2_actionable**: `{late['B2_actionable']['numerator']}/12`",
        "18. **first_sufficiency_TTCmt_distributions_by_stratum_family**: see `RQ2_EXTENSION_RESULTS.json`; " + json.dumps(results["first_sufficiency_TTCmt_distributions_by_stratum_and_family"], sort_keys=True),
        "19. **B2_actionable_window_duration_distributions**: " + json.dumps(results["B2_actionable_window_duration_distributions"], sort_keys=True),
        f"20. **same_frame_B1_false_B2_true_witnesses**: `{results['overall']['same_frame_B1_false_B2_true_witness_count']}`",
        f"21. **evidence_only_premature_too_late_trigger_counts**: `{baseline['evidence_only']['premature_unsupported_triggers']} / {baseline['evidence_only']['too_late_triggers']}`",
        f"22. **time_only_premature_unsupported_count**: `{baseline['time_only']['premature_unsupported_triggers']}`",
        f"23. **joint_premature_unsupported_count**: `{baseline['joint']['premature_unsupported_triggers']}`",
        f"24. **joint_too_late_trigger_count**: `{baseline['joint']['too_late_triggers']}`",
        f"25. **joint_missed_window_count**: `{baseline['joint']['missed_windows_evidence_only_sufficient_too_late']}`",
        "26. **REF_heterogeneity_summary**: " + json.dumps(family["families"]["REF"], sort_keys=True),
        "27. **LMK_heterogeneity_summary**: " + json.dumps(family["families"]["LMK"], sort_keys=True),
        "28. **ORD_heterogeneity_summary**: " + json.dumps(family["families"]["ORD"], sort_keys=True),
        "29. **scientific_retries**: `0`",
        "30. **seed_replacements**: `0`",
        "31. **RQ2_frozen_semantics_changed**: `NO`",
        "32. **1.20_s_reserve_changed**: `NO`",
        f"33. **true_intent_firewall_violations**: `{firewall_violations}`",
        f"34. **source_integrity_status**: `{source_receipt['status']}`; historical report tree unchanged: `{str(historical_unchanged).upper()}`",
        "35. **exact_artifact_paths**: " + json.dumps([str((REPORT / name).resolve()) for name in artifacts], ensure_ascii=False),
        "",
        "The extension is a controlled/certified-grounding robustness study. It does not claim automatic free-form grounding, universal family superiority, or formal safety, and it does not alter the accepted historical RQ2 result.",
    ]
    _write_text(REPORT / "FINAL_REPORT.md", "\n".join(report_lines))
    ledger["status"] = status
    ledger["finalized"] = True
    ledger["notebook_validation"] = notebook
    _update_ledger(ledger)
    _write_json(REPORT / "RQ2_EXTENSION_EXECUTION_LEDGER.json", ledger)
    _append_command("finalize", status)
    # Re-check JSON parseability and exact required artifacts after the final writes.
    required_json = [REPORT / name for name in artifacts if name.endswith(".json")]
    for path in required_json:
        _load(path)
    if any(not (REPORT / name).is_file() for name in artifacts):
        raise RuntimeError("RQ2_EXTENSION_REQUIRED_ARTIFACT_MISSING")
    return {
        "status": status,
        "freeze_digest": freeze["freeze_digest"],
        "historical_dependency": HISTORICAL_STATUS + " / unchanged",
        "planned": 24,
        "executed": 24,
        "evaluable": 24,
        "final_report": str((REPORT / "FINAL_REPORT.md").resolve()),
        "source_integrity": source_receipt["status"],
    }


def run_all(wall_timeout_s: float) -> Mapping[str, Any]:
    prepare()
    generate_seeds()
    ledger = run_formal(wall_timeout_s)
    if str(ledger.get("status", "")).startswith("BLOCKED_"):
        return ledger
    analyzed = analyze()
    if str(analyzed.get("status", "")).startswith("BLOCKED_"):
        return analyzed
    return finalize()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "seed", "run-formal", "analyze", "finalize", "run-all"))
    parser.add_argument("--wall-timeout-seconds", type=float, default=2400.0)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare()
    elif args.command == "seed":
        result = generate_seeds()
    elif args.command == "run-formal":
        result = run_formal(args.wall_timeout_seconds)
    elif args.command == "analyze":
        result = analyze()
    elif args.command == "finalize":
        result = finalize()
    else:
        result = run_all(args.wall_timeout_seconds)
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if str(result.get("status", "")).startswith("PASS_") else 2


if __name__ == "__main__":
    raise SystemExit(main())
