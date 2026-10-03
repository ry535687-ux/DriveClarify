"""native.benchmark agent implementation."""
import ast
import functools
import hashlib
import json
import os
from pathlib import Path
import time

import torch
from agent_simlingo import LingoAgent
from srunner.scenariomanager.timer import GameTime

from driveclarify.native.activation import INTERFACE_VERSION, select_implementation


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    tmp.replace(path)


def fingerprint(value):
    if isinstance(value, torch.Tensor):
        raw = value.detach().contiguous().cpu().view(torch.uint8).numpy().tobytes()
        return {'shape': list(value.shape), 'dtype': str(value.dtype), 'sha256': hashlib.sha256(raw).hexdigest()}
    if isinstance(value, dict):
        return {str(k): fingerprint(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [fingerprint(v) for v in value]
    if value is None or isinstance(value, (str, int, bool, float)):
        return value
    return repr(value)


class ObservedNativeAgent(LingoAgent):
    """A0和A1 bypass共有：仅setup/destroy及无返回值observer hooks。"""
    def setup(self, path_to_conf_file, route_index=None):
        super().setup(path_to_conf_file, route_index=route_index)
        self._b2d_out = Path(os.environ['DC_B2D_ATTEMPT_DIR'])
        self._b2d_qualification = os.environ.get('DC_B2D_QUALIFICATION') == '1'
        self._b2d_pre = self._b2d_post = self._b2d_pid = 0
        self._b2d_samples = []
        self._b2d_violations = []
        self._b2d_order = []
        self._b2d_same_input_objects = True
        self._b2d_max_generation = 0
        # 原样提取原生 prompt 分支；在同一live状态验证字符/模式，无第二tick或forward。
        source = (Path(os.environ['WORK_DIR']) / 'team_code/agent_simlingo.py').read_text()
        cls = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == 'LingoAgent')
        tick = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'tick')
        prompt_nodes = [n for n in tick.body if isinstance(n, ast.If) and n.lineno in (699, 704, 711)]
        # 以源码片段而非行号作为兼容查找。
        prompt_nodes = [n for n in tick.body if isinstance(n, ast.If) and
                        (isinstance(n.test, ast.Attribute) and n.test.attr == 'use_cot' or
                         any(isinstance(x, ast.Attribute) and x.attr == 'custom_prompt' for x in ast.walk(n.test)) or
                         isinstance(n.test, ast.BoolOp) and any(isinstance(x, ast.Attribute) and x.attr == 'user_flag' for x in ast.walk(n.test)))]
        if len(prompt_nodes) != 3:
            raise RuntimeError('NATIVE_PROMPT_SOURCE_IDENTITY_CHANGED')
        self._b2d_prompt_code = compile(ast.fix_missing_locations(ast.Module(body=prompt_nodes, type_ignores=[])), '<native-prompt-source>', 'exec')
        self._b2d_original_pid = self.control_pid
        @functools.wraps(self._b2d_original_pid)
        def observed_pid(*args, **kwargs):
            self._b2d_pid += 1
            if self._b2d_qualification:
                self._b2d_order.append('PID')
            return self._b2d_original_pid(*args, **kwargs)
        self.control_pid = observed_pid
        self._b2d_hooks = [self.model.register_forward_pre_hook(self._b2d_before_model), self.model.register_forward_hook(self._b2d_after_model)]
        self._b2d_setup_receipt = {
            'interface_version': INTERFACE_VERSION, 'arm': os.environ['DC_B2D_ARM'],
            'DRIVECLARIFY_ACTIVE': False,
            'dispatch_mode': 'NATIVE_A0' if os.environ['DC_B2D_ARM']=='A0' else 'NO_CONTEXT_TRANSPARENT_BYPASS',
            'native_class': 'agent_simlingo.LingoAgent',
            'run_step_is_native': type(self).run_step is LingoAgent.run_step,
            'tick_is_native': type(self).tick is LingoAgent.tick,
            'class_PID_is_native': type(self).control_pid is LingoAgent.control_pid,
            'bound_PID_delegate_is_native': self._b2d_original_pid.__func__ is LingoAgent.control_pid,
            'prompt_flags_native': self.custom_prompt is None and self.user_flag is None and self.user_command is None,
            'checkpoint': self.config_path, 'route': os.environ['ROUTES'],
            'scientific_task_context_installed': False, 'old_active_entry_imported': False,
            'monitoring_shared_between_arms': True, 'monitoring_changes_simulation_time': False,
            'official_dense_route': [
                [float(t.location.x),float(t.location.y),float(t.location.z),int(option)]
                for t,option in self.org_dense_route_world_coord],
            'native_global_gps_route': [[dict(gps),int(option)] for gps,option in self._global_plan],
        }
        atomic_json(self._b2d_out/'agent_setup.json', self._b2d_setup_receipt)

    def _b2d_before_model(self, module, args):
        started_sim = GameTime.get_time()
        self._b2d_pre += 1
        obj = args[0]
        if self._b2d_qualification:
            self._b2d_order.append('MODEL_PRE')
        # 最终model边界必须是原生DrivingInput原对象字段；没有重建conditioning。
        same = all(getattr(obj, k) is v for k, v in self.DrivingInput.items())
        self._b2d_same_input_objects &= same
        if not same:
            self._b2d_violations.append('MODEL_INPUT_NOT_NATIVE_FIELD_OBJECTS')
        if self.custom_prompt is not None or self.user_flag is not None or self.user_command is not None:
            self._b2d_violations.append('NON_NATIVE_LANGUAGE_STATE')
        generation = int(self._route_planner.online_update_generation)
        self._b2d_max_generation = max(generation, self._b2d_max_generation)
        if generation != 0 or bool(obj.route_switch_active.any().item()):
            self._b2d_violations.append('UNEXPECTED_ROUTE_MUTATION')
        if self._b2d_qualification and len(self._b2d_samples) < 4:
            speed = round(self.DrivingInput['vehicle_speed'].item(), 1)
            # 原生prompt中的speed为tick局部量；取其原文前缀，避免浮点显示格式再解释。
            speed_text = self.prompt.split('Current speed: ',1)[1].split(' m/s.',1)[0]
            scope = {'self': self, 'speed': speed_text, 'prompt_tp': self.prompt_tp}
            exec(self._b2d_prompt_code, scope)
            expected_prompt = scope['prompt']
            lang = obj.prompt.language_string
            tokens = self.tokenizer(lang, padding=True, return_tensors='pt', return_offsets_mapping=True, add_special_tokens=False)['input_ids']
            token_equal = torch.equal(tokens, obj.prompt.phrase_ids.detach().cpu())
            sample = {
                'forward_index': self._b2d_pre, 'native_prompt': self.prompt, 'expected_native_prompt': expected_prompt,
                'prompt_utf8_identity': self.prompt.encode()==expected_prompt.encode(),
                'instruction_following_token_count': self.prompt.count('<INSTRUCTION_FOLLOWING>'),
                'tokenizer_input_and_final_ids_identity': token_equal,
                'final_input_fields_are_native_objects': same,
                'model_input_fingerprints': fingerprint(obj._asdict()),
                'same_sample_counterfactual_A0_A1_class_identity': select_implementation('A0', None, ObservedNativeAgent, lambda:None)[0] is select_implementation('A1', None, ObservedNativeAgent, lambda:None)[0],
                'route_generation': generation, 'simulation_time_before_observer': started_sim,
            }
            sample['simulation_time_after_observer'] = GameTime.get_time()
            self._b2d_samples.append(sample)
            if not sample['prompt_utf8_identity'] or not token_equal:
                self._b2d_violations.append('PROMPT_OR_TOKEN_IDENTITY_FAILURE')
        if GameTime.get_time() != started_sim:
            self._b2d_violations.append('OBSERVER_CHANGED_SIMULATION_CLOCK')
        # 返回None：PyTorch传入的args没有替换或修改。

    def _b2d_after_model(self, module, args, output):
        self._b2d_post += 1
        if self._b2d_qualification:
            self._b2d_order.append('MODEL_POST')

    def destroy(self, results=None):
        if hasattr(self, '_b2d_out'):
            expected_forwards = max(0, int(self.step))
            receipt = {**self._b2d_setup_receipt,
                'native_step_final': int(self.step), 'expected_model_forwards': expected_forwards,
                'model_pre_count': self._b2d_pre, 'model_post_count': self._b2d_post, 'PID_count': self._b2d_pid,
                'forward_count_contract': self._b2d_pre == self._b2d_post == self._b2d_pid == expected_forwards,
                'same_native_field_objects_all_observed_forwards': self._b2d_same_input_objects,
                'route_update_generation_max': self._b2d_max_generation,
                'model_PID_order': self._b2d_order, 'samples': self._b2d_samples, 'violations': self._b2d_violations,
                'ASK': 0, 'WAIT': 0, 'answer_events': 0, 'candidate_comparisons': 0, 'evidence_margin_decisions': 0,
                'clarification_triggered_full_replan': 0, 'nonclarification_DriveClarify_route_installation': 0,
                'nonclarification_full_replan': 0, 'initial_native_route_installation': 1,
                'direct_control_intervention': 0, 'extra_model_forwards': max(0,self._b2d_pre-expected_forwards),
                'second_control_writer': 0,
                'zero_count_evidence': 'observed native dispatch; no active object/callback installed; forwards/PID and route generation monitored',
                'metric_info_path': str(Path(self.save_path_metric)/'metric_info.json'),
                'finished_wall_epoch': time.time(),
            }
            atomic_json(self._b2d_out/'agent_terminal.json', receipt)
        return super().destroy(results=results)


