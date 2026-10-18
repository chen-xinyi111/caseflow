"""Isolated schema integration; controlled model responses never count as live model evidence."""
import json
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
import psycopg
import pytest
from psycopg.rows import dict_row
from fastapi.testclient import TestClient
from opslens import db,durability
from opslens.config import platform_dsn
from opslens.api import app
from opslens.caseflow import store,agent
from opslens.diagnostic import store as diagnostic

@pytest.fixture
def client(monkeypatch):
    schema='cf_test_'+uuid4().hex;original=db.connect
    with original() as c:c.execute(psycopg.sql.SQL('CREATE SCHEMA {}').format(psycopg.sql.Identifier(schema)))
    @contextmanager
    def connect():
        with psycopg.connect(platform_dsn(),row_factory=dict_row,options=f'-c search_path={schema},public') as c:yield c
    monkeypatch.setattr(db,'connect',connect)
    try:
        with connect() as c:
            for file in ('schema.sql','durability.sql','rag/schema.sql','investigation/schema.sql','recovery/schema.sql'):
                c.execute((Path('src/opslens')/file).read_text(encoding='utf-8'))
        diagnostic.initialize();store.initialize()
        yield TestClient(app)
    finally:
        with original() as c:c.execute(psycopg.sql.SQL('DROP SCHEMA {} CASCADE').format(psycopg.sql.Identifier(schema)))

class ControlledProvider:
    def __init__(self,rt,cfg):self.rt=rt;self.calls=0
    def generate(self,messages,tools,key):
        def response():
            if key.startswith('extract:'):
                data=json.loads(messages[-1]['content']);line=data['text'].splitlines()[0][:120]
                content={'requirements':[{'text':line,'citations':[{'document_id':data['document_id'],'page':data['page'],'quote':line}],'questions':[]}]}
                return {'role':'assistant','content':json.dumps(content,ensure_ascii=False),'usage':{'total_tokens':10}}
            n=int(key.split(':')[-1])
            if n==0:return {'role':'assistant','content':'','tool_calls':[{'id':'req','type':'function','function':{'name':'read_requirements','arguments':'{}'}},{'id':'existing','type':'function','function':{'name':'existing_cases','arguments':'{}'}}],'usage':{'total_tokens':10}}
            if n==1:return {'role':'assistant','content':'','tool_calls':[{'id':'search','type':'function','function':{'name':'search_documents','arguments':json.dumps({'query':'预约 时长'})}}],'usage':{'total_tokens':10}}
            req=json.loads(next(m['content'] for m in messages if m.get('tool_call_id')=='req'))[0]
            content={'cases':[{'title':'允许最短预约时长','requirement_ids':[req['id']],'preconditions':'用户已登录且存在空闲会议室','data':'30分钟','steps':['选择会议室','提交30分钟预约'],'expected':'其他条件满足时允许预约','category':'边界','basis':'explicit','citations':req['citations']}]}
            return {'role':'assistant','content':json.dumps(content,ensure_ascii=False),'usage':{'total_tokens':10}}
        return self.rt.operation(key,response,paid=True)

def project(client):
    p=client.post('/api/v1/caseflow/projects',json={'name':'隔离流程检查'}).json()
    p=client.post('/api/v1/caseflow/projects/'+p['id']+'/texts',json={'revision':p['revision'],'name':'预约需求','content':'预约时长为30至120分钟。'}).json()
    return p
def task_body(p,kind):return {'revision':p['revision'],'request_id':str(uuid4()),'kind':kind,'document_ids':[d['id'] for d in p['state']['documents']],'requirement_ids':[r['id'] for r in p['state']['requirements'] if r['status']=='confirmed'],'consent':True}

def test_full_agent_rag_review_export_execution(client,monkeypatch):
    monkeypatch.setattr(agent,'Provider',ControlledProvider)
    p=project(client);url='/api/v1/caseflow/projects/'+p['id'];body=task_body(p,'extract')
    t=client.post(url+'/tasks',json=body);assert t.status_code==202,t.text
    agent.process(durability.claim('caseflow'))
    assert client.post(url+'/tasks',json=body).json()['id']==t.json()['id']
    p=client.get(url).json();r=p['state']['requirements'][0]
    p=client.post(url+'/requirements/'+r['id'],json={'revision':p['revision'],'text':r['text'],'status':'confirmed'}).json()
    t=client.post(url+'/tasks',json=task_body(p,'generate'));assert t.status_code==202,t.text
    agent.process(durability.claim('caseflow'))
    task=client.get('/api/v1/caseflow/tasks/'+t.json()['id']).json();assert task['status']=='completed',task
    p=client.get(url).json();case=p['state']['cases'][0]
    p=client.post(url+'/cases/'+case['id'],json={'revision':p['revision'],'status':'approved','review_note':'控制检查，核对了原文和步骤'}).json()
    assert p['state']['summary']['approved']==1
    export=client.get(url+'/export?format=xlsx');assert export.status_code==200 and export.content[:2]==b'PK'
    md=client.get(url+'/export?format=md');assert 'T001' in md.text and '已审核' in md.text
    p=client.post(url+'/runs',json={'revision':p['revision'],'request_id':str(uuid4()),'name':'隔离检查','results':[{'case_id':case['id'],'revision':1,'status':'通过','note':'仅工程检查记录'}]}).json()
    assert p['state']['runs'][0]['results'][0]['current']
    p=client.post(url+'/requirements/'+r['id'],json={'revision':p['revision'],'text':'预约时长为30至180分钟。','status':'confirmed'}).json()
    assert p['state']['cases'][0]['status']=='needs_review' and not p['state']['runs'][0]['results'][0]['current']
    with db.connect() as c:
        assert c.execute('SELECT count(*) n FROM cf_requests').fetchone()['n']==0
        assert c.execute('SELECT count(*) n FROM kb_indexes WHERE active').fetchone()['n']==0
        events=c.execute("SELECT * FROM cf_events WHERE kind='tool'").fetchall();assert any(e['payload']['name']=='search_documents' for e in events)

