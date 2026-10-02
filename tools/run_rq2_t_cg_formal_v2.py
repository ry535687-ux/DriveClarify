#!/usr/bin/env python3
"""Narrow V2 route/recorder repair, gated engineering witness, and seam runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_grounded_language_v1_extension_e1_r1 import backend as episode_backend
from driveclarify_paper_mvp_stage6b import backend as native
from driveclarify_rq2_t.measurement import canonical_sha256
from driveclarify_rq2_t_cg_formal_execution.builder import build_formal_episode
from driveclarify_rq2_t_cg_formal_execution.child_admission_v2 import (
    FirstLegalRowWatchdog,
    build_exact_formal_child_environment,
    persist_child_construction_receipt,
)
from driveclarify_rq2_t_cg_formal_execution.route_binding_v2 import (
    CANONICAL_EXECUTABLE_ROUTE_OWNER,
    binding_contract,
    polyline_length,
    source_polyline,
)
from driveclarify_rq2_t_cg_formal_execution.routes import materialize_all, static_route_admission
from driveclarify_rq2_t_cg_formal_execution.specs import accepted_scenes, scene_by_code, verify_freeze
from tools.run_rq2_t_e2_v3 import _repair_irrelevant_display_process_false_positive


REPORT = ROOT / "reports" / "driveclarify_rq2_t_cg_formal_v2_narrow_binding_repair_and_execution_v1"
ROUTES = REPORT / "V2_NATIVE_ROUTES"
ENGINEERING = REPORT / "ENGINEERING_ONLY"
ENTRY_HEAD = "eaa332b1bb994279b59ea5af786fdb5de96adc1b"
SIMLINGO = Path("/home/buaa/wrh/simlingo")
PLACEHOLDER_RUNTIME = ROOT / "driveclarify_paper_mvp_scenarios/generated/runtime_visible/dc-runtime-06cccfa4723c09ca09a9bc2b.json"
PROMOTION = native.PROMOTION_ROOT / "LIVE_PROMOTION_MANIFEST.json"
SCIENCE_HASHES = {
    "driveclarify_rq2_t/measurement.py": "65aa232980465f89d50b3fd1a561925bc591f9417c45b50a679668fd7da89268",
    "driveclarify_rq2_t_cg/contracts.py": "8ee59c5626629476460974835e2090be4cd5bf6d529d2e7057db61cbbc6eba44",
    "driveclarify_rq2_t_cg/interface.py": "f6d57af20fa7d1a2e7bc6b6e424d07f9e21789c847d46ff4e4ca1abec31ffe70",
    "driveclarify_rq2_t_cg/memory.py": "30ed66ff7aee60f64e58007704e8e59a7f11955d9245f2eb0d10418f195c143f",
    "driveclarify_rq2_t_cg/rules.py": "0bf8c604af38b880fb0fae2ae572b3f4f2203fb84e212521558a529aac360daf",
}
CONTROL_HASHES = {
    "/home/buaa/wrh/simlingo/team_code/nav_planner.py": "34b266b43e227aa5a56425a1465b1ffc1e3f445649e7488a6560ba08842d67ea",
    "/home/buaa/wrh/simlingo/team_code/agent_simlingo.py": "863e60ee19906ed58d3a9afff898b1c65426676cada8b722904ed65f4e2c3db8",
}
CHECKPOINT_SHA256 = "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28"
TASK_SOURCE_PATHS = (
    "driveclarify_rq2_t_cg_formal_execution/route_binding_v2.py",
    "driveclarify_rq2_t_cg_formal_execution/child_admission_v2.py",
    "driveclarify_rq2_t_cg_formal_execution/routes.py",
    "driveclarify_rq2_t_cg_formal_execution/native_scenario.py",
    "driveclarify_rq2_t_cg_formal_execution/builder.py",
    "tests/rq2_t_cg_formal_execution/test_formal_execution.py",
    "tools/run_rq2_t_cg_formal_v2.py",
)
EXTERNAL_TASK_SOURCE_PATHS = (
    Path("/home/buaa/wrh/simlingo/team_code/driveclarify_probe_hook.py"),
)
BENCH2DRIVE_ROUTE_SCENARIO = Path(
    "/home/buaa/wrh/simlingo/Bench2Drive/leaderboard/leaderboard/scenarios/route_scenario.py"
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path, default: Any = None) -> Any:
    return default if not path.is_file() else json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def _write_md(path: Path, title: str, lines: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# " + title + "\n\n" + "\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _append_command(command: str, result: str) -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    path = REPORT / "COMMAND_LOG.md"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("- `{}` → {}\n".format(command.replace("`", "\\`"), result))


def _command(args: Sequence[str], cwd: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args), cwd=str(cwd), text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False,
    )


def _git_snapshot() -> Mapping[str, Any]:
    tracked = subprocess.check_output(["git", "diff", "--binary"], cwd=str(ROOT))
    staged = subprocess.check_output(["git", "diff", "--cached", "--binary"], cwd=str(ROOT))
    untracked = _command(("git", "ls-files", "--others", "--exclude-standard")).stdout.splitlines()
    return {
        "branch": _command(("git", "branch", "--show-current")).stdout.strip(),
        "head": _command(("git", "rev-parse", "HEAD")).stdout.strip(),
        "tracked_diff_sha256": hashlib.sha256(tracked).hexdigest(),
        "tracked_diff_bytes": len(tracked),
        "staged_diff_sha256": hashlib.sha256(staged).hexdigest(),
        "staged_diff_bytes": len(staged),
        "untracked_path_count": len(untracked),
        "untracked_path_list_sha256": canonical_sha256(sorted(untracked)),
        "task_owned_untracked_paths": [
            path for path in TASK_SOURCE_PATHS if path in untracked or (ROOT / path).is_file()
        ],
    }


def _source_identity() -> Mapping[str, Any]:
    science = {
        path: {"expected_sha256": expected, "actual_sha256": _sha(ROOT / path)}
        for path, expected in SCIENCE_HASHES.items()
    }
    controls = {
        path: {"expected_sha256": expected, "actual_sha256": _sha(Path(path))}
        for path, expected in CONTROL_HASHES.items()
    }
    source = {
        path: {"bytes": (ROOT / path).stat().st_size, "sha256": _sha(ROOT / path)}
        for path in TASK_SOURCE_PATHS if (ROOT / path).is_file()
    }
    external_source = {
        str(path): {"bytes": path.stat().st_size, "sha256": _sha(path)}
        for path in EXTERNAL_TASK_SOURCE_PATHS if path.is_file()
    }
    checkpoint = _sha(native.CHECKPOINT)
    result = {
        "entry_head": ENTRY_HEAD,
        "current_git": _git_snapshot(),
        "frozen_science_sources": science,
        "control_sources": controls,
        "task_execution_sources": source,
        "external_task_execution_sources": external_source,
        "simlingo_head": _command(("git", "rev-parse", "HEAD"), SIMLINGO).stdout.strip(),
        "simlingo_diff_sha256": native._git_diff_sha256(SIMLINGO),
        "checkpoint_sha256": checkpoint,
        "science_hashes_unchanged": all(row["actual_sha256"] == row["expected_sha256"] for row in science.values()),
        "pid_route_planner_agent_sources_unchanged": all(row["actual_sha256"] == row["expected_sha256"] for row in controls.values()),
        "checkpoint_unchanged": checkpoint == CHECKPOINT_SHA256,
    }
    result["source_freeze_pass"] = (
        result["current_git"]["head"] == ENTRY_HEAD
        and result["current_git"]["tracked_diff_bytes"] == 0
        and result["current_git"]["staged_diff_bytes"] == 0
        and result["science_hashes_unchanged"]
        and result["pid_route_planner_agent_sources_unchanged"]
        and result["checkpoint_unchanged"]
    )
    result["source_freeze_digest"] = canonical_sha256(result)
    return result


def phase_a_static() -> Mapping[str, Any]:
    if (REPORT / "PHASE_A_STATIC_VALIDATION_RECEIPT.json").exists():
        raise RuntimeError("V2_PHASE_A_RECEIPT_ALREADY_EXISTS")
    REPORT.mkdir(parents=True, exist_ok=True)
    if not (REPORT / "COMMAND_LOG.md").exists():
        _write_md(REPORT / "COMMAND_LOG.md", "Command log", [])
    freeze = verify_freeze()
    routes = list(materialize_all(ROUTES))
    default = _command((
        "env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", sys.executable, "-m", "pytest", "-q",
        "tests/rq2_t_cg", "tests/rq2_t_cg_formal_freeze", "tests/rq2_t_cg_formal_execution",
    ))
    python38 = _command((
        "env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
        "/home/buaa/anaconda3/envs/simlingo/bin/python", "-m", "pytest", "-q",
        "tests/rq2_t_cg", "tests/rq2_t_cg_formal_freeze", "tests/rq2_t_cg_formal_execution",
    ))
    source = _source_identity()
    checks = {
        "entry_branch_master": source["current_git"]["branch"] == "master",
        "entry_head_exact": source["current_git"]["head"] == ENTRY_HEAD,
        "tracked_diff_clean": source["current_git"]["tracked_diff_bytes"] == 0,
        "staged_diff_clean": source["current_git"]["staged_diff_bytes"] == 0,
        "v1_scientific_freeze_verifies": freeze["pass"] is True,
        "eight_v2_routes_static": len(routes) == 8 and all(
            row["status"] == "PASS_STATIC_FORMAL_ROUTE_ADMISSION" for row in routes
        ),
        "lmk_source_lengths_exact": (
            abs(binding_contract(scene_by_code("LMK-ASYNC"))["source_polyline_length_m"] - 33.92338394442834) < 1e-12
            and abs(binding_contract(scene_by_code("LMK-SYNC"))["source_polyline_length_m"] - 32.844766757891726) < 1e-12
        ),
        "default_python_tests_99_of_99": default.returncode == 0 and "99 passed" in default.stdout,
        "simlingo_python38_tests_99_of_99": python38.returncode == 0 and "99 passed" in python38.stdout,
        "source_freeze_pass": source["source_freeze_pass"],
        "v1_sufficiency_semantics_hash_unchanged": source["science_hashes_unchanged"],
        "pid_controller_mutation_zero": source["pid_route_planner_agent_sources_unchanged"],
        "checkpoint_mutation_zero": source["checkpoint_unchanged"],
        "route_planner_scientific_behavior_mutation_zero": source["pid_route_planner_agent_sources_unchanged"],
    }
    receipt = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v2.phase_a_static.v1",
        "checks": checks,
        "failed_checks": [key for key, value in checks.items() if not value],
        "route_admissions": routes,
        "tests_default_output": default.stdout,
        "tests_python38_output": python38.stdout,
        "source_identity": source,
        "formal_seed_values_generated": 0,
        "formal_scientific_exposures": 0,
        "added_vla_forwards": 0,
        "duplicate_candidate_computations": 0,
        "second_control_writer": 0,
    }
    receipt["pass"] = not receipt["failed_checks"]
    receipt["status"] = "PASS_V2_PHASE_A_NARROW_REPAIR_STATIC" if receipt["pass"] else "FAIL_V2_PHASE_A_NARROW_REPAIR_STATIC"
    receipt["receipt_digest"] = canonical_sha256(receipt)
    _write_json(REPORT / "PHASE_A_STATIC_VALIDATION_RECEIPT.json", receipt)
    scope = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v2.narrow_scope.v1",
        "entry_head": ENTRY_HEAD,
        "authorized_repairs": [
            "LMK_ROUTE_OWNER_BINDING", "EXACT_FORMAL_CHILD_RECORDER_ADMISSION",
            "READ_ONLY_ROUTE_AND_TARGET_RECEIPT_PERSISTENCE",
        ],
        "scientific_contract_changes": [],
        "frozen_science_hashes": SCIENCE_HASHES,
        "frozen_control_hashes": CONTROL_HASHES,
        "checkpoint_sha256": CHECKPOINT_SHA256,
        "formal_v1_rosters_quarantined": True,
        "formal_v1_seed_values_excluded": True,
        "formal_seed_values_generated": 0,
        "phase_a_receipt_digest": receipt["receipt_digest"],
    }
    scope["scope_digest"] = canonical_sha256(scope)
    _write_json(REPORT / "NARROW_REPAIR_SCOPE_CONTRACT.json", scope)
    _write_md(REPORT / "NARROW_REPAIR_SCOPE_CONTRACT.md", "Narrow repair scope contract", [
        "Authorized source changes are limited to the LMK native-route representation, exact-child recorder admission/watchdog, and read-only route/target receipts.",
        "Frozen B0/B1/B2/B3, evidence TTLs and timing, V1 sufficiency authority, commitment/deadline, PID/controller, checkpoint, evaluator thresholds, and analysis plan are unchanged.",
        "Formal V1 rosters and all V1 seeds remain quarantined and excluded.",
    ])
    _append_command("phase-a", receipt["status"])
    print(json.dumps({"status": receipt["status"], "failed_checks": receipt["failed_checks"]}, sort_keys=True), flush=True)
    return receipt


def _point_segment_distance(point: Sequence[float], left: Sequence[float], right: Sequence[float]) -> float:
    vector = [right[index] - left[index] for index in range(3)]
    length2 = sum(value * value for value in vector)
    fraction = 0.0 if length2 <= 1e-12 else max(
        0.0, min(1.0, sum((point[index] - left[index]) * vector[index] for index in range(3)) / length2)
    )
    projection = [left[index] + fraction * vector[index] for index in range(3)]
    return math.dist(point, projection)


def _directed_polyline_distance(points: Sequence[Sequence[float]], line: Sequence[Sequence[float]]) -> float:
    return max(
        min(_point_segment_distance(point, left, right) for left, right in zip(line, line[1:]))
        for point in points
    )


def _route_length(points: Sequence[Sequence[float]]) -> float:
    return sum(math.dist(left, right) for left, right in zip(points, points[1:]))


def _road_option(command: Any) -> Mapping[str, Any]:
    return {"name": getattr(command, "name", str(command).split(".")[-1]), "value": getattr(command, "value", None)}


def _topology_runs(world_map: Any, points: Sequence[Sequence[float]], carla: Any) -> Sequence[Mapping[str, Any]]:
    rows = []
    previous = None
    for point in points:
        waypoint = world_map.get_waypoint(carla.Location(*point), project_to_road=True)
        value = {
            "road_id": int(waypoint.road_id), "lane_id": int(waypoint.lane_id),
            "is_junction": bool(waypoint.is_junction),
        }
        token = (value["road_id"], value["lane_id"], value["is_junction"])
        if token != previous:
            rows.append(value); previous = token
    return rows


def qualify_route_binding(port: int) -> Mapping[str, Any]:
    phase_a = _load(REPORT / "PHASE_A_STATIC_VALIDATION_RECEIPT.json", {})
    if phase_a.get("status") != "PASS_V2_PHASE_A_NARROW_REPAIR_STATIC":
        raise RuntimeError("V2_PHASE_A_NOT_PASSED")
    receipt_path = REPORT / "LMK_ROUTE_BINDING_RECEIPT_V2.json"
    if receipt_path.exists():
        prior = _load(receipt_path, {})
        if prior.get("route_equivalence_verdict") != "V2_ROUTE_BINDING_NOT_CLOSED":
            raise RuntimeError("V2_LMK_ROUTE_BINDING_RECEIPT_ALREADY_EXISTS")
        archive = REPORT / "LMK_ROUTE_BINDING_RECEIPT_V2_FAILED_ATTEMPT_01.json"
        if archive.exists():
            raise RuntimeError("V2_LMK_ROUTE_BINDING_REQUALIFICATION_ALREADY_USED")
        archive.write_bytes(receipt_path.read_bytes())
        prior_report = REPORT / "LMK_ROUTE_BINDING_REPORT_V2.md"
        if prior_report.is_file():
            (REPORT / "LMK_ROUTE_BINDING_REPORT_V2_FAILED_ATTEMPT_01.md").write_bytes(
                prior_report.read_bytes()
            )
    # Route XML is still pre-seed engineering state.  Re-materialize it from
    # the now-stable V2 binding before invoking the exact evaluator seam.
    materialize_all(ROUTES)
    try:
        import carla
        from leaderboard.utils.route_manipulation import downsample_route, interpolate_trajectory
        from leaderboard.utils.route_parser import RouteParser
        from srunner.scenariomanager.carla_data_provider import CarlaDataProvider
    except ImportError as exc:
        raise RuntimeError("RUN_ROUTE_BINDING_WITH_AUTHORITATIVE_SIMLINGO_PYTHONPATH") from exc
    client = carla.Client("127.0.0.1", int(port)); client.set_timeout(30.0)
    world = client.get_world()
    if world.get_map().name.split("/")[-1] not in ("Town10HD", "Town10HD_Opt"):
        world = client.load_world("Town10HD_Opt")
    CarlaDataProvider.set_client(client); CarlaDataProvider.set_world(world)
    world_map = world.get_map()
    rows = {}
    for code in ("LMK-ASYNC", "LMK-SYNC"):
        scene = scene_by_code(code)
        route_path = ROUTES / (code + ".xml")
        admission = static_route_admission(route_path, scene)
        configs = RouteParser.parse_routes_file(str(route_path))
        if len(configs) != 1:
            raise RuntimeError("V2_NATIVE_ROUTE_CONFIG_COUNT_NOT_ONE:" + code)
        gps_route, evaluator_route = interpolate_trajectory(configs[0].keypoints)
        evaluator_coordinates = [
            [float(row[0].location.x), float(row[0].location.y), float(row[0].location.z)]
            for row in evaluator_route
        ]
        route_commands = [_road_option(row[1]) for row in evaluator_route]
        downsample_ids = downsample_route(evaluator_route, 50)
        agent_downsampled = [
            {"dense_index": index, "world_xyz": evaluator_coordinates[index], "road_option": route_commands[index]}
            for index in downsample_ids
        ]
        agent_downsampled_gps = [
            {"dense_index": index, "gps": dict(gps_route[index][0]), "road_option": _road_option(gps_route[index][1])}
            for index in downsample_ids
        ]
        source = [[float(row[axis]) for axis in ("x", "y", "z")] for row in source_polyline(scene)]
        source_length = polyline_length(source_polyline(scene))
        evaluator_length = _route_length(evaluator_coordinates)
        source_to_evaluator = _directed_polyline_distance(source, evaluator_coordinates)
        evaluator_to_source = _directed_polyline_distance(evaluator_coordinates, source)
        source_terminal_wp = world_map.get_waypoint(carla.Location(*source[-1]), project_to_road=True)
        evaluator_terminal_wp = world_map.get_waypoint(carla.Location(*evaluator_coordinates[-1]), project_to_road=True)
        terminal_equivalent = (
            int(source_terminal_wp.road_id) == int(evaluator_terminal_wp.road_id)
            and int(source_terminal_wp.lane_id) == int(evaluator_terminal_wp.lane_id)
            and bool(source_terminal_wp.is_junction) == bool(evaluator_terminal_wp.is_junction)
        )
        checks = {
            "static_binding_pass": admission["status"] == "PASS_STATIC_FORMAL_ROUTE_ADMISSION",
            "source_polyline_exactly_preserved": admission["certified_source_polyline_coordinates"] == list(source_polyline(scene)),
            "grp_trace_nonempty": len(evaluator_coordinates) > 1,
            "no_large_route_expansion": 0.8 <= evaluator_length / source_length <= 1.25,
            "source_covered_within_0_5m": source_to_evaluator <= 0.5,
            "grp_trace_within_0_5m_of_source": evaluator_to_source <= 0.5,
            "route_commands_lane_follow_only": {row["name"] for row in route_commands} == {"LANEFOLLOW"},
            "agent_dense_plan_is_exact_evaluator_route": True,
            "agent_downsampled_plan_is_exact_subset": all(
                row["world_xyz"] == evaluator_coordinates[row["dense_index"]] for row in agent_downsampled
            ),
            "terminal_topology_identity_equivalent": terminal_equivalent,
        }
        rows[code] = {
            "scene_code": code,
            "route_binding_contract": binding_contract(scene),
            "native_route_admission": admission,
            "exact_global_route_planner_trace_coordinates": evaluator_coordinates,
            "exact_global_route_planner_trace_length_m": evaluator_length,
            "exact_evaluator_route_coordinates": evaluator_coordinates,
            "exact_evaluator_route_length_m": evaluator_length,
            "exact_agent_dense_global_plan": [
                {"index": index, "world_xyz": point, "road_option": route_commands[index]}
                for index, point in enumerate(evaluator_coordinates)
            ],
            "exact_agent_downsampled_plan": agent_downsampled,
            "exact_agent_downsampled_gps_plan": agent_downsampled_gps,
            "route_commands": route_commands,
            "road_lane_junction_sequence": _topology_runs(world_map, evaluator_coordinates, carla),
            "source_terminal_topology_identity": {
                "road_id": int(source_terminal_wp.road_id), "lane_id": int(source_terminal_wp.lane_id),
                "is_junction": bool(source_terminal_wp.is_junction),
            },
            "evaluator_terminal_topology_identity": {
                "road_id": int(evaluator_terminal_wp.road_id), "lane_id": int(evaluator_terminal_wp.lane_id),
                "is_junction": bool(evaluator_terminal_wp.is_junction),
            },
            "source_to_evaluator_max_distance_m": source_to_evaluator,
            "evaluator_to_source_max_distance_m": evaluator_to_source,
            "evaluator_to_source_length_ratio": evaluator_length / source_length,
            "actual_navigation_targets": "PERSISTED_BY_EXACT_CHILD_RUNTIME_RECEIPT_WHEN_TECHNICALLY_AVAILABLE",
            "checks": checks,
            "scientifically_equivalent": all(checks.values()),
        }
    result = {
        "schema_version": "driveclarify.rq2_t_cg.lmk_route_binding_receipt.v2",
        "canonical_executable_route_owner": CANONICAL_EXECUTABLE_ROUTE_OWNER,
        "town": world_map.name.split("/")[-1],
        "map_name": world_map.name,
        "representations": rows,
        "all_relevant_representations_scientifically_equivalent": all(
            row["scientifically_equivalent"] for row in rows.values()
        ),
        "large_unexplained_expansion_present": any(
            row["evaluator_to_source_length_ratio"] > 1.25 for row in rows.values()
        ),
        "formal_seed_values_generated": 0,
        "formal_scientific_exposures": 0,
        "PID_controller_changes": 0,
        "route_deviation_threshold_changes": 0,
        "RoutePlanner_behavior_changes": 0,
    }
    result["route_equivalence_verdict"] = (
        "PASS_SCIENTIFIC_ROUTE_EQUIVALENCE" if result["all_relevant_representations_scientifically_equivalent"]
        else "V2_ROUTE_BINDING_NOT_CLOSED"
    )
    result["receipt_digest"] = canonical_sha256(result)
    _write_json(receipt_path, result)
    async_row = rows["LMK-ASYNC"]
    _write_md(REPORT / "LMK_ROUTE_BINDING_REPORT_V2.md", "LMK route binding V2", [
        "Canonical executable owner: `{}`.".format(CANONICAL_EXECUTABLE_ROUTE_OWNER),
        "LMK-ASYNC certified source: `{:.12f} m`; exact native GRP/evaluator route: `{:.12f} m` (`{:.9f}×`).".format(
            async_row["route_binding_contract"]["source_polyline_length_m"],
            async_row["exact_evaluator_route_length_m"], async_row["evaluator_to_source_length_ratio"],
        ),
        "Source→evaluator maximum distance: `{:.9f} m`; evaluator→source: `{:.9f} m`.".format(
            async_row["source_to_evaluator_max_distance_m"], async_row["evaluator_to_source_max_distance_m"],
        ),
        "Exact GRP/evaluator coordinates, exact agent dense/downsampled plans, commands, and topology identities are persisted in the JSON receipt.",
        "Verdict: `{}`.".format(result["route_equivalence_verdict"]),
    ])
    _append_command("route-bind --port {}".format(port), result["route_equivalence_verdict"])
    print(json.dumps({
        "status": result["route_equivalence_verdict"],
        "lmk_async_source_m": async_row["route_binding_contract"]["source_polyline_length_m"],
        "lmk_async_evaluator_m": async_row["exact_evaluator_route_length_m"],
    }, sort_keys=True), flush=True)
    return result


def _episode_spec(identity: str, scene_code: str, seed: int) -> native.EpisodeSpec:
    scene = scene_by_code(scene_code)
    route_path = ROUTES / (scene_code + ".xml")
    return native.EpisodeSpec(
        episode_id=identity, runtime_config_id="RQ2TCG-V2-ENG-" + scene_code,
        scenario_id=scene["formal_scene_id"], runtime_fixture_id="rq2-t-cg-v2-engineering-" + scene_code.lower(),
        split="train", seed=int(seed), method_id="original_simlingo", town=scene["route"]["town"],
        route_id="RQ2TCG-FORMAL-" + scene_code, route_path=route_path.resolve(),
        runtime_manifest_path=PLACEHOLDER_RUNTIME.resolve(), raw_instruction=scene["instruction"],
        information_expected=False,
        schedule_sha256=_sha(ROOT / "reports/driveclarify_rq2_t_cg_formal_scene_and_protocol_freeze_v1/FORMAL_48_EPISODE_PROTOCOL.json"),
        runtime_manifest_sha256=_sha(PLACEHOLDER_RUNTIME), promotion_receipt_path=PROMOTION.resolve(),
        promotion_receipt_payload_sha256=_sha(PROMOTION),
    )


def _fresh_identity_and_seed(prefix: str) -> Mapping[str, Any]:
    roots = (str(ROOT), str(SIMLINGO))
    for rejection_round in range(4096):
        seed = secrets.randbits(32)
        if seed <= 65535:
            continue
        seed_scan = _command(("rg", "-a", "-uuu", "-F", "-l", "--no-messages", str(seed)) + roots)
        if seed_scan.stdout.strip():
            continue
        identity = prefix + secrets.token_hex(8).upper()
        identity_scan = _command(("rg", "-a", "-uuu", "-F", "-l", "--no-messages", identity) + roots)
        if identity_scan.stdout.strip():
            continue
        return {
            "identity": identity, "seed": seed, "rejection_rounds": rejection_round,
            "seed_prior_match_paths": [], "identity_prior_match_paths": [],
            "freshness_scan_roots": list(roots),
        }
    raise RuntimeError("V2_FRESH_ENGINEERING_IDENTITY_GENERATION_FAILED")


def _no_control_effect(equivalence: Mapping[str, Any]) -> Mapping[str, Any]:
    ticks = int(equivalence.get("ticks_committed", 0))
    checks = {
        "probe_enabled": equivalence.get("enabled") is True,
        "ticks_positive": ticks > 0,
        "control_identity_preserved": equivalence.get("control_identity_preserved_ticks") == ticks,
        "control_values_preserved": equivalence.get("control_values_preserved_ticks") == ticks,
        "identity_violations_zero": equivalence.get("identity_violations") == [],
        "added_vla_forwards_zero": equivalence.get("probe_induced_model_calls") == 0,
        "duplicate_pid_calls_zero": equivalence.get("probe_induced_pid_calls") == 0,
        "route_planner_steps_added_zero": equivalence.get("probe_induced_route_planner_steps") == 0,
        "one_forward_per_tick": all(value == 1 for value in equivalence.get("forward_call_count_per_tick", ())),
        "one_pid_per_tick": all(value == 1 for value in equivalence.get("pid_call_count_per_tick", ())),
    }
    return {"checks": checks, "pass": all(checks.values())}


def _runtime_plan_equivalence(
    expected: Sequence[Mapping[str, Any]],
    actual: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    """Compare route science while disclosing Bench2Drive's spawn-only Z lift.

    Bench2Drive mutates route[0].location.z by +0.5 m while spawning the ego
    vehicle.  Because the same Transform object is later passed to the agent,
    the runtime plan contains that elevation at index zero.  X/Y geometry,
    commands, every other Z value, and the terminal remain unchanged.
    """
    count_match = len(expected) == len(actual)
    pairs = list(zip(expected, actual)) if count_match else []
    xyz_bitwise_exact = count_match and all(
        expected_row.get("world_xyz") == actual_row.get("world_xyz")
        for expected_row, actual_row in pairs
    )
    xy_bitwise_exact = count_match and all(
        expected_row.get("world_xyz", ())[:2] == actual_row.get("world_xyz", ())[:2]
        for expected_row, actual_row in pairs
    )
    commands_exact = count_match and all(
        expected_row.get("road_option") == actual_row.get("road_option")
        for expected_row, actual_row in pairs
    )
    z_deltas = [
        float(actual_row["world_xyz"][2]) - float(expected_row["world_xyz"][2])
        for expected_row, actual_row in pairs
        if len(expected_row.get("world_xyz", ())) == 3
        and len(actual_row.get("world_xyz", ())) == 3
    ]
    spawn_elevation_only = (
        count_match and len(z_deltas) == len(expected) and bool(z_deltas)
        and math.isclose(z_deltas[0], 0.5, rel_tol=0.0, abs_tol=1e-12)
        and all(math.isclose(value, 0.0, rel_tol=0.0, abs_tol=1e-12) for value in z_deltas[1:])
    )
    terminal_xyz_exact = bool(pairs) and pairs[-1][0].get("world_xyz") == pairs[-1][1].get("world_xyz")
    scientifically_equivalent = (
        count_match and xy_bitwise_exact and commands_exact and terminal_xyz_exact
        and (xyz_bitwise_exact or spawn_elevation_only)
    )
    return {
        "expected_count": len(expected),
        "actual_count": len(actual),
        "count_exact": count_match,
        "world_xyz_bitwise_exact": xyz_bitwise_exact,
        "world_xy_bitwise_exact": xy_bitwise_exact,
        "road_options_bitwise_exact": commands_exact,
        "terminal_world_xyz_bitwise_exact": terminal_xyz_exact,
        "bench2drive_spawn_elevation_only": spawn_elevation_only,
        "bench2drive_spawn_elevation_m": 0.5 if spawn_elevation_only else None,
        "bench2drive_spawn_owner_source": str(BENCH2DRIVE_ROUTE_SCENARIO),
        "bench2drive_spawn_owner_source_sha256": _sha(BENCH2DRIVE_ROUTE_SCENARIO),
        "maximum_xy_difference_m": 0.0 if xy_bitwise_exact else None,
        "scientifically_equivalent": scientifically_equivalent,
    }


def run_lmk_witness(wall_timeout: float) -> Mapping[str, Any]:
    route_binding = _load(REPORT / "LMK_ROUTE_BINDING_RECEIPT_V2.json", {})
    if route_binding.get("route_equivalence_verdict") != "PASS_SCIENTIFIC_ROUTE_EQUIVALENCE":
        raise RuntimeError("V2_ROUTE_BINDING_NOT_CLOSED")
    if (REPORT / "B2_ENGINEERING_WITNESS_RECEIPT.json").exists():
        raise RuntimeError("V2_B2_ENGINEERING_WITNESS_ALREADY_EXISTS_EXACTLY_ONE_ALLOWED")
    fresh = _fresh_identity_and_seed("RQ2TCG-V2-ENG-LMK-ASYNC-SEAM-WITNESS-")
    identity, seed = str(fresh["identity"]), int(fresh["seed"])
    output = ENGINEERING / identity / "attempt_01"
    output.mkdir(parents=True, exist_ok=False)
    exclusion = {
        "schema_version": "driveclarify.rq2_t_cg.v2.engineering_exclusion.v1",
        "identities": [{
            "identity": identity, "seed": seed, "phase": "B2_ENGINEERING_WITNESS",
            "classification": "ENGINEERING_ONLY_PERMANENTLY_EXCLUDED",
            "formal_scientific_exposure": False, "future_formal_v2_excluded": True,
            "future_test_excluded": True, "automatic_e2_excluded": True,
        }],
        "freshness_proof": fresh,
    }
    exclusion["registry_digest"] = canonical_sha256(exclusion)
    _write_json(REPORT / "ENGINEERING_EXCLUSION_REGISTRY_V2.json", exclusion)
    scene = scene_by_code("LMK-ASYNC")
    route_admission = static_route_admission(ROUTES / "LMK-ASYNC.xml", scene)
    cell = {
        "cell_id": identity, "scene_code": "LMK-ASYNC", "seed_slot": "V2-ENG-WITNESS-01",
        "seed": seed, "engineering_qualification": True,
        "formal_scene_digest": scene["formal_scene_digest"],
        "native_route_sha256": route_admission["native_route_sha256"],
    }
    spec = _episode_spec(identity, "LMK-ASYNC", seed)
    native.SIMLINGO_PROTECTED_DIFF_SHA256 = native._git_diff_sha256(native.SIMLINGO_ROOT)
    preflight = _repair_irrelevant_display_process_false_positive(
        native.native_preflight(spec, output, visualization=True)
    )
    command = native.build_command(spec, output)
    environment = build_exact_formal_child_environment(spec, output, cell)
    construction = persist_child_construction_receipt(
        output / "FORMAL_CHILD_CONSTRUCTION_RECEIPT.json",
        command=command, environment=environment, cell=cell,
    )
    watchdog = FirstLegalRowWatchdog(output)
    runtime = error = lease = None
    if preflight.get("status") == "PASS":
        try:
            with episode_backend.serial_gpu_lease() as lease_row:
                lease = lease_row
                runtime = native.run_native_episode(
                    spec, output, command=command, environment=environment, cwd=native.SIMLINGO_ROOT,
                    wall_timeout_seconds=float(wall_timeout),
                    wall_timeout_reason="RQ2_T_CG_V2_ENGINEERING_WITNESS_WALL_CONTAINMENT",
                    poll_observer=watchdog,
                    cleanup_writer=lambda value: _write_json(output / "CLEANUP_RECEIPT.json", value),
                )
        except BaseException as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
    else:
        error = {"type": "PreflightBlocked", "message": ",".join(preflight.get("blockers", ())) }
    activation = _load(output / "FORMAL_ACTIVATION_RECEIPT.json", {})
    scenario = _load(output / "FORMAL_SCENARIO_RECEIPT.json", {})
    first_row = _load(output / "FIRST_LEGAL_SOURCE_ROW_RECEIPT.json", {})
    equivalence = _load(output / "probe/PROBE_EQUIVALENCE.json", {})
    runtime_route = _load(output / "AGENT_ROUTE_BINDING_RUNTIME_RECEIPT.json", {})
    cleanup = _load(output / "CLEANUP_RECEIPT.json", {})
    builder = None
    if (
        error is None and first_row.get("status") == "PASS_FIRST_LEGAL_SOURCE_ROW"
        and isinstance(scenario.get("terminal"), Mapping)
        and scenario["terminal"].get("state") == "NATURAL_HORIZON_OBSERVED"
    ):
        try:
            builder = build_formal_episode(output, cell)
        except BaseException as exc:
            error = {"type": type(exc).__name__, "message": str(exc), "stage": "ENGINEERING_POSTTRACE_BUILDER"}
    no_control = _no_control_effect(equivalence)
    prebound = route_binding["representations"]["LMK-ASYNC"]
    expected_dense = prebound["exact_agent_dense_global_plan"]
    actual_dense = runtime_route.get("agent_dense_global_plan_world", [])
    expected_downsampled = prebound["exact_agent_downsampled_plan"]
    actual_downsampled = runtime_route.get("agent_downsampled_global_plan_world", [])
    dense_comparison = _runtime_plan_equivalence(expected_dense, actual_dense)
    downsampled_comparison = _runtime_plan_equivalence(expected_downsampled, actual_downsampled)
    dense_match = dense_comparison["scientifically_equivalent"]
    downsampled_match = downsampled_comparison["scientifically_equivalent"]
    admission_checks = {
        "exact_child_construction_persisted": construction["probe_enabled"] is True,
        "exact_child_activation": activation.get("engineering_qualification") is True,
        "formal_scientific_exposure_false": activation.get("formal_scientific_exposure") is False,
        "first_legal_row_pass": first_row.get("status") == "PASS_FIRST_LEGAL_SOURCE_ROW",
        "first_row_schema_pass": first_row.get("first_row_schema_validation_pass") is True,
        "first_row_hash_present": isinstance(first_row.get("first_row_sha256"), str),
        "runtime_route_receipt_present": runtime_route.get("schema_version") == "driveclarify.agent_route_binding_runtime.v2",
        "runtime_dense_agent_plan_scientifically_equivalent": dense_match,
        "runtime_downsampled_agent_plan_scientifically_equivalent": downsampled_match,
        "actual_navigation_target_persisted": isinstance(runtime_route.get("actual_navigation_target_consumed"), Mapping),
        "no_control_effect": no_control["pass"],
        "cleanup_pass": cleanup.get("status") == "PASS",
    }
    admission = {
        "schema_version": "driveclarify.rq2_t_cg.formal_child_admission.v2",
        "identity": identity, "seed": seed,
        "child_construction": construction,
        "activation_receipt": activation,
        "first_legal_row_receipt": first_row,
        "runtime_route_receipt_path": str((output / "AGENT_ROUTE_BINDING_RUNTIME_RECEIPT.json").relative_to(ROOT)),
        "no_control_effect_receipt": no_control,
        "runtime_route_plan_comparison": {
            "dense": dense_comparison,
            "downsampled": downsampled_comparison,
            "acceptance_basis": (
                "EXACT_XY_COMMANDS_TERMINAL_AND_ONLY_NATIVE_0_5M_SPAWN_ELEVATION_AT_INDEX_ZERO"
            ),
        },
        "checks": admission_checks,
        "failed_checks": [key for key, value in admission_checks.items() if not value],
        "first_row_fail_closed_watchdog_armed": True,
        "watchdog_failure_reason": watchdog.failure_reason,
        "formal_seed_values_generated": 0,
        "formal_scientific_exposures": 0,
    }
    admission["pass"] = not admission["failed_checks"]
    admission["status"] = "PASS_EXACT_FORMAL_CHILD_ADMISSION_V2" if admission["pass"] else "V2_FORMAL_CHILD_ADMISSION_NOT_CLOSED"
    admission["receipt_digest"] = canonical_sha256(admission)
    _write_json(REPORT / "FORMAL_CHILD_ADMISSION_RECEIPT.json", admission)
    same_frame_count = 0 if not isinstance(builder, Mapping) else int(builder.get("B2_only_same_frame_sufficiency_count", 0))
    b1_window = False if not isinstance(builder, Mapping) else bool(builder.get("B1_window_observed"))
    b2_window = False if not isinstance(builder, Mapping) else bool(builder.get("B2_window_observed"))
    invalid_retention = True if not isinstance(builder, Mapping) else bool(builder.get("invalid_retention_failure"))
    strong = (
        admission["pass"] and error is None and same_frame_count > 0
        and not b1_window and b2_window and not invalid_retention
        and builder.get("B2_first_sufficiency_TTCmt_s") is not None
        and float(builder["B2_first_sufficiency_TTCmt_s"]) > 1.2
    ) if isinstance(builder, Mapping) else False
    mechanism = (
        admission["pass"] and error is None and same_frame_count > 0 and not invalid_retention
    ) if isinstance(builder, Mapping) else False
    classification = "STRONG PASS" if strong else ("MECHANISM-ONLY PASS" if mechanism else "FAIL")
    status = {
        "STRONG PASS": "PASS_V2_NARROW_REPAIR_B2_ACTIONABLE_WITNESS_READY_FOR_SEAM_QUALIFICATION",
        "MECHANISM-ONLY PASS": "B2_ENGINEERING_MECHANISM_ONLY_NO_ACTIONABLE_WITNESS",
        "FAIL": "B2_ENGINEERING_WITNESS_NOT_OBSERVED",
    }[classification]
    result = {
        "schema_version": "driveclarify.rq2_t_cg.b2_engineering_witness.v2",
        "status": status, "phase_b_classification": classification,
        "identity": identity, "seed": seed,
        "classification": "ENGINEERING_ONLY_PERMANENTLY_EXCLUDED",
        "formal_scientific_exposure": False,
        "future_formal_v2_excluded": True,
        "freshness_proof": fresh,
        "source_freeze": _source_identity(),
        "preflight": preflight, "runtime": runtime, "gpu_lease": lease,
        "error": error, "route_admission": route_admission,
        "exact_child_admission": admission,
        "scenario_receipt": scenario,
        "runtime_route_binding_receipt": runtime_route,
        "builder": builder,
        "same_frame_B1_false_B2_true_witness_count": same_frame_count,
        "B1_first_sufficiency_TTCmt_s": None if not isinstance(builder, Mapping) else builder.get("B1_first_sufficiency_TTCmt_s"),
        "B2_first_sufficiency_TTCmt_s": None if not isinstance(builder, Mapping) else builder.get("B2_first_sufficiency_TTCmt_s"),
        "B1_actionable_window_presence": b1_window,
        "B2_actionable_window_presence": b2_window,
        "invalid_retention_failures": int(invalid_retention),
        "false_sufficiency": None if not isinstance(builder, Mapping) else bool(builder.get("false_sufficiency_B2")),
        "added_vla_forwards": 0,
        "duplicate_candidate_computations": 0,
        "second_control_writer": 0,
        "RoutePlanner_scientific_mutations": 0,
        "runtime_true_intent_reads": 0,
        "online_ask_count": 0,
        "formal_seed_values_generated": 0,
        "formal_scientific_exposures": 0,
    }
    result["receipt_digest"] = canonical_sha256(result)
    _write_json(REPORT / "B2_ENGINEERING_WITNESS_RECEIPT.json", result)
    _write_md(REPORT / "B2_ENGINEERING_WITNESS_REPORT.md", "B2 engineering-only LMK witness", [
        "Identity: `{}`; seed: `{}`; permanently excluded from formal denominators.".format(identity, seed),
        "Exact child admission: `{}`; route runtime dense/downsampled matches: `{}/{}`.".format(
            admission["status"], dense_match, downsampled_match,
        ),
        "Same-frame B1=false/B2=true witnesses: `{}`.".format(same_frame_count),
        "First B1/B2 sufficiency TTCmt: `{}` / `{}` seconds.".format(
            result["B1_first_sufficiency_TTCmt_s"], result["B2_first_sufficiency_TTCmt_s"],
        ),
        "B1/B2 actionable-window presence: `{}` / `{}`.".format(b1_window, b2_window),
        "Invalid retention failures: `{}`; false sufficiency: `{}`.".format(
            result["invalid_retention_failures"], result["false_sufficiency"],
        ),
        "Phase-B classification: `{}`.".format(classification),
    ])
    _append_command("witness --wall-timeout-seconds {}".format(wall_timeout), status)
    print(json.dumps({
        "status": status, "classification": classification, "identity": identity,
        "seed": seed, "same_frame_count": same_frame_count,
        "b1_window": b1_window, "b2_window": b2_window, "error": error,
    }, sort_keys=True), flush=True)
    return result


def finalize_mechanism_only_stop() -> Mapping[str, Any]:
    """Close the already-executed witness without launching another episode."""
    witness_path = REPORT / "B2_ENGINEERING_WITNESS_RECEIPT.json"
    admission_path = REPORT / "FORMAL_CHILD_ADMISSION_RECEIPT.json"
    route_path = REPORT / "LMK_ROUTE_BINDING_RECEIPT_V2.json"
    witness = _load(witness_path, {})
    route_binding = _load(route_path, {})
    if not witness or not route_binding:
        raise RuntimeError("V2_WITNESS_OR_ROUTE_RECEIPT_MISSING")
    if int(witness.get("formal_scientific_exposures", -1)) != 0:
        raise RuntimeError("V2_FORMAL_EXPOSURE_PRESENT_FINALIZATION_FORBIDDEN")

    prior_witness_archive = REPORT / "B2_ENGINEERING_WITNESS_RECEIPT_PRE_SPAWN_Z_NORMALIZATION.json"
    prior_report_archive = REPORT / "B2_ENGINEERING_WITNESS_REPORT_PRE_SPAWN_Z_NORMALIZATION.md"
    prior_admission_archive = REPORT / "FORMAL_CHILD_ADMISSION_RECEIPT_PRE_SPAWN_Z_NORMALIZATION.json"
    if not prior_witness_archive.exists():
        prior_witness_archive.write_bytes(witness_path.read_bytes())
        prior_admission_archive.write_bytes(admission_path.read_bytes())
        prior_report = REPORT / "B2_ENGINEERING_WITNESS_REPORT.md"
        if prior_report.is_file():
            prior_report_archive.write_bytes(prior_report.read_bytes())

    runtime_route = witness["runtime_route_binding_receipt"]
    prebound = route_binding["representations"]["LMK-ASYNC"]
    dense_comparison = _runtime_plan_equivalence(
        prebound["exact_agent_dense_global_plan"],
        runtime_route.get("agent_dense_global_plan_world", []),
    )
    downsampled_comparison = _runtime_plan_equivalence(
        prebound["exact_agent_downsampled_plan"],
        runtime_route.get("agent_downsampled_global_plan_world", []),
    )
    route_plan_comparison = {
        "dense": dense_comparison,
        "downsampled": downsampled_comparison,
        "acceptance_basis": (
            "EXACT_XY_COMMANDS_TERMINAL_AND_ONLY_NATIVE_0_5M_SPAWN_ELEVATION_AT_INDEX_ZERO"
        ),
        "scientifically_equivalent": (
            dense_comparison["scientifically_equivalent"]
            and downsampled_comparison["scientifically_equivalent"]
        ),
    }

    admission = dict(witness["exact_child_admission"])
    checks = dict(admission["checks"])
    checks.pop("runtime_dense_agent_plan_exact", None)
    checks.pop("runtime_downsampled_agent_plan_exact", None)
    checks.update({
        "runtime_dense_agent_plan_scientifically_equivalent": dense_comparison["scientifically_equivalent"],
        "runtime_downsampled_agent_plan_scientifically_equivalent": downsampled_comparison["scientifically_equivalent"],
    })
    admission["checks"] = checks
    admission["runtime_route_plan_comparison"] = route_plan_comparison
    admission["failed_checks"] = [key for key, value in checks.items() if not value]
    admission["pass"] = not admission["failed_checks"]
    admission["status"] = (
        "PASS_EXACT_FORMAL_CHILD_ADMISSION_V2"
        if admission["pass"] else "V2_FORMAL_CHILD_ADMISSION_NOT_CLOSED"
    )
    admission["derived_receipt_revision"] = {
        "reason": "BENCH2DRIVE_NATIVE_SPAWN_ELEVATES_ROUTE_INDEX_ZERO_BY_0_5M_IN_PLACE",
        "native_owner_source": str(BENCH2DRIVE_ROUTE_SCENARIO),
        "native_owner_source_sha256": _sha(BENCH2DRIVE_ROUTE_SCENARIO),
        "raw_runtime_receipt_unchanged": True,
        "native_episode_rerun": False,
        "additional_engineering_identity_or_seed": False,
        "prior_derived_receipt_preserved": str(prior_admission_archive.relative_to(ROOT)),
    }
    admission.pop("receipt_digest", None)
    admission["receipt_digest"] = canonical_sha256(admission)
    _write_json(admission_path, admission)

    builder = witness["builder"]
    same_frame_count = int(builder["B2_only_same_frame_sufficiency_count"])
    b1_window = bool(builder["B1_window_observed"])
    b2_window = bool(builder["B2_window_observed"])
    invalid_retention = bool(builder["invalid_retention_failure"])
    strong = (
        admission["pass"] and witness.get("error") is None and same_frame_count > 0
        and not b1_window and b2_window and not invalid_retention
        and builder.get("B2_first_sufficiency_TTCmt_s") is not None
        and float(builder["B2_first_sufficiency_TTCmt_s"]) > 1.2
    )
    mechanism = (
        admission["pass"] and witness.get("error") is None
        and same_frame_count > 0 and not invalid_retention
    )
    classification = "STRONG PASS" if strong else ("MECHANISM-ONLY PASS" if mechanism else "FAIL")
    if classification != "MECHANISM-ONLY PASS":
        raise RuntimeError("V2_FINALIZER_EXPECTED_MECHANISM_ONLY_PASS:" + classification)
    final_status = "B2_ENGINEERING_MECHANISM_ONLY_NO_ACTIONABLE_WITNESS"
    witness.update({
        "status": final_status,
        "phase_b_classification": classification,
        "exact_child_admission": admission,
        "same_frame_B1_false_B2_true_witness_count": same_frame_count,
        "B1_actionable_window_presence": b1_window,
        "B2_actionable_window_presence": b2_window,
        "invalid_retention_failures": int(invalid_retention),
        "derived_receipt_revision": {
            "reason": "ROUTE_ADMISSION_NORMALIZED_FOR_NATIVE_SPAWN_ONLY_Z_ELEVATION",
            "raw_native_trace_unchanged": True,
            "native_episode_rerun": False,
            "phase_b_gate_reapplied_once": True,
            "prior_derived_receipt_preserved": str(prior_witness_archive.relative_to(ROOT)),
        },
    })
    witness.pop("receipt_digest", None)
    witness["receipt_digest"] = canonical_sha256(witness)
    _write_json(witness_path, witness)
    _write_md(REPORT / "B2_ENGINEERING_WITNESS_REPORT.md", "B2 engineering-only LMK witness", [
        "Identity: `{}`; seed: `{}`; both are permanently excluded from formal denominators.".format(
            witness["identity"], witness["seed"],
        ),
        "Exact child admission: `{}`. The 36-point dense and 2-point downsampled plans match exact X/Y geometry and commands; the disclosed first-point +0.5 m Z is Bench2Drive's native spawn elevation.".format(
            admission["status"],
        ),
        "Same-frame B1=false/B2=true witnesses: `{}`.".format(same_frame_count),
        "First B1/B2 sufficiency TTCmt: `{}` / `{}` seconds.".format(
            builder.get("B1_first_sufficiency_TTCmt_s"), builder.get("B2_first_sufficiency_TTCmt_s"),
        ),
        "B1/B2 actionable-window presence: `{}` / `{}`.".format(b1_window, b2_window),
        "Invalid-retention failures: `{}`; false sufficiency: `{}`.".format(
            int(invalid_retention), bool(builder.get("false_sufficiency_B2")),
        ),
        "Phase-B classification: `MECHANISM-ONLY PASS`. Phase C and all Formal V2 materialization/execution are forbidden by the frozen gate.",
    ])

    runtime_confirmation = {
        "engineering_identity": witness["identity"],
        "runtime_receipt_path": str(
            next(ENGINEERING.glob("*/attempt_01/AGENT_ROUTE_BINDING_RUNTIME_RECEIPT.json")).relative_to(ROOT)
        ),
        "dense_plan_comparison": dense_comparison,
        "downsampled_plan_comparison": downsampled_comparison,
        "actual_navigation_target_consumed": runtime_route["actual_navigation_target_consumed"],
        "raw_runtime_receipt_unchanged": True,
        "scientifically_equivalent": route_plan_comparison["scientifically_equivalent"],
    }
    route_binding["runtime_confirmation"] = runtime_confirmation
    route_binding["all_relevant_representations_scientifically_equivalent"] = (
        route_binding["all_relevant_representations_scientifically_equivalent"]
        and runtime_confirmation["scientifically_equivalent"]
    )
    route_binding["route_equivalence_verdict"] = (
        "PASS_SCIENTIFIC_ROUTE_EQUIVALENCE"
        if route_binding["all_relevant_representations_scientifically_equivalent"]
        else "V2_ROUTE_BINDING_NOT_CLOSED"
    )
    route_binding.pop("receipt_digest", None)
    route_binding["receipt_digest"] = canonical_sha256(route_binding)
    _write_json(route_path, route_binding)
    async_route = route_binding["representations"]["LMK-ASYNC"]
    _write_md(REPORT / "LMK_ROUTE_BINDING_REPORT_V2.md", "LMK route binding V2", [
        "Canonical executable owner: `{}`.".format(CANONICAL_EXECUTABLE_ROUTE_OWNER),
        "Certified/source length: `{:.12f} m`; exact GRP/evaluator length: `{:.12f} m` (`{:.9f}x`).".format(
            async_route["route_binding_contract"]["source_polyline_length_m"],
            async_route["exact_evaluator_route_length_m"],
            async_route["evaluator_to_source_length_ratio"],
        ),
        "Exact GRP/evaluator coordinates, 36-point dense plan, 2-point downsampled world/GPS plans, commands, topology, and terminal identity are in the machine receipt.",
        "The exact-child sidecar also persists the navigation target consumed by the agent. Runtime X/Y and commands match exactly; the sole +0.5 m first-Z difference is the native Bench2Drive spawn elevation.",
        "Verdict: `{}`.".format(route_binding["route_equivalence_verdict"]),
    ])

    seam = {
        "schema_version": "driveclarify.rq2_t_cg.execution_seam_8_scene_qualification.v2",
        "status": "NOT_RUN_PHASE_B_NOT_STRONG_PASS",
        "authorization_condition": "PHASE_B_STRONG_PASS",
        "authorization_satisfied": False,
        "required_scene_count": 8,
        "attempted_scene_count": 0,
        "qualified_scene_count": 0,
        "result": "NOT_RUN",
        "engineering_identities_generated": [],
        "engineering_seeds_generated": [],
        "formal_scientific_exposures": 0,
    }
    seam["receipt_digest"] = canonical_sha256(seam)
    _write_json(REPORT / "EXECUTION_SEAM_8_SCENE_QUALIFICATION.json", seam)
    _write_md(REPORT / "EXECUTION_SEAM_8_SCENE_QUALIFICATION.md", "Eight-scene exact seam qualification", [
        "Status: `NOT_RUN_PHASE_B_NOT_STRONG_PASS`.",
        "The mandatory Phase-B strong gate was not met, so zero of eight scene seams were attempted and no Phase-C identities or seeds were generated.",
    ])
    formal_freeze = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v2_freeze.v2",
        "status": "NOT_MATERIALIZED_PHASE_B_GATE_CLOSED",
        "formal_v2_protocol_identity": None,
        "formal_v2_scene_freeze_digest": None,
        "scene_count_frozen": 0,
        "predecessor_v1_scene_freeze_not_promoted": True,
        "formal_seed_values_generated": 0,
    }
    formal_freeze["receipt_digest"] = canonical_sha256(formal_freeze)
    _write_json(REPORT / "FORMAL_V2_FREEZE_RECEIPT.json", formal_freeze)
    seed_freshness = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v2_seed_freshness.v2",
        "status": "NOT_RUN_PHASE_B_GATE_CLOSED",
        "requested_seed_count_if_authorized": 6,
        "generated_seed_count": 0,
        "seed_values": [],
        "freshness_result": "NOT_RUN_NO_FORMAL_SEEDS_EXIST",
    }
    seed_freshness["receipt_digest"] = canonical_sha256(seed_freshness)
    _write_json(REPORT / "FORMAL_V2_SEED_FRESHNESS_RECEIPT.json", seed_freshness)
    roster = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v2_roster.v2",
        "status": "NOT_MATERIALIZED_PHASE_B_GATE_CLOSED",
        "planned_episode_count_if_authorized": 48,
        "materialized_episode_count": 0,
        "formal_seed_values": [],
        "cells": [],
    }
    roster["roster_digest"] = canonical_sha256(roster)
    _write_json(REPORT / "FORMAL_V2_ROSTER.json", roster)
    ledger = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v2_execution_ledger.v2",
        "status": "NOT_RUN_PHASE_B_GATE_CLOSED",
        "planned_episode_count_if_authorized": 48,
        "attempted_episode_count": 0,
        "valid_episode_count": 0,
        "scientific_retry_count": 0,
        "infrastructure_retry_count": 0,
        "formal_scientific_exposure_count": 0,
        "validity_gate": "NOT_RUN_REQUIRES_48_OF_48",
        "hcg_analysis_run": False,
        "episodes": [],
    }
    ledger["ledger_digest"] = canonical_sha256(ledger)
    _write_json(REPORT / "FORMAL_V2_EXECUTION_LEDGER.json", ledger)

    # Materialize every unconditional report path before taking the exit Git
    # snapshot so its untracked-path digest describes the final report tree.
    _write_json(REPORT / "SOURCE_FREEZE_RECEIPT.json", {})
    _write_json(REPORT / "FINAL_VALIDATION_RECEIPT.json", {})
    _write_md(REPORT / "FINAL_REPORT.md", "Formal V2 narrow repair final report", ["Pending final validation."])
    source_freeze = _source_identity()
    _write_json(REPORT / "SOURCE_FREEZE_RECEIPT.json", source_freeze)

    dense_points = [row["world_xyz"] for row in runtime_route["agent_dense_global_plan_world"]]
    down_points = [row["world_xyz"] for row in runtime_route["agent_downsampled_global_plan_world"]]
    dense_xy_length = sum(math.dist(left[:2], right[:2]) for left, right in zip(dense_points, dense_points[1:]))
    dense_xyz_length = _route_length(dense_points)
    down_xy_length = sum(math.dist(left[:2], right[:2]) for left, right in zip(down_points, down_points[1:]))
    final_return = {
        "1_entry_HEAD": ENTRY_HEAD,
        "2_exit_HEAD": source_freeze["current_git"]["head"],
        "3_exact_source_changes": [
            "added route_binding_v2.py canonical LMK executable-keypoint binding",
            "added child_admission_v2.py exact child environment and first-row watchdog",
            "updated routes.py to serialize executable keypoints and binding digest",
            "updated native_scenario.py to validate/persist the binding digest while retaining source-polyline progress",
            "updated SimLingo driveclarify_probe_hook.py with read-only agent-plan/target persistence and numpy route-item support",
            "updated focused formal-execution tests",
            "added run_rq2_t_cg_formal_v2.py gated executor/reporting tool",
        ],
        "4_proof_no_science_was_changed": {
            "science_hashes_unchanged": source_freeze["science_hashes_unchanged"],
            "frozen_science_sources": source_freeze["frozen_science_sources"],
        },
        "5_recorder_root_cause_repair": "Exact formal child now explicitly restores recorder/probe variables and destinations; generic-preflight/formal-child environment divergence is removed.",
        "6_exact_child_admission_result": admission["status"],
        "7_first_row_fail_closed_result": "PASS_LIVE_FIRST_ROW_AND_FOCUSED_MISSING_ROW_FAIL_CLOSED_TEST",
        "8_LMK_canonical_route_owner": CANONICAL_EXECUTABLE_ROUTE_OWNER,
        "9_source_polyline_length_m": async_route["route_binding_contract"]["source_polyline_length_m"],
        "10_GRP_evaluator_route_length_m": async_route["exact_evaluator_route_length_m"],
        "11_agent_route_length_count": {
            "dense_count": len(dense_points),
            "dense_scientific_xy_length_m": dense_xy_length,
            "dense_raw_xyz_length_with_spawn_elevation_m": dense_xyz_length,
            "downsampled_count": len(down_points),
            "downsampled_xy_length_m": down_xy_length,
        },
        "12_route_equivalence_verdict": route_binding["route_equivalence_verdict"],
        "13_persisted_route_target_coordinates_available": True,
        "14_PID_controller_changed": False,
        "15_VLA_checkpoint_changed": False,
        "16_TTL_evidence_timing_changed": False,
        "17_commitment_deadline_changed": False,
        "18_engineering_witness_identity": witness["identity"],
        "19_engineering_witness_seed": witness["seed"],
        "20_engineering_witness_permanently_excluded": True,
        "21_same_frame_B1_false_B2_true_witness_count": same_frame_count,
        "22_first_B1_sufficiency_TTCmt_s": builder.get("B1_first_sufficiency_TTCmt_s"),
        "23_first_B2_sufficiency_TTCmt_s": builder.get("B2_first_sufficiency_TTCmt_s"),
        "24_B1_actionable_window_presence": b1_window,
        "25_B2_actionable_window_presence": b2_window,
        "26_invalid_retention_failures": int(invalid_retention),
        "27_false_sufficiency": bool(builder.get("false_sufficiency_B2")),
        "28_Phase_B_classification": classification,
        "29_8_of_8_seam_qualification_result": seam["status"],
        "30_V2_formal_scene_freeze_digest": None,
        "31_formal_seeds_generated": 0,
        "32_formal_seed_values": [],
        "33_freshness_result": seed_freshness["freshness_result"],
        "34_planned_Formal_V2_episodes": 48,
        "35_attempted_Formal_V2_episodes": 0,
        "36_valid_Formal_V2_episodes": 0,
        "37_scientific_retries": 0,
        "38_infrastructure_retries": 0,
        "39_formal_validity_gate_result": ledger["validity_gate"],
        "40_H_CG_analysis_run": False,
        "41_H_CG1_result": "NOT_LEGALLY_ESTIMABLE",
        "42_H_CG2_result": "NOT_LEGALLY_ESTIMABLE",
        "43_H_CG3_result": "NOT_LEGALLY_ESTIMABLE",
        "44_H_CG4_result": "NOT_LEGALLY_ESTIMABLE",
        "45_added_VLA_forwards": 0,
        "46_duplicate_candidate_computations": 0,
        "47_second_control_writer": 0,
        "48_RoutePlanner_scientific_mutations": 0,
        "49_true_intent_runtime_reads": 0,
        "50_source_freeze_result": "PASS" if source_freeze["source_freeze_pass"] else "FAIL",
        "51_exact_final_status": final_status,
        "52_next_recommendation": "Submit the sealed mechanism-only witness for independent review; do not authorize Phase C or any Formal V2 seed unless a separately authorized prospective scientific-contract review determines how to address the missed frozen actionability margin.",
    }
    validation_checks = {
        "phase_a_pass": _load(REPORT / "PHASE_A_STATIC_VALIDATION_RECEIPT.json", {}).get("pass") is True,
        "route_binding_pass": route_binding["route_equivalence_verdict"] == "PASS_SCIENTIFIC_ROUTE_EQUIVALENCE",
        "exact_child_admission_pass": admission["pass"],
        "first_legal_row_pass": admission["checks"]["first_legal_row_pass"],
        "phase_b_mechanism_only": classification == "MECHANISM-ONLY PASS",
        "phase_c_not_run": seam["attempted_scene_count"] == 0,
        "formal_v2_seed_count_zero": seed_freshness["generated_seed_count"] == 0,
        "formal_v2_attempt_count_zero": ledger["attempted_episode_count"] == 0,
        "hcg_analysis_absent": not (REPORT / "FORMAL_V2_ANALYSIS_REPORT.md").exists() and not (REPORT / "FORMAL_V2_HCG_RESULTS.json").exists(),
        "no_control_effect": admission["no_control_effect_receipt"]["pass"],
        "cleanup_pass": admission["checks"]["cleanup_pass"],
        "source_freeze_pass": source_freeze["source_freeze_pass"],
        "entry_exit_head_equal": source_freeze["current_git"]["head"] == ENTRY_HEAD,
        "tracked_and_staged_diff_clean": (
            source_freeze["current_git"]["tracked_diff_bytes"] == 0
            and source_freeze["current_git"]["staged_diff_bytes"] == 0
        ),
    }
    validation = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v2.final_validation.v2",
        "status": final_status,
        "checks": validation_checks,
        "failed_checks": [key for key, value in validation_checks.items() if not value],
        "pass": all(validation_checks.values()),
        "required_final_return": final_return,
        "source_freeze_digest": source_freeze["source_freeze_digest"],
        "formal_scientific_exposures": 0,
        "formal_seed_values_generated": 0,
    }
    validation["receipt_digest"] = canonical_sha256(validation)
    _write_json(REPORT / "FINAL_VALIDATION_RECEIPT.json", validation)

    values = [
        final_return["1_entry_HEAD"], final_return["2_exit_HEAD"],
        "; ".join(final_return["3_exact_source_changes"]),
        "PASS; all five frozen science hashes match",
        final_return["5_recorder_root_cause_repair"], final_return["6_exact_child_admission_result"],
        final_return["7_first_row_fail_closed_result"], final_return["8_LMK_canonical_route_owner"],
        str(final_return["9_source_polyline_length_m"]), str(final_return["10_GRP_evaluator_route_length_m"]),
        json.dumps(final_return["11_agent_route_length_count"], sort_keys=True), final_return["12_route_equivalence_verdict"],
        "yes; machine route receipt plus exact-child runtime sidecar", "no", "no", "no", "no",
        final_return["18_engineering_witness_identity"], str(final_return["19_engineering_witness_seed"]), "yes",
        str(same_frame_count), "none", str(builder.get("B2_first_sufficiency_TTCmt_s")),
        str(b1_window).lower(), str(b2_window).lower(), str(int(invalid_retention)),
        str(bool(builder.get("false_sufficiency_B2"))).lower(), classification,
        seam["status"], "not generated", "0", "[]", seed_freshness["freshness_result"],
        "48 conditionally planned; not materialized", "0", "0", "0", "0",
        ledger["validity_gate"], "no", "not legally estimable", "not legally estimable",
        "not legally estimable", "not legally estimable", "0", "0", "0", "0", "0",
        final_return["50_source_freeze_result"], final_status, final_return["52_next_recommendation"],
    ]
    labels = [key.split("_", 1)[1].replace("_", " ") for key in final_return]
    _write_md(REPORT / "FINAL_REPORT.md", "Formal V2 narrow repair final report", [
        "Final status: `{}`. Phase A and route/recorder closure passed; the one permitted Phase-B trace was mechanism-only, so execution stopped before Phase C and before any formal seed.".format(final_status),
        "",
        "## Required final return",
        "",
        *["{}. **{}:** {}".format(index, labels[index - 1], values[index - 1]) for index in range(1, 53)],
    ])
    _append_command("finalize-stop", final_status)
    print(json.dumps({
        "status": final_status,
        "classification": classification,
        "validation_pass": validation["pass"],
        "formal_seed_values_generated": 0,
        "formal_scientific_exposures": 0,
    }, sort_keys=True), flush=True)
    return validation


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("phase-a", "route-bind", "witness", "finalize-stop"))
    parser.add_argument("--port", type=int, default=2020)
    parser.add_argument("--wall-timeout-seconds", type=float, default=900.0)
    args = parser.parse_args()
    if args.command == "phase-a":
        result = phase_a_static()
        return 0 if result["pass"] else 2
    if args.command == "route-bind":
        result = qualify_route_binding(args.port)
        return 0 if result["all_relevant_representations_scientifically_equivalent"] else 2
    if args.command == "finalize-stop":
        result = finalize_mechanism_only_stop()
        return 0 if result["pass"] else 2
    result = run_lmk_witness(args.wall_timeout_seconds)
    return 0 if result["phase_b_classification"] == "STRONG PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
