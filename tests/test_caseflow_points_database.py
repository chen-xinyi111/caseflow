"""Stage4: real SQL/Runtime with explicit in-process model responses, never live models."""
import copy
import json
from uuid import uuid4
import pytest
from opslens import db,durability
from opslens.caseflow import store,agent,groups,points,preparation
from opslens.rag.provider import ModelBlocked
from test_caseflow_database import client


class Controlled:
    transports=[]
    semantic='checked'
    bad_segment=None
    def __init__(self,rt,cfg):self.rt=rt
    def generate(self,messages,tools,key):
        def response():
            type(self).transports.append(key);payload=json.loads(messages[1]['content'])
            if key.startswith('prepare_segment:'):
                source=payload['sources'][0];text=source['quote']
                if self.bad_segment and self.bad_segment in text:return {'content':'bad-json','usage':{'total_tokens':10}}
                row={'text':text,'evidence_ids':[source['id']],'questions':['权限范围待确认'] if '权限待确认' in text else []}
                content={'rules':[row] if payload['role']=='requirement' else [],
                         'public_conditions':[] if payload['role']=='requirement' else [row|{'applicability':'预约检查需登录'}], 'context_items':[]}
            elif key.startswith('extract_points:'):
                common=payload['public_conditions_and_context'];content={'points':[]}
                for r in payload['rules'][:3]:
                    evidence=set(payload['rule_evidence'][r['id']]);evidence.update(e for i in common for e in i['evidence_ids'])
                    content['points'].append({'intent':'核对'+r['text'][:100],'group':'预约场景','types':['边界'],'module':'预约','tags':[],
                        'priority':'P2','rule_ids':[r['id']],'basis_item_ids':[i['id'] for i in common],
                        'evidence_ids':sorted(evidence),'questions':[]})
            elif key.startswith('point_review:'):
                if self.semantic=='invalid':return {'content':'bad-json','usage':{'total_tokens':10}}
                content={'reviews':[{'index':i,'status':self.semantic,'reason':'受控语义意见，仍需人工批准','missing_rule':False,'conflict':False} for i,_ in enumerate(payload['points'])]}
            else:raise AssertionError(key)
            return {'role':'assistant','content':json.dumps(content,ensure_ascii=False),'usage':{'total_tokens':10}}
        return self.rt.operation(key,response,paid=True)
    def rag(self):pytest.fail('Keyword path must not invoke a model provider')


@pytest.fixture
def controlled(monkeypatch):
    Controlled.transports=[];Controlled.semantic='checked';Controlled.bad_segment=None
    monkeypatch.setattr(agent,'Provider',Controlled)
    return Controlled


def setup(c,mode='standard',text='预约时长30至120分钟。',context=True):
    p=c.post('/api/v1/caseflow/projects',json={'name':'第四阶段隔离验证','workflow_mode':mode}).json();base='/api/v1/caseflow/projects/'+p['id']
    p=c.post(base+'/texts',json={'revision':p['revision'],'content':text}).json()
    if context:p=c.post(base+'/texts',json={'revision':p['revision'],'name':'公共条件','role':'context','content':'用户需登录后预约。'}).json()
    return p,base


def submit(c,p,base,kind='prepare_basis',**kwargs):
    body={'revision':p['revision'],'request_id':str(uuid4()),'kind':kind,'consent':True}
    if kind=='prepare_basis':body.update(target='检查预约规则',document_ids=[d['id'] for d in p['state']['documents']])
    body.update(kwargs);r=c.post(base+'/task-groups',json=body)
    assert r.status_code==202,r.text
    return r.json(),body


def pump(c,base,gid,limit=80):
    for _ in range(limit):
        groups.schedule();g=c.get(base+'/task-groups/'+gid).json()
        if g['status'] not in ('queued','running'):return g
        task=durability.claim('caseflow')
        if task:agent.process(task)
    pytest.fail('Group did not settle')


def ready(c,controlled,**kwargs):
    p,base=setup(c,**kwargs);g,_=submit(c,p,base);g=pump(c,base,g['id'])
    assert g['status']=='completed',g
    p=c.get(base).json();bundle=p['state']['basis_bundles'][-1]
    assert bundle['status']=='ready'
    return p,base,bundle


