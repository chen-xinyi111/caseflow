import copy
from datetime import datetime,timezone,timedelta
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import pytest
from opslens import db
from opslens.rag.store import jsonb
from opslens.investigation import worker as investigation_worker
from opslens.recovery import store,worker
from opslens.recovery.contracts import Propose,Approve
from test_investigation_database import client,pytestmark
from test_verification_core import Model
from test_collaboration_core import FakeTools
from test_recovery_core import state

class Adapter:
    def __init__(self):self.value=state();self.actions=[];self.probes=[];self.pass_probe=True;self.unknown=False
    def snapshot(self):return copy.deepcopy(self.value)
    def mutate(self,action,current):
        self.actions.append(action);self.value['bindings']['worker']['state']='running'
        if self.unknown:raise TimeoutError('response lost')
    def submit_probe(self,request_id):self.probes.append(request_id);return {'id':str(uuid4())}
    def verify(self,i):return {'passed':self.pass_probe,'probe':{'id':str(i),'status':'completed','result':{'char_count':31,'word_count':5 if self.pass_probe else 1},'worker_version':'v1'},'reason':'probe check'}
    def read_probe(self,i):return self.verify(i)['probe']

@pytest.fixture
def recovery(client,monkeypatch):
    adapter=Adapter();monkeypatch.setattr(store,'Adapter',lambda:adapter)
    body={'request_id':str(uuid4()),'question':'worker waiting','target':'fault','mode':'verified'}
    task=client.post('/api/v1/investigations',json=body).json()
    investigation_worker.process(investigation_worker.claim(),Model(),FakeTools())
    return adapter,task['id']
def propose(client,inv):return client.post('/api/v1/recovery/plans',json={'request_id':str(uuid4()),'investigation_id':inv,'action':'start_worker'})
def approval(p):return {'request_id':str(uuid4()),'version':p['version'],'digest':p['digest'],'confirm':True}

def test_unapproved_and_superseded_plans_never_change_service(client,recovery):
    a,inv=recovery;p=propose(client,inv).json()
    assert p['status']=='proposed' and worker.store.claim() is None and a.actions==[] and a.probes==[]
    new=propose(client,inv).json();assert new['version']==2
    assert client.post('/api/v1/recovery/plans/'+p['id']+'/approve',json=approval(p)).status_code==400
    assert client.post('/api/v1/recovery/plans/'+new['id']+'/approve',json=approval(new)|{'digest':'0'*64}).status_code==400
    assert a.actions==[]

def test_approval_execution_probe_and_duplicate_delivery_are_durable(client,recovery):
    a,inv=recovery;p=propose(client,inv).json();body=approval(p)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:store.approve(p['id'],Approve(**body)),range(4)))
    assert all(r['status']=='approved' for r in results)
    claimed=store.claim();worker.process(claimed,a);worker.process(claimed,a)
    saved=client.get('/api/v1/recovery/plans/'+p['id']).json()
    assert saved['status']=='completed' and saved['result']['passed'] and len(a.actions)==1 and len(a.probes)==1
    assert client.post('/api/v1/recovery/plans/'+p['id']+'/approve',json=body).json()['status']=='completed'
    text=client.get('/api/v1/recovery/plans/'+p['id']+'/events').text
    assert all(v in text for v in ('approved','action_issued','verifying','probe_submitted','finished'))
    first=int(next(v[4:] for v in text.splitlines() if v.startswith('id: ')))
    replay=client.get('/api/v1/recovery/plans/'+p['id']+'/events',headers={'Last-Event-ID':str(first)}).text
    assert f'id: {first}\n' not in replay

def test_changed_state_or_expiry_blocks_before_mutation(client,recovery):
    a,inv=recovery;p=propose(client,inv).json();store.approve(p['id'],Approve(**approval(p)))
    a.value['bindings']['worker']['id']='different'
    worker.process(store.claim(),a)
    assert client.get('/api/v1/recovery/plans/'+p['id']).json()['status']=='blocked' and a.actions==[]
    a.value=state();p=propose(client,inv).json()
    with db.connect() as c:c.execute('UPDATE recovery_plans SET expires_at=%s WHERE id=%s',(datetime.now(timezone.utc)-timedelta(seconds=1),p['id']))
    assert client.post('/api/v1/recovery/plans/'+p['id']+'/approve',json=approval(p)).status_code==400

def test_unknown_response_never_reissues_and_readonly_recheck_is_separate(client,recovery):
    a,inv=recovery;p=propose(client,inv).json();store.approve(p['id'],Approve(**approval(p)));claimed=store.claim();a.unknown=True
    worker.process(claimed,a);worker.process(claimed,a)
    assert client.get('/api/v1/recovery/plans/'+p['id']).json()['status']=='manual'
    observed=worker.reconcile(p['id'],a)
    assert observed['action_reissued'] is False and observed['probe'] is None
    assert observed['action_record']['status']=='issued'
    assert len(a.actions)==1 and a.probes==[] and store.claim() is None

def test_failed_probe_is_not_reported_as_recovered(client,recovery):
    a,inv=recovery;p=propose(client,inv).json();store.approve(p['id'],Approve(**approval(p)));a.pass_probe=False
    worker.process(store.claim(),a)
    saved=client.get('/api/v1/recovery/plans/'+p['id']).json();assert saved['status']=='failed' and not saved['result']['passed']

def test_restart_preserves_issued_actions_without_replay(client,recovery):
    a,inv=recovery;p=propose(client,inv).json();store.approve(p['id'],Approve(**approval(p)));store.claim()
    with db.connect() as c:c.execute("INSERT INTO recovery_actions(id,plan_id,action,status) VALUES (%s,%s,'start_worker','issued')",(uuid4(),p['id']))
    db.initialize()
    assert client.get('/api/v1/recovery/plans/'+p['id']).json()['status']=='executing' and store.claim() is None
    with db.connect() as c:c.execute("UPDATE durable_tasks SET lease_until=now()-interval '1 second' WHERE family='recovery' AND task_id=%s",(p['id'],))
    worker.process(store.claim(),a)
    assert client.get('/api/v1/recovery/plans/'+p['id']).json()['status']=='manual' and a.actions==[]
    with db.connect() as c:assert c.execute('SELECT count(*) n FROM recovery_actions WHERE plan_id=%s',(p['id'],)).fetchone()['n']==1

def test_insufficient_and_other_instance_cannot_create_recovery(client,recovery):
    a,inv=recovery
    for invalid in ('insufficient','healthy'):
        with db.connect() as c:
            if invalid=='insufficient':c.execute("UPDATE investigations SET verification='{}' WHERE id=%s",(inv,))
            else:c.execute("UPDATE investigations SET input=jsonb_set(input,'{target}','\"healthy\"') WHERE id=%s",(inv,))
        assert propose(client,inv).status_code==400
    assert a.actions==[]


def test_readiness_is_polled_without_repeating_the_action(client,recovery):
    a,inv=recovery;p=propose(client,inv).json();store.approve(p['id'],Approve(**approval(p)))
    original=a.snapshot;calls=0
    def warming_up():
        nonlocal calls
        calls+=1
        if calls==2:raise ValueError('API initializing')
        return original()
    a.snapshot=warming_up
    worker.process(store.claim(),a)
    assert client.get('/api/v1/recovery/plans/'+p['id']).json()['status']=='completed'
    assert a.actions==['start_worker'] and len(a.probes)==1
