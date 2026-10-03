"""runtime.native binding implementation."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from driveclarify.runtime.answer_binding import AnswerBoundExecution


class InstructionBindingMixin:
    """两个策略共用答案到模型指令的路径；不自行调用forward/PID。

    仅用于新的开发配置。旧运行的原始指令检查计数仍照原义保留；新增
    PAPER_MODEL_INPUTS记录验证解析后的指令，不使用旧scoring.py裁定新运行。
    """

    def setup(self, path_to_conf_file, route_index=None, traffic_manager=None):
        config=json.loads(Path(path_to_conf_file).read_text())
        protocol=config.get('pairing', {})
        if protocol.get('schema')!='driveclarify.pairing.v1':
            raise ValueError('NEW_DEVELOPMENT_PROTOCOL_REQUIRED')
        if protocol.get('policy') not in {'NEVER_CLARIFY','DRIVECLARIFY'}:
            raise ValueError('PAIRED_POLICY_INVALID')
        if config.get('mode')!='DRIVECLARIFY':
            raise ValueError('BOTH_POLICIES_REQUIRE_THE_SAME_EXECUTION_ENTRY')
        self._binding_policy=protocol['policy']
        public=config['method_input']
        resolved=public['resolved_instructions']
        if set(resolved)!={r['candidate_id'] for r in public['alternatives']}:
            raise ValueError('PUBLIC_INSTRUCTION_CANDIDATE_IDS_DIFFER')
        self._binding_binding=AnswerBoundExecution(public['instruction'],resolved)
        self._binding_query_registered=False
        self._binding_answer=None
        self._binding_direct_choice=None
        self._binding_activated=False
        self._binding_observation_id=None
        self._binding_observation_frame=None
        super().setup(path_to_conf_file,route_index=route_index,traffic_manager=traffic_manager)

    def _evaluate_consequences(self,routes):
        if self._binding_policy=='NEVER_CLARIFY':
            from driveclarify.native.contracts import ConsequenceDecision,PolicyAction
            decision=ConsequenceDecision(action=PolicyAction.ACT,
                selected_candidate_id=routes[0].candidate.candidate_id,question=None,
                reason_codes=('PUBLIC_RANK_ONE_NEVER_CLARIFY',))
        else:
            decision=super()._evaluate_consequences(routes)
        if decision.action.value=='ACT' and self._binding_direct_choice is None:
            self._binding_binding.select_without_query(decision.selected_candidate_id,int(self._latest_supervision_frame))
            self._binding_direct_choice=decision.selected_candidate_id
        return decision

    def _read_passenger_answer(self,receipt):
        if self._binding_policy=='NEVER_CLARIFY':
            raise RuntimeError('NEVER_CLARIFY_MUST_NOT_READ_ANSWER')
        frame=int(self._latest_supervision_frame)
        if not self._binding_query_registered:
            self._binding_binding.begin_query(receipt.query_id,frame,durable=receipt.durable)
            self._binding_query_registered=True
        if self._binding_answer is None:
            answer=super()._read_passenger_answer(receipt)
            if answer is None:return None
            self._binding_binding.receive_answer(answer.query_id,answer.selected_candidate_id,frame)
            self._binding_answer=answer
            self._write_binding_event('ANSWER_RECEIVED',{'query_id':answer.query_id,
                'candidate_id':answer.selected_candidate_id,'frame':frame})
        # 延后一份真实传感器观测；不在答案所在帧重用刚收到的图像。
        request=self._binding_binding.prepare(self._binding_observation_id,frame)
        return self._binding_answer if request is not None else None

    def _activate_binding_instruction(self,navigation_status):
        if self._binding_activated:return
        request=self._binding_binding.prepare(self._binding_observation_id,self._binding_observation_frame)
        if request is None:raise RuntimeError('FRESH_RESOLVED_EXECUTION_REQUEST_NOT_READY')
        instruction=self._binding_binding.activate(request,navigation_committed=True,
            current_observation_id=self._binding_observation_id,current_frame=self._binding_observation_frame)
        self.custom_prompt=instruction
        self._binding_activated=True
        self._write_binding_event('INSTRUCTION_BOUND',{'request':asdict(request),
            'navigation_status':navigation_status,'no_model_call_in_binding':True})

    def _commit_resolved_route(self,route):
        # 先通过原来的可接受性检查和原生owner事务；失败不激活新指令。
        result=super()._commit_resolved_route(route)
        if result.get('committed') is not True:
            raise RuntimeError('NATIVE_OWNER_DID_NOT_COMMIT')
        self._activate_binding_instruction('NEW_ROUTE_INSTALLATION')
        return result

    def _advance_supervision(self,frame):
        super()._advance_supervision(frame)
        result=self._supervisor_result
        if result is not None and result.status=='AMBIGUOUS_RESOLVED_NAVIGATION_ALREADY_AUTHORITATIVE':
            self._activate_binding_instruction('EXISTING_NAVIGATION_MATCHES_SELECTED_CANDIDATE')

    def _observed_observe_forward(self,module,inputs,output):
        # 原检查仍记录原始指令是否改变；不能把这个旧计数称作新协议的完整性错误。
        super()._observed_observe_forward(module,inputs,output)
        expected=self._binding_binding.active_instruction
        actual_prompt=self.prompt
        languages=tuple(inputs[0].prompt.language_string)
        if not actual_prompt.endswith(expected) or not languages or not all(actual_prompt in p for p in languages):
            raise RuntimeError('ACTUAL_NATIVE_MODEL_INPUT_DOES_NOT_CONTAIN_BOUND_PROMPT')
        actual_instruction=actual_prompt[-len(expected):]
        self._binding_binding.verify_model_input(observation_id=self._binding_observation_id,
            observation_frame=self._binding_observation_frame,instruction=actual_instruction)
        self._write_binding_event('MODEL_INPUT',{'frame':self._binding_observation_frame,
            'observation_id':self._binding_observation_id,'instruction':actual_instruction,
            'native_prompt':actual_prompt,'native_language_sha256':[
                hashlib.sha256(p.encode()).hexdigest() for p in languages],
            'model_forward_count':self._observed_model_forward_count,'binding_active':self._binding_activated})

    def run_step(self,input_data,timestamp,sensors=None):
        frames={int(v[0]) for v in input_data.values()}
        if len(frames)!=1:raise RuntimeError('PAPER_SENSOR_FRAMES_NOT_SYNCHRONIZED')
        self._binding_observation_frame=next(iter(frames))
        self._binding_observation_id=f"{self._native_config['run_id']}:sensor-frame:{self._binding_observation_frame}"
        return super().run_step(input_data,timestamp,sensors=sensors)

    def _write_binding_event(self,event,payload):
        with (Path(self._output)/'EXECUTION_BINDING.jsonl').open('a') as stream:
            stream.write(json.dumps({'event':event,**payload},ensure_ascii=False,allow_nan=False)+'\n')
