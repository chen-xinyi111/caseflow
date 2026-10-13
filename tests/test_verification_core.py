import json
import pytest
from opslens.investigation.verification import run_verified, validate_verdict
from test_collaboration_core import FakeTools, tasks, contribution, R, K


def candidate(refs):
    return {'assessment':'supported','summary':'任务等待','root_cause':'worker 停止导致等待',
        'findings':[{'statement':r['content'],'references':[{'id':r['id'],'quote':r['content']}]} for r in refs],
        'missing_information':[],'next_steps':[]}

class Model:
    def __init__(self,uncertain_rounds=0,rejected=False):self.uncertain_rounds=uncertain_rounds;self.rejected=rejected;self.verifies=0
    def generate(self,messages,definitions):
        prompt=messages[0]['content'];context=json.loads(messages[1]['content'])
        for field in ('references','collected_evidence'):
            by_id={}
            for ref in context.get(field,[]):
                if 'content_same_as' in ref:ref['content']=by_id[ref['content_same_as']]['content']
                by_id[ref['id']]=ref
        if '初始规划协调' in prompt:data={'reason':'检查状态与机制','tasks':tasks()}
        elif '独立调查' in prompt:data=contribution(context['collected_evidence'][0])
        elif '候选协调' in prompt:data=candidate(context['references'])
        elif '核验 Agent' in prompt:
            self.verifies+=1
            ok=self.verifies>self.uncertain_rounds and not self.rejected
            report=candidate([r for r in context['references'] if r.get('scope','current')=='current'])
            if not ok:report|={'assessment':'insufficient','root_cause':None,'missing_information':['需要再次读取状态']}
            data={'decision':'confirmed' if ok else 'rejected' if self.rejected else 'uncertain','reason':'依据一致' if ok else '证据尚未支持候选原因','report':report,'issues':[]}
        elif '重新规划协调' in prompt:
            data={'action':'clarify' if self.rejected else 'recheck','reason':'补查状态','tasks':[] if self.rejected else [tasks()[0]],'questions':['请提供实际任务状态'] if self.rejected else []}
        else:raise AssertionError(prompt[:100])
        return {'role':'assistant','content':json.dumps(data,ensure_ascii=False)}


def test_candidate_confirmed_only_after_independent_verifier():
    model=Model();events=[]
    result=run_verified({'target':'fault','question':'任务等待'},model,FakeTools(),lambda k,p:events.append(k))
    assert result['report']['assessment']=='supported' and model.verifies==1
    assert result['model_calls']==5 and result['tool_calls']==2
    assert result['verification']['status']=='confirmed'
    assert 'candidate_ready' in events and 'verification_completed' in events


def test_confirmed_verifier_missing_knowledge_gets_one_bounded_repair():
    class MissingKind(Model):
        def generate(self,messages,definitions):
            result=super().generate(messages,definitions)
            if '核验 Agent' in messages[0]['content']:
                context=json.loads(messages[1]['content'])
                assert context['required_evidence_kinds']==['knowledge','runtime']
                if self.verifies==1:
                    data=json.loads(result['content'])
                    ids={r['id'] for r in context['references'] if r['kind']=='runtime'}
                    data['report']['findings']=[f for f in data['report']['findings'] if f['references'][0]['id'] in ids]
                    result['content']=json.dumps(data)
            return result
    model=MissingKind();events=[]
    result=run_verified({'target':'fault','question':'waiting'},model,FakeTools(),lambda k,p:events.append(k))
    assert model.verifies==2 and result['model_calls']==6
    assert result['verification']['status']=='confirmed' and 'format_repair' in events


def test_wrong_candidate_is_rejected_and_questions_shown():
    result=run_verified({'target':'fault','question':'任务等待'},Model(rejected=True),FakeTools())
    assert result['report']['root_cause'] is None and result['verification']['status']=='needs_information'
    assert result['verification']['questions']==['请提供实际任务状态']
    assert result['verification']['rounds'][0]['verdict']['decision']=='rejected'
    assert result['verification']['replans'][0]['action']=='clarify'


def test_one_replan_collects_new_evidence_then_reverifies():
    model=Model(uncertain_rounds=1);tools=FakeTools()
    result=run_verified({'target':'fault','question':'任务等待'},model,tools)
    assert result['report']['assessment']=='supported' and model.verifies==2
    assert result['model_calls']==7 and result['tool_calls']==3
    assert len(result['verification']['replans'])==1 and len(result['collaboration']['tasks'])==3
    assert len({r['id'] for r in result['references']})==3


def test_shared_eight_call_limit_never_confirms_unverified_followup():
    result=run_verified({'target':'fault','question':'任务等待'},Model(uncertain_rounds=10),FakeTools())
    assert result['model_calls']<=8 and result['report']['root_cause'] is None
    assert len(result['verification']['replans'])<=2
    assert result['verification']['stop_reason']


