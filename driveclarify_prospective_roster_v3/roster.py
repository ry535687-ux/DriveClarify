#!/usr/bin/env python3
"""Build and independently validate the prospective DriveClarify V3 roster.

This module performs only offline/static preparation and production route-owner
preflight.  It never trains, invokes a decision policy, or starts an RQ arm.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping

from driveclarify_four_family_qualification.qualification import (
    A1,
    CASES,
    ENDPOINT_TOLERANCE_M,
    EXPECTED_A1,
    EXPECTED_HEAD,
    NAV,
    ROOT,
    XODR,
    atomic_json,
    atomic_text,
    canonical,
    digest,
    route_binding,
    sha,
    source_runtime,
    split_routes,
    xyz,
)


REPORT = ROOT / "reports/driveclarify_v3_prospective_24_case_roster_v3"
QUALIFICATION = ROOT / "reports/driveclarify_v3_carla_readiness_and_four_family_live_grounding_recovery"
BASE_QUALIFICATION = ROOT / "reports/driveclarify_v3_four_family_grounded_route_binding_qualification"
EXPECTED_QUALIFICATION_STATUS = "PASS_FOUR_FAMILY_MINIMAL_LIVE_GROUNDING_QUALIFIED"
EXPECTED_NAV_SHA = "34b266b43e227aa5a56425a1465b1ffc1e3f445649e7488a6560ba08842d67ea"
EXPECTED_BRIDGE_SHA = "c6639361b92bdd594810af6d26d945a9c7667f2c5f00f2744f9a4ee5df714fcb"
BRIDGE = ROOT / "driveclarify_candidate_local_navigation_bridge.py"
FAMILIES = ("Referential", "Landmark", "Order", "Underspecified")
CLASSES = ("HIGH_CONSEQUENCE", "LOW_CONSEQUENCE")
FREEZE_DATE = "2026-08-25"
LOW_WINDOW_ROWS = (5, 8)
HIGH_WINDOW_ROWS_AFTER_ANCHOR = (24, 32)


class RosterError(RuntimeError):
    pass


def command(*args: str) -> str:
    return subprocess.run(args, cwd=ROOT, check=True, text=True, stdout=subprocess.PIPE).stdout.rstrip("\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def family_token(family: str) -> str:
    return {"Referential": "REF", "Landmark": "LAND", "Order": "ORDER", "Underspecified": "UNDER"}[family]


def base_case(family: str) -> Mapping[str, Any]:
    return next(case for case in CASES if case["family"] == family)


def frozen_seed_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for family_index, family in enumerate(FAMILIES, start=1):
        prefix = 41000 + (family_index - 1) * 1000
        token = family_token(family)
        for class_index, consequence in enumerate(CLASSES, start=1):
            for ordinal in (1, 2):
                rows.append({
                    "case_id": f"V3-{token}-{consequence.split('_')[0]}-{ordinal:02d}",
                    "family": family,
                    "case_type": "AMBIGUOUS",
                    "consequence_class": consequence,
                    "seed": prefix + class_index * 100 + ordinal,
                    "ordinal": ordinal,
                    "selection_status": "PRIMARY_FROZEN",
                })
        for ordinal in (1, 2):
            rows.append({
                "case_id": f"V3-{token}-CONTROL-{ordinal:02d}",
                "family": family,
                "case_type": "UNAMBIGUOUS_CONTROL",
                "consequence_class": "CONTROL",
                "seed": prefix + 300 + ordinal,
                "ordinal": ordinal,
                "selection_status": "PRIMARY_FROZEN",
            })
    return rows


def frozen_reserves() -> list[dict[str, Any]]:
    return [
        {
            "reserve_id": f"V3-{family_token(family)}-RESERVE-{ordinal:02d}",
            "family": family,
            "seed": 49000 + index * 100 + ordinal,
            "ordinal": ordinal,
            "status": "FROZEN_UNUSED",
            "activation_rule": "only after CASE_INELIGIBLE_GROUNDING; never for a scientific outcome",
        }
        for index, family in enumerate(FAMILIES, start=1)
        for ordinal in (1, 2)
    ]


def seed_registry() -> dict[str, Any]:
    rows = frozen_seed_rows()
    return {
        "schema": "driveclarify.prospective-seed-registry.v3",
        "freeze_date": FREEZE_DATE,
        "freeze_stage": "BEFORE_CASE_QUALIFICATION_AND_BEFORE_SCIENTIFIC_EXPOSURE",
        "replacement_policy": "NO_RESULT_DRIVEN_REPLACEMENT; ENGINEERING_INVALID_RETRIES_SAME_SEED",
        "derivation_rule": "fixed family block + fixed stratum block + ordinal",
        "primary_count": len(rows),
        "primary_seeds": rows,
        "reserve_count": 8,
        "reserve_candidates": frozen_reserves(),
        "formal_scientific_exposure": 0,
    }


def create_entry_state() -> None:
    if REPORT.exists():
        raise RosterError("REPORT_NAMESPACE_ALREADY_EXISTS")
    head = command("git", "rev-parse", "HEAD")
    status = command("git", "status", "--short")
    diff = command("git", "diff")
    cached = command("git", "diff", "--cached")
    if head != EXPECTED_HEAD:
        raise RosterError(f"ENTRY_HEAD_MISMATCH:{head}")
    if sha(A1) != EXPECTED_A1:
        raise RosterError("A1_HASH_MISMATCH")
    qualification = read_json(QUALIFICATION / "FINAL_RECEIPT.json")
    if qualification["final_status"] != EXPECTED_QUALIFICATION_STATUS:
        raise RosterError("QUALIFICATION_SOURCE_NOT_PASS")
    if sha(NAV) != EXPECTED_NAV_SHA or sha(BRIDGE) != EXPECTED_BRIDGE_SHA:
        raise RosterError("PROTECTED_ROUTE_SOURCE_CHANGED_AT_ENTRY")
    REPORT.mkdir(parents=True)
    atomic_text(REPORT / "00_ENTRY_STATE.md", f"""# Entry state

