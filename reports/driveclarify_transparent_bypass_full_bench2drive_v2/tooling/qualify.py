"""16次non-formal原生闭环；只以输入与控制透明性判定。"""
import argparse
import sys
from common import *
from run_route import run,authoritative

def check(output):
    d=load(Path(output)/'dispatch.json',{});r=load(Path(output)/'agent_terminal.json',{})
    samples=r.get('samples',[]);count=r.get('model_pre_count',0)
    checks={
      'native_prompt_identity':bool(samples) and all(s['prompt_utf8_identity'] for s in samples),
      'instruction_token_identity':bool(samples) and all(s['instruction_following_token_count']==0 and s['tokenizer_input_and_final_ids_identity'] for s in samples),
      'matched_native_model_input_seam':r.get('same_native_field_objects_all_observed_forwards') is True,
      'same_sample_A0_A1_dispatch':bool(samples) and all(s['same_sample_counterfactual_A0_A1_class_identity'] for s in samples),
      'image_state_target_route_fields_recorded':bool(samples) and all(set(s['model_input_fingerprints'])=={'camera_images','image_sizes','camera_intrinsics','camera_extrinsics','vehicle_speed','target_point','prompt','prompt_inference','route_switch_active'} for s in samples),
      'forward_count':r.get('forward_count_contract') is True and count>=4,
      'execution_order':r.get('model_PID_order')==['MODEL_PRE','MODEL_POST','PID']*count,
      'controller_and_PID_native':r.get('class_PID_is_native') is True and r.get('bound_PID_delegate_is_native') is True,
      'run_step_tick_native':r.get('run_step_is_native') is True and r.get('tick_is_native') is True,
      'hidden_language_flags':r.get('prompt_flags_native') is True,
      'no_context_bypassed':d.get('DRIVECLARIFY_ACTIVE') is False,
      'observer_preserves_simulation_time':bool(samples) and all(s['simulation_time_before_observer']==s['simulation_time_after_observer'] for s in samples),
      'no_method_activity':bool(r) and all(r.get(k)==0 for k in ['ASK','WAIT','answer_events','candidate_comparisons','evidence_margin_decisions',
        'clarification_triggered_full_replan','nonclarification_DriveClarify_route_installation','nonclarification_full_replan','direct_control_intervention',
        'extra_model_forwards','second_control_writer','route_update_generation_max']),
      'no_integrity_violations':r.get('violations')==[],
      'official_result_written':authoritative(Path(output)/'official_checkpoint.json') is not None,
    }
    return {'output':str(output),'checks':checks,'pass':all(checks.values()),'observed_native_forwards':count,
      'agent_terminal_sha256':sha(Path(output)/'agent_terminal.json') if r else None,
      'route_gps_digest':digest(r.get('native_global_gps_route')),'route_world_digest':digest(r.get('official_dense_route'))}

def main():
    assert load(OUT/'ACTIVE_PATH_REGRESSION_RECEIPT.json',{}).get('pass') is True
    manifest=load(OUT/'TRANSPARENCY_QUALIFICATION_MANIFEST.json')
    activation=load(OUT/'TRANSPARENT_BYPASS_ACTIVATION_CONTRACT.json')
    assert sha(activation['predicate_source'])==activation['predicate_source_sha256']
    results=load(OUT/'TRANSPARENCY_QUALIFICATION_RESULTS.json',{'status':'RUNNING','entries':[],'pairs':[]})
    for i,route in enumerate(manifest['routes']):
        pair=[]
        for arm in manifest['arm_order'][i]:
            base=OUT/'non_formal'/route['route_id']/arm
            prior=sorted(base.glob('attempt_*')) if base.exists() else []
            authoritative_outputs=[p for p in prior if (p/'official_checkpoint.json').exists() and authoritative(p/'official_checkpoint.json') is not None]
            if len(authoritative_outputs)>1:raise RuntimeError('DUPLICATE_AUTHORITATIVE_QUALIFICATION_RESULT')
            if authoritative_outputs:output=authoritative_outputs[0]
            else:
                output=base/('attempt_%02d'%(len(prior)+1))
                print('NON_FORMAL_START',i+1,route['route_id'],arm,flush=True)
                process=run(arm,route['split_files'][0]['path'],manifest['seed'],27000,output,True)
                if not process['authoritative']:
                    results.update(status='ENGINEERING_DIAGNOSIS_REQUIRED',pending_route=route['route_id'],pending_arm=arm,pending_output=str(output))
                    save(OUT/'TRANSPARENCY_QUALIFICATION_RESULTS.json',results)
                    print('TECHNICAL_DIAGNOSIS_REQUIRED',output,flush=True);return 2
            row={'route_id':route['route_id'],'arm':arm,**check(output)}
            results['entries']=[r for r in results['entries'] if (r['route_id'],r['arm'])!=(row['route_id'],arm)]+[row]
            save(OUT/'TRANSPARENCY_QUALIFICATION_RESULTS.json',results)
            pair.append(row)
            if not row['pass']:
                results['status']='TRANSPARENCY_CHECK_FAILURE';save(OUT/'TRANSPARENCY_QUALIFICATION_RESULTS.json',results)
                print('QUALIFICATION_CHECK_FAILURE',row,flush=True);return 3
            print('NON_FORMAL_PASS',i+1,route['route_id'],arm,'forwards',row['observed_native_forwards'],flush=True)
        paired={'route_id':route['route_id'],'pass':all(r['pass'] for r in pair) and len({r['route_gps_digest'] for r in pair})==1 and len({r['route_world_digest'] for r in pair})==1,
          'native_navigation_seam_route_gps_identity':len({r['route_gps_digest'] for r in pair})==1,
          'native_navigation_seam_route_world_identity':len({r['route_world_digest'] for r in pair})==1,
          'trajectory_identity_required':False}
        results['pairs']=[r for r in results['pairs'] if r['route_id']!=paired['route_id']]+[paired]
        save(OUT/'TRANSPARENCY_QUALIFICATION_RESULTS.json',results)
        if not paired['pass']:print('PAIRED_INPUT_IDENTITY_FAILURE',paired,flush=True);return 4
    results['status']='PASS';save(OUT/'TRANSPARENCY_QUALIFICATION_RESULTS.json',results)
    receipt={'status':'PASS_TRANSPARENCY_8_OF_8','pass':True,'qualified_routes':8,'native_executions':16,
      'manifest_digest':manifest['digest'],'predicate_digest':activation['digest'],
      'results_sha256':sha(OUT/'TRANSPARENCY_QUALIFICATION_RESULTS.json'),
      'interface_sources':{str(p):sha(p) for p in (ROOT/'driveclarify_transparent_bypass_v2').glob('*.py')},
      'identity_scope':'Same common native route seam across paired runs; each final DrivingInput is exact native object fields; prompt/token boundary verified on live samples; native class/method identities shared. No stochastic trajectory identity claim.',
      'comparative_scores_used_for_selection':False,'created_utc':now()}
    receipt['digest']=digest(receipt);save(OUT/'TRANSPARENCY_QUALIFICATION_RECEIPT.json',receipt)
    print('PASS_TRANSPARENCY_8_OF_8',flush=True);return 0

if __name__=='__main__':sys.exit(main())
