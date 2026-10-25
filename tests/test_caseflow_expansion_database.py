"""Real SQL/Runtime point->case contract; in-process controlled responses only."""
import copy
import io
import json
import zipfile
from uuid import uuid4
import pytest
from opslens import db,durability
from opslens.caseflow import store,agent,groups,points,case_trace,grounding
REAL_CALL=agent.Provider.call
from test_caseflow_database import client
from test_caseflow_points_database import Controlled,setup,submit,pump,review_body


class CasesProvider(Controlled):
    bad_point=None
    review_bad=False
    missing=False
    propose=False
    numbers=None
    transports=[]
    payloads=[]
    foreign_evidence=None
    foreign_review=None
    def generate(self,messages,tools,key):
        if key.startswith('prepare_segment:') and json.loads(messages[1]['content'])['role']=='context':
            def source_response():
                type(self).transports.append(key);payload=json.loads(messages[1]['content']);e=payload['sources'][0]
                content={'rules':[],'public_conditions':[{'text':e['quote'],'evidence_ids':[e['id']],'questions':[],'applicability':'时长规则同时适用于员工和管理员'}],'context_items':[]}
                return {'content':json.dumps(content,ensure_ascii=False),'usage':{'total_tokens':10}}
            return self.rt.operation(key,source_response,paid=True)
        if not key.startswith(('expand:','point-grounding:')):return super().generate(messages,tools,key)
        def response():
            type(self).transports.append(key);payload=json.loads(messages[1]['content']);type(self).payloads.append((key,copy.deepcopy(payload)))
            ids=[s['id'] for s in payload['sources']]
            if key.startswith('expand:'):
                if self.bad_point==payload['point']['id']:return {'content':'invalid-json','usage':{'total_tokens':10}}
                values=self.numbers or ([119,120,121] if payload['numeric_rules'] else [None])
                rows=[]
                for n in values:
                    rows.append({'title':'同名时长检查','preconditions':'用户已登录，分别检查员工与管理员' if any('登录' in x['text'] for x in payload['public_conditions_and_context']) else '构造空闲会议室，分别检查员工与管理员','data':'构造时长输入',
                        'steps':['提交所构造的时长','检查时长规则符合性'],'expected':'仅核对时长规则，其他条件另行检查','category':'边界','basis':'explicit',
                        'evidence_ids':ids+([self.foreign_evidence] if self.foreign_evidence else []),'numeric_input':{'rule_id':payload['numeric_rules'][0]['id'],'value':str(n)} if payload['numeric_rules'] else None,
                        'replaces_case_id':next(iter(payload.get('replace_targets',{})),None) if self.propose else None})
                content={'cases':rows}
            else:
                if self.review_bad:return {'content':'invalid-json','usage':{'total_tokens':10}}
                content={'reviews':[{'case_index':i,'missing_rule':self.missing,'conflict':False,
                    'fields':[{'field':f,'status':'test_action' if f=='steps' else 'test_data' if f=='data' else 'supported',
                               'reason':'受控复核，仅证明协议','evidence_ids':[] if f in ('steps','data') else ids+([self.foreign_review] if self.foreign_review else [])} for f in grounding.FIELDS]} for i,_ in enumerate(payload['cases'])]}
            return {'role':'assistant','content':json.dumps(content,ensure_ascii=False),'usage':{'total_tokens':10}}
        return self.rt.operation(key,response,paid=True)


@pytest.fixture
def provider(monkeypatch):
    CasesProvider.transports=[];CasesProvider.payloads=[];CasesProvider.bad_point=None;CasesProvider.review_bad=False
    CasesProvider.missing=False;CasesProvider.propose=False;CasesProvider.numbers=None;CasesProvider.foreign_evidence=None;CasesProvider.foreign_review=None;CasesProvider.semantic='checked';CasesProvider.bad_segment=None
    monkeypatch.setattr(agent,'Provider',CasesProvider)
    return CasesProvider


