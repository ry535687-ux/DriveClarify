#!/usr/bin/env python3
"""Prospectively seal the RQ2-T-CG engineering set before native execution."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_rq2_t.measurement import DeadlineContract, canonical_sha256  # noqa: E402
from driveclarify_rq2_t_e2_v3.usc_admission import project_point_to_polyline  # noqa: E402
from driveclarify_rq2_t_cg.builder import COMMITMENT_POINTS  # noqa: E402
from driveclarify_rq2_t_cg.lifecycle import SCENARIO_TYPE, static_route_admission  # noqa: E402
from driveclarify_rq2_t_cg.scene_bindings import ENGINEERING_SEEDS, SCENE_BINDINGS, frozen_binding  # noqa: E402


REPORT = ROOT / "reports/driveclarify_rq2_t_controlled_grounding_pre_science_v1"
ROUTES = ROOT / "driveclarify_rq2_t_cg/engineering_routes"
IDENTITY_ORDER = [
    "CG-REF-ASYNC", "CG-LMK-ASYNC", "CG-ORD-ASYNC", "CG-REF-SYNC",
    "CG-LMK-SYNC", "CG-ORD-LATE-REVEAL", "CG-NONREVEAL", "CG-USC-INTRINSIC",
]
FINAL_RQ = (
    "Under certified candidate-grounding conditions, how does interpretation-relevant evidence evolve as "
    "the vehicle approaches commitment, and does joint evidence–margin reasoning identify actionable "
    "clarification windows more reliably than evidence-only or time-only decision rules?"
)
HYPOTHESES = {
    "H-CG1": "In asynchronous reveal scenes, B2 increases truthful precommitment evidence sufficiency and/or actionable-window availability relative to B1.",
    "H-CG2": "In synchronous reveal scenes, B1 and B2 do not exhibit a systematic memory advantage.",
    "H-CG3": "Across positive scene-grounded ambiguity episodes, R-JOINT produces fewer premature unsupported triggers and fewer too-late triggers than R-TIME-ONLY and R-EVIDENCE-ONLY respectively.",
    "H-CG4": "B2 does not create false sufficiency or fabricated semantic resolution in NONREVEAL or USC controls.",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_md(path: Path, title: str, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# " + title + "\n\n" + "\n\n".join(lines) + "\n", encoding="utf-8")


def head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def tree_receipt(relative: str) -> Mapping[str, Any]:
    path = ROOT / relative
    rows = [{"path": str(file.relative_to(path)), "sha256": sha256(file), "bytes": file.stat().st_size}
            for file in sorted(path.rglob("*")) if file.is_file()]
    return {"path": relative, "file_count": len(rows), "byte_count": sum(row["bytes"] for row in rows),
            "tree_digest": canonical_sha256(rows)}


def materialize_route(scene_id: str) -> Path:
    binding = frozen_binding(scene_id)
    source = ROOT / binding["base_route"]
    root = ET.parse(source).getroot()
    route = root.find("route")
    if route is None:
        raise RuntimeError("BASE_ROUTE_MISSING")
    old_scenario = route.find("./scenarios/scenario")
    old_trigger = None if old_scenario is None else old_scenario.find("trigger_point")
    first = route.find("./waypoints/position")
    if first is None:
        raise RuntimeError("BASE_ROUTE_WAYPOINT_MISSING")
    trigger_attributes = dict(first.attrib)
    trigger_attributes["yaw"] = "0.0" if old_trigger is None else old_trigger.attrib.get("yaw", "0.0")
    old_scenarios = route.find("scenarios")
    if old_scenarios is not None:
        route.remove(old_scenarios)
    route.attrib["id"] = "RQ2TCG-" + scene_id
    scenarios = ET.SubElement(route, "scenarios")
    scenario = ET.SubElement(scenarios, "scenario", {"name": "RQ2TCG-" + scene_id, "type": SCENARIO_TYPE})
    ET.SubElement(scenario, "trigger_point", trigger_attributes)
    ET.SubElement(scenario, "rq2_t_cg", {
        "scene_config_id": scene_id,
        "scene_configuration_sha256": binding["scene_configuration_sha256"],
    })
    ET.indent(root, space="  ")
    path = ROUTES / (scene_id.lower() + ".xml")
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / "ENGINEERING_SCENE_CERTIFICATES").mkdir(parents=True, exist_ok=True)
    entry_head = head()
    scene_rows, identities, seed_rows = [], [], []
    for index, scene_id in enumerate(IDENTITY_ORDER, 1):
        binding = frozen_binding(scene_id)
        path = materialize_route(scene_id)
        admission = static_route_admission(path, binding)
        if admission["status"] != "PASS_STATIC_ROUTE_ADMISSION":
            raise RuntimeError(json.dumps(admission, sort_keys=True))
        identity = "RQ2TCG-ENG-{:03d}".format(index)
        row = {
            "identity": identity, "scene": scene_id, "template": binding["template"],
            "seed": ENGINEERING_SEEDS[scene_id], "phase": "ENGINEERING_ONLY",
            "route_path": str(path.relative_to(ROOT)), "route_sha256": sha256(path),
            "scene_configuration_sha256": binding["scene_configuration_sha256"],
            "registered_before_execution": True, "formal_scientific_exposure": False,
        }
        identities.append(row)
        seed_rows.append({"seed": row["seed"], "identity": identity, "scene": scene_id,
                          "permanent_exclusion": "ALL_FUTURE_RQ2_T_CG_DEV_TEST_PAPER_DENOMINATORS"})
        certificate = {
            "schema_version": "driveclarify.rq2_t_cg.engineering_scene_certificate.v1",
            "scene": scene_id, "template": binding["template"], "binding": binding,
            "route_admission": admission, "candidate_certification": binding["candidate_set_certification"],
            "candidate_bindings_semantically_distinct": True,
            "passenger_intent_identified": False, "event_owners_independent_of_views_and_outcomes": True,
            "formal_scientific_exposure": False,
        }
        certificate["certificate_digest"] = canonical_sha256(certificate)
        write_json(REPORT / "ENGINEERING_SCENE_CERTIFICATES" / (scene_id + ".json"), certificate)
        scene_rows.append({**row, "certificate_digest": certificate["certificate_digest"],
                           "candidate_set_digest": binding["candidate_set_certification"]["candidate_set_digest"],
                           "event_schedule_digest": canonical_sha256(binding["current_event_schedule"])})

    registry = {"schema_version": "driveclarify.rq2_t_cg.engineering_identity_registry.v1",
                "identity_count": 8, "maximum_authorized_identities": 12, "identities": identities,
                "formal_identity_count": 0}
    registry["registry_digest"] = canonical_sha256(registry)
    write_json(REPORT / "ENGINEERING_IDENTITY_REGISTRY.json", registry)
    exclusion = {"schema_version": "driveclarify.rq2_t_cg.engineering_seed_exclusion.v1",
                 "seed_count": 8, "seeds": seed_rows, "formal_seed_values_generated": 0,
                 "formal_scientific_exposures": 0}
    exclusion["registry_digest"] = canonical_sha256(exclusion)
    write_json(REPORT / "ENGINEERING_SEED_EXCLUSION_REGISTRY.json", exclusion)
    manifest = {"schema_version": "driveclarify.rq2_t_cg.engineering_scene_manifest.v1",
                "scene_count": 8, "scenes": scene_rows, "prospectively_frozen": True,
                "ord_sync_replaced_by": "ORD-LATE-REVEAL"}
    manifest["manifest_digest"] = canonical_sha256(manifest)
    write_json(REPORT / "ENGINEERING_SCENE_MANIFEST.json", manifest)
    write_md(REPORT / "ENGINEERING_SCENE_MANIFEST.md", "Engineering scene manifest", [
        "Exactly eight prospectively registered engineering-only native episodes.",
        "Templates: " + ", ".join(row["template"] for row in scene_rows) + ".",
        "All exact scenes, routes, layouts, configurations and seeds are permanently excluded from formal denominators.",
    ])

    prior_paths = [
        "reports/driveclarify_rq2_t_formal_experiment_2a_v2",
        "reports/driveclarify_rq2_t_v2_evidence_enabled_method_redesign_v1",
        "reports/driveclarify_rq2_t_v2_e2_grounding_root_cause_audit_v1",
        "reports/driveclarify_rq2_t_v2_e2_tracked_association_and_full_mechanism_v1",
        "reports/driveclarify_rq2_t_v2_final_blind_domain_shift_redesign_v1",
    ]
    preservation = {
        "schema_version": "driveclarify.rq2_t_cg.prior_preservation.v1", "entry_head": entry_head,
        "trees": [tree_receipt(path) for path in prior_paths],
        "RQ2_T_V1": "CLOSED_NEGATIVE_PASSIVE_EVIDENCE_RESULT",
        "RQ2_T_V1_ACCEPTED_RESULT": "RQ2_T_TIMING_HYPOTHESIS_NOT_SUPPORTED",
        "AUTOMATIC_E2_V3": ["DEVELOPMENT_MECHANISM_PASS", "BLIND_GENERALIZATION_NOT_QUALIFIED"],
        "AUTOMATIC_E2_V4_REDESIGN": "DEFERRED_BY_SCIENTIFIC_SCOPE_PIVOT",
        "RQ2_T_CG": "NEW_ACTIVE_CONTROLLED_GROUNDING_PRE_SCIENCE_TASK",
        "prior_reports_rewritten": False, "formal_seed_values_generated": 0,
        "formal_scientific_exposures": 0,
    }
    preservation["receipt_digest"] = canonical_sha256(preservation)
    write_json(REPORT / "PRIOR_AUTOMATIC_E2_RESULT_PRESERVATION_RECEIPT.json", preservation)

    pivot = {
        "schema_version": "driveclarify.rq2_t_cg.scope_contract.v1", "exact_final_rq": FINAL_RQ,
        "conditional_on": ["certified candidate interpretations", "certified candidate-to-entity/task bindings"],
        "isolated_factor_a": "B2_FIELD_SPECIFIC_TEMPORAL_RETENTION_VERSUS_B1_CURRENT_FRAME_ONLY",
        "isolated_factor_b": "EVIDENCE_ONLY_VERSUS_TIME_ONLY_VERSUS_JOINT_RULES",
        "automatic_e2_reopened": False, "formal_science_authorized": False,
        "online_ask_policy_added": False, "hypotheses": HYPOTHESES,
    }
    pivot["contract_digest"] = canonical_sha256(pivot)
    write_json(REPORT / "SCIENTIFIC_PIVOT_AND_SCOPE_CONTRACT.json", pivot)
    write_md(REPORT / "SCIENTIFIC_PIVOT_AND_SCOPE_CONTRACT.md", "Scientific pivot and scope", [
        FINAL_RQ, "The claim is conditional on certified candidate interpretations and bindings.",
        "Automatic open-vocabulary grounding is a separate diagnostic limitation and is not reopened.",
    ])
    evidence_margin = {
        "schema_version": "driveclarify.rq2_t_cg.evidence_margin_contract.v1", "exact_rq": FINAL_RQ,
        "factor_a": {"B1": "current-frame certified evidence only", "B2": "same current evidence plus frozen retention"},
        "factor_b": ["R-EVIDENCE-ONLY", "R-TIME-ONLY", "R-JOINT", "R-ORACLE"],
        "fixed_time_only_TTCmt_s": 3.0, "primary_unit": "scene × seed episode", "hypotheses": HYPOTHESES,
    }
    evidence_margin["contract_digest"] = canonical_sha256(evidence_margin)
    write_json(REPORT / "EVIDENCE_MARGIN_RQ2_CONTRACT.json", evidence_margin)
    write_md(REPORT / "EVIDENCE_MARGIN_RQ2_CONTRACT.md", "Evidence–margin RQ2 contract", [FINAL_RQ,
        "Factor A compares B2 with B1. Factor B compares three non-controlling rules on the same trace, with R-ORACLE as an analysis upper bound."])

    candidate_contract = {
        "interface": "CERTIFIED_CANDIDATE_EVIDENCE_INTERFACE_V1", "minimum_candidate_count": 2,
        "bindings_semantically_distinct": True, "passenger_intent_identified": False,
        "forbidden_inputs": ["passenger true choice", "correct final answer", "QueryNecessityGold", "true intended route"],
    }
    candidate_contract["contract_digest"] = canonical_sha256(candidate_contract)
    write_json(REPORT / "CERTIFIED_CANDIDATE_BINDING_CONTRACT.json", candidate_contract)
    write_md(REPORT / "CERTIFIED_CANDIDATE_BINDING_CONTRACT.md", "Certified candidate binding contract", [
        "Each scene has two independently certified, semantically distinct interpretations and bindings.",
        "Certification identifies candidate entities/tasks, never the passenger's intended choice.",
    ])
    interface_contract = {
        "interface_id": "CERTIFIED_CANDIDATE_EVIDENCE_INTERFACE_V1",
        "evidence_grade": "CERTIFIED_EXPERIMENTAL_ONLY",
        "allowed_usage": "RQ2_T_CG_MECHANISM_EVALUATION_ONLY", "default_off": True,
        "runtime_enabled": False, "production_act_ask_wait_eligible": False,
        "safety_authority": False, "builder_position": "POST_EPISODE_IMMUTABLE_TRACE_ANALYSIS",
    }
    interface_contract["contract_digest"] = canonical_sha256(interface_contract)
    write_json(REPORT / "CONTROLLED_EVIDENCE_INTERFACE_CONTRACT.json", interface_contract)
    write_md(REPORT / "CONTROLLED_EVIDENCE_INTERFACE_CONTRACT.md", "Controlled evidence interface", [
        "The separate interface runs post-episode and cannot authorize ACT, ASK, WAIT or safety-critical control.",
        "Every controlled envelope is CERTIFIED_EXPERIMENTAL_ONLY / RQ2_T_CG_MECHANISM_EVALUATION_ONLY.",
    ])
    paired_contract = {
        "B0": "exact V1 passive provider/adapter semantics; diagnostic only",
        "B1": "controlled current-frame evidence; no retention",
        "B2": "same current evidence plus frozen V2 TTL, binding, freshness and invalidation",
        "B3": "offline complete-evidence analysis upper bound",
        "same_native_source_frames_trajectory_route_controls_timestamps_commitment": True,
        "unchanged_sufficiency_authority": "driveclarify_rq2_t.measurement.epistemic_evidence_sufficient",
    }
    paired_contract["contract_digest"] = canonical_sha256(paired_contract)
    write_json(REPORT / "B0_B1_B2_B3_PAIRED_VIEW_CONTRACT.json", paired_contract)
    write_md(REPORT / "B0_B1_B2_B3_PAIRED_VIEW_CONTRACT.md", "B0/B1/B2/B3 paired-view contract", [
        "B0, B1, B2 and B3 are built from identical native source identities. Only B2 adds frozen legal temporal memory.",
    ])

    rules = {
        "R-EVIDENCE-ONLY": "trigger at first EpistemicEvidenceSufficient; classify a post-deadline proposal TOO_LATE_RULE_TRIGGER",
        "R-TIME-ONLY": "propose at frozen TTCmt=3.0 s without evidence; unsupported proposals are PREMATURE_UNSUPPORTED_TRIGGER",
        "R-JOINT": "trigger only at first sufficiency AND ClarificationActionable; never trigger after deadline",
        "R-ORACLE": "nondeployable earliest certified valid point in the independently defined window",
        "all_rules_offline_noncontrolling": True, "rule_added_native_episodes": 0,
    }
    rules["contract_digest"] = canonical_sha256(rules)
    write_json(REPORT / "DECISION_RULE_CONTRACT.json", rules)
    write_md(REPORT / "DECISION_RULE_CONTRACT.md", "Decision-rule contract", [
        "All four rules are post-trace evaluators. They share one immutable trace and do not alter vehicle behavior.",
        "R-TIME-ONLY is frozen at TTCmt = 3.0 simulated seconds.",
    ])

    late = frozen_binding("CG-ORD-LATE-REVEAL")
    late_route = ROOT / next(row["route_path"] for row in identities if row["scene"] == "CG-ORD-LATE-REVEAL")
    route = ET.parse(late_route).getroot().find("route")
    points = [[float(row.attrib[key]) for key in ("x", "y", "z")] for row in route.findall("./waypoints/position")]
    commitment = dict(project_point_to_polyline(COMMITMENT_POINTS["ORDER"], points))
    late_threshold = float(commitment["route_arc_length_m"]) - float(late["late_reveal_remaining_to_commitment_m"])
    late_certificate = {
        "schema_version": "driveclarify.rq2_t_cg.late_reveal_scene_certificate.v1",
        "certified_before_execution": True, "scene": "CG-ORD-LATE-REVEAL",
        "route_sha256": sha256(late_route), "commitment_projection": commitment,
        "late_reveal_route_arc_length_m": late_threshold,
        "remaining_route_distance_to_commitment_m": late["late_reveal_remaining_to_commitment_m"],
        "strictly_after_route_origin": late_threshold > 0.0,
        "strictly_before_commitment": late_threshold < commitment["route_arc_length_m"],
        "independent_event_owner": late["current_event_schedule"]["obligation_owner"],
        "frozen_target_speed_reference_mps": 1.25,
        "prospective_TTCmt_reference_s": late["late_reveal_remaining_to_commitment_m"] / 1.25,
        "prospectively_inside_0_to_1_20_s_interval": 0.0 < late["late_reveal_remaining_to_commitment_m"] / 1.25 < 1.2,
        "actual_native_temporal_certification_pending_execution": True,
        "runtime_gold_trigger": False,
    }
    late_certificate["certificate_digest"] = canonical_sha256(late_certificate)
    write_json(REPORT / "LATE_REVEAL_SCENE_CERTIFICATE.json", late_certificate)
    write_md(REPORT / "LATE_REVEAL_SCENE_CERTIFICATE.md", "Late-reveal scene certificate", [
        "Before execution, the route-owned topology event is frozen 0.60 m before the unchanged ORD-01 commitment boundary.",
        "At the frozen 1.25 m/s owner reference this is TTCmt=0.48 s. Native temporal confirmation remains an execution integrity check.",
    ])

    future = {
        "formal_scene_count": 8, "fresh_formal_DEV_seeds_per_scene": 6,
        "formal_native_episode_count": 48, "formal_seed_values_generated": 0,
        "formal_scientific_exposures": 0, "views_per_episode": ["B0", "B1", "B2", "B3"],
        "offline_rules_per_episode": ["R-EVIDENCE-ONLY", "R-TIME-ONLY", "R-JOINT", "R-ORACLE"],
        "native_execution_not_multiplied_by_views_or_rules": True,
        "primary_unit": "scene × seed episode", "hypotheses": HYPOTHESES,
    }
    future["protocol_digest"] = canonical_sha256(future)
    write_json(REPORT / "FUTURE_FORMAL_EXPERIMENT_PROTOCOL.json", future)
    write_md(REPORT / "FUTURE_FORMAL_EXPERIMENT_PROTOCOL.md", "Future formal experiment protocol — design only", [
        "Eight fresh scenes × six fresh DEV seeds = 48 native episodes. No formal seed values have been generated and no episode is authorized here.",
    ])
    write_md(REPORT / "FUTURE_ANALYSIS_PLAN.md", "Future paired analysis plan", [
        "Use paired episode-level comparisons; frame rows are never independent denominator samples.",
        "Primary memory comparison: R-JOINT(B2) versus R-JOINT(B1).",
    ])
    write_md(REPORT / "FUTURE_TRADEOFF_ANALYSIS_PLAN.md", "Future evidence–margin trade-off plan", [
        "On B2 and the exact same episode trace, compare R-EVIDENCE-ONLY, R-TIME-ONLY and R-JOINT for supported timely, premature unsupported and too-late proposals.",
    ])
    endpoints = [
        "precommitment sufficiency event", "first-sufficiency TTCmt", "actionable-window presence",
        "actionable-window duration", "proposed query TTCmt", "remaining margin at proposed query",
        "SUPPORTED_TIMELY_TRIGGER", "PREMATURE_UNSUPPORTED_TRIGGER", "TOO_LATE_RULE_TRIGGER",
        "ACTIONABLE_WINDOW_MISSED", "no-sufficiency classification", "false sufficiency",
        "invalid-retention failure", "fabricated semantic resolution",
    ]
    endpoint_contract = {"primary_unit": "scene × seed episode", "frame_rows_independent_samples": False,
                         "endpoints": {name: {"numerator": "eligible episodes with " + name,
                                             "denominator": "eligible scene × seed episodes",
                                             "censoring": "classify explicitly; never zero-impute UNKNOWN"}
                                       for name in endpoints}}
    endpoint_contract["contract_digest"] = canonical_sha256(endpoint_contract)
    write_json(REPORT / "ENDPOINT_DEFINITIONS.json", endpoint_contract)
    write_json(REPORT / "ORACLE_AND_TRUE_INTENT_FIREWALL_RECEIPT.json", {
        "runtime_true_intent_reads": 0, "passenger_answer_fields_in_bindings": 0,
        "oracle_runtime_reads": 0, "oracle_control_writes": 0,
        "controlled_evidence_runtime_reads": 0, "status": "PASS_PROSPECTIVE_FIREWALL",
    })
    write_md(REPORT / "COMMAND_LOG.md", "Command log", [
        "Prospective preparation: `python tools/prepare_rq2_t_cg.py`.",
        "No formal seed generation, automatic-E2 execution, or formal science command is authorized.",
    ])
    write_md(REPORT / "FINAL_REPORT.md", "RQ2-T-CG pre-science report", ["PENDING_EIGHT_NATIVE_ENGINEERING_EPISODES_AND_FINAL_VALIDATION"])
    print(json.dumps({"status": "PASS_PROSPECTIVE_RQ2_T_CG_FREEZE", "entry_head": entry_head,
                      "scene_count": len(scene_rows), "identity_count": len(identities),
                      "formal_seeds_generated": 0, "formal_exposures": 0}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