def test_stale_revision_cross_project_and_atomic_import(client,monkeypatch):
    monkeypatch.setattr(agent,'Provider',ControlledProvider);a=project(client);b=project(client);url='/api/v1/caseflow/projects/'+a['id']
    foreign=task_body(a,'extract')|{'document_ids':[b['state']['documents'][0]['id']]}
    assert client.post(url+'/tasks',json=foreign).status_code==404
    assert client.post(url+'/texts',json={'revision':1,'content':'stale'}).status_code==409
    assert client.post(url+'/tasks',json=task_body(a,'extract')|{'consent':False}).status_code==400
    request=task_body(a,'extract');response=client.post(url+'/tasks',json=request);assert response.status_code==202
    with db.connect() as c:c.execute("UPDATE cf_tasks SET status='failed' WHERE id=%s",(response.json()['id'],))
    assert client.post(url+'/tasks',json=request|{'document_ids':[]}).status_code==422

def test_unknown_external_operation_is_not_replayed(client):
    p=project(client);response=client.post('/api/v1/caseflow/projects/'+p['id']+'/tasks',json=task_body(p,'extract'));assert response.status_code==202
    claimed=durability.claim('caseflow');rt=durability.Runtime('caseflow',claimed)
    def unknown():raise RuntimeError('controlled interruption')
    with pytest.raises(RuntimeError):rt.operation('external',unknown,paid=True)
    with pytest.raises(durability.UnknownResult):rt.operation('external',lambda:pytest.fail('replayed'),paid=True)
    with db.connect() as c:c.execute("UPDATE durable_tasks SET lease_until=now()-interval '1 second' WHERE task_id=%s",(claimed['id'],))
    takeover=durability.claim('caseflow');assert takeover['_attempt']==2
    with pytest.raises(durability.LeaseLost):
        with rt.transaction():pass

def test_long_input_walks_every_segment_and_preserves_each_page(client,monkeypatch):
    monkeypatch.setattr(agent,'Provider',ControlledProvider)
    p=client.post('/api/v1/caseflow/projects',json={'name':'长资料检查'}).json();url='/api/v1/caseflow/projects/'+p['id']
    pages=['第'+str(i)+'页规则：预约时长为30至120分钟。\n'+('测试背景说明。'*350) for i in range(1,4)]
    p=store.public(store.update(p['id'],p['revision'],'fixture',lambda s:store.add_document(s,'长文档',pages)))
    response=client.post(url+'/tasks',json=task_body(p,'extract'));assert response.status_code==202
    agent.process(durability.claim('caseflow'))
    task=client.get('/api/v1/caseflow/tasks/'+response.json()['id']).json();assert task['status']=='completed',task
    with db.connect() as c:
        rows=c.execute("SELECT operation_key,result FROM durable_operations WHERE task_id=%s AND operation_key LIKE 'extract:%%'",(response.json()['id'],)).fetchall()
    from opslens.rag.text import split_pages
    assert len(rows)==len(split_pages(pages,max_chars=2000,overlap=150))
    assert {x['page'] for row in rows for r in json.loads(row['result']['content'])['requirements'] for x in r['citations']}=={1,2,3}

def test_conflicting_documents_are_not_silently_merged(client,monkeypatch):
    monkeypatch.setattr(agent,'Provider',ControlledProvider);p=project(client);url='/api/v1/caseflow/projects/'+p['id']
    p=client.post(url+'/texts',json={'revision':p['revision'],'name':'另一份规则','content':'预约时长为60至90分钟。'}).json()
    response=client.post(url+'/tasks',json=task_body(p,'extract'));agent.process(durability.claim('caseflow'))
    assert client.get('/api/v1/caseflow/tasks/'+response.json()['id']).json()['status']=='completed'
    p=client.get(url).json();rules=p['state']['requirements']
    assert len(rules)==2 and all(r['status']=='draft' for r in rules)
    assert len({r['document_id'] for r in rules})==2

def test_budget_reservation_preserves_first_result_and_blocks_next_call(client):
    from opslens.diagnostic import model
    from opslens.diagnostic.contracts import ModelSettings
    from opslens.rag.provider import ModelBlocked
    model.save_settings(ModelSettings(daily_limit=1))
    p=project(client);response=client.post('/api/v1/caseflow/projects/'+p['id']+'/tasks',json=task_body(p,'extract'))
    claimed=durability.claim('caseflow');rt=durability.Runtime('caseflow',claimed)
    with db.connect() as c:cfg=model.config(c)
    provider=agent.Provider(rt,cfg);sent=[]
    def first():sent.append(1);return {'content':'saved','usage':{'total_tokens':10}}
    assert provider.call('one',first)['content']=='saved'
    assert provider.call('one',lambda:pytest.fail('completed step replayed'))['content']=='saved'
    with pytest.raises(ModelBlocked):provider.call('two',lambda:pytest.fail('budget bypassed'))
    with db.connect() as c:assert c.execute('SELECT count(*) n FROM cf_requests').fetchone()['n']==1
    assert sent==[1]
