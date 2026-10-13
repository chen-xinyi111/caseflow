import json
from uuid import uuid4
import pytest
from opslens import db,external
from opslens.investigation.tools import Tools
from opslens.investigation import worker
from opslens.rag.provider import QuotaGuard,ModelBlocked
from test_investigation_database import client,pytestmark

@pytest.fixture
def ext(client,monkeypatch):
    external.initialize()
    commands=[]
    def docker(args):
        commands.append(args)
        if args[0]=='ps':return 'a'*12
        if args[0]=='inspect':return json.dumps({'project':'qinghe-after-sales','service':'agent_service','image':'qinghe:v1','state':'exited','started_at':'2026-01-01T00:00:00Z'})
        if args[0]=='logs':return 'worker failed password=private '+('z'*70000)
        raise AssertionError('Unexpected Docker command '+str(args))
    monkeypatch.setattr(external,'docker',docker)
    # The default argument is injected explicitly: no real Docker or model calls.
    monkeypatch.setattr(external,'capture',lambda i,r:original_capture(i,r,run=docker))
    monkeypatch.setattr(QuotaGuard,'check',lambda *a:None)
    return client,commands

original_capture=external.capture

def preview(client):
    r=client.post('/api/v1/external-targets/'+str(external.DEFAULT_ID)+'/snapshots',json={'request_id':str(uuid4())})
    assert r.status_code==200,r.text
    return r.json()

def grant(client,s,ids=None):
    ids=ids or [r['id'] for r in s['items']+s['documents'] if r.get('success') is not False]
    r=client.post('/api/v1/external-snapshots/'+s['id']+'/consent',json={'digest':s['sha256'],'selected_ids':ids,'confirm':True})
    assert r.status_code==200,r.text
    return r.json()

def body(s,proof):
    return dict(request_id=str(uuid4()),target='external',question='为什么应用无法访问？',mode='single',external_target_id=s['target_id'],external_snapshot_id=s['id'],external_consent_id=proof['id'])

def test_capture_is_readonly_limited_and_idempotent(ext):
    c,commands=ext;s=preview(c)
    assert all(a[0] in ['ps','inspect','logs'] for a in commands)
    assert all(a[0]!='logs' or a[1:4]==['--timestamps','--tail','80'] for a in commands)
    assert 'DO_NOT_READ' not in json.dumps(s)
    assert len(s['items'][0]['content'].encode())<=external.MAX_LOG_BYTES
    assert 'exited' in s['items'][-1]['content'] and s['errors']
    n=len(commands)
    r=c.post('/api/v1/external-targets/'+s['target_id']+'/snapshots',json={'request_id':s['request_id']})
    assert r.json()['id']==s['id'] and len(commands)==n

def test_mismatched_container_identity_and_missing_container_are_explicit(ext):
    c,_=ext
    def wrong(args):
        if args[0]=='ps':return 'b'*12
        return json.dumps({'project':'other'})
    s=original_capture(external.DEFAULT_ID,uuid4(),run=wrong)
    assert s['items'][-1]['success'] is False and '不匹配' in s['errors'][0]
    empty=original_capture(external.DEFAULT_ID,uuid4(),run=lambda args:'')
    assert json.loads(empty['items'][0]['content'])['missing_services']==['agent_service','kafka','postgres','redis']

def test_unconfirmed_wrong_digest_and_unavailable_model_never_queue(ext,monkeypatch):
    c,_=ext;s=preview(c)
    b=body(s,{'id':str(uuid4())})
    assert c.post('/api/v1/investigations',json=b).status_code==400
    path='/api/v1/external-snapshots/'+s['id']+'/consent'
    assert c.post(path,json={'digest':'0'*64,'selected_ids':[s['items'][-1]['id']],'confirm':True}).status_code==400
    assert c.post(path,json={'digest':s['sha256'],'selected_ids':[s['items'][-1]['id']],'confirm':False}).status_code==400
    p=grant(c,s)
    def block(*args):raise ModelBlocked('额度不可用')
    monkeypatch.setattr(QuotaGuard,'check',block)
    assert c.post('/api/v1/investigations',json=body(s,p)).status_code==409
    with db.connect() as conn:assert conn.execute("SELECT count(*) AS n FROM investigations WHERE input->>'target'='external'").fetchone()['n']==0

