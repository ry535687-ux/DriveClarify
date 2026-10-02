"""通过既有语言与路线接口进行明确任务诊断；原生控制器唯一驾驶。"""
from dataclasses import asdict
import json
import os
import time

from srunner.scenariomanager.carla_data_provider import CarlaDataProvider
from driveclarify_clear_passthrough_v11.contracts import CandidateInterpretation, TransitionDisposition
from driveclarify_clear_passthrough_v11.simlingo_agent import _atomic_json
from driveclarify_rq3.simlingo_agent import DriveClarifyRQ3SimLingoAgent
from driveclarify_rq3_paired_v2.runtime_common import PairedObservationMixin
from .configuration import validate_config
from . import runtime


class ClearTaskAgent(PairedObservationMixin, DriveClarifyRQ3SimLingoAgent):
    def setup(self, path_to_conf_file, route_index=None, traffic_manager=None):
        import random
        import numpy as np
        import torch
        seed = int(os.environ['DRIVECLARIFY_ABLATION_SEED'])
        random.seed(seed)
        np.random.seed(seed % (2 ** 32))
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        self._diag_received = False
        self._diag_proposed = False
        self._diag_finished = False
        self._diag_transition = None
        self._diag_epoch = 0
        super().setup(path_to_conf_file, route_index=route_index, traffic_manager=traffic_manager)
        self._diag = validate_config(self._v11_config)
        self._diag_events = (self._output / 'CLEAR_TASK_EVENTS.jsonl').open('x')
        self._diag_control = (self._output / 'CLEAR_TASK_CONTROL_PLAN.jsonl').open('x')
        self._diag_language = (self._output / 'CLEAR_TASK_MODEL_INPUT.jsonl').open('x')
        self._diag_record('DIAGNOSTIC_SETUP', {
            'condition': self._diag['condition'],
            'original_instruction': self._diag['original_instruction'],
            'initial_method_instruction': self._method_input['instruction'],
            'initial_custom_prompt': self.custom_prompt,
            'runtime_contains_supplied_correct_task': True,
            'legacy_identity_true_intent_false_is_not_applicable_to_this_diagnostic': True,
            'relation_judge_invoked': False, 'ask_emitted': False, 'answer_received': False})
        if self._diag['condition'] == 'CLEAR_FROM_START':
            self._diag_received = True
            self._diag_epoch = 1
            self._diag_record('CLEAR_TASK_RECEIVED', {'source': 'SETUP_BEFORE_FIRST_NATIVE_TICK',
                              'clear_instruction': self.custom_prompt,
                              'supplied_candidate_id': self._diag['supplied_candidate_id']})
        self._diag_observe_world()

    def _diag_record(self, event, fields):
        snap = CarlaDataProvider.get_world().get_snapshot()
        row = dict(fields, event=event, frame=snap.frame,
                   simulation_time_s=snap.timestamp.elapsed_seconds,
                   wall_time_epoch=time.time(), clock='CARLA_SNAPSHOT_ELAPSED_SECONDS',
                   task_epoch=self._diag_epoch, actual_question=False, actual_answer=False)
        self._diag_events.write(json.dumps(row, sort_keys=True, allow_nan=False) + '\n')
        self._diag_events.flush()

    def _advance_supervision(self, frame):
        # This diagnostic receives an explicit task update, never a fabricated
        # ambiguity decision, question or passenger answer. Native setup's first
        # tick creates the planner/input context before a route can be installed.
        if not hasattr(self, '_diag') or not self.DrivingInput:
            return
        if not self._diag_received:
            if not self._at_gate_anchor():
                return
            self._method_input['instruction'] = self._diag['clear_instruction']
            self.custom_prompt = self._diag['clear_instruction']
            self._diag_epoch += 1
            self._diag_received = True
            self._diag_record('CLEAR_TASK_RECEIVED', {
                'source': 'ORIGINAL_TEMPLATE_ANCHOR_FIRST_REACHED',
                'clear_instruction': self.custom_prompt,
                'supplied_candidate_id': self._diag['supplied_candidate_id'],
                'anchor_xyz': self._diag['anchor_xyz'],
                'anchor_distance_m': self._diag['anchor_distance_m'],
                'anchor_is_certified_safe_window': False})
        if self._diag_finished:
            return
        self._latest_supervision_frame = int(frame)
        if self._gate_frame is None:
            self._gate_frame = int(frame)
            self._window_frame = int(frame)
        manager = self._supervisor.transition_manager
        if not self._diag_proposed:
            selected = next(row for row in self._method_input['alternatives']
                            if row['candidate_id'] == self._diag['supplied_candidate_id'])
            candidate = CandidateInterpretation(candidate_id=selected['candidate_id'],
                description=selected['description'], evidence_id=selected['evidence_id'])
            bound = self._bind_candidate_route(candidate)
            current = self._current_route(frame)
            self._diag_record('TASK_ROUTE_BINDING_PROPOSED', {
                'selected_candidate_id': candidate.candidate_id,
                'current_route': asdict(current), 'resolved_route': asdict(bound.route),
                'native_model_forwards_before_binding': self._v2_model_forward_count,
                'candidate_model_forwards': 0, 'preview_plan': None})
            self._diag_proposed = True
            assessment, transaction = manager.propose(self._hero_state(frame), current,
                                                       bound.route, preview_plan=bound.simlingo_preview_plan)
        else:
            assessment, transaction = manager.reevaluate(self._hero_state(frame))
        if assessment is None:
            self._diag_finished = True
            self._diag_record('TASK_ROUTE_NO_PENDING_TRANSITION', {})
            return
        self._diag_transition = asdict(assessment)
        self._decision = ('ACT' if transaction is not None else 'WAIT'
                          if assessment.disposition is TransitionDisposition.DEFER_COMMIT else 'FALLBACK')
        self._status = 'CLEAR_TASK_' + assessment.disposition.value
        self._diag_record('TASK_ROUTE_ASSESSMENT', {
            'assessment': asdict(assessment),
            'transaction': None if transaction is None else asdict(transaction),
            'action': self._decision, 'public_safety_thresholds_unchanged': True,
            'native_driving_continues_without_extra_control_writer': True})
        self._diag_finished = assessment.disposition is not TransitionDisposition.DEFER_COMMIT

    def _commit_resolved_route(self, route):
        result = super()._commit_resolved_route(route)
        self._diag_record('CLEAR_TASK_ROUTE_INSTALLED', {
            'route': asdict(route), 'installation_receipt': result,
            'source': 'EXPLICIT_TASK_UPDATE_NOT_PASSENGER_ANSWER',
            'legacy_rq1_answer_conditioned_filename_does_not_imply_answer': True})
        return result

    def _v2_observe_forward(self, module, inputs, output):
        result = super()._v2_observe_forward(module, inputs, output)
        if hasattr(self, '_diag_language'):
            snap = CarlaDataProvider.get_world().get_snapshot()
            row = {'frame': snap.frame, 'simulation_time_s': snap.timestamp.elapsed_seconds,
                   'task_epoch': self._diag_epoch, 'method_instruction': self._method_input['instruction'],
                   'custom_prompt': self.custom_prompt,
                   'actual_model_language': list(inputs[0].prompt.language_string),
                   'actual_model_prompt_inference': list(inputs[0].prompt_inference.language_string),
                   'prompt_and_inference_same_object': inputs[0].prompt is inputs[0].prompt_inference,
                   'native_model_forward_count': self._v2_model_forward_count,
                   'active_route_identity': self._online_route_update_owner.active_route_identity,
                   'route_consumption_receipt': self._online_route_update_owner.active_consumption_receipt(),
                   'extra_model_forward': False}
            self._diag_language.write(json.dumps(row, sort_keys=True, allow_nan=False) + '\n')
            self._diag_language.flush()
        return result

    def _diag_observe_world(self):
        world = CarlaDataProvider.get_world()
        snap = world.get_snapshot()
        runtime.OBSERVER.observe_world(world, frame=snap.frame,
            simulation_time_s=snap.timestamp.elapsed_seconds,
            ego_actor=CarlaDataProvider.get_hero_actor(),
            metadata={'task_epoch': self._diag_epoch, 'status': self._status})

    def run_step(self, input_data, timestamp, sensors=None):
        control = super().run_step(input_data, timestamp, sensors=sensors)
        snap = CarlaDataProvider.get_world().get_snapshot()
        target = self.DrivingInput.get('target_point')
        row = {'frame': snap.frame, 'simulation_time_s': snap.timestamp.elapsed_seconds,
               'timestamp_argument': float(timestamp), 'task_epoch': self._diag_epoch,
               'task_received': self._diag_received, 'task_route_proposed': self._diag_proposed,
               'action': self._decision, 'status': self._status,
               'steer': float(control.steer), 'throttle': float(control.throttle),
               'brake': float(control.brake), 'hand_brake': bool(control.hand_brake),
               'reverse': bool(control.reverse),
               'native_model_forward_count': self._v2_model_forward_count,
               'control_return_count': self._v2_control_return_count,
               'native_plan': self._pending_model_output,
               'target_point': None if target is None else target.detach().float().cpu().tolist(),
               'active_route_identity': self._online_route_update_owner.active_route_identity,
               'route_consumption_receipt': self._online_route_update_owner.active_consumption_receipt(),
               'model_calls_by_diagnostic': 0, 'PID_calls_by_diagnostic': 0,
               'planner_steps_by_diagnostic': 0, 'control_writes_by_diagnostic': 0}
        self._diag_control.write(json.dumps(row, sort_keys=True, allow_nan=False) + '\n')
        self._diag_control.flush()
        self._diag_observe_world()
        return control

    def destroy(self, results=None):
        try:
            if hasattr(self, '_diag_events'):
                self._diag_record('DIAGNOSTIC_NATIVE_TERMINATION', {
                    'task_received': self._diag_received, 'route_proposed': self._diag_proposed,
                    'route_transaction_count': self._route_transaction_count,
                    'last_assessment': self._diag_transition,
                    'native_model_forwards': self._v2_model_forward_count,
                    'native_control_returns': self._v2_control_return_count})
                self._diag_observe_world()
                runtime.OBSERVER.write_summary()
        finally:
            super().destroy(results=results)
            for name in ['_diag_events', '_diag_control', '_diag_language']:
                if hasattr(self, name):
                    getattr(self, name).close()


def get_entry_point():
    return 'ClearTaskAgent'
