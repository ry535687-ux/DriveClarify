"""仅解析已冻结输入并打印命令；没有运行模式，不导入原生入口。"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
from pathlib import Path
import shlex
import sys
import xml.etree.ElementTree as ET

CONFIGS = Path(__file__).resolve().parent / "configs"
PROTOCOL = "DC_NATIVE_CLEAR_INIT_DIAG_V1"


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def parse_route(path):
    root = ET.parse(path).getroot()
    routes = root.findall("route")
    require(root.tag == "routes" and len(routes) == 1, "ONE_COMPLETE_NATIVE_ROUTE_REQUIRED")
    route = routes[0]
    points = [[float(p.attrib[k]) for k in "xyz"] for p in route.findall("waypoints/position")]
    require(len(points) >= 3 and all(math.isfinite(v) for p in points for v in p), "INVALID_NATIVE_WAYPOINTS")
    require(route.find("scenarios/scenario/trigger_point") is not None, "NATIVE_SCENARIO_REQUIRED")
    require(route.find("weathers/weather") is not None, "NATIVE_WEATHER_REQUIRED")
    return route, points


def prepare(case_path, *, verify_checkpoint=False):
    case_path = Path(case_path).resolve()
    case = read_json(case_path)
    require(case["protocol_id"] == PROTOCOL and case["enabled"] is False, "DEFAULT_DISABLED_CONFIG_REQUIRED")
    require(case["task_id"] in ("A", "B") and case["question_budget"] == 0 and case["hidden_intent"] is None, "PUBLIC_INITIAL_TASK_ONLY")
    require(case["repetitions"] == 1 and case["traffic_manager_seed"] == 0, "ONE_EXISTING_DEFAULT_SEED_RUN_ONLY")
    base = case_path.parent
    backend = read_json(base / case["backend_identity_file"])
    files = backend["files"]
    for key, record in files.items():
        path = Path(record["path"])
        require(path.is_file() and path.stat().st_size == record["size"], "PIN_MISSING_OR_SIZE_CHANGED:" + key)
        # 大 checkpoint 摘要在准备时取得；默认 dry-run 只检查文件及大小。
        if key != "checkpoint" or verify_checkpoint:
            require(digest(path) == record["sha256"], "PIN_HASH_CHANGED:" + key)
    require(files["checkpoint"]["sha256"] == backend["checkpoint_expected_from_existing_backend"], "CHECKPOINT_BASELINE_MISMATCH")
    route_path = (base / case["route_file"]).resolve()
    source = Path(case["route_source"]["path"])
    require(digest(route_path) == digest(source) == case["route_source"]["sha256"], "ROUTE_COPY_NOT_BYTE_IDENTICAL")
    route, points = parse_route(route_path)
    require(route.attrib["id"] == case["route_id"] and route.attrib["town"] == case["town"], "ROUTE_IDENTITY_MISMATCH")
    require(case["case_id"] == f"DEV_NATIVE_INIT_{case['task_id']}_{case['town'].upper()}_{case['route_id']}", "CASE_OUTPUT_IDENTITY_MISMATCH")
    contract = read_json(base / case["evaluation_file"])
    require(contract["public_task_id"] == case["task_id"] and contract["route_sha256"] == digest(route_path), "EVALUATION_ROUTE_MISMATCH")
    require(contract["method_truth_inputs"] == [], "METHOD_TRUTH_FORBIDDEN")
    require([g["name"] for g in contract["gates"]] == ["entry", "exit", "local_end"], "ORDERED_GATES_REQUIRED")
    for gate in contract["gates"]:
        i = gate["source_waypoint_index_zero_based"]
        require(0 < i < len(points) - 1, "GATE_INDEX_INVALID")
        require(gate["center"] == points[i][:2], "GATE_NOT_AT_DECLARED_SOURCE_POINT")
        require(gate["normal"] == [points[i][j] - points[i-1][j] for j in (0, 1)], "GATE_NORMAL_SOURCE_MISMATCH")
    repo = Path(backend["repo_root"])
    dev = repo / "experiments/driveclarify_native_clear_backend_dev_20260913"
    output = Path(case["output_dir"])
    expected = dev / "outputs" / case["case_id"]
    require(output.is_absolute() and output.resolve() == expected and not output.exists(), "OUTPUT_MUST_BE_FRESH_ISOLATED_CASE_PATH")
    sim = Path(backend["simlingo_root"])
    carla = Path(backend["carla_root"])
    python = Path(backend["python"])
    require(python.is_file() and (carla / "CarlaUE4.sh").is_file(), "NATIVE_EXECUTABLE_MISSING")
    leaderboard = sim / "Bench2Drive/leaderboard"
    scenario = sim / "Bench2Drive/scenario_runner"
    # 空环境起步，不继承 SHELL 中的 SHADOW / LIVE / owner / CP3B 开关。
    env = {
        "HOME": "/home/buaa", "USER": "buaa", "LOGNAME": "buaa",
        "PATH": str(python.parent) + ":/usr/bin:/bin", "LANG": "C.UTF-8",
        "DISPLAY": ":1", "XAUTHORITY": "/run/user/1000/gdm/Xauthority",
        "XDG_RUNTIME_DIR": "/run/user/1000", "XDG_SESSION_TYPE": "x11", "XDG_SESSION_REMOTE": "false",
        "__NV_PRIME_RENDER_OFFLOAD": "1", "__GLX_VENDOR_LIBRARY_NAME": "nvidia",
        "CARLA_ROOT": str(carla), "LEADERBOARD_ROOT": str(leaderboard), "SCENARIO_RUNNER_ROOT": str(scenario),
        "PYTHONPATH": ":".join(map(str, (repo, sim, leaderboard, scenario, carla / "PythonAPI", carla / "PythonAPI/carla"))),
        "CUDA_VISIBLE_DEVICES": "0", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "TOKENIZERS_PARALLELISM": "false", "PYTHONUNBUFFERED": "1", "IS_BENCH2DRIVE": "1",
        "SAVE_PATH": str(output / "native_save") + "/",
        "DRIVECLARIFY_REPO": str(repo), "DRIVECLARIFY_PROBE_ENABLED": "1",
        "DRIVECLARIFY_PROBE_OUTPUT": str(output / "probe.jsonl"),
        "DRIVECLARIFY_PROBE_EQUIVALENCE": str(output / "PROBE_EQUIVALENCE.json"),
        "DRIVECLARIFY_PROBE_RUN_ID": case["case_id"],
        "DRIVECLARIFY_WORLDSTATE_OUTPUT": str(output / "world_state.jsonl"),
        "DRIVECLARIFY_ROUTE_BINDING_RUNTIME_RECEIPT": str(output / "ROUTE_BINDING_RUNTIME.json"),
        "DRIVECLARIFY_CONFIG_HASH": digest(case_path), "DRIVECLARIFY_CKPT_HASH": files["checkpoint"]["sha256"],
    }
    argv = [str(python), "-u", files["evaluator"]["path"],
            "--routes=" + str(route_path), "--repetitions=1", "--track=SENSORS",
            "--checkpoint=" + str(output / "leaderboard_results.json"),
            "--debug-checkpoint=" + str(output / "leaderboard_debug.txt"),
            "--agent=" + files["agent"]["path"], "--agent-config=" + files["checkpoint"]["path"],
            "--debug=0", "--timeout=600", "--port=2020", "--traffic-manager-port=8020", "--traffic-manager-seed=0", "--gpu-rank=0"]
    # AST 读取 argparse 字符串；不 import 或执行 evaluator。
    tree = ast.parse(Path(files["evaluator"]["path"]).read_text())
    supported = {a.value for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "add_argument" for a in n.args if isinstance(a, ast.Constant) and isinstance(a.value, str)}
    require(all(a.split("=", 1)[0] in supported for a in argv[3:]), "NATIVE_ARGPARSE_FLAG_MISMATCH")
    command = shlex.join(["/usr/bin/env", "-i", *[k + "=" + v for k, v in sorted(env.items())], *argv])
    return {"status": "DRY_RUN_INPUTS_PARSED_NOT_AUTHORIZED", "protocol_id": PROTOCOL,
            "case_id": case["case_id"], "cwd": str(sim), "output_dir": str(output),
            "argv": argv, "environment": env, "command": command,
            "output_preparation_commands": [shlex.join(["mkdir", "-p", "--", str(output.parent)]),
                                            shlex.join(["mkdir", "--", str(output)])],
            "checkpoint_rehashed_this_call": verify_checkpoint,
            "launch_allowed": False, "native_executed": False,
            "unmet_before_execution": ["EXPLICIT_USER_AUTHORIZATION", "VERIFIED_LOCAL_PHYSICAL_DISPLAY",
                                       "CURRENT_RESOURCE_BUDGET_AND_AVAILABILITY", "OWNED_PROCESS_420S_WALL_GUARD_AND_CLEANUP"],
            "measured_native_route_acceptance": None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("A", "B"), required=True)
    parser.add_argument("--verify-checkpoint-bytes", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = prepare(CONFIGS / f"case_{args.case}.json", verify_checkpoint=args.verify_checkpoint_bytes)
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as exc:
        print(json.dumps({"status": "INPUT_ERROR", "reason": str(exc), "native_executed": False}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
