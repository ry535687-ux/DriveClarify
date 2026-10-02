import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from experiments.driveclarify_three_policy_dev import policy as p
from experiments.driveclarify_three_policy_dev.evaluator import evaluate_trace
from experiments.driveclarify_three_policy_dev.tests.fixtures import (
    COVERAGE, TRUTH, contract, public_case, safety, state, trace,
)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sequence = 0

    def runner(self, name, public=None, answer="FIRST"):
        self.sequence += 1
        writer = p.DurableQuestions(Path(self.temp.name) / str(self.sequence))
        service = p.ControlledAnswerService(answer, writer)
        backend = p.CPURequestBackend()
        return p.ThreePolicy(name, public or public_case(), writer, service, backend, enabled=True)

    def test_default_disabled_and_cli_no_side_effects(self):
        with self.assertRaisesRegex(RuntimeError, "CPU_DEV_DISABLED"):
            p.ThreePolicy(p.POLICIES[0], public_case(), None, None, None)
        r = subprocess.run([sys.executable, "-B", "-m", "experiments.driveclarify_three_policy_dev", "policy",
                            "--public", "missing", "--states", "missing", "--policy", p.POLICIES[0],
                            "--receipts", str(Path(self.temp.name) / "disabled")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("CPU_DEV_DISABLED", r.stderr)
        self.assertFalse((Path(self.temp.name) / "disabled").exists())

    def test_clear_all_follow_public_instruction_without_ask(self):
        for policy in p.POLICIES:
            runner = self.runner(policy, public_case(clear=True), "SECOND")
            decision = runner.step(state())
            self.assertEqual(decision["request"]["candidate_id"], "A")
            self.assertEqual(runner.service.read_count, 0)
            self.assertEqual(runner.query_count, 0)

    def test_equivalent_immediate_gets_legal_answer_and_shared_backend(self):
        immediate = self.runner(p.POLICIES[1], public_case(equivalent=True), "SECOND")
        self.assertEqual(immediate.step(state())["action"], "ASK")
        self.assertEqual(immediate.service.read_count, 1)
        answered = immediate.step(state(2))["request"]
        self.assertEqual(answered["candidate_id"], "B")
        controlled = self.runner(p.POLICIES[2], public_case(equivalent=True))
        unqueried = controlled.step(state())["request"]
        self.assertEqual(controlled.query_count, 0)
        self.assertEqual(unqueried["candidate_id"], "A")
        self.assertEqual(answered["route_xy"], unqueried["route_xy"])
        self.assertEqual(answered["backend_kind"], unqueried["backend_kind"])

    def test_divergent_two_answers_both_asking_policies_same_backend(self):
        for policy in p.POLICIES[1:]:
            for label, expected in (("FIRST", "A"), ("SECOND", "B")):
                runner = self.runner(policy, answer=label)
                self.assertEqual(runner.step(state())["action"], "ASK")
                request = runner.step(state(2))["request"]
                self.assertEqual(request["candidate_id"], expected)
                self.assertIsNone(request["computed_plan"])
                self.assertIsNone(request["native_installation"])
                self.assertIsNone(request["native_first_control_adoption"])

    def test_missing_necessary_field_dominates_known_difference_near_far(self):
        for xy in ([[0, 0], [0.001, 0.001]], [[0, 0], [10000, 10000]]):
            public = public_case()
            public["candidates"][1]["route_xy"] = xy
            public["candidates"][1]["task_signature"]["task_completion_region"] = None
            # 存在已知 branch 差异也不得绕过必要字段缺失。
            self.assertEqual(p.relation(public), "UNKNOWN")
            runner = self.runner(p.POLICIES[2], public)
            self.assertEqual(runner.step(state())["reason"], "TASK_EVIDENCE_UNKNOWN")
            self.assertEqual(runner.service.read_count, 0)
            self.assertEqual(runner.backend.requests, [])

    def test_no_task_evidence_no_grounding_agreement_escape(self):
        public = public_case()
        for row in public["candidates"]:
            row["task_evidence"] = {"value": None, "provenance": "UNKNOWN", "source": "缺证据"}
            row["reference_id"] = "same_reference"
        self.assertEqual(p.relation(public), "UNKNOWN")
        # 立即询问源于歧义和共同门，不是 UNKNOWN 自动授权。
        self.assertEqual(self.runner(p.POLICIES[1], public).step(state())["action"], "ASK")
        self.assertEqual(self.runner(p.POLICIES[2], public).step(state())["action"], "WAIT")

    def test_signature_validation_reuses_live_module(self):
        self.assertEqual(p.compare_task_signatures.__module__, "driveclarify_rq1_v2.consequence")
        for bad in (None, "", 7, True):
            public = public_case()
            public["candidates"][0]["task_signature"]["task_completion_region"] = bad
            self.assertEqual(p.relation(public), "UNKNOWN")
        public = public_case()
        public["candidates"][0]["task_signature"]["certified"] = False
        self.assertEqual(p.relation(public), "UNKNOWN")

    def test_invalid_question_pending_budget_service_time_do_not_bypass_gate(self):
        for mutation, expected in (
            (lambda c: c["question"].update(answer_to_candidates={"FIRST": ["A"], "SECOND": ["A"]}), "QUESTION_MAPPING_INVALID"),
            (lambda c: c.update(query_budget=0), "QUERY_BUDGET_EXHAUSTED"),
            (lambda c: c["deadline"].update(budget_s=None), "TIME_UNKNOWN_OR_EXHAUSTED"),
            (lambda c: c["checks"]["execution_constraints"].update(value=None, provenance="UNKNOWN"), "EXECUTION_CONSTRAINTS_UNKNOWN_OR_INVALID"),
        ):
            for policy in p.POLICIES[1:]:
                public = public_case()
                mutation(public)
                runner = self.runner(policy, public)
                self.assertIn(expected, runner.step(state())["failed"])
                self.assertEqual(runner.query_count, 0)
        runner = self.runner(p.POLICIES[1])
        runner.receipt = {"external_pending": True}
        self.assertEqual(runner.step(state())["reason"], "PENDING_QUESTION")
        self.assertEqual(runner.service.read_count, 0)
        runner = self.runner(p.POLICIES[1])
        runner.service.available = False
        self.assertIn("SERVICE_UNAVAILABLE", runner.step(state())["failed"])
        runner = self.runner(p.POLICIES[2])
        self.assertIn("TIME_UNKNOWN_OR_EXHAUSTED", runner.step(state(9))["failed"])

    def test_truth_swap_ambiguous_public_default_and_preask_decision_invariant(self):
        for eq in (True, False):
            for policy in p.POLICIES:
                first = self.runner(policy, public_case(equivalent=eq), "FIRST")
                second = self.runner(policy, public_case(equivalent=eq), "SECOND")
                self.assertEqual(p.digest(first.public), p.digest(second.public))
                self.assertEqual(first.public["default_candidate_id"], "A")
                d1, d2 = first.step(state()), second.step(state())
                self.assertEqual(d1["action"], d2["action"])
                if d1["action"] == "REQUEST_ROUTE":
                    self.assertEqual(d1["request"]["candidate_id"], d2["request"]["candidate_id"])
                if d1["action"] == "ASK":
                    self.assertEqual(d1["receipt"]["query_id"], d2["receipt"]["query_id"])

    def test_hidden_input_rejected_and_no_clarification_never_reads_answer(self):
        public = public_case()
        public["hidden_truth"] = "B"
        with self.assertRaisesRegex(ValueError, "NONPUBLIC_INPUT"):
            self.runner(p.POLICIES[0], public)
        runner = self.runner(p.POLICIES[0], answer="SECOND")
        runner.service.release = lambda *_: self.fail("无澄清调用了答案服务")
        self.assertEqual(runner.step(state())["request"]["candidate_id"], "A")

    def test_answer_release_requires_actual_untampered_durable_receipt(self):
        runner = self.runner(p.POLICIES[1])
        with self.assertRaisesRegex(ValueError, "NO_VALID_DURABLE"):
            runner.service.release({"query_id": "fabricated"}, state())
        receipt = runner.writer.write(runner.public["question"], state())
        self.assertTrue(Path(receipt["path"]).is_file())
        self.assertEqual(runner.service.release(receipt, state())["label"], "FIRST")
        Path(receipt["path"]).write_text("{}")
        with self.assertRaisesRegex(ValueError, "NO_VALID_DURABLE"):
            runner.service.release(receipt, state())
        self.assertEqual(runner.service.read_count, 1)

    def test_deadline_public_anchor_not_ask_relative_and_unknown_stays_null(self):
        public = public_case()
        deadlines = []
        for ask_frame in (1, 3, 6):
            runner = self.runner(p.POLICIES[1], public)
            decision = runner.step(state(ask_frame))
            deadlines.append(decision["timing"]["deadline_sim_time_s"])
            runner.step(state(ask_frame))
            self.assertEqual(runner.query_count, 1)
        self.assertEqual(deadlines, [10.0, 10.0, 10.0])
        public["deadline"]["budget_s"] = None
        timing = p.deadline_result(public["deadline"], 1)
        self.assertIsNone(timing["deadline_sim_time_s"])
        self.assertIsNone(timing["remaining_sim_s"])
        self.assertIsNone(timing["wall_latency_s"])
        public["deadline"].update(budget_s=10, anchor_kind="FIRST_ASK")
        self.assertFalse(p.deadline_result(public["deadline"], 1)["time_ok"])

    def test_fresh_replan_after_answer_rejects_old_state_and_plan(self):
        runner = self.runner(p.POLICIES[2])
        runner.step(state())
        self.assertEqual(runner.step(state())["reason"], "WAIT_FOR_POST_ANSWER_STATE")
        with self.assertRaisesRegex(ValueError, "FRESH_STATE"):
            runner.backend.request(runner.public["candidates"][0], state(), answer=runner.answer)
        request = runner.step(state(2))["request"]
        self.assertEqual(request["state"]["frame"], 2)
        self.assertEqual(request["answer_query_id"], runner.receipt["query_id"])
        old = {"request_id": "OLD", "source_frame": 1, "source_observation_digest": p.digest(state()),
               "route_digest": p.digest(request["route_xy"])}
        self.assertFalse(runner.backend.validate_plan_binding(request, old))
        bound = dict(old, request_id=request["request_id"], source_frame=2, source_observation_digest=p.digest(state(2)))
        self.assertTrue(runner.backend.validate_plan_binding(request, bound))

    def test_late_answer_state_and_unknown_execution_preserve_authority(self):
        runner = self.runner(p.POLICIES[2])
        runner.step(state())
        result = runner.step(state(9))
        self.assertEqual(result["reason"], "ANSWER_TOO_LATE_OR_TIME_UNKNOWN")
        self.assertFalse(result["physical_holding"])
        self.assertEqual(runner.backend.requests, [])


class EvaluationTests(unittest.TestCase):
    def evaluate(self, rows, **kwargs):
        return evaluate_trace(contract(), rows, kwargs.get("truth", TRUTH),
                              kwargs.get("coverage", COVERAGE), kwargs.get("safety", safety()))

    def test_correct_order_and_explicit_end(self):
        result = self.evaluate(trace())
        self.assertEqual(result["status"], "CORRECT_COMPLETE")
        self.assertTrue(result["safe_task_complete"])
        self.assertEqual([e["kind"] for e in result["events"]], ["entry", "exit", "end"])
        self.assertFalse(self.evaluate(trace()[:-1])["task_complete"])

    def test_wrong_branch_latches_even_later_correct_completion(self):
        rows = trace(y=-2)
        rows += [{"frame": 4+i, "sim_time_s": (4+i)*.05, "x": x, "y": 2} for i, x in enumerate((0, 2, 3.5, 5))]
        result = self.evaluate(rows)
        self.assertTrue(result["wrong_irreversible_branch"])
        self.assertFalse(result["task_complete"])
        self.assertTrue(result["correct_pass_observed"])
        self.assertEqual(result["status"], "WRONG_BRANCH")

    def test_reverse_and_not_reached_are_not_completion(self):
        for rows in (trace(reverse=True), trace(y=20)):
            result = self.evaluate(rows)
            self.assertFalse(result["task_complete"])
            self.assertFalse(result["wrong_irreversible_branch"])
            self.assertEqual(result["events"], [])

    def test_missing_before_event_cannot_infer_wrong_absence_or_cross_gap(self):
        rows = trace(y=-2)
        rows[1] = None
        result = self.evaluate(rows)
        self.assertIsNone(result["wrong_irreversible_branch"])
        self.assertIsNone(result["task_complete"])
        self.assertIsNone(result["safe_task_complete"])

    def test_proven_wrong_before_missing_is_retained(self):
        rows = trace(y=-2)
        rows[2] = None
        result = self.evaluate(rows)
        self.assertTrue(result["wrong_irreversible_branch"])
        self.assertFalse(result["task_complete"])

    def test_proven_pass_before_gap_retained_but_task_unknown_without_no_wrong_evidence(self):
        result = self.evaluate(trace() + [None])
        self.assertTrue(result["correct_pass_observed"])
        self.assertIsNone(result["task_complete"])
        self.assertIsNone(result["wrong_irreversible_branch"])

    def test_numeric_frame_gap_incomplete_horizon_and_empty_record(self):
        rows = trace()
        rows[1]["frame"] = 100
        for result in (self.evaluate(rows), self.evaluate([]), self.evaluate(trace(), coverage={"start_observed": False, "end_observed": True})):
            self.assertIsNone(result["task_complete"])

    def test_safety_endpoint_identifiability_is_independent(self):
        endpoints = safety()
        endpoints["collision"] = {"event_observed": True, "coverage_complete": False}
        endpoints["offroad"] = {"event_observed": None, "coverage_complete": False}
        result = self.evaluate(trace(), safety=endpoints)
        self.assertTrue(result["task_complete"])
        self.assertFalse(result["safe_task_complete"])
        self.assertTrue(result["safety_events"]["collision"])
        self.assertIsNone(result["safety_events"]["offroad"])
        endpoints["collision"] = {"event_observed": False, "coverage_complete": True}
        self.assertIsNone(self.evaluate(trace(), safety=endpoints)["safe_task_complete"])

    def test_method_fields_cannot_determine_evaluation(self):
        for field in ("policy_id", "relation", "selected_candidate_id", "TaskDetermined", "ReplanSuccess", "RC"):
            rows = trace()
            rows[0][field] = "SUCCESS"
            with self.assertRaisesRegex(ValueError, "FORBIDS_METHOD_FIELDS"):
                self.evaluate(rows)
        self.assertFalse(self.evaluate(trace(), truth={"allowed_branches": ["RIGHT"]})["task_complete"])


if __name__ == "__main__":
    unittest.main()
