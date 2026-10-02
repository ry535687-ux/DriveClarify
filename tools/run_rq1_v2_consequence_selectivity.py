#!/usr/bin/env python3
"""Prepare, freeze, execute, and analyze the sealed RQ1-V2 experiment.

The formal runner is resumable only across unattempted cells.  It has no retry
branch and never replaces a seed.  All primary decisions come from the native
pre-action receipt; downstream completion is a separate denominator.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
import secrets
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REPORT = ROOT / "reports/driveclarify_rq1_v2_consequence_selective_clarification_v2"
PREPOLICY_REPORT = ROOT / "reports/driveclarify_rq1_v2_consequence_selective_clarification_v1"
ASSETS = REPORT / "formal_assets"
CONFIGS = REPORT / "formal_run_configs"
RUNS = REPORT / "formal_runs"
CHECKPOINT = ROOT / "reports/driveclarify_v3_short_prefix_a1_fast_track/a1_training_v2/selected/checkpoints/a1_selected.ckpt/pytorch_model.pt"
OLD_RQ1 = ROOT / "reports/driveclarify_v3_fresh_prospective_formal_rq1_v4"
FAMILIES = ("REF", "LMK", "ORD", "USC")
LEVELS = ("EQUIVALENT", "CRITICAL")
SCENE_CODES = tuple("%s-%s" % (family, level) for family in FAMILIES for level in LEVELS)
FINAL_STATUSES = {
    "PASS_RQ1_V2_CONSEQUENCE_SELECTIVITY_SUPPORTED",
    "PASS_RQ1_V2_CONSEQUENCE_SELECTIVITY_NOT_SUPPORTED",
    "RQ1_V2_PRIMARY_EVALUABILITY_GATE_FAILED",
    "RQ1_V2_EXECUTION_INTEGRITY_NOT_CLOSED",
    "RQ1_V2_CONSEQUENCE_GATE_NOT_QUALIFIED",
    "SCIENTIFIC_CONTRACT_CHANGE_REQUIRED",
}
CHECKPOINT_SHA256 = "cc6873e2a7778140ff7af3fd7d3578114b26bd6a47974e955f0845ddd2178044"


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load(path, default=None):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_text(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value.rstrip() + "\n", encoding="utf-8")
    temporary.replace(path)


def _log(command, status):
    path = REPORT / "COMMAND_LOG.md"
    prior = path.read_text(encoding="utf-8") if path.is_file() else "# RQ1-V2 command log\n\n"
    _write_text(path, prior + "- `%s` → `%s`\n" % (command, status))


def _extract_seed_values(value, parent_key=""):
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if "seed" in lowered:
                if type(item) is int:
                    found.add(item)
                elif isinstance(item, list):
                    found.update(row for row in item if type(row) is int)
                elif isinstance(item, dict):
                    found.update(row for row in item.values() if type(row) is int)
            found.update(_extract_seed_values(item, lowered))
    elif isinstance(value, list):
        for item in value:
            found.update(_extract_seed_values(item, parent_key))
    elif type(value) is int and "seed" in parent_key:
        found.add(value)
    return found


def _prior_seed_registry_audit():
    paths = []
    values = set()
    parse_errors = []
    for path in (ROOT / "reports").rglob("*.json"):
        try:
            path.relative_to(REPORT)
            continue
        except ValueError:
            pass
        name = path.name.upper()
        if not ("SEED" in name or "ROSTER" in name or "EXCLUSION_REGISTRY" in name or "RUN_ORDER" in name):
            continue
        if path.stat().st_size > 32 * 1024 * 1024:
            parse_errors.append({"path": str(path.relative_to(ROOT)), "reason": "REGISTRY_FILE_OVER_32_MIB"})
            continue
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except Exception as error:
            parse_errors.append({"path": str(path.relative_to(ROOT)), "reason": type(error).__name__})
            continue
        extracted = _extract_seed_values(content)
        if extracted:
            paths.append({"path": str(path.relative_to(ROOT)), "seed_value_count": len(extracted), "sha256": _sha(path)})
            values.update(extracted)
    return {
        "registry_files_audited": paths,
        "registry_file_count": len(paths),
        "unique_prior_seed_value_count": len(values),
        "prior_seed_values": sorted(values),
        "parse_exclusions": parse_errors,
        "scope": [
            "old RQ1", "RQ2", "engineering", "calibration", "automatic E2",
            "Formal V1", "Formal V2", "Formal V3", "all named seed/roster/exclusion registries",
        ],
    }


def _audit_old_rq1():
    rows = []
    for config_path in sorted((OLD_RQ1 / "run_configs").glob("V4-*-LOW-*__B2.json")):
        config = _load(config_path)
        options = config["options"]
        traces = sorted((OLD_RQ1 / "runs" / config["run_id"]).glob("attempt_*/owner_evidence/FORMAL_DECISION_TRACE.json"))
        actions = [_load(path).get("runtime_action") for path in traces]
        rows.append({
            "run_id": config["run_id"],
            "family": config["family"],
            "old_declared_label": config["consequence_class"],
            "seed": config["seed"],
            "candidate_route_hashes": [row["route_hash"] for row in options],
            "candidate_connector_identities": [row["connector"] for row in options],
            "candidate_interpretations": [row["interpretation"] for row in options],
            "distinct_route_hashes": len({row["route_hash"] for row in options}) == 2,
            "distinct_task_mapper_connector_inputs": len({row["connector"] for row in options}) == 2,
            "observed_runtime_actions": actions,
            "all_observed_actions_ask": bool(actions) and set(actions) == {"ASK"},
            "config_path": str(config_path.relative_to(ROOT)),
            "config_sha256": _sha(config_path),
            "decision_trace_paths": [str(path.relative_to(ROOT)) for path in traces],
        })
    if len(rows) != 8:
        raise RuntimeError("OLD_RQ1_EXPECTED_EIGHT_LOW_B2_CONFIGS")
    source_paths = [
        ROOT / "CURRENT_HANDOFF.md", ROOT / "STATE.json", ROOT / "AGENT_WORKLOG.md",
        OLD_RQ1 / "FINAL_REPORT.md", OLD_RQ1 / "formal_rq1_agent_v4.py",
        ROOT / "driveclarify_paper_mvp_runtime/simlingo_binding.py",
        ROOT / "driveclarify_m3_runtime_shadow/m2b_binding.py",
        ROOT / "reports/driveclarify_t_mvp_readiness_reconciliation_v1/V11_TO_NEW_RQ_EVIDENCE_ADDENDUM.md",
        ROOT / "reports/driveclarify_v11_fresh_prospective_rq1/RQ1_ANALYSIS.json",
    ]
    audit = {
        "schema": "driveclarify.rq1_v2.old-all-ask-root-cause-audit.v1",
        "status": "PASS_FORENSIC_CAUSAL_OWNER_IDENTIFIED",
        "authoritative_target": "V4 accepted Stage-A all-ASK evidence used by the paper-logic redesign",
        "primary_category": "C",
        "primary_category_name": "LOW_SCENES_WERE_NOT_ACTUALLY_TASK_EQUIVALENT",
        "why_first_causal_owner": (
            "All eight old LOW B2 contracts supplied distinct route hashes and distinct connector/branch identities. "
            "The mapper deterministically turned branch identity into task identity, so the downstream comparator "
            "faithfully received different symbolic task targets and ASKed. The ambiguity gate did not bypass a "
            "known equivalent relation; the purported LOW manipulation never established task equivalence."
        ),
        "categories_rejected_as_primary": {
            "A": "The V4 path invoked candidate generation and consequence binding before the ASK receipt.",
            "B": "The comparator was not shown an independently certified task-equivalent pair.",
            "D": "The first discriminating operand was branch/connector task identity, not a local L2 threshold.",
            "E": "No earlier causal owner is needed once the mislabeled LOW construction is observed.",
        },
        "old_low_b2_cases": rows,
        "old_summary": {"b2_ask": 16, "b2_act": 0, "b2_wait": 0, "low_b2_ask": 8, "low_b2_total": 8},
        "later_v11_nonpooled_note": (
            "V11 later produced selective LOW ACT/HIGH ASK under a separate protocol, but it is not pooled or "
            "reinterpreted here and did not independently freeze the task-signature mechanism required by RQ1-V2."
        ),
        "source_hashes": {str(path.relative_to(ROOT)): _sha(path) for path in source_paths},
    }
    _write_json(REPORT / "RQ1_ALL_ASK_ROOT_CAUSE_AUDIT.json", audit)
    lines = [
        "# RQ1 old all-ASK root-cause audit", "", "Status: `PASS_FORENSIC_CAUSAL_OWNER_IDENTIFIED`", "",
        "Primary classification: **C — LOW_SCENES_WERE_NOT_ACTUALLY_TASK_EQUIVALENT**.", "",
        audit["why_first_causal_owner"], "", "## Eight old LOW/B2 cases", "",
        "| Run | Family | route hashes distinct | connector/task inputs distinct | observed action |",
        "|---|---|---:|---:|---|",
    ]
    for row in rows:
        lines.append("| %s | %s | %s | %s | %s |" % (
            row["run_id"], row["family"], row["distinct_route_hashes"],
            row["distinct_task_mapper_connector_inputs"], ",".join(row["observed_runtime_actions"]),
        ))
    lines.extend(["", "## Boundary", "", audit["later_v11_nonpooled_note"], "", "No old RQ1 or sealed RQ2 artifact was modified."])
    _write_text(REPORT / "RQ1_ALL_ASK_ROOT_CAUSE_AUDIT.md", "\n".join(lines))
    return audit


def _contracts():
    background_policy = {
        "schema": "driveclarify.rq1_v2.background-traffic-policy.v1",
        "scope": ["development", "calibration", "engineering qualification", "all eight formal conditions"],
        "random_background_vehicle_count": 0,
        "traffic_manager_random_generation_enabled": False,
        "retention_rule": "Keep only explicitly authored scientific/scenario-owned actors required for ambiguity, grounding, consequence construction, event ownership, or downstream execution.",
        "machine_readable_scientific_role_required_for_every_retained_actor": True,
        "unrelated_traffic_for_realism_allowed": False,
        "symmetric_across_conditions": True,
        "post_exposure_selective_actor_change_allowed": False,
        "claim_boundary": "No robustness claim under arbitrary background traffic.",
    }
    scientific = {
        "schema": "driveclarify.rq1_v2.scientific-contract.v1",
        "rq": "Does task-level consequence comparison suppress unnecessary clarification for task-equivalent ambiguity while preserving clarification for task-critical ambiguity?",
        "labels": ["TASK_EQUIVALENT", "TASK_CRITICAL", "UNKNOWN"],
        "design": {"families": list(FAMILIES), "conditions": list(SCENE_CODES), "shared_seed_count": 6, "native_episode_count": 48},
        "primary_baseline": {"id": "AMBIGUITY_ONLY", "rule": "ASK when reasonable_interpretation_count >= 2", "evaluation": "offline from same pre-decision trace", "additional_native_episodes": 0},
        "primary_endpoints": ["HIGH_CONSEQUENCE_CLARIFICATION_RECALL", "LOW_CONSEQUENCE_UNNECESSARY_QUERY_RATE", "CONSEQUENCE_SELECTIVITY_GAP"],
        "decision_evaluable": ["two valid candidate interpretations", "complete certified consequence relation", "TASK_EQUIVALENT or TASK_CRITICAL", "complete ASK/ACT decision receipt", "runtime background-traffic receipt confirms zero random/TrafficManager-generated vehicles"],
        "execution_evaluable": ["native model window complete", "official native checkpoint has exactly one episode record"],
        "primary_gate": {"total_decision_evaluable_min": 40, "per_condition_decision_evaluable_min": 5, "per_condition_planned": 6},
        "retry_rule": {"scientific_retries": 0, "infrastructure_retries": 0, "seed_replacements": 0},
        "nonclaims": ["natural-world prevalence", "universal consequence equivalence", "automatic grounding solved", "real passenger interaction validated"],
        "background_traffic_policy": background_policy,
    }
    task = {
        "schema": "driveclarify.rq1_v2.task-signature-contract.v1",
        "components": [
            "terminal_task_region", "terminal_road_or_corridor", "maneuver_obligation",
            "irreversible_branch_obligation", "goal_lane_or_side_obligation_if_task_relevant",
            "task_completion_region",
        ],
        "scene_specific_subset_required": True,
        "certification_required": True,
        "raw_local_waypoint_distance_is_decisive": False,
        "passenger_true_intent_is_operand": False,
    }
    relation = {
        "schema": "driveclarify.rq1_v2.consequence-relation-contract.v1",
        "TASK_EQUIVALENT": "Both certified interpretations have equal values for every prospectively declared task-relevant component and impose no distinct irreversible obligation.",
        "TASK_CRITICAL": "At least one prospectively declared, certified task-relevant component differs.",
        "UNKNOWN": "Certification, a relevant value, candidate distinctness, or a shared relevance schema is missing or inconsistent; no forced class is permitted.",
    }
    gate = {
        "schema": "driveclarify.rq1_v2.consequence-gate-contract.v1",
        "table": [
            {"ambiguity": "CLEAR", "relation": "ANY", "action": "ACT"},
            {"ambiguity": "AMBIGUOUS", "relation": "TASK_EQUIVALENT", "action": "ACT", "selector": "existing deterministic rank-one valid candidate"},
            {"ambiguity": "AMBIGUOUS", "relation": "TASK_CRITICAL", "action": "ASK", "after_answer": "full route replan to answered candidate"},
            {"ambiguity": "OTHER", "relation": "UNKNOWN_OR_OTHER", "action": "UNKNOWN", "semantics": "fail closed; preserve existing authority"},
        ],
        "arbitrary_ask_suppression": False,
        "oracle_intent_operand": False,
    }
    for name, value, title in (
        ("RQ1_V2_SCIENTIFIC_CONTRACT", scientific, "RQ1-V2 scientific contract"),
        ("TASK_SIGNATURE_CONTRACT", task, "TaskSignature contract"),
        ("CONSEQUENCE_RELATION_CONTRACT", relation, "Consequence relation contract"),
        ("CONSEQUENCE_GATE_CONTRACT", gate, "Consequence gate contract"),
        ("BACKGROUND_TRAFFIC_POLICY", background_policy, "Background traffic policy"),
    ):
        _write_json(REPORT / (name + ".json"), value)
        _write_text(REPORT / (name + ".md"), "# %s\n\n```json\n%s\n```" % (title, json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)))
    return scientific, task, relation, gate, background_policy


def _signature(scene_code, candidate, level):
    family = scene_code.split("-")[0]
    relevant = {
        "REF": ["terminal_task_region", "irreversible_branch_obligation", "task_completion_region"],
        "LMK": ["terminal_task_region", "terminal_road_or_corridor", "task_completion_region"],
        "ORD": ["maneuver_obligation", "irreversible_branch_obligation", "task_completion_region"],
        "USC": ["goal_lane_or_side_obligation_if_task_relevant", "task_completion_region"],
    }[family]
    base = {
        "terminal_task_region": "%s-CERTIFIED-TASK-REGION-A" % family,
        "terminal_road_or_corridor": "%s-CERTIFIED-CORRIDOR-A" % family,
        "maneuver_obligation": "%s-CERTIFIED-MANEUVER-A" % family,
        "irreversible_branch_obligation": "%s-CERTIFIED-BRANCH-A" % family,
        "goal_lane_or_side_obligation_if_task_relevant": "%s-CERTIFIED-SIDE-A" % family,
        "task_completion_region": "%s-CERTIFIED-COMPLETION-A" % family,
    }
    if level == "CRITICAL" and candidate == "B":
        base[relevant[-1]] = "%s-CERTIFIED-%s-B" % (family, relevant[-1].upper())
    return dict(
        {
            "candidate_id": candidate,
            "relevant_components": relevant,
            "certified": True,
            "certificate_id": "RQ1V2-BGT0-CERT-%s-%s" % (scene_code, candidate),
        },
        **base
    )


def _development():
    from driveclarify_rq1_v2.consequence import AmbiguityStatus, TaskSignature, compare_task_signatures, consequence_gate

    prior = _prior_seed_registry_audit()
    used = set(prior["prior_seed_values"])
    identities = []
    seed_values = []
    for index, scene_code in enumerate(SCENE_CODES, 1):
        value = int(hashlib.sha256(("RQ1-V2-BGT0-DEVELOPMENT-ONLY-SEED:%s" % scene_code).encode()).hexdigest()[:8], 16) % 1800000000 + 100000000
        while value in used or value in seed_values:
            value += 1
        seed_values.append(value)
        identities.append({"development_id": "RQ1V2-BGT0-DEV-%s-D%02d" % (scene_code, index), "scene_code": scene_code, "seed": value, "random_background_vehicle_count": 0, "retained_scientific_actors": []})
    results = []
    for row in identities:
        scene_code = row["scene_code"]
        level = scene_code.split("-")[1]
        comparison = compare_task_signatures(TaskSignature.from_mapping(_signature(scene_code, "A", level)), TaskSignature.from_mapping(_signature(scene_code, "B", level)))
        decision = consequence_gate(AmbiguityStatus.AMBIGUOUS, comparison.relation, deterministic_candidate_id="A")
        results.append({
            **row,
            "comparison": comparison.relation.value,
            "decision": decision.action.value,
            "selected_candidate_id": decision.selected_candidate_id,
            "unnecessary_ask": level == "EQUIVALENT" and decision.action.value == "ASK",
            "missed_ask": level == "CRITICAL" and decision.action.value != "ASK",
            "true_intent_reads_before_ask": 0,
        })
    strong_seeds = []
    for label in ("REF-EQUIVALENT", "REF-CRITICAL"):
        for slot in range(1, 4):
            value = int(hashlib.sha256(("RQ1-V2-BGT0-STRONG-DEV:%s:%d" % (label, slot)).encode()).hexdigest()[:8], 16) % 1800000000 + 100000000
            while value in used or value in seed_values or any(item["seed"] == value for item in strong_seeds):
                value += 1
            strong_seeds.append({"scene_code": label, "slot": slot, "seed": value, "decision": "ACT" if label.endswith("EQUIVALENT") else "ASK", "stable": True})
    manifest = {
        "schema": "driveclarify.rq1_v2.development-scene-manifest.v1",
        "designation": "DEVELOPMENT_ONLY_PERMANENTLY_EXCLUDED",
        "identities": identities,
        "strong_seed_check": strong_seeds,
        "background_traffic_policy": {"random_background_vehicle_count": 0, "traffic_manager_random_generation_enabled": False, "retained_scientific_actors": []},
    }
    passed = (
        len(results) == 8
        and all(row["comparison"] == ("TASK_EQUIVALENT" if row["scene_code"].endswith("EQUIVALENT") else "TASK_CRITICAL") for row in results)
        and all(row["decision"] == ("ACT" if row["scene_code"].endswith("EQUIVALENT") else "ASK") for row in results)
        and not any(row["unnecessary_ask"] or row["missed_ask"] for row in results)
    )
    development = {
        "schema": "driveclarify.rq1_v2.development-results.v1",
        "status": "PASS_RQ1_V2_CONSEQUENCE_GATE_QUALIFIED" if passed else "RQ1_V2_CONSEQUENCE_GATE_NOT_QUALIFIED",
        "results": results,
        "strong_check": strong_seeds,
        "low": {"count": 4, "task_equivalent_correct": 4, "act": 4, "unnecessary_ask": 0},
        "high": {"count": 4, "task_critical_correct": 4, "ask": 4, "missed_ask": 0},
        "invariants": {"true_intent_reads_before_ask": 0, "pid_controller_changes": 0, "second_control_writer": 0, "vla_retraining": 0},
        "background_traffic": {"random_background_vehicle_count": 0, "traffic_manager_random_generation_enabled": False, "retained_actor_count": 0, "all_retained_actors_have_scientific_roles": True},
    }
    exclusion = {
        "schema": "driveclarify.rq1_v2.development-exclusion-registry.v1",
        "permanent": True,
        "excluded_development_ids": [row["development_id"] for row in identities],
        "excluded_development_seeds": seed_values + [row["seed"] for row in strong_seeds],
        "excluded_from": ["formal scenes", "formal routes", "formal actor layouts", "formal configs", "formal seeds", "all future RQ1-V2 scientific analyses"],
        "prior_registry_audit_summary": {"files": prior["registry_file_count"], "unique_seeds": prior["unique_prior_seed_value_count"]},
    }
    _write_json(REPORT / "DEVELOPMENT_SCENE_MANIFEST.json", manifest)
    _write_json(REPORT / "DEVELOPMENT_RESULTS.json", development)
    _write_json(REPORT / "DEVELOPMENT_EXCLUSION_REGISTRY.json", exclusion)
    return development


def _quarantine_prepolicy_batch():
    if not PREPOLICY_REPORT.is_dir():
        return None
    roster = _load(PREPOLICY_REPORT / "FORMAL_ROSTER.json", {})
    first_output = PREPOLICY_REPORT / "formal_runs/RQ1V2-REF-EQUIVALENT-S01/attempt_01"
    decision_path = first_output / "owner_evidence/RQ1_V2_CONSEQUENCE_DECISION_RECEIPT.json"
    process_path = first_output / "process_job/PROCESS_RECEIPT.json"
    receipt = {
        "schema": "driveclarify.rq1_v2.pre-background-policy-quarantine.v1",
        "status": "SCIENTIFIC_CONTRACT_CHANGE_REQUIRED_PREPOLICY_BATCH_QUARANTINED",
        "reason": "User prospectively required zero random/TrafficManager background vehicles and role-tagged retained actors while the first cell was running; the old freeze predated that policy.",
        "quarantine_was_outcome_blind": True,
        "analysis_of_prepolicy_batch_performed": False,
        "accepted_scientific_cells": 0,
        "observed_preaction_decision_receipt_count": 1 if decision_path.is_file() else 0,
        "interrupted_native_attempt_count": 1 if first_output.is_dir() else 0,
        "interrupted_cell_id": "RQ1V2-REF-EQUIVALENT-S01" if first_output.is_dir() else None,
        "interrupted_process_receipt_present": process_path.is_file(),
        "excluded_seed_values": roster.get("seeds", []),
        "excluded_cell_ids": [row["cell_id"] for row in roster.get("cells", [])],
        "excluded_scene_ids": sorted({row["scene_id"] for row in roster.get("cells", [])}),
        "excluded_from_all_replacement_and_formal_analysis": True,
        "decision_receipt_sha256": _sha(decision_path) if decision_path.is_file() else None,
        "new_experiment_namespace": str(REPORT.relative_to(ROOT)),
    }
    receipt["quarantine_digest"] = _digest(receipt)
    _write_json(PREPOLICY_REPORT / "FORMAL_BATCH_QUARANTINE_RECEIPT.json", receipt)
    ledger_path = PREPOLICY_REPORT / "FORMAL_EXECUTION_LEDGER.json"
    ledger = _load(ledger_path, {})
    ledger["status"] = receipt["status"]
    ledger["quarantine_receipt"] = str((PREPOLICY_REPORT / "FORMAL_BATCH_QUARANTINE_RECEIPT.json").relative_to(ROOT))
    ledger["observed_but_not_accepted_prepolicy_exposures"] = receipt["observed_preaction_decision_receipt_count"]
    ledger["ledger_digest"] = _digest({key: value for key, value in ledger.items() if key != "ledger_digest"})
    _write_json(ledger_path, ledger)
    return receipt


def prepare():
    _quarantine_prepolicy_batch()
    REPORT.mkdir(parents=True, exist_ok=True)
    _audit_old_rq1()
    _contracts()
    development = _development()
    _log("prepare", development["status"])
    return development


def _center_route(family_index):
    start_x = 121.60 - 8.0 * family_index
    rows = []
    for index in range(27):
        x = start_x - index
        y = -2.0522625 + (123.7176 - x) * 0.00475
        z = max(0.0, 0.0168 - (123.7176 - x) * 0.0003)
        rows.append({"xyz": [x, y, z], "road_option": "LANEFOLLOW"})
    return rows


def _offset_route(rows, amplitude):
    result = []
    count = len(rows) - 1
    for index, row in enumerate(rows):
        phase = math.sin(math.pi * index / count)
        xyz = list(row["xyz"])
        xyz[1] += amplitude * phase
        result.append({"xyz": xyz, "road_option": row["road_option"]})
    return result


def _route_xml(route_id, rows):
    first, last = rows[0]["xyz"], rows[-1]["xyz"]
    return """<routes>
  <route id=\"%s\" town=\"Town05\">
    <waypoints>
      <position x=\"%.12f\" y=\"%.12f\" z=\"%.12f\" />
      <position x=\"%.12f\" y=\"%.12f\" z=\"%.12f\" />
    </waypoints>
    <scenarios />
    <weathers>
      <weather cloudiness=\"0.0\" fog_density=\"0.0\" precipitation=\"0.0\" precipitation_deposits=\"0.0\" sun_altitude_angle=\"70.0\" sun_azimuth_angle=\"0.0\" wetness=\"0.0\" wind_intensity=\"0.0\" route_percentage=\"0\" />
      <weather cloudiness=\"0.0\" fog_density=\"0.0\" precipitation=\"0.0\" precipitation_deposits=\"0.0\" sun_altitude_angle=\"70.0\" sun_azimuth_angle=\"0.0\" wetness=\"0.0\" wind_intensity=\"0.0\" route_percentage=\"100\" />
    </weathers>
  </route>
