"""Regression evidence for observed starvation, recovery, and fail-open defects."""
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from opslens import db,durability
from opslens.diagnostic import intake,model,store,worker
from opslens.rag.provider import ModelBlocked
from test_diagnostic_database import diagnostic,client,setup,analyze,Provider,pytestmark


def test_multifile_budget_and_utf8_keeps_small_root_and_every_selection(diagnostic):
    c=diagnostic;sid,s,_=setup(c, '\n'.join('ERROR failure '+str(i)+'\n'+'INFO '+('中'*1500)+'\n'*2 for i in range(12)))
    small=c.post(f'/api/v1/diagnostic/sessions/{sid}/sources',json={'request_id':str(uuid4()),'name':'root.log','content':'ERROR ConnectionRefusedError: unique root evidence'}).json()
    p=c.post(f'/api/v1/diagnostic/sessions/{sid}/previews',json={'request_id':str(uuid4()),'source_ids':[s['id'],small['id']]}).json()
    assert {x['source_id'] for x in p['segments']}=={s['id'],small['id']}
    assert any('unique root evidence' in x['content'] for x in p['segments'])
    with db.connect() as conn:context=store.preview(conn,p['id'])['context']
    assert p['coverage']['model_input_bytes']==len(json.dumps(worker.messages(context),ensure_ascii=False).encode())<=32768
    assert all('\ufffd' not in x['content'] for x in p['segments'])


def test_insufficient_budget_is_explicit_not_silent():
    source={'id':uuid4(),'name':'x.log','kind':'log','version':1,'content':'ERROR 中文'*1000,'summary':intake.summarize('ERROR 中文'*1000),'provenance':{}}
    context={'sources':intake.segments([source],[],'failure'),'problem':'x'*32768}
    with pytest.raises(ValueError,match='减少资料选择'):
        intake.fit_context(context,lambda:len(json.dumps(worker.messages(context,enforce=False),ensure_ascii=False).encode()))


def test_continuation_pins_checks_selected_results_and_no_hidden_original(diagnostic):
    c=diagnostic;sid,s,p=setup(c);first=analyze(c,sid,p).json();worker.process(durability.claim('diagnostic'),Provider())
    body={'request_id':str(uuid4()),'kind':'check_result','content':'人工检查：Connection refused；目标5432','check_reference':{'turn_id':first['id'],'check_index':1}}
    source=c.post(f'/api/v1/diagnostic/sessions/{sid}/sources',json=body).json()
    p2=c.post(f'/api/v1/diagnostic/sessions/{sid}/previews',json={'request_id':str(uuid4()),'source_ids':[source['id']]}).json()
    assert p2['continuation']['previous_checks'] and p2['continuation']['provided_results'][0]['check_reference']==body['check_reference']
    assert {x['source_id'] for x in p2['segments']}=={source['id']}
    with db.connect() as conn:
        context=store.preview(conn,p2['id'])['context']
        assert s['id'] not in context['_source_hashes']
        conn.execute("UPDATE diagnostic_previews SET context=jsonb_set(context,'{continuation,boundary}',%s::jsonb) WHERE id=%s",(json.dumps('tampered'),p2['id']))
    assert analyze(c,sid,p2).status_code==400
    assert c.post(f'/api/v1/diagnostic/sessions/{sid}/sources',json=body|{'request_id':str(uuid4()),'check_reference':{'turn_id':first['id'],'check_index':3}}).status_code==400
    foreign,_,_=setup(c)
    assert c.post(f'/api/v1/diagnostic/sessions/{foreign}/sources',json=body|{'request_id':str(uuid4())}).status_code==400


def test_feedback_lost_response_recovered_without_replay_or_model(diagnostic):
    c=diagnostic;sid,_,_=setup(c);rid=str(uuid4());path=f'/api/v1/diagnostic/sessions/{sid}/feedback'
    body={'request_id':rid,'status':'paused','note':'先暂停，保留原记录'}
    original=c.post(path,json=body).json()
    recovered=c.get('/api/v1/diagnostic/submissions/'+rid).json()
    assert recovered=={'kind':'session','value':original}
    assert c.post(path,json=body).json()==original
    assert c.post(path,json=body|{'note':'other'}).status_code==409
    assert c.post(path,json=body|{'request_id':str(uuid4()),'status':'open'}).status_code==200
    with db.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM diagnostic_feedback').fetchone()['n']==2
        assert conn.execute('SELECT count(*) AS n FROM diagnostic_requests').fetchone()['n']==0