def extract(c,p,base,bundle):
    g,_=submit(c,p,base,'extract_points',basis_bundle_id=bundle['id']);g=pump(c,base,g['id'])
    assert g['status']=='completed',g
    return c.get(base).json()


def review_body(p,selected=None,manual=False):
    selected=selected or p['state']['test_points']
    return {'revision':p['revision'],'items':[{'id':r['id'],'point_revision':r['revision'],'dependency_fingerprint':r['dependency_fingerprint'],
        **({'reviewed_fields':sorted(points.REVIEW_FIELDS),'semantic_resolution':'逐项阅读当前原文，确认意图和适用条件','evidence_ids':r['evidence_ids']} if manual else {})} for r in selected],
        'decision':'approved','note':'逐项核对测试意图和原文，不代表业务执行通过'}


def test_new_standard_old_default_and_switch_never_confirm_rules(client):
    p,base=setup(client,context=False)
    assert p['state']['workflow_mode']=='standard'
    raw=store.create('legacy','strict')
    with db.connect() as c:
        s=store.empty();c.execute('UPDATE cf_projects SET state=%s WHERE id=%s',(store.jsonb(s),raw['id']))
    assert client.get('/api/v1/caseflow/projects/'+str(raw['id'])).json()['state']['workflow_mode']=='strict'
    with db.connect() as c:assert 'workflow_mode' not in store.get(c,raw['id'])['state']
    switched=client.post(base+'/workflow',json={'revision':p['revision'],'mode':'strict'})
    assert switched.status_code==200 and switched.json()['state']['requirements']==p['state']['requirements']


def test_full_prepare_draft_rules_context_readback_and_point_approval(client,controlled):
    p,base,bundle=ready(client,controlled)
    assert all(r['status']=='draft' for r in p['state']['requirements'])
    assert bundle['items'] and all(m['completed_segments']==m['total_segments'] for m in bundle['coverage_manifest'])
    assert bundle['retrieval_log'] and all(r['result']['readback_verified'] for r in bundle['retrieval_log'])
    assert all(e['viewer_url'].startswith('/#/projects/'+p['id']) for e in bundle['evidence_catalog'].values())
    p=extract(client,p,base,bundle);tp=p['state']['test_points'][0]
    assert tp['status']=='pending' and tp['location_status']=='valid' and tp['semantic_review']['status']=='checked'
    saved=client.post(base+'/test-point-reviews',json=review_body(p));assert saved.status_code==200,saved.text
    assert saved.json()['state']['test_points'][0]['status']=='approved'
    assert all(r['status']=='draft' for r in saved.json()['state']['requirements'])
    assert saved.json()['state']['point_reviews'][0]['actor']=='local_user'
    bypass=client.post(base+'/tasks',json={'revision':saved.json()['revision'],'request_id':str(uuid4()),'kind':'generate','consent':True,
        'document_ids':[d['id'] for d in p['state']['documents']],'requirement_ids':[p['state']['requirements'][0]['id']]})
    assert bypass.status_code==400


def test_same_input_cache_and_manual_rule_do_not_fake_coverage(client,controlled):
    p,base=setup(client,context=False,text='时长规则30至120分钟。\n\n'+'另一规则。'*100)
    did=p['state']['documents'][0]['id']
    p=client.post(base+'/requirements',json={'revision':p['revision'],'text':'人工编辑保持原样','document_id':did,'page':1,'quote':'时长规则30至120分钟。'}).json()
    old=copy.deepcopy(p['state']['requirements'][0]);g,_=submit(client,p,base);assert pump(client,base,g['id'])['status']=='completed'
    p=client.get(base).json();b=p['state']['basis_bundles'][-1]
    assert b['coverage_manifest'][0]['total_segments']>1 and p['state']['requirements'][0]==old
    count=len(controlled.transports)
    g,_=submit(client,p,base);assert pump(client,base,g['id'])['status']=='completed'
    assert len(controlled.transports)==count
    b=client.get(base).json()['state']['basis_bundles'][-1]
    assert all(i['reused'] for i in b['coverage_manifest'][0]['segments'])


