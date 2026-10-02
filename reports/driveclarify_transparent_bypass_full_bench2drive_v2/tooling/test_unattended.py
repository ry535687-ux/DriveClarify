"""只用隔离目录验证工程边界，不启动 CARLA 或实际正式路线。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import formal
import unattended as u
from common import save, sha, digest


def raw(path,score=0):
    save(path,{'_checkpoint':{'records':[{'route_id':'RouteScenario_1_rep0',
         'status':'Failed - Agent blocked','scores':dict(score_composed=score,score_route=2,score_penalty=0)}]}})


class UnattendedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def owner(self):
        s = object.__new__(u.Supervisor)
        s.policy = dict(disk_soft_bytes=12*1024**3,disk_hard_bytes=8*1024**3)
        s.last_disk_level = None
        s.event = Mock()
        s.original_reason = formal.infrastructure_reason
        s.freeze = {'technical_retry_policy':{'max_default_attempts':3}}
        return s

    def test_single_instance_lock(self):
        first = u.acquire_lock(self.root/'lock')
        self.addCleanup(first.close)
        with self.assertRaises(u.HardStop):
            u.acquire_lock(self.root/'lock')

    def test_disk_thresholds(self):
        policy = self.owner().policy
        self.assertEqual(u.disk_level(12*1024**3,policy),'OK')
        self.assertEqual(u.disk_level(8*1024**3,policy),'SOFT_WARNING')
        self.assertEqual(u.disk_level(8*1024**3-1,policy),'HARD_STOP')

    def test_disk_stop_preserves_evidence(self):
        p = self.root/'evidence.json';p.write_text('scientific evidence')
        with patch.object(u,'OUT',self.root),patch.object(u.shutil,'disk_usage',return_value=Mock(free=7*1024**3)):
            with self.assertRaises(u.DiskStop):self.owner().disk_guard()
        self.assertEqual(p.read_text(),'scientific evidence')
        self.assertTrue((self.root/'DISK_SPACE_HARD_STOP.json').exists())

    def test_unknown_interrupted_attempt_never_auto_retries(self):
        output=self.root/'attempt_01';output.mkdir()
        (output/'evaluator.log').write_text('inference was running; termination cause unknown')
        with patch.object(u,'OUT',self.root):
            with self.assertRaises(u.HardStop):
                self.owner().before_resume({'route_id':'1'},'A0',[output])
        self.assertFalse((self.root/'TECHNICAL_RETRY_LEDGER.json').exists())

    def test_oom_still_requires_frozen_root_cause_adjudication(self):
        output=self.root/'attempt_01';output.mkdir()
        (output/'evaluator.log').write_text('CUDA out of memory')
        with patch.object(u,'OUT',self.root):
            with self.assertRaises(u.HardStop):self.owner().before_resume({'route_id':'1'},'A0',[output])

    def reviewed_memory_fixture(self):
        output=self.root/'attempt_01';output.mkdir()
        pair=dict(route_id='1',seed=902609062,route_sha256='route-hash')
        save(output/'RUN_SPEC.json',dict(scope='FORMAL',arm='A0',seed=pair['seed'],
                                       route_sha256=pair['route_sha256']))
        save(output/'official_checkpoint.json',{'_checkpoint':{'records':[dict(
             route_id='RouteScenario_1_rep0',status='Failed - Agent crashed',
             meta=dict(duration_game=0.1))]}})
        save(output/'agent_terminal.json',dict(native_step_final=1,model_pre_count=1,
             model_post_count=0,PID_count=0,violations=[]))
        save(output/'agent_setup.json',{'setup':True})
        save(output/'PROCESS_RECEIPT.json',{'authoritative':False})
        (output/'evaluator.log').write_text('torch.cuda.OutOfMemoryError: CUDA out of memory.')
        probe=self.root/'probe.json';save(probe,{'pass_':True})
        receipt=self.root/'review.json'
        data=dict(freeze_digest=u.EXPECTED_FREEZE,output=str(output),route_id='1',arm='A0',
             attempt=1,conclusive_infrastructure_failure=True,no_authoritative_result=True,
             scientific_configuration_unchanged=True,previous_owned_processes_exited=True,
             evidence_sha256={p.name:sha(p) for p in output.iterdir()},
             cuda_probe_path=str(probe),cuda_probe_sha256=sha(probe))
        save(receipt,data)
        s=self.owner()
        s.policy['reviewed_first_forward_memory_failures']={str(output):dict(
             path=str(receipt),sha256=sha(receipt))}
        save(self.root/'TECHNICAL_RETRY_LEDGER.json',{'entries':[{'output':str(output)}]})
        return s,output,pair,receipt

    def test_reviewed_first_forward_failure_can_resume_same_item(self):
        s,output,pair,receipt=self.reviewed_memory_fixture()
        with patch.object(u,'OUT',self.root):s.before_resume(pair,'A0',[output])
        self.assertEqual(s.reason(output),'GPU_OOM_BEFORE_AUTHORITATIVE_RESULT')
        s.event.assert_called_once()

    def test_review_cannot_authorize_another_arm(self):
        s,output,pair,receipt=self.reviewed_memory_fixture()
        with patch.object(u,'OUT',self.root):
            with self.assertRaises(u.HardStop):s.before_resume(pair,'A1',[output])

    def test_review_evidence_tampering_stops(self):
        s,output,pair,receipt=self.reviewed_memory_fixture()
        (output/'evaluator.log').write_text('torch.cuda.OutOfMemoryError: CUDA out of memory. altered')
        with patch.object(u,'OUT',self.root):
            with self.assertRaises(u.HardStop):s.before_resume(pair,'A0',[output])

    def test_review_after_completed_model_output_stops_even_with_updated_hashes(self):
        s,output,pair,receipt=self.reviewed_memory_fixture()
        terminal=json.loads((output/'agent_terminal.json').read_text())
        terminal['model_post_count']=1;terminal['PID_count']=1
        save(output/'agent_terminal.json',terminal)
        data=json.loads(receipt.read_text())
        data['evidence_sha256']['agent_terminal.json']=sha(output/'agent_terminal.json')
        save(receipt,data)
        s.policy['reviewed_first_forward_memory_failures'][str(output)]['sha256']=sha(receipt)
        with patch.object(u,'OUT',self.root):
            with self.assertRaises(u.HardStop):s.before_resume(pair,'A0',[output])

    def test_review_never_overrides_poor_authoritative_result(self):
        s,output,pair,receipt=self.reviewed_memory_fixture()
        raw(output/'official_checkpoint.json')
        with patch.object(u,'OUT',self.root):
            with self.assertRaises(u.HardStop):s.before_resume(pair,'A0',[output])
        self.assertIsNone(s.reason(output))

    def reviewed_setup_fixture(self):
        s,output,pair,receipt=self.reviewed_memory_fixture()
        (output/'agent_setup.json').unlink();(output/'agent_terminal.json').unlink()
        save(output/'official_checkpoint.json',{'_checkpoint':{'records':[dict(
             route_id='RouteScenario_1_rep0',status="Failed - Agent couldn't be set up",
             meta=dict(duration_game=0,duration_system=0))]}})
        recovery=self.root/'resources.json'
        save(recovery,dict(pass_=True,scientific_render_settings_unchanged=True))
        data=json.loads(receipt.read_text())
        data.update(phase='AGENT_SETUP',evidence_sha256={p.name:sha(p) for p in output.iterdir()},
                    resource_recovery_path=str(recovery),resource_recovery_sha256=sha(recovery))
        save(receipt,data)
        s.policy.pop('reviewed_first_forward_memory_failures')
        s.policy['reviewed_setup_memory_failures']={str(output):dict(path=str(receipt),sha256=sha(receipt))}
        return s,output,pair,receipt,recovery

    def test_reviewed_setup_memory_failure_after_resource_recovery_can_resume(self):
        s,output,pair,receipt,recovery=self.reviewed_setup_fixture()
        with patch.object(u,'OUT',self.root):s.before_resume(pair,'A0',[output])
        self.assertEqual(s.reason(output),'GPU_OOM_BEFORE_AUTHORITATIVE_RESULT')

    def test_setup_review_requires_unchanged_recovery_evidence(self):
        s,output,pair,receipt,recovery=self.reviewed_setup_fixture()
        save(recovery,dict(pass_=False,scientific_render_settings_unchanged=True))
        with patch.object(u,'OUT',self.root):
            with self.assertRaises(u.HardStop):s.before_resume(pair,'A0',[output])

    def test_setup_review_rejects_existing_driving_terminal(self):
        s,output,pair,receipt,recovery=self.reviewed_setup_fixture()
        save(output/'agent_terminal.json',dict(model_post_count=1,PID_count=1))
        with patch.object(u,'OUT',self.root):
            with self.assertRaises(u.HardStop):s.before_resume(pair,'A0',[output])

    def test_setup_review_rejects_different_seed(self):
        s,output,pair,receipt,recovery=self.reviewed_setup_fixture()
        pair['seed']+=1
        with patch.object(u,'OUT',self.root):
            with self.assertRaises(u.HardStop):s.before_resume(pair,'A0',[output])

    def test_conclusive_rpc_attempt_index_recovery(self):
        output=self.root/'attempt_01';output.mkdir()
        (output/'evaluator.log').write_text('rpc::rpc_error')
        save(self.root/'TECHNICAL_RETRY_LEDGER.json',{'entries':[]})
        with patch.object(u,'OUT',self.root):
            self.owner().before_resume({'route_id':'1'},'A0',[output])
        row=json.loads((self.root/'TECHNICAL_RETRY_LEDGER.json').read_text())['entries'][0]
        self.assertTrue(row['retry_allowed']);self.assertEqual(row['reason'],'RPC_DISCONNECT')

    def cuda_failure_fixture(self):
        output=self.root/'attempt_01';output.mkdir()
        (output/'evaluator.log').write_text('torch._C._cuda_init()\nRuntimeError: CUDA driver initialization failed')
        save(output/'official_checkpoint.json',{'_checkpoint':{'records':[{
             'status':"Failed - Agent couldn't be set up",'meta':dict(duration_game=0,duration_system=0)}]}})
        return output

    def test_cuda_initialization_failure_before_driving_is_infrastructure(self):
        output=self.cuda_failure_fixture()
        self.assertEqual(self.owner().reason(output),'CUDA_DRIVER_INITIALIZATION_FAILED_BEFORE_DRIVING')

    def test_cuda_error_cannot_override_authoritative_poor_result(self):
        output=self.cuda_failure_fixture();raw(output/'official_checkpoint.json')
        self.assertIsNone(self.owner().reason(output))

    def test_cuda_error_after_driving_does_not_use_setup_recovery(self):
        output=self.cuda_failure_fixture()
        p=output/'official_checkpoint.json';data=json.loads(p.read_text())
        data['_checkpoint']['records'][0]['meta']['duration_game']=1
        save(p,data);self.assertIsNone(self.owner().reason(output))

    def test_cuda_error_with_existing_agent_setup_needs_adjudication(self):
        output=self.cuda_failure_fixture();save(output/'agent_setup.json',{'pass':True})
        self.assertIsNone(self.owner().reason(output))

    def test_formal_recovery_keeps_poor_authoritative_result_without_run(self):
        route=self.root/'route.xml';route.write_text('<routes/>')
        freeze={'pair_order':[dict(route_id='1',canonical_index=0,arm_order=['A0','A1'],
                                  route_path=str(route),route_sha256=sha(route),seed=902609062)]}
        for arm in ('A0','A1'):
            output=self.root/'formal'/'1'/arm/'attempt_01'
            raw(output/'official_checkpoint.json')
            save(output/'agent_terminal.json',dict(forward_count_contract=True,violations=[]))
            save(self.root/(arm+'_EXECUTION_LEDGER.json'),dict(authoritative_completed=0,entries=[dict(
                route_id='1',canonical_index=0,status='PENDING',attempts=[])]))
        with (self.root/'.formal_supervisor.lock').open('a') as lock, \
             patch.object(formal,'OUT',self.root),patch.object(formal,'make_freeze',return_value=freeze), \
             patch.object(formal,'verify_scientific_sources'),patch.object(formal,'run') as native, \
             patch.object(formal.subprocess,'call',return_value=0):
            self.assertEqual(formal.main(supervisor_lock=lock),0)
        native.assert_not_called()
        for arm in ('A0','A1'):
            ledger=json.loads((self.root/(arm+'_EXECUTION_LEDGER.json')).read_text())
            self.assertEqual(ledger['authoritative_completed'],1)

    def test_duplicate_authority_stops_before_native_run(self):
        output=self.root/'formal'/'1'/'A0'
        for attempt in ('attempt_01','attempt_02'):raw(output/attempt/'official_checkpoint.json')
        s=self.owner();s.verify=Mock()
        with patch.object(u,'OUT',self.root):
            with self.assertRaises(u.HardStop):s.before_item({},dict(route_id='1'),'A0',dict(status='PENDING'))

    def frozen_fixture(self):
        files={}
        for name in ('science.py','weights.pt','all.xml','activation.json','version.json','evaluator.py'):
            p=self.root/name;p.write_text(name);files[name]=p
        save(self.root/'FULL_B2D_ROUTE_MANIFEST.json',dict(path=str(files['all.xml'])))
        files['activation.json'].rename(self.root/'TRANSPARENT_BYPASS_ACTIVATION_CONTRACT.json')
        files['version.json'].rename(self.root/'BENCHMARK_VERSION_AUDIT.json')
        cp=files['weights.pt']
        frozen=dict(scientific_files={str(files['science.py']):sha(files['science.py'])},
            checkpoint=dict(path=str(cp),size=cp.stat().st_size,mtime_ns=cp.stat().st_mtime_ns,sha256=sha(cp)),
            route_manifest_sha256=sha(self.root/'FULL_B2D_ROUTE_MANIFEST.json'),
            activation_contract_sha256=sha(self.root/'TRANSPARENT_BYPASS_ACTIVATION_CONTRACT.json'),
            benchmark_version_audit_sha256=sha(self.root/'BENCHMARK_VERSION_AUDIT.json'),
            evaluator=dict(path=str(files['evaluator.py']),sha256=sha(files['evaluator.py'])),
            metric_scripts={},official_XML_sha256=sha(files['all.xml']),pair_order=[],
            source_HEADs=dict(DriveClarify_HEAD='head',SimLingo_HEAD='head'))
        expected=digest(frozen);frozen['freeze_digest']=expected
        save(self.root/'FULL_B2D_FREEZE_RECEIPT.json',frozen)
        s=self.owner();s.freeze=frozen
        s.policy.update(freeze_file_sha256=sha(self.root/'FULL_B2D_FREEZE_RECEIPT.json'),engineering_files={})
        return s,files,expected

    def test_source_and_checkpoint_hash_tampering(self):
        s,files,expected=self.frozen_fixture()
        with patch.object(u,'OUT',self.root),patch.object(u,'EXPECTED_FREEZE',expected), \
             patch.object(u.subprocess,'check_output',return_value='head\n'):
            s.verify()
            old=files['science.py'].read_bytes();files['science.py'].write_text('altered')
            with self.assertRaises(RuntimeError):s.verify()
            files['science.py'].write_bytes(old)
            # 同大小同 mtime 的 checkpoint 字节变化也必须识别。
            import os
            cp=files['weights.pt'];before=cp.stat();cp.write_bytes(b'x'*before.st_size)
            os.utime(str(cp),ns=(before.st_atime_ns,before.st_mtime_ns))
            with self.assertRaises(u.HardStop):s.verify()

    def test_freeze_file_tampering(self):
        s,files,expected=self.frozen_fixture()
        p=self.root/'FULL_B2D_FREEZE_RECEIPT.json';p.write_text(p.read_text()+' ')
        with patch.object(u,'OUT',self.root),patch.object(u,'EXPECTED_FREEZE',expected):
            with self.assertRaises(u.HardStop):s.verify()

    def test_incomplete_coverage_cannot_emit_completion(self):
        s=self.owner();s.ledger_snapshot=Mock(return_value=({'A0':219,'A1':220},None))
        with patch.object(u,'OUT',self.root):
            with self.assertRaises(u.HardStop):s.complete()
        self.assertFalse((self.root/'UNATTENDED_EXECUTION_COMPLETE.json').exists())


if __name__=='__main__':unittest.main()