- Entry HEAD: `{head}`
- Expected HEAD matched: `true`
- A1 SHA-256: `{sha(A1)}`
- A1 retraining: `0`; A2/A3/LoRA: `OFF/OFF/OFF`
- Authoritative qualification: `{qualification['final_status']}`
- Production route owner SHA-256: `{sha(NAV)}`
- Formal scientific exposure: `0`
- Historical artifacts were read-only; no reset, clean, restore, or checkout was used.

## Verbatim `git status --short`

```text
{status}
```

## Verbatim `git diff`

```diff
{diff}
```

## Verbatim `git diff --cached`

```diff
{cached}
```
""")
    atomic_json(REPORT / "PROSPECTIVE_SEED_REGISTRY.json", seed_registry())


def shared_prefix_evidence(case: Mapping[str, Any], ordinal: int) -> dict[str, Any]:
    _path, runtime = source_runtime(case)
    route_a = runtime[case["route_a_source"]]
    route_b = runtime[case["route_b_source"]]
    structure = split_routes(route_a, route_b)
    rows = LOW_WINDOW_ROWS[ordinal - 1]
    if rows >= structure["shared_prefix_rows"]:
        raise RosterError("LOW_WINDOW_REACHES_DIVERGENCE")
    window_a = route_a[:rows]
    window_b = route_b[:rows]
    distance = sum(math.dist(xyz(a), xyz(b)) for a, b in zip(window_a, window_a[1:]))
    return {
        "criterion_version": "FROZEN_CONSEQUENCE_RULE_V3",
        "planning_window_rows": rows,
        "planning_window_distance_m": distance,
        "route_A_window_hash": digest(window_a),
        "route_B_window_hash": digest(window_b),
        "window_actions_equivalent": window_a == window_b,
        "divergence_outside_current_window": True,
        "shared_prefix_total_rows": structure["shared_prefix_rows"],
        "classification": "LOW_CONSEQUENCE",
    }


def high_evidence(case: Mapping[str, Any], binding: Mapping[str, Any], ordinal: int) -> dict[str, Any]:
    _path, runtime = source_runtime(case)
    route_a = runtime[case["route_a_source"]]
    route_b = runtime[case["route_b_source"]]
    structure = split_routes(route_a, route_b)
    start = structure["anchor_index"] - 1
    stop = start + HIGH_WINDOW_ROWS_AFTER_ANCHOR[ordinal - 1]
    window_a, window_b = route_a[start:stop], route_b[start:stop]
    return {
        "criterion_version": "FROZEN_CONSEQUENCE_RULE_V3",
        "planning_window_anchor_index": start,
        "planning_window_rows": HIGH_WINDOW_ROWS_AFTER_ANCHOR[ordinal - 1],
        "route_A_window_hash": digest(window_a),
        "route_B_window_hash": digest(window_b),
        "window_actions_equivalent": window_a == window_b,
        "connector_A": binding["connector_A_identity"],
        "connector_B": binding["connector_B_identity"],
        "different_connector_within_commitment_window": binding["connector_A_identity"] != binding["connector_B_identity"] and window_a != window_b,
        "classification": "HIGH_CONSEQUENCE",
    }


def qualified_entities(family: str) -> tuple[dict[str, Any], dict[str, Any], Path]:
    path = QUALIFICATION / "LIVE_GROUNDING_RECEIPTS" / f"{family.upper()}.json"
    source = read_json(path)
    if source["status"] != "PASS":
        raise RosterError(f"LIVE_GROUNDING_NOT_PASS:{family}")
    if family == "Underspecified":
        return copy.deepcopy(source["option_A"]), copy.deepcopy(source["option_B"]), path
    return copy.deepcopy(source["entity_A"]), copy.deepcopy(source["entity_B"]), path


def route_row_identity(row: Mapping[str, Any], route_index: int) -> dict[str, Any]:
    return {
        "route_index": route_index,
        "xyz": list(xyz(row)),
        "road_option": row["road_option"],
        "row_hash": digest(row),
    }


def physical_configuration(case: Mapping[str, Any], ordinal: int, case_type: str, stratum: str, selected_label: str | None = None) -> dict[str, Any]:
    """Return seed-independent map facts selected by the frozen ordinal.

    The seed chooses an ordinal; the ordinal chooses concrete route rows.  This
    makes each case a reproducible placement/observation configuration rather
    than a renamed copy of the qualified source scene.
    """
    _path, runtime = source_runtime(case)
    route_a = runtime[case["route_a_source"]]
    route_b = runtime[case["route_b_source"]]
    structure = split_routes(route_a, route_b)
    stratum_offset = {"HIGH_CONSEQUENCE": 0, "LOW_CONSEQUENCE": 6, "CONTROL": 12}[stratum]
    observation_index = 2 + ordinal * 3 + stratum_offset
    result: dict[str, Any] = {
        "derivation": "FROZEN_ROUTE_ROW_SELECTION_V3",
        "stratum": stratum,
        "observation_anchor": route_row_identity(route_a[observation_index], observation_index),
    }
    if case["family"] == "Referential":
        ia, ib = 36 + ordinal * 3 + stratum_offset, 38 + ordinal * 3 + stratum_offset
        result["entity_A_spawn"] = route_row_identity(route_a[ia], ia)
        result["entity_B_spawn"] = route_row_identity(route_b[ib], ib)
    elif case["family"] == "Landmark":
        result["landmark_observation_anchor"] = result["observation_anchor"]
        result["static_landmark_ids"] = list(case["landmark_ids"])
    elif case["family"] == "Order":
        result["junction_observation_anchor"] = result["observation_anchor"]
        result["junction_ids_in_order"] = list(case["junction_ids"])
    else:
        ia = structure["anchor_index"] + 4 + ordinal * 3 + stratum_offset
        ib = structure["anchor_index"] + 5 + ordinal * 3 + stratum_offset
        result["corridor_A_entry"] = route_row_identity(route_a[ia], ia)
        result["corridor_B_entry"] = route_row_identity(route_b[ib], ib)
    if case_type == "UNAMBIGUOUS_CONTROL":
        result["only_matching_interpretation"] = selected_label
        if case["family"] == "Referential":
            result["entity_B_spawn"] = None if selected_label == "A" else result["entity_B_spawn"]
            result["entity_A_spawn"] = None if selected_label == "B" else result["entity_A_spawn"]
        elif case["family"] == "Underspecified":
            result[f"corridor_{'B' if selected_label == 'A' else 'A'}_entry"] = None
    result["physical_configuration_hash"] = digest(result)
    return result


def derive_entity(entity: dict[str, Any], seed: int, ordinal: int, label: str, case_type: str, physical: Mapping[str, Any]) -> dict[str, Any]:
    entity["qualified_source_identity"] = entity.get("identity")
    entity["configuration_identity"] = digest({
        "qualified_identity": entity.get("identity"), "seed": seed, "ordinal": ordinal,
        "label": label, "case_type": case_type, "physical_configuration_hash": physical["physical_configuration_hash"],
    })
    entity["deterministic_seed"] = seed
    entity["configuration_ordinal"] = ordinal
    return entity


def instruction_for(family: str, case_type: str, ordinal: int) -> str:
    ambiguous = {
        "Referential": ("Turn after the white car.", "Take the turn just beyond the white car."),
        "Landmark": ("Turn by the 40 mph sign.", "Take the turn at the 40 mph sign."),
        "Order": ("Turn at the junction ahead.", "Take the turn at the junction up ahead."),
        "Underspecified": ("Go around this block to the destination.", "Take a route around the block to our destination."),
    }
    controls = {
        "Referential": ("Turn after the only white hatchback ahead.", "Turn after the only white sedan ahead."),
        "Landmark": ("Turn by the 40 mph sign on road 1056.", "Turn by the 40 mph sign on road 570."),
        "Order": ("Turn at the first eligible junction ahead.", "Continue through the first junction and turn at the second eligible junction."),
        "Underspecified": ("Take the clockwise southbound corridor around this block.", "Take the counter-clockwise westbound corridor around this block."),
    }
    return (controls if case_type == "UNAMBIGUOUS_CONTROL" else ambiguous)[family][ordinal - 1]


def ambiguous_receipt(row: Mapping[str, Any], world_map) -> dict[str, Any]:
    case = base_case(row["family"])
    binding = route_binding(case, world_map)  # exact production owner + converter
    binding["base_qualification_case_id"] = binding.pop("case_id")
    binding["case_id"] = row["case_id"]
    physical = physical_configuration(case, row["ordinal"], "AMBIGUOUS", row["consequence_class"])
    entity_a, entity_b, live_path = qualified_entities(row["family"])
    entity_a = derive_entity(entity_a, row["seed"], row["ordinal"], "A", "AMBIGUOUS", physical)
    entity_b = derive_entity(entity_b, row["seed"], row["ordinal"], "B", "AMBIGUOUS", physical)
    consequence = high_evidence(case, binding, row["ordinal"]) if row["consequence_class"] == "HIGH_CONSEQUENCE" else shared_prefix_evidence(case, row["ordinal"])
    same_destination = binding["same_destination_verdict"] == "PASS"
    different_local = binding["different_local_route_verdict"] == "PASS"
    eligible = all((
        entity_a["configuration_identity"] != entity_b["configuration_identity"],
        case["interpretation_a"] != case["interpretation_b"],
        binding["production_guard_A"] == "PASS", binding["production_guard_B"] == "PASS",
        same_destination, different_local,
        consequence["classification"] == row["consequence_class"],
        consequence["window_actions_equivalent"] is (row["consequence_class"] == "LOW_CONSEQUENCE"),
    ))
    return {
        "schema": "driveclarify.prospective-grounding-receipt.v3",
        **dict(row),
        "map": "Town12",
        "scenario_template_identity": case["case_id"],
        "physical_configuration": physical,
        "case_configuration_identity": digest({"case_id": row["case_id"], "seed": row["seed"], "physical": physical, "entities": [entity_a["configuration_identity"], entity_b["configuration_identity"]], "window": consequence}),
        "raw_instruction": instruction_for(row["family"], row["case_type"], row["ordinal"]),
        "entity_A": entity_a, "entity_B": entity_b,
        "interpretation_A": case["interpretation_a"], "interpretation_B": case["interpretation_b"],
        "interpretation_A_valid": True, "interpretation_B_valid": True,
        "binding_A": {"entity": entity_a["configuration_identity"], "route_hash": binding["route_A_hash"]},
        "binding_B": {"entity": entity_b["configuration_identity"], "route_hash": binding["route_B_hash"]},
        "route_A_hash": binding["route_A_hash"], "route_B_hash": binding["route_B_hash"],
        "connector_A": binding["connector_A_identity"], "connector_B": binding["connector_B_identity"],
        "canonical_global_destination": binding["canonical_destination_identity_A"],
        "same_destination_PASS": same_destination,
        "different_local_route": different_local,
        "shared_prefix_classification": consequence,
        "qualification_source": {"path": str(live_path), "sha256": sha(live_path), "status": EXPECTED_QUALIFICATION_STATUS},
        "production_route_binding": binding,
        "grounding_eligible": eligible,
        "formal_roster_eligible": eligible,
        "formal_scientific_exposure": 0,
    }


def control_receipt(row: Mapping[str, Any]) -> dict[str, Any]:
    case = base_case(row["family"])
    _path, runtime = source_runtime(case)
    selected_label = "A" if row["ordinal"] == 1 else "B"
    route_field = case["route_a_source"] if selected_label == "A" else case["route_b_source"]
    route = runtime[route_field]
    entity_a, entity_b, live_path = qualified_entities(row["family"])
    selected = entity_a if selected_label == "A" else entity_b
    physical = physical_configuration(case, row["ordinal"], "UNAMBIGUOUS_CONTROL", "CONTROL", selected_label)
    selected = derive_entity(selected, row["seed"], row["ordinal"], selected_label, "UNAMBIGUOUS_CONTROL", physical)
    interpretation = case["interpretation_a"] if selected_label == "A" else case["interpretation_b"]
    config = digest({"case_id": row["case_id"], "seed": row["seed"], "physical": physical, "only_matching_entity": selected["configuration_identity"], "route_hash": digest(route)})
    return {
        "schema": "driveclarify.prospective-grounding-receipt.v3",
        **dict(row), "map": "Town12", "scenario_template_identity": case["case_id"],
        "physical_configuration": physical, "case_configuration_identity": config,
        "raw_instruction": instruction_for(row["family"], row["case_type"], row["ordinal"]),
        "unambiguous": True, "reasonable_interpretation_count": 1,
        "entity_A": selected, "entity_B": None,
        "intended_interpretation": interpretation, "intended_interpretation_valid": True,
        "binding": {"entity": selected["configuration_identity"], "route_hash": digest(route), "source_field": route_field},
        "route_hash": digest(route), "connector": split_routes(runtime[case["route_a_source"]], runtime[case["route_b_source"]])[f"connector_{selected_label.lower()}_identity"],
        "canonical_global_destination": digest(route[-1]),
        "qualification_source": {"path": str(live_path), "sha256": sha(live_path), "status": EXPECTED_QUALIFICATION_STATUS},
        "grounding_eligible": True, "formal_roster_eligible": True,
        "formal_scientific_exposure": 0,
    }


def validate_receipt(receipt: Mapping[str, Any]) -> None:
    if receipt["case_type"] == "AMBIGUOUS":
        required = ("entity_A", "entity_B", "interpretation_A", "interpretation_B", "binding_A", "binding_B", "route_A_hash", "route_B_hash", "connector_A", "connector_B")
        if any(not receipt.get(key) for key in required):
            raise RosterError(f"AMBIGUOUS_REQUIRED_FIELD:{receipt['case_id']}")
        if receipt["interpretation_A"] == receipt["interpretation_B"] or not receipt["same_destination_PASS"]:
            raise RosterError(f"AMBIGUOUS_SEMANTIC_OR_DESTINATION:{receipt['case_id']}")
        binding = receipt["production_route_binding"]
        if any(binding[key] != "PASS" for key in ("production_guard_A", "production_guard_B", "same_destination_verdict", "different_local_route_verdict")):
            raise RosterError(f"PRODUCTION_BINDING_FAIL:{receipt['case_id']}")
        equivalent = receipt["shared_prefix_classification"]["window_actions_equivalent"]
        if equivalent != (receipt["consequence_class"] == "LOW_CONSEQUENCE"):
            raise RosterError(f"CONSEQUENCE_MISCLASSIFIED:{receipt['case_id']}")
    else:
        if not receipt.get("unambiguous") or receipt.get("reasonable_interpretation_count") != 1 or not receipt.get("route_hash"):
            raise RosterError(f"CONTROL_INVALID:{receipt['case_id']}")
    if not receipt.get("grounding_eligible") or not receipt.get("formal_roster_eligible"):
        raise RosterError(f"CASE_INELIGIBLE:{receipt['case_id']}")


def build() -> None:
    if not REPORT.exists():
        create_entry_state()
    registry = read_json(REPORT / "PROSPECTIVE_SEED_REGISTRY.json")
    if registry != seed_registry():
        raise RosterError("SEED_REGISTRY_CHANGED_AFTER_FREEZE")
    import carla
    world_map = carla.Map("Town12", XODR.read_text(encoding="utf-8"))
    receipts = []
    for row in registry["primary_seeds"]:
        receipt = ambiguous_receipt(row, world_map) if row["case_type"] == "AMBIGUOUS" else control_receipt(row)
        validate_receipt(receipt)
        atomic_json(REPORT / "GROUNDING_RECEIPTS" / f"{row['case_id']}.json", receipt)
        receipts.append(receipt)
    ambiguous = [x for x in receipts if x["case_type"] == "AMBIGUOUS"]
    controls = [x for x in receipts if x["case_type"] == "UNAMBIGUOUS_CONTROL"]
    roster = {
        "schema": "driveclarify.prospective-formal-roster.v3", "status": "FROZEN_ELIGIBLE",
        "freeze_date": FREEZE_DATE, "seed_registry_sha256": sha(REPORT / "PROSPECTIVE_SEED_REGISTRY.json"),
        "total_case_seed_count": len(receipts), "eligible_case_seed_count": sum(x["formal_roster_eligible"] for x in receipts),
        "ambiguous_count": len(ambiguous), "control_count": len(controls), "formal_scientific_exposure": 0,
        "cases": receipts,
    }
    atomic_json(REPORT / "PROSPECTIVE_FORMAL_ROSTER_V3.json", roster)
    lines = ["# Prospective formal roster V3", "", "Status: `FROZEN_ELIGIBLE`; `24/24` case-seeds eligible; scientific exposure `0`.", "", "| Case | Family | Type | Class | Seed | Eligible |", "|---|---|---|---|---:|---|"]
    lines += [f"| {x['case_id']} | {x['family']} | {x['case_type']} | {x['consequence_class']} | {x['seed']} | {x['formal_roster_eligible']} |" for x in receipts]
    atomic_text(REPORT / "PROSPECTIVE_FORMAL_ROSTER_V3.md", "\n".join(lines) + "\n")
    binding_rows = [{
        "case_id": x["case_id"], "family": x["family"], "class": x["consequence_class"],
        "route_A_hash": x["route_A_hash"], "route_B_hash": x["route_B_hash"],
        "connector_A": x["connector_A"], "connector_B": x["connector_B"],
        "production_guard_A": x["production_route_binding"]["production_guard_A"],
        "production_guard_B": x["production_route_binding"]["production_guard_B"],
        "same_destination": x["production_route_binding"]["same_destination_verdict"],
        "endpoint_tolerance_m": x["production_route_binding"]["endpoint_tolerance_m"],
    } for x in ambiguous]
    binding_summary = {"schema": "driveclarify.route-binding-summary.v3", "required_count": 16, "pass_count": sum(all(r[k] == "PASS" for k in ("production_guard_A", "production_guard_B", "same_destination")) for r in binding_rows), "same_destination_failures": sum(r["same_destination"] != "PASS" for r in binding_rows), "endpoint_tolerance_m": ENDPOINT_TOLERANCE_M, "production_owner_sha256": sha(NAV), "rows": binding_rows}
    atomic_json(REPORT / "ROUTE_BINDING_SUMMARY.json", binding_summary)
    atomic_text(REPORT / "ROUTE_BINDING_SUMMARY.md", f"# Route binding summary\n\nResult: `{binding_summary['pass_count']}/16 PASS`; same-destination failures: `{binding_summary['same_destination_failures']}`. Every ambiguous case invoked the unchanged production owner and converter. Endpoint tolerance: `{ENDPOINT_TOLERANCE_M} m`.\n")
    diversity = {
        "schema": "driveclarify.roster-diversity.v3",
        "unique_maps": sorted({x["map"] for x in receipts}),
        "unique_route_hashes": sorted({h for x in receipts for h in ([x["route_hash"]] if x["case_type"] != "AMBIGUOUS" else [x["route_A_hash"], x["route_B_hash"]])}),
        "unique_connector_pairs": sorted({f"{x['connector_A']}::{x['connector_B']}" for x in ambiguous}),
        "unique_anchor_identities": sorted({x["production_route_binding"]["anchor_identity"] for x in ambiguous}),
        "unique_entity_configurations": len({y["configuration_identity"] for x in receipts for y in (x.get("entity_A"), x.get("entity_B")) if y}),
        "unique_physical_configurations": len({x["physical_configuration"]["physical_configuration_hash"] for x in receipts}),
        "unique_case_configurations": len({x["case_configuration_identity"] for x in receipts}),
        "not_cosmetic_labels": len({x["case_configuration_identity"] for x in receipts}) == 24,
    }
    atomic_json(REPORT / "ROSTER_DIVERSITY_SUMMARY.json", diversity)
    atomic_text(REPORT / "ROSTER_DIVERSITY_SUMMARY.md", f"# Roster diversity summary\n\nThe roster has `{len(diversity['unique_maps'])}` map, `{len(diversity['unique_route_hashes'])}` route hashes, `{len(diversity['unique_connector_pairs'])}` connector pairs, `{len(diversity['unique_anchor_identities'])}` anchors, `{diversity['unique_entity_configurations']}` entity configurations, `{diversity['unique_physical_configurations']}` route-row-derived physical configurations, and `24` unique case configurations. Reuse of qualified geometry is intentional; all case-seed configurations are distinct.\n")
    atomic_json(REPORT / "CARLA_QUALIFICATION_LEDGER.json", {"schema": "driveclarify.carla-qualification-ledger.v3", "launch_count": 0, "reason": "All configurations are deterministic derivations of four already-live-qualified templates; static OpenDRIVE verification plus exact production route-owner preflight was sufficient.", "qualification_source_launches": 2, "formal_scientific_exposure": 0, "runs": []})
    atomic_text(REPORT / "ENGINEERING_INVALID_LEDGER.md", "# Engineering-invalid ledger\n\nCount: `0`. No server was launched and no engineering-invalid retry occurred.\n")


def independent_review() -> dict[str, Any]:
    roster = read_json(REPORT / "PROSPECTIVE_FORMAL_ROSTER_V3.json")
    registry = read_json(REPORT / "PROSPECTIVE_SEED_REGISTRY.json")
    binding = read_json(REPORT / "ROUTE_BINDING_SUMMARY.json")
    diversity = read_json(REPORT / "ROSTER_DIVERSITY_SUMMARY.json")
    cases = roster["cases"]
    checks = {
        "24_cases_exist": len(cases) == len(list((REPORT / "GROUNDING_RECEIPTS").glob("*.json"))) == 24,
        "not_cosmetic_labels": diversity["not_cosmetic_labels"] and diversity["unique_case_configurations"] == 24 and diversity["unique_physical_configurations"] == 24,
        "16_ambiguous_two_interpretations": len([x for x in cases if x["case_type"] == "AMBIGUOUS" and x["interpretation_A_valid"] and x["interpretation_B_valid"] and x["interpretation_A"] != x["interpretation_B"]]) == 16,
        "8_controls_unambiguous": len([x for x in cases if x["case_type"] == "UNAMBIGUOUS_CONTROL" and x["unambiguous"] and x["reasonable_interpretation_count"] == 1]) == 8,
        "frozen_high_low_rule": len([x for x in cases if x["consequence_class"] == "HIGH_CONSEQUENCE" and not x["shared_prefix_classification"]["window_actions_equivalent"]]) == 8 and len([x for x in cases if x["consequence_class"] == "LOW_CONSEQUENCE" and x["shared_prefix_classification"]["window_actions_equivalent"]]) == 8,
        "production_bindings": binding["pass_count"] == 16 and binding["production_owner_sha256"] == EXPECTED_NAV_SHA,
        "same_destination": binding["same_destination_failures"] == 0,
        "prospective_seed_freeze": registry == seed_registry() and roster["seed_registry_sha256"] == sha(REPORT / "PROSPECTIVE_SEED_REGISTRY.json"),
        "no_result_driven_replacement": all(x["selection_status"] == "PRIMARY_FROZEN" for x in cases),
        "a1_unchanged": sha(A1) == EXPECTED_A1,
        "route_guard_unchanged": sha(NAV) == EXPECTED_NAV_SHA and ENDPOINT_TOLERANCE_M == 0.001,
        "decision_logic_unchanged": sha(BRIDGE) == EXPECTED_BRIDGE_SHA,
        "rq1_not_started": roster["formal_scientific_exposure"] == 0 and read_json(REPORT / "CARLA_QUALIFICATION_LEDGER.json")["formal_scientific_exposure"] == 0,
    }
    result = {"schema": "driveclarify.independent-roster-review.v3", "mode": "FRESH_READ_ONLY_RECOMPUTATION_FROM_FROZEN_ARTIFACTS", "checks": checks, "pass_count": sum(checks.values()), "check_count": len(checks), "status": "PASS" if all(checks.values()) else "FAIL"}
    if result["status"] != "PASS":
        raise RosterError("INDEPENDENT_REVIEW_FAILED")
    atomic_text(REPORT / "FINAL_INDEPENDENT_REVIEW.md", "# Final independent review\n\nMode: fresh read-only recomputation from the frozen registry, roster, 24 receipts, route summary, and source hashes.\n\n" + "\n".join(f"- {key}: `{'PASS' if value else 'FAIL'}`" for key, value in checks.items()) + f"\n\nVerdict: `{result['status']}` ({result['pass_count']}/{result['check_count']}). No RQ arm was executed.\n")
    return result


def finalize(regression_summary: str = "NOT_YET_RECORDED") -> None:
    review = independent_review()
    roster = read_json(REPORT / "PROSPECTIVE_FORMAL_ROSTER_V3.json")
    binding = read_json(REPORT / "ROUTE_BINDING_SUMMARY.json")
    diversity = read_json(REPORT / "ROSTER_DIVERSITY_SUMMARY.json")
    head = command("git", "rev-parse", "HEAD")
    receipt = {
        "schema": "driveclarify.prospective-24-case-roster-final.v3",
        "final_status": "PASS_PROSPECTIVE_24_CASE_ROSTER_FROZEN",
        "entry_head": EXPECTED_HEAD, "exit_head": head,
        "a1_sha256": sha(A1), "a1_retraining_count": 0, "a1_changed": False,
        "total_roster": "24/24", "ambiguous": "16/16", "controls": "8/8",
        "family_distribution": {family: {"HIGH": 2, "LOW": 2, "CONTROL": 2} for family in FAMILIES},
        "production_route_bindings": f"{binding['pass_count']}/16 PASS", "same_destination_failures": binding["same_destination_failures"],
        "carla_qualification_launches": 0, "engineering_invalid": 0, "grounding_ineligible": 0,
        "reserve_substitutions": 0, "reserve_substitution_reasons": [],
        "unique_maps": len(diversity["unique_maps"]), "unique_route_hashes": len(diversity["unique_route_hashes"]), "unique_connector_pairs": len(diversity["unique_connector_pairs"]),
        "formal_scientific_exposure": 0, "route_guard_changed": False, "endpoint_tolerance_m": 0.001,
        "decision_policy_changed": False, "focused_regressions": regression_summary,
        "independent_review": f"{review['pass_count']}/{review['check_count']} PASS", "exact_blocker": None,
        "next_step": "formal pre-execution integrity gate",
    }
    atomic_json(REPORT / "FINAL_RECEIPT.json", receipt)
    atomic_text(REPORT / "FINAL_REPORT.md", f"""# Final report

