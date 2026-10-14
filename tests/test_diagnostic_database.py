import json
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import pytest
from opslens import db,durability
from opslens.diagnostic import store,model,worker,intake
from opslens.diagnostic.contracts import ModelSettings
from opslens.rag.provider import ModelBlocked
from test_investigation_database import client,pytestmark

@pytest.fixture
def diagnostic(client,monkeypatch):
    store.initialize()
    monkeypatch.setenv('OPSLENS_TEST_MODEL_KEY','fixture-not-real')
    client.post('/api/v1/diagnostic/settings',json=ModelSettings(mode='compatible',base_url='https://model.example.test/v1',model='fixture',key_env='OPSLENS_TEST_MODEL_KEY',enabled=True).model_dump())
    return client

def setup(c,text='ERROR Connection refused\n'):
    sid=c.post('/api/v1/diagnostic/sessions',json={'request_id':str(uuid4()),'problem':'部署后数据库连接失败'}).json()['id']
    source=c.post('/api/v1/diagnostic/sessions/'+sid+'/sources',json={'request_id':str(uuid4()),'name':'server.log','content':text}).json()
    preview=c.post('/api/v1/diagnostic/sessions/'+sid+'/previews',json={'request_id':str(uuid4()),'source_ids':[source['id']]}).json()
    return sid,source,preview

def analyze(c,sid,p,confirm=True):return c.post('/api/v1/diagnostic/sessions/'+sid+'/turns',json={'request_id':str(uuid4()),'preview_id':p['id'],'digest':p['digest'],'confirm':confirm})

class Provider:
    def __init__(self,invalid=False,unknown=False):self.calls=0;self.invalid=invalid;self.unknown=unknown
    def generate(self,turn,stage,cfg,packet):
        self.calls+=1;assert len(json.dumps(packet,ensure_ascii=False).encode())<=32768
        context=json.loads(packet[1]['content']);ref=context['sources'][0]
        report={'summary':'日志记录了连接被拒绝，真实原因尚待核对','facts':[{'text':'日志中出现连接被拒绝','citations':[{'id':ref['id'],'quote':'Connection refused'}]}],'candidate_causes':[{'title':'目标服务或连接地址需要检查','reason':'不能仅凭拒绝连接判断数据库停止','citations':[]}],'next_checks':[{'purpose':'核对目标地址','operation':'在你的部署配置中核对数据库主机与端口，不提交密码','expected':'目标地址与实际数据库监听地址一致','changes_state':False}],'missing_information':['数据库实际监听地址与端口']}
        if self.invalid and stage=='0':report['facts'][0]['citations'][0]['id']='nonexistent'
        return {'content':json.dumps(report),'usage_unknown':self.unknown,'usage':{'total_tokens':100}}

def test_prepare_requires_no_project_or_index_and_binds_selected_ranges(diagnostic):
    c=diagnostic;sid,s,p=setup(c,'INFO ready\n'*8000+'ERROR Connection refused\n')
    assert p['coverage']['model_input_bytes']<=32768 and any(x['start_line']>7900 for x in p['segments'])
    assert analyze(c,sid,p,False).status_code==400
    with db.connect() as conn:assert conn.execute('SELECT count(*) AS n FROM diagnostic_requests').fetchone()['n']==0
    foreign=c.post('/api/v1/diagnostic/sessions',json={'request_id':str(uuid4())}).json()['id']
    assert c.get('/api/v1/diagnostic/sessions/'+foreign+'/sources/'+s['id']).status_code==404
    changed=c.post('/api/v1/diagnostic/sessions/'+sid+'/previews',json={'request_id':str(uuid4()),'source_ids':[s['id']],'ranges':[{'source_id':s['id'],'start_line':1,'end_line':2}]}).json()
    assert changed['digest']!=p['digest']

def test_followup_reports_feedback_and_export_preserve_previous_round(diagnostic):
    c=diagnostic;sid,s,p=setup(c);body={'request_id':str(uuid4()),'preview_id':p['id'],'digest':p['digest'],'confirm':True}
    task=c.post('/api/v1/diagnostic/sessions/'+sid+'/turns',json=body).json()
    assert c.post('/api/v1/diagnostic/sessions/'+sid+'/turns',json=body).json()['id']==task['id']
    provider=Provider();worker.process(durability.claim('diagnostic'),provider)
    assert provider.calls==1
    out=c.get('/api/v1/diagnostic/turns/'+task['id']).json();assert out['status']=='completed'
    new=c.post('/api/v1/diagnostic/sessions/'+sid+'/sources',json={'request_id':str(uuid4()),'name':'检查输出','kind':'check_result','content':'ERROR Connection refused persists'}).json()
    p2=c.post('/api/v1/diagnostic/sessions/'+sid+'/previews',json={'request_id':str(uuid4()),'source_ids':[new['id']]}).json();assert p2['id']!=p['id']
    second=analyze(c,sid,p2).json();worker.process(durability.claim('diagnostic'),provider)
    assert len(c.get('/api/v1/diagnostic/sessions/'+sid+'/turns').json())==2
    feedback=c.post('/api/v1/diagnostic/sessions/'+sid+'/feedback',json={'request_id':str(uuid4()),'status':'solved','note':'人工修正地址后恢复'}).json()
    assert feedback['status']=='solved'
    exported=c.get('/api/v1/diagnostic/sessions/'+sid+'/export').text
    assert '用户反馈已解决' in exported and '人工修正地址' in exported and '检查输出' in exported
    assert c.get('/api/v1/diagnostic/sessions/'+sid+'/sources/'+s['id']).json()['content']=='ERROR Connection refused\n'
    events=c.get('/api/v1/diagnostic/turns/'+task['id']+'/events').text
    first=int(next(line[4:] for line in events.splitlines() if line.startswith('id: ')))
    assert f'id: {first}\n' not in c.get('/api/v1/diagnostic/turns/'+task['id']+'/events?after='+str(first)).text