def test_partial_coverage_cannot_be_used_as_ready(client,controlled):
    controlled.bad_segment='BROKEN'
    p,base=setup(client,context=False,text='正常规则。\n\n'+'BROKEN '+('未完成内容。'*110))
    g,_=submit(client,p,base);g=pump(client,base,g['id']);assert g['status']=='partial',g
    p=client.get(base).json();b=p['state']['basis_bundles'][-1]
    assert b['status']=='partial' and any(x['status']=='failed' for x in b['coverage_manifest'][0]['segments'])
    r=client.post(base+'/task-groups',json={'revision':p['revision'],'request_id':str(uuid4()),'kind':'extract_points','consent':True,'basis_bundle_id':b['id']})
    assert r.status_code==409


def test_semantic_unavailable_requires_per_point_evidence_review(client,controlled):
    controlled.semantic='invalid';p,base,b=ready(client,controlled);p=extract(client,p,base,b)
    assert p['state']['test_points'][0]['semantic_review']['status']=='unavailable'
    assert client.post(base+'/test-point-reviews',json=review_body(p)).status_code==400
    assert client.post(base+'/test-point-reviews',json=review_body(p,manual=True)).status_code==200


def test_atomic_batch_rejects_stale_or_foreign_point_and_preserves_history(client,controlled):
    p,base,b=ready(client,controlled,text='时长规则。\n\n权限规则。');p=extract(client,p,base,b)
    body=review_body(p);body['items'][-1]['point_revision']+=1
    rejected=client.post(base+'/test-point-reviews',json=body)
    assert rejected.status_code==400 and rejected.json()['detail']['items']
    assert all(r['status']=='pending' for r in client.get(base).json()['state']['test_points'])
    good=client.post(base+'/test-point-reviews',json=review_body(p));assert good.status_code==200,good.text
    p=good.json();tp=p['state']['test_points'][0]
    content={k:tp[k] for k in ('intent','group','types','module','tags','priority','basis_bundle_id','basis_item_ids','evidence_ids','questions')}
    content.update(rule_ids=[b['id'] for b in tp['requirement_bindings']],intent='人工修改测试意图',revision=p['revision'],
                   point_revision=tp['revision'],dependency_fingerprint=tp['dependency_fingerprint'])
    changed=client.post(base+'/test-points/'+tp['id'],json=content);assert changed.status_code==200,changed.text
    row=changed.json()['state']['test_points'][0]
    assert row['code']==tp['code'] and row['revision']==2 and row['status']=='needs_review'
    assert any(h['kind']=='test_point' and h['value']['intent']==tp['intent'] for h in changed.json()['state']['history'])


def test_unrelated_question_does_not_block_other_point(client,controlled):
    p,base=setup(client,context=False)
    p=client.post(base+'/texts',json={'revision':p['revision'],'content':'权限待确认规则。'}).json()
    g,_=submit(client,p,base);assert pump(client,base,g['id'])['status']=='completed'
    p=client.get(base).json();b=p['state']['basis_bundles'][-1];p=extract(client,p,base,b)
    rows=p['state']['test_points'];valid=[r for r in rows if not r['blocking_reasons']];blocked=[r for r in rows if r['blocking_reasons']]
    assert valid and blocked
    assert client.post(base+'/test-point-reviews',json=review_body(p,valid)).status_code==200
    p=client.get(base).json();assert client.post(base+'/test-point-reviews',json=review_body(p,blocked,manual=True)).status_code==400


def test_group_idempotence_exact_lookup_and_external_revision_stops_calls(client,controlled):
    p,base=setup(client);g,body=submit(client,p,base)
    assert client.post(base+'/task-groups',json=body).json()['id']==g['id']
    assert client.post(base+'/task-groups',json=body|{'target':'其他目标'}).status_code==409
    assert client.get(base+'/task-groups?request_id='+body['request_id']).json()[0]['id']==g['id']
    groups.schedule();task=durability.claim('caseflow')
    store.update(p['id'],p['revision'],'manual_edit',lambda s:s.update(note='outside group'))
    agent.process(task);assert not controlled.transports
    assert client.get(base+'/task-groups/'+g['id']).json()['status']=='stale'


def test_unknown_result_blocks_resume_and_substitute_group(client,controlled,monkeypatch):
    class Unknown(Controlled):
        def generate(self,messages,tools,key):
            return self.rt.operation(key,lambda:(_ for _ in ()).throw(RuntimeError('controlled lost response')),paid=True)
    monkeypatch.setattr(agent,'Provider',Unknown)
    p,base=setup(client);g,_=submit(client,p,base);g=pump(client,base,g['id']);assert g['status']=='blocked'
    assert client.post(base+'/task-groups/'+g['id']+'/resume',json={'revision':p['revision']}).status_code==409
    new={ 'revision':p['revision'],'request_id':str(uuid4()),'kind':'prepare_basis','consent':True,'target':'same target','document_ids':[d['id'] for d in p['state']['documents']]}
    assert client.post(base+'/task-groups',json=new).status_code==409