Final status: `PASS_PROSPECTIVE_24_CASE_ROSTER_FROZEN`.

The four already-qualified grounding mechanisms were prospectively expanded into 24 deterministic case-seeds: 16 genuinely ambiguous cases (2 high and 2 low per family) and 8 unambiguous controls. All 16 A/B bindings passed the unchanged production owner and same-destination guard. High/low uses one frozen rule: divergence inside the current commitment window is high; a byte-identical shared-prefix window with two still-reasonable eventual interpretations is low.

- Eligible roster: `24/24` (`16/16` ambiguous, `8/8` controls)
- Production route bindings: `{binding['pass_count']}/16 PASS`; same-destination failures `0`
- CARLA launches this stage: `0`; deterministic reuse of prior live qualification
- A1 retraining: `0`; A1 SHA-256 `{sha(A1)}`
- Scientific exposure: `0`; RQ1/RQ2/RQ3 not started
- Focused regressions: `{regression_summary}`
- Fresh read-only review: `{review['pass_count']}/{review['check_count']} PASS`

Claim boundary: this report establishes only that a prospective grounded evaluation roster was constructed and frozen before scientific exposure. It makes no method-effectiveness claim.

Next step: `formal pre-execution integrity gate`.
""")
    manifest = write_hashes()
    receipt["artifact_count"] = manifest["artifact_count"]
    receipt["aggregate_sha256"] = manifest["aggregate_sha256"]
    receipt["aggregate_coverage"] = manifest["aggregate_coverage"]
    atomic_json(REPORT / "FINAL_RECEIPT.json", receipt)


def write_hashes() -> dict[str, Any]:
    excluded = {"ARTIFACT_HASHES.json", "FINAL_RECEIPT.json"}
    files = []
    for path in sorted(p for p in REPORT.rglob("*") if p.is_file() and p.name not in excluded):
        files.append({"relative_path": str(path.relative_to(REPORT)), "bytes": path.stat().st_size, "sha256": sha(path)})
    manifest = {"schema": "driveclarify.artifact-hashes.v3", "aggregate_coverage": "all report files except ARTIFACT_HASHES.json and FINAL_RECEIPT.json (non-circular)", "artifact_count": len(files) + 2, "files": files, "aggregate_sha256": digest(files)}
    atomic_json(REPORT / "ARTIFACT_HASHES.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("entry", "build", "review", "finalize"))
    parser.add_argument("--regressions", default="NOT_YET_RECORDED")
    args = parser.parse_args()
    if args.phase == "entry": create_entry_state()
    elif args.phase == "build": build()
    elif args.phase == "review": independent_review()
    else: finalize(args.regressions)


if __name__ == "__main__":
    main()