def prepared(c,provider,maximum=180,second=False,numeric=False):
    p,base=setup(c,text=f'预约时长为30至{maximum}分钟，包含上下限。',context=False)
    if second:p=c.post(base+'/texts',json={'revision':p['revision'],'name':'可见范围','content':'普通员工仅查看自己的预约。'}).json()
    p=c.post(base+'/texts',json={'revision':p['revision'],'name':'公共资料B','role':'context','content':'预约时长限制同时适用于员工和管理员。\n\n用户登录后使用预约功能。本资料不规定失败提示文案、弹窗颜色或页面位置。'}).json()
    g,_=submit(c,p,base);assert pump(c,base,g['id'])['status']=='completed'
    p=c.get(base).json()
    if numeric:
        r=p['state']['requirements'][0]
        p=c.post(base+'/requirements/'+r['id'],json={'revision':p['revision'],'text':r['text'],'status':'confirmed'}).json()
        response=c.post(base+'/numeric-rules',json={'revision':p['revision'],'name':'时长范围','metric':'预约时长','unit':'分钟','kind':'range','minimum':'30','maximum':str(maximum),'requirement_ids':[r['id']],'confirmed':True})
        assert response.status_code==200,response.text;p=response.json()
        g,_=submit(c,p,base);assert pump(c,base,g['id'])['status']=='completed';p=c.get(base).json()
    b=p['state']['basis_bundles'][-1]
    g,_=submit(c,p,base,'extract_points',basis_bundle_id=b['id']);assert pump(c,base,g['id'])['status']=='completed'
    p=c.get(base).json();result=c.post(base+'/test-point-reviews',json=review_body(p));assert result.status_code==200,result.text
    return result.json(),base


def expand(c,p,base,selected=None):
    selected=selected or p['state']['test_points']
    body={'revision':p['revision'],'request_id':str(uuid4()),'kind':'expand_cases','consent':True,
          'point_bindings':[{'id':t['id'],'revision':t['revision'],'fingerprint':t['dependency_fingerprint']} for t in selected]}
    r=c.post(base+'/task-groups',json=body);assert r.status_code==202,r.text
    return r.json(),body


def case_body(p,c,**values):
    return {'revision':p['revision'],'case_revision':c['revision'],'binding_fingerprint':c['binding_fingerprint'],**values}


def manual(p,c,**values):
    return case_body(p,c,status='approved',review_note='逐例人工设计审核，未执行业务',acknowledge_grounding=True,
        reviewed_fields=list(grounding.FIELDS),semantic_resolution='逐字核对规则、公共条件及所选原文，结论仅为范围符合性',evidence_ids=c['evidence_ids'],**values)


def test_draft_rules_expand_same_title_different_points_full_trace_and_same_review_scope(client,provider):
    p,base=prepared(client,provider,second=True);assert all(r['status']=='draft' for r in p['state']['requirements'])
    g,body=expand(client,p,base);assert client.post(base+'/task-groups',json=body).json()['id']==g['id']
    settled=pump(client,base,g['id']);assert settled['status']=='completed',settled
    p=client.get(base).json();rows=p['state']['cases'];assert len(rows)==2 and len({c['test_point_id'] for c in rows})==2
    assert all(c['status']=='draft' and c['trace_current'] and c['grounding']['current'] for c in rows)
    for c in rows:
        trace=client.get(base+'/cases/'+c['id']+'/trace').json();assert trace['adopted']['point']['revision']==c['test_point_revision']
        assert trace['adopted']['public_conditions_and_context'] and c['source_versions']
    generation=[v for k,v in provider.payloads if k.startswith('expand:')]
    review=[v for k,v in provider.payloads if k.startswith('point-grounding:')]
    assert len(generation)==len(review)==2
    for a,b in zip(generation,review):assert a['sources']==b['sources'] and a['public_conditions_and_context']==b['public_conditions_and_context']
    c=rows[0];r=client.post(base+'/cases/'+c['id'],json=case_body(p,c,status='approved',review_note='人工核对本点与公共条件'))
    assert r.status_code==200,r.text
    assert all(r['status']=='draft' for r in r.json()['state']['requirements'])


