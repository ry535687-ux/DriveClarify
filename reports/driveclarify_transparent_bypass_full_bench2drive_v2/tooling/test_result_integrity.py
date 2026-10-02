import json
import pytest
import numpy as np
from paired_statistics import continuous,binary
from run_route import authoritative

def test_constant_paired_effect_and_sign():
    a=[2,5,9,11];b=[5,8,12,14]
    result=continuous(a,b,902609063)
    assert result['paired_mean_difference']==3 and result['paired_bootstrap_95_CI']==[3,3]
    neg=continuous(b,a,902609063)
    assert neg['paired_bootstrap_95_CI']==[-3,-3]
    assert result['resamples']==100000

def test_paired_bootstrap_is_not_independent_arm_resampling():
    result=continuous([0,20,70,100],[1,21,71,101],902609063)
    assert result['paired_bootstrap_95_CI']==[1,1]

def test_exact_mcnemar_discordance():
    result=binary([True,True,True,False],[False,False,False,True])
    assert result['discordance_table']['A0_true_A1_false']==3
    assert result['discordance_table']['A0_false_A1_true']==1
    assert result['exact_McNemar_p']==0.625
    assert result['delta_percentage_points']==-50

def test_empty_or_invalid_pairs_fail_instead_of_zero():
    with pytest.raises(ValueError):binary([],[])
    with pytest.raises(ValueError):continuous([],[],1)
    with pytest.raises(ValueError):continuous([1],[float('nan')],1)
    with pytest.raises(ValueError):continuous([1],[2,3],1)

@pytest.mark.parametrize('status',['Failed','Failed - Agent got blocked','Failed - Agent timed out','Completed','Perfect'])
def test_first_valid_poor_result_final_even_if_progress_cleanup_incomplete(tmp_path,status):
    p=tmp_path/'nonformal_parser_fixture.json'
    p.write_text(json.dumps({'_checkpoint':{'progress':[0,1],'records':[{'route_id':'RouteScenario_1711_rep0',
      'status':status,'scores':{'score_composed':0,'score_route':0,'score_penalty':0.6},'infractions':{'collisions_vehicle':['event']}}]}}))
    assert authoritative(p)['status']==status

@pytest.mark.parametrize('status',['Started','Failed - Agent crashed','Failed - Simulation crashed',"Failed - Agent couldn't be set up"])
def test_crash_shell_not_silently_scored_or_selected(tmp_path,status):
    p=tmp_path/'nonformal_parser_fixture.json'
    p.write_text(json.dumps({'_checkpoint':{'records':[{'status':status,'scores':{'score_composed':0,'score_route':0,'score_penalty':1}}]}}))
    assert authoritative(p) is None

def test_corrupt_record_not_repaired_into_invented_score(tmp_path):
    p=tmp_path/'corrupt.json';p.write_text('{"_checkpoint":')
    assert authoritative(p) is None and p.read_text()=='{"_checkpoint":'

def test_qualification_prelaunch_retry_survives_formal_ledger_refresh(tmp_path,monkeypatch):
    import formal
    monkeypatch.setattr(formal,'OUT',tmp_path)
    p=tmp_path/'TECHNICAL_RETRY_LEDGER.json'
    p.write_text(json.dumps({'entries':[],'qualification_technical_retries':1,'formal_technical_retries':0,'total_technical_retries':1}))
    for arm,n in [('A0',2),('A1',1)]:
        for number in range(1,n+1):(tmp_path/'formal/1711'/arm/('attempt_%02d'%number)).mkdir(parents=True)
    formal.refresh_retry_counts({'pair_order':[{'route_id':'1711'}]})
    result=json.loads(p.read_text())
    assert result['qualification_technical_retries']==1
    assert result['formal_technical_retries']==1
    assert result['total_technical_retries']==2
    assert result['route_arms_with_retries']==[{'route_id':'1711','arm':'A0','attempts':2}]