def test_excluded_logs_and_project_documents_are_only_sources(ext):
    c,_=ext;s=preview(c);state=s['items'][-1]['id'];p=grant(c,s,[state])
    payload=body(s,p);tools=Tools(payload)
    refs=tools.execute('collect_runtime',{})['references']
    assert len(refs)==1 and refs[0]['source']=='docker.services'
    assert tools.execute('search_knowledge',{'query':'售后'})['references']==[]
    assert tools.execute('collect_runtime',{})['unavailable']
    assert tools.execute('read_job',{'job_id':str(uuid4())})['unavailable']
    assert c.get(refs[0]['url']).json()['content']==s['items'][-1]['content']
    payload['external_target_id']=str(uuid4())
    with pytest.raises(ValueError):Tools(payload).execute('collect_runtime',{})

def test_confirmed_task_persists_but_external_recovery_is_rejected(ext):
    c,_=ext;s=preview(c);p=grant(c,s);b=body(s,p)
    r=c.post('/api/v1/investigations',json=b);assert r.status_code==202,r.text
    assert c.post('/api/v1/investigations',json=b).json()['id']==r.json()['id']
    tools=Tools(r.json()['input']);refs=tools.execute('collect_runtime',{})['references']
    assert refs and tools.execute('search_knowledge',{'query':'售后 Docker'})['references']
    with db.connect() as conn:
        conn.execute("UPDATE investigations SET status='completed',report=%s,verification=%s WHERE id=%s",(external.jsonb({'assessment':'supported','summary':'fixture','root_cause':'fixture','findings':[],'missing_information':[],'next_steps':[]}),external.jsonb({'status':'confirmed'}),r.json()['id']))
    out=c.post('/api/v1/recovery/plans',json={'request_id':str(uuid4()),'investigation_id':r.json()['id'],'action':'start_worker'})
    assert out.status_code==400
    assert c.get('/api/v1/investigations/'+r.json()['id']).json()['input']['external_snapshot_id']==s['id']

def test_changed_confirmed_snapshot_blocks_before_model_generate(ext):
    c,_=ext;s=preview(c);p=grant(c,s);r=c.post('/api/v1/investigations',json=body(s,p)).json()
    with db.connect() as conn:conn.execute("UPDATE external_snapshots SET items='[]' WHERE id=%s",(s['id'],))
    class Model:
        name='controlled-external-test'
        def generate(self,*args):raise AssertionError('Must never call model')
    worker.process(worker.claim(),Model())
    saved=c.get('/api/v1/investigations/'+r['id']).json()
    assert saved['status']=='failed' and '变化' in saved['error']

def test_external_worker_finishes_with_citations_without_network(ext):
    c,_=ext;s=preview(c);state=s['items'][-1]['id'];p=grant(c,s,[state]);r=c.post('/api/v1/investigations',json=body(s,p)).json()
    class Model:
        name='controlled-external-test'
        def generate(self,messages,definitions):
            if not any(m['role']=='tool' for m in messages):
                return {'role':'assistant','content':None,'tool_calls':[{'id':'collect','type':'function','function':{'name':'collect_runtime','arguments':'{}'}}]}
            refs=json.loads(next(m['content'] for m in messages if m['role']=='tool'))['references']
            return {'role':'assistant','content':json.dumps({'assessment':'supported','summary':'采集时应用已停止','root_cause':'采集时应用容器已停止','findings':[{'statement':'采集时应用已停止','references':[{'id':refs[0]['id'],'quote':'"state": "exited"'}]}],'missing_information':[],'next_steps':['请人工核对停机原因']})}
    worker.process(worker.claim(),Model())
    saved=c.get('/api/v1/investigations/'+r['id']).json()
    assert saved['status']=='completed' and saved['metrics']['model']=='controlled-external-test'
    assert saved['references'] and c.get(saved['references'][0]['url']).status_code==200
    assert c.get('/api/v1/investigations?view=daily').json()==[]