@pytest.mark.parametrize('state',['pending','rejected','needs_review'])
def test_only_current_approved_point_may_expand(client,provider,state):
    p,base=prepared(client,provider);tid=p['state']['test_points'][0]['id']
    store.update(p['id'],p['revision'],'controlled_point_state',lambda s:store.find(s['test_points'],tid).update(status=state))
    p=client.get(base).json();before=len(provider.transports)
    body={'revision':p['revision'],'request_id':str(uuid4()),'kind':'expand_cases','consent':True,'point_bindings':[{'id':tid,'revision':1,'fingerprint':p['state']['test_points'][0]['dependency_fingerprint']}]}
    assert client.post(base+'/task-groups',json=body).status_code==409 and len(provider.transports)==before


def test_foreign_or_stale_selection_cannot_create_group(client,provider):
    p,base=prepared(client,provider);tp=p['state']['test_points'][0]
    body={'revision':p['revision'],'request_id':str(uuid4()),'kind':'expand_cases','consent':True,'point_bindings':[{'id':tp['id'],'revision':2,'fingerprint':tp['dependency_fingerprint']}]}
    assert client.post(base+'/task-groups',json=body).status_code==409
    body['point_bindings'][0]['id']=str(uuid4());assert client.post(base+'/task-groups',json=body).status_code==404


def test_known_bad_leaf_keeps_other_point_results_and_failure_history(client,provider):
    p,base=prepared(client,provider,second=True);provider.bad_point=p['state']['test_points'][0]['id']
    g,_=expand(client,p,base);done=pump(client,base,g['id']);assert done['status']=='partial'
    p=client.get(base).json();assert len(p['state']['cases'])==1
    assert done['counts']['failed']==1 and done['counts']['completed']==1
    count=len(provider.transports);assert client.post(base+'/task-groups/'+g['id']+'/resume',json={'revision':p['revision']}).status_code==200
    done=pump(client,base,g['id']);assert done['status']=='partial' and len(provider.transports)==count
    assert len(client.get(base).json()['state']['cases'])==1 and len(done['items'][0]['attempts'])==2


def test_unavailable_review_requires_per_case_fields_not_global_ack(client,provider):
    p,base=prepared(client,provider);provider.review_bad=True;g,_=expand(client,p,base);assert pump(client,base,g['id'])['status']=='completed'
    p=client.get(base).json();c=p['state']['cases'][0];assert c['grounding']['status']=='unavailable' and c['status']=='draft'
    invalid=case_body(p,c,status='approved',review_note='一个全局确认',acknowledge_grounding=True)
    response=client.post(base+'/cases/'+c['id'],json=invalid);assert response.status_code==400,response.text
    assert client.post(base+'/cases/'+c['id'],json=manual(p,c)).status_code==200


def test_explicit_missing_business_rule_cannot_be_acknowledged_away(client,provider):
    p,base=prepared(client,provider);provider.missing=True;g,_=expand(client,p,base);assert pump(client,base,g['id'])['status']=='completed'
    p=client.get(base).json();assert p['state']['test_points'][0]['status']=='needs_review'
    c=p['state']['cases'][0];assert c['status']=='needs_review' and c['trace_reasons']
    assert client.post(base+'/cases/'+c['id'],json=manual(p,c)).status_code==409


@pytest.mark.parametrize('moment',['before','after'])
def test_case_atomic_commit_crash_reuses_responses_without_duplicate_case(client,provider,monkeypatch,moment):
    p,base=prepared(client,provider);g,_=expand(client,p,base);groups.schedule();task=durability.claim('caseflow');original=groups.atomic_commit
    if moment=='before':
        original_event=durability.event
        def interrupted(c,family,tid,kind,payload):
            if tid==task['id'] and kind=='finished':raise durability.LeaseLost('controlled precommit crash')
            return original_event(c,family,tid,kind,payload)
        monkeypatch.setattr(durability,'event',interrupted)
    else:
        def interrupted(*args,**kwargs):original(*args,**kwargs);raise durability.LeaseLost('controlled postcommit crash')
        monkeypatch.setattr(groups,'atomic_commit',interrupted)
    agent.process(task);count=len(provider.transports)
    if moment=='before':
        assert not client.get(base).json()['state']['cases']
        with db.connect() as c:c.execute("UPDATE durable_tasks SET lease_until=now()-interval '1 second' WHERE task_id=%s",(task['id'],))
        monkeypatch.setattr(durability,'event',original_event);agent.process(durability.claim('caseflow'))
    else:monkeypatch.setattr(groups,'atomic_commit',original)
    settled=pump(client,base,g['id']);assert settled['status']=='completed',settled
    p=client.get(base).json();assert len(p['state']['cases'])==1 and len(provider.transports)==count
    with db.connect() as c:
        group=groups.get(c,p['id'],g['id']);assert group['items'][0]['result']['case_ids']==[p['state']['cases'][0]['id']]
        assert group['expected_project_revision']==p['revision']


