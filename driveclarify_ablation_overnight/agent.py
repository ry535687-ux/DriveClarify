"""Two real-driving arms differing at the task-relation seam only.

Method candidate forwards are new common plumbing. Native tick/PID/planner and
the frozen ASK, answer binding, replan and temporal memory owners are delegated.
"""
from dataclasses import asdict
import json
import os
from pathlib import Path
from types import SimpleNamespace
import time

from driveclarify_clear_passthrough_v11.contracts import ConsequenceDecision, PolicyAction
from driveclarify_clear_passthrough_v11.simlingo_agent import _atomic_json, _input_frame
from driveclarify_rq1_v2.consequence import AmbiguityStatus, GateAction, consequence_gate
from driveclarify_rq1_v2.simlingo_agent import DriveClarifyRQ1V2SimLingoAgent
from driveclarify_rq3.simlingo_agent import DriveClarifyRQ3SimLingoAgent, _field
from driveclarify_rq3_paired_v2.runtime_common import PairedObservationMixin
from driveclarify_official_dreaming_adapter.adapter import OfficialDreamingCandidateForwardProvider
from driveclarify_task_relation_ablation_dev.relation import MetricConfig, compare_trajectories, unknown
from driveclarify_ablation_overnight.candidates import context_digest, timed_speed_waypoints


class TrajectoryRelationSeam(DriveClarifyRQ1V2SimLingoAgent):
    def _evaluate_consequences(self, routes):
        # ID binds observations to slots; no language, map, signature or truth
        # enters compare_trajectories. Public navigation keeps its own routes.
        plans = [self._abl_timed_by_id.get(r.candidate.candidate_id) for r in routes]
        result = (compare_trajectories(plans[0], plans[1], self._abl_metric)
                  if len(plans) == 2 else unknown('EXACTLY_TWO_CANDIDATES_REQUIRED'))
        self._abl_relation = result.relation.value
        self._abl_relation_details = asdict(result)
        self._abl_relation_details['relation'] = result.relation.value
        gate = consequence_gate(AmbiguityStatus.AMBIGUOUS, result.relation,
                                deterministic_candidate_id=routes[0].candidate.candidate_id if routes else None)
        if gate.action is GateAction.ACT:
            return ConsequenceDecision(PolicyAction.ACT, gate.selected_candidate_id, None, gate.reason_codes)
        if gate.action is GateAction.ASK:
            return ConsequenceDecision(PolicyAction.ASK, None,
                'Which of the two grounded task interpretations did you mean?', gate.reason_codes)
        return ConsequenceDecision(PolicyAction.WAIT, None, None, gate.reason_codes)


class FullBase(DriveClarifyRQ3SimLingoAgent):
    pass


class TrajectoryBase(DriveClarifyRQ3SimLingoAgent, TrajectoryRelationSeam):
    pass


