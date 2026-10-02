"""只用合成数组核对统计和官方后处理桥接，不估计开发策略效果。"""
import json,sys,tempfile,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from driveclarify_rq3_paired_v2.analysis_statistics import paired_binary,paired_continuous
from driveclarify_rq3_paired_v2.official_metrics import run_official_postprocessing,OFFICIAL_POSTPROCESSOR,sha
Q=ROOT/'reports/driveclarify_rq3_paired_comparison_v2_task_binding_qualification_and_execution/qualification';checks=[]
def ck(n,v):checks.append({'check':n,'pass':bool(v)})
x=paired_binary([0]*24,[1]*24);ck('all_benefit_exact_p',math.isclose(x['exact_mcnemar_p'],2**-23));ck('all_benefit_rd',x['risk_difference']==1)
x=paired_binary([0]*24,[0]*24);ck('no_discordance_p_one',x['exact_mcnemar_p']==1);ck('no_discordance_ci_not_degenerate',x['ci95'][0]<0<x['ci95'][1])
a0=[0]*7+[1]*9+[0]*8;a1=[1]*7+[0]*4+[1]*5+[0]*8;x=paired_binary(a0,a1);y=paired_binary(a1,a0);ck('arm_swap_p',x['exact_mcnemar_p']==y['exact_mcnemar_p']);ck('arm_swap_rd',x['risk_difference']==-y['risk_difference']);ck('arm_swap_ci',all(math.isclose(a,-b) for a,b in zip(x['ci95'],reversed(y['ci95']))));ck('marginals_and_four_cells',sum(x['table_rows_A0_columns_A1'].values())==24)
x=paired_continuous([1,5,8],[4,8,11]);ck('whole_pair_constant_difference',x['mean_paired_difference']==3 and x['ci95']==[3,3])
fixture=Q/'OFFICIAL_POSTPROCESSOR_SYNTHETIC_FIXTURE';fixture.mkdir(exist_ok=False);native=fixture/'official_data';native.mkdir();states={str(i):{'acceleration':[0,0,0],'angular_velocity':[0,0,0],'forward_vector':[1,0,0],'right_vector':[0,1,0],'location':[i*.05,0,0],'rotation':[0,0,0]} for i in range(1,41)};(native/'metric_info.json').write_text(json.dumps(states)+'\n');trace=[{'simulation_time_s':i*.05} for i in range(41)];record={'infractions':{'min_speed_infractions':['Average speed is 100.0% of the surrounding vehicles']}}
x=run_official_postprocessing(fixture,record,trace,fixture/'bridge');ck('official_fixture_efficiency',x['Driving_Efficiency']==100);ck('official_fixture_smoothness',x['Driving_Smoothness']==1);ck('official_input_native_10hz',x['samples_10hz']==20);ck('official_formula_unchanged',x['formula_modified'] is False)
result={'scope':'SYNTHETIC_SOFTWARE_ONLY_NO_POLICY_DATA','checks':checks,'pass':all(x['pass'] for x in checks),'postprocessor_path':str(OFFICIAL_POSTPROCESSOR),'postprocessor_sha256':sha(OFFICIAL_POSTPROCESSOR),'method_references':['https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.binomtest.html','https://docs.scipy.org/doc/scipy-1.15.3/reference/generated/scipy.stats._result_classes.BinomTestResult.proportion_ci.html','https://www.itl.nist.gov/div898/handbook/prc/section4/prc473.htm'],'formal_exposure':0}
(Q/'ANALYSIS_AND_OFFICIAL_POSTPROCESSOR_QUALIFICATION.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result));assert result['pass']