def test_numeric_expecteds_are_computed_and_exports_preserve_trace(client,provider):
    p,base=prepared(client,provider,maximum=120,numeric=True);g,_=expand(client,p,base);assert pump(client,base,g['id'])['status']=='completed'
    p=client.get(base).json();rows=p['state']['cases'];assert [c['calculation']['result']['within_range'] for c in rows]==[True,True,False]
    assert all('其他规则需另行检查' in c['expected'] for c in rows)
    md=client.get(base+'/export?format=md').text
    assert 'TP001' in md and '公共条件' in md and '本地定位链接需要' in md and 'http://127.0.0.1:18180/#/projects/' in md
    workbook=client.get(base+'/export?format=xlsx').content
    with zipfile.ZipFile(io.BytesIO(workbook)) as z:
        sheet=z.read('xl/worksheets/sheet1.xml').decode();assert 'TP001' in sheet and '定位链接' in sheet and '<f>' not in sheet


def test_case_batch_stale_entity_is_atomic_and_rebind_creates_revision(client,provider):
    p,base=prepared(client,provider);g,_=expand(client,p,base);assert pump(client,base,g['id'])['status']=='completed';p=client.get(base).json();c=p['state']['cases'][0]
    body={'revision':p['revision'],'ids':[c['id']],'status':'approved','review_note':'核对当前设计','items':[{'id':c['id'],'case_revision':2,'binding_fingerprint':c['binding_fingerprint']}]}
    r=client.post(base+'/reviews',json=body);assert r.status_code==400 and r.json()['detail']['items']
    assert client.get(base).json()['state']['cases'][0]['status']=='draft'
    approved=client.post(base+'/cases/'+c['id'],json=case_body(p,c,status='approved',review_note='核对当前设计')).json();c=approved['state']['cases'][0]
    run=client.post(base+'/runs',json={'revision':approved['revision'],'request_id':str(uuid4()),'name':'合成未执行批次','results':[{'case_id':c['id'],'revision':c['revision'],'status':'未执行','note':'未对接真实业务'}]})
    assert run.status_code==200,run.text
    p=run.json();tp=p['state']['test_points'][0]
    values={k:tp[k] for k in ('intent','group','types','module','tags','priority','basis_bundle_id','basis_item_ids','evidence_ids','questions')}
    values.update(rule_ids=[r['id'] for r in tp['requirement_bindings']],intent='人工更新测试意图',revision=p['revision'],point_revision=tp['revision'],dependency_fingerprint=tp['dependency_fingerprint'])
    p=client.post(base+'/test-points/'+tp['id'],json=values).json();assert p['state']['cases'][0]['status']=='needs_review'
    p=client.post(base+'/test-point-reviews',json=review_body(p,manual=True)).json();tp=p['state']['test_points'][0];c=p['state']['cases'][0]
    r=client.post(base+'/cases/'+c['id'],json=case_body(p,c,status='draft',point_binding={'id':tp['id'],'revision':tp['revision'],'fingerprint':tp['dependency_fingerprint']}))
    assert r.status_code==200,r.text;p=r.json();c=p['state']['cases'][0]
    assert c['revision']==2 and c['test_point_revision']==2
    assert not p['state']['runs'][0]['results'][0]['current'] and p['state']['runs'][0]['results'][0]['snapshot']['test_point_revision']==1
    assert any(h['kind']=='case' and h['value']['status']=='approved' for h in p['state']['history'])



