#!/usr/bin/env python3
"""Run the authorized RQ2-T-CG Formal V2 controlled-background campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_rq2_t.measurement import canonical_sha256
from driveclarify_rq2_t_cg_background_traffic import persist_policy
from driveclarify_rq2_t_cg_formal_freeze.scenes import SCENE_ORDER
from driveclarify_rq2_t_cg_ord_async_redesign.contract import CALIBRATION_FREEZE_DIGEST, build_redesigned_scene
from driveclarify_rq2_t_cg_v2_calibration.scenes import engineering_seam_scene
from tools import run_rq2_t_cg_ord_async_redesign as campaign


REPORT = ROOT / "reports" / "driveclarify_rq2_t_cg_formal_v2_background_traffic_and_execution_v1"
ROUTE_SCENARIO = Path("/home/buaa/wrh/simlingo/Bench2Drive/leaderboard/leaderboard/scenarios/route_scenario.py")


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _configure() -> None:
    campaign.ORD_ADMINISTRATIVE_CAP_SIMULATION_S = 120.0
    campaign.NON_ORD_ADMINISTRATIVE_CAP_SIMULATION_S = 120.0
    campaign.ORD_ENGINEERING_WALL_TIMEOUT_S = 2400.0
    campaign.ENGINEERING_SEAM_WALL_TIMEOUT_S = 2400.0
    campaign.REPORT = REPORT
    campaign.ORD_CONFIG = REPORT / "ORD_ENGINEERING_CONFIG" / "ORD-ASYNC.json"
    campaign.ORD_ROUTE = REPORT / "ORD_ENGINEERING_ROUTE" / "ORD-ASYNC.xml"
    campaign.ORD_RUNS = REPORT / "ORD_ENGINEERING_RUNS"
    campaign.SEAM_CONFIGS = REPORT / "EXECUTION_SEAM_CONFIGS"
    campaign.SEAM_ROUTES = REPORT / "EXECUTION_SEAM_ROUTES"
    campaign.SEAM_RUNS = REPORT / "EXECUTION_SEAM_RUNS"
    campaign.FORMAL_CONFIGS = REPORT / "FORMAL_V2_SCENES"
    campaign.FORMAL_ROUTES = REPORT / "FORMAL_V2_ROUTES"
    campaign.FORMAL_RUNS = REPORT / "FORMAL_V2_RUNS"
    wrapper_path = "tools/run_rq2_t_cg_formal_v2_background_traffic.py"
    if wrapper_path not in campaign.SOURCE_PATHS:
        campaign.SOURCE_PATHS = tuple(campaign.SOURCE_PATHS) + (wrapper_path,)


def _policy_scenes() -> Mapping[str, Mapping[str, Any]]:
    rows = {}
    for index, code in enumerate(SCENE_ORDER, 1):
        if code == "ORD-ASYNC":
            rows[code] = build_redesigned_scene(
                scene_identity="RQ2TCG-V2-POLICY-TEMPLATE-ORD-ASYNC",
                execution_class="RQ2_T_CG_V2_ENGINEERING_SEAM_ONLY",
                formal_denominator_eligible=False,
            )
        else:
            rows[code] = engineering_seam_scene(
                code,
                instance="POLICY-V2-{:02d}".format(index),
                freeze_digest=CALIBRATION_FREEZE_DIGEST,
            )
    return rows


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    campaign._write_json(path, value)


def prepare() -> Mapping[str, Any]:
    _configure()
    gate = campaign.prepare()
    policy = persist_policy(REPORT, _policy_scenes())
    original = subprocess.check_output(
        ["git", "show", "HEAD:Bench2Drive/leaderboard/leaderboard/scenarios/route_scenario.py"],
        cwd="/home/buaa/wrh/simlingo",
    )
    prior_failure = json.loads((ROOT / "reports/driveclarify_rq2_t_cg_v2_ord_async_redesign_and_formal_v1/EXECUTION_SEAM_FAILURE_ADJUDICATION.json").read_text(encoding="utf-8"))
    repair = {
        "schema_version": "driveclarify.rq2_t_cg.background_traffic_repair.v2",
        "status": "PASS_AUTHORIZED_GLOBAL_NONSCIENTIFIC_BACKGROUND_TRAFFIC_REPAIR",
        "policy_id": policy["policy_id"],
        "policy_digest": policy["policy_digest"],
        "scope": list(SCENE_ORDER),
        "prior_collision_actor": "vehicle.lincoln.mkz_2020 id=3722",
        "prior_collision_actor_scientific_role": "NONE",
        "prior_collision_receipt_digest": prior_failure["receipt_digest"],
        "implementation_file": str(ROUTE_SCENARIO),
        "implementation_file_head_sha256": _sha_bytes(original),
        "implementation_file_policy_sha256": campaign._sha(ROUTE_SCENARIO),
        "changed_behavior": [
            "omit RouteScenario BackgroundBehavior for exact policy environment",
            "omit automatic unrelated parked mesh spawning for exact policy environment",
        ],
        "unchanged_behavior": [
            "scenario-owned actor initialization", "ego control", "PID/controller", "checkpoint",
            "native VLA behavior", "candidate geometry", "evidence", "commitment", "deadline",
        ],
        "formal_seed_values_generated": 0,
        "formal_scientific_exposures": 0,
    }
    repair["receipt_digest"] = canonical_sha256(repair)
    _write_json(REPORT / "BACKGROUND_TRAFFIC_REPAIR_RECEIPT.json", repair)
    campaign._append_command("prepare-background-policy-v2", repair["status"])
    return {"gate": gate, "policy": policy, "repair": repair}


def run_all(wall_timeout_seconds: float) -> Mapping[str, Any]:
    _configure()
    if not REPORT.exists():
        prepare()
    ord_result = campaign._load(REPORT / "ORD_ASYNC_ENGINEERING_QUALIFICATION.json", {})
    if ord_result.get("pass") is not True:
        if (
            ord_result.get("pass") is False
            and int(ord_result.get("engineering_attempt_index", 0)) == 1
            and not (REPORT / "ORD_ASYNC_POLICY_ADMINISTRATIVE_CAP_REPAIR_RECEIPT.json").is_file()
        ):
            campaign.repair_ord_policy_administrative_containment()
        elif (
            ord_result.get("pass") is False
            and int(ord_result.get("engineering_attempt_index", 0)) == 2
            and not (REPORT / "ORD_ASYNC_WALL_CONTAINMENT_REPAIR_RECEIPT.json").is_file()
        ):
            campaign.repair_ord_wall_containment()
        ord_result = campaign.qualify_ord()
    if ord_result.get("pass") is not True:
        return campaign.finalize()
    seam = campaign._load(REPORT / "EXECUTION_SEAM_8_OF_8_RECEIPT.json", {})
    if seam.get("status") != "PASS_EXECUTION_SEAM_8_OF_8":
        if (
            seam.get("status") == "EXECUTION_SEAM_8_OF_8_NOT_CLOSED"
            and not (REPORT / "GLOBAL_ADMINISTRATIVE_CONTAINMENT_REPAIR_RECEIPT.json").is_file()
        ):
            campaign.repair_global_administrative_containment()
        seam = campaign.qualify_seams()
    if seam.get("status") != "PASS_EXECUTION_SEAM_8_OF_8":
        return campaign.finalize()
    if not (REPORT / "FORMAL_V2_ROSTER.json").is_file():
        campaign.freeze_and_materialize_formal()
    ledger = campaign.run_formal(wall_timeout_seconds)
    if ledger.get("status") == "PASS_48_OF_48_VALID_COMPLETE_FORMAL_V2":
        if not (REPORT / "FORMAL_V2_HCG_RESULTS.json").is_file():
            campaign.analyze_hcg()
    return campaign.finalize()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=(
        "prepare", "repair-ord-policy-cap", "repair-ord-wall", "repair-global-cap", "qualify-ord", "qualify-seams", "freeze-formal",
        "run-formal", "analyze", "finalize", "run-all",
    ))
    parser.add_argument("--wall-timeout-seconds", type=float, default=2400.0)
    args = parser.parse_args()
    _configure()
    if args.command == "prepare":
        result = prepare(); print(json.dumps(result, sort_keys=True)); return 0
    if args.command == "repair-ord-policy-cap":
        result = campaign.repair_ord_policy_administrative_containment()
        return 0 if result["status"] == "PASS_ORDINARY_POLICY_ADMINISTRATIVE_CONTAINMENT_REPAIR" else 2
    if args.command == "repair-ord-wall":
        result = campaign.repair_ord_wall_containment()
        return 0 if result["status"] == "PASS_ORDINARY_WALL_CONTAINMENT_REPAIR" else 2
    if args.command == "repair-global-cap":
        result = campaign.repair_global_administrative_containment()
        return 0 if result["status"] == "PASS_ORDINARY_GLOBAL_ADMINISTRATIVE_CONTAINMENT_REPAIR" else 2
    if args.command == "qualify-ord":
        result = campaign.qualify_ord(); return 0 if result["pass"] else 2
    if args.command == "qualify-seams":
        result = campaign.qualify_seams(); return 0 if result["status"] == "PASS_EXECUTION_SEAM_8_OF_8" else 2
    if args.command == "freeze-formal":
        campaign.freeze_and_materialize_formal(); return 0
    if args.command == "run-formal":
        result = campaign.run_formal(args.wall_timeout_seconds); return 0 if result["status"] == "PASS_48_OF_48_VALID_COMPLETE_FORMAL_V2" else 2
    if args.command == "analyze":
        campaign.analyze_hcg(); return 0
    if args.command == "finalize":
        result = campaign.finalize(); return 0 if result["status"].startswith("PASS_") else 2
    result = run_all(args.wall_timeout_seconds)
    print(json.dumps({"status": result["status"]}, sort_keys=True))
    return 0 if result["status"].startswith("PASS_") else 2


if __name__ == "__main__":
    raise SystemExit(main())
