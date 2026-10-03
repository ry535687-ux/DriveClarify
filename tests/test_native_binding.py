"""在无CARLA的假原生边界上验证接缝顺序；不冒充真实驾驶验收。"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest

from driveclarify.runtime.native_binding import InstructionBindingMixin
from driveclarify.native.contracts import ConsequenceDecision,PolicyAction


class FakeNative:
    def setup(self,path_to_conf_file,route_index=None,traffic_manager=None):
        self._native_config=json.loads(Path(path_to_conf_file).read_text())
        self._method_input=self._native_config['method_input']
        self._output=Path(path_to_conf_file).parent
        self.custom_prompt=self._method_input['instruction']
        self._supervisor_result=None;self._decision_made=None
        self._observed_model_forward_count=0;self.answer_reads=0;self.commits=[]
        self.control=object();self.fail_install=False

    def _evaluate_consequences(self,routes):
        return ConsequenceDecision(action=PolicyAction.ASK,selected_candidate_id=None,
                                   question='Which bay?',reason_codes=('TEST',))

    def _read_passenger_answer(self,receipt):
        self.answer_reads+=1
        if self._latest_supervision_frame<11:return None
        return NS(query_id='q1',selected_candidate_id='B')

    def _commit_resolved_route(self,route):
        if self.fail_install:raise RuntimeError('OWNER_REJECTED')
        self.commits.append((self._latest_supervision_frame,route.route_id))
        return {'committed':True}

    def _advance_supervision(self,frame):
        self._latest_supervision_frame=frame
        if self.commits:return
        if self._decision_made is None:
            self._decision_made=self._evaluate_consequences([NS(candidate=NS(candidate_id='A')),NS(candidate=NS(candidate_id='B'))])
        decision=self._decision_made
        choice=decision.selected_candidate_id
        if decision.action is PolicyAction.ASK:
            answer=self._read_passenger_answer(NS(query_id='q1',durable=True))
            if answer is None:return
            choice=answer.selected_candidate_id
        self._commit_resolved_route(NS(route_id='candidate-'+choice+'-route'))

    def _observed_observe_forward(self,module,inputs,output):
        self._observed_model_forward_count+=1

    def run_step(self,input_data,timestamp,sensors=None):
        frame=input_data['rgb_0'][0]
        self._advance_supervision(frame)
        self.prompt='Current speed: 1.0 m/s. '+self.custom_prompt
        inputs=(NS(prompt=NS(language_string=['native template: '+self.prompt])),)
        self._observed_observe_forward(None,inputs,None)
        return self.control


class DevelopmentAgent(InstructionBindingMixin,FakeNative):pass


class NativeBindingTests(unittest.TestCase):
    def setup_agent(self,directory,policy):
        path=Path(directory)/'config.json'
        path.write_text(json.dumps({'run_id':'dev-test','mode':'DRIVECLARIFY',
            'pairing':{'schema':'driveclarify.pairing.v1','policy':policy},
            'method_input':{'instruction':'Use the bay.','alternatives':[{'candidate_id':'A'},{'candidate_id':'B'}],
                            'resolved_instructions':{'A':'Use the near bay.','B':'Use the far bay.'}}}))
        agent=DevelopmentAgent();agent.setup(str(path));return agent

    def test_answer_to_prompt_uses_a_later_native_frame(self):
        with TemporaryDirectory() as directory:
            agent=self.setup_agent(directory,'DRIVECLARIFY')
            for frame in [10,11]:
                self.assertIs(agent.run_step({'rgb_0':(frame,None)},0),agent.control)
                self.assertEqual(agent.custom_prompt,'Use the bay.')
            self.assertIs(agent.run_step({'rgb_0':(12,None)},0),agent.control)
            self.assertEqual(agent.custom_prompt,'Use the far bay.')
            self.assertEqual(agent.commits,[(12,'candidate-B-route')])
            events=[json.loads(s) for s in (Path(directory)/'EXECUTION_BINDING.jsonl').read_text().splitlines()]
            answer=next(x for x in events if x['event']=='ANSWER_RECEIVED')
            binding=next(x for x in events if x['event']=='INSTRUCTION_BOUND')
            self.assertEqual((answer['frame'],binding['request']['observation_frame']),(11,12))
            self.assertEqual(sum(x['event']=='MODEL_INPUT' for x in events),3)
            self.assertEqual(agent._observed_model_forward_count,3)

    def test_never_clarify_uses_same_binding_without_reading_answer(self):
        with TemporaryDirectory() as directory:
            agent=self.setup_agent(directory,'NEVER_CLARIFY')
            self.assertIs(agent.run_step({'rgb_0':(10,None)},0),agent.control)
            self.assertEqual(agent.custom_prompt,'Use the near bay.')
            self.assertEqual(agent.answer_reads,0)
            self.assertEqual(agent.commits,[(10,'candidate-A-route')])

    def test_native_rejection_prevents_instruction_activation(self):
        with TemporaryDirectory() as directory:
            agent=self.setup_agent(directory,'NEVER_CLARIFY');agent.fail_install=True
            with self.assertRaisesRegex(RuntimeError,'OWNER_REJECTED'):
                agent.run_step({'rgb_0':(10,None)},0)
            self.assertEqual(agent.custom_prompt,'Use the bay.')
            self.assertFalse(agent._binding_activated)

    def test_mixed_sensor_frames_rejected_before_native_step(self):
        with TemporaryDirectory() as directory:
            agent=self.setup_agent(directory,'NEVER_CLARIFY')
            with self.assertRaisesRegex(RuntimeError,'SENSOR_FRAMES'):
                agent.run_step({'rgb_0':(10,None),'gps':(9,None)},0)
            self.assertEqual(agent._observed_model_forward_count,0)


if __name__=='__main__':unittest.main()