def rebind_point(c,p,base,point,bundle):
    values={k:point[k] for k in ('intent','group','types','module','tags','priority','questions')}
    rids=[r['id'] for r in point['requirement_bindings']];items=[i['id'] for i in bundle['items']]
    evidence=set(e for rid in rids for e in bundle['rule_evidence'][rid]);evidence.update(e for item in bundle['items'] for e in item['evidence_ids'])
    values.update(revision=p['revision'],point_revision=point['revision'],dependency_fingerprint=point['dependency_fingerprint'],
                  basis_bundle_id=bundle['id'],rule_ids=rids,basis_item_ids=items,evidence_ids=sorted(evidence))
    r=c.post(base+'/test-points/'+point['id'],json=values);assert r.status_code==200,r.text;p=r.json()
    selected=[t for t in p['state']['test_points'] if t['id']==point['id']]
    r=c.post(base+'/test-point-reviews',json=review_body(p,selected,manual=True));assert r.status_code==200,r.text
    return r.json()


def test_fixed_180_to_120_change_proposals_old_links_and_unexecuted_snapshots(client,provider):
    from pathlib import Path
    fixture=Path('tests/fixtures/caseflow_stage5');v1=(fixture/'预约需求_v1.md').read_text('utf-8-sig');v2=(fixture/'预约需求_v2.md').read_text('utf-8-sig');common=(fixture/'公共资料B.md').read_text('utf-8-sig')
    assert v1.replace('30至180','30至120')==v2
    p,base=setup(client,context=False,text=v1);old_doc=p['state']['documents'][0]['id']
    p=client.post(base+'/texts',json={'revision':p['revision'],'name':'公共资料B','role':'context','content':common}).json();ctx_doc=p['state']['documents'][-1]['id']
    g,_=submit(client,p,base);assert pump(client,base,g['id'])['status']=='completed';p=client.get(base).json();rid=p['state']['requirements'][0]['id'];r=p['state']['requirements'][0]
    p=client.post(base+'/requirements/'+rid,json={'revision':p['revision'],'text':r['text'],'status':'confirmed'}).json()
    p=client.post(base+'/numeric-rules',json={'revision':p['revision'],'name':'v1时长','metric':'预约时长','unit':'分钟','kind':'range','minimum':'30','maximum':'180','requirement_ids':[rid],'confirmed':True}).json()
    g,_=submit(client,p,base);assert pump(client,base,g['id'])['status']=='completed';p=client.get(base).json();bundle=p['state']['basis_bundles'][-1]
    g,_=submit(client,p,base,'extract_points',basis_bundle_id=bundle['id']);assert pump(client,base,g['id'])['status']=='completed';p=client.get(base).json()
    p=client.post(base+'/test-point-reviews',json=review_body(p)).json();provider.numbers=[150]
    g,_=expand(client,p,base);assert pump(client,base,g['id'])['status']=='completed';p=client.get(base).json();oldcase=p['state']['cases'][0]
    assert oldcase['calculation']['result']['within_range'] and '登录' not in oldcase['preconditions']
    p=client.post(base+'/cases/'+oldcase['id'],json=case_body(p,oldcase,status='approved',review_note='仅人工设计审核，未执行')).json();oldcase=copy.deepcopy(p['state']['cases'][0]);old_eid=next(e['id'] for e in oldcase['trace_snapshot']['sources'] if e['document_id']==old_doc)
    p=client.post(base+'/runs',json={'revision':p['revision'],'request_id':str(uuid4()),'name':'v1合成记录','results':[{'case_id':oldcase['id'],'revision':1,'status':'未执行','note':'无真实业务接入'}]}).json()
    p=client.post(base+'/texts',json={'revision':p['revision'],'name':'需求A v2','content':v2,'previous_id':old_doc}).json();new_doc=p['state']['documents'][-1]['id']
    assert p['state']['test_points'][0]['status']=='needs_review' and p['state']['cases'][0]['status']=='needs_review'
    assert p['state']['cases'][0]['expected']==oldcase['expected'] and not p['state']['runs'][0]['results'][0]['current']
    g,_=submit(client,p,base,document_ids=[new_doc,ctx_doc]);assert pump(client,base,g['id'])['status']=='completed';p=client.get(base).json();change=next(x for x in p['state']['changes'] if x['status']=='pending')
    r=client.post(base+'/changes/'+change['id'],json={'revision':p['revision'],'action':'replace','target_ids':[rid]});assert r.status_code==200,r.text;p=r.json()
    rule=next(r for r in p['state']['requirements'] if r['id']==rid)
    p=client.post(base+'/requirements/'+rid,json={'revision':p['revision'],'text':rule['text'],'status':'confirmed'}).json()
    r=client.post(base+'/numeric-rules',json={'revision':p['revision'],'name':'v2时长','metric':'预约时长','unit':'分钟','kind':'range','minimum':'30','maximum':'120','requirement_ids':[rid],'confirmed':True});assert r.status_code==200,r.text;p=r.json()
    g,_=submit(client,p,base,document_ids=[new_doc,ctx_doc],rule_ids=[rid]);assert pump(client,base,g['id'])['status']=='completed';p=client.get(base).json()
    p=rebind_point(client,p,base,p['state']['test_points'][0],p['state']['basis_bundles'][-1]);provider.propose=True
    g,_=expand(client,p,base);assert pump(client,base,g['id'])['status']=='completed';p=client.get(base).json();proposal=next(c for c in p['state']['cases'] if c.get('replaces_case_id'))
    assert not proposal['calculation']['result']['within_range'] and proposal['replaces_case_id']==oldcase['id']
    r=client.post(base+'/proposals/'+proposal['id'],json=case_body(p,proposal));assert r.status_code==200,r.text;p=r.json();updated=next(c for c in p['state']['cases'] if c['id']==oldcase['id'])
    assert updated['code']==oldcase['code'] and updated['revision']==2 and updated['status']=='draft' and updated['test_point_revision']==2
    assert client.get(base+'/evidence/'+old_eid).json()['document_id']==old_doc
    assert p['state']['runs'][0]['results'][0]['snapshot']['expected']==oldcase['expected'] and p['state']['runs'][0]['results'][0]['status']=='未执行'
    assert any(v['id']==new_doc for v in updated['source_versions'])