def test_settings_change_and_tampered_source_stop_before_generation(diagnostic):
    c=diagnostic;sid,s,p=setup(c)
    with db.connect() as conn:conn.execute('UPDATE diagnostic_sources SET content=%s WHERE id=%s',('tampered',s['id']))
    assert analyze(c,sid,p).status_code==400
    sid,s,p=setup(c)
    settings=c.get('/api/v1/diagnostic/settings').json()['settings'];settings['model']='new'
    c.post('/api/v1/diagnostic/settings',json=settings)
    assert analyze(c,sid,p).status_code==400

def test_format_repair_is_bounded_and_unknown_usage_never_retries(diagnostic):
    c=diagnostic;sid,s,p=setup(c);task=analyze(c,sid,p).json();provider=Provider(invalid=True)
    worker.process(durability.claim('diagnostic'),provider)
    assert provider.calls==2 and c.get('/api/v1/diagnostic/turns/'+task['id']).json()['status']=='completed'
    sid,s,p=setup(c);task=analyze(c,sid,p).json();provider=Provider(invalid=True,unknown=True)
    worker.process(durability.claim('diagnostic'),provider)
    assert provider.calls==1 and c.get('/api/v1/diagnostic/turns/'+task['id']).json()['status']=='blocked'

def test_atomic_daily_budget_is_shared_across_configs(diagnostic):
    c=diagnostic;sid,s,p=setup(c);task=analyze(c,sid,p).json()
    with db.connect() as conn:cfg=model.config(conn)
    cfg['settings']['daily_limit']=1
    # Apply a real configuration version rather than overriding accounting.
    model.save_settings(ModelSettings(**cfg['settings']))
    with db.connect() as conn:cfg=model.config(conn)
    def reserve(n):
        try:return model.reserve(task['id'],str(n),cfg)
        except ModelBlocked:return None
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(reserve,range(4)))
    assert sum(r is not None for r in results)==1
    assert c.get('/api/v1/diagnostic/settings').json()['used_today']==1

def test_paid_operation_takeover_does_not_resend_uncertain_request(diagnostic):
    c=diagnostic;sid,s,p=setup(c);task=analyze(c,sid,p).json();claimed=durability.claim('diagnostic')
    from opslens.durability import Runtime
    rt=Runtime('diagnostic',claimed)
    def lost():raise ConnectionError('lost response')
    with pytest.raises(ConnectionError):rt.operation('generate:0',lost,paid=True)
    with db.connect() as conn:conn.execute("UPDATE durable_tasks SET lease_until=now()-interval '1 second' WHERE family='diagnostic'")
    provider=Provider();worker.process(durability.claim('diagnostic'),provider)
    assert provider.calls==0
    assert c.get('/api/v1/diagnostic/turns/'+task['id']).json()['status']=='blocked'

def test_file_batch_limits_utf8_and_request_recovery(diagnostic):
    c=diagnostic;sid=c.post('/api/v1/diagnostic/sessions',json={'request_id':str(uuid4())}).json()['id']
    path='/api/v1/diagnostic/sessions/'+sid+'/files'
    assert c.post(path,data={'request_id':str(uuid4())},files=[('files',(str(i)+'.log',b'ERROR x','text/plain')) for i in range(5)]).status_code==400
    assert c.post(path,data={'request_id':str(uuid4())},files=[('files',('x.log',b'\xff\xfe','text/plain'))]).status_code==400
    rid=str(uuid4());payload=[('files',('x.log',b'ERROR connection failed','text/plain'))]
    one=c.post(path,data={'request_id':rid},files=payload);assert one.status_code==201,one.text
    assert c.post(path,data={'request_id':rid},files=payload).json()[0]['id']==one.json()[0]['id']
    resolved=c.get('/api/v1/diagnostic/submissions/'+rid).json();assert resolved['kind']=='files'
    assert 'content' not in resolved['value'][0]

def test_history_is_explicit_and_not_current_evidence(diagnostic):
    c=diagnostic;sid,s,p=setup(c);sid2,_,_=setup(c)
    related=c.get('/api/v1/diagnostic/sessions/'+sid2+'/related').json();assert any(x['id']==sid for x in related)
    source=c.post('/api/v1/diagnostic/sessions/'+sid2+'/history-reference',json={'request_id':str(uuid4()),'reference_session_id':sid}).json()
    assert source['kind']=='history' and '不能证明当前问题' in source['provenance']['source']