def test_real_disabled_provider_blocks_without_touching_settings_or_requests(client):
    p,base=setup(client,context=False)
    with db.connect() as c:before=c.execute('SELECT * FROM diagnostic_configs ORDER BY version').fetchall()
    g,_=submit(client,p,base);assert pump(client,base,g['id'])['status']=='blocked'
    with db.connect() as c:
        assert c.execute('SELECT * FROM diagnostic_configs ORDER BY version').fetchall()==before
        assert c.execute('SELECT count(*) n FROM cf_requests').fetchone()['n']==0


@pytest.mark.parametrize('moment',['before','after'])
def test_atomic_commit_crash_replays_completed_calls_without_duplicate_points(client,controlled,monkeypatch,moment):
    p,base,b=ready(client,controlled);g,_=submit(client,p,base,'extract_points',basis_bundle_id=b['id'])
    groups.schedule();task=durability.claim('caseflow');original=groups.atomic_commit
    if moment=='before':
        original_event=durability.event
        def crash_event(c,family,tid,kind,payload):
            if tid==task['id'] and kind=='finished':raise durability.LeaseLost('controlled precommit crash')
            return original_event(c,family,tid,kind,payload)
        monkeypatch.setattr(durability,'event',crash_event)
    else:
        def crash_after(*args,**kwargs):
            original(*args,**kwargs);raise durability.LeaseLost('controlled postcommit crash')
        monkeypatch.setattr(groups,'atomic_commit',crash_after)
    agent.process(task);calls=len(controlled.transports)
    saved=client.get(base).json()
    if moment=='before':
        assert not saved['state']['test_points'] and saved['revision']==p['revision']
        with db.connect() as c:
            assert store.task(c,task['id'])['status']=='running'
            assert groups.get(c,p['id'],g['id'])['items'][0]['status']=='running'
            c.execute("UPDATE durable_tasks SET lease_until=now()-interval '1 second' WHERE task_id=%s",(task['id'],))
        monkeypatch.setattr(durability,'event',original_event)
        replay=durability.claim('caseflow');assert replay['id']==task['id'] and replay['_attempt']==2
        agent.process(replay)
    else:
        assert saved['revision']==p['revision']+1
        monkeypatch.setattr(groups,'atomic_commit',original)
        assert durability.claim('caseflow') is None
    assert pump(client,base,g['id'])['status']=='completed'
    final=client.get(base).json();assert len(final['state']['test_points'])==1 and len(controlled.transports)==calls
    with db.connect() as c:
        group=groups.get(c,p['id'],g['id']);leaf=group['items'][0]
        assert leaf['result']['point_ids']==[final['state']['test_points'][0]['id']]
        assert leaf['committed_revision']==group['expected_project_revision']==final['revision']
        assert store.task(c,task['id'])['status']=='completed'
        assert c.execute("SELECT count(*) n FROM cf_events WHERE task_id=%s AND kind='finished'",(task['id'],)).fetchone()['n']==1


def test_known_budget_stop_resumes_same_leaf_without_repeating_completed_calls(client,controlled,monkeypatch):
    class Budget(Controlled):
        paused=True
        def generate(self,messages,tools,key):
            if type(self).paused and len(type(self).transports)==1:raise ModelBlocked('controlled preflight budget')
            return super().generate(messages,tools,key)
    monkeypatch.setattr(agent,'Provider',Budget)
    p,base=setup(client);g,_=submit(client,p,base);g=pump(client,base,g['id']);assert g['status']=='blocked'
    first=g['items'][0]['task_id'];failed=next(i for i in g['items'] if i['status']=='failed');failed_id=failed['task_id']
    Budget.paused=False
    assert client.post(base+'/task-groups/'+g['id']+'/resume',json={'revision':p['revision']}).status_code==200
    done=pump(client,base,g['id']);assert done['status']=='completed'
    assert done['items'][0]['task_id']==first and next(i for i in done['items'] if i['key']==failed['key'])['task_id']==failed_id
    assert len(controlled.transports)==2
    assert any(a.get('error')=='controlled preflight budget' for a in next(i for i in done['items'] if i['key']==failed['key'])['attempts'])


