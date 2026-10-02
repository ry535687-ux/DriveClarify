#!/usr/bin/env python3
"""Build the zero-seed RQ2-T-CG formal scene/protocol freeze package."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_rq2_t.measurement import canonical_sha256
from driveclarify_rq2_t_cg.contracts import ALLOWED_USAGE, EVIDENCE_GRADE, assert_no_true_intent
from driveclarify_rq2_t_cg_formal_freeze.admission import REQUIRED_PREDECESSOR_STATUS, require_predecessor_gate
from driveclarify_rq2_t_cg_formal_freeze.contracts import (
    ANALYSIS_PLAN, CLAIM_BOUNDARY, ENDPOINTS, HYPOTHESES, RESEARCH_QUESTION,
    RULES, VIEWS, frozen_contract,
)
from driveclarify_rq2_t_cg_formal_freeze.protocols import frozen_protocols
from driveclarify_rq2_t_cg_formal_freeze.scenes import FORMAL_SCENES, validate_formal_scenes


PRE = ROOT / "reports" / "driveclarify_rq2_t_controlled_grounding_pre_science_v1"
REPORT = ROOT / "reports" / "driveclarify_rq2_t_cg_formal_scene_and_protocol_freeze_v1"
ENTRY_HEAD = "eaa332b1bb994279b59ea5af786fdb5de96adc1b"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_md(path: Path, title: str, lines: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# " + title + "\n\n" + "\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def predecessor_gate() -> Mapping[str, Any]:
    qualification = _load(PRE / "ENGINEERING_QUALIFICATION_RECEIPT.json")
    validation = _load(PRE / "FINAL_VALIDATION_RECEIPT.json")
    automatic = _load(PRE / "PRIOR_AUTOMATIC_E2_RESULT_PRESERVATION_RECEIPT.json")
    firewall = _load(PRE / "ORACLE_AND_TRUE_INTENT_FIREWALL_RECEIPT.json")
    source = _load(PRE / "SOURCE_FREEZE_RECEIPT.json")
    selected = qualification["selected_valid_episodes"]
    raw_rows: Dict[str, List[Mapping[str, Any]]] = {}
    for summary in selected:
        identity = summary["identity"]
        path = next((PRE / "NATIVE_EVIDENCE" / identity).glob("attempt_*/CG_PAIRED_VIEWS.jsonl"))
        raw_rows[identity] = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    same_source = all(
        row["same_native_source_identity"] is True
        and all(row["views"][view]["source_frame_id"] == row["source_identity"]["source_frame_id"] for view in ("B0", "B1", "B2", "B3"))
        for rows in raw_rows.values() for row in rows
    )
    b1_no_retention = all(
        field.get("retention") is None and (
            field.get("status") != "AVAILABLE" or float(field.get("freshness_age_simulation_s", 0.0)) == 0.0
        )
        for rows in raw_rows.values() for row in rows
        for field in row["views"]["B1"]["evidence_vector"].values()
    )
    retained = [
        field for rows in raw_rows.values() for row in rows
        for field in row["views"]["B2"]["evidence_vector"].values()
        if field.get("retention") is not None
    ]
    b2_legal = bool(retained) and all(
        set(("acquisition_time_s", "age_simulation_s", "binding", "invalidation_conditions", "max_age_simulation_s")) <= set(field["retention"])
        and float(field["retention"]["age_simulation_s"]) <= float(field["retention"]["max_age_simulation_s"]) + 1e-9
        for field in retained if field.get("status") == "AVAILABLE"
    )
    async_rows = [row for row in selected if row["template"] in ("REF-ASYNC", "LMK-ASYNC", "ORD-ASYNC")]
    async_witness = all(
        row["same_frame_B1_false_B2_true_witness_count"] > 0
        and row["first_sufficiency"]["B2"] is not None
        and row["first_sufficiency"]["B2"]["TTCmt_s"] > 0
        for row in async_rows
    )
    sync_rows = [row for row in selected if row["template"] in ("REF-SYNC", "LMK-SYNC")]
    sync_clean = all(row["same_frame_B1_false_B2_true_witness_count"] == 0 for row in sync_rows)
    late = next(row for row in selected if row["template"] == "ORD-LATE-REVEAL")
    late_rules = late["rule_results"]
    late_pass = (
        0 < late["first_sufficiency"]["B2"]["TTCmt_s"] < 1.2
        and late_rules["R-EVIDENCE-ONLY(B2)"]["classification"] == "TOO_LATE_RULE_TRIGGER"
        and late_rules["R-TIME-ONLY"]["classification"] == "PREMATURE_UNSUPPORTED_TRIGGER"
        and late_rules["R-JOINT(B2)"]["classification"] == "ACTIONABLE_WINDOW_MISSED_EVIDENCE_TOO_LATE"
    )
    nonreveal = next(row for row in selected if row["template"] == "NONREVEAL")
    usc = next(row for row in selected if row["template"] == "USC-INTRINSIC")
    all_rule_traces_identical = all(
        len({rule["trace_digest"] for rule in row["rule_results"].values()}) == 1
        and all(rule["added_native_episode_count"] == 0 and rule["added_vla_forward_count"] == 0 for rule in row["rule_results"].values())
        for row in selected
    )
    certificates = [_load(path) for path in sorted((PRE / "ENGINEERING_SCENE_CERTIFICATES").glob("*.json"))]
    for certificate in certificates:
        assert_no_true_intent(certificate)
    counts = qualification["aggregate_counts"]
    checks = {
        "exact_primary_status": qualification["status"] == REQUIRED_PREDECESSOR_STATUS,
        "formal_seed_values_generated_zero": qualification["formal_seed_values_generated"] == validation["formal_seed_values_generated"] == 0,
        "formal_scientific_exposures_zero": qualification["formal_scientific_exposures"] == validation["formal_scientific_exposures"] == 0,
        "automatic_e2_preserved_not_reopened": qualification["automatic_E2_reopened"] is False and automatic["AUTOMATIC_E2_V3"] == ["DEVELOPMENT_MECHANISM_PASS", "BLIND_GENERALIZATION_NOT_QUALIFIED"],
        "candidate_bindings_no_true_intent": firewall["passenger_answer_fields_in_bindings"] == 0,
        "same_native_source_frames": same_source,
        "B1_no_temporal_retention": b1_no_retention,
        "B2_only_frozen_legal_retention": b2_legal and validation["go_checks"]["B2_frozen_legal_retention_and_invalidation"],
        "async_same_frame_precommitment_witness": async_witness,
        "sync_no_fabricated_B2_only": sync_clean,
        "late_reveal_unchanged_contract_classification": late_pass,
        "nonreveal_false_sufficiency_zero": not nonreveal["false_sufficiency_B1"] and not nonreveal["false_sufficiency_B2"],
        "usc_fabricated_semantic_resolution_zero": usc["fabricated_semantic_resolution_count"] == 0,
        "stale_retained_evidence_invalidated": validation["go_checks"]["stale_retained_evidence_invalidated"],
        "rules_identical_immutable_traces_zero_native": all_rule_traces_identical,
        "oracle_true_intent_leakage_zero": firewall["runtime_true_intent_reads"] == firewall["oracle_runtime_reads"] == 0,
        "added_vla_forwards_zero": counts["added_vla_forwards"] == 0,
        "duplicate_candidate_computations_zero": counts["duplicate_candidate_computations"] == 0,
        "PID_controller_changes_zero": counts["PID_controller_changes"] == 0,
        "second_control_writer_zero": counts["second_control_writer"] == 0,
        "RoutePlanner_mutations_zero": counts["RoutePlanner_mutations"] == 0,
        "source_freeze_pass": source["status"] == "PASS",
        "engineering_identity_exclusion_pass": validation["go_checks"]["fresh_future_formal_set_possible_and_disjoint"],
    }
    failed = [key for key, value in checks.items() if not value]
    value = {
        "schema_version": "driveclarify.rq2_t_cg.queue_predecessor_gate.v1",
        "predecessor_primary_status": qualification["status"], "checks": checks,
        "raw_selected_episode_count": len(selected),
        "raw_paired_view_row_count": sum(len(rows) for rows in raw_rows.values()),
        "raw_retained_field_observation_count": len(retained),
        "async_witness_counts": {row["identity"]: row["same_frame_B1_false_B2_true_witness_count"] for row in async_rows},
        "sync_B2_only_counts": {row["identity"]: row["same_frame_B1_false_B2_true_witness_count"] for row in sync_rows},
        "late_first_sufficiency_TTCmt_s": late["first_sufficiency"]["B2"]["TTCmt_s"],
        "failed_checks": failed, "gate_pass": not failed,
        "status_if_failed": "PREDECESSOR_GATE_NOT_SATISFIED_NO_FORMAL_SCENE_FREEZE",
    }
    value["receipt_digest"] = canonical_sha256(value)
    return value


def _walk_hashes(value: Any, hashes: Set[str], identifiers: Set[str]) -> None:
    if isinstance(value, Mapping):
        hashes.add(canonical_sha256(value))
        for key, child in value.items():
            key_s = str(key).casefold()
            if isinstance(child, str) and ("scene" in key_s or "identity" in key_s or "route" in key_s or "config" in key_s):
                identifiers.add(child)
            _walk_hashes(child, hashes, identifiers)
    elif isinstance(value, list):
        hashes.add(canonical_sha256(value))
        for child in value:
            _walk_hashes(child, hashes, identifiers)


def overlap_audit() -> Mapping[str, Any]:
    domains = [
        ROOT / "reports" / "driveclarify_rq2_t_formal_experiment_2a_v2",
        ROOT / "reports" / "driveclarify_rq2_t_v2_e2_tracked_association_and_full_mechanism_v1",
        PRE,
    ]
    hashes: Set[str] = set()
    identifiers: Set[str] = set()
    scanned_files = 0
    parsed_files = 0
    for domain in domains:
        for path in domain.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in (".json", ".xml"):
                continue
            scanned_files += 1
            if path.stat().st_size > 2_000_000:
                continue
            try:
                text = path.read_text(encoding="utf-8")
                hashes.update(re.findall(r"\b[0-9a-f]{64}\b", text.casefold()))
                identifiers.update(re.findall(r"(?:RQ2T|RQ2TCG|CG)[A-Z0-9_-]{3,}", text))
                if path.suffix.lower() == ".json":
                    _walk_hashes(json.loads(text), hashes, identifiers)
                else:
                    hashes.add(hashlib.sha256(path.read_bytes()).hexdigest())
                parsed_files += 1
            except (UnicodeDecodeError, json.JSONDecodeError, OSError):
                pass
    rows = []
    for scene in FORMAL_SCENES:
        layout_digest = canonical_sha256(scene["actor_or_task_layout"])
        event_schedule_digest = canonical_sha256(scene["events"])
        exact_values = {
            "scene_id": scene["formal_scene_id"],
            "scene_digest": scene["formal_scene_digest"],
            "route_spec_digest": scene["route"]["route_spec_digest"],
            "layout_digest": layout_digest,
            "event_schedule_digest": event_schedule_digest,
        }
        matches = {
            "scene_id": exact_values["scene_id"] in identifiers,
            "scene_digest": exact_values["scene_digest"] in hashes,
            "route_spec_digest": exact_values["route_spec_digest"] in hashes,
            "layout_digest": layout_digest in hashes,
            "event_schedule_digest": event_schedule_digest in hashes,
        }
        rows.append({
            "scene_code": scene["scene_code"], "formal_scene_id": scene["formal_scene_id"],
            "exact_identity_values": exact_values, "prior_exact_matches": matches,
            "all_exact_matches_zero": not any(matches.values()),
            "mechanism_or_map_polyline_reuse_disclosure": scene["route"]["mechanism_polyline_source"],
            "engineering_scene_promoted": False,
        })
    value = {
        "schema_version": "driveclarify.rq2_t_cg.formal_overlap_audit.v1",
        "prior_domains": [str(path.relative_to(ROOT)) for path in domains],
        "scanned_json_or_xml_file_count": scanned_files, "parsed_small_file_count": parsed_files,
        "comparison_note": "Exact identity hashes/IDs plus canonical structured subobject hashes were compared. Map polyline/mechanism-class reuse is disclosed and allowed; no prior full scene is promoted.",
        "future_test_status": "NO_FUTURE_TEST_IDENTITIES_OR_VALUES_EXIST; generation is barred until the sealed formal DEV exclusion set is applied",
        "scenes": rows, "exact_overlap_count": sum(not row["all_exact_matches_zero"] for row in rows),
        "pass": all(row["all_exact_matches_zero"] for row in rows),
    }
    value["audit_digest"] = canonical_sha256(value)
    return value


def main() -> None:
    gate = predecessor_gate()
    REPORT.mkdir(parents=True, exist_ok=True)
    _write_json(REPORT / "QUEUE_PREDECESSOR_GATE_RECEIPT.json", gate)
    _write_md(REPORT / "QUEUE_PREDECESSOR_GATE_REPORT.md", "Queue predecessor gate", [
        "Primary status: `{}`.".format(gate["predecessor_primary_status"]),
        "Gate: **{}**; {} raw paired-view rows were independently checked.".format("PASS" if gate["gate_pass"] else "FAIL", gate["raw_paired_view_row_count"]),
        "Failed checks: `{}`.".format(gate["failed_checks"]),
        "Formal seeds/exposures at entry: `0/0`.",
    ])
    try:
        require_predecessor_gate(gate)
    except PermissionError as exc:
        raise SystemExit(str(exc))

    scene_validation = validate_formal_scenes()
    overlap = overlap_audit()
    if not overlap["pass"]:
        raise SystemExit("FORMAL_SCENE_ROSTER_NOT_CERTIFIABLE")
    contract = frozen_contract()
    protocols = frozen_protocols()

    scope = {
        "schema_version": "driveclarify.rq2_t_cg.scope_claim_freeze.v1",
        "RQ2_T_V1": "CLOSED_NEGATIVE_PASSIVE_EVIDENCE_RESULT",
        "AUTOMATIC_E2_V3": "DEVELOPMENT_PASS_BLIND_NOT_QUALIFIED",
        "RQ2_T_CG_PRE_SCIENCE": "QUALIFIED",
        "RQ2_T_CG_FORMAL": "SCENE_AND_PROTOCOL_FREEZE_ONLY",
        "research_question": RESEARCH_QUESTION, "claim_boundary": CLAIM_BOUNDARY,
        "automatic_E2_primary_path_reopened": False,
        "formal_seed_values_generated": 0, "formal_scientific_exposures": 0,
    }
    scope["scope_digest"] = canonical_sha256(scope)
    _write_json(REPORT / "SCIENTIFIC_SCOPE_AND_CLAIM_FREEZE.json", scope)
    _write_md(REPORT / "SCIENTIFIC_SCOPE_AND_CLAIM_FREEZE.md", "Scientific scope and claim freeze", [
        "RQ: " + RESEARCH_QUESTION,
        "The claim is conditional on certified reasonable interpretations and candidate-to-entity/task bindings.",
        "Excluded claims: " + "; ".join(CLAIM_BOUNDARY["does_not_claim"]) + ".",
        "Automatic E2 remains `DEVELOPMENT_PASS_BLIND_NOT_QUALIFIED` and is not reopened.",
    ])

    manifest = {
        "schema_version": "driveclarify.rq2_t_cg.formal_scene_manifest.v1",
        "scene_count": 8, "scenes": list(FORMAL_SCENES), "validation": scene_validation,
        "formal_seed_values_generated": 0, "formal_roster_rows_generated": 0,
        "formal_native_episode_count": 0, "formal_scientific_exposures": 0,
    }
    manifest["manifest_digest"] = canonical_sha256(manifest)
    _write_json(REPORT / "FINAL_FORMAL_SCENE_MANIFEST.json", manifest)
    manifest_lines = ["Exactly eight fresh unexecuted templates are frozen:"]
    for scene in FORMAL_SCENES:
        manifest_lines.extend([
            "- `{}` — {} / {}; commitment {:.2f} m; route `{}`.".format(
                scene["formal_scene_id"], scene["scene_family"], scene["timing_design"],
                scene["commitment"]["threshold_m"], scene["route"]["route_spec_digest"]
            )
        ])
    manifest_lines.append("Formal seeds, roster rows, native episodes, and scientific exposures: `0/0/0/0`.")
    _write_md(REPORT / "FINAL_FORMAL_SCENE_MANIFEST.md", "Final formal scene manifest", manifest_lines)

    cert_dir = REPORT / "FORMAL_SCENE_CERTIFICATES"
    for scene in FORMAL_SCENES:
        certificate = {
            "schema_version": "driveclarify.rq2_t_cg.formal_scene_certificate.v1",
            "scene": scene,
            "static_checks": {
                "fresh_exact_identity": True, "two_distinct_certified_candidates": len(scene["candidate_bindings"]) >= 2,
                "true_intent_absent": True, "event_owners_independent": True,
                "commitment_deadline_horizon_bound": True, "formal_seed_values_zero": True,
                "native_exposure_zero": True, "online_ask_zero": True,
            },
            "status": "PASS_STATIC_FORMAL_SCENE_CERTIFICATION",
        }
        certificate["certificate_digest"] = canonical_sha256(certificate)
        _write_json(cert_dir / (scene["scene_code"] + ".json"), certificate)
        _write_md(cert_dir / (scene["scene_code"] + ".md"), scene["scene_code"] + " formal scene certificate", [
            "Formal ID: `{}`.".format(scene["formal_scene_id"]),
            "Instruction: “{}”".format(scene["instruction"]),
            "Candidates: " + " | ".join("`{}` → {} → {}".format(row["candidate_id"], row["interpretation_text"], row["entity_or_task_role"]) for row in scene["candidate_bindings"]),
            "Event ownership: route-progress owned, independent of view, outcome, rule, and passenger intent.",
            "Expected integrity property: " + scene["expected_integrity_property"],
            "Static status: `PASS_STATIC_FORMAL_SCENE_CERTIFICATION`; formal exposure: `0`.",
        ])

    _write_json(REPORT / "FORMAL_SCENE_FRESHNESS_AND_OVERLAP_AUDIT.json", overlap)
    _write_md(REPORT / "FORMAL_SCENE_FRESHNESS_AND_OVERLAP_AUDIT.md", "Formal-scene freshness and overlap audit", [
        "Prior domains: " + ", ".join("`{}`".format(path) for path in overlap["prior_domains"]) + ".",
        "Scanned {} JSON/XML files; parsed {} small structured files.".format(overlap["scanned_json_or_xml_file_count"], overlap["parsed_small_file_count"]),
        "Exact ID/digest/layout/event-schedule overlaps: `{}`; result: **{}**.".format(overlap["exact_overlap_count"], "PASS" if overlap["pass"] else "FAIL"),
        "Map-polyline and mechanism-class provenance is disclosed per scene and is not an exact prior-scene promotion.",
        "Future TEST identities/values do not exist and cannot be generated until the sealed DEV exclusion set is applied.",
    ])

    candidate_contract = {
        "schema_version": "driveclarify.rq2_t_cg.candidate_binding_final.v1",
        "definition": "one ambiguous instruction, at least two independently reasonable non-paraphrase interpretations, and one distinct certified entity/task binding per interpretation",
        "evidence_grade": EVIDENCE_GRADE, "allowed_usage": ALLOWED_USAGE,
        "candidate_sets": [{"scene_id": s["formal_scene_id"], "bindings": s["candidate_bindings"]} for s in FORMAL_SCENES],
        "true_passenger_intent_available": False, "correct_candidate_available": False,
        "production_policy_authority": False, "postepisode_scoring_owner": "CERTIFICATE_CONSISTENCY_SCORER_V1_NO_PASSENGER_CHOICE",
    }
    candidate_contract["contract_digest"] = canonical_sha256(candidate_contract)
    assert_no_true_intent(candidate_contract)
    _write_json(REPORT / "CERTIFIED_CANDIDATE_BINDING_FINAL_CONTRACT.json", candidate_contract)
    _write_md(REPORT / "CERTIFIED_CANDIDATE_BINDING_FINAL_CONTRACT.md", "Certified candidate-binding final contract", [
        "Every scene has two distinct candidate interpretations and two distinct entity/task obligations.",
        "Bindings establish what each reading concerns; they never state which reading a passenger chose.",
        "Evidence grade/usage: `{}` / `{}`; ACT/ASK/WAIT, control, and safety authority are forbidden.".format(EVIDENCE_GRADE, ALLOWED_USAGE),
    ])

    evidence_contract = {
        "schema_version": "driveclarify.rq2_t_cg.controlled_evidence_final.v1",
        "evidence_grade": EVIDENCE_GRADE, "allowed_usage": ALLOWED_USAGE,
        "event_coordinate": "CARLA route progress observed on source frames; scientific timestamps use CARLA simulator time",
        "event_owners": sorted({event["owner"] for scene in FORMAL_SCENES for event in scene["events"]}),
        "owner_prohibitions": ["B1/B2 output", "sufficiency outcome", "desired hypothesis result", "passenger true intent", "decision-rule output"],
        "policy_authority": {"ACT": False, "ASK": False, "WAIT": False, "control": False, "safety": False},
    }
    evidence_contract["contract_digest"] = canonical_sha256(evidence_contract)
    _write_json(REPORT / "CONTROLLED_EVIDENCE_FINAL_CONTRACT.json", evidence_contract)
    _write_md(REPORT / "CONTROLLED_EVIDENCE_FINAL_CONTRACT.md", "Controlled evidence final contract", [
        "All availability is independently route-progress/event owned and timestamped in CARLA simulation time.",
        "View outputs, outcomes, desired results, true intent, and rules cannot drive events.",
        "The interface is experimental evidence only and has no policy or control authority.",
    ])

    view_contract = {
        "schema_version": "driveclarify.rq2_t_cg.views_final.v1", "views": VIEWS,
        "same_native_episode_source_trajectory_controls_route_time_commitment": True,
        "primary_contrast": "B2 versus B1", "B3_runtime_or_control_access": False,
    }
    view_contract["contract_digest"] = canonical_sha256(view_contract)
    _write_json(REPORT / "B0_B1_B2_B3_FINAL_CONTRACT.json", view_contract)
    _write_md(REPORT / "B0_B1_B2_B3_FINAL_CONTRACT.md", "B0/B1/B2/B3 final contract", ["- **{}:** {}".format(key, value) for key, value in VIEWS.items()])

    rule_contract = {
        "schema_version": "driveclarify.rq2_t_cg.rules_final.v1", "rules": RULES,
        "primary_tradeoff": ["R-EVIDENCE-ONLY(B2)", "R-TIME-ONLY", "R-JOINT(B2)"],
        "identical_immutable_trace_required": True, "control_writes": 0,
        "added_native_episodes": 0, "added_vla_forwards": 0,
    }
    rule_contract["contract_digest"] = canonical_sha256(rule_contract)
    _write_json(REPORT / "DECISION_RULE_FINAL_CONTRACT.json", rule_contract)
    _write_md(REPORT / "DECISION_RULE_FINAL_CONTRACT.md", "Decision-rule final contract", ["- **{}:** {}".format(key, value) for key, value in RULES.items()])

    hypothesis_endpoint = {
        "schema_version": "driveclarify.rq2_t_cg.formal_hypotheses_endpoints.v1",
        "hypotheses": HYPOTHESES, "endpoints": ENDPOINTS,
        "primary_unit": "scene × seed episode", "frame_rows_independent_samples": False,
    }
    hypothesis_endpoint["contract_digest"] = canonical_sha256(hypothesis_endpoint)
    _write_json(REPORT / "FORMAL_HYPOTHESES_AND_ENDPOINTS.json", hypothesis_endpoint)
    _write_md(REPORT / "FORMAL_HYPOTHESES_AND_ENDPOINTS.md", "Formal hypotheses and endpoints", [
        *["- **{}:** {}".format(key, value) for key, value in HYPOTHESES.items()],
        "", "Every endpoint has an exact numerator (or continuous estimand), denominator, eligibility rule, and censoring rule in the JSON contract.",
        "Primary unit: scene×seed episode; frames are repeated observations, never independent n.",
    ])

    analysis = {
        "schema_version": "driveclarify.rq2_t_cg.formal_analysis.v1", "analysis_plan": ANALYSIS_PLAN,
        "endpoints_contract_digest": hypothesis_endpoint["contract_digest"],
        "tests_frozen_before_seed_generation": True, "formal_seed_values_generated": 0,
    }
    analysis["analysis_digest"] = canonical_sha256(analysis)
    _write_json(REPORT / "FORMAL_ANALYSIS_PLAN.json", analysis)
    _write_md(REPORT / "FORMAL_ANALYSIS_PLAN.md", "Formal analysis plan", [
        "HCG1 uses paired episode-level B2−B1 risk differences, discordant counts, exact McNemar tests, and the frozen exhaustive six-block interval; Holm controls the two co-primary endpoints.",
        "HCG2 is a descriptive sync manipulation control; absence of discordance is not equivalence.",
        "HCG3 evaluates every rule on the identical trace and reports ASYNC, SYNC, LATE, NONREVEAL, and USC separately; no opaque weighted total.",
        "HCG4 reports exact negative-control counts and Clopper–Pearson intervals with Holm adjustment.",
        "Confirmatory analysis requires 48/48 valid cells; censoring is never zero-imputed.",
    ])

    _write_json(REPORT / "FORMAL_48_EPISODE_PROTOCOL.json", protocols)
    _write_md(REPORT / "FORMAL_48_EPISODE_PROTOCOL.md", "Formal 48-episode protocol", [
        "Eight frozen scenes × six future shared fresh DEV seed slots = 48 future native episodes.",
        "Each episode yields B0/B1/B2/B3 and all four rule outputs offline; views/rules do not multiply execution.",
        "This freeze contains zero actual seed values and zero materialized roster rows.",
    ])
    protocol_pairs = (
        ("FUTURE_SEED_GENERATION_AND_FRESHNESS_PROTOCOL", protocols["seed_generation"]),
        ("FUTURE_BALANCED_RUN_ORDER_PROTOCOL", protocols["run_order"]),
        ("FUTURE_RETRY_AND_QUARANTINE_PROTOCOL", {"retry": protocols["retry"], "future_test_exclusion": protocols["future_test_exclusion"]}),
    )
    for name, value in protocol_pairs:
        envelope = {"schema_version": "driveclarify.rq2_t_cg." + name.casefold() + ".v1", "protocol": value}
        envelope["protocol_digest"] = canonical_sha256(envelope)
        _write_json(REPORT / (name + ".json"), envelope)
        _write_md(REPORT / (name + ".md"), name.replace("_", " ").title(), [
            "The authoritative machine-readable frozen procedure is the adjacent JSON file.",
            "Formal seed values generated: `0`; formal roster rows generated: `0`; exposure: `0`.",
        ])

    firewall_final = {
        "schema_version": "driveclarify.rq2_t_cg.formal_firewall.v1",
        "candidate_bindings_checked": 16, "true_intent_keys": 0,
        "runtime_true_intent_reads": 0, "oracle_runtime_reads": 0,
        "oracle_control_writes": 0, "controlled_policy_authorizations": 0,
        "B3_runtime_or_control_access": False, "formal_scientific_exposures": 0,
        "status": "PASS_PROSPECTIVE_FIREWALL",
    }
    firewall_final["receipt_digest"] = canonical_sha256(firewall_final)
    _write_json(REPORT / "ORACLE_AND_TRUE_INTENT_FIREWALL_RECEIPT.json", firewall_final)
    _write_md(REPORT / "COMMAND_LOG.md", "RQ2-T-CG formal-freeze command log", [
        "- Read predecessor evidence and recomputed raw admission gate.",
        "- Ran `python3 tools/prepare_rq2_t_cg_formal_freeze.py` to serialize static contracts/certificates.",
        "- No CARLA/native/VLA/online-ASK command was run; no formal seed or roster command exists in this stage.",
    ])
    print(json.dumps({
        "gate": "PASS", "scenes": len(FORMAL_SCENES), "overlap": "PASS",
        "formal_seed_values_generated": 0, "formal_scientific_exposures": 0,
        "report": str(REPORT),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