def test_later_human_edit_rejects_old_proposal_without_overwriting(client,provider):
    p,base=prepared(client,provider);g,_=expand(client,p,base);pump(client,base,g['id']);p=client.get(base).json();target=p['state']['cases'][0]
    store.update(p['id'],p['revision'],'controlled_review_needed',lambda s:store.find(s['cases'],target['id']).update(status='needs_review'))
    p=client.get(base).json();provider.propose=True;g,_=expand(client,p,base);assert pump(client,base,g['id'])['status']=='completed';p=client.get(base).json();target=p['state']['cases'][0];proposal=p['state']['cases'][-1]
    r=client.post(base+'/cases/'+target['id'],json=case_body(p,target,title='后来的人工修改，请保留',status='draft'));assert r.status_code==200,r.text;p=r.json()
    proposal=p['state']['cases'][-1];r=client.post(base+'/proposals/'+proposal['id'],json=case_body(p,proposal));assert r.status_code==409
    assert client.get(base).json()['state']['cases'][0]['title']=='后来的人工修改，请保留'


@pytest.mark.parametrize('phase',['design','review','usage_unknown'])
def test_unknown_external_ledger_stops_all_later_items_and_substitute_requests(client,provider,monkeypatch,phase):
    from opslens.diagnostic import model
    # The original provider reservation logic is deliberately exercised with a local callback only.
    real_call=REAL_CALL
    class Unknown(CasesProvider):
        def generate(self,messages,tools,key):
            trigger=(key.startswith('expand:') if phase=='design' else key.startswith('point-grounding:'))
            if not trigger:return super().generate(messages,tools,key)
            with db.connect() as c:self.cfg=model.config(c)
            fn=(lambda:{'content':'{}','usage':{}}) if phase=='usage_unknown' else (lambda:(_ for _ in ()).throw(RuntimeError('controlled unknown response; no network')))
            return real_call(self,key,fn)
    p,base=prepared(client,provider,second=True);monkeypatch.setattr(agent,'Provider',Unknown)
    g,body=expand(client,p,base);done=pump(client,base,g['id']);assert done['status']=='blocked'
    p=client.get(base).json();assert len(p['state']['cases'])==(0 if phase=='design' else 1)
    assert done['counts']['pending']==1
    assert client.post(base+'/task-groups/'+g['id']+'/resume',json={'revision':p['revision']}).status_code==409
    body.update(revision=p['revision'],request_id=str(uuid4()));assert client.post(base+'/task-groups',json=body).status_code==409