def test_answer_reprepare_preserves_explicit_mapping_and_reuses_source_facts(client,controlled):
    p,base=setup(client,context=False)
    p=client.post(base+'/texts',json={'revision':p['revision'],'name':'公共条件','role':'context','content':'权限待确认：登录范围。'}).json()
    g,_=submit(client,p,base);assert pump(client,base,g['id'])['status']=='completed'
    p=client.get(base).json();old=p['state']['basis_bundles'][-1];iid=old['items'][0]['id'];qid=old['issues'][0]['question_id']
    p=extract(client,p,base,old);assert p['state']['test_points'][0]['blocking_reasons']
    p=client.post(base+'/clarifications/'+qid,json={'revision':p['revision'],'answer':'仅已登录用户，逐项采用此回答。'}).json()
    assert any('回答已变化' in x for x in points.bundle_reasons(p['state'],old))
    calls=len(controlled.transports);g,_=submit(client,p,base);assert pump(client,base,g['id'])['status']=='completed'
    p=client.get(base).json();new=p['state']['basis_bundles'][-1]
    assert new['id']!=old['id'] and new['items'][0]['id']==iid and len(controlled.transports)==calls
    assert new['confirmed_answers'][0]['id']==qid and len([q for q in p['state']['clarifications'] if q['id']==qid])==1
    p=extract(client,p,base,new);tp=p['state']['test_points'][-1];assert not tp['blocking_reasons']
    assert client.post(base+'/test-point-reviews',json=review_body(p,[tp])).status_code==200


@pytest.mark.parametrize('mutation',['rule','source','numeric','mode','question'])
def test_relevant_dependency_changes_invalidate_approved_with_history(client,controlled,mutation):
    p,base,b=ready(client,controlled);p=extract(client,p,base,b)
    p=client.post(base+'/test-point-reviews',json=review_body(p)).json();tp=p['state']['test_points'][0];rid=tp['requirement_bindings'][0]['id']
    def change(s):
        if mutation=='rule':store.find(s['requirements'],rid)['text']+='已修改'
        elif mutation=='source':s['documents'].append({'id':store.uid(),'previous_id':s['documents'][0]['id'],'name':'新版本','role':'requirement','pages':['新正文']})
        elif mutation=='numeric':s.setdefault('numeric_rules',[]).append({'id':store.uid(),'requirement_ids':[rid],'requirement_revisions':{rid:1},'enabled':True})
        elif mutation=='mode':points.switch(s,'strict')
        else:points.question(s,'新增待确定业务问题',requirement_ids=[rid])
    saved=store.public(store.update(p['id'],p['revision'],'controlled_dependency_change',change))
    assert saved['state']['test_points'][0]['status']=='needs_review'
    assert saved['state']['point_reviews'][0]['decision']=='approved'
    assert saved['state']['history'][-1]['value']['status']=='approved'


def test_strict_draft_blocks_approval_and_unmapped_old_answers_do_not_close_questions(client,controlled):
    p,base,b=ready(client,controlled,mode='strict',context=False,text='权限待确认规则。');p=extract(client,p,base,b)
    with db.connect() as c:
        raw=store.get(c,p['id']);r=raw['state']['requirements'][0];r['answers']=[{'text':'没有问题映射的旧回答'}]
        c.execute('UPDATE cf_projects SET state=%s WHERE id=%s',(store.jsonb(raw['state']),p['id']))
    p=client.get(base).json();reasons=p['state']['test_points'][0]['blocking_reasons']
    assert any('严格流程' in x for x in reasons) and any('待澄清' in x for x in reasons)
    assert client.post(base+'/test-point-reviews',json=review_body(p,manual=True)).status_code==400