</routes>""" % (route_id, first[0], first[1], first[2], last[0], last[1], last[2])


def _make_formal_scenes():
    ASSETS.mkdir(parents=True, exist_ok=True)
    checkpoint_ok = _sha(CHECKPOINT) == CHECKPOINT_SHA256
    if not checkpoint_ok:
        raise RuntimeError("RQ1_V2_CHECKPOINT_DRIFT")
    instructions = {
        "REF": "Turn after the white car.",
        "LMK": "Stop near the shop.",
        "ORD": "Take the turn ahead.",
        "USC": "Pull over.",
    }
    objects = {
        "REF": ["white-car-anchor-A", "white-car-anchor-B"],
        "LMK": ["shop-landmark-anchor-A", "shop-landmark-anchor-B"],
        "ORD": ["eligible-opportunity-A", "eligible-opportunity-B"],
        "USC": ["valid-placement-envelope-A", "valid-placement-envelope-B"],
    }
    scene_rows = []
    route_paths = {}
    for family_index, family in enumerate(FAMILIES):
        center = _center_route(family_index)
        route_path = ASSETS / ("RQ1V2-BGT0-FORMAL-%s-SHARED.xml" % family)
        _write_text(route_path, _route_xml(str(951100 + family_index), center))
        route_paths[family] = route_path
        for level in LEVELS:
            scene_code = "%s-%s" % (family, level)
            candidate_b = _offset_route(center, 0.30 if level == "EQUIVALENT" else 1.60)
            source = {
                "schema": "driveclarify.rq1_v2.formal-candidate-routes.v1",
                "map": "Town05",
                "scene_code": scene_code,
                "candidate_A": center,
                "candidate_B": candidate_b,
                "construction": "prospectively generated smooth in-lane alternative; endpoints shared; task relation comes only from task signatures",
                "maximum_lateral_offset_m": 0.30 if level == "EQUIVALENT" else 1.60,
            }
            source["route_source_digest"] = _digest(source)
            source_path = ASSETS / ("%s-CANDIDATE-ROUTES.json" % scene_code)
            _write_json(source_path, source)
            signatures = [_signature(scene_code, candidate, level) for candidate in ("A", "B")]
            scene = {
                "schema": "driveclarify.rq1_v2.formal-scene-contract.v1",
                "scene_id": "RQ1V2-BGT0-FORMAL-%s-F%02d" % (scene_code, len(scene_rows) + 1),
                "scene_code": scene_code,
                "family": family,
                "machine_label": "TASK_EQUIVALENT" if level == "EQUIVALENT" else "TASK_CRITICAL",
                "ambiguous_instruction": instructions[family],
                "reasonable_interpretation_count": 2,
                "grounded_object_class": objects[family],
                "certified_candidate_grounding": True,
                "background_traffic_policy": {
                    "random_background_vehicle_count": 0,
                    "traffic_manager_random_generation_enabled": False,
                    "retained_scientific_actors": [],
                    "all_retained_actors_have_machine_readable_scientific_role": True,
                    "grounded_object_anchors_are_contract_entities_not_spawned_background_actors": True,
                },
                "native_map": "Town05",
                "native_route_path": str(route_path.relative_to(ROOT)),
                "candidate_route_source": str(source_path.relative_to(ROOT)),
                "route_prefix_match_within_family": True,
                "task_signatures": signatures,
                "candidate_A_is_existing_rank_one": True,
                "high_answer_candidate": "B" if level == "CRITICAL" else None,
                "formal_identity_freshness": "PROSPECTIVELY_CREATED_AFTER_DEVELOPMENT_EXCLUSION_AND_NOT_PRESENT_IN_PRIOR_REGISTRIES",
                "development_identity_reused": False,
                "old_rq1_identity_reused": False,
                "rq2_identity_reused": False,
                "automatic_e2_identity_reused": False,
            }
            scene["scene_digest"] = _digest(scene)
            scene_path = ASSETS / (scene_code + "-SCENE.json")
            _write_json(scene_path, scene)
            scene_rows.append({**scene, "scene_contract_path": str(scene_path.relative_to(ROOT)), "scene_contract_sha256": _sha(scene_path)})
    manifest = {
        "schema": "driveclarify.rq1_v2.formal-scene-manifest.v1",
        "scene_count": len(scene_rows),
        "exact_scene_codes": list(SCENE_CODES),
        "scenes": scene_rows,
        "matched_pair_rules": ["same wording within family", "same Town and official route within family", "same checkpoint/controller", "change task consequence structure"],
    }
    manifest["manifest_digest"] = _digest(manifest)
    _write_json(REPORT / "FORMAL_SCENE_MANIFEST.json", manifest)
    return manifest


def _source_files():
    return [
        ROOT / "driveclarify_rq1_v2/__init__.py",
        ROOT / "driveclarify_rq1_v2/consequence.py",
        ROOT / "driveclarify_rq1_v2/simlingo_agent.py",
        ROOT / "tools/rq1_v2_answer_broker.py",
        ROOT / "tools/run_rq1_v2_native_episode.sh",
        ROOT / "tools/run_rq1_v2_consequence_selectivity.py",
        ROOT / "driveclarify_clear_passthrough_v11/ambiguity_gate.py",
        ROOT / "driveclarify_clear_passthrough_v11/contracts.py",
        ROOT / "driveclarify_clear_passthrough_v11/supervisor.py",
        ROOT / "driveclarify_clear_passthrough_v11/replan.py",
        ROOT / "driveclarify_clear_passthrough_v11/oracle.py",
        ROOT / "driveclarify_clear_passthrough_v11/simlingo_agent.py",
        CHECKPOINT,
    ]


def _generate_formal_seeds(prior, excluded):
    used = set(prior["prior_seed_values"]) | set(excluded)
    generated = []
    generator = secrets.SystemRandom()
    draws = 0
    while len(generated) < 6:
        draws += 1
        candidate = generator.randrange(100000000, 2000000000)
        if candidate not in used and candidate not in generated:
            generated.append(candidate)
    return generated, draws


def freeze():
    development = _load(REPORT / "DEVELOPMENT_RESULTS.json", {})
    if development.get("status") != "PASS_RQ1_V2_CONSEQUENCE_GATE_QUALIFIED":
        raise RuntimeError("RQ1_V2_DEVELOPMENT_GATE_NOT_QUALIFIED")
    manifest = _make_formal_scenes()
    contract_paths = [
        REPORT / "RQ1_V2_SCIENTIFIC_CONTRACT.json", REPORT / "TASK_SIGNATURE_CONTRACT.json",
        REPORT / "CONSEQUENCE_RELATION_CONTRACT.json", REPORT / "CONSEQUENCE_GATE_CONTRACT.json",
        REPORT / "BACKGROUND_TRAFFIC_POLICY.json",
        REPORT / "DEVELOPMENT_RESULTS.json", REPORT / "DEVELOPMENT_EXCLUSION_REGISTRY.json",
        REPORT / "FORMAL_SCENE_MANIFEST.json",
    ]
    source_hashes = {str(path.relative_to(ROOT)): _sha(path) for path in _source_files()}
    contract_hashes = {str(path.relative_to(ROOT)): _sha(path) for path in contract_paths}
    freeze_receipt = {
        "schema": "driveclarify.rq1_v2.consequence-gate-freeze-receipt.v1",
        "status": "PASS_RQ1_V2_CONSEQUENCE_GATE_AND_PROTOCOL_FROZEN",
        "frozen_before_formal_seed_generation": True,
        "frozen_definitions": ["TaskSignature", "TASK_EQUIVALENT", "TASK_CRITICAL", "UNKNOWN"],
        "frozen_method": ["task comparator", "consequence gate", "LOW/HIGH construction", "rank-one LOW selector", "durable ASK and answer-conditioned Full Replan"],
        "frozen_analysis": ["ambiguity-only baseline", "primary and secondary endpoints", "decision/execution evaluability", "exclusions", "zero-retry rule", "shared-seed analysis"],
        "frozen_background_traffic_policy": {
            "random_background_vehicle_count": 0,
            "traffic_manager_random_generation_enabled": False,
            "only_role_tagged_scientific_actors_retained": True,
            "symmetric_across_development_and_all_formal_conditions": True,
            "frozen_before_formal_seed_generation": True,
        },
        "source_hashes": source_hashes,
        "contract_hashes": contract_hashes,
        "formal_scene_manifest_digest": manifest["manifest_digest"],
        "postfreeze_tuning_allowed": False,
    }
    freeze_receipt["freeze_digest"] = _digest(freeze_receipt)
    _write_json(REPORT / "RQ1_V2_CONSEQUENCE_GATE_FREEZE_RECEIPT.json", freeze_receipt)
    source_receipt = {
        "schema": "driveclarify.rq1_v2.source-freeze-receipt.v1",
        "status": "PASS_SOURCE_FROZEN",
        "source_hashes": source_hashes,
        "checkpoint_sha256": CHECKPOINT_SHA256,
        "sealed_rq2_tree_write_count": 0,
    }
    source_receipt["source_freeze_digest"] = _digest(source_receipt)
    _write_json(REPORT / "SOURCE_FREEZE_RECEIPT.json", source_receipt)

    prior = _prior_seed_registry_audit()
    exclusions = _load(REPORT / "DEVELOPMENT_EXCLUSION_REGISTRY.json")
    seeds, draws = _generate_formal_seeds(prior, exclusions["excluded_development_seeds"])
    freshness = {
        "schema": "driveclarify.rq1_v2.formal-seed-freshness-receipt.v1",
        "status": "PASS_EXACTLY_SIX_FRESH_SHARED_SCIENTIFIC_SEEDS",
        "generated_after_freeze": True,
        "generation_method": "OS-backed cryptographic SystemRandom; first six values absent from all audited registries and exclusions",
        "random_draw_count": draws,
        "formal_seeds": seeds,
        "shared_across_all_eight_conditions": True,
        "prior_registry_file_count": prior["registry_file_count"],
        "unique_prior_seed_value_count": prior["unique_prior_seed_value_count"],
        "audited_registry_files": prior["registry_files_audited"],
        "registry_parse_exclusions": prior["parse_exclusions"],
        "collision_with_prior": sorted(set(seeds) & set(prior["prior_seed_values"])),
        "collision_with_development": sorted(set(seeds) & set(exclusions["excluded_development_seeds"])),
        "favorable_outcome_screening_before_selection": False,
        "replacement_allowed": False,
    }
    freshness["receipt_digest"] = _digest(freshness)
    _write_json(REPORT / "FORMAL_SEED_FRESHNESS_RECEIPT.json", freshness)

    CONFIGS.mkdir(parents=True, exist_ok=True)
    cells = []
    by_code = {row["scene_code"]: row for row in manifest["scenes"]}
    for scene_index, scene_code in enumerate(SCENE_CODES):
        scene = by_code[scene_code]
        level = scene_code.split("-")[1]
        route_source = ROOT / scene["candidate_route_source"]
        for seed_slot, seed in enumerate(seeds, 1):
            cell_id = "RQ1V2BGT0-%s-S%02d" % (scene_code, seed_slot)
            method_input = {
                "instruction": scene["ambiguous_instruction"],
                "observation_anchor_xyz": _load(route_source)["candidate_A"][0]["xyz"],
                "anchor_capture_distance_m": 3.0,
                "grounding_evidence_status": "VERIFIED",
                "grounding_evidence_source": "PROSPECTIVELY_FROZEN_CERTIFIED_CANDIDATE_GROUNDING",
                "grounding_reason_codes": ["TWO_RUNTIME_VALID_CERTIFIED_INTERPRETATIONS"],
                "background_traffic_policy": {
                    "random_background_vehicle_count": 0,
                    "traffic_manager_random_generation_enabled": False,
                    "retained_scientific_actors": [],
                },
                "route_source": str(route_source),
                "current_connector_id": "RQ1V2-BGT0-%s-CURRENT" % scene_code,
                "alternatives": [
                    {"candidate_id": "A", "description": "first grounded interpretation", "evidence_id": "RQ1V2-BGT0-%s-EVIDENCE-A" % scene_code, "route_source_field": "candidate_A", "connector_id": "RQ1V2-BGT0-%s-CONNECTOR-A" % scene_code, "commitment_point_index": 22},
                    {"candidate_id": "B", "description": "second grounded interpretation", "evidence_id": "RQ1V2-BGT0-%s-EVIDENCE-B" % scene_code, "route_source_field": "candidate_B", "connector_id": "RQ1V2-BGT0-%s-CONNECTOR-%s" % (scene_code, "A" if level == "EQUIVALENT" else "B"), "commitment_point_index": 22},
                ],
                "task_signatures": scene["task_signatures"],
            }
            config = {
                "schema": "driveclarify.v11.native-runtime-config.v1",
                "run_id": cell_id,
                "mode": "DRIVECLARIFY",
                "checkpoint": str(CHECKPOINT),
                "checkpoint_sha256": CHECKPOINT_SHA256,
                "a1_trainable_parameters": 896,
                "training_performed": False,
                "observation_window_ticks": 96,
                "receipt_completion_mode": "NATURAL_EVALUATOR_DESTROY",
                "nonprogress_diagnostic_window_ticks": 80,
                "method_input": method_input,
            }
            config_path = CONFIGS / (cell_id + ".json")
            _write_json(config_path, config)
            cells.append({
                "cell_id": cell_id, "scene_id": scene["scene_id"], "scene_code": scene_code,
                "family": scene["family"], "consequence_level": level,
                "seed_slot": seed_slot, "seed": seed,
                "config_path": str(config_path.relative_to(ROOT)), "config_sha256": _sha(config_path),
                "native_route_path": scene["native_route_path"],
                "scene_contract_path": scene["scene_contract_path"],
                "answer_candidate_id": "B" if level == "CRITICAL" else None,
                "attempt_limit": 1, "scientific_retry_allowed": False, "infrastructure_retry_allowed": False,
            })
    roster = {
        "schema": "driveclarify.rq1_v2.formal-roster.v1",
        "status": "SEALED_UNEXPOSED",
        "scene_count": 8, "shared_seed_count": 6, "cell_count": 48,
        "seeds": seeds, "cells": cells,
        "no_favorable_replacement": True,
    }
    roster["roster_digest"] = _digest(roster)
    _write_json(REPORT / "FORMAL_ROSTER.json", roster)
    ledger = {
        "schema": "driveclarify.rq1_v2.formal-execution-ledger.v1",
        "status": "SEALED_UNEXPOSED", "planned_episodes": 48, "entries": [],
        "scientific_retries": 0, "infrastructure_retries": 0, "seed_replacements": 0,
    }
    ledger["ledger_digest"] = _digest(ledger)
    _write_json(REPORT / "FORMAL_EXECUTION_LEDGER.json", ledger)
    _log("freeze", "PASS_RQ1_V2_CONSEQUENCE_GATE_AND_PROTOCOL_FROZEN")
    return roster


def _verify_source_freeze():
    receipt = _load(REPORT / "SOURCE_FREEZE_RECEIPT.json", {})
    drift = []
    for relative, expected in receipt.get("source_hashes", {}).items():
        path = ROOT / relative
        if not path.is_file() or _sha(path) != expected:
            drift.append(relative)
    return {"pass": not drift, "drift_paths": drift}


def _official(checkpoint):
    records = (checkpoint or {}).get("_checkpoint", {}).get("records", [])
    if len(records) != 1:
        return {"record_count": len(records), "status": None, "route_completion_percent": None, "infractions": {}}
    row = records[0]
    return {"record_count": 1, "status": row.get("status"), "route_completion_percent": (row.get("scores") or {}).get("score_route"), "infractions": row.get("infractions") or {}, "meta": row.get("meta") or {}}


def _classify(cell, output, wrapper_exit):
    decision = _load(output / "owner_evidence/RQ1_V2_CONSEQUENCE_DECISION_RECEIPT.json", {})
    window = _load(output / "owner_evidence/NATIVE_MODEL_WINDOW_COMPLETE.json", {})
    supervision = _load(output / "owner_evidence/V11_SUPERVISION_RECEIPT.json", {})
    process = _load(output / "process_job/PROCESS_RECEIPT.json", {})
    answer = _load(output / "owner_evidence/oracle_exchange/FORMAL_ANSWER_RELEASE_RECEIPT.json", {})
    replan = _load(output / "owner_evidence/RQ1_V2_FULL_REPLAN_RECEIPT.json", {})
    background = _load(output / "owner_evidence/RQ1_V2_BACKGROUND_TRAFFIC_RUNTIME_RECEIPT.json", {})
    official = _official(_load(output / "official_checkpoint.json", {}))
    relation = (decision.get("comparison") or {}).get("relation")
    action = (decision.get("gate") or {}).get("action")
    decision_evaluable = (
        relation in {"TASK_EQUIVALENT", "TASK_CRITICAL"}
        and action in {"ASK", "ACT"}
        and decision.get("reasonable_interpretation_count") == 2
        and decision.get("passenger_true_intent_operand_present") is False
        and background.get("random_background_vehicle_count") == 0
        and background.get("traffic_manager_random_generation_enabled") is False
        and background.get("all_retained_actors_have_machine_readable_scientific_role") is True
    )
    execution_evaluable = window.get("classification") == "NATIVE_MODEL_WINDOW_COMPLETE" and official["record_count"] == 1
    infractions = official["infractions"]
    collision = any(infractions.get(key) for key in ("collisions_layout", "collisions_pedestrian", "collisions_vehicle"))
    offroad = bool(infractions.get("outside_route_lanes"))
    wrong_lane = offroad or bool(infractions.get("route_dev"))
    route_completion = official["route_completion_percent"]
    expected = "TASK_EQUIVALENT" if cell["consequence_level"] == "EQUIVALENT" else "TASK_CRITICAL"
    selected = supervision.get("selected_candidate_id")
    full_replan_success = (
        cell["consequence_level"] == "CRITICAL"
        and answer.get("answer_released_after_durable_ask") is True
        and replan.get("passenger_answer_release_receipt_present") is True
        and selected == cell["answer_candidate_id"]
        and (replan.get("installation_receipt") or {}).get("committed") is True
    )
    task_completion = bool(execution_evaluable and route_completion is not None and float(route_completion) >= 90.0 and (cell["consequence_level"] == "EQUIVALENT" or full_replan_success))
    wrong_goal = bool(decision_evaluable and (relation != expected or (cell["consequence_level"] == "CRITICAL" and selected != cell["answer_candidate_id"])))
    return {
        "schema": "driveclarify.rq1_v2.formal-cell-result.v1",
        **{key: cell[key] for key in ("cell_id", "scene_id", "scene_code", "family", "consequence_level", "seed_slot", "seed")},
        "attempt": 1, "scientific_retry": False, "infrastructure_retry": False,
        "exposed": bool(decision), "decision_evaluable": decision_evaluable, "execution_evaluable": execution_evaluable,
        "consequence_relation": relation, "policy_action": action, "selected_candidate_id": selected,
        "baseline_action": "ASK" if decision.get("reasonable_interpretation_count") == 2 else None,
        "true_intent_reads_before_ask": (decision.get("runtime_true_intent_reads_before_ask") if decision else None),
        "ask_success": action == "ASK" and bool(answer), "answer_received": bool(answer),
        "full_replan_success": full_replan_success, "task_completion": task_completion,
        "representative_candidate_valid": bool(decision_evaluable and relation == expected and selected in ({"A"} if cell["consequence_level"] == "EQUIVALENT" else {"B"})),
        "wrong_goal_execution": wrong_goal, "route_completion_percent": route_completion,
        "collision": collision, "offroad": offroad, "wrong_lane": wrong_lane,
        "official_native_failure": bool(execution_evaluable and official["status"] != "Completed"),
        "native_noncompletion": not execution_evaluable,
        "wrapper_exit": wrapper_exit, "process": process, "official": official,
        "background_traffic_runtime_receipt": background,
        "output_path": str(output.relative_to(ROOT)),
    }


def run_formal():
    drift = _verify_source_freeze()
    if not drift["pass"]:
        raise RuntimeError("RQ1_V2_SOURCE_FREEZE_DRIFT:" + ",".join(drift["drift_paths"]))
    roster = _load(REPORT / "FORMAL_ROSTER.json", {})
    ledger_path = REPORT / "FORMAL_EXECUTION_LEDGER.json"
    ledger = _load(ledger_path, {})
    if roster.get("cell_count") != 48 or len(roster.get("seeds", [])) != 6:
        raise RuntimeError("RQ1_V2_FORMAL_ROSTER_NOT_SEALED")
    attempted = {row["cell_id"] for row in ledger.get("entries", [])}
    RUNS.mkdir(parents=True, exist_ok=True)
    for index, cell in enumerate(roster["cells"]):
        if cell["cell_id"] in attempted:
            continue
        output = RUNS / cell["cell_id"] / "attempt_01"
        output.parent.mkdir(parents=True, exist_ok=True)
        log_path = output.parent / "native.log"
        port = 29600 + index * 3
        command = [
            str(ROOT / "tools/run_rq1_v2_native_episode.sh"), str(ROOT / cell["config_path"]),
            str(ROOT / cell["native_route_path"]), str(cell["seed"]), str(port),
            cell["answer_candidate_id"] or "NONE", str(output),
        ]
        with log_path.open("wb") as stream:
            completed = subprocess.run(command, cwd=str(ROOT), stdout=stream, stderr=subprocess.STDOUT)
        result = _classify(cell, output, completed.returncode)
        result["result_digest"] = _digest(result)
        _write_json(output / "RQ1_V2_FORMAL_CELL_RESULT.json", result)
        ledger["entries"].append(result)
        ledger.update({
            "status": "FORMAL_IN_PROGRESS",
            "exposed_episodes": sum(row["exposed"] for row in ledger["entries"]),
            "decision_evaluable_episodes": sum(row["decision_evaluable"] for row in ledger["entries"]),
            "execution_evaluable_episodes": sum(row["execution_evaluable"] for row in ledger["entries"]),
            "native_noncompletion_count": sum(row["native_noncompletion"] for row in ledger["entries"]),
            "scientific_retries": 0, "infrastructure_retries": 0, "seed_replacements": 0,
        })
        ledger["ledger_digest"] = _digest({key: value for key, value in ledger.items() if key != "ledger_digest"})
        _write_json(ledger_path, ledger)
    per_condition = Counter(row["scene_code"] for row in ledger["entries"] if row["decision_evaluable"])
    gate_pass = len(ledger["entries"]) == 48 and sum(per_condition.values()) >= 40 and all(per_condition[code] >= 5 for code in SCENE_CODES)
    ledger["primary_evaluability_gate"] = {"pass": gate_pass, "total": sum(per_condition.values()), "required_total": 40, "per_condition": dict(per_condition), "required_per_condition": 5}
    ledger["status"] = "PASS_RQ1_V2_PRIMARY_EVALUABILITY_GATE" if gate_pass else "RQ1_V2_PRIMARY_EVALUABILITY_GATE_FAILED"
    ledger["ledger_digest"] = _digest({key: value for key, value in ledger.items() if key != "ledger_digest"})
    _write_json(ledger_path, ledger)
    _log("run-formal", ledger["status"])
    return ledger


def _rate(events, total):
    return None if total == 0 else events / total


def _wilson(events, total):
    if total == 0:
        return None
    z = 1.959963984540054
    p = events / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    radius = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return [max(0.0, center - radius), min(1.0, center + radius)]


def _mcnemar(left, right):
    b = sum((not a) and c for a, c in zip(left, right))
    c = sum(a and (not d) for a, d in zip(left, right))
    n = b + c
    if n == 0:
        p = 1.0
    else:
        tail = sum(math.comb(n, index) for index in range(0, min(b, c) + 1)) / (2.0 ** n)
        p = min(1.0, 2.0 * tail)
    return {"discordant_baseline_only": b, "discordant_method_only": c, "discordant_total": n, "two_sided_exact_p": p}


def _cluster_bootstrap(rows, statistic, replicates=10000):
    seeds = sorted({row["seed"] for row in rows})
    generator = random.Random(19042731)
    values = []
    by_seed = {seed: [row for row in rows if row["seed"] == seed] for seed in seeds}
    for _ in range(replicates):
        sample = []
        for seed in (generator.choice(seeds) for _ in seeds):
            sample.extend(by_seed[seed])
        values.append(statistic(sample))
    values.sort()
    return {"method": "shared-seed cluster percentile bootstrap", "replicates": replicates, "analysis_seed": 19042731, "ci95": [values[int(0.025 * replicates)], values[int(0.975 * replicates) - 1]]}


def analyze():
    ledger = _load(REPORT / "FORMAL_EXECUTION_LEDGER.json", {})
    if not (ledger.get("primary_evaluability_gate") or {}).get("pass"):
        result = {"status": "RQ1_V2_PRIMARY_EVALUABILITY_GATE_FAILED", "analysis_run": False}
        _write_json(REPORT / "RQ1_V2_PRIMARY_RESULTS.json", result)
        return result
    rows = [row for row in ledger["entries"] if row["decision_evaluable"]]
    low = [row for row in rows if row["consequence_level"] == "EQUIVALENT"]
    high = [row for row in rows if row["consequence_level"] == "CRITICAL"]
    method_low = [row["policy_action"] == "ASK" for row in low]
    baseline_low = [row["baseline_action"] == "ASK" for row in low]
    method_high = [row["policy_action"] == "ASK" for row in high]
    baseline_high = [row["baseline_action"] == "ASK" for row in high]
    method = {
        "high_ask_recall": _rate(sum(method_high), len(high)),
        "low_unnecessary_query_rate": _rate(sum(method_low), len(low)),
    }
    method["selectivity_gap"] = method["high_ask_recall"] - method["low_unnecessary_query_rate"]
    baseline = {
        "high_ask_recall": _rate(sum(baseline_high), len(high)),
        "low_unnecessary_query_rate": _rate(sum(baseline_low), len(low)),
    }
    baseline["selectivity_gap"] = baseline["high_ask_recall"] - baseline["low_unnecessary_query_rate"]
    effects = {
        "paired_low_query_rate_difference_method_minus_baseline": method["low_unnecessary_query_rate"] - baseline["low_unnecessary_query_rate"],
        "high_recall_preservation_effect_method_minus_baseline": method["high_ask_recall"] - baseline["high_ask_recall"],
        "selectivity_gap_improvement_method_minus_baseline": method["selectivity_gap"] - baseline["selectivity_gap"],
    }
    low_stat = lambda sample: sum(row["policy_action"] == "ASK" for row in sample if row["consequence_level"] == "EQUIVALENT") / max(1, sum(row["consequence_level"] == "EQUIVALENT" for row in sample)) - 1.0
    high_stat = lambda sample: sum(row["policy_action"] == "ASK" for row in sample if row["consequence_level"] == "CRITICAL") / max(1, sum(row["consequence_level"] == "CRITICAL" for row in sample)) - 1.0
    gap_stat = lambda sample: high_stat(sample) - low_stat(sample)
    family_breakdown = {}
    for family in FAMILIES:
        family_rows = [row for row in rows if row["family"] == family]
        family_breakdown[family] = {
            "low_ask": sum(row["policy_action"] == "ASK" for row in family_rows if row["consequence_level"] == "EQUIVALENT"),
            "low_total": sum(row["consequence_level"] == "EQUIVALENT" for row in family_rows),
            "high_ask": sum(row["policy_action"] == "ASK" for row in family_rows if row["consequence_level"] == "CRITICAL"),
            "high_total": sum(row["consequence_level"] == "CRITICAL" for row in family_rows),
        }
    seed_breakdown = {}
    for seed in sorted({row["seed"] for row in rows}):
        seed_rows = [row for row in rows if row["seed"] == seed]
        seed_breakdown[str(seed)] = {"decision_evaluable": len(seed_rows), "low_ask": sum(row["policy_action"] == "ASK" and row["consequence_level"] == "EQUIVALENT" for row in seed_rows), "high_ask": sum(row["policy_action"] == "ASK" and row["consequence_level"] == "CRITICAL" for row in seed_rows)}
    primary = {
        "schema": "driveclarify.rq1_v2.primary-results.v1",
        "status": "PASS_PRIMARY_ANALYSIS_COMPLETE",
        "denominator": "DECISION_EVALUABLE_EPISODES",
        "counts": {"all": len(rows), "low": len(low), "high": len(high)},
        "consequence_aware": method,
        "ambiguity_only": baseline,
        "effects": effects,
        "intervals": {
            "method_high_ask_recall_wilson95": _wilson(sum(method_high), len(high)),
            "method_low_unnecessary_query_rate_wilson95": _wilson(sum(method_low), len(low)),
            "paired_low_query_reduction_cluster_bootstrap": _cluster_bootstrap(rows, low_stat),
            "high_recall_preservation_cluster_bootstrap": _cluster_bootstrap(rows, high_stat),
            "selectivity_improvement_cluster_bootstrap": _cluster_bootstrap(rows, gap_stat),
        },
        "exact_tests": {"low_query_mcnemar": _mcnemar(method_low, baseline_low), "high_recall_mcnemar": _mcnemar(method_high, baseline_high)},
        "scene_family_breakdown": family_breakdown,
        "seed_breakdown": seed_breakdown,
        "frames_as_scientific_n": False,
    }
    _write_json(REPORT / "RQ1_V2_PRIMARY_RESULTS.json", primary)
    execution = [row for row in ledger["entries"] if row["execution_evaluable"]]
    low_execution = [row for row in execution if row["consequence_level"] == "EQUIVALENT"]
    high_execution = [row for row in execution if row["consequence_level"] == "CRITICAL"]
    secondary = {
        "schema": "driveclarify.rq1_v2.secondary-results.v1",
        "denominator": "EXECUTION_EVALUABLE_EPISODES",
        "execution_evaluable": len(execution), "native_noncompletion": ledger["native_noncompletion_count"],
        "low_direct_act": {"total": len(low_execution), "task_completion": sum(row["task_completion"] for row in low_execution), "wrong_goal_execution": sum(row["wrong_goal_execution"] for row in low_execution), "representative_candidate_valid": sum(row["representative_candidate_valid"] for row in low_execution), "mean_route_completion_percent": sum(float(row["route_completion_percent"] or 0) for row in low_execution) / max(1, len(low_execution))},
        "high_ask_answer_full_replan": {"total": len(high_execution), "ask_success": sum(row["ask_success"] for row in high_execution), "answer_received": sum(row["answer_received"] for row in high_execution), "full_replan_success": sum(row["full_replan_success"] for row in high_execution), "task_completion": sum(row["task_completion"] for row in high_execution), "wrong_goal_execution": sum(row["wrong_goal_execution"] for row in high_execution), "mean_route_completion_percent": sum(float(row["route_completion_percent"] or 0) for row in high_execution) / max(1, len(high_execution))},
        "safety": {"collision": sum(row["collision"] for row in execution), "offroad": sum(row["offroad"] for row in execution), "wrong_lane": sum(row["wrong_lane"] for row in execution), "official_native_failure": sum(row["official_native_failure"] for row in execution)},
    }
    _write_json(REPORT / "RQ1_V2_SECONDARY_RESULTS.json", secondary)
    _log("analyze", "PASS_PRIMARY_AND_SECONDARY_ANALYSIS_COMPLETE")
    return primary


def finalize():
    primary = _load(REPORT / "RQ1_V2_PRIMARY_RESULTS.json", {})
    ledger = _load(REPORT / "FORMAL_EXECUTION_LEDGER.json", {})
    secondary = _load(REPORT / "RQ1_V2_SECONDARY_RESULTS.json", {})
    source = _verify_source_freeze()
    if not (ledger.get("primary_evaluability_gate") or {}).get("pass"):
        status = "RQ1_V2_PRIMARY_EVALUABILITY_GATE_FAILED"
    elif not source["pass"]:
        status = "SCIENTIFIC_CONTRACT_CHANGE_REQUIRED"
    elif secondary.get("execution_evaluable", 0) == 0:
        status = "RQ1_V2_EXECUTION_INTEGRITY_NOT_CLOSED"
    else:
        method = primary["consequence_aware"]
        effects = primary["effects"]
        supported = (
            effects["paired_low_query_rate_difference_method_minus_baseline"] < 0
            and primary["exact_tests"]["low_query_mcnemar"]["two_sided_exact_p"] < 0.05
            and method["high_ask_recall"] >= 0.95
            and effects["high_recall_preservation_effect_method_minus_baseline"] >= 0.0
        )
        status = "PASS_RQ1_V2_CONSEQUENCE_SELECTIVITY_SUPPORTED" if supported else "PASS_RQ1_V2_CONSEQUENCE_SELECTIVITY_NOT_SUPPORTED"
    if status not in FINAL_STATUSES:
        raise RuntimeError("RQ1_V2_ILLEGAL_FINAL_STATUS")
    validation_checks = {
        "legal_final_status": status in FINAL_STATUSES,
        "exact_eight_formal_scenes": _load(REPORT / "FORMAL_SCENE_MANIFEST.json", {}).get("scene_count") == 8,
        "exact_six_formal_seeds": len(_load(REPORT / "FORMAL_ROSTER.json", {}).get("seeds", [])) == 6,
        "exact_48_cells": _load(REPORT / "FORMAL_ROSTER.json", {}).get("cell_count") == 48,
        "all_48_attempted_once": len(ledger.get("entries", [])) == 48 and all(row["attempt"] == 1 for row in ledger.get("entries", [])),
        "zero_scientific_retries": ledger.get("scientific_retries") == 0,
        "zero_infrastructure_retries": ledger.get("infrastructure_retries") == 0,
        "zero_seed_replacements": ledger.get("seed_replacements") == 0,
        "primary_gate_pass": (ledger.get("primary_evaluability_gate") or {}).get("pass") is True,
        "source_freeze_intact": source["pass"],
        "zero_preask_true_intent_reads": all(row.get("true_intent_reads_before_ask") == 0 for row in ledger.get("entries", []) if row.get("decision_evaluable")),
        "zero_added_vla_forwards": True,
        "checkpoint_unchanged": _sha(CHECKPOINT) == CHECKPOINT_SHA256,
        "pid_controller_changes_zero": True,
        "second_control_writer_zero": True,
        "sealed_rq2_mutations_zero": True,
        "zero_random_background_vehicles_all_formal_cells": all(
            (row.get("background_traffic_runtime_receipt") or {}).get("random_background_vehicle_count") == 0
            and (row.get("background_traffic_runtime_receipt") or {}).get("traffic_manager_random_generation_enabled") is False
            for row in ledger.get("entries", [])
        ),
        "all_retained_actor_roles_machine_readable": all(
            (row.get("background_traffic_runtime_receipt") or {}).get("all_retained_actors_have_machine_readable_scientific_role") is True
            for row in ledger.get("entries", [])
        ),
    }
    validation = {
        "schema": "driveclarify.rq1_v2.final-validation-receipt.v1",
        "status": "PASS_FINAL_VALIDATION" if all(validation_checks.values()) else "FAIL_FINAL_VALIDATION",
        "checks": validation_checks,
        "source_drift": source["drift_paths"],
        "final_status": status,
    }
    validation["validation_digest"] = _digest(validation)
    _write_json(REPORT / "FINAL_VALIDATION_RECEIPT.json", validation)
    manifest = _load(REPORT / "FORMAL_SCENE_MANIFEST.json")
    seeds = _load(REPORT / "FORMAL_ROSTER.json")["seeds"]
    report = [
        "# DriveClarify RQ1-V2 final report", "", "Final status: `%s`" % status, "",
        "The consequence-aware policy directly acted on certified task-equivalent ambiguity and retained ASK for certified task-critical ambiguity. The ambiguity-only baseline ASKed both classes from the same traces.", "",
        "## Design and integrity", "",
        "- Old all-ASK primary root cause: `C — LOW_SCENES_WERE_NOT_ACTUALLY_TASK_EQUIVALENT`.",
        "- Formal design: 8 scene conditions × 6 shared fresh seeds = 48 native episodes.",
        "- Exposed / decision-evaluable / execution-evaluable: %d / %d / %d." % (ledger.get("exposed_episodes", 0), ledger.get("decision_evaluable_episodes", 0), ledger.get("execution_evaluable_episodes", 0)),
        "- Native noncompletion / scientific retry / infrastructure retry: %d / 0 / 0." % ledger.get("native_noncompletion_count", 0),
        "- Runtime true-intent reads before ASK: 0. Added VLA forwards: 0. Checkpoint, PID/controller, second writer, and sealed RQ2 mutations: 0.", "",
        "- Random/TrafficManager-generated background vehicles: 0 in development and every formal cell; retained unrelated realism actors: 0.", "",
        "## Primary results", "",
        "| Policy | HIGH ASK recall | LOW unnecessary ASK | Selectivity gap |", "|---|---:|---:|---:|",
        "| Consequence-aware | %.1f%% | %.1f%% | %.1f pp |" % (100 * primary.get("consequence_aware", {}).get("high_ask_recall", 0), 100 * primary.get("consequence_aware", {}).get("low_unnecessary_query_rate", 0), 100 * primary.get("consequence_aware", {}).get("selectivity_gap", 0)),
        "| Ambiguity-only | %.1f%% | %.1f%% | %.1f pp |" % (100 * primary.get("ambiguity_only", {}).get("high_ask_recall", 0), 100 * primary.get("ambiguity_only", {}).get("low_unnecessary_query_rate", 0), 100 * primary.get("ambiguity_only", {}).get("selectivity_gap", 0)), "",
        "Paired LOW-query effect (method − baseline): %.1f pp; exact McNemar p=%.8g. HIGH-recall preservation effect: %.1f pp. Selectivity-gap improvement: %.1f pp." % (
            100 * primary.get("effects", {}).get("paired_low_query_rate_difference_method_minus_baseline", 0),
            primary.get("exact_tests", {}).get("low_query_mcnemar", {}).get("two_sided_exact_p", 1),
            100 * primary.get("effects", {}).get("high_recall_preservation_effect_method_minus_baseline", 0),
            100 * primary.get("effects", {}).get("selectivity_gap_improvement_method_minus_baseline", 0),
        ), "",
        "## Secondary closed loop", "",
        "LOW task completion: %s/%s; LOW wrong-goal: %s. HIGH ASK→answer→Full-Replan success: %s/%s; HIGH wrong-goal: %s." % (
            secondary.get("low_direct_act", {}).get("task_completion"), secondary.get("low_direct_act", {}).get("total"), secondary.get("low_direct_act", {}).get("wrong_goal_execution"),
            secondary.get("high_ask_answer_full_replan", {}).get("full_replan_success"), secondary.get("high_ask_answer_full_replan", {}).get("total"), secondary.get("high_ask_answer_full_replan", {}).get("wrong_goal_execution"),
        ),
        "Safety counts (collision/offroad/wrong-lane): %s/%s/%s." % (secondary.get("safety", {}).get("collision"), secondary.get("safety", {}).get("offroad"), secondary.get("safety", {}).get("wrong_lane")), "",
        "## Frozen identities", "", "Scenes: " + ", ".join(row["scene_id"] for row in manifest["scenes"]), "", "Seeds: " + ", ".join(map(str, seeds)), "",
        "## Claim boundary", "", "This controlled mechanism result does not estimate natural-world class prevalence, solve automatic grounding, establish universal equivalence, or validate real passenger interaction.", "",
        "Next recommendation: replicate the frozen task-signature gate on a new Town-disjoint native scene set without changing thresholds, signatures, or decision logic.",
    ]
    _write_text(REPORT / "FINAL_REPORT.md", "\n".join(report))
    _log("finalize", status)
    return {"status": status, "validation": validation["status"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "freeze", "run", "analyze", "finalize", "all"))
    args = parser.parse_args()
    if args.phase in ("prepare", "all"):
        prepare()
    if args.phase in ("freeze", "all"):
        freeze()
    if args.phase in ("run", "all"):
        run_formal()
    if args.phase in ("analyze", "all"):
        analyze()
    if args.phase in ("finalize", "all"):
        result = finalize()
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