def test_concurrent_preview_same_id_different_input_conflicts(diagnostic):
    c=diagnostic;sid,s,_=setup(c,'ERROR Connection refused\nINFO second');rid=str(uuid4())
    base={'request_id':rid,'source_ids':[s['id']]}
    alternate=base|{'ranges':[{'source_id':s['id'],'start_line':2,'end_line':2}]}
    def submit(body):return c.post(f'/api/v1/diagnostic/sessions/{sid}/previews',json=body)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(submit,[base,alternate]))
    assert sorted(r.status_code for r in results)==[200,409]
    winner=0 if results[0].status_code==200 else 1
    assert submit([base,alternate][winner]).json()==results[winner].json()
    with db.connect() as conn:assert conn.execute('SELECT count(*) AS n FROM diagnostic_previews WHERE request_id=%s',(rid,)).fetchone()['n']==1


def test_secret_configuration_failure_blocks_processing_and_outbound(diagnostic,monkeypatch):
    c=diagnostic;sid,_,p=setup(c);task=analyze(c,sid,p).json();provider=Provider()
    def unavailable(*args):raise ModelBlocked('无法读取脱敏密钥配置，已停止处理')
    monkeypatch.setattr(model,'dynamic_secrets',unavailable)
    worker.process(durability.claim('diagnostic'),provider)
    assert provider.calls==0
    assert c.get('/api/v1/diagnostic/turns/'+task['id']).json()['status']=='blocked'
    assert c.post(f'/api/v1/diagnostic/sessions/{sid}/sources',json={'request_id':str(uuid4()),'content':'fixture-not-real naked key'}).status_code==409
    with db.connect() as conn:assert conn.execute('SELECT count(*) AS n FROM diagnostic_requests').fetchone()['n']==0


def test_secret_lookup_once_and_fail_closed(monkeypatch):
    calls=[]
    monkeypatch.setattr(model,'dynamic_secrets',lambda connection=None:(calls.append(connection) or ['private-fixture-key']))
    report={'summary':'private-fixture-key 原因待核对','facts':[],'candidate_causes':[],'next_checks':[{'purpose':'检查','operation':'查看状态','expected':'提供结果','changes_state':False}],'missing_information':['错误时间']}
    result=worker.validate_report(report,[])
    assert len(calls)==1 and 'private-fixture-key' not in json.dumps(result)
    class Failed:
        def execute(self,*args):raise RuntimeError('private-fixture-key')
    monkeypatch.undo()
    with pytest.raises(ModelBlocked,match='停止处理'):model.dynamic_secrets(Failed())
    for secret in ['private"fixture\\key','多字节密钥']:
        with pytest.raises(ModelBlocked,match='阻止外发'):
            model.guard_messages([{'content':json.dumps({'log':secret},ensure_ascii=False)}],[secret])


def test_final_secret_guard_prevents_reservation(diagnostic):
    c=diagnostic;sid,_,p=setup(c);task=analyze(c,sid,p).json()
    with db.connect() as conn:cfg=model.config(conn)
    with pytest.raises(ModelBlocked,match='阻止外发'):
        model.Provider().generate(task['id'],'0',cfg,[{'role':'user','content':'fixture-not-real'}])
    with db.connect() as conn:assert conn.execute('SELECT count(*) AS n FROM diagnostic_requests').fetchone()['n']==0


def test_old_preview_viewable_requires_new_confirmation_and_prompt_version(diagnostic):
    from opslens.external import digest
    from opslens.rag.store import jsonb
    c=diagnostic;sid,_,p=setup(c)
    with db.connect() as conn:
        context=store.preview(conn,p['id'])['context'];context.pop('analysis_prompt_version')
        sha=digest({'context':{k:v for k,v in context.items() if k!='_source_hashes'},'config_id':p['config_id'],'source_hashes':context['_source_hashes']})
        conn.execute('UPDATE diagnostic_previews SET context=%s,digest=%s WHERE id=%s',(jsonb(context),sha,p['id']))
    old=c.get(f'/api/v1/diagnostic/sessions/{sid}/previews/'+p['id']);assert old.status_code==200
    assert old.json()['analysis_prompt_version'] is None
    assert analyze(c,sid,old.json()).status_code==400
