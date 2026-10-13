import json
from uuid import uuid4
from datetime import datetime,timezone
from opslens import db
from opslens.rag.store import jsonb
from opslens.investigation import worker
from test_investigation_database import client,pytestmark
from test_verification_core import Model
from test_collaboration_core import FakeTools


def test_connection_failure_stops_verification_before_any_model_call(client,monkeypatch):
    from opslens.rag.provider import ModelFailure
    class Offline:
        def prepare_parallel(self):raise ModelFailure('connection unavailable')
        def generate(self,*args):raise AssertionError('no generation allowed')
    monkeypatch.setattr(worker,'GenerationProvider',Offline)
    task=client.post('/api/v1/investigations',json={'request_id':str(uuid4()),'question':'check','target':'fault','mode':'verified'}).json()
    worker.process(worker.claim(),tools=FakeTools())
    saved=client.get('/api/v1/investigations/'+task['id']).json()
    assert saved['status']=='failed' and saved['report'] is None
    assert saved['verification']['status']=='failed'
    assert saved['verification']['stop_reason']=='connection unavailable'
    assert saved['verification']['rounds']==[]


def snapshot(lab='fault',source='docker.services',success=True):
    i,collection=uuid4(),uuid4()
    text=json.dumps([{'service':'worker','state':'running','health':None}],indent=2)
    with db.connect() as c:
        c.execute("INSERT INTO collections(id,lab,status) VALUES (%s,%s,'completed')",(collection,lab))
        c.execute('INSERT INTO evidence(id,collection_id,lab,source,collected_at,success,content,sha256) VALUES (%s,%s,%s,%s,now(),%s,%s,%s)',(i,collection,lab,source,success,text,'fixture'))
    return i


def test_verified_progress_report_rounds_and_replan_are_persistent(client):
    body={'request_id':str(uuid4()),'question':'check worker','target':'fault','mode':'verified'}
    task=client.post('/api/v1/investigations',json=body).json()
    assert task['input']['baseline']=='verified-agent-v1'
    worker.process(worker.claim(),Model(uncertain_rounds=1),FakeTools())
    saved=client.get('/api/v1/investigations/'+task['id']).json()
    assert saved['verification']['status']=='confirmed'
    assert len(saved['verification']['rounds'])==2 and len(saved['verification']['replans'])==1
    assert saved['metrics']['model_calls']==7 and 'verification' not in saved['metrics'] and 'references' not in saved['metrics']
    events=client.get('/api/v1/investigations/'+task['id']+'/events').text
    assert 'verification_started' in events and 'replan_ready' in events and 'followup_completed' in events
    ids=[int(line[4:]) for line in events.splitlines() if line.startswith('id: ')]
    replay=client.get('/api/v1/investigations/'+task['id']+'/events',headers={'Last-Event-ID':str(ids[0])}).text
    assert f'id: {ids[0]}\n' not in replay and f'id: {ids[-1]}\n' in replay


def test_snapshot_uuid_is_same_target_successful_service_source_only(client):
    body={'request_id':str(uuid4()),'question':'check snapshot','target':'fault','mode':'verified'}
    for i in (uuid4(),snapshot(lab='healthy'),snapshot(source='docker.logs.worker'),snapshot(success=False)):
        assert client.post('/api/v1/investigations',json=body|{'request_id':str(uuid4()),'snapshot_id':str(i)}).status_code==400
    valid=snapshot()
    assert client.post('/api/v1/investigations',json=body|{'snapshot_id':str(valid),'mode':'multi'}).status_code==400
    task=client.post('/api/v1/investigations',json=body|{'snapshot_id':str(valid)}).json()
    assert task['input']['snapshot_id']==str(valid)
    from opslens.investigation.tools import Tools
    ref=Tools(task['input']).load_snapshot(valid)
    assert ref['scope']=='historical' and ref['url']=='/api/v1/evidence/'+str(valid)
    assert client.get(ref['url']).status_code==200


def test_conflict_example_requires_real_snapshot_and_sample(client):
    assert client.get('/api/v1/investigations/examples/conflict').json()['available'] is False
    i=snapshot();job=uuid4()
    with db.connect() as c:c.execute("INSERT INTO fault_cases(id,title,description,sample_job_id) VALUES ('worker_stopped','worker','fixture',%s)",(job,))
    example=client.get('/api/v1/investigations/examples/conflict').json()
    assert example['available'] and example['snapshot_id']==str(i) and example['job_id']==str(job)


def test_restart_preserves_verdicts_but_interrupts_active_round_and_replan(client):
    body={'request_id':str(uuid4()),'question':'x','target':'fault','mode':'verified'}
    task=client.post('/api/v1/investigations',json=body).json()
    audit={'status':'running','rounds':[{'number':1,'candidate':{'assessment':'insufficient','summary':'x','root_cause':None,'findings':[],'missing_information':['x'],'next_steps':[]},'started_at':datetime.now(timezone.utc).isoformat()}],
        'replans':[{'number':1,'action':'recheck','reason':'x','task_ids':[],'questions':[],'status':'running'}]}
    with db.connect() as c:c.execute("UPDATE investigations SET status='running',verification=%s WHERE id=%s",(jsonb(audit),task['id']))
    db.initialize()
    saved=client.get('/api/v1/investigations/'+task['id']).json()
    assert saved['verification']['status']=='interrupted' and saved['verification']['rounds'][0]['error']
    assert saved['verification']['replans'][0]['status']=='interrupted'