def test_compatible_transport_budget_unknown_no_retry_and_secret_redaction(diagnostic,monkeypatch):
    import httpx
    c=diagnostic;sid,s,p=setup(c);task=analyze(c,sid,p).json();sent=[]
    original=httpx.Client
    def handler(request):
        sent.append(request);body=json.loads(request.content)
        assert body['max_tokens']==2048 and request.headers['Authorization']=='Bearer fixture-not-real'
        assert 'tools' not in body
        raise httpx.ReadTimeout('fixture-not-real',request=request)
    monkeypatch.setattr(model.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    worker.process(durability.claim('diagnostic'))
    assert len(sent)==1
    out=c.get('/api/v1/diagnostic/turns/'+task['id']).json();assert out['status']=='failed' and 'fixture-not-real' not in json.dumps(out)
    with db.connect() as conn:
        rows=conn.execute('SELECT * FROM diagnostic_requests').fetchall();assert len(rows)==1 and rows[0]['status']=='unknown'
        assert 'fixture-not-real' not in rows[0]['error']
    assert durability.claim('diagnostic') is None


def test_three_real_reproductions_continue_with_controlled_reports(diagnostic):
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('fixtures',Path('scripts/reproduce_diagnostic_cases.py'))
    fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)
    c=diagnostic
    class Controlled:
        def generate(self,turn,stage,cfg,packet):
            context=json.loads(packet[1]['content']);ref=context['sources'][0]
            quote=ref['content'].strip().splitlines()[-1][:200]
            return {'content':json.dumps({'summary':'根据提供资料继续核对，尚未验证根因','facts':[{'text':'提供的原文记录了这一现象','citations':[{'id':ref['id'],'quote':quote}]}],'candidate_causes':[],'next_checks':[{'purpose':'核对当前配置或监听状态','operation':check,'expected':'补充实际检查输出以判断是否继续','changes_state':False}],'missing_information':[]}), 'usage_unknown':False}
    for case in fixtures.reproduce():
        check=case['check'];sid,s,p=setup(c,case['log'])
        assert s['summary']['groups'] and any('Error' in x['content'] for x in p['segments'])
        first=analyze(c,sid,p).json();worker.process(durability.claim('diagnostic'),Controlled())
        assert c.get('/api/v1/diagnostic/turns/'+first['id']).json()['status']=='completed'
        follow=c.post('/api/v1/diagnostic/sessions/'+sid+'/sources',json={'request_id':str(uuid4()),'kind':'check_result','name':'真实隔离检查输出','content':case['followup']}).json()
        nextp=c.post('/api/v1/diagnostic/sessions/'+sid+'/previews',json={'request_id':str(uuid4()),'source_ids':[follow['id']]}).json()
        second=analyze(c,sid,nextp).json();worker.process(durability.claim('diagnostic'),Controlled())
        assert c.get('/api/v1/diagnostic/turns/'+second['id']).json()['status']=='completed'
        assert len(c.get('/api/v1/diagnostic/sessions/'+sid+'/sources').json())==2
        assert case['followup'] in c.get('/api/v1/diagnostic/sessions/'+sid+'/export').text


def test_disabled_profile_stops_without_transport_or_request_reservation(diagnostic):
    c=diagnostic;sid,s,p=setup(c)
    cfg=c.get('/api/v1/diagnostic/settings').json()['settings'];cfg['enabled']=False
    c.post('/api/v1/diagnostic/settings',json=cfg)
    p=c.post('/api/v1/diagnostic/sessions/'+sid+'/previews',json={'request_id':str(uuid4()),'source_ids':[s['id']]}).json()
    assert analyze(c,sid,p).status_code==409
    with db.connect() as conn:assert conn.execute('SELECT count(*) AS n FROM diagnostic_requests').fetchone()['n']==0


def test_upload_batch_is_atomic_and_rejects_changed_count(diagnostic,monkeypatch):
    c=diagnostic;sid=c.post('/api/v1/diagnostic/sessions',json={'request_id':str(uuid4())}).json()['id'];base='/api/v1/diagnostic/sessions/'+sid;rid=str(uuid4())
    files=[('files',('a.log',b'ERROR first','text/plain')),('files',('b.log',b'ERROR second','text/plain'))]
    original=store.add_source;calls=0
    def failure(*args):
        nonlocal calls
        calls+=1
        if calls==2:raise ValueError('isolated storage failure')
        return original(*args)
    monkeypatch.setattr(store,'add_source',failure)
    assert c.post(base+'/files',data={'request_id':rid},files=files).status_code==400
    assert c.get(base+'/sources').json()==[] and c.get('/api/v1/diagnostic/submissions/'+rid).status_code==404
    monkeypatch.setattr(store,'add_source',original)
    assert c.post(base+'/files',data={'request_id':rid},files=files).status_code==201
    assert c.post(base+'/files',data={'request_id':rid},files=files[:1]).status_code==409
    assert len(c.get(base+'/sources').json())==2