def test_each_model_call_revalidates_confirmation(ext):
    c,_=ext;s=preview(c);p=grant(c,s);payload=body(s,p)
    class Model:
        calls=0
        def generate(self,*args):self.calls+=1;return {}
    m=Model();guard=external.AuthorizedModel(m,payload)
    guard.generate([],[]);assert m.calls==1
    with db.connect() as conn:conn.execute('DELETE FROM external_consents WHERE id=%s',(p['id'],))
    with pytest.raises(ValueError):guard.generate([],[])
    assert m.calls==1

def test_external_verified_pipeline_uses_pinned_project_sources_without_semantic_index(ext):
    c,_=ext;s=preview(c);p=grant(c,s,[s['items'][-1]['id'],s['documents'][0]['id']])
    with db.connect() as conn:conn.execute('UPDATE kb_indexes SET active=false')
    b=body(s,p)|{'mode':'verified'};r=c.post('/api/v1/investigations',json=b)
    assert r.status_code==202,r.text
    class Model:
        name='controlled-external-verified-test'
        def generate(self,messages,definitions):
            prompt=messages[0]['content'];ctx=json.loads(messages[1]['content'])
            def findings(refs):
                return [{'statement':'采集时应用已停止' if ref['kind']=='runtime' else '部署说明涉及Docker',
                         'references':[{'id':ref['id'],'quote':'exited' if ref['kind']=='runtime' else 'Docker'}]} for ref in refs]
            if '规划协调' in prompt:
                out={'reason':'核对状态及项目说明','tasks':[{'id':'runtime','role':'runtime','objective':'核对容器状态','depends_on':[]},{'id':'knowledge','role':'knowledge','objective':'Docker','depends_on':[]}]}
            elif '独立调查' in prompt:
                out={'summary':'已读取确认资料','findings':findings(ctx['collected_evidence'][:1]),'missing_information':[]}
            elif '候选协调' in prompt:
                out={'assessment':'supported','summary':'采集时应用已停止','root_cause':'应用容器处于停止状态','findings':findings(ctx['references']),'missing_information':[],'next_steps':['人工核对停机原因']}
            elif '核验 Agent' in prompt:
                out={'decision':'confirmed','reason':'快照和项目来源一致','report':ctx['candidate'],'issues':[]}
            else:raise AssertionError('Unexpected phase')
            return {'role':'assistant','content':json.dumps(out)}
    worker.process(worker.claim(),Model())
    saved=c.get('/api/v1/investigations/'+r.json()['id']).json()
    assert saved['status']=='completed',saved.get('error')
    assert saved['verification']['status']=='confirmed',saved['verification']
    assert {ref['kind'] for ref in saved['references']}=={'runtime','knowledge'}
    assert saved['metrics']['model_calls']==5

def test_new_document_requires_new_preview_and_cannot_enter_old_snapshot(ext):
    c,_=ext;s=preview(c);p=grant(c,s)
    r=c.post('/api/v1/external-targets/'+s['target_id']+'/documents',json={'title':'新手册','content':'新版本故障排查资料'})
    assert r.status_code==201
    tools=Tools(body(s,p));assert all(r['source']!='新手册' for r in tools.execute('search_knowledge',{'query':'新版本故障'})['references'])
    new=preview(c);assert any(d['source']=='新手册' for d in new['documents'])
    assert c.post('/api/v1/external-snapshots/'+new['id']+'/consent',json={'digest':s['sha256'],'selected_ids':[new['items'][-1]['id']],'confirm':True}).status_code==400
