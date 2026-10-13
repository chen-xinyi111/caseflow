import json
from uuid import uuid4
from opslens import db
from opslens.investigation import worker
from test_investigation_database import client, pytestmark
from test_collaboration_core import Model, FakeTools

def test_multi_report_plan_events_and_request_mode_are_persistent(client):
    body={"request_id":str(uuid4()),"question":"worker waiting","target":"fault","mode":"multi"}
    response=client.post('/api/v1/investigations',json=body)
    assert response.status_code==202,response.text
    task=response.json()
    assert task['input']['baseline']=='multi-agent-v1'
    assert client.post('/api/v1/investigations',json=body|{'mode':'single'}).status_code==409
    assert client.post('/api/v1/investigations',json=body|{'mode':'shell'}).status_code==422
    worker.process(worker.claim(),Model(),FakeTools())
    saved=client.get('/api/v1/investigations/'+task['id']).json()
    assert saved['status']=='completed'
    assert len(saved['collaboration']['tasks'])==2
    assert saved['metrics']['model_calls']==6 and saved['metrics']['agent_count']==3
    assert saved['collaboration']['merge_status']=='completed'
    assert {r['kind'] for r in saved['references']}=={'knowledge','runtime'}
    assert len({r['id'] for r in saved['references']})==len(saved['references'])
    events=client.get('/api/v1/investigations/'+task['id']+'/events').text
    assert 'plan_ready' in events and 'subtask_started' in events and 'merge_completed' in events
    with db.connect() as c:
        records=c.execute("SELECT * FROM investigation_steps WHERE investigation_id=%s ORDER BY id",(task['id'],)).fetchall()
    assert any(r['payload'].get('task_id')=='runtime' for r in records)
    assert any(r['payload'].get('detail',{}).get('response') for r in records)

def test_quota_block_preserves_plan_errors_and_call_counts(client):
    body={'request_id':str(uuid4()),'question':'x','target':'fault','mode':'multi'}
    task=client.post('/api/v1/investigations',json=body).json()
    worker.process(worker.claim(),Model(blocked=True),FakeTools())
    saved=client.get('/api/v1/investigations/'+task['id']).json()
    assert saved['status']=='blocked' and saved['report'] is None
    assert saved['collaboration']['merge_status']=='blocked'
    assert saved['metrics']['model_calls']<=3
    assert any(t['error'] for t in saved['collaboration']['tasks'])

def test_existing_input_without_mode_remains_idempotent_single(client):
    body={'request_id':str(uuid4()),'question':'x','target':'fault'}
    task=client.post('/api/v1/investigations',json=body).json()
    with db.connect() as c:c.execute("UPDATE investigations SET input=input-'mode' WHERE id=%s",(task['id'],))
    assert client.post('/api/v1/investigations',json=body).json()['id']==task['id']

def test_interrupted_collaboration_stays_visible_without_active_tasks(client):
    from opslens.rag.store import jsonb
    body={'request_id':str(uuid4()),'question':'x','target':'fault','mode':'multi'}
    task=client.post('/api/v1/investigations',json=body).json()
    progress={'reason':'fixture','merge_status':'waiting','tasks':[
        {'id':'r','role':'runtime','objective':'read','depends_on':[],'status':'running','output':None},
        {'id':'k','role':'knowledge','objective':'search','depends_on':['r'],'status':'pending','output':None}]}
    with db.connect() as c:c.execute("UPDATE investigations SET status='running',collaboration=%s WHERE id=%s",(jsonb(progress),task['id']))
    db.initialize()
    saved=client.get('/api/v1/investigations/'+task['id']).json()
    assert saved['status']=='failed' and saved['collaboration']['merge_status']=='failed'
    assert [t['status'] for t in saved['collaboration']['tasks']]==['failed','skipped']
