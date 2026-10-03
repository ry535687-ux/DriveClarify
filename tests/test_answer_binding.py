"""答案必须落到最新模型输入；与原冻结运行和CARLA均无副作用。"""
import unittest
from dataclasses import replace
from driveclarify.runtime.answer_binding import AnswerBoundExecution


class AnswerBindingTests(unittest.TestCase):
    def controller(self):
        return AnswerBoundExecution("Use the bay.", {"A": "Use the near bay.", "B": "Use the far bay."})

    def answered(self):
        state = self.controller()
        state.begin_query("q1", 10, durable=True)
        state.receive_answer("q1", "B", 11)
        return state

    def test_answer_requires_strictly_later_observation(self):
        state = self.answered()
        self.assertIsNone(state.prepare("f11", 11))
        self.assertEqual(state.active_instruction, "Use the bay.")
        request = state.prepare("f12", 12)
        self.assertEqual(request.instruction, "Use the far bay.")
        self.assertEqual(state.active_instruction, "Use the bay.")
        state.activate(request, navigation_committed=True, current_observation_id="f12", current_frame=12)
        state.verify_model_input(observation_id="f12", observation_frame=12, instruction="Use the far bay.")

    def test_failed_install_keeps_original_instruction(self):
        state = self.answered()
        with self.assertRaisesRegex(ValueError, "NOT_COMMITTED"):
            state.activate(state.prepare("f12", 12), navigation_committed=False,
                           current_observation_id="f12", current_frame=12)
        self.assertEqual(state.active_instruction, "Use the bay.")

    def test_wrong_query_and_unknown_candidate_rejected(self):
        state = self.controller()
        with self.assertRaises(ValueError): state.receive_answer("q1", "B", 11)
        state.begin_query("q1", 10, durable=True)
        with self.assertRaises(ValueError): state.receive_answer("q2", "B", 11)
        with self.assertRaises(ValueError): state.receive_answer("q1", "C", 11)
        self.assertIsNone(state.prepare("f12", 12))

    def test_query_cannot_be_bypassed_or_answer_replaced(self):
        state = self.answered()
        with self.assertRaises(ValueError): state.select_without_query("A", 12)
        with self.assertRaises(ValueError): state.receive_answer("q1", "A", 12)
        self.assertEqual(state.prepare("f12", 12).candidate_id, "B")

    def test_stale_and_tampered_requests_rejected(self):
        state = self.answered(); request = state.prepare("f12", 12)
        with self.assertRaises(ValueError):
            state.activate(request, navigation_committed=True, current_observation_id="f13", current_frame=13)
        with self.assertRaises(ValueError):
            state.activate(replace(request, instruction="Use another road."), navigation_committed=True,
                           current_observation_id="f12", current_frame=12)
        self.assertEqual(state.active_instruction, "Use the bay.")

    def test_direct_baseline_and_equivalent_tasks_share_the_interface(self):
        for instructions, selected, expected in [
            ({"A": "Take A.", "B": "Take B."}, "A", "Take A."),
            ({"A": "Use the shared bay.", "B": "Use the shared bay."}, "B", "Use the shared bay.")]:
            state = AnswerBoundExecution("Ambiguous task.", instructions)
            state.select_without_query(selected, 10)
            request = state.prepare("f10", 10)
            self.assertIsNone(request.answer_frame)
            self.assertEqual(state.activate(request, navigation_committed=True,
                                           current_observation_id="f10", current_frame=10), expected)

    def test_actual_input_must_match_activation(self):
        state = self.answered(); request = state.prepare("f12", 12)
        state.activate(request, navigation_committed=True, current_observation_id="f12", current_frame=12)
        for frame, text in [(11, "Use the far bay."), (12, "Use the bay.")]:
            with self.assertRaises(ValueError):
                state.verify_model_input(observation_id=f"f{frame}", observation_frame=frame, instruction=text)


if __name__ == "__main__": unittest.main()