def test_group_signature_change_stops_new_calls_and_exact_lookup_beyond_50(client,controlled,monkeypatch):
    p,base=setup(client);g,body=submit(client,p,base);groups.schedule();agent.process(durability.claim('caseflow'))
    monkeypatch.setattr(groups,'signature',lambda:'different')
    assert pump(client,base,g['id'])['status']=='stale' and len(controlled.transports)==1
    with db.connect() as c:
        for _ in range(51):
            c.execute("INSERT INTO cf_task_groups(id,project_id,request_id,kind,input,input_digest,expected_project_revision,config_id,processing_signature,items,status) SELECT %s,project_id,%s,kind,input,input_digest,expected_project_revision,config_id,processing_signature,items,'completed' FROM cf_task_groups WHERE id=%s",(uuid4(),uuid4(),g['id']))
    assert g['id'] not in {x['id'] for x in client.get(base+'/task-groups').json()}
    assert client.get(base+'/task-groups?request_id='+body['request_id']).json()[0]['id']==g['id']


def test_backup_guard_sees_group_between_leaf_tasks(client,monkeypatch):
    import importlib.util,sys
    p,base=setup(client);submit(client,p,base)
    spec=importlib.util.spec_from_file_location('stage4_archive_check','scripts/archive_product.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(sys,'argv',['archive_product.py','--check-active'])
    with pytest.raises(RuntimeError,match='task groups'):module.main()


def test_ambiguous_legacy_quote_requires_explicit_evidence_new_bundle(client,controlled):
    p,base=setup(client,context=False,text='重复规则。\n\n重复规则。');did=p['state']['documents'][0]['id']
    p=client.post(base+'/requirements',json={'revision':p['revision'],'text':'人工规则','document_id':did,'page':1,'quote':'重复规则。'}).json();rid=p['state']['requirements'][0]['id']
    g,_=submit(client,p,base);assert pump(client,base,g['id'])['status']=='completed'
    p=client.get(base).json();old=copy.deepcopy(p['state']['basis_bundles'][-1]);assert any(i.get('kind')=='ambiguous_reference' for i in old['issues'])
    eid=next(iter(old['evidence_catalog']))
    result=client.post(base+'/basis-bundles/'+old['id']+'/rule-evidence',json={'revision':p['revision'],'rule_id':rid,'evidence_ids':[eid],'note':'阅读两个出现位置，明确采用这一精确范围'})
    assert result.status_code==200,result.text
    p=result.json();assert p['state']['basis_bundles'][-2]==old
    new=p['state']['basis_bundles'][-1];assert new['rule_evidence'][rid]==[eid] and new['human_bindings'][0]['actor']=='local_user'
    assert not any(i.get('kind')=='ambiguous_reference' for i in new['issues'])



def test_manual_point_missing_evidence_and_foreign_batch_never_approve(client,controlled):
    p,base,b=ready(client,controlled)
    body={'revision':p['revision'],'basis_bundle_id':b['id'],'intent':'人工边界点','rule_ids':[b['requirement_bindings'][0]['id']]}
    result=client.post(base+'/test-points',json=body);assert result.status_code==200,result.text
    p=result.json();assert p['state']['test_points'][0]['status']=='pending'
    assert client.post(base+'/test-point-reviews',json=review_body(p,manual=True)).status_code==400
    request=review_body(p);request['items'][0]['id']=str(uuid4())
    rejected=client.post(base+'/test-point-reviews',json=request);assert rejected.status_code==400 and rejected.json()['detail']['items']
    assert client.get(base).json()['revision']==p['revision']


def test_cross_project_scope_consent_and_draft_numeric_rule_protection(client,controlled):
    p,base,b=ready(client,controlled);other,otherbase=setup(client,context=False)
    invalid={'revision':p['revision'],'kind':'prepare_basis','request_id':str(uuid4()),'target':'目标','document_ids':[other['state']['documents'][0]['id']],'consent':True}
    assert client.post(base+'/task-groups',json=invalid).status_code==404
    invalid.update(document_ids=[p['state']['documents'][0]['id']],consent=False)
    assert client.post(base+'/task-groups',json=invalid).status_code==400
    r=client.post(base+'/numeric-rules',json={'revision':p['revision'],'name':'预约时长','metric':'时长','unit':'分钟','kind':'range','minimum':'30','maximum':'120','requirement_ids':[p['state']['requirements'][0]['id']],'confirmed':True})
    assert r.status_code==400 and not client.get(base).json()['state'].get('numeric_rules')


def test_real_request_budget_preflight_never_writes_issued_ledger_or_changes_config(client,monkeypatch):
    original_provider=agent.Provider
    class BudgetProvider(original_provider):
        def generate(self,messages,tools,key):
            self.cfg=copy.deepcopy(self.cfg);self.cfg['settings']['daily_limit']=0
            return self.call(key,lambda:pytest.fail('Budget must reject before transport'))
    monkeypatch.setattr(agent,'Provider',BudgetProvider)
    p,base=setup(client,context=False)
    with db.connect() as c:before=c.execute('SELECT * FROM diagnostic_configs ORDER BY version').fetchall()
    g,_=submit(client,p,base);assert pump(client,base,g['id'])['status']=='blocked'
    with db.connect() as c:
        assert c.execute('SELECT * FROM diagnostic_configs ORDER BY version').fetchall()==before
        assert c.execute('SELECT count(*) n FROM cf_requests').fetchone()['n']==0
        assert c.execute("SELECT count(*) n FROM durable_operations WHERE paid AND status='issued'").fetchone()['n']==0


def test_model_configuration_change_stops_group_without_new_call(client,controlled,monkeypatch):
    p,base=setup(client);g,_=submit(client,p,base)
    original=groups.model.config
    monkeypatch.setattr(groups.model,'config',lambda c:original(c)|{'id':uuid4()})
    assert pump(client,base,g['id'])['status']=='stale' and not controlled.transports



def test_new_item_question_after_bundle_requires_reprepare_even_after_resolution(client,controlled):
    p,base,b=ready(client,controlled);iid=b['items'][0]['id']
    r=client.post(base+'/clarifications',json={'revision':p['revision'],'question':'新的公共条件问题','basis_bundle_id':b['id'],'basis_item_ids':[iid]})
    assert r.status_code==200,r.text
    p=r.json();qid=p['state']['clarifications'][-1]['id']
    p=client.post(base+'/clarifications/'+qid,json={'revision':p['revision'],'answer':'明确仅对已登录用户适用'}).json()
    assert any('问题或回答已变化' in reason for reason in points.bundle_reasons(p['state'],b))



def test_legacy_rule_without_quotes_cannot_borrow_context_as_its_own_evidence(client,controlled):
    p,base,b=ready(client,controlled);rid=p['state']['requirements'][0]['id']
    store.update(p['id'],p['revision'],'controlled_legacy_missing_quote',lambda s:store.find(s['requirements'],rid).update(citations=[],preparation_key=None))
    p=client.get(base).json();g,_=submit(client,p,base);assert pump(client,base,g['id'])['status']=='completed'
    p=client.get(base).json();b=p['state']['basis_bundles'][-1]
    assert any(i.get('kind')=='ambiguous_reference' and rid in i['requirement_ids'] for i in b['issues'])
    ctx=next(e for e,v in b['evidence_catalog'].items() if v['document_id']==b['items'][0]['document_id'])
    r=client.post(base+'/test-points',json={'revision':p['revision'],'basis_bundle_id':b['id'],'intent':'仅有公共条件来源','rule_ids':[rid],'evidence_ids':[ctx]})
    assert r.status_code==200,r.text
    p=r.json();assert any('目标规则缺少' in x for x in p['state']['test_points'][-1]['blocking_reasons'])
    assert client.post(base+'/test-point-reviews',json=review_body(p,manual=True)).status_code==400



def test_existing_rule_cannot_pull_unselected_document_via_old_citation(client,controlled):
    p,base=setup(client,context=False);first=p['state']['documents'][0]
    p=client.post(base+'/texts',json={'revision':p['revision'],'name':'本次不授权的参考','content':'隐藏参考不得进入本次准备。','role':'context'}).json();hidden=p['state']['documents'][-1]
    p=client.post(base+'/requirements',json={'revision':p['revision'],'text':'既有目标规则','document_id':first['id'],'page':1,'quote':first['pages'][0]}).json();rid=p['state']['requirements'][0]['id']
    store.update(p['id'],p['revision'],'controlled_old_extra_citation',lambda s:store.find(s['requirements'],rid)['citations'].append({'document_id':hidden['id'],'page':1,'quote':hidden['pages'][0]}))
    p=client.get(base).json();g,_=submit(client,p,base,document_ids=[first['id']]);assert pump(client,base,g['id'])['status']=='completed'
    p=client.get(base).json();b=p['state']['basis_bundles'][-1]
    assert {e['document_id'] for e in b['evidence_catalog'].values()}=={first['id']}
    assert any(i.get('kind')=='unauthorized_reference' for i in b['issues'])
