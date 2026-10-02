"""仅本轮合同检查；合成轨迹不是驾驶记录。"""
import ast
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch

DEV = Path(__file__).resolve().parents[1]


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, DEV / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dry = load_module("dry_run")
extract = load_module("extract_observer")


def fixture(letter="A"):
    case = dry.read_json(DEV / f"configs/case_{letter}.json")
    contract = dry.read_json(DEV / case_path(case, "evaluation_file"))
    _, points = dry.parse_route(DEV / case_path(case, "route_file"))
    start = contract["gates"][0]["source_waypoint_index_zero_based"] - 1
    end = contract["gates"][-1]["source_waypoint_index_zero_based"]
    rows = []
    for frame, xyz in enumerate(points[start:end+1]):
        rows.append({"run_id": case["case_id"], "observation_id": f"{case['case_id']}:{frame}:simlingo_agent_v0",
                     "carla_snapshot_frame": frame, "snapshot_frame": frame, "snapshot_elapsed_seconds": frame * .05,
                     "snapshot_delta_seconds": .05, "ego": {"location_xyz": xyz},
                     "model_start_monotonic_s": 10 + frame, "model_end_monotonic_s": 10.5 + frame,
                     "pred_route_values": [[1, 2], [3, 4]], "pred_speed_wps_values": [2],
                     "baseline_control": {"steer": .1, "throttle": .2, "brake": 0}, "control_ready_monotonic_s": 10.6 + frame})
    return case, contract, rows


def case_path(case, key):
    return Path("configs") / case[key]