class CommonCandidateMixin:
    def setup(self, path_to_conf_file, route_index=None, traffic_manager=None):
        # Separate evaluator processes reset all state. Seed every model-side
        # generator as well as CARLA's traffic manager, identically in both arms.
        import random
        import numpy as np
        import torch
        seed = int(os.environ['DRIVECLARIFY_ABLATION_SEED'])
        random.seed(seed)
        np.random.seed(seed % (2 ** 32))
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        self._abl_candidate_active = False
        self._abl_method_forward_count = 0
        self._abl_candidate_attempt_count = 0
        self._abl_timed_by_id = {}
        self._abl_context_frame = None
        self._abl_context_time = None
        self._abl_context_speed = None
        self._abl_relation = None
        self._abl_relation_details = None
        super().setup(path_to_conf_file, route_index=route_index, traffic_manager=traffic_manager)
        cfg = self._v11_config['ablation']
        self._abl_arm = cfg['configuration_id']
        self._abl_metric = MetricConfig(**cfg['metric'])
        self._abl_provider = OfficialDreamingCandidateForwardProvider(self)
        _atomic_json(self._output / 'ABL_RUNTIME_CONFIG.json', self._v11_config)

    def _v2_observe_forward(self, module, inputs, output):
        if self._abl_candidate_active:
            self._abl_method_forward_count += 1
            return None
        return super()._v2_observe_forward(module, inputs, output)

    def tick(self, input_data):
        # Preserve original ordering: supervision, then exactly one native tick.
        # During supervision DrivingInput belongs to the preceding sensor tick.
        # Record that actual frame/time instead of relabelling it current.
        result = super().tick(input_data)
        self._abl_context_frame = int(_input_frame(input_data))
        self._abl_context_time = self._simulation_time()
        speed = self.DrivingInput.get('vehicle_speed')
        self._abl_context_speed = float(speed.detach().cpu().reshape(-1)[0]) if hasattr(speed, 'detach') else float(speed)
        return result

    def run_step(self, input_data, timestamp, sensors=None):
        control = super().run_step(input_data, timestamp, sensors=sensors)
        # Observe the returned object, no PID/model/planner call and no mutation.
        row = {'frame': self._v2_last_frame, 'simulation_time_s': self._simulation_time(),
               'timestamp_argument': float(timestamp), 'steer': float(control.steer),
               'throttle': float(control.throttle), 'brake': float(control.brake),
               'hand_brake': bool(control.hand_brake), 'reverse': bool(control.reverse),
               'supervisor_status': getattr(self, '_status', None),
               'policy_decision': getattr(self, '_decision', None),
               'native_model_forward_count': self._v2_model_forward_count,
               'method_candidate_forward_count': self._abl_method_forward_count,
               'control_return_count': self._v2_control_return_count,
               'observer_control_writes': 0}
        with (self._output / 'ABL_ACTUAL_CONTROL_TIMELINE.jsonl').open('a') as stream:
            stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + '\n')
        return control

    def _bind_candidate_route(self, candidate):
        bound = super()._bind_candidate_route(candidate)
        started = time.monotonic()
        self._abl_candidate_attempt_count += 1
        slot = next(i for i, row in enumerate(self._method_input['alternatives'])
                    if row['candidate_id'] == candidate.candidate_id)
        row = {'configuration_id': self._abl_arm, 'candidate_id': candidate.candidate_id,
               'slot': slot, 'supervision_frame': self._latest_supervision_frame,
               'source_sensor_frame': self._abl_context_frame,
               'source_sensor_time_s': self._abl_context_time,
               'method_forward': True, 'diagnostic_forward': False}
        self._abl_timed_by_id[candidate.candidate_id] = None
        digest = None
        try:
            if self._abl_context_frame is None:
                raise ValueError('NATIVE_SENSOR_CONTEXT_UNAVAILABLE')
            digest = context_digest(self.DrivingInput)
            ep = SimpleNamespace(ego_state=SimpleNamespace(speed_mps=self._abl_context_speed),
                vision_observation=SimpleNamespace(observation_id='native-sensor-' + str(self._abl_context_frame),
                                                   frame_id=self._abl_context_frame))
            interpretation = SimpleNamespace(candidate_id=candidate.candidate_id,
                interpretation_id=candidate.candidate_id, prompt_text=candidate.description)
            self._abl_candidate_active = True
            output = self._abl_provider(ep, interpretation)
            after = context_digest(self.DrivingInput)
            if after != digest:
                raise RuntimeError('CANDIDATE_MUTATED_NONLANGUAGE_CONTEXT')
            plan = timed_speed_waypoints(output.plan.speed, slot=slot,
                frame=self._abl_context_frame, simulation_time_s=self._abl_context_time,
                digest=digest, data_save_freq=self.config.data_save_freq,
                carla_fps=self.config.carla_fps, wp_dilation=self.config.wp_dilation,
                age_s=self._simulation_time() - self._abl_context_time)
            self._abl_timed_by_id[candidate.candidate_id] = plan
            row.update(valid=True, timed_trajectory=asdict(plan),
                       forward_evidence=output.forward_evidence, context_unchanged=True)
        except Exception as exc:
            row.update(valid=False, error=repr(exc), timed_trajectory=None)
            # Invalid method candidates are UNKNOWN, never fabricated safe plans.
            # A context mutation is an integrity defect and must stop this episode.
            if 'MUTATED_NONLANGUAGE_CONTEXT' in str(exc):
                _atomic_json(self._output / 'ABL_FATAL_INTEGRITY.json', row)
                raise
        finally:
            self._abl_candidate_active = False
            if digest is not None and context_digest(self.DrivingInput) != digest:
                row.update(valid=False, error='CANDIDATE_MUTATED_NONLANGUAGE_CONTEXT',
                           timed_trajectory=None, context_unchanged=False)
                self._abl_timed_by_id[candidate.candidate_id] = None
                _atomic_json(self._output / 'ABL_FATAL_INTEGRITY.json', row)
                raise RuntimeError('CANDIDATE_MUTATED_NONLANGUAGE_CONTEXT')
            row['wall_s'] = time.monotonic() - started
            row['method_forward_count_total'] = self._abl_method_forward_count
            with (self._output / 'ABL_CANDIDATE_TIMELINE.jsonl').open('a') as stream:
                stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + '\n')
        # Do not pass timed trajectories into the public replan preview checker:
        # the original CandidateRoute preview remains None in BOTH arms.
        return bound

    def _runtime_evidence_fields(self, routes, now_s, frame):
        fields = super()._runtime_evidence_fields(routes, now_s, frame)
        # RQ3 calls this only after the arm's relation requested ASK.  These are
        # consequences of that result, never a second full task comparison.
        if self._abl_arm == 'ABL_TRAJ_ONLY':
            if self._abl_relation != 'TASK_CRITICAL':
                raise RuntimeError('TRAJECTORY_DOWNSTREAM_RELATION_MISMATCH')
            for key, value in {
                'E4_FUTURE_OBLIGATION_RELATION': {'relation': 'DIVERGENT', 'authorization_eligible': True,
                    'obligation_digests': [p.nonlanguage_context_sha256 for p in self._abl_timed_by_id.values() if p]},
                'E6_CANDIDATE_CONSEQUENCE_DIVERGENCE': {'material_divergence': True, 'candidate_relationship': 'DIVERGENT'},
                'E9_ANSWER_CHANGES_ACTION': {'answer_changes_next_meaningful_decision': True},
            }.items():
                fields[key] = _field(key, value, now_s, frame, 'ABL_TRAJ_ONLY_RELATION_OUTPUT')
        return fields

    def _evaluate_consequences(self, routes):
        decision = super()._evaluate_consequences(routes)
        if self._abl_arm == 'ABL_FULL':
            receipt = json.loads((self._output / 'RQ1_V2_CONSEQUENCE_DECISION_RECEIPT.json').read_text())
            self._abl_relation = receipt['comparison']['relation']
            self._abl_relation_details = receipt['comparison']
        plans = [self._abl_timed_by_id.get(r.candidate.candidate_id) for r in routes]
        metric = compare_trajectories(plans[0], plans[1], self._abl_metric) if len(plans) == 2 else unknown('CANDIDATE_COUNT')
        row = {'configuration_id': self._abl_arm, 'simulation_time_s': self._simulation_time(),
               'frame': self._latest_supervision_frame, 'relation': self._abl_relation,
               'relation_details': self._abl_relation_details,
               'requested_action_after_joint_timing': decision.action.value,
               'question_requested': decision.question, 'actual_emitted_question': None,
               'emission_authority': 'DURABLE_ASK_WRITER_SEPARATE_RECEIPT',
               'trajectory_metric_m': metric.max_aligned_distance_m,
               'trajectory_metric_reason': metric.reason}
        with (self._output / 'ABL_DECISION_TIMELINE.jsonl').open('a') as stream:
            stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + '\n')
        return decision


_config = json.loads(Path(os.environ['DRIVECLARIFY_V11_CONFIG']).read_text())
_arm = _config['ablation']['configuration_id']
if _arm not in ('ABL_FULL', 'ABL_TRAJ_ONLY'):
    raise RuntimeError('UNKNOWN_ABLATION_CONFIGURATION')
if _arm == 'ABL_TRAJ_ONLY':
    # Runtime config carries only stable identity tokens for RQ3 memory binding.
    # Scientific task signatures are absent even before the model is loaded.
    for _signature in _config['method_input']['task_signatures']:
        if set(_signature) != {'candidate_id', 'binding_id'}:
            raise RuntimeError('TRAJECTORY_RUNTIME_TASK_SIGNATURE_LEAK')


class OvernightAgent(CommonCandidateMixin, PairedObservationMixin,
                     FullBase if _arm == 'ABL_FULL' else TrajectoryBase):
    pass


def get_entry_point():
    return 'OvernightAgent'