def active_loader():
    from driveclarify.native.temporal_agent import TemporalSimLingoAgent
    class ActiveEvaluatorAdapter(TemporalSimLingoAgent):
        def setup(self, path_to_conf_file, route_index=None, traffic_manager=None):
            # 官方B2D给agent_config附加+save_name；冻结路径只消费原始config文件。
            path = path_to_conf_file.split('+', 1)[0]
            original = os.environ.get('DC_B2D_ACTIVE_CONFIG_PATH')
            if original is not None:
                frozen_config = json.loads(Path(original).read_text())
                if Path(path).resolve() not in (Path(original).resolve(), Path(frozen_config['checkpoint']).resolve()):
                    raise RuntimeError('ACTIVE_EVALUATOR_CONFIG_BINDING_MISMATCH')
                path = original
            return super().setup(path, route_index=route_index, traffic_manager=traffic_manager)
    return ActiveEvaluatorAdapter


def get_entry_point():
    global SelectedAgent
    config = json.loads(Path(os.environ['DC_B2D_INTERFACE_CONFIG']).read_text())
    SelectedAgent, active, reasons = select_implementation(os.environ['DC_B2D_ARM'], config.get('context'), ObservedNativeAgent, active_loader)
    if active:
        # 有效上下文继续使用原完整runtime config；不创建占位签名或重写旧操作数。
        original = config.get('active_runtime_config_path') or os.environ.get('DRIVECLARIFY_RUNTIME_CONFIG')
        if original is None:
            raise RuntimeError('ACTIVE_CONTEXT_REQUIRES_EXISTING_FULL_RUNTIME_CONFIG_BINDING')
        original = str(Path(original).resolve())
        payload = Path(original).read_bytes()
        if json.loads(payload)['method_input'] != config['context']:
            raise RuntimeError('ACTIVE_CONTEXT_AND_FROZEN_CONFIG_DIFFER')
        os.environ['DC_B2D_ACTIVE_CONFIG_PATH'] = original
        os.environ['DRIVECLARIFY_RUNTIME_CONFIG'] = original
        os.environ['DRIVECLARIFY_RUNTIME_CONFIG_SHA256'] = hashlib.sha256(payload).hexdigest()
        os.environ.setdefault('DRIVECLARIFY_RUNTIME_OWNER_DIR', str(Path(os.environ['DC_B2D_ATTEMPT_DIR'])/'active_owner'))
    atomic_json(Path(os.environ['DC_B2D_ATTEMPT_DIR'])/'dispatch.json', {
        'interface_version': INTERFACE_VERSION, 'arm': os.environ['DC_B2D_ARM'],
        'DRIVECLARIFY_ACTIVE': active, 'context_absent_reasons': reasons,
        'selected_class': SelectedAgent.__module__+'.'+SelectedAgent.__qualname__,
        'context_payload_unchanged': True,
    })
    return 'SelectedAgent'