def test_saved_point_case_review_uses_draft_rules_and_exact_adopted_documents(client,provider):
    p,base=prepared(client,provider);g,_=expand(client,p,base);pump(client,base,g['id']);p=client.get(base).json();row=p['state']['cases'][0]
    r=client.post(base+'/tasks',json={'revision':p['revision'],'request_id':str(uuid4()),'kind':'review','document_ids':[v['id'] for v in row['source_versions']],'requirement_ids':row['requirement_ids'],'case_ids':[row['id']],'consent':True})
    assert r.status_code==202,r.text;agent.process(durability.claim('caseflow'))
    p=client.get(base).json();assert p['state']['cases'][0]['grounding']['current']
    assert all(r['status']=='draft' for r in p['state']['requirements'])


def test_foreign_generation_sources_reject_batch_and_foreign_review_is_unavailable(client,provider):
    p,base=prepared(client,provider);provider.foreign_evidence=str(uuid4());g,_=expand(client,p,base)
    assert pump(client,base,g['id'])['status']=='failed' and not client.get(base).json()['state']['cases']
    provider.foreign_evidence=None;provider.foreign_review=str(uuid4());p=client.get(base).json();g,_=expand(client,p,base)
    assert pump(client,base,g['id'])['status']=='completed';c=client.get(base).json()['state']['cases'][0]
    assert c['grounding']['status']=='unavailable' and provider.foreign_review not in c['evidence_ids']



@pytest.mark.parametrize('maximum,value,expected',[(180,29,False),(180,30,True),(180,31,True),(180,179,True),(180,180,True),(180,181,False),(180,150,True),(120,29,False),(120,30,True),(120,31,True),(120,119,True),(120,120,True),(120,121,False),(120,150,False)])
def test_fixed_numeric_oracle_independent_of_model(maximum,value,expected):
    s=store.empty();store.add_document(s,'合成预约',[f'预约时长为30至{maximum}分钟，包含上下限。'])
    doc=s['documents'][0];r=store.new_requirement(s,{'text':doc['pages'][0],'citations':[{'document_id':doc['id'],'page':1,'quote':doc['pages'][0]}]},doc['id']);r['status']='confirmed'
    grounding.add_rule(s,{'name':'时长范围','metric':'预约时长','unit':'分钟','kind':'range','minimum':'30','maximum':str(maximum),'requirement_ids':[r['id']],'confirmed':True})
    row=grounding.prepare_numeric(s,{'requirement_ids':[r['id']],'numeric_input':{'rule_id':s['numeric_rules'][0]['id'],'value':str(value)},'data':'合成数据','expected':'模型原草稿，待程序计算'},[doc['id']])
    assert row['calculation']['result']['within_range'] is expected and '其他规则需另行检查' in row['expected']
    assert '预约成功' not in row['expected']