def test_verdict_cannot_confirm_from_manual_or_historical_runtime():
    report=candidate([R]);data={'decision':'confirmed','reason':'查到原句','report':report,'issues':[]}
    with pytest.raises(ValueError,match='当前运行'):validate_verdict(data,[R|{'scope':'historical'}],'fault',report)
    with pytest.raises(ValueError):validate_verdict(data,[R|{'kind':'knowledge'}],'fault',report)
    with pytest.raises(ValueError):validate_verdict(data,[R],'unconnected',report)


def test_verifier_cannot_replace_candidate_with_an_unproposed_cause():
    report=candidate([R]);data={'decision':'confirmed','reason':'查到原句','report':report|{'root_cause':'Redis 停止'},'issues':[]}
    with pytest.raises(ValueError,match='候选'):validate_verdict(data,[R],'fault',report)


class SnapshotTools(FakeTools):
    def __init__(self,states=('exited','exited','exited'),fail_followup=False):super().__init__();self.states=states;self.reads=0;self.fail_followup=fail_followup
    def load_snapshot(self,i):
        ref=R|{'id':'E-old','source':'docker.services','scope':'historical','collected_at':'2026-10-12T00:00:00Z',
            'content':json.dumps([{'service':'worker','state':'running','health':None}],indent=2)}
        self.refs.append(ref);return ref
    def fork(self,task_id,role):
        child=super().fork(task_id,role);read=child.execute
        def execute(name,args):
            if role=='runtime':
                self.reads+=1
                if self.fail_followup and self.reads>1:return {'references':[{'source':'docker.services','error':'collection failed'}]}
            result=read(name,args)
            if role=='runtime':
                result['references'][0].update(source='docker.services',collected_at=f'2026-10-12T00:00:0{self.reads}Z',
                    content=json.dumps([{'service':'worker','state':self.states[min(self.reads-1,len(self.states)-1)],'health':None}],indent=2))
            return result
        child.execute=execute;return child


def test_old_real_snapshot_cannot_be_confirmed_without_fresh_recheck_even_if_model_ignores_it():
    tools=SnapshotTools();model=Model()
    result=run_verified({'target':'fault','question':'snapshot conflict','snapshot_id':'fixture'},model,tools)
    assert tools.reads==2 and model.verifies==2
    assert result['verification']['rounds'][0]['verdict']['decision']=='uncertain'
    assert result['verification']['conflicts'][0]['resolved'] is True
    assert result['report']['assessment']=='supported'
    assert result['model_calls']==7 and result['tool_calls']==4
    assert result['references'][0]['scope']=='historical'


def test_failed_followup_never_confirms_cause_and_keeps_old_evidence():
    result=run_verified({'target':'fault','question':'snapshot conflict','snapshot_id':'fixture'},Model(),SnapshotTools(fail_followup=True))
    assert result['report']['root_cause'] is None and result['verification']['replans'][0]['status']=='failed'
    assert result['verification']['conflicts'][0]['resolved'] is False
    assert any(r.get('scope')=='historical' for r in result['references'])


def test_current_conflicts_and_ambiguous_time_order_are_not_hidden():
    from opslens.investigation.verification import detected_conflicts
    tools=SnapshotTools();tools.load_snapshot('fixture')
    child=tools.fork('r','runtime');ref=child.execute('collect_runtime',{})['references'][0]
    assert not detected_conflicts(tools.refs+[ref],False)[0].resolved
    same=ref|{'id':'E-second','collected_at':ref['collected_at']}
    assert not detected_conflicts(tools.refs+[ref,same],True)[0].resolved
    changed=same|{'content':ref['content'].replace('exited','running'),'collected_at':'2026-10-12T00:00:03Z'}
    issues=detected_conflicts([ref,changed],True)
    assert any(i.kind=='runtime_conflict' and not i.resolved for i in issues)


def test_two_replan_limit_with_one_initial_role_uses_eight_calls_then_stops():
    class OneRole(Model):
        def generate(self,messages,definitions):
            value=super().generate(messages,definitions)
            if '初始规划协调' in messages[0]['content']:value['content']=json.dumps({'reason':'current only','tasks':[tasks()[0]]})
            return value
    result=run_verified({'target':'fault','question':'current only'},OneRole(uncertain_rounds=10),FakeTools())
    assert result['model_calls']==8 and len(result['verification']['rounds'])==3
    assert len(result['verification']['replans'])==2 and result['report']['root_cause'] is None


def test_replan_six_task_limit_never_dispatches_extra_tasks():
    class Oversized(Model):
        def generate(self,messages,definitions):
            value=super().generate(messages,definitions)
            if '重新规划协调' in messages[0]['content']:
                value['content']=json.dumps({'action':'recheck','reason':'too many','questions':[],
                    'tasks':[tasks()[0]|{'id':f'new{i}'} for i in range(5)]})
            return value
    tools=FakeTools();result=run_verified({'target':'fault','question':'x'},Oversized(uncertain_rounds=10),tools)
    assert len(result['collaboration']['tasks'])==2 and len(tools.order)==2
    assert result['report']['root_cause'] is None and '六项' in result['verification']['stop_reason']
    assert result['verification']['replans'][0]['status']=='skipped'


