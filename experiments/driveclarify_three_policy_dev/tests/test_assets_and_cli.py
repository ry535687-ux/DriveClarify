"""真实地图资产只做静态绑定测试；沿地图线构造的轨迹明确是合成记录。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from experiments.driveclarify_three_policy_dev.evaluator import evaluate_trace
from experiments.driveclarify_three_policy_dev.policy import (
    POLICIES, ControlledAnswerService, CPURequestBackend, DurableQuestions, ThreePolicy, relation,
)
from experiments.driveclarify_three_policy_dev.tests.fixtures import public_case, state

ROOT = Path(__file__).resolve().parents[3]
DEV = ROOT / "experiments/driveclarify_three_policy_dev"


def read(name):
    return json.loads((DEV / "configs" / name).read_text())


class AssetAndCLITests(unittest.TestCase):
    def test_actual_bound_geometries_share_start_and_have_distinct_pass_events(self):
        public = read("TOWN04_DIVERGENT_DEV.public.json")
        contract = read("TOWN04_BRANCH_CONTRACT.json")
        self.assertEqual(relation(public), "TASK_CRITICAL")
        a, b = public["candidates"]
        self.assertEqual(a["route_xy"][:2], b["route_xy"][:2])
        self.assertNotEqual(a["route_xy"][-1], b["route_xy"][-1])
        for candidate, expected_branch in zip(public["candidates"], ("J870_RIGHT", "J483_RIGHT")):
            # 每个采样从地图几何构造，并非实测 ego trace 或论文结果。
            samples = [{"frame": i, "sim_time_s": i*.05, "x": xy[0], "y": xy[1]}
                       for i, xy in enumerate(candidate["route_xy"])]
            endpoints = {k: {"event_observed": None, "coverage_complete": False} for k in contract["safety_endpoints"]}
            result = evaluate_trace(contract, samples, {"allowed_branches": [expected_branch]},
                                    {"start_observed": True, "end_observed": True}, endpoints)
            self.assertTrue(result["task_complete"])
            self.assertEqual({e["branch"] for e in result["events"]}, {expected_branch})
            self.assertIsNone(result["safe_task_complete"])
            other = "J483_RIGHT" if expected_branch == "J870_RIGHT" else "J870_RIGHT"
            wrong = evaluate_trace(contract, samples, {"allowed_branches": [other]},
                                   {"start_observed": True, "end_observed": True}, endpoints)
            self.assertTrue(wrong["wrong_irreversible_branch"])

    def test_equivalent_shares_layout_and_real_configs_remain_runtime_blocked(self):
        equivalent = read("TOWN04_EQUIVALENT_DEV.public.json")
        self.assertEqual(relation(equivalent), "TASK_EQUIVALENT")
        self.assertEqual(equivalent["candidates"][0]["route_xy"], equivalent["candidates"][1]["route_xy"])
        for public in (equivalent, read("TOWN04_DIVERGENT_DEV.public.json")):
            self.assertIsNone(public["deadline"]["budget_s"])
            with tempfile.TemporaryDirectory() as directory:
                for policy in POLICIES:
                    writer = DurableQuestions(Path(directory)/policy)
                    service, backend = ControlledAnswerService("FIRST", writer), CPURequestBackend()
                    runner = ThreePolicy(policy, public, writer, service, backend, enabled=True)
                    decision = runner.step(state())
                    self.assertEqual(decision["action"], "WAIT")
                    self.assertEqual(backend.requests, [])
                    self.assertEqual(service.read_count, 0)

    def test_enabled_cli_no_clarification_does_not_open_nonexistent_answer_file(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-B", "-m", "experiments.driveclarify_three_policy_dev",
                "--enable-cpu-dev", "policy", "--public", str(DEV/"configs/SYNTHETIC.public.json"),
                "--states", str(DEV/"configs/SYNTHETIC.states.json"), "--policy", POLICIES[0],
                "--receipts", directory, "--answer-file", str(Path(directory)/"does_not_exist")],
                cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(output["answer_reads"], 0)
            self.assertEqual(output["backend_requests"][0]["candidate_id"], "A")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_answer_must_match_written_question(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = DurableQuestions(directory)
            service = ControlledAnswerService("FIRST", writer)
            service.release = lambda *_: {"label": "FIRST", "query_id": "other", "received_frame": 1, "received_sim_time_s": 1.0}
            backend = CPURequestBackend()
            runner = ThreePolicy(POLICIES[2], public_case(), writer, service, backend, enabled=True)
            self.assertEqual(runner.step(state())["reason"], "INVALID_ANSWER_RECEIPT_BINDING")
            self.assertEqual(runner.step(state(2))["reason"], "PENDING_QUESTION")
            self.assertEqual(backend.requests, [])

    def test_cpu_imports_have_no_vehicle_or_model_dependencies(self):
        self.assertNotIn("carla", sys.modules)
        self.assertNotIn("torch", sys.modules)
        self.assertNotIn("transformers", sys.modules)


if __name__ == "__main__":
    unittest.main()