def test_budget_stop_after_design_saves_draft_and_resumes_pending_point(client,provider,monkeypatch):
    from opslens.rag.provider import ModelBlocked
    class Budget(CasesProvider):
        paused=True
        def generate(self,messages,tools,key):
            if self.paused and key.startswith('point-grounding:'):raise ModelBlocked('controlled preflight budget')
            return super().generate(messages,tools,key)
    p,base=prepared(client,provider,second=True);monkeypatch.setattr(agent,'Provider',Budget)
    g,_=expand(client,p,base);done=pump(client,base,g['id']);assert done['status']=='blocked' and done['counts']['pending']==1
    p=client.get(base).json();first=p['state']['cases'][0];assert first['grounding']['status']=='unavailable'
    calls=len(provider.transports);Budget.paused=False
    assert client.post(base+'/task-groups/'+g['id']+'/resume',json={'revision':p['revision']}).status_code==200
    assert pump(client,base,g['id'])['status']=='completed' and len(provider.transports)==calls+2
    assert len(client.get(base).json()['state']['cases'])==2


def test_unrelated_document_update_does_not_invalidate_other_point_case(client,provider):
    p,base=prepared(client,provider,second=True);g,_=expand(client,p,base);pump(client,base,g['id']);p=client.get(base).json();first=p['state']['documents'][0]['id']
    p=client.post(base+'/texts',json={'revision':p['revision'],'name':'只更新时长来源','content':'预约时长为30至120分钟。','previous_id':first}).json()
    bypoint={c['test_point_id']:c for c in p['state']['cases']}
    assert bypoint[p['state']['test_points'][0]['id']]['status']=='needs_review'
    other=bypoint[p['state']['test_points'][1]['id']];assert other['status']=='draft' and other['trace_current']


def test_numeric_human_edit_requires_explicit_recalculation_before_approval(client,provider):
    p,base=prepared(client,provider,maximum=120,numeric=True);g,_=expand(client,p,base);pump(client,base,g['id']);p=client.get(base).json();c=p['state']['cases'][0]
    r=client.post(base+'/cases/'+c['id'],json=case_body(p,c,expected='预约一定成功',status='draft'));assert r.status_code==200,r.text;p=r.json();c=p['state']['cases'][0]
    assert client.post(base+'/cases/'+c['id'],json=manual(p,c)).status_code==400
    r=client.post(base+'/cases/'+c['id'],json=case_body(p,c,numeric_input=c['numeric_input'],recalculate=True,status='draft'));assert r.status_code==200,r.text;p=r.json();c=p['state']['cases'][0]
    assert '其他规则需另行检查' in c['expected'] and client.post(base+'/cases/'+c['id'],json=manual(p,c)).status_code==200



def test_protocol_hash_is_not_redacted_as_phone_and_numeric_rule_can_be_removed(client,provider,monkeypatch):
    p,base=prepared(client,provider,maximum=120,numeric=True);g,_=expand(client,p,base);pump(client,base,g['id'])
    frozen='a'*20+'13812345678'+'b'*33
    monkeypatch.setattr(case_trace,'fingerprint',lambda s,row:frozen)
    p=client.get(base).json();c=p['state']['cases'][0]
    response=client.post(base+'/cases/'+c['id'],json=case_body(p,c,status='draft',review_note='保留人工备注'));assert response.status_code==200,response.text
    p=response.json();c=p['state']['cases'][0]
    assert c['binding_fingerprint']==frozen
    r=client.post(base+'/cases/'+c['id'],json=case_body(p,c,numeric_input=None,status='draft'))
    assert r.status_code==200,r.text
    saved=r.json()['state']['cases'][0]
    assert saved['numeric_input'] is None and not saved.get('calculation') and saved['revision']==2 and saved['review_note']=='保留人工备注'



def test_refreshed_project_revision_does_not_overwrite_another_case_review_decision(client,provider):
    p,base=prepared(client,provider);g,_=expand(client,p,base);pump(client,base,g['id']);p=client.get(base).json();frozen=copy.deepcopy(p['state']['cases'][0])
    response=client.post(base+'/cases/'+frozen['id'],json=case_body(p,frozen,status='rejected'));assert response.status_code==200,response.text
    p=response.json();assert p['state']['cases'][0]['revision']==frozen['revision']
    stale=client.post(base+'/cases/'+frozen['id'],json=case_body(p,frozen,status='draft'))
    assert stale.status_code==409 and client.get(base).json()['state']['cases'][0]['status']=='rejected'
