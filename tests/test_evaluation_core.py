import copy
import pytest
from opslens.evaluation.dataset import load_dataset, validate_dataset,verify_freeze
from opslens.evaluation.scoring import retrieval_score, investigation_score, summarize

def test_frozen_sample_counts_and_source_groups_are_disjoint():
    data=load_dataset()
    for name,n in [('retrieval',60),('investigation',30)]:
        rows=data[name]
        assert len(rows)==2*n
        assert all(sum(r['split']==s for r in rows)==n for s in ('dev','test'))
        assert {r['group'] for r in rows if r['split']=='dev'}.isdisjoint(r['group'] for r in rows if r['split']=='test')
    validate_dataset(data)
    assert verify_freeze()['dataset_sha256']

def test_leakage_and_duplicate_inputs_fail_validation():
    data=load_dataset();bad=copy.deepcopy(data)
    bad['retrieval'][0]['split']='test'
    with pytest.raises(ValueError):validate_dataset(bad)
    bad=copy.deepcopy(data);bad['retrieval'][1]['query']=bad['retrieval'][0]['query']
    with pytest.raises(ValueError):validate_dataset(bad)

def test_retrieval_metrics_are_ranked_and_source_deduplicated():
    hits=[{'document':{'source':s}} for s in ['wrong','gold','gold']]
    assert retrieval_score({'relevant_sources':['gold']},hits)=={'hit_at_6':1,'mrr_at_6':.5,'recall_at_6':1.0,'expected_rank':2}
    assert retrieval_score({'relevant_sources':['absent']},hits)['hit_at_6']==0

def test_refusal_and_quote_integrity_are_separate_from_semantic_support():
    case={'expected':'insufficient','root_groups':[],'evidence_source':None}
    result=investigation_score(case,{'assessment':'insufficient','root_cause':None,'missing_information':['需要日志'],'findings':[]},[])
    assert result['rule_correct'] and result['appropriate_refusal']
    report={'assessment':'supported','root_cause':'worker停止','missing_information':[],'findings':[{'statement':'worker停止','references':[{'id':'E1','quote':'invented'}]}]}
    score=investigation_score({'expected':'supported','root_groups':[['worker'],['停止','退出']],'evidence_source':'docker.services'},report,[{'id':'E1','kind':'runtime','source':'docker.services','content':'worker exited'}])
    assert not score['rule_correct'] and score['quote_valid']==0
    assert score['semantic_support']=='requires_review'

def test_failed_blocked_and_unrun_rows_remain_in_denominator():
    rows=[{'kind':'investigation','mode':'single','split':'test','status':'completed','score':{'rule_correct':True},'metrics':{'elapsed_ms':100,'actual_tokens':50}},
          {'kind':'investigation','mode':'single','split':'test','status':'failed'},
          {'kind':'investigation','mode':'single','split':'test','status':'blocked'},
          {'kind':'investigation','mode':'single','split':'test','status':'pending'}]
    value=summarize(rows)[0]
    assert value['planned']==4 and value['completed']==1 and value['failed']==1 and value['blocked']==1 and value['pending']==1
    assert value['correct']==1 and value['success_rate']==.25
    assert value['completed_success_rate']==1
    assert value['latency_n']==1 and value['median_ms']==100

def test_two_case_executor_keeps_pair_order_and_stops_on_block():
    from opslens.evaluation.batch import execute_pairs
    log=[]
    execute_pairs([['a-single','a-multi'],['b-multi','b-single']],lambda r:log.append(r))
    assert log.index('a-single')<log.index('a-multi') and log.index('b-multi')<log.index('b-single')
    log=[]
    def blocked(r):log.append(r);return False
    execute_pairs([['a','b'],['c','d']],blocked,workers=1)
    assert log==['a']

def test_repeated_unconnected_inputs_are_shared_controls_without_changing_raw_split():
    from opslens.evaluation.analysis import analysis_rows
    raw=[{'kind':'investigation','input':{'target':'unconnected','question':'same'},'split':s,'source_group':g} for s,g in [('dev','a'),('test','b')]]
    analyzed=analysis_rows(raw)
    assert all(r['split']=='control' and r['source_group']=='shared-unconnected-control' for r in analyzed)
    assert raw[0]['split']=='dev' and raw[1]['split']=='test'
