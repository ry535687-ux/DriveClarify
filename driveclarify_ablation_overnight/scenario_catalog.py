"""Prospective reuse of all seven physical V2 templates, independent of outcomes.

No historical results are read. Historical assets are copied into this round;
private intent is an input to the broker/offline evaluator, never this builder.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from driveclarify_rq3_paired_v2.configuration import make_runtime_config
from driveclarify_ablation_overnight.task_outcome import physical_relation

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "reports/driveclarify_rq3_paired_comparison_v2_task_binding_qualification_and_execution"
DEFAULT_OUTPUT = ROOT / "reports/driveclarify_ablation_overnight_20260909_v1/scenarios"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(str(path))
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def build(output=DEFAULT_OUTPUT):
    output = Path(output)
    original = SOURCE / "V2_TEMPLATE_CANDIDATE_MANIFEST.json"
    templates = json.loads(original.read_text())["templates"]
    rows = []
    for item in templates:
        entry = copy.deepcopy(item)
        condition = item["condition"]
        paths = {}
        for kind, source_path in (
            ("task_binding", Path(item["task_binding_path"])),
            ("candidate_route", Path(item["candidate_route_path"])),
            ("layout", SOURCE / "candidate_assets" / (condition + "_LAYOUT.json")),
        ):
            folder = "evaluation_only" if kind == "task_binding" else "public_assets"
            target = output / folder / source_path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise FileExistsError(str(target))
            shutil.copyfile(source_path, target)
            paths[kind] = {"path": str(target), "sha256": sha(target),
                           "source_path": str(source_path), "source_sha256": sha(source_path)}
        original_route = Path(item["official_route_context"]["official_route_path"])
        route = output / "public_assets" / (condition + "_CONTROLLED_ROUTE.xml")
        if route.exists():
            raise FileExistsError(str(route))
        shutil.copyfile(original_route, route)
        tree = ET.parse(route)
        route_node = tree.getroot().find("route")
        paths["official_route"] = {"path": str(route), "sha256": sha(route),
                                   "source_path": str(original_route), "source_sha256": sha(original_route)}
        binding = json.loads(Path(paths["task_binding"]["path"]).read_text())
        entry["template_id"] = "ABL-" + condition + "-PHYSICAL-REUSE-01"
        entry["source_template_id"] = item["template_id"]
        entry["task_binding_path"] = paths["task_binding"]["path"]
        entry["candidate_route_path"] = paths["candidate_route"]["path"]
        entry["official_route_context"]["official_route_path"] = str(route)
        entry["official_route_context"]["official_route_sha256"] = sha(route)
        entry["assets"] = paths
        entry["physical_relation_truth"] = physical_relation(binding)
        entry["short_trajectory_condition"] = "UNKNOWN_PENDING_DEVELOPMENT_MODEL_CANDIDATES"
        entry["native_map"] = route_node.attrib["town"]
        entry["native_route_id"] = route_node.attrib["id"]
        entry["selection_policy"] = "ALL_EXISTING_PHYSICAL_V2_TEMPLATES_IN_SOURCE_ORDER_NO_RESULTS_READ"
        entry["stage"] = "DEVELOPMENT_TEMPLATE_REUSE_NOT_UNSEEN_TEMPLATE_GENERALIZATION"
        entry["formal_seeds"] = []
        entry["formal_intents_allocated"] = False
        entry["status"] = "REUSED_PHYSICALLY_QUALIFIED_PENDING_CURRENT_RUNTIME_PROBE"
        config = make_runtime_config(item, "A1", entry["template_id"] + "-UNASSIGNED", 0)
        config["method_input"]["route_source"] = entry["candidate_route_path"]
        config.pop("scientific_seed_not_available_to_method", None)
        config["run_id"] = entry["template_id"] + "-UNASSIGNED"
        config["ablation_stage"] = "CONFIG_TEMPLATE_REQUIRES_RUN_ID_SEED_AND_VARIANT"
        config_path = output / "runtime_config_templates" / (condition + ".json")
        write(config_path, config)
        entry["runtime_config_template"] = str(config_path)
        entry["runtime_config_template_sha256"] = sha(config_path)
        rows.append(entry)
    result = {
        "schema": "driveclarify.ablation-overnight.physical-template-catalog.v1",
        "source_manifest": str(original), "source_manifest_sha256": sha(original),
        "template_count": len(rows), "templates": rows,
        "selection_reads_historical_outcomes": False,
        "usce_absent_in_source": True,
        "coverage_missing": ["USC task-equivalent template absent in source",
                             "near/different model trajectory coverage not yet measured on development data"],
        "planned_runtime_answers": "A or B only after durable ASK, for equivalent and critical alike",
        "seed_allocation": "PARENT_RUNNER_MUST_ASSIGN_DISJOINT_FRESH_DEV_AND_FORMAL_SEEDS_BEFORE_LAUNCH",
        "public_context_is_same_between_arms": True,
        "standard_bench2drive_queue_started": False,
    }
    write(output / "SCENARIO_CATALOG.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build(args.output)
    print(json.dumps({"templates": result["template_count"], "output": str(args.output),
                      "source_outcomes_read": False}))