class InputsTests(unittest.TestCase):
    def test_two_full_native_geometric_tasks(self):
        a, pa = dry.parse_route(DEV / "configs/route_A.xml")
        b, pb = dry.parse_route(DEV / "configs/route_B.xml")
        self.assertEqual((a.attrib["id"], b.attrib["id"]), ("25968", "26458"))
        self.assertEqual((len(pa), len(pb)), (18, 27))
        self.assertGreater(pa[-1][0] - pa[3][0], 0)
        self.assertLess(pb[-1][0] - pb[12][0], 0)
        self.assertNotEqual(pa[0], pb[0])

    def test_dry_run_native_flags_and_zero_side_effects(self):
        with patch("subprocess.Popen", side_effect=AssertionError("禁止启动进程")), patch("socket.socket", side_effect=AssertionError("禁止占端口")):
            for letter in "AB":
                result = dry.prepare(DEV / f"configs/case_{letter}.json")
                self.assertFalse(result["launch_allowed"])
                self.assertFalse(result["native_executed"])
                self.assertFalse(Path(result["output_dir"]).exists())
                self.assertEqual(shlex.split(result["command"])[0:2], ["/usr/bin/env", "-i"])
                self.assertIn("--repetitions=1", result["argv"])
                self.assertIn("--traffic-manager-seed=0", result["argv"])
                self.assertEqual(result["environment"]["DISPLAY"], ":1")

    def test_inherited_live_flags_cannot_enter_command(self):
        with patch.dict("os.environ", {"DRIVECLARIFY_SHADOW_V0": "1", "DRIVECLARIFY_RQ2_T_2A_OWNER_ENABLED": "1", "DISPLAY": "remote:9"}):
            result = dry.prepare(DEV / "configs/case_A.json")
        self.assertNotIn("DRIVECLARIFY_SHADOW_V0", result["environment"])
        self.assertNotIn("DRIVECLARIFY_RQ2_T_2A_OWNER_ENABLED", result["environment"])
        self.assertNotIn("remote:9", result["command"])

    def test_fresh_outputs_distinct_and_protected(self):
        a = dry.prepare(DEV / "configs/case_A.json")
        b = dry.prepare(DEV / "configs/case_B.json")
        self.assertNotEqual(a["output_dir"], b["output_dir"])
        original = dry.read_json
        def redirected(path):
            result = original(path)
            if Path(path).name == "case_A.json":
                result["output_dir"] = str(DEV / "configs")
            return result
        with patch.object(dry, "read_json", side_effect=redirected), self.assertRaisesRegex(ValueError, "OUTPUT_MUST"):
            dry.prepare(DEV / "configs/case_A.json")

    def test_default_disabled_config_required(self):
        original = dry.read_json
        def enabled(path):
            result = original(path)
            if Path(path).name == "case_A.json":
                result["enabled"] = True
            return result
        with patch.object(dry, "read_json", side_effect=enabled), self.assertRaisesRegex(ValueError, "DEFAULT_DISABLED"):
            dry.prepare(DEV / "configs/case_A.json")

    def test_dry_script_has_only_stdlib_and_no_process_executor(self):
        tree = ast.parse((DEV / "dry_run.py").read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(n.name.split(".")[0] for n in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module.split(".")[0])
        self.assertLessEqual(imported, {"__future__", "argparse", "ast", "hashlib", "json", "math", "pathlib", "shlex", "sys", "xml"})
        self.assertNotIn("subprocess", imported)


class ObserverTests(unittest.TestCase):
    def test_source_defined_ordered_closed_gates_A_and_B(self):
        for letter in "AB":
            case, contract, rows = fixture(letter)
            result = extract.summarize(case, contract, rows)
            evaluation = result["task_event_evaluation"]
            self.assertTrue(evaluation["ordered_closed_gate_arrival_observed"])
            self.assertEqual([e["name"] for e in evaluation["events"]], ["entry", "exit", "local_end"])
            self.assertIsNone(evaluation["whole_mission_success"])
            self.assertIsNone(result["control_observation"]["carla_apply_control_receipt"])

    def test_missing_frame_breaks_order(self):
        _, contract, rows = fixture()
        del rows[3]
        result = extract.task_events(contract, rows)
        self.assertFalse(result["ordered_closed_gate_arrival_observed"])
        self.assertEqual(result["gaps"][0]["reason"], "FRAME_OR_CLOCK_GAP")

    def test_inconsistent_real_clock_not_accepted(self):
        _, contract, rows = fixture()
        for i, row in enumerate(rows):
            row["snapshot_elapsed_seconds"] = i * 100
        result = extract.task_events(contract, rows)
        self.assertFalse(result["ordered_closed_gate_arrival_observed"])
        self.assertTrue(result["gaps"])

    def test_method_truth_and_RC_do_not_change_events(self):
        case, contract, rows = fixture()
        expected = extract.task_events(contract, rows)
        for row in rows:
            row.update(selected_candidate="B", hidden_truth="WRONG", task_complete=False, RC=0)
        result = extract.summarize(case, contract, rows, native_statistics={"_checkpoint": {"records": [{"status": "Completed", "scores": {"score_route": 100}}]}})
        self.assertEqual(expected, result["task_event_evaluation"])
        self.assertFalse(extract.summarize(case, contract, [], native_statistics={"_checkpoint": {"records": [{"scores": {"score_route": 100}}]}})["task_event_evaluation"]["ordered_closed_gate_arrival_observed"])

    def test_equal_numeric_plans_still_have_observation_bound_calls(self):
        case, contract, rows = fixture()
        result = extract.summarize(case, contract, rows)
        self.assertEqual(result["model_observation"]["records_with_call_times_output_and_observation_binding"], len(rows))
        self.assertEqual(result["control_observation"]["records_with_bound_returned_control"], len(rows))

    def test_counters_alone_never_prove_forward_or_control(self):
        case, contract, rows = fixture()
        for row in rows:
            for key in ("model_start_monotonic_s", "model_end_monotonic_s", "control_ready_monotonic_s", "pred_route_values"):
                row.pop(key)
            row["forward_invocation_counter"] = 1
        result = extract.summarize(case, contract, rows)
        self.assertEqual(result["model_observation"]["records_with_call_times_output_and_observation_binding"], 0)
        self.assertEqual(result["control_observation"]["records_with_bound_returned_control"], 0)

    def test_run_identity_mismatch_rejected(self):
        case, contract, rows = fixture()
        rows[0]["run_id"] = "OTHER_RUN"
        with self.assertRaisesRegex(ValueError, "RUN_ID_MISMATCH"):
            extract.summarize(case, contract, rows)

    def test_frame_binding_mismatch_does_not_prove_forward(self):
        case, contract, rows = fixture()
        rows[0]["carla_snapshot_frame"] = 999
        result = extract.summarize(case, contract, rows[:1])
        self.assertEqual(result["model_observation"]["records_with_call_times_output_and_observation_binding"], 0)


if __name__ == "__main__":
    unittest.main()