def test_quota_block_and_deadline_do_not_escape_shared_guard():
    from opslens.rag.provider import ModelBlocked
    class Blocked(Model):
        def generate(self,messages,definitions):
            if '核验 Agent' in messages[0]['content']:raise ModelBlocked('quota fixture')
            return super().generate(messages,definitions)
    events=[]
    with pytest.raises(ModelBlocked):run_verified({'target':'fault','question':'x'},Blocked(),FakeTools(),lambda k,p:events.append(p))
    assert events[-1]['verification']['status']=='blocked'
    tools=FakeTools();result=run_verified({'target':'fault','question':'x'},Model(),tools,timeout=-1)
    assert result['model_calls']==0 and not tools.order and result['report']['root_cause'] is None


def test_only_manual_or_unconnected_never_confirms_current_cause():
    class Manual(Model):
        def generate(self,messages,definitions):
            prompt=messages[0]['content']
            if '初始规划协调' in prompt:return {'role':'assistant','content':json.dumps({'reason':'manual only','tasks':[tasks()[1]]})}
            if '候选协调' in prompt:
                context=json.loads(messages[1]['content']);report=candidate(context['references'])
                report|={'assessment':'insufficient','root_cause':None,'missing_information':['未采集当前运行资料']}
                return {'role':'assistant','content':json.dumps(report)}
            return super().generate(messages,definitions)
    for target in ('healthy','unconnected'):
        result=run_verified({'target':target,'question':'manual'},Manual(),FakeTools())
        assert result['verification']['status']=='not_required' and result['report']['root_cause'] is None


def test_unknown_replan_role_or_external_target_is_rejected_without_extra_tools():
    from opslens.investigation.verification import Followup
    with pytest.raises(ValueError):Followup.model_validate({'action':'recheck','reason':'x','tasks':[tasks()[0]|{'role':'shell'}]})
    with pytest.raises(ValueError):Followup.model_validate({'action':'recheck','reason':'x','target':'project-one','tasks':tasks()})
    with pytest.raises(ValueError):Followup.model_validate({'action':'clarify','reason':'x','tasks':[],'questions':[]})


def test_wrong_manual_version_cannot_confirm_even_with_exact_quotes():
    report=candidate([R,K]);data={'decision':'confirmed','reason':'quotes exist','report':report,'issues':[]}
    with pytest.raises(ValueError,match='版本'):
        validate_verdict(data,[R,K|{'applicable_versions':['v2']}],'fault',report)


def test_context_keeps_cited_and_current_counterevidence_without_repeated_manual_bodies():
    from opslens.investigation.verification import context_references
    refs=[R,K,R|{'id':'E-states','source':'docker.services','content':'redis running'},
        R|{'id':'E-jobs','source':'lab.job_snapshot','content':'completed'},
        R|{'id':'E-diag','source':'lab.diagnostics','content':'redis reachable'},
        R|{'id':'E-old','scope':'historical'},K|{'id':'K-duplicate'},R|{'id':'E-uncited-log','source':'docker.logs.api','content':'uncited log'}]
    selected=context_references(refs,contribution(K)['findings'],{'K-duplicate'})
    assert {r['id'] for r in selected}=={'E1','K1','E-states','E-jobs','E-diag','E-old'}
    assert len(refs)==8 and refs[-1]['content']=='uncited log'


def test_compaction_keeps_all_times_and_ids_and_never_fuzzily_merges_bodies():
    from opslens.investigation.verification import compact_context
    refs=[R|{'source':'docker.logs.worker','collected_at':'2026-10-12T00:00:00Z'},R|{'source':'docker.logs.worker','id':'E2','collected_at':'2026-10-12T00:00:01Z'},R|{'source':'docker.logs.worker','id':'E3','content':'worker  exited'}]
    value=compact_context({'references':refs})['references']
    assert [r['id'] for r in value]==['E1','E2','E3']
    assert value[1]['content_same_as']=='E1' and value[1]['collected_at']==refs[1]['collected_at']
    assert value[2]['content']=='worker  exited' and all(r['content']=='worker exited' for r in refs[:2])


def test_rechecked_service_states_each_show_actual_content():
    from opslens.investigation.verification import compact_context
    body='[{"service":"worker","state":"exited"}]'
    refs=[R|{'id':str(i),'source':'docker.services','content':body,'collected_at':f'2026-10-12T00:00:0{i}Z'} for i in (1,2)]
    view=compact_context({'references':refs})['references']
    assert all(r['content']==body and 'content_same_as' not in r for r in view)
    assert view[0]['collected_at']!=view[1]['collected_at'] and refs[0]['content']==body


def test_json_display_compaction_preserves_source_quotes_and_number_spellings():
    from opslens.investigation.verification import compact_context
    from opslens.investigation.engine import validate_report
    original=R|{'content':'{\n  "state": "exited"\n}'}
    view=compact_context({'references':[original]})['references'][0]
    assert view['content']=='{"state":"exited"}'
    report=candidate([original]);report['findings'][0]['references'][0]['quote']='"state":"exited"'
    checked=validate_report(report,[original],'fault')
    assert checked.findings[0].references[0].quote=='"state": "exited"'
    number=R|{'content':'{"value":1e3}'}
    assert compact_context({'references':[number]})['references'][0]['content']==number['content']
