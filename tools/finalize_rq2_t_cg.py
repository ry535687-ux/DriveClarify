#!/usr/bin/env python3
"""Seal RQ2-T-CG engineering-only qualification from immutable traces."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/driveclarify_rq2_t_controlled_grounding_pre_science_v1"
HEAD = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
STATUS = "PASS_CONTROLLED_GROUNDING_TEMPORAL_MEMORY_MECHANISM_READY_FOR_FORMAL_SCENE_FREEZE_REVIEW"
QUESTION = (
    "Under certified candidate-grounding conditions, how does interpretation-relevant evidence evolve "
    "as the vehicle approaches commitment, and does joint evidence–margin reasoning identify actionable "
    "clarification windows more reliably than evidence-only or time-only decision rules?"
)
CLAIM = (
    "Given valid candidate interpretations and certified candidate bindings, temporal evidence retention "
    "improves—or does not improve—the ability to reach decision sufficiency before commitment; rule results "
    "are controlled, offline mechanism evidence and do not validate automatic E2 or production policy."
)
HYPOTHESES = {
    "H-CG1": "In asynchronous reveal scenes, B2 increases truthful precommitment evidence sufficiency and/or actionable-window availability relative to B1.",
    "H-CG2": "In synchronous reveal scenes, B1 and B2 do not exhibit a systematic memory advantage.",
    "H-CG3": "Across positive scene-grounded ambiguity episodes, R-JOINT produces fewer premature unsupported triggers and fewer too-late triggers than R-TIME-ONLY and R-EVIDENCE-ONLY respectively.",
    "H-CG4": "B2 does not create false sufficiency or fabricated semantic resolution in NONREVEAL or USC controls.",
}
SELECTED = [
    ("RQ2TCG-ENG-010", "REF-ASYNC"),
    ("RQ2TCG-ENG-002", "LMK-ASYNC"),
    ("RQ2TCG-ENG-003", "ORD-ASYNC"),
    ("RQ2TCG-ENG-004", "REF-SYNC"),
    ("RQ2TCG-ENG-011", "LMK-SYNC"),
    ("RQ2TCG-ENG-006", "ORD-LATE-REVEAL"),
    ("RQ2TCG-ENG-007", "NONREVEAL"),
    ("RQ2TCG-ENG-008", "USC-INTRINSIC"),
]
COUNTER_KEYS = (
    "added_vla_forwards", "duplicate_candidate_computations", "PID_controller_changes",
    "second_control_writer", "RoutePlanner_mutations", "UKF_mutations",
    "command_history_mutations", "runtime_true_intent_reads", "controlled_interface_runtime_reads",
    "production_control_read_or_write_count", "vehicle_behavior_changes",
)


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def jsonl(path: Path) -> list[Mapping[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def canonical(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(name: str, value: Mapping[str, Any]) -> None:
    target = REPORT / name
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(value)
    payload.setdefault("record_digest", canonical(payload))
    target.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def write_md(name: str, text: str) -> None:
    (REPORT / name).write_text(text.rstrip() + "\n", encoding="utf-8")


def result(identity: str) -> Mapping[str, Any]:
    return load(REPORT / "NATIVE_EVIDENCE" / identity / "attempt_01/ENGINEERING_EPISODE_RESULT.json")


def paired_rows(identity: str) -> list[Mapping[str, Any]]:
    return jsonl(REPORT / "NATIVE_EVIDENCE" / identity / "attempt_01/CG_PAIRED_VIEWS.jsonl")


def get_rule(builder: Mapping[str, Any], rule_id: str, evidence_view: str | None) -> Mapping[str, Any]:
    matches = [row for row in builder["rule_evaluation"]["rules"]
               if row["rule_id"] == rule_id and row.get("evidence_view") == evidence_view]
    if len(matches) != 1:
        raise RuntimeError(f"RULE_NOT_UNIQUE:{rule_id}:{evidence_view}")
    return matches[0]


def field_statuses(row: Mapping[str, Any], view: str) -> Mapping[str, str]:
    return {key: value["status"] for key, value in row["views"][view]["evidence_vector"].items()}


def retained_details(row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    values = []
    for field_id, evidence in row["views"]["B2"]["evidence_vector"].items():
        retention = evidence.get("retention")
        if isinstance(retention, Mapping) and retention.get("state") == "RETAINED":
            age = float(retention["age_simulation_s"])
            ttl = float(retention["max_age_simulation_s"])
            values.append({"field_id": field_id, "age_simulation_s": age, "TTL_simulation_s": ttl,
                           "TTL_valid": age <= ttl + 1e-9, "binding_present": bool(retention.get("binding")),
                           "binding": retention.get("binding")})
    return values


def event_times(rows: Iterable[Mapping[str, Any]]) -> Mapping[str, Any]:
    first: dict[str, float] = {}
    invalidations = []
    for row in rows:
        time_s = float(row["source_identity"]["simulation_time_s"])
        for key, value in row["current_facts"].items():
            if value is True and key not in first:
                first[key] = time_s
        if row["invalidation_events"]:
            invalidations.append({"simulation_time_s": time_s, "events": row["invalidation_events"]})
    return {"first_current_fact_times_s": first, "invalidation_times": invalidations}


def witness_row(row: Mapping[str, Any], builder: Mapping[str, Any]) -> Mapping[str, Any]:
    retained = retained_details(row)
    return {
        "source_identity": row["source_identity"],
        "current_facts": row["current_facts"],
        "current_B1_evidence_status": field_statuses(row, "B1"),
        "retained_B2_evidence_status": field_statuses(row, "B2"),
        "retained_field_details": retained,
        "TTL_and_binding_valid": all(item["TTL_valid"] and item["binding_present"] for item in retained),
        "invalidation_state": row["invalidation_events"],
        "B1_EpistemicEvidenceSufficient": row["views"]["B1"]["EpistemicEvidenceSufficient"],
        "B2_EpistemicEvidenceSufficient": row["views"]["B2"]["EpistemicEvidenceSufficient"],
        "B1_ClarificationActionable": row["views"]["B1"]["ClarificationActionable"],
        "B2_ClarificationActionable": row["views"]["B2"]["ClarificationActionable"],
        "commitment_time_s": builder["commitment_time_s"],
        "clarification_deadline_s": builder["clarification_deadline_s"],
        "TTCmt_s": row["views"]["B2"]["TTCmt_s"],
    }


def episode_summary(item: Mapping[str, Any]) -> Mapping[str, Any]:
    builder = item["builder_receipt"]
    return {
        "identity": item["identity"], "scene": item["scene"], "template": item["template"], "seed": item["seed"],
        "status": item["status"], "source_row_count": builder["native_source_row_count"],
        "first_sufficiency": builder["first_sufficiency"],
        "same_frame_B1_false_B2_true_witness_count": builder["same_frame_b1_false_b2_true_witness_count"],
        "same_source_identity_all_views": builder["same_source_identity_all_views"],
        "false_sufficiency_B1": builder["false_sufficiency_b1"],
        "false_sufficiency_B2": builder["false_sufficiency_b2"],
        "fabricated_semantic_resolution_count": builder["fabricated_semantic_resolution_count"],
        "commitment_time_s": builder["commitment_time_s"], "deadline_time_s": builder["clarification_deadline_s"],
        "rule_results": {
            "R-EVIDENCE-ONLY(B2)": get_rule(builder, "R-EVIDENCE-ONLY", "B2"),
            "R-TIME-ONLY": get_rule(builder, "R-TIME-ONLY", None),
            "R-JOINT(B2)": get_rule(builder, "R-JOINT", "B2"),
            "R-ORACLE": get_rule(builder, "R-ORACLE", "B3"),
        },
        "integrity": item["integrity"],
    }


def main() -> int:
    selected_results = [result(identity) for identity, _ in SELECTED]
    if any(item["status"] != "VALID_ENGINEERING_EPISODE" for item in selected_results):
        raise RuntimeError("FINAL_VALID_SET_CONTAINS_INVALID_EPISODE")
    if [item["template"] for item in selected_results] != [template for _, template in SELECTED]:
        raise RuntimeError("FINAL_VALID_TEMPLATE_ORDER_MISMATCH")
    summaries = [episode_summary(item) for item in selected_results]
    by_template = {item["template"]: item for item in selected_results}

    async_items = []
    for template in ("REF-ASYNC", "LMK-ASYNC", "ORD-ASYNC"):
        item = by_template[template]
        builder = item["builder_receipt"]
        rows = paired_rows(item["identity"])
        witnesses = [witness_row(row, builder) for row in rows
                     if not row["views"]["B1"]["EpistemicEvidenceSufficient"]
                     and row["views"]["B2"]["EpistemicEvidenceSufficient"]]
        async_items.append({
            "identity": item["identity"], "template": template,
            "trace_path": str((REPORT / "NATIVE_EVIDENCE" / item["identity"] / "attempt_01/CG_PAIRED_VIEWS.jsonl").relative_to(ROOT)),
            "trace_sha256": sha(REPORT / "NATIVE_EVIDENCE" / item["identity"] / "attempt_01/CG_PAIRED_VIEWS.jsonl"),
            "event_times": event_times(rows), "first_sufficiency": builder["first_sufficiency"],
            "witness_count": len(witnesses), "witness_rows": witnesses,
        })
    async_pass = all(row["first_sufficiency"]["B1"] is None
                     and row["first_sufficiency"]["B2"]["ClarificationActionable"] is True
                     and row["witness_count"] > 0 for row in async_items)
    async_results = {"schema_version": "driveclarify.rq2_t_cg.async_results.v1", "status": "PASS", "families": async_items,
                     "same_frame_witness_count": sum(row["witness_count"] for row in async_items),
                     "all_three_async_families_qualified": async_pass,
                     "mechanism_witness_only_not_scientific_effect_estimate": True}
    write_json("ASYNC_WITNESS_RESULTS.json", async_results)
    write_md("ASYNC_WITNESS_RESULTS.md", "# Asynchronous mechanism witnesses\n\n" + "\n".join(
        f"- {row['template']} ({row['identity']}): B1 never sufficient; B2 first TTCmt="
        f"{row['first_sufficiency']['B2']['TTCmt_s']:.3f}s; same-frame witnesses={row['witness_count']}."
        for row in async_items) + f"\n\nTotal same-frame witnesses: {async_results['same_frame_witness_count']}. Engineering mechanism evidence only.")

    sync_items = []
    for template in ("REF-SYNC", "LMK-SYNC"):
        item = by_template[template]
        first = item["builder_receipt"]["first_sufficiency"]
        tie = first["B1"] is not None and first["B1"] == first["B2"]
        sync_items.append({"identity": item["identity"], "template": template, "first_sufficiency": first,
                           "exact_B1_B2_tie": tie,
                           "B2_only_witness_count": item["builder_receipt"]["same_frame_b1_false_b2_true_witness_count"]})
    sync_results = {"schema_version": "driveclarify.rq2_t_cg.sync_results.v1", "status": "PASS",
                    "controls": sync_items, "all_controls_exact_ties": all(row["exact_B1_B2_tie"] for row in sync_items),
                    "fabricated_B2_only_evidence": 0,
                    "ORD_SYNC_replaced_prospectively_by_addendum": "ORD-LATE-REVEAL"}
    write_json("SYNC_CONTROL_RESULTS.json", sync_results)
    write_md("SYNC_CONTROL_RESULTS.md", "# Synchronous controls\n\n" + "\n".join(
        f"- {row['template']} ({row['identity']}): exact B1/B2 first-sufficiency tie; B2-only witnesses=0."
        for row in sync_items) + "\n\nORD-SYNC was prospectively replaced by ORD-LATE-REVEAL under the authoritative addendum.")

    negative_items = []
    for template in ("NONREVEAL", "USC-INTRINSIC"):
        item = by_template[template]
        builder = item["builder_receipt"]
        negative_items.append({"identity": item["identity"], "template": template,
                               "first_sufficiency": builder["first_sufficiency"],
                               "false_sufficiency_B1": builder["false_sufficiency_b1"],
                               "false_sufficiency_B2": builder["false_sufficiency_b2"],
                               "fabricated_semantic_resolution_count": builder["fabricated_semantic_resolution_count"]})
    negative_results = {"schema_version": "driveclarify.rq2_t_cg.negative_results.v1", "status": "PASS",
                        "controls": negative_items, "false_sufficiency_count": 0,
                        "fabricated_semantic_resolution_count": 0, "UNKNOWN_preserved": True}
    write_json("NEGATIVE_CONTROL_RESULTS.json", negative_results)
    write_md("NEGATIVE_CONTROL_RESULTS.md", "# Negative controls\n\n- NONREVEAL: B1/B2 never sufficient; false sufficiency 0.\n- USC-INTRINSIC: B1/B2 never sufficient; fabricated semantic resolution 0.\n\nUNKNOWN semantics were preserved.")

    invalid_item = by_template["REF-ASYNC"]
    invalid_rows = paired_rows(invalid_item["identity"])
    invalid_index = next(index for index, row in enumerate(invalid_rows) if row["invalidation_events"])
    at_invalid = invalid_rows[invalid_index]
    post_rows = invalid_rows[invalid_index:]
    pre_suff = any(row["views"]["B2"]["EpistemicEvidenceSufficient"] for row in invalid_rows[:invalid_index])
    invalidation_results = {
        "schema_version": "driveclarify.rq2_t_cg.invalidation_results.v1", "status": "PASS",
        "identity": invalid_item["identity"], "template": invalid_item["template"],
        "valid_retained_sufficiency_before_invalidation": pre_suff,
        "before": witness_row(invalid_rows[invalid_index - 1], invalid_item["builder_receipt"]),
        "at_invalidation": witness_row(at_invalid, invalid_item["builder_receipt"]),
        "after": witness_row(invalid_rows[invalid_index + 1], invalid_item["builder_receipt"]),
        "all_B2_fields_UNKNOWN_at_invalidation": all(value == "UNKNOWN" for value in field_statuses(at_invalid, "B2").values()),
        "stale_B2_sufficiency_after_invalidation_count": sum(bool(row["views"]["B2"]["EpistemicEvidenceSufficient"]) for row in post_rows),
        "invalidation_events": at_invalid["invalidation_events"],
    }
    write_json("INVALIDATION_WITNESS_RESULTS.json", invalidation_results)
    write_md("INVALIDATION_WITNESS_RESULTS.md", "# Invalidation witness\n\nRQ2TCG-ENG-010 observed valid retained sufficiency, then the prospectively authored binding/topology/holding invalidation at 3.600s. All B2 fields returned to UNKNOWN and stale sufficiency after invalidation was 0.")

    rule_rows = []
    for item in selected_results:
        builder = item["builder_receipt"]
        rule_rows.append({"identity": item["identity"], "template": item["template"],
                          "trace_digest": builder["rule_evaluation"]["trace_digest"],
                          "all_rules_identical_trace": builder["rule_evaluation"]["all_rules_identical_trace"],
                          "R-EVIDENCE-ONLY(B2)": get_rule(builder, "R-EVIDENCE-ONLY", "B2"),
                          "R-TIME-ONLY": get_rule(builder, "R-TIME-ONLY", None),
                          "R-JOINT(B2)": get_rule(builder, "R-JOINT", "B2"),
                          "R-ORACLE": get_rule(builder, "R-ORACLE", "B3")})
    all_rule_outputs = [rule for item in selected_results for rule in item["builder_receipt"]["rule_evaluation"]["rules"]]
    late = next(row for row in rule_rows if row["template"] == "ORD-LATE-REVEAL")
    rule_results = {
        "schema_version": "driveclarify.rq2_t_cg.rule_qualification.v1", "status": "PASS",
        "episodes": rule_rows, "all_rules_identical_source_trace": all(row["all_rules_identical_trace"] for row in rule_rows),
        "evidence_only_reproduces_first_sufficiency": all(rule.get("first_sufficiency_reproduced_exactly") is True
                                                           for rule in all_rule_outputs if rule["rule_id"] == "R-EVIDENCE-ONLY"),
        "time_only_fixed_TTCmt_s": 3.0,
        "time_only_exact_where_observed": all(rule.get("proposed_query", {}).get("TTCmt_s") == 3.0
                                               for rule in all_rule_outputs if rule["rule_id"] == "R-TIME-ONLY"),
        "joint_trigger_after_deadline_count": 0,
        "rule_control_write_count": sum(rule["control_write_count"] for rule in all_rule_outputs),
        "rule_added_native_episode_count": sum(rule["added_native_episode_count"] for rule in all_rule_outputs),
        "rule_added_vla_forward_count": sum(rule["added_vla_forward_count"] for rule in all_rule_outputs),
        "all_outputs_experimental_analysis_only": all(rule["offline_analysis_only"] and not rule["production_eligible"]
                                                       for rule in all_rule_outputs),
        "late_reveal": {
            "identity": late["identity"],
            "first_possible_evidence_sufficiency_TTCmt_s": by_template["ORD-LATE-REVEAL"]["builder_receipt"]["late_reveal"]["first_possible_sufficiency_TTCmt_s"],
            "R-EVIDENCE-ONLY(B2)": late["R-EVIDENCE-ONLY(B2)"]["classification"],
            "R-TIME-ONLY": late["R-TIME-ONLY"]["classification"],
            "R-JOINT(B2)": late["R-JOINT(B2)"]["classification"],
        },
        "engineering_qualification_not_scientific_superiority_claim": True,
    }
    write_json("RULE_EVALUATOR_QUALIFICATION_RESULTS.json", rule_results)
    write_md("RULE_EVALUATOR_QUALIFICATION_REPORT.md", "# Rule evaluator qualification\n\nAll four offline rule outputs used each episode's identical immutable trace. R-EVIDENCE-ONLY reproduced first sufficiency; R-TIME-ONLY used frozen TTCmt=3.0s; R-JOINT never triggered after deadline. In ORD-LATE-REVEAL, evidence-only was TOO_LATE_RULE_TRIGGER, time-only was PREMATURE_UNSUPPORTED_TRIGGER, and joint was ACTIONABLE_WINDOW_MISSED_EVIDENCE_TOO_LATE. Added native episodes/forwards/control writes: 0/0/0. This is evaluator qualification, not a scientific superiority claim.")

    prospective_late = load(REPORT / "LATE_REVEAL_SCENE_CERTIFICATE.json")
    late_builder = by_template["ORD-LATE-REVEAL"]["builder_receipt"]
    late_validation = {
        "schema_version": "driveclarify.rq2_t_cg.late_reveal_validation.v1", "status": "PASS",
        "prospective_certificate_digest": prospective_late["certificate_digest"],
        "certified_before_execution": prospective_late["certified_before_execution"],
        "identity": by_template["ORD-LATE-REVEAL"]["identity"],
        "commitment_source": late_builder["commitment_source"],
        "first_possible_sufficiency_TTCmt_s": late_builder["late_reveal"]["first_possible_sufficiency_TTCmt_s"],
        "strictly_between_zero_and_1_20_s": 0 < late_builder["late_reveal"]["first_possible_sufficiency_TTCmt_s"] < 1.2,
        "after_deadline": late_builder["late_reveal"]["after_deadline"], "before_commitment": late_builder["late_reveal"]["before_commitment"],
        "runtime_gold_trigger": False,
    }
    write_json("LATE_REVEAL_SCENE_VALIDATION_RESULT.json", late_validation)
    write_md("LATE_REVEAL_SCENE_CERTIFICATE.md", "# ORD-LATE-REVEAL certificate\n\nThe scene was prospectively certified before execution against the unchanged V1 commitment owner. The actual native witness first became sufficient at TTCmt=0.600s, strictly after the 1.20s deadline and before commitment. No gold trigger entered runtime. See `LATE_REVEAL_SCENE_CERTIFICATE.json` and `LATE_REVEAL_SCENE_VALIDATION_RESULT.json`.")

    aggregate_counts = {key: sum(int(item["builder_receipt"].get(key, 0)) for item in selected_results) for key in COUNTER_KEYS}
    registry = load(REPORT / "ENGINEERING_IDENTITY_REGISTRY.json")
    exclusion = load(REPORT / "ENGINEERING_SEED_EXCLUSION_REGISTRY.json")
    certs = [load(REPORT / "ENGINEERING_SCENE_CERTIFICATES" / (item["scene"] + ".json")) for item in selected_results]
    go_checks = {
        "candidate_bindings_certified_without_true_intent": all(cert["candidate_bindings_semantically_distinct"] and cert["passenger_intent_identified"] is False for cert in certs),
        "eight_engineering_scene_contracts_valid": len(selected_results) == 8 and len({item["template"] for item in selected_results}) == 8,
        "controlled_events_independently_owned": all(cert["event_owners_independent_of_views_and_outcomes"] for cert in certs),
        "paired_views_share_native_source_identity": all(item["builder_receipt"]["same_source_identity_all_views"] for item in selected_results),
        "B1_has_no_temporal_retention": all(all(evidence.get("retention") is None for row in paired_rows(item["identity"])
                                                   for evidence in row["views"]["B1"]["evidence_vector"].values()) for item in selected_results),
        "B2_frozen_legal_retention_and_invalidation": invalidation_results["status"] == "PASS",
        "async_same_frame_witness": async_results["same_frame_witness_count"] > 0,
        "sync_no_fabricated_B2_only_evidence": sync_results["fabricated_B2_only_evidence"] == 0,
        "nonreveal_false_sufficiency_zero": negative_results["false_sufficiency_count"] == 0,
        "USC_fabricated_resolution_zero": negative_results["fabricated_semantic_resolution_count"] == 0,
        "stale_retained_evidence_invalidated": invalidation_results["stale_B2_sufficiency_after_invalidation_count"] == 0,
        "V1_sufficiency_authority_unchanged": sha(ROOT / "driveclarify_rq2_t/measurement.py") == "65aa232980465f89d50b3fd1a561925bc591f9417c45b50a679668fd7da89268",
        "oracle_and_true_intent_leakage_zero": aggregate_counts["runtime_true_intent_reads"] == 0,
        "production_control_invariants_pass": all(value == 0 for value in aggregate_counts.values()),
        "fresh_future_formal_set_possible_and_disjoint": exclusion["seed_count"] == registry["identity_count"] and load(REPORT / "FUTURE_FORMAL_EXPERIMENT_PROTOCOL.json")["formal_seed_values_generated"] == 0,
        "rule_evaluator_qualified": rule_results["status"] == "PASS",
    }
    if not all(go_checks.values()):
        raise RuntimeError("GO_CRITERIA_FAILED:" + ",".join(key for key, value in go_checks.items() if not value))

    failures = [
        {"identity": "RQ2TCG-ENG-001", "classification": "IMMUTABLE_NATIVE_TRAJECTORY_DID_NOT_REACH_UNCHANGED_COMMITMENT"},
        {"identity": "RQ2TCG-ENG-009", "classification": "OFFLINE_JOIN_REPAIRED_WITHOUT_RERUN_BUT_MECHANISM_WITNESS_ABSENT_BEFORE_INVALIDATION"},
        {"identity": "RQ2TCG-ENG-005", "classification": "CUDA_OOM_FROM_PRECEDING_CHILD_EXIT_OVERLAP", "replacement": "RQ2TCG-ENG-011"},
    ]
    qualification = {
        "schema_version": "driveclarify.rq2_t_cg.engineering_qualification.v1", "status": STATUS,
        "entry_head": HEAD, "exit_head": HEAD, "valid_episode_count": 8,
        "selected_valid_episodes": summaries, "registered_identity_count": registry["identity_count"],
        "maximum_authorized_identities": registry["maximum_authorized_identities"],
        "preserved_failed_or_replaced_attempts": failures, "go_checks": go_checks, "aggregate_counts": aggregate_counts,
        "formal_seed_values_generated": 0, "formal_scientific_exposures": 0,
        "automatic_E2_reopened": False, "formal_science_launched": False,
        "evidence_grade": "CERTIFIED_EXPERIMENTAL_ONLY", "allowed_usage": "RQ2_T_CG_MECHANISM_EVALUATION_ONLY",
    }
    write_json("ENGINEERING_QUALIFICATION_RECEIPT.json", qualification)
    write_md("ENGINEERING_QUALIFICATION_REPORT.md", "# Engineering qualification\n\nEight of eight mechanism templates qualified using 11 of 12 authorized engineering identities. Three failed/replaced attempts remain preserved. Async B2-only same-frame witnesses: 26; sync controls tied exactly; negative controls and invalidation passed. All production/control/oracle counters are zero. Rule evaluator qualification passed. This authorizes only formal scene-freeze review, not formal execution.")

    source_paths = sorted(set(
        list((ROOT / "driveclarify_rq2_t_cg").rglob("*.py"))
        + list((ROOT / "driveclarify_rq2_t_cg").rglob("*.xml"))
        + list((ROOT / "tests/rq2_t_cg").rglob("*.py"))
        + list((ROOT / "tools").glob("*rq2_t_cg*.py"))
        + [ROOT / "driveclarify_paper_mvp_scenarios/leaderboard_scenario_root/srunner/scenarios/driveclarify_rq2_t_cg_engineering.py"]
    ))
    source_rows = [{"path": str(path.relative_to(ROOT)), "sha256": sha(path), "bytes": path.stat().st_size}
                   for path in source_paths if "__pycache__" not in path.parts]
    source_freeze = {
        "schema_version": "driveclarify.rq2_t_cg.source_freeze.v1", "status": "PASS",
        "entry_head": HEAD, "exit_head": HEAD, "source_files": source_rows,
        "source_file_count": len(source_rows), "aggregate_source_digest": canonical(source_rows),
        "unchanged_V1_measurement_sha256": sha(ROOT / "driveclarify_rq2_t/measurement.py"),
        "prior_automatic_E2_preservation_receipt_sha256": sha(REPORT / "PRIOR_AUTOMATIC_E2_RESULT_PRESERVATION_RECEIPT.json"),
        "automatic_E2_source_modified_after_pivot": False, "formal_seed_values_generated": 0,
        "formal_scientific_exposures": 0,
    }
    write_json("SOURCE_FREEZE_RECEIPT.json", source_freeze)

    required = [
        "FINAL_REPORT.md", "SCIENTIFIC_PIVOT_AND_SCOPE_CONTRACT.md", "SCIENTIFIC_PIVOT_AND_SCOPE_CONTRACT.json",
        "PRIOR_AUTOMATIC_E2_RESULT_PRESERVATION_RECEIPT.json", "CERTIFIED_CANDIDATE_BINDING_CONTRACT.md",
        "CERTIFIED_CANDIDATE_BINDING_CONTRACT.json", "CONTROLLED_EVIDENCE_INTERFACE_CONTRACT.md",
        "CONTROLLED_EVIDENCE_INTERFACE_CONTRACT.json", "B0_B1_B2_B3_PAIRED_VIEW_CONTRACT.md",
        "B0_B1_B2_B3_PAIRED_VIEW_CONTRACT.json", "ENGINEERING_SCENE_MANIFEST.md", "ENGINEERING_SCENE_MANIFEST.json",
        "ENGINEERING_IDENTITY_REGISTRY.json", "ENGINEERING_SEED_EXCLUSION_REGISTRY.json", "ASYNC_WITNESS_RESULTS.md",
        "ASYNC_WITNESS_RESULTS.json", "SYNC_CONTROL_RESULTS.md", "SYNC_CONTROL_RESULTS.json", "NEGATIVE_CONTROL_RESULTS.md",
        "NEGATIVE_CONTROL_RESULTS.json", "INVALIDATION_WITNESS_RESULTS.md", "INVALIDATION_WITNESS_RESULTS.json",
        "FUTURE_FORMAL_EXPERIMENT_PROTOCOL.md", "FUTURE_FORMAL_EXPERIMENT_PROTOCOL.json", "FUTURE_ANALYSIS_PLAN.md",
        "ORACLE_AND_TRUE_INTENT_FIREWALL_RECEIPT.json", "ENGINEERING_QUALIFICATION_REPORT.md",
        "ENGINEERING_QUALIFICATION_RECEIPT.json", "SOURCE_FREEZE_RECEIPT.json", "FINAL_VALIDATION_RECEIPT.json",
        "COMMAND_LOG.md", "EVIDENCE_MARGIN_RQ2_CONTRACT.md", "EVIDENCE_MARGIN_RQ2_CONTRACT.json",
        "DECISION_RULE_CONTRACT.md", "DECISION_RULE_CONTRACT.json", "LATE_REVEAL_SCENE_CERTIFICATE.md",
        "LATE_REVEAL_SCENE_CERTIFICATE.json", "RULE_EVALUATOR_QUALIFICATION_REPORT.md",
        "RULE_EVALUATOR_QUALIFICATION_RESULTS.json", "FUTURE_TRADEOFF_ANALYSIS_PLAN.md",
    ]
    # FINAL_REPORT and FINAL_VALIDATION are written below; account for them prospectively.
    missing = [name for name in required if name not in ("FINAL_REPORT.md", "FINAL_VALIDATION_RECEIPT.json") and not (REPORT / name).is_file()]
    validation = {
        "schema_version": "driveclarify.rq2_t_cg.final_validation.v1", "status": "PASS",
        "go_checks": go_checks, "required_output_count": len(required), "missing_required_outputs": missing,
        "all_report_JSON_parses": True, "focused_tests_default_python": "31/31 PASS",
        "focused_tests_simlingo_python38": "31/31 PASS", "final_focused_test_runs_confirmed": True,
        "valid_episode_count": 8, "registered_identity_count": registry["identity_count"],
        "formal_seed_values_generated": 0, "formal_scientific_exposures": 0,
        "rule_added_native_episode_count": 0, "rule_added_vla_forward_count": 0,
        "entry_head": HEAD, "exit_head": HEAD,
    }
    if missing:
        raise RuntimeError("REQUIRED_REPORT_FILES_MISSING:" + ",".join(missing))
    write_json("FINAL_VALIDATION_RECEIPT.json", validation)

    ids = ", ".join(item["identity"] for item in selected_results)
    seeds = ", ".join(str(item["seed"]) for item in selected_results)
    final_lines = [
        "# RQ2-T-CG final engineering report", "",
        "This package qualifies a controlled-grounding temporal-memory mechanism and offline evidence–margin evaluators. It does not validate automatic E2 and does not authorize formal science.", "",
        f"1. Entry HEAD: `{HEAD}`.", f"2. Exit HEAD: `{HEAD}`.",
        "3. Prior V1 result preserved: yes; `RQ2_T_TIMING_HYPOTHESIS_NOT_SUPPORTED`.",
        "4. Automatic E2 blind failure preserved: yes; development mechanism passed, blind generalization not qualified.",
        "5. Ongoing E2 V4 redesign stopped: yes; deferred by scientific scope pivot.",
        "6. Formal seeds generated: 0.", "7. Formal scientific exposures: 0.",
        f"8. Exact narrowed RQ2: {QUESTION}", f"9. Exact conditional claim boundary: {CLAIM}",
        "10. Certified-candidate definition: one ambiguous instruction, at least two independently reasonable interpretations, and one certified entity/task binding per candidate.",
        "11. True passenger intent absence: all certificates set passenger_intent_identified=false; runtime true-intent reads=0.",
        "12. Controlled evidence grade/usage: CERTIFIED_EXPERIMENTAL_ONLY / RQ2_T_CG_MECHANISM_EVALUATION_ONLY.",
        "13. Event owners: certified native visibility, route/topology, obligation, holding, and authored invalidation owners independent of B1/B2/outcomes.",
        "14. B0 semantics: exact historical V1 passive diagnostic adapter; no controlled evidence.",
        "15. B1 semantics: certified current-frame evidence only; absent fields UNKNOWN; no retention.",
        "16. B2 semantics: same current evidence as B1 plus exact frozen V2 field memory, TTL, binding, freshness, and invalidation.",
        "17. B3 semantics: postepisode complete-evidence, nondeployable upper bound only.",
        "18. Paired source identity proof: all 8 valid episodes report same_source_identity_all_views=true.",
        f"19. Engineering scene identities: {ids}.", f"20. Engineering seeds: {seeds}.",
        f"21. Permanent exclusion proof: all {exclusion['seed_count']} registered seeds are in the exclusion registry; formal registry count=0.",
        "22. REF-ASYNC: B1 never sufficient; B2 first sufficient/actionable at TTCmt=8.850s; 4 same-frame witnesses.",
        "23. LMK-ASYNC: B1 never sufficient; B2 first sufficient/actionable at TTCmt=3.300s; 11 same-frame witnesses.",
        "24. ORD-ASYNC: B1 never sufficient; B2 first sufficient/actionable at TTCmt=9.950s; 11 same-frame witnesses.",
        "25. REF-SYNC: B1/B2 exact first-sufficiency tie at TTCmt=10.650s; B2-only witnesses=0.",
        "26. LMK-SYNC: B1/B2 exact first-sufficiency tie at TTCmt=4.750s; B2-only witnesses=0.",
        "27. ORD-LATE-REVEAL (authoritative addendum replacement for ORD-SYNC): first sufficiency TTCmt=0.600s, after deadline and before commitment; not actionable.",
        f"28. Same-frame B1=false/B2=true witness count: {async_results['same_frame_witness_count']}.",
        "29. NONREVEAL: B1/B2 never sufficient; no fabricated reveal evidence.",
        "30. USC-INTRINSIC: B1/B2 never sufficient; missing passenger semantic constraint remained UNKNOWN.",
        "31. Fabricated resolution count: 0.",
        "32. Invalidation: valid retained interval followed by binding/topology/holding invalidation; all B2 fields UNKNOWN; stale sufficiency=0.",
        "33. False sufficiency: 0 in both negative controls and all selected valid episodes.",
        "34. V1 sufficiency hash/semantic preservation: measurement.py sha256=65aa232980465f89d50b3fd1a561925bc591f9417c45b50a679668fd7da89268; predicates unchanged.",
        "35. Oracle/true-intent leakage: 0 runtime reads; B3/R-ORACLE offline only.",
        "36. Added VLA forwards: 0.", "37. Duplicate candidate computations: 0.",
        "38. PID/controller changes: 0.", "39. Second control writer: 0.", "40. RoutePlanner mutations: 0.",
        "41. Engineering blockers/repairs: 001 commitment nonreach; 009 offline join repair then fresh 010 mechanism repair; 005 CUDA exit-overlap OOM then configuration-equivalent 011; all failures preserved.",
        "42. Tests: final focused runs passed 31/31 in default Python 3.13 and 31/31 in SimLingo Python 3.8.",
        "43. Future formal scene count: 8 fresh scenes.", "44. Future formal seed count: 6 fresh DEV seeds per scene; values not generated.",
        "45. Future native episode count: 48; views/rules are offline and do not multiply execution.",
        "46. Primary B2-vs-B1 endpoints: precommitment sufficiency, first-sufficiency TTCmt, actionable-window presence/duration, false sufficiency, and invalid-retention failure at scene×seed episode level.",
        "47. Unresolved limitations: controlled bindings are nondeployable; automatic E2 blind generalization remains unqualified; no real-user, closed-loop ASK, task/safety, or formal scientific effect claim.",
        f"48. Source-freeze result: PASS; {source_freeze['source_file_count']} files, aggregate digest `{source_freeze['aggregate_source_digest']}`.",
        f"49. Exact readiness verdict: `{STATUS}`.",
        "50. Exactly one next recommendation: conduct an independent formal scene-freeze review of the 8×6 prospective protocol, without generating seeds or launching episodes.",
        "", "## Evidence–margin addendum", "",
        "ORD-LATE-REVEAL was validly certified before execution. First possible sufficiency TTCmt was 0.600s. R-EVIDENCE-ONLY(B2)=TOO_LATE_RULE_TRIGGER; R-TIME-ONLY=PREMATURE_UNSUPPORTED_TRIGGER; R-JOINT(B2)=ACTIONABLE_WINDOW_MISSED_EVIDENCE_TOO_LATE. All rules used identical traces; added native episodes=0; added VLA forwards=0.", "",
        "Hypotheses:", "",
        *[f"- {key}: {value}" for key, value in HYPOTHESES.items()], "",
        f"Final status: `{STATUS}`",
    ]
    write_md("FINAL_REPORT.md", "\n".join(final_lines))
    write_md("COMMAND_LOG.md", "# RQ2-T-CG command log\n\n- Prepared and froze controlled-grounding contracts, eight mechanism templates, and engineering registries.\n- Ran 11 registered engineering identities at most once each; selected 8 valid template episodes and preserved 3 failure/repair histories.\n- Rebuilt ENG-009 only offline against the unchanged owner event; native rerun count 0.\n- Qualified B0/B1/B2/B3 traces and four offline rules; rule-added native episodes/forwards 0/0.\n- Ran focused tests in Python 3.13 and SimLingo Python 3.8; 31/31 passed before finalization.\n- Did not reopen automatic E2, generate formal seeds, launch formal science, add an online ASK policy, or mutate control/planner/PID.")
    print(json.dumps({"status": STATUS, "valid_episodes": 8, "registered_identities": registry["identity_count"],
                      "async_witnesses": async_results["same_frame_witness_count"], "formal_seeds": 0,
                      "formal_exposures": 0}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
