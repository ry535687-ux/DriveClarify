"""STEP3A 手写合同回归；不把异常探针或合成几何当历史科学数据。"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from experiments.driveclarify_three_policy_dev import policy as p
from experiments.driveclarify_three_policy_dev.evaluator import evaluate_trace
from experiments.driveclarify_three_policy_dev.tests.fixtures import (
    COVERAGE, TRUTH, contract, public_case, safety, state, trace,
)


class PolicyBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.counter = 0

    def runner(self, name, public, answer_path=None):
        self.counter += 1
        writer = p.DurableQuestions(self.directory / str(self.counter))
        service = (p.FileAnswerService(answer_path, writer) if answer_path is not None
                   else p.ControlledAnswerService("FIRST", writer))
        return p.ThreePolicy(name, public, writer, service, p.CPURequestBackend(), enabled=True)

    def at(self, runner, now, frame=1):
        value = state(frame)
        value["sim_time_s"] = now
        return runner.step(value)

    def test_r1_all_clear_policies_unknown_before_after_and_exact_reserve(self):
        for policy in p.POLICIES:
            for name, now, changes in (("null_deadline", 1, None), ("null_budget", 1, {"budget_s": None}),
                ("null_clock", 1, {"anchor_sim_time_s": None}), ("null_execution_reserve", 1, {"execution_reserve_s": None}),
                ("before_start", 1, {"anchor_sim_time_s": 2}), ("after_deadline", 11, {}),
                ("exact_deadline", 10, {}), ("exact_execution_reserve", 9, {})):
                with self.subTest(policy=policy, case=name):
                    public = public_case(clear=True)
                    if changes is None:
                        public["deadline"] = None
                    else:
                        public["deadline"].update(changes)
                    runner = self.runner(policy, public)
                    result = self.at(runner, now)
                    self.assertEqual(result["action"], "WAIT")
                    self.assertEqual(result["authority"], "PRESERVE_EXISTING_AUTHORITY")
                    self.assertFalse(result["physical_holding"])
                    self.assertFalse(runner.done)
                    self.assertEqual(runner.backend.requests, [])

    def test_r1_clear_equivalent_and_divergent_direct_routes_pay_only_execution(self):
        # 手写合同：deadline=10, execution=1, interaction=1。t=8.5 可执行而不可问答。
        for policy, clear, equivalent in [(name, True, False) for name in p.POLICIES] + [
            (p.POLICIES[0], False, False), (p.POLICIES[0], False, True), (p.POLICIES[2], False, True)]:
            public = public_case(clear=clear, equivalent=equivalent)
            result = self.at(self.runner(policy, public), 8.5)
            self.assertEqual(result["action"], "REQUEST_ROUTE")
            self.assertEqual(result["timing"]["required_budget_s"], 1.0)
            self.assertEqual(result["timing"]["deadline_sim_time_s"], 10.0)
        for policy in p.POLICIES[1:]:
            for now in (8, 8.5):
                result = self.at(self.runner(policy, public_case()), now)
                self.assertEqual(result["action"], "WAIT")
                self.assertIn("TIME_UNKNOWN_OR_EXHAUSTED", result["failed"])

    def test_r1_unknown_interaction_budget_does_not_charge_direct_execution(self):
        public = public_case()
        public["deadline"]["interaction_budget_s"] = None
        self.assertEqual(self.at(self.runner(p.POLICIES[0], public), 1)["action"], "REQUEST_ROUTE")
        for policy in p.POLICIES[1:]:
            self.assertEqual(self.at(self.runner(policy, public), 1)["action"], "WAIT")

    def test_r1_answer_after_new_state_keeps_disclosed_conservative_budget(self):
        runner = self.runner(p.POLICIES[2], public_case())
        self.assertEqual(self.at(runner, 1)["action"], "ASK")
        result = self.at(runner, 2, frame=2)
        self.assertEqual(result["action"], "REQUEST_ROUTE")
        self.assertEqual(result["timing"]["phase"], "POST_ANSWER_CONSERVATIVE")
        self.assertEqual(result["timing"]["required_budget_s"], 2.0)
        self.assertEqual(result["request"]["state"]["frame"], 2)
        boundary = self.runner(p.POLICIES[1], public_case())
        self.at(boundary, 1)
        self.assertEqual(self.at(boundary, 8, frame=2)["reason"], "ANSWER_TOO_LATE_OR_TIME_UNKNOWN")
        self.assertEqual(boundary.backend.requests, [])

    def test_r1_existing_request_not_revoked_or_reissued_after_deadline(self):
        runner = self.runner(p.POLICIES[0], public_case())
        self.assertEqual(self.at(runner, 0)["action"], "REQUEST_ROUTE")
        result = self.at(runner, 11, frame=2)
        self.assertEqual(result["reason"], "REQUEST_ALREADY_ISSUED")
        self.assertEqual(len(runner.backend.requests), 1)
        self.assertEqual(result["authority"], "PRESERVE_EXISTING_AUTHORITY")

    def test_r2_malformed_containers_never_reach_relation_or_backend(self):
        cases = [None, []]
        for field, value in (("candidates", None), ("candidates", [None, None]), ("checks", None),
                             ("question", []), ("deadline", [])):
            public = public_case()
            public[field] = value
            cases.append(public)
        for policy in p.POLICIES:
            for public in cases:
                runner = self.runner(policy, public)
                with patch.object(p, "relation", side_effect=AssertionError("格式非法仍进入关系函数")):
                    result = runner.step(state())
                self.assertEqual(result["reason"], "INVALID_PUBLIC_STRUCTURE")
                self.assertTrue(result["errors"])
                self.assertEqual(runner.backend.requests, [])
                self.assertEqual(runner.query_count, 0)

    def test_r2_task_missing_is_unknown_and_immediate_query_gate_stays_independent(self):
        public = public_case()
        public["candidates"][0]["task_signature"] = None
        self.assertEqual(p.public_structure_errors(public), [])
        self.assertEqual(p.relation(public), "UNKNOWN")
        self.assertEqual(self.runner(p.POLICIES[1], public).step(state())["action"], "ASK")
        self.assertEqual(self.runner(p.POLICIES[2], public).step(state())["reason"], "TASK_EVIDENCE_UNKNOWN")
        public["checks"]["execution_constraints"]["provenance"] = []
        self.assertEqual(self.runner(p.POLICIES[1], public).step(state())["action"], "WAIT")

    def test_r3_file_failures_are_durable_and_never_retried_or_answered(self):
        for name, content, expected in (("missing", None, "ANSWER_FILE_MISSING"),
            ("directory", None, "ANSWER_FILE_IO_ERROR"), ("bad_json", "{", "ANSWER_FILE_DECODE_ERROR"),
            ("bad_utf8", b"\xff", "ANSWER_FILE_DECODE_ERROR"), ("array", "[]", "ANSWER_FORMAT_INVALID"),
            ("missing_label", "{}", "ANSWER_FORMAT_INVALID"), ("empty_label", '{"answer_label":""}', "ANSWER_FORMAT_INVALID"),
            ("unknown_label", '{"answer_label":"THIRD"}', "INVALID_ANSWER_RECEIPT_BINDING")):
            answer = self.directory/(name+".json")
            if name == "directory":
                answer.mkdir()
            elif isinstance(content, bytes):
                answer.write_bytes(content)
            elif content is not None:
                answer.write_text(content)
            for policy in p.POLICIES[1:]:
                with self.subTest(error=name, policy=policy):
                    runner = self.runner(policy, public_case(), answer)
                    result = runner.step(state())
                    self.assertEqual(result["reason"], expected)
                    self.assertTrue(result["question_written"])
                    self.assertEqual(result["query_count"], 1)
                    persisted = json.loads(Path(result["durable_failure_receipt"]["path"]).read_text())
                    self.assertEqual(persisted["reason"], expected)
                    self.assertEqual(persisted["question_receipt"]["query_id"], runner.receipt["query_id"])
                    self.assertEqual(persisted["backend_request_count"], 0)
                    runner.step(state(2))
                    self.assertEqual(runner.service.read_attempt_count, 1)
                    self.assertEqual(runner.query_count, 1)
                    self.assertIsNone(runner.answer)
                    self.assertEqual(runner.backend.requests, [])

    def test_r3_file_service_valid_answer_still_enters_fresh_common_request(self):
        answer = self.directory/"valid.json"
        answer.write_text('{"answer_label":"SECOND"}')
        runner = self.runner(p.POLICIES[1], public_case(), answer)
        self.assertEqual(runner.step(state())["action"], "ASK")
        self.assertEqual(runner.step(state(2))["request"]["candidate_id"], "B")
        self.assertIsNone(runner.service_failure)

    def test_r3_cli_missing_file_persists_structured_outcome_with_controlled_exit(self):
        public_path, states_path = self.directory/"public.json", self.directory/"states.json"
        public_path.write_text(json.dumps(public_case()))
        states_path.write_text(json.dumps([state(), state(2)]))
        result = subprocess.run([sys.executable, "-B", "-m", "experiments.driveclarify_three_policy_dev", "--enable-cpu-dev", "policy",
            "--public", str(public_path), "--states", str(states_path), "--policy", p.POLICIES[2],
            "--receipts", str(self.directory/"cli"), "--answer-file", str(self.directory/"missing")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 3)
        self.assertNotIn("Traceback", result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["query_count"], 1)
        self.assertEqual(output["answer_reads"], 0)
        self.assertEqual(output["backend_requests"], [])
        failure = output["service_failure"]
        self.assertEqual(failure["reason"], "ANSWER_FILE_MISSING")
        self.assertTrue(Path(failure["durable_failure_receipt"]["path"]).exists())

    def test_prepared_clear_a_b_are_explicit_and_blocked_without_runtime_evidence(self):
        configs = Path(__file__).resolve().parents[1]/"configs"
        for candidate in ("A", "B"):
            public = json.loads((configs/("STEP3A_CLEAR_TASK_"+candidate+".public.json")).read_text())
            self.assertEqual(public["ambiguity"], "CLEAR")
            self.assertEqual(public["clear_candidate_id"], candidate)
            self.assertEqual([row["id"] for row in public["candidates"]], [candidate])
            self.assertIsNone(public["question"])
            self.assertEqual(public["query_budget"], 0)
            self.assertIsNone(public["deadline"]["budget_s"])
            for policy in p.POLICIES:
                runner = self.runner(policy, public)
                self.assertEqual(runner.step(state())["action"], "WAIT")
                self.assertEqual(runner.query_count, 0)
                self.assertEqual(runner.service.read_count, 0)
                self.assertEqual(runner.backend.requests, [])


class EvaluationContractTests(unittest.TestCase):
    def evaluate(self, points, *, cadence=None):
        task = contract()
        task["gate_boundary_rule"] = "NEGATIVE_TO_NONNEGATIVE_CLOSED_BOUNDARY"
        if cadence is not None:
            task["recorder_cadence"] = cadence
        samples = [{"frame": i, "sim_time_s": i*.05, "x": x, "y": 2} for i, x in enumerate(points)]
        return evaluate_trace(task, samples, TRUTH, COVERAGE, safety())

    def test_b1_touch_then_retreat_counts_one_closed_boundary_arrival(self):
        result = self.evaluate([0, 1, 0])
        self.assertEqual([event["kind"] for event in result["events"]], ["entry"])
        self.assertEqual(result["events"][0]["fraction"], 1.0)
        self.assertFalse(result["task_complete"])
        self.assertEqual(result["gate_boundary_rule"], "NEGATIVE_TO_NONNEGATIVE_CLOSED_BOUNDARY")

    def test_b1_touch_then_enter_does_not_duplicate_event(self):
        result = self.evaluate([0, 1, 2])
        self.assertEqual([event["kind"] for event in result["events"]], ["entry"])

    def test_b1_reverse_touch_does_not_count_forward_arrival(self):
        self.assertEqual(self.evaluate([2, 1, 0])["events"], [])

    def test_b1_task_end_is_closed_boundary_arrival_and_fraction_sort_stays(self):
        result = self.evaluate([0, 4])
        self.assertEqual([event["kind"] for event in result["events"]], ["entry", "exit", "end"])
        self.assertEqual([event["fraction"] for event in result["events"]], [.25, .75, 1.0])
        self.assertTrue(result["correct_ordered_gate_arrival_observed"])
        self.assertTrue(result["task_complete"])

    def fixed_cadence(self):
        return {"binding": "FIXED_STEP_RECORDER_CONTRACT", "recorder_contract_id": "SYNTHETIC_FIXTURE_50MS",
                "frame_period_s": .05, "tolerance_s": 1e-9,
                "source": "本测试显式生成 sim_time_s = frame * 0.05；仅合成记录器合同"}

    def test_b2_unbound_does_not_claim_clock_consistency(self):
        result = self.evaluate([0, 4])
        self.assertEqual(result["cadence_check"]["contract"]["binding"], "UNBOUND")
        self.assertEqual(result["cadence_check"]["checked_intervals"], 0)
        self.assertIsNone(result["cadence_check"]["observed_intervals_consistent"])

    def test_b2_explicit_synthetic_recorder_contract_validates_and_detects_mismatch(self):
        good = self.evaluate([0, 2, 4], cadence=self.fixed_cadence())
        self.assertTrue(good["cadence_check"]["observed_intervals_consistent"])
        task = contract()
        task["recorder_cadence"] = self.fixed_cadence()
        samples = trace()
        samples[1]["sim_time_s"] = .075  # 单调且 frame 连续，但违背本合成 recorder 合同。
        result = evaluate_trace(task, samples, TRUTH, COVERAGE, safety())
        self.assertFalse(result["cadence_check"]["observed_intervals_consistent"])
        self.assertEqual(result["cadence_check"]["mismatch_count"], 2)
        self.assertIsNone(result["task_complete"])

    def test_b2_cadence_binding_requires_source_and_no_numbers_when_unbound(self):
        for cadence in ({"binding": "UNBOUND", "frame_period_s": .05},
                        dict(self.fixed_cadence(), source=None)):
            with self.assertRaises(ValueError):
                self.evaluate([0, 4], cadence=cadence)


if __name__ == "__main__":
    unittest.main()
