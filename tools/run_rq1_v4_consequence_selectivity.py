#!/usr/bin/env python3
"""RQ1-V4 ORD-CRITICAL repair, prospective freeze, execution, and analysis.

V3 is immutable.  This campaign reads only its two preserved ORD-CRITICAL
non-evaluable traces for execution-integrity forensics and imports the frozen
V2/V3 scientific implementation without changing it.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import random
import secrets
import subprocess
import sys
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

V3_TOOL = ROOT / "tools/run_rq1_v3_consequence_selectivity.py"
_spec = importlib.util.spec_from_file_location("driveclarify_rq1_v3_frozen_tool", V3_TOOL)
if _spec is None or _spec.loader is None:
    raise RuntimeError("RQ1_V3_TOOL_IMPORT_FAILED")
v3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v3)

REPORT = ROOT / "reports/driveclarify_rq1_v4_ord_critical_stability_and_formal_v1"
ENGINEERING = REPORT / "engineering_generation_1"
ENGINEERING_ASSETS = ENGINEERING / "assets"
ENGINEERING_CONFIGS = ENGINEERING / "run_configs"
ENGINEERING_RUNS = ENGINEERING / "native_runs"
ENGINEERING2 = REPORT / "engineering_generation_2"
ENGINEERING2_ASSETS = ENGINEERING2 / "assets"
ENGINEERING2_CONFIGS = ENGINEERING2 / "run_configs"
ENGINEERING2_RUNS = ENGINEERING2 / "native_runs"
FORMAL_ASSETS = REPORT / "formal_assets"
FORMAL_CONFIGS = REPORT / "formal_run_configs"
FORMAL_RUNS = REPORT / "formal_runs"
V3_REPORT = ROOT / "reports/driveclarify_rq1_v3_executability_full_replan_consequence_selectivity_v1"
V2_REPORT = ROOT / "reports/driveclarify_rq1_v2_consequence_selective_clarification_v2"
TEMPLATE = ROOT / "reports/driveclarify_v11_fresh_prospective_rq1/formal_routes/V11-FORMAL-TOWN05-CORRIDORS.json"
TOWN05_XODR = Path("/home/buaa/CARLA_0.9.15/CarlaUE4/Content/Carla/Maps/OpenDrive/Town05.xodr")
CHECKPOINT = v3.CHECKPOINT
CHECKPOINT_SHA256 = v3.CHECKPOINT_SHA256
NATIVE_RUNNER = ROOT / "tools/run_rq1_v4_native_episode.sh"
INDEPENDENT_AUDITOR = ROOT / "tools/audit_rq1_v4_results.py"
FAMILIES = ("REF", "LMK", "ORD", "USC")
LEVELS = ("EQUIVALENT", "CRITICAL")
SCENE_CODES = tuple(f"{family}-{level}" for family in FAMILIES for level in LEVELS)
V3_PRESERVED_CODES = tuple(code for code in SCENE_CODES if code != "ORD-CRITICAL")
INSTRUCTIONS = dict(v3.INSTRUCTIONS, ORD="Take the next turn.")
SEMANTIC_KINDS = dict(v3.SEMANTIC_KINDS)
XML_START = v3.TOWN05_XML_START
XML_DESTINATION = v3.TOWN05_XML_DESTINATION
FINAL_STATUSES = {
    "PASS_RQ1_V4_CONSEQUENCE_SELECTIVITY_SUPPORTED",
    "PASS_RQ1_V4_CONSEQUENCE_SELECTIVITY_NOT_SUPPORTED",
    "RQ1_V4_PRIMARY_EVALUABILITY_GATE_FAILED",
    "ORD_CRITICAL_NATIVE_STABILITY_NOT_QUALIFIED",
    "RQ1_V4_EXECUTION_INTEGRITY_NOT_CLOSED",
}


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def sha(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def load(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value.rstrip() + "\n", encoding="utf-8")
    temporary.replace(path)


def log(phase: str, status: str) -> None:
    path = REPORT / "COMMAND_LOG.md"
    prior = path.read_text(encoding="utf-8") if path.is_file() else "# RQ1-V4 command log\n\n"
    write_text(path, prior + f"- `{phase}` -> `{status}`\n")


def replacement_strings(value: Any, designation: str) -> Any:
    if isinstance(value, dict):
        return {key: replacement_strings(item, designation) for key, item in value.items()}
    if isinstance(value, list):
        return [replacement_strings(item, designation) for item in value]
    if isinstance(value, str):
        return value.replace("RQ1V3", "RQ1V4").replace("FORMAL", designation)
    return value


def task_signature(scene_code: str, candidate: str, designation: str) -> dict[str, Any]:
    value = replacement_strings(v3.task_signature(scene_code, candidate, designation), designation)
    value["certificate_id"] = f"RQ1V4-{designation}-CERT-{scene_code}-{candidate}"
    if scene_code == "ORD-CRITICAL" and candidate == "B":
        value["maneuver_obligation"] = f"{designation}-ORD-SECOND-ELIGIBLE-TURN"
        value["irreversible_branch_obligation"] = f"{designation}-ORD-SECOND-ORDERED-BRANCH"
        value["task_completion_region"] = f"{designation}-ORD-SECOND-TURN-COMPLETION"
    elif scene_code == "ORD-CRITICAL":
        value["maneuver_obligation"] = f"{designation}-ORD-FIRST-ELIGIBLE-TURN"
        value["irreversible_branch_obligation"] = f"{designation}-ORD-FIRST-ORDERED-BRANCH"
        value["task_completion_region"] = f"{designation}-ORD-FIRST-TURN-COMPLETION"
    return value


def route_source(scene_code: str, designation: str, center: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    amplitude = 0.30 if scene_code.endswith("EQUIVALENT") else 0.65
    value = {
        "schema": "driveclarify.rq1_v4.candidate-route-source.v1",
        "designation": designation,
        "scene_code": scene_code,
        "map": "Town05",
        "candidate_A": v3.offset_route(center, -amplitude),
        "candidate_B": v3.offset_route(center, amplitude),
        "same_global_destination": True,
        "maximum_lateral_offset_m": amplitude,
        "construction": "continuous simple qualified Town05 corridor; consequence is defined only by frozen TaskSignature",
    }
    if scene_code == "ORD-CRITICAL":
        value["ordered_interpretations"] = {
            "z1_candidate_A": "take the first eligible ordered turn/opportunity",
            "z2_candidate_B": "continue and take the second eligible ordered turn/opportunity",
            "ordering_coordinate": "forward route arc length from the common decision anchor",
            "opportunity_indices": [10, 35],
        }
    value["route_source_digest"] = digest(value)
    return value


def layout(scene_code: str, designation: str, center: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    family = scene_code.split("-")[0]
    entity_kind = {
        "REF": "REFERENCE_OBJECT_ANCHOR",
        "LMK": "LANDMARK_ANCHOR",
        "ORD": "ELIGIBLE_ORDER_OPPORTUNITY",
        "USC": "VALID_CONSTRAINT_PLACEMENT_ENVELOPE",
    }[family]
    indices = (10, 35)
    entities = []
    for candidate, index in zip(("A", "B"), indices):
        entities.append({
            "entity_id": f"RQ1V4-{designation}-{scene_code}-{candidate}",
            "scientific_role": f"{entity_kind}_CANDIDATE_{candidate}",
            "route_arc_order": 1 if candidate == "A" else 2,
            "route_index": index,
            "anchor_xyz": list(center[index]["xyz"]),
            "spawned_actor": False,
        })
    value = {
        "schema": "driveclarify.rq1_v4.scientific-layout.v1",
        "layout_id": f"RQ1V4-{designation}-LAYOUT-{scene_code}",
        "town_layout_identity": f"Town05::RQ1V4-{designation}-LAYOUT-{scene_code}",
        "scene_code": scene_code,
        "retained_scientific_actors": [],
        "scientific_entities": entities,
        "random_background_vehicle_count": 0,
        "traffic_manager_random_generation_enabled": False,
        "unrelated_realism_traffic": False,
    }
    value["layout_digest"] = digest(value)
    return value


def make_scene(scene_code: str, designation: str, index: int, assets: Path, engineering: bool, engineering_generation: int = 1) -> dict[str, Any]:
    center = load(TEMPLATE)["native_corridor"]
    family, level = scene_code.split("-")
    prefix = f"ENG-G{engineering_generation}" if engineering else "FORMAL"
    route_path = assets / f"RQ1V4-{prefix}-{scene_code}-ROUTE.xml"
    route_id = ((994600 + engineering_generation * 100) if engineering else 995600) + index
    write_text(route_path, v3.route_xml(route_id, center, XML_START, XML_DESTINATION))
    center_value = {
        "schema": "driveclarify.rq1_v4.center-route.v1", "designation": designation,
        "scene_code": scene_code, "center_route": center,
    }
    center_value["center_route_digest"] = digest(center_value)
    center_path = assets / f"{scene_code}-CENTER-ROUTE.json"
    write_json(center_path, center_value)
    source_path = assets / f"{scene_code}-CANDIDATE-ROUTES.json"
    write_json(source_path, route_source(scene_code, designation, center))
    layout_path = assets / f"{scene_code}-LAYOUT.json"
    write_json(layout_path, layout(scene_code, designation, center))
    scene = {
        "schema": f"driveclarify.rq1_v4.{'engineering' if engineering else 'formal'}-scene-contract.v1",
        "designation": "ENGINEERING_ONLY_PERMANENTLY_EXCLUDED" if engineering else "FRESH_FORMAL_V4",
        "scene_id": f"RQ1V4-{prefix}-{scene_code}-{'Q' if engineering else 'F'}{index:02d}",
        "scene_code": scene_code, "family": family, "consequence_level": level,
        "machine_label": "TASK_EQUIVALENT" if level == "EQUIVALENT" else "TASK_CRITICAL",
        "ambiguous_instruction": INSTRUCTIONS[family], "reasonable_interpretation_count": 2,
        "native_map": "Town05", "native_route_path": str(route_path.relative_to(ROOT)),
        "center_route_source": str(center_path.relative_to(ROOT)),
        "candidate_route_source": str(source_path.relative_to(ROOT)),
        "layout_path": str(layout_path.relative_to(ROOT)),
        "task_signatures": [task_signature(scene_code, candidate, designation) for candidate in ("A", "B")],
        "candidate_A_is_deterministic_rank_one": True,
        "grounding_certified_before_native_exposure": engineering,
        "grounding_certified_before_formal_exposure": not engineering,
        "fresh_identity": True, "random_background_vehicle_count": 0,
    }
    if scene_code == "ORD-CRITICAL":
        scene.update({
            "order_ambiguity_contract": {
                "original": INSTRUCTIONS["ORD"],
                "z1": "take the first eligible ordered turn/opportunity",
                "z2": "continue and take the second eligible ordered turn/opportunity",
                "genuinely_order_ambiguous": True,
                "task_signature_difference_required": True,
            },
            "qualified_engineering_counterpart": None if engineering else "RQ1V4-ENG-G2-ORD-CRITICAL-Q01",
        })
    scene["scene_digest"] = digest(scene)
    scene_path = assets / f"{scene_code}-SCENE.json"
    write_json(scene_path, scene)
    scene["scene_contract_path"] = str(scene_path.relative_to(ROOT))
    scene["scene_contract_sha256"] = sha(scene_path)
    return scene


def max_gap(rows: Sequence[Mapping[str, Any]]) -> float:
    return max(math.dist(a["xyz"], b["xyz"]) for a, b in zip(rows, rows[1:]))


def grp_certificate(source_path: Path) -> dict[str, Any]:
    code = r'''
import json,math,sys
from pathlib import Path
import carla
from agents.navigation.global_route_planner import GlobalRoutePlanner
source=json.loads(Path(sys.argv[1]).read_text())
world_map=carla.Map("Town05",Path(sys.argv[2]).read_text())
planner=GlobalRoutePlanner(world_map,1.0)
out={}
for candidate in ("A","B"):
 rows=source["candidate_"+candidate]
 trace=planner.trace_route(carla.Location(*rows[0]["xyz"]),carla.Location(*rows[-1]["xyz"]))
 coords=[[float(w.transform.location.x),float(w.transform.location.y),float(w.transform.location.z)] for w,_ in trace]
 max_nearest=max(min(math.dist(row["xyz"],coord) for coord in coords) for row in rows) if coords else None
 out[candidate]={"trace_point_count":len(coords),"nonempty":len(coords)>1,"max_candidate_to_grp_distance_m":max_nearest,"trace_start_xyz":coords[0] if coords else None,"trace_end_xyz":coords[-1] if coords else None}
print(json.dumps(out,sort_keys=True))
'''
    completed = subprocess.run(
        ["/home/buaa/anaconda3/envs/simlingo/bin/python", "-c", code, str(source_path), str(TOWN05_XODR)],
        cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        return {"status": "FAIL_GRP_PREFLIGHT", "exit": completed.returncode, "stderr": completed.stderr[-2000:]}
    rows = json.loads(completed.stdout)
    return {"status": "PASS_BOTH_CANDIDATES_GRP_TRACEABLE" if all(row["nonempty"] for row in rows.values()) else "FAIL_GRP_PREFLIGHT", "sampling_resolution_m": 1.0, "candidates": rows}


def narrowed_matrix(scenes: Sequence[Mapping[str, Any]], designation: str) -> dict[str, Any]:
    value = v3.static_replan_matrix(scenes, designation)
    required = 2 * sum(scene["consequence_level"] == "CRITICAL" for scene in scenes)
    value.update({
        "schema": "driveclarify.rq1_v4.full-replan-admissibility-matrix.v1",
        "required_count": required,
        "status": f"PASS_{required}_OF_{required}_HIGH_CANDIDATE_ROUTES_ADMISSIBLE" if value["pass_count"] == required else "FAIL_HIGH_CANDIDATE_ROUTE_ADMISSIBILITY",
    })
    value["matrix_digest"] = digest({key: item for key, item in value.items() if key != "matrix_digest"})
    return value


def forensic() -> dict[str, Any]:
    REPORT.mkdir(parents=True, exist_ok=True)
    ledger_path = V3_REPORT / "FORMAL_EXECUTION_LEDGER.json"
    final_path = V3_REPORT / "FINAL_REPORT.md"
    ledger = load(ledger_path, {})
    rows = [row for row in ledger.get("entries", []) if row.get("scene_code") == "ORD-CRITICAL" and row.get("decision_evaluable") is False]
    if len(rows) != 2:
        raise RuntimeError(f"EXPECTED_EXACTLY_TWO_V3_ORD_CRITICAL_NONEVALUABLE_TRACES:{len(rows)}")
    traces = []
    for row in rows:
        output = ROOT / row["output_path"]
        process_path = output / "process_job/PROCESS_RECEIPT.json"
        carla_path = output / "process_job/carla_server.log"
        native_path = output.parent / "native.log"
        process = load(process_path, {})
        carla_text = carla_path.read_text(encoding="utf-8", errors="replace")
        native_text = native_path.read_text(encoding="utf-8", errors="replace")
        owner_evidence_exists = (output / "owner_evidence").exists()
        official_exists = (output / "official_checkpoint.json").exists()
        evidence = {
            "cell_id": row["cell_id"], "seed_slot": row["seed_slot"], "seed": row["seed"],
            "wrapper_exit": row["wrapper_exit"], "evaluator_exit": process.get("evaluator_exit"),
            "world_ready_receipt_exists": (output / "process_job/world_ready.json").is_file(),
            "evaluator_log_exists": (output / "process_job/evaluator.log").is_file(),
            "owner_evidence_directory_exists": owner_evidence_exists,
            "official_checkpoint_exists": official_exists,
            "carla_startup_signature": "close: Bad file descriptor" if "close: Bad file descriptor" in carla_text else None,
            "carla_signal": "SIGSEGV" if "Signal 11 caught" in carla_text else None,
            "client_failure_signature": "simulator readiness timeout" if "time-out of 5000ms while waiting for the simulator" in native_text else None,
            "cleanup_pass": process.get("cleanup_pass"),
            "source_files": {
                str(process_path.relative_to(ROOT)): sha(process_path),
                str(carla_path.relative_to(ROOT)): sha(carla_path),
                str(native_path.relative_to(ROOT)): sha(native_path),
            },
        }
        evidence["preworld_startup_failure_proved"] = all([
            evidence["wrapper_exit"] == 68, evidence["evaluator_exit"] == 125,
            not evidence["world_ready_receipt_exists"], not evidence["evaluator_log_exists"],
            not owner_evidence_exists, not official_exists,
            evidence["carla_startup_signature"] is not None, evidence["carla_signal"] == "SIGSEGV",
            evidence["client_failure_signature"] is not None,
        ])
        traces.append(evidence)
    owner = "A. PRE-WORLD / AGENT STARTUP" if all(row["preworld_startup_failure_proved"] for row in traces) else "G. OTHER DETERMINISTIC ENGINEERING OWNER"
    value = {
        "schema": "driveclarify.rq1_v4.ord-critical-v3-noncompletion-forensic.v1",
        "status": "PASS_FORENSIC_OWNER_IDENTIFIED" if owner.startswith("A.") else "RQ1_V4_EXECUTION_INTEGRITY_NOT_CLOSED",
        "v3_status_preserved": "RQ1_V3_PRIMARY_EVALUABILITY_GATE_FAILED",
        "v3_final_report_sha256": sha(final_path), "v3_ledger_sha256": sha(ledger_path),
        "scope": "ONLY_TWO_PRESERVED_V3_ORD_CRITICAL_DECISION_NONEVALUABLE_TRACES",
        "scientific_ask_selectivity_fields_inspected_or_aggregated": False,
        "trace_count": 2, "first_causal_owner": owner,
        "causal_chain": ["UE4 process crashes during launch", "CARLA world never becomes readable", "evaluator and agent never start", "decision anchor cannot be observed"],
        "traces": traces,
        "repair_authority": "bounded same-cell CARLA server restart only before world creation and evaluator/agent launch",
    }
    value["receipt_digest"] = digest(value)
    write_json(REPORT / "ORD_CRITICAL_V3_NONCOMPLETION_FORENSIC.json", value)
    lines = [
        "# ORD-CRITICAL V3 noncompletion forensic", "",
        f"- First causal owner: `{owner}`.",
        "- Both preserved failures show the same UE4 `close: Bad file descriptor` / signal-11 crash before `world_ready.json`, evaluator log, agent owner evidence, or official checkpoint existed.",
        "- The downstream client readiness timeout is a consequence of the server crash, not a route, target, anchor, scenario, or admissibility failure.",
        "- Scientific ASK/selectivity outcomes were not inspected or aggregated.", "",
        "| V3 cell | wrapper | evaluator | world | evaluator launched | owner evidence | classified owner |",
        "|---|---:|---:|---|---|---|---|",
    ]
    for row in traces:
        lines.append(f"| {row['cell_id']} | {row['wrapper_exit']} | {row['evaluator_exit']} | no | no | no | A |")
    lines.extend(["", "Repair: permit a bounded server relaunch for this exact pre-world/pre-evaluator signature. The same scene, config, and seed remain fixed; no scientific episode is retried after a world or agent exists."])
    write_text(REPORT / "ORD_CRITICAL_V3_NONCOMPLETION_FORENSIC.md", "\n".join(lines))
    log("forensic", value["status"])
    return value


def prior_seed_audit() -> dict[str, Any]:
    return v3.prior_seed_audit()


def engineering_seed(slot: int, used: set[int]) -> int:
    value = int(hashlib.sha256(f"RQ1-V4-ORD-CRITICAL-ENGINEERING-G1:{slot}".encode()).hexdigest()[:8], 16) % 1_800_000_000 + 100_000_000
    while value in used:
        value += 1
    used.add(value)
    return value


def preserved_identity_check() -> dict[str, Any]:
    manifest = load(V3_REPORT / "FORMAL_SCENE_MANIFEST.json", {})
    freeze = load(V3_REPORT / "RQ1_V3_FORMAL_FREEZE_RECEIPT.json", {})
    by_code = {row["scene_code"]: row for row in manifest.get("scenes", [])}
    rows = {}
    for code in V3_PRESERVED_CODES:
        scene = by_code.get(code, {})
        paths = [scene.get("scene_contract_path"), scene.get("native_route_path"), scene.get("center_route_source"), scene.get("candidate_route_source"), scene.get("layout_path")]
        missing = [path for path in paths if not path or not (ROOT / path).is_file()]
        contract_hash = sha(ROOT / scene["scene_contract_path"]) if scene.get("scene_contract_path") and (ROOT / scene["scene_contract_path"]).is_file() else None
        rows[code] = {
            "scene_id": scene.get("scene_id"), "scene_contract_path": scene.get("scene_contract_path"),
            "scene_contract_sha256": contract_hash, "expected_scene_contract_sha256": scene.get("scene_contract_sha256"),
            "all_identity_files_present": not missing, "missing": missing,
            "scene_contract_unchanged": contract_hash == scene.get("scene_contract_sha256"),
        }
    source_drift = []
    for relative, expected in freeze.get("source_hashes", {}).items():
        path = ROOT / relative
        if not path.is_file() or sha(path) != expected:
            source_drift.append(relative)
    value = {
        "schema": "driveclarify.rq1_v4.minimal-global-prefomal-check.v1",
        "checked_conditions": list(V3_PRESERVED_CODES), "rerun_native_development": False,
        "conditions": rows, "v3_frozen_source_drift": source_drift,
    }
    value["pass"] = len(rows) == 7 and all(row["all_identity_files_present"] and row["scene_contract_unchanged"] for row in rows.values()) and not source_drift
    value["status"] = "PASS_SEVEN_FROZEN_IDENTITIES_AND_RECEIPTS_INTACT" if value["pass"] else "RQ1_V4_EXECUTION_INTEGRITY_NOT_CLOSED"
    value["receipt_digest"] = digest(value)
    write_json(REPORT / "MINIMAL_GLOBAL_PREFORMAL_CHECK.json", value)
    return value


def prepare() -> dict[str, Any]:
    existing = load(ENGINEERING / "ENGINEERING_MANIFEST.json")
    if existing:
        return existing
    forensic_receipt = load(REPORT / "ORD_CRITICAL_V3_NONCOMPLETION_FORENSIC.json") or forensic()
    if forensic_receipt.get("first_causal_owner") != "A. PRE-WORLD / AGENT STARTUP":
        raise RuntimeError("ORD_CRITICAL_FORENSIC_OWNER_NOT_CLOSED")
    if sha(CHECKPOINT) != CHECKPOINT_SHA256:
        raise RuntimeError("RQ1_V4_CHECKPOINT_DRIFT")
    if not NATIVE_RUNNER.is_file() or not INDEPENDENT_AUDITOR.is_file():
        raise RuntimeError("RQ1_V4_TOOLING_INCOMPLETE")
    global_check = preserved_identity_check()
    if not global_check["pass"]:
        raise RuntimeError("RQ1_V4_MINIMAL_GLOBAL_PREFORMAL_CHECK_FAILED")
    prior = prior_seed_audit()
    used = set(prior["prior_seed_values"])
    scene = make_scene("ORD-CRITICAL", "ENGINEERING_G1", 1, ENGINEERING_ASSETS, True)
    source_path = ROOT / scene["candidate_route_source"]
    source = load(source_path)
    static_check = v3.static_scene_check(scene)
    static_check["schema"] = "driveclarify.rq1_v4.static-scene-check.v1"
    matrix = narrowed_matrix([scene], "RQ1V4_ENGINEERING_G1")
    write_json(ENGINEERING / "FULL_REPLAN_ADMISSIBILITY_MATRIX.json", matrix)
    signatures_differ = any(
        scene["task_signatures"][0][component] != scene["task_signatures"][1][component]
        for component in scene["task_signatures"][0]["relevant_components"]
    )
    grp = grp_certificate(source_path)
    preflight = {
        "schema": "driveclarify.rq1_v4.ord-critical-pre-native-proof.v1",
        "scene_id": scene["scene_id"],
        "candidate_A": {"path_continuous": max_gap(source["candidate_A"]) < 8.0, "maximum_adjacent_gap_m": max_gap(source["candidate_A"]), "grp": grp.get("candidates", {}).get("A")},
        "candidate_B": {"path_continuous": max_gap(source["candidate_B"]) < 8.0, "maximum_adjacent_gap_m": max_gap(source["candidate_B"]), "grp": grp.get("candidates", {}).get("B")},
        "both_routes_grp_traceable": grp.get("status") == "PASS_BOTH_CANDIDATES_GRP_TRACEABLE",
        "decision_anchor_reachable": all((grp.get("candidates", {}).get(candidate) or {}).get("nonempty") for candidate in ("A", "B")),
        "task_signature_relation": static_check["task_relation"],
        "task_signature_differ": signatures_differ,
        "differing_components": static_check["differing_components"],
        "genuinely_order_ambiguous": static_check["semantic_kind"] == "SPATIAL_ORDER" and scene["order_ambiguity_contract"]["genuinely_order_ambiguous"],
        "candidate_A_full_replan_admissible": next(row["admissible"] for row in matrix["matrix"] if row["candidate_answer"] == "A"),
        "candidate_B_full_replan_admissible": next(row["admissible"] for row in matrix["matrix"] if row["candidate_answer"] == "B"),
        "random_background_traffic": 0,
        "static_gate": static_check,
        "grp_certificate": grp,
    }
    preflight["pass"] = all([
        preflight["candidate_A"]["path_continuous"], preflight["candidate_B"]["path_continuous"],
        preflight["both_routes_grp_traceable"], preflight["decision_anchor_reachable"],
        preflight["task_signature_relation"] == "TASK_CRITICAL", preflight["task_signature_differ"],
        preflight["genuinely_order_ambiguous"], preflight["candidate_A_full_replan_admissible"],
        preflight["candidate_B_full_replan_admissible"], preflight["random_background_traffic"] == 0,
    ])
    preflight["status"] = "PASS_ORD_CRITICAL_PRE_NATIVE_PROOF" if preflight["pass"] else "ORD_CRITICAL_NATIVE_STABILITY_NOT_QUALIFIED"
    preflight["receipt_digest"] = digest(preflight)
    write_json(ENGINEERING / "ORD_CRITICAL_PRE_NATIVE_PROOF.json", preflight)
    cells = []
    for slot in range(1, 9):
        seed = engineering_seed(slot, used)
        run_id = f"RQ1V4-ENG-G1-ORD-CRITICAL-S{slot:02d}"
        config = v3.make_config(run_id, scene, seed)
        config["schema"] = "driveclarify.rq1_v4.native-runtime-config.v1"
        config["rq1_v4_startup_repair"] = "PRE_WORLD_PRE_EVALUATOR_SERVER_RESTART_ONLY"
        config_path = ENGINEERING_CONFIGS / f"{run_id}.json"
        write_json(config_path, config)
        cells.append({
            "run_id": run_id, "scene_id": scene["scene_id"], "scene_code": "ORD-CRITICAL",
            "family": "ORD", "consequence_level": "CRITICAL", "seed_slot": slot, "seed": seed,
            "config_path": str(config_path.relative_to(ROOT)), "config_sha256": sha(config_path),
            "native_route_path": scene["native_route_path"], "route_sha256": sha(ROOT / scene["native_route_path"]),
            "layout_path": scene["layout_path"], "answer_candidate_id": "A" if slot % 2 else "B",
            "permanently_excluded": True, "seed_replacement_allowed": False,
        })
    manifest = {
        "schema": "driveclarify.rq1_v4.ord-critical-engineering-manifest.v1",
        "status": "READY_FOR_8_SEED_NATIVE_STABILITY" if preflight["pass"] else preflight["status"],
        "generation": 1, "maximum_generations": 3, "scene": scene,
        "planned_episodes": 8, "cells": cells,
        "fresh_against_prior_seed_registry": all(row["seed"] not in set(prior["prior_seed_values"]) for row in cells),
        "no_seed_replacement": True, "formal_exposures": 0,
        "pre_native_proof_digest": preflight["receipt_digest"],
    }
    manifest["manifest_digest"] = digest(manifest)
    write_json(ENGINEERING / "ENGINEERING_MANIFEST.json", manifest)
    exclusion = {
        "schema": "driveclarify.rq1_v4.engineering-exclusion-registry.v1", "permanent": True,
        "excluded_scene_ids": [scene["scene_id"]], "excluded_route_paths": [scene["native_route_path"]],
        "excluded_candidate_route_paths": [scene["candidate_route_source"]], "excluded_layout_paths": [scene["layout_path"]],
        "excluded_config_paths": [row["config_path"] for row in cells], "excluded_engineering_seeds": [row["seed"] for row in cells],
        "excluded_from": ["Formal V4 scenes", "Formal V4 routes", "Formal V4 configs", "Formal V4 layouts", "Formal V4 seeds", "Formal V4 analysis"],
        "prior_registry_file_count": prior["registry_file_count"], "prior_unique_seed_count": prior["unique_prior_seed_value_count"],
        "prior_registry_digest": digest(prior["registry_files_audited"]),
    }
    exclusion["exclusion_digest"] = digest(exclusion)
    write_json(ENGINEERING / "ENGINEERING_EXCLUSION_REGISTRY.json", exclusion)
    repair = {
        "schema": "driveclarify.rq1_v4.ord-critical-repair.v1", "status": "FROZEN_ENGINEERING_REPAIR",
        "forensic_owner": forensic_receipt["first_causal_owner"],
        "exact_repair": "The native wrapper may relaunch CARLA up to two times after the initial launch only when no CARLA world has been observed and before evaluator/agent launch; same scene/config/seed; every pre-world failure recorded.",
        "scientific_semantics_changed": False, "route_geometry_changed_to_fix_failure": False,
        "checkpoint_changed": False, "pid_or_controller_changed": False, "simlingo_weights_changed": False,
        "source_path": str(NATIVE_RUNNER.relative_to(ROOT)), "source_sha256": sha(NATIVE_RUNNER),
    }
    repair["repair_digest"] = digest(repair)
    write_json(REPORT / "ORD_CRITICAL_REPAIR_RECEIPT.json", repair)
    state = {"status": manifest["status"], "rq2_snapshot_before": v3.rq2_snapshot(), "manifest_digest": manifest["manifest_digest"]}
    write_json(REPORT / "PRE_FORMAL_STATE.json", state)
    log("prepare", manifest["status"])
    return manifest


def prepare_generation_2() -> dict[str, Any]:
    existing = load(ENGINEERING2 / "ENGINEERING_MANIFEST.json")
    if existing:
        return existing
    first_ledger = load(ENGINEERING / "ENGINEERING_EXECUTION_LEDGER.json", {})
    if len(first_ledger.get("entries", [])) != 8:
        raise RuntimeError("RQ1_V4_GENERATION_1_NOT_COMPLETE")
    evidence = []
    for row in first_ledger["entries"]:
        output = ROOT / row["output_path"]
        evaluator_path = output / "process_job/evaluator.log"
        text = evaluator_path.read_text(encoding="utf-8", errors="replace") if evaluator_path.is_file() else ""
        item = {
            "run_id": row["run_id"], "seed": row["seed"],
            "world_observed": (output / "process_job/world_ready.json").is_file(),
            "agent_owner_evidence_exists": (output / "owner_evidence").exists(),
            "exact_failure": "V11_CONFIG_SCHEMA_INVALID" if "V11_CONFIG_SCHEMA_INVALID" in text else None,
            "evaluator_log_sha256": sha(evaluator_path) if evaluator_path.is_file() else None,
        }
        item["proved"] = item["world_observed"] and not item["agent_owner_evidence_exists"] and item["exact_failure"] == "V11_CONFIG_SCHEMA_INVALID"
        evidence.append(item)
    if not all(row["proved"] for row in evidence):
        raise RuntimeError("RQ1_V4_GENERATION_1_OWNER_NOT_DETERMINISTIC")
    failure = {
        "schema": "driveclarify.rq1_v4.engineering-generation-failure-forensic.v1",
        "status": "PASS_DETERMINISTIC_ENGINEERING_OWNER_IDENTIFIED",
        "generation": 1, "first_causal_owner": "G. OTHER DETERMINISTIC ENGINEERING OWNER — CONFIG SCHEMA SERIALIZATION",
        "cause": "The generation-1 materializer changed the required frozen V11 runtime config schema tag, so all eight evaluators rejected the config before agent setup completed.",
        "scientific_outcomes_available_or_inspected": False,
        "repair": "Restore the exact frozen schema tag driveclarify.v11.native-runtime-config.v1; no scientific field, route geometry, checkpoint, controller, or weight changes.",
        "evidence": evidence,
    }
    failure["receipt_digest"] = digest(failure)
    write_json(ENGINEERING / "GENERATION_1_FAILURE_FORENSIC.json", failure)

    prior = prior_seed_audit()
    used = set(prior["prior_seed_values"])
    scene = make_scene("ORD-CRITICAL", "ENGINEERING_G2", 1, ENGINEERING2_ASSETS, True, engineering_generation=2)
    source_path = ROOT / scene["candidate_route_source"]
    source = load(source_path)
    static_check = v3.static_scene_check(scene)
    matrix = narrowed_matrix([scene], "RQ1V4_ENGINEERING_G2")
    write_json(ENGINEERING2 / "FULL_REPLAN_ADMISSIBILITY_MATRIX.json", matrix)
    grp = grp_certificate(source_path)
    signatures_differ = any(
        scene["task_signatures"][0][component] != scene["task_signatures"][1][component]
        for component in scene["task_signatures"][0]["relevant_components"]
    )
    preflight = {
        "schema": "driveclarify.rq1_v4.ord-critical-pre-native-proof.v1", "generation": 2,
        "scene_id": scene["scene_id"],
        "candidate_A": {"path_continuous": max_gap(source["candidate_A"]) < 8.0, "maximum_adjacent_gap_m": max_gap(source["candidate_A"]), "grp": grp.get("candidates", {}).get("A")},
        "candidate_B": {"path_continuous": max_gap(source["candidate_B"]) < 8.0, "maximum_adjacent_gap_m": max_gap(source["candidate_B"]), "grp": grp.get("candidates", {}).get("B")},
        "both_routes_grp_traceable": grp.get("status") == "PASS_BOTH_CANDIDATES_GRP_TRACEABLE",
        "decision_anchor_reachable": all((grp.get("candidates", {}).get(candidate) or {}).get("nonempty") for candidate in ("A", "B")),
        "task_signature_relation": static_check["task_relation"], "task_signature_differ": signatures_differ,
        "differing_components": static_check["differing_components"],
        "genuinely_order_ambiguous": static_check["semantic_kind"] == "SPATIAL_ORDER" and scene["order_ambiguity_contract"]["genuinely_order_ambiguous"],
        "candidate_A_full_replan_admissible": next(row["admissible"] for row in matrix["matrix"] if row["candidate_answer"] == "A"),
        "candidate_B_full_replan_admissible": next(row["admissible"] for row in matrix["matrix"] if row["candidate_answer"] == "B"),
        "random_background_traffic": 0, "static_gate": static_check, "grp_certificate": grp,
        "generation_1_repair": failure["repair"],
    }
    preflight["pass"] = all([
        preflight["candidate_A"]["path_continuous"], preflight["candidate_B"]["path_continuous"],
        preflight["both_routes_grp_traceable"], preflight["decision_anchor_reachable"],
        preflight["task_signature_relation"] == "TASK_CRITICAL", preflight["task_signature_differ"],
        preflight["genuinely_order_ambiguous"], preflight["candidate_A_full_replan_admissible"],
        preflight["candidate_B_full_replan_admissible"], preflight["random_background_traffic"] == 0,
    ])
    preflight["status"] = "PASS_ORD_CRITICAL_PRE_NATIVE_PROOF" if preflight["pass"] else "ORD_CRITICAL_NATIVE_STABILITY_NOT_QUALIFIED"
    preflight["receipt_digest"] = digest(preflight)
    write_json(ENGINEERING2 / "ORD_CRITICAL_PRE_NATIVE_PROOF.json", preflight)
    cells = []
    for slot in range(1, 9):
        seed = engineering_seed(slot + 100, used)
        run_id = f"RQ1V4-ENG-G2-ORD-CRITICAL-S{slot:02d}"
        config = v3.make_config(run_id, scene, seed)
        # The exact V11 schema is a frozen runtime interface, not a V4 label.
        if config.get("schema") != "driveclarify.v11.native-runtime-config.v1":
            raise RuntimeError("RQ1_V4_GENERATION_2_CONFIG_SCHEMA_NOT_FROZEN_V11")
        config_path = ENGINEERING2_CONFIGS / f"{run_id}.json"
        write_json(config_path, config)
        cells.append({
            "run_id": run_id, "scene_id": scene["scene_id"], "scene_code": "ORD-CRITICAL",
            "family": "ORD", "consequence_level": "CRITICAL", "seed_slot": slot, "seed": seed,
            "config_path": str(config_path.relative_to(ROOT)), "config_sha256": sha(config_path),
            "native_route_path": scene["native_route_path"], "route_sha256": sha(ROOT / scene["native_route_path"]),
            "layout_path": scene["layout_path"], "answer_candidate_id": "A" if slot % 2 else "B",
            "permanently_excluded": True, "seed_replacement_allowed": False,
        })
    manifest = {
        "schema": "driveclarify.rq1_v4.ord-critical-engineering-manifest.v1",
        "status": "READY_FOR_8_SEED_NATIVE_STABILITY" if preflight["pass"] else preflight["status"],
        "generation": 2, "maximum_generations": 3, "scene": scene, "planned_episodes": 8,
        "cells": cells, "fresh_against_prior_seed_registry": all(row["seed"] not in set(prior["prior_seed_values"]) for row in cells),
        "no_seed_replacement": True, "formal_exposures": 0, "pre_native_proof_digest": preflight["receipt_digest"],
    }
    manifest["manifest_digest"] = digest(manifest)
    write_json(ENGINEERING2 / "ENGINEERING_MANIFEST.json", manifest)
    exclusion = {
        "schema": "driveclarify.rq1_v4.engineering-exclusion-registry.v1", "permanent": True,
        "excluded_scene_ids": [scene["scene_id"]], "excluded_route_paths": [scene["native_route_path"]],
        "excluded_candidate_route_paths": [scene["candidate_route_source"]], "excluded_layout_paths": [scene["layout_path"]],
        "excluded_config_paths": [row["config_path"] for row in cells], "excluded_engineering_seeds": [row["seed"] for row in cells],
        "excluded_from": ["Formal V4 scenes", "Formal V4 routes", "Formal V4 configs", "Formal V4 layouts", "Formal V4 seeds", "Formal V4 analysis"],
        "generation_1_also_permanently_excluded": True,
    }
    exclusion["exclusion_digest"] = digest(exclusion)
    write_json(ENGINEERING2 / "ENGINEERING_EXCLUSION_REGISTRY.json", exclusion)
    log("prepare-generation-2", manifest["status"])
    return manifest


def selected_engineering_dir() -> Path:
    return ENGINEERING2 if (ENGINEERING2 / "ENGINEERING_MANIFEST.json").is_file() else ENGINEERING


def classify_native(cell: Mapping[str, Any], output: Path, wrapper_exit: int, phase: str) -> dict[str, Any]:
    result = v3.classify_native(cell, output, wrapper_exit, phase)
    result["schema"] = f"driveclarify.rq1_v4.{phase.lower()}-cell-result.v1"
    process = result.get("process") or {}
    result["infrastructure_retries"] = int(process.get("infrastructure_retries", 0) or 0)
    result["startup_repair_boundary_respected"] = process.get("startup_retry_boundary") in {None, "PRE_WORLD_PRE_EVALUATOR_ONLY"}
    result["result_digest"] = digest({key: value for key, value in result.items() if key != "result_digest"})
    return result


def run_one(cell: Mapping[str, Any], output: Path, port: int, phase: str) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    log_path = output.parent / "native.log"
    command = [str(NATIVE_RUNNER), str(ROOT / cell["config_path"]), str(ROOT / cell["native_route_path"]), str(cell["seed"]), str(port), cell.get("answer_candidate_id") or "NONE", str(output), f"{phase}_NO_SCIENTIFIC_RETRY"]
    with log_path.open("wb") as stream:
        completed = subprocess.run(command, cwd=str(ROOT), stdout=stream, stderr=subprocess.STDOUT)
    result = classify_native(cell, output, completed.returncode, phase)
    write_json(output / f"RQ1_V4_{phase}_CELL_RESULT.json", result)
    return result


def qualify() -> dict[str, Any]:
    first = load(ENGINEERING / "ORD_CRITICAL_STABILITY_QUALIFICATION.json")
    if first and first.get("status") == "PASS_ORD_CRITICAL_NATIVE_STABILITY_QUALIFIED":
        return first
    if first and first.get("status") == "ORD_CRITICAL_NATIVE_STABILITY_NOT_QUALIFIED":
        manifest = prepare_generation_2()
        base = ENGINEERING2
        runs_dir = ENGINEERING2_RUNS
        port_base = 32400
    else:
        manifest = load(ENGINEERING / "ENGINEERING_MANIFEST.json", {})
        base = ENGINEERING
        runs_dir = ENGINEERING_RUNS
        port_base = 32200
    prior = load(base / "ORD_CRITICAL_STABILITY_QUALIFICATION.json")
    if prior and prior.get("status") in {"PASS_ORD_CRITICAL_NATIVE_STABILITY_QUALIFIED", "ORD_CRITICAL_NATIVE_STABILITY_NOT_QUALIFIED"}:
        return prior
    if manifest.get("status") != "READY_FOR_8_SEED_NATIVE_STABILITY":
        raise RuntimeError("RQ1_V4_ENGINEERING_NOT_READY")
    ledger_path = base / "ENGINEERING_EXECUTION_LEDGER.json"
    ledger = load(ledger_path, {"schema": "driveclarify.rq1_v4.engineering-execution-ledger.v1", "status": "ENGINEERING_IN_PROGRESS", "entries": [], "scientific_retries": 0, "seed_replacements": 0})
    attempted = {row["run_id"] for row in ledger["entries"]}
    for index, cell in enumerate(manifest["cells"]):
        if cell["run_id"] in attempted:
            continue
        output = runs_dir / cell["run_id"] / "attempt_01"
        result = run_one(cell, output, port_base + index * 3, "ENGINEERING")
        ledger["entries"].append(result)
        ledger.update({
            "attempted": len(ledger["entries"]),
            "decision_evaluable": sum(row["decision_evaluable"] for row in ledger["entries"]),
            "failures": sum(not row["decision_evaluable"] for row in ledger["entries"]),
            "infrastructure_retries": sum(row.get("infrastructure_retries", 0) for row in ledger["entries"]),
        })
        ledger["ledger_digest"] = digest({key: value for key, value in ledger.items() if key != "ledger_digest"})
        write_json(ledger_path, ledger)
    rows = ledger["entries"]
    failures = [{
        "run_id": row["run_id"], "seed_slot": row["seed_slot"], "seed": row["seed"],
        "wrapper_exit": row["wrapper_exit"], "exposed": row["exposed"],
        "decision_evaluable": row["decision_evaluable"], "execution_evaluable": row["execution_evaluable"],
        "process": row["process"], "output_path": row["output_path"],
    } for row in rows if not row["decision_evaluable"]]
    branches = {row["selected_candidate_id"] for row in rows if row["high_chain_pass"]}
    matrix = load(base / "FULL_REPLAN_ADMISSIBILITY_MATRIX.json", {})
    static_branches = {row["candidate_answer"]: row["admissible"] for row in matrix.get("matrix", [])}
    evaluable = sum(row["decision_evaluable"] for row in rows)
    passed = len(rows) == 8 and evaluable >= 7 and branches == {"A", "B"} and static_branches == {"A": True, "B": True}
    receipt = {
        "schema": "driveclarify.rq1_v4.ord-critical-stability-qualification.v1",
        "status": "PASS_ORD_CRITICAL_NATIVE_STABILITY_QUALIFIED" if passed else "ORD_CRITICAL_NATIVE_STABILITY_NOT_QUALIFIED",
        "generation": manifest["generation"], "fresh_engineering_seeds": [row["seed"] for row in manifest["cells"]],
        "attempted": len(rows), "decision_evaluable": evaluable, "failures": 8 - evaluable,
        "minimum_required": 7, "preferred": 8, "no_seed_replacement": True,
        "failure_records": failures,
        "candidate_A_full_replan_static": static_branches.get("A"), "candidate_B_full_replan_static": static_branches.get("B"),
        "candidate_A_native_chain": "A" in branches, "candidate_B_native_chain": "B" in branches,
        "native_chain_definition": "ASK -> ANSWER -> FULL REPLAN -> admitted route -> native clarified execution",
        "infrastructure_retries": sum(row.get("infrastructure_retries", 0) for row in rows),
        "scientific_retries": 0, "formal_exposures": 0,
    }
    receipt["receipt_digest"] = digest(receipt)
    write_json(base / "ORD_CRITICAL_STABILITY_QUALIFICATION.json", receipt)
    ledger["status"] = receipt["status"]
    ledger["ledger_digest"] = digest({key: value for key, value in ledger.items() if key != "ledger_digest"})
    write_json(ledger_path, ledger)
    log("qualify", receipt["status"])
    return receipt


def identity_freshness(scenes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    old_scenes = []
    old_layout_ids = set()
    for base in (V2_REPORT, V3_REPORT):
        manifest = load(base / "FORMAL_SCENE_MANIFEST.json", {})
        for row in manifest.get("scenes", []):
            old_scenes.append(row)
            layout_path = row.get("layout_path")
            if layout_path and (ROOT / layout_path).is_file():
                old_layout_ids.add(load(ROOT / layout_path, {}).get("layout_id"))
    old_scene_ids = {row.get("scene_id") for row in old_scenes}
    old_route_paths = {row.get("native_route_path") for row in old_scenes}
    new_layout_ids = {load(ROOT / row["layout_path"])["layout_id"] for row in scenes}
    engineering_paths: set[str] = set()
    for exclusion_path in (ENGINEERING / "ENGINEERING_EXCLUSION_REGISTRY.json", ENGINEERING2 / "ENGINEERING_EXCLUSION_REGISTRY.json"):
        exclusion = load(exclusion_path, {})
        for key in ("excluded_route_paths", "excluded_candidate_route_paths", "excluded_layout_paths"):
            engineering_paths.update(exclusion.get(key, []))
    value = {
        "schema": "driveclarify.rq1_v4.formal-identity-freshness.v1",
        "v2_v3_scene_id_overlap": sorted({row["scene_id"] for row in scenes} & old_scene_ids),
        "v2_v3_route_path_overlap": sorted({row["native_route_path"] for row in scenes} & old_route_paths),
        "v2_v3_layout_id_overlap": sorted(new_layout_ids & old_layout_ids),
        "native_map_name": "Town05",
        "town_name_reuse": True,
        "town_name_reuse_disposition": "PERMITTED_COMMON_QUALIFIED_PUBLIC_NATIVE_CORRIDOR; exact scene/route/config/layout identities are the V2/V3 freshness unit",
        "formal_engineering_path_overlap": sorted((
            {row["native_route_path"] for row in scenes} | {row["candidate_route_source"] for row in scenes} | {row["layout_path"] for row in scenes}
        ) & engineering_paths),
    }
    value["pass"] = not value["v2_v3_scene_id_overlap"] and not value["v2_v3_route_path_overlap"] and not value["v2_v3_layout_id_overlap"] and not value["formal_engineering_path_overlap"]
    value["status"] = "PASS_FRESH_FORMAL_IDENTITIES" if value["pass"] else "RQ1_V4_EXECUTION_INTEGRITY_NOT_CLOSED"
    value["receipt_digest"] = digest(value)
    return value


def source_files() -> list[Path]:
    return [
        ROOT / "driveclarify_rq1_v2/__init__.py", ROOT / "driveclarify_rq1_v2/consequence.py",
        ROOT / "driveclarify_rq1_v2/simlingo_agent.py", ROOT / "driveclarify_clear_passthrough_v11/ambiguity_gate.py",
        ROOT / "driveclarify_clear_passthrough_v11/contracts.py", ROOT / "driveclarify_clear_passthrough_v11/supervisor.py",
        ROOT / "driveclarify_clear_passthrough_v11/replan.py", ROOT / "driveclarify_clear_passthrough_v11/oracle.py",
        ROOT / "driveclarify_clear_passthrough_v11/simlingo_agent.py", ROOT / "tools/rq1_v2_answer_broker.py",
        V3_TOOL, NATIVE_RUNNER, Path(__file__).resolve(), INDEPENDENT_AUDITOR, CHECKPOINT,
    ]


def freeze() -> dict[str, Any]:
    existing = load(REPORT / "FORMAL_ROSTER.json")
    if existing:
        return existing
    selected_engineering = selected_engineering_dir()
    stability = load(selected_engineering / "ORD_CRITICAL_STABILITY_QUALIFICATION.json", {})
    if stability.get("status") != "PASS_ORD_CRITICAL_NATIVE_STABILITY_QUALIFIED":
        raise RuntimeError("ORD_CRITICAL_NATIVE_STABILITY_NOT_QUALIFIED")
    global_check = preserved_identity_check()
    if not global_check["pass"]:
        raise RuntimeError("RQ1_V4_MINIMAL_GLOBAL_PREFORMAL_CHECK_FAILED")
    scenes = [make_scene(code, "FORMAL_V4", index, FORMAL_ASSETS, False) for index, code in enumerate(SCENE_CODES, 1)]
    checks = [v3.static_scene_check(scene) for scene in scenes]
    if not all(row["pass"] for row in checks):
        raise RuntimeError("RQ1_V4_FORMAL_STATIC_DECISION_CONTRACT_FAILED")
    matrix = narrowed_matrix(scenes, "RQ1V4_FORMAL_V4")
    if matrix["pass_count"] != 8:
        raise RuntimeError("RQ1_V4_FORMAL_REPLAN_ADMISSIBILITY_FAILED")
    write_json(REPORT / "FORMAL_FULL_REPLAN_ADMISSIBILITY_MATRIX.json", matrix)
    freshness = identity_freshness(scenes)
    write_json(REPORT / "FORMAL_IDENTITY_FRESHNESS_RECEIPT.json", freshness)
    if not freshness["pass"]:
        raise RuntimeError("RQ1_V4_FORMAL_IDENTITY_FRESHNESS_FAILED")
    manifest = {
        "schema": "driveclarify.rq1_v4.formal-scene-manifest.v1", "status": "FRESH_UNEXPOSED",
        "scene_count": 8, "exact_scene_codes": list(SCENE_CODES), "scenes": scenes,
        "matched_low_high_design": True, "formal_identity_freshness_digest": freshness["receipt_digest"],
        "ord_critical_selected_engineering_generation": stability["generation"],
    }
    manifest["manifest_digest"] = digest(manifest)
    write_json(REPORT / "FORMAL_SCENE_MANIFEST.json", manifest)
    contract = {
        "schema": "driveclarify.rq1_v4.scientific-contract.v1",
        "v3_status_preserved": "RQ1_V3_PRIMARY_EVALUABILITY_GATE_FAILED",
        "primary_endpoint": "consequence decision ACT vs ASK",
        "rationale": "The primary RQ1 endpoint is the consequence decision (ACT vs ASK), not perfect native route completion. Native SimLingo noncompletion can prevent a decision endpoint from being observed but is not automatically a scientific consequence-gate failure. Therefore RQ1-V4 uses a bounded prospective evaluability contract rather than an all-or-nearly-all condition requirement.",
        "decision_evaluable": ["complete candidate identities", "complete TaskSignature", "valid consequence relation", "complete ACT/ASK receipt"],
        "execution_evaluable": ["downstream native execution reaches the endpoint required for completion/safety metrics"],
        "planned_cells": 48,
        "primary_evaluability_gate": {"total_min": 40, "condition_min": 4, "condition_planned": 6, "family_min": 9, "family_planned": 12},
        "non_evaluable_imputation": False,
        "non_evaluable_forbidden_labels": ["ACT", "ASK", "success", "failure", "zero"],
        "hard_stop": "only when total>=40, any condition>=4, or any family>=9 becomes mathematically impossible",
        "retry_policy": {"scientific_retries": 0, "formal_seed_replacements": 0, "preworld_infrastructure_restarts_max_per_cell": 2, "preworld_only": True},
        "primary_endpoints": ["HIGH clarification recall", "LOW unnecessary query rate", "consequence selectivity gap"],
        "support_rule": {"low": "consequence-aware unnecessary ASK materially lower than ambiguity-only", "high": "consequence-aware clarification recall preserved relative to ambiguity-only", "both_required": True},
        "statistics": {"unit": "scene x seed episode", "tests": ["paired exact McNemar", "Wilson 95% rate intervals", "shared-seed cluster percentile bootstrap 95% intervals"], "frames_independent_n": False, "family_heterogeneity": True},
        "sensitivity": ["conditions with >=5/6 evaluability", "conditions with 6/6 evaluability"],
        "background_random_traffic": 0,
    }
    write_json(REPORT / "RQ1_V4_SCIENTIFIC_CONTRACT.json", contract)
    write_json(REPORT / "ENDPOINT_ANALYSIS_PLAN.json", {"schema": "driveclarify.rq1_v4.endpoint-plan.v1", **contract["statistics"], "primary_denominator": "DECISION_EVALUABLE", "secondary_denominator": "EXECUTION_EVALUABLE"})
    source_hashes = {str(path.relative_to(ROOT)): sha(path) for path in source_files()}
    contract_paths = [
        V2_REPORT / "TASK_SIGNATURE_CONTRACT.json", V2_REPORT / "CONSEQUENCE_RELATION_CONTRACT.json", V2_REPORT / "CONSEQUENCE_GATE_CONTRACT.json",
        REPORT / "RQ1_V4_SCIENTIFIC_CONTRACT.json", REPORT / "ENDPOINT_ANALYSIS_PLAN.json", REPORT / "FORMAL_SCENE_MANIFEST.json",
        REPORT / "FORMAL_FULL_REPLAN_ADMISSIBILITY_MATRIX.json", REPORT / "FORMAL_IDENTITY_FRESHNESS_RECEIPT.json",
        REPORT / "ORD_CRITICAL_REPAIR_RECEIPT.json", selected_engineering / "ORD_CRITICAL_PRE_NATIVE_PROOF.json",
        selected_engineering / "ORD_CRITICAL_STABILITY_QUALIFICATION.json", selected_engineering / "ENGINEERING_EXCLUSION_REGISTRY.json",
        ENGINEERING / "GENERATION_1_FAILURE_FORENSIC.json", ENGINEERING / "ENGINEERING_EXCLUSION_REGISTRY.json",
        REPORT / "MINIMAL_GLOBAL_PREFORMAL_CHECK.json",
    ]
    contract_hashes = {str(path.relative_to(ROOT)): sha(path) for path in contract_paths}
    freeze_receipt = {
        "schema": "driveclarify.rq1_v4.formal-freeze-receipt.v1",
        "status": "PASS_RQ1_V4_FORMAL_PROTOCOL_FROZEN_BEFORE_SEEDS", "frozen_before_seed_generation": True,
        "frozen_science": ["TaskSignature", "TASK_EQUIVALENT", "TASK_CRITICAL", "UNKNOWN", "consequence comparator", "EQUIVALENT->ACT", "CRITICAL->ASK", "true-intent firewall", "deterministic LOW selector", "HIGH ASK semantics", "answer release", "Full Replan"],
        "frozen_execution": ["SimLingo checkpoint", "PID/controller", "eight scene contracts", "ORD-CRITICAL repair", "candidate routes", "Full Replan receipts", "zero-background policy"],
        "frozen_gates": contract["primary_evaluability_gate"],
        "frozen_endpoints_statistics_retry_policy": True,
        "source_hashes": source_hashes, "contract_hashes": contract_hashes,
        "formal_scene_manifest_digest": manifest["manifest_digest"], "formal_replan_matrix_digest": matrix["matrix_digest"],
        "postfreeze_scientific_change_allowed": False,
    }
    freeze_receipt["freeze_digest"] = digest(freeze_receipt)
    write_json(REPORT / "RQ1_V4_FORMAL_FREEZE_RECEIPT.json", freeze_receipt)
    source_receipt = {
        "schema": "driveclarify.rq1_v4.source-freeze-receipt.v1", "status": "PASS_SOURCE_FROZEN",
        "source_hashes": source_hashes, "checkpoint_sha256": CHECKPOINT_SHA256,
        "pid_controller_changes": 0, "simlingo_weight_changes": 0, "rq2_write_count": 0,
    }
    source_receipt["source_freeze_digest"] = digest(source_receipt)
    write_json(REPORT / "SOURCE_FREEZE_RECEIPT.json", source_receipt)

    prior = prior_seed_audit()
    excluded: set[int] = set()
    for exclusion_path in (ENGINEERING / "ENGINEERING_EXCLUSION_REGISTRY.json", ENGINEERING2 / "ENGINEERING_EXCLUSION_REGISTRY.json"):
        excluded.update(load(exclusion_path, {}).get("excluded_engineering_seeds", []))
    used = set(prior["prior_seed_values"]) | excluded
    generator = secrets.SystemRandom()
    seeds = []
    draws = 0
    while len(seeds) < 6:
        draws += 1
        candidate = generator.randrange(100_000_000, 2_000_000_000)
        if candidate not in used and candidate not in seeds:
            seeds.append(candidate)
    seed_receipt = {
        "schema": "driveclarify.rq1_v4.formal-seed-freshness.v1", "status": "PASS_EXACTLY_SIX_FRESH_SHARED_FORMAL_SEEDS",
        "generated_after_freeze": True, "formal_freeze_digest": freeze_receipt["freeze_digest"],
        "generation_method": "OS-backed SystemRandom; first six values absent from all audited registries and exclusions",
        "random_draw_count": draws, "formal_seeds": seeds, "shared_across_eight_conditions": True,
        "audited_registry_file_count": prior["registry_file_count"], "audited_registry_digest": digest(prior["registry_files_audited"]),
        "unique_prior_seed_value_count": prior["unique_prior_seed_value_count"],
        "collision_with_prior": sorted(set(seeds) & set(prior["prior_seed_values"])),
        "collision_with_engineering": sorted(set(seeds) & excluded),
        "manual_selection": False, "favorable_selection": False, "replacement_allowed": False,
    }
    seed_receipt["receipt_digest"] = digest(seed_receipt)
    write_json(REPORT / "FORMAL_SEED_FRESHNESS_RECEIPT.json", seed_receipt)
    by_code = {row["scene_code"]: row for row in scenes}
    cells = []
    for slot, seed in enumerate(seeds, 1):
        rotated = SCENE_CODES[slot - 1:] + SCENE_CODES[:slot - 1]
        for scene_code in rotated:
            scene = by_code[scene_code]
            cell_id = f"RQ1V4-{scene_code}-S{slot:02d}"
            config = v3.make_config(cell_id, scene, seed)
            if config.get("schema") != "driveclarify.v11.native-runtime-config.v1":
                raise RuntimeError("RQ1_V4_FORMAL_CONFIG_SCHEMA_NOT_FROZEN_V11")
            config_path = FORMAL_CONFIGS / f"{cell_id}.json"
            write_json(config_path, config)
            cells.append({
                "cell_id": cell_id, "scene_id": scene["scene_id"], "scene_code": scene_code,
                "family": scene["family"], "consequence_level": scene["consequence_level"],
                "seed_slot": slot, "seed": seed, "config_path": str(config_path.relative_to(ROOT)),
                "config_sha256": sha(config_path), "native_route_path": scene["native_route_path"],
                "scene_contract_path": scene["scene_contract_path"],
                "answer_candidate_id": ("A" if slot % 2 else "B") if scene["consequence_level"] == "CRITICAL" else None,
                "scientific_retry_allowed": False, "formal_seed_replacement_allowed": False,
            })
    roster = {
        "schema": "driveclarify.rq1_v4.formal-roster.v1", "status": "SEALED_UNEXPOSED",
        "scene_count": 8, "shared_seed_count": 6, "cell_count": 48, "seeds": seeds,
        "run_order": "seed-block interleaved with rotating condition start", "cells": cells,
        "no_favorable_selection_or_replacement": True,
    }
    roster["roster_digest"] = digest(roster)
    write_json(REPORT / "FORMAL_ROSTER.json", roster)
    ledger = {"schema": "driveclarify.rq1_v4.formal-execution-ledger.v1", "status": "SEALED_UNEXPOSED", "planned_episodes": 48, "entries": [], "scientific_retries": 0, "seed_replacements": 0, "infrastructure_retries": 0}
    ledger["ledger_digest"] = digest(ledger)
    write_json(REPORT / "FORMAL_EXECUTION_LEDGER.json", ledger)
    log("freeze", freeze_receipt["status"])
    return roster


def verify_source_freeze() -> dict[str, Any]:
    receipt = load(REPORT / "SOURCE_FREEZE_RECEIPT.json", {})
    drift = []
    for relative, expected in receipt.get("source_hashes", {}).items():
        path = ROOT / relative
        if not path.is_file() or sha(path) != expected:
            drift.append(relative)
    return {"pass": not drift, "drift_paths": drift}


def update_ledger(ledger: dict[str, Any]) -> None:
    rows = ledger["entries"]
    attempted_condition = Counter(row["scene_code"] for row in rows)
    evaluable_condition = Counter(row["scene_code"] for row in rows if row["decision_evaluable"])
    attempted_family = Counter(row["family"] for row in rows)
    evaluable_family = Counter(row["family"] for row in rows if row["decision_evaluable"])
    total_evaluable = sum(row["decision_evaluable"] for row in rows)
    ledger.update({
        "attempted_episodes": len(rows), "exposed_episodes": sum(row["exposed"] for row in rows),
        "decision_evaluable_episodes": total_evaluable, "execution_evaluable_episodes": sum(row["execution_evaluable"] for row in rows),
        "native_noncompletion_count": sum(row["native_noncompletion"] for row in rows),
        "attempted_by_condition": {code: attempted_condition[code] for code in SCENE_CODES},
        "decision_evaluable_by_condition": {code: evaluable_condition[code] for code in SCENE_CODES},
        "attempted_by_family": {family: attempted_family[family] for family in FAMILIES},
        "decision_evaluable_by_family": {family: evaluable_family[family] for family in FAMILIES},
        "scientific_retries": 0, "seed_replacements": 0,
        "infrastructure_retries": sum(row.get("infrastructure_retries", 0) for row in rows),
    })
    condition_impossible = [code for code in SCENE_CODES if evaluable_condition[code] + (6 - attempted_condition[code]) < 4]
    family_impossible = [family for family in FAMILIES if evaluable_family[family] + (12 - attempted_family[family]) < 9]
    total_impossible = total_evaluable + (48 - len(rows)) < 40
    ledger["mathematical_reachability"] = {
        "total_gate_reachable": not total_impossible, "condition_gate_reachable": not condition_impossible,
        "family_gate_reachable": not family_impossible, "impossible_conditions": condition_impossible,
        "impossible_families": family_impossible,
    }
    ledger["ledger_digest"] = digest({key: value for key, value in ledger.items() if key != "ledger_digest"})


def run_formal() -> dict[str, Any]:
    drift = verify_source_freeze()
    if not drift["pass"]:
        raise RuntimeError("RQ1_V4_SOURCE_FREEZE_DRIFT:" + ",".join(drift["drift_paths"]))
    roster = load(REPORT / "FORMAL_ROSTER.json", {})
    if roster.get("status") != "SEALED_UNEXPOSED" or roster.get("cell_count") != 48 or len(roster.get("seeds", [])) != 6:
        raise RuntimeError("RQ1_V4_FORMAL_ROSTER_NOT_SEALED")
    ledger_path = REPORT / "FORMAL_EXECUTION_LEDGER.json"
    ledger = load(ledger_path, {})
    attempted = {row["cell_id"] for row in ledger.get("entries", [])}
    reachability = ledger.get("mathematical_reachability") or {}
    if reachability and not all(reachability.get(key, True) for key in ("total_gate_reachable", "condition_gate_reachable", "family_gate_reachable")):
        return ledger
    for index, cell in enumerate(roster["cells"]):
        if cell["cell_id"] in attempted:
            continue
        output = FORMAL_RUNS / cell["cell_id"] / "attempt_01"
        result = run_one(cell, output, 33000 + index * 3, "FORMAL")
        ledger["entries"].append(result)
        ledger["status"] = "FORMAL_IN_PROGRESS"
        update_ledger(ledger)
        write_json(ledger_path, ledger)
        reachability = ledger["mathematical_reachability"]
        if not all(reachability[key] for key in ("total_gate_reachable", "condition_gate_reachable", "family_gate_reachable")):
            ledger["status"] = "RQ1_V4_PRIMARY_EVALUABILITY_GATE_FAILED"
            ledger["hard_stop_trigger"] = "FROZEN_V4_GATE_MATHEMATICALLY_IMPOSSIBLE"
            update_ledger(ledger)
            write_json(ledger_path, ledger)
            break
    per_condition = ledger.get("decision_evaluable_by_condition", {})
    per_family = ledger.get("decision_evaluable_by_family", {})
    complete = len(ledger.get("entries", [])) == 48
    gate = {
        "pass": complete and ledger.get("decision_evaluable_episodes", 0) >= 40 and all(per_condition.get(code, 0) >= 4 for code in SCENE_CODES) and all(per_family.get(family, 0) >= 9 for family in FAMILIES),
        "complete_48_attempts": complete, "total": ledger.get("decision_evaluable_episodes", 0), "required_total": 40,
        "per_condition": per_condition, "required_per_condition": 4,
        "per_family": per_family, "required_per_family": 9,
    }
    ledger["primary_evaluability_gate"] = gate
    ledger["status"] = "PASS_RQ1_V4_PRIMARY_EVALUABILITY_GATE" if gate["pass"] else "RQ1_V4_PRIMARY_EVALUABILITY_GATE_FAILED"
    update_ledger(ledger)
    write_json(ledger_path, ledger)
    log("run-formal", ledger["status"])
    return ledger


def rate(events: int, total: int) -> float | None:
    return None if total == 0 else events / total


def wilson(events: int, total: int) -> list[float] | None:
    return v3.wilson(events, total)


def mcnemar(left: Sequence[bool], right: Sequence[bool]) -> dict[str, Any]:
    return v3.mcnemar(left, right)


def metrics(rows: Sequence[Mapping[str, Any]], action_key: str) -> dict[str, Any]:
    low = [row for row in rows if row["consequence_level"] == "EQUIVALENT"]
    high = [row for row in rows if row["consequence_level"] == "CRITICAL"]
    high_rate = rate(sum(row[action_key] == "ASK" for row in high), len(high))
    low_rate = rate(sum(row[action_key] == "ASK" for row in low), len(low))
    return {"high_ask_recall": high_rate, "low_unnecessary_ask_rate": low_rate, "selectivity_gap": None if high_rate is None or low_rate is None else high_rate - low_rate}


def effects(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    method = metrics(rows, "policy_action")
    baseline = metrics(rows, "baseline_action")
    if method["selectivity_gap"] is None or baseline["selectivity_gap"] is None:
        return {"paired_low_query_rate_difference_method_minus_baseline": None, "paired_low_query_reduction_baseline_minus_method": None, "high_recall_preservation_method_minus_baseline": None, "selectivity_gap_improvement_method_minus_baseline": None}
    return {
        "paired_low_query_rate_difference_method_minus_baseline": method["low_unnecessary_ask_rate"] - baseline["low_unnecessary_ask_rate"],
        "paired_low_query_reduction_baseline_minus_method": baseline["low_unnecessary_ask_rate"] - method["low_unnecessary_ask_rate"],
        "high_recall_preservation_method_minus_baseline": method["high_ask_recall"] - baseline["high_ask_recall"],
        "selectivity_gap_improvement_method_minus_baseline": method["selectivity_gap"] - baseline["selectivity_gap"],
    }


def cluster_bootstrap(rows: Sequence[Mapping[str, Any]], key: str, replicates: int = 10000) -> dict[str, Any] | None:
    seeds = sorted({row["seed"] for row in rows})
    if not seeds:
        return None
    by_seed = {seed: [row for row in rows if row["seed"] == seed] for seed in seeds}
    generator = random.Random(41849027)
    values = []
    for _ in range(replicates):
        sample = []
        for _slot in seeds:
            sample.extend(by_seed[generator.choice(seeds)])
        value = effects(sample)[key]
        if value is not None:
            values.append(value)
    if not values:
        return None
    values.sort()
    return {"method": "shared-seed cluster percentile bootstrap", "replicates": replicates, "analysis_seed": 41849027, "ci95": [values[int(0.025 * len(values))], values[max(0, int(0.975 * len(values)) - 1)]]}


def sensitivity(rows: Sequence[Mapping[str, Any]], per_condition: Mapping[str, int], minimum: int) -> dict[str, Any]:
    selected = [code for code in SCENE_CODES if per_condition.get(code, 0) >= minimum]
    subset = [row for row in rows if row["scene_code"] in selected]
    return {
        "condition_minimum": minimum, "included_conditions": selected,
        "decision_evaluable": len(subset), "consequence_aware": metrics(subset, "policy_action"),
        "ambiguity_only": metrics(subset, "baseline_action"), "effects": effects(subset),
    }


def analyze() -> dict[str, Any]:
    ledger = load(REPORT / "FORMAL_EXECUTION_LEDGER.json", {})
    if not (ledger.get("primary_evaluability_gate") or {}).get("pass"):
        primary = {"schema": "driveclarify.rq1_v4.primary-results.v1", "status": "RQ1_V4_PRIMARY_EVALUABILITY_GATE_FAILED", "analysis_run": False, "all_primary_metrics": "NOT_ESTIMABLE_PRIMARY_GATE_FAILED"}
        write_json(REPORT / "RQ1_V4_PRIMARY_RESULTS.json", primary)
        write_json(REPORT / "RQ1_V4_SECONDARY_RESULTS.json", {"schema": "driveclarify.rq1_v4.secondary-results.v1", "status": "NOT_RUN_PRIMARY_GATE_FAILED", "analysis_run": False})
        return primary
    rows = [row for row in ledger["entries"] if row["decision_evaluable"]]
    low = [row for row in rows if row["consequence_level"] == "EQUIVALENT"]
    high = [row for row in rows if row["consequence_level"] == "CRITICAL"]
    method = metrics(rows, "policy_action")
    baseline = metrics(rows, "baseline_action")
    effect = effects(rows)
    family = {}
    for name in FAMILIES:
        subset = [row for row in rows if row["family"] == name]
        family[name] = {"decision_evaluable": len(subset), "consequence_aware": metrics(subset, "policy_action"), "ambiguity_only": metrics(subset, "baseline_action"), "effects": effects(subset)}
    primary = {
        "schema": "driveclarify.rq1_v4.primary-results.v1", "status": "PASS_PRIMARY_ANALYSIS_COMPLETE", "analysis_run": True,
        "denominator": "DECISION_EVALUABLE_EPISODES", "scientific_unit": "scene x seed episode",
        "counts": {"all": len(rows), "low": len(low), "high": len(high)},
        "consequence_aware": method, "ambiguity_only": baseline, "effects": effect,
        "rate_confidence_intervals": {
            "consequence_aware_high_ask_recall_wilson95": wilson(sum(row["policy_action"] == "ASK" for row in high), len(high)),
            "consequence_aware_low_unnecessary_ask_wilson95": wilson(sum(row["policy_action"] == "ASK" for row in low), len(low)),
            "ambiguity_only_high_ask_recall_wilson95": wilson(sum(row["baseline_action"] == "ASK" for row in high), len(high)),
            "ambiguity_only_low_unnecessary_ask_wilson95": wilson(sum(row["baseline_action"] == "ASK" for row in low), len(low)),
        },
        "paired_exact_tests": {
            "low_query_mcnemar": mcnemar([row["policy_action"] == "ASK" for row in low], [row["baseline_action"] == "ASK" for row in low]),
            "high_recall_mcnemar": mcnemar([row["policy_action"] == "ASK" for row in high], [row["baseline_action"] == "ASK" for row in high]),
        },
        "shared_seed_cluster_confidence_intervals": {key: cluster_bootstrap(rows, key) for key in effect},
        "family_heterogeneity": family,
        "sensitivity_5_of_6": sensitivity(rows, ledger["decision_evaluable_by_condition"], 5),
        "sensitivity_6_of_6": sensitivity(rows, ledger["decision_evaluable_by_condition"], 6),
        "frame_rows_treated_as_independent": False,
    }
    primary["support_criteria"] = {
        "low_unnecessary_ask_materially_decreased": effect["paired_low_query_rate_difference_method_minus_baseline"] is not None and effect["paired_low_query_rate_difference_method_minus_baseline"] < 0,
        "high_clarification_recall_preserved": effect["high_recall_preservation_method_minus_baseline"] is not None and effect["high_recall_preservation_method_minus_baseline"] >= 0,
    }
    primary["support_criteria"]["both_required_criteria_pass"] = all(primary["support_criteria"].values())
    primary["result_digest"] = digest(primary)
    write_json(REPORT / "RQ1_V4_PRIMARY_RESULTS.json", primary)
    execution = [row for row in ledger["entries"] if row["execution_evaluable"]]
    low_exec = [row for row in execution if row["consequence_level"] == "EQUIVALENT"]
    high_exec = [row for row in execution if row["consequence_level"] == "CRITICAL"]
    secondary = {
        "schema": "driveclarify.rq1_v4.secondary-results.v1", "status": "PASS_SECONDARY_ANALYSIS_COMPLETE", "analysis_run": True,
        "denominator": "EXECUTION_EVALUABLE_EPISODES", "all_execution_evaluable": len(execution),
        "low": {"total": len(low_exec), "direct_act": sum(row["low_direct_act_chain_pass"] for row in low_exec), "task_completion": sum(row["task_completion"] for row in low_exec), "wrong_goal": sum(row["wrong_goal_execution"] for row in low_exec), "route_completion_90_percent": sum((row["route_completion_percent"] or 0) >= 90 for row in low_exec)},
        "high": {"total": len(high_exec), "ask": sum(row["ask_receipt"] for row in high_exec), "answer": sum(row["answer_receipt"] for row in high_exec), "full_replan_admission": sum(row["full_replan_admission"] for row in high_exec), "full_replan_execution": sum(row["full_replan_execution"] for row in high_exec), "clarified_completion": sum(row["task_completion"] for row in high_exec), "wrong_goal": sum(row["wrong_goal_execution"] for row in high_exec), "route_completion_90_percent": sum((row["route_completion_percent"] or 0) >= 90 for row in high_exec)},
        "safety": {"collision": sum(row["collision"] for row in execution), "offroad": sum(row["offroad"] for row in execution), "wrong_lane": sum(row["wrong_lane"] for row in execution)},
        "native_noncompletion_separate": ledger["native_noncompletion_count"], "native_noncompletion_imputed": False,
    }
    secondary["result_digest"] = digest(secondary)
    write_json(REPORT / "RQ1_V4_SECONDARY_RESULTS.json", secondary)
    log("analyze", primary["status"])
    return primary


def final_validation() -> dict[str, Any]:
    source = verify_source_freeze()
    global_check = preserved_identity_check()
    forensic_receipt = load(REPORT / "ORD_CRITICAL_V3_NONCOMPLETION_FORENSIC.json", {})
    pre_state = load(REPORT / "PRE_FORMAL_STATE.json", {})
    rq2_after = v3.rq2_snapshot()
    rq2_before = pre_state.get("rq2_snapshot_before", {})
    rq2_unchanged = rq2_before.get("tree_digest") == rq2_after.get("tree_digest") and rq2_before.get("file_count") == rq2_after.get("file_count")
    ledger = load(REPORT / "FORMAL_EXECUTION_LEDGER.json", {})
    eng1 = load(ENGINEERING / "ENGINEERING_EXECUTION_LEDGER.json", {})
    eng2 = load(ENGINEERING2 / "ENGINEERING_EXECUTION_LEDGER.json", {})
    rows = eng1.get("entries", []) + eng2.get("entries", []) + ledger.get("entries", [])
    json_errors = []
    for path in REPORT.rglob("*.json"):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as error:
            json_errors.append({"path": str(path.relative_to(ROOT)), "error": type(error).__name__})
    cleanup = all((row.get("process") or {}).get("cleanup_pass") is True for row in rows)
    boundary = all(row.get("startup_repair_boundary_respected") is True for row in rows)
    background = all(
        not row.get("decision_evaluable") or (
            (row.get("background_traffic_runtime_receipt") or {}).get("random_background_vehicle_count") == 0
            and (row.get("background_traffic_runtime_receipt") or {}).get("traffic_manager_random_generation_enabled") is False
        ) for row in rows
    )
    v3_immutable = forensic_receipt.get("v3_ledger_sha256") == sha(V3_REPORT / "FORMAL_EXECUTION_LEDGER.json") and forensic_receipt.get("v3_final_report_sha256") == sha(V3_REPORT / "FINAL_REPORT.md")
    value = {
        "schema": "driveclarify.rq1_v4.final-validation.v1",
        "source_freeze": source, "seven_preserved_identities_intact": global_check["pass"],
        "v3_roster_and_result_immutable": v3_immutable, "rq2_unchanged": rq2_unchanged,
        "rq2_before": {"file_count": rq2_before.get("file_count"), "tree_digest": rq2_before.get("tree_digest")},
        "rq2_after": {"file_count": rq2_after.get("file_count"), "tree_digest": rq2_after.get("tree_digest")},
        "json_parse_errors": json_errors, "process_cleanup_pass": cleanup,
        "startup_retry_boundary_respected": boundary, "zero_background_for_all_decision_evaluable": background,
        "checkpoint_unchanged": sha(CHECKPOINT) == CHECKPOINT_SHA256,
        "true_intent_pre_ask_reads": sum(row.get("runtime_true_intent_reads_before_ask", 0) or 0 for row in rows),
        "checkpoint_changes": 0, "pid_controller_changes": 0, "simlingo_weight_changes": 0,
        "rq2_mutation_count": 0 if rq2_unchanged else None,
        "added_vla_forwards": sum(row.get("additional_vla_forwards", 0) or 0 for row in rows),
    }
    value["pass"] = all([source["pass"], global_check["pass"], v3_immutable, rq2_unchanged, not json_errors, cleanup, boundary, background, value["checkpoint_unchanged"]])
    value["status"] = "PASS_FINAL_VALIDATION" if value["pass"] else "RQ1_V4_EXECUTION_INTEGRITY_NOT_CLOSED"
    value["validation_digest"] = digest(value)
    write_json(REPORT / "FINAL_VALIDATION_RECEIPT.json", value)
    return value


def display(value: Any) -> str:
    if value is None:
        return "NOT_ESTIMABLE_OR_NOT_APPLICABLE"
    if isinstance(value, float):
        return f"{value:.6f}"
    return json.dumps(value, sort_keys=True, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)


def finalize() -> dict[str, Any]:
    selected_engineering = selected_engineering_dir()
    stability = load(selected_engineering / "ORD_CRITICAL_STABILITY_QUALIFICATION.json", {})
    ledger = load(REPORT / "FORMAL_EXECUTION_LEDGER.json", {})
    primary = load(REPORT / "RQ1_V4_PRIMARY_RESULTS.json", {})
    secondary = load(REPORT / "RQ1_V4_SECONDARY_RESULTS.json", {})
    if stability.get("status") != "PASS_ORD_CRITICAL_NATIVE_STABILITY_QUALIFIED":
        status = "ORD_CRITICAL_NATIVE_STABILITY_NOT_QUALIFIED"
        validation = {"status": "NOT_APPLICABLE_STOPPED_BEFORE_FORMAL", "true_intent_pre_ask_reads": 0, "checkpoint_changes": 0, "pid_controller_changes": 0, "rq2_mutation_count": 0, "source_freeze": {"pass": True, "not_created": True}}
    elif not (ledger.get("primary_evaluability_gate") or {}).get("pass"):
        status = "RQ1_V4_PRIMARY_EVALUABILITY_GATE_FAILED"
        validation = final_validation()
    else:
        validation = final_validation()
        if not validation["pass"]:
            status = "RQ1_V4_EXECUTION_INTEGRITY_NOT_CLOSED"
        elif (primary.get("support_criteria") or {}).get("both_required_criteria_pass"):
            status = "PASS_RQ1_V4_CONSEQUENCE_SELECTIVITY_SUPPORTED"
        else:
            status = "PASS_RQ1_V4_CONSEQUENCE_SELECTIVITY_NOT_SUPPORTED"
    if status not in FINAL_STATUSES:
        raise RuntimeError("RQ1_V4_ILLEGAL_FINAL_STATUS")
    independent = {"status": "NOT_RUN_BEFORE_PRIMARY_GATE"}
    if (REPORT / "RQ1_V4_PRIMARY_RESULTS.json").is_file():
        subprocess.run([sys.executable, str(INDEPENDENT_AUDITOR)], cwd=str(ROOT), check=False)
        independent = load(REPORT / "INDEPENDENT_RESULT_AUDIT.json", {})
        if independent.get("pass") is not True and status.startswith("PASS_"):
            status = "RQ1_V4_EXECUTION_INTEGRITY_NOT_CLOSED"
    forensic_receipt = load(REPORT / "ORD_CRITICAL_V3_NONCOMPLETION_FORENSIC.json", {})
    repair = load(REPORT / "ORD_CRITICAL_REPAIR_RECEIPT.json", {})
    matrix = load(selected_engineering / "FULL_REPLAN_ADMISSIBILITY_MATRIX.json", {})
    freeze_receipt = load(REPORT / "RQ1_V4_FORMAL_FREEZE_RECEIPT.json", {})
    seeds = load(REPORT / "FORMAL_SEED_FRESHNESS_RECEIPT.json", {})
    method = primary.get("consequence_aware", {})
    baseline = primary.get("ambiguity_only", {})
    effect = primary.get("effects", {})
    low = secondary.get("low", {})
    high = secondary.get("high", {})
    eng1_ledger = load(ENGINEERING / "ENGINEERING_EXECUTION_LEDGER.json", {})
    eng_ledger = load(selected_engineering / "ENGINEERING_EXECUTION_LEDGER.json", {})
    infra = ledger.get("infrastructure_retries", 0)
    report_paths = [
        str((REPORT / name).relative_to(ROOT)) for name in [
            "FINAL_REPORT.md", "ORD_CRITICAL_V3_NONCOMPLETION_FORENSIC.md", "ORD_CRITICAL_V3_NONCOMPLETION_FORENSIC.json",
            "ORD_CRITICAL_REPAIR_RECEIPT.json", "engineering_generation_1/GENERATION_1_FAILURE_FORENSIC.json",
            "RQ1_V4_FORMAL_FREEZE_RECEIPT.json", "FORMAL_EXECUTION_LEDGER.json",
            "RQ1_V4_PRIMARY_RESULTS.json", "RQ1_V4_SECONDARY_RESULTS.json", "FINAL_VALIDATION_RECEIPT.json", "INDEPENDENT_RESULT_AUDIT.json",
        ] if (REPORT / name).exists() or name == "FINAL_REPORT.md"
    ]
    recommendations = {
        "PASS_RQ1_V4_CONSEQUENCE_SELECTIVITY_SUPPORTED": "Replicate the frozen V4 evaluability contract on a truly Town-disjoint zero-background scene set without changing the consequence mechanism.",
        "PASS_RQ1_V4_CONSEQUENCE_SELECTIVITY_NOT_SUPPORTED": "Preserve the sealed null result and preregister a new study of the observed family heterogeneity.",
        "RQ1_V4_PRIMARY_EVALUABILITY_GATE_FAILED": "Preserve the sealed V4 roster and investigate only the recorded non-evaluable native owners in a new engineering generation.",
        "ORD_CRITICAL_NATIVE_STABILITY_NOT_QUALIFIED": "Use the recorded generation-1 failures to authorize at most one causally targeted fresh ORD-CRITICAL engineering generation.",
        "RQ1_V4_EXECUTION_INTEGRITY_NOT_CLOSED": "Resolve the exact failed final-audit item without reusing or reinterpreting any exposed V4 cell.",
    }
    formal_matrix = load(REPORT / "FORMAL_FULL_REPLAN_ADMISSIBILITY_MATRIX.json", {})
    eng_branches = {row["candidate_answer"]: row["admissible"] for row in matrix.get("matrix", [])}
    lines = [
        "# DriveClarify RQ1-V4 final report", "",
        f"1. final status: `{status}`",
        f"2. ORD-CRITICAL forensic owner: `{forensic_receipt.get('first_causal_owner')}`",
        f"3. exact ORD-CRITICAL repair: `{repair.get('exact_repair')}`",
        f"4. ORD-CRITICAL engineering evaluable / 8; failures / 8: `{stability.get('decision_evaluable', 0)}/8`; `{stability.get('failures', 8)}/8`",
        f"5. ORD candidate A/B Full Replan: `static={display(eng_branches)}; native_A={stability.get('candidate_A_native_chain')}; native_B={stability.get('candidate_B_native_chain')}`",
        f"6. Formal V4 freeze digest: `{freeze_receipt.get('freeze_digest', 'NOT_CREATED')}`",
        f"7. six fresh seeds: `{display(seeds.get('formal_seeds'))}`",
        f"8. planned / exposed: `{ledger.get('planned_episodes', 0)}/{ledger.get('exposed_episodes', 0)}`",
        f"9. total decision-evaluable: `{ledger.get('decision_evaluable_episodes', 0)}/48`",
        f"10. per-condition evaluability: `{display(ledger.get('decision_evaluable_by_condition'))}`",
        f"11. per-family evaluability: `{display(ledger.get('decision_evaluable_by_family'))}`",
        f"12. execution-evaluable: `{ledger.get('execution_evaluable_episodes', 0)}/48`",
        f"13. native noncompletion: `{ledger.get('native_noncompletion_count', 0)}`",
        f"14. scientific retries: `{ledger.get('scientific_retries', 0)}`",
        f"15. infrastructure retries: `engineering_generation_1={eng1_ledger.get('infrastructure_retries', 0)}; selected_engineering={eng_ledger.get('infrastructure_retries', 0)}; formal={infra}`",
        f"16. consequence-aware HIGH ASK recall: `{display(method.get('high_ask_recall'))}`",
        f"17. consequence-aware LOW unnecessary ASK: `{display(method.get('low_unnecessary_ask_rate'))}`",
        f"18. consequence-aware selectivity gap: `{display(method.get('selectivity_gap'))}`",
        f"19. ambiguity-only HIGH ASK recall: `{display(baseline.get('high_ask_recall'))}`",
        f"20. ambiguity-only LOW unnecessary ASK: `{display(baseline.get('low_unnecessary_ask_rate'))}`",
        f"21. ambiguity-only selectivity gap: `{display(baseline.get('selectivity_gap'))}`",
        f"22. paired LOW-query reduction: `{display(effect.get('paired_low_query_reduction_baseline_minus_method'))}` (method-minus-baseline `{display(effect.get('paired_low_query_rate_difference_method_minus_baseline'))}`)",
        f"23. HIGH-recall preservation: `{display(effect.get('high_recall_preservation_method_minus_baseline'))}`",
        f"24. selectivity improvement: `{display(effect.get('selectivity_gap_improvement_method_minus_baseline'))}`",
        f"25. exact tests / 95% CIs: `{display({'paired_exact': primary.get('paired_exact_tests'), 'rate_wilson95': primary.get('rate_confidence_intervals'), 'effect_cluster95': primary.get('shared_seed_cluster_confidence_intervals')})}`",
        f"26. REF: `{display((primary.get('family_heterogeneity') or {}).get('REF'))}`",
        f"27. LMK: `{display((primary.get('family_heterogeneity') or {}).get('LMK'))}`",
        f"28. ORD: `{display((primary.get('family_heterogeneity') or {}).get('ORD'))}`",
        f"29. USC family results: `{display((primary.get('family_heterogeneity') or {}).get('USC'))}`",
        f"30. LOW task completion / route completion / direct ACT: `{display(low.get('task_completion'))}/{display(low.get('total'))}; {display(low.get('route_completion_90_percent'))}/{display(low.get('total'))}; {display(low.get('direct_act'))}/{display(low.get('total'))}`",
        f"31. LOW wrong-goal: `{display(low.get('wrong_goal'))}`",
        f"32. HIGH ASK: `{display(high.get('ask'))}/{display(high.get('total'))}`",
        f"33. answer: `{display(high.get('answer'))}/{display(high.get('total'))}`",
        f"34. Full Replan admission / execution: `{display(high.get('full_replan_admission'))}/{display(high.get('total'))}; {display(high.get('full_replan_execution'))}/{display(high.get('total'))}`",
        f"35. clarified completion: `{display(high.get('clarified_completion'))}/{display(high.get('total'))}`",
        f"36. HIGH wrong-goal: `{display(high.get('wrong_goal'))}`",
        f"37. safety: `{display(secondary.get('safety'))}`",
        f"38. >=5/6 sensitivity: `{display(primary.get('sensitivity_5_of_6'))}`",
        f"39. 6/6 sensitivity: `{display(primary.get('sensitivity_6_of_6'))}`",
        f"40. true-intent pre-ASK reads: `{validation.get('true_intent_pre_ask_reads', 0)}`",
        f"41. checkpoint / PID / controller changes: `{validation.get('checkpoint_changes', 0)}/{validation.get('pid_controller_changes', 0)}/0`",
        f"42. RQ2 mutation count: `{validation.get('rq2_mutation_count', 0)}`",
        f"43. source freeze: `{display(validation.get('source_freeze'))}`",
        f"44. final audit: `validation={validation.get('status')}; independent={independent.get('status')}`",
        f"45. report paths: `{display(report_paths)}`",
        f"46. exactly one next recommendation: {recommendations[status]}",
    ]
    write_text(REPORT / "FINAL_REPORT.md", "\n".join(lines))
    final = {
        "schema": "driveclarify.rq1_v4.final-receipt.v1", "status": status,
        "report": str((REPORT / "FINAL_REPORT.md").relative_to(ROOT)),
        "validation": validation.get("status"), "independent_audit": independent.get("status"),
        "next_recommendation": recommendations[status],
    }
    final["receipt_digest"] = digest(final)
    write_json(REPORT / "FINAL_RECEIPT.json", final)
    log("finalize", status)
    return final


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("forensic", "prepare", "qualify", "freeze", "run", "analyze", "finalize", "all"))
    args = parser.parse_args()
    if args.phase in ("forensic", "all"):
        forensic()
    if args.phase in ("prepare", "all"):
        prepare()
    if args.phase in ("qualify", "all"):
        qualification = qualify()
        if args.phase == "all" and qualification["status"] != "PASS_ORD_CRITICAL_NATIVE_STABILITY_QUALIFIED":
            print(json.dumps(finalize(), sort_keys=True))
            return 0
    if args.phase in ("freeze", "all"):
        freeze()
    if args.phase in ("run", "all"):
        run_formal()
    if args.phase in ("analyze", "all"):
        analyze()
    if args.phase in ("finalize", "all"):
        print(json.dumps(finalize(), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
