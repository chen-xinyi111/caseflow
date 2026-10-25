from uuid import uuid4
import pytest
from opslens import db, durability
from test_investigation_database import client, submit, pytestmark
from test_recovery_database import recovery,propose,approval

def expire(family, task_id):
    with db.connect() as c:
        c.execute("UPDATE durable_tasks SET lease_until=now()-interval '1 second' WHERE family=%s AND task_id=%s", (family, task_id))

def test_lease_takeover_fences_old_writer_and_limits_recovery(client):
    _, task = submit(client)
    first = durability.claim('investigation')
    assert first['id'] == task['id'] or str(first['id']) == task['id']
    assert durability.claim('investigation') is None
    expire('investigation', first['id'])
    second = durability.claim('investigation')
    assert second['_attempt'] == first['_attempt'] + 1
    with pytest.raises(durability.LeaseLost):
        with durability.Runtime('investigation', first).transaction(): pass
    expire('investigation', first['id'])
    third = durability.claim('investigation')
    assert third['_attempt'] == 3
    expire('investigation', first['id'])
    assert durability.claim('investigation') is None
    assert client.get('/api/v1/investigations/'+task['id']).json()['status'] == 'failed'

def test_saved_operation_is_reused_but_unknown_paid_call_is_blocked(client):
    _, task = submit(client)
    claimed = durability.claim('investigation')
    rt = durability.Runtime('investigation', claimed)
    calls = []
    assert rt.operation('model:a', lambda: calls.append(1) or {'answer': 7}, paid=True) == {'answer': 7}
    assert rt.operation('model:a', lambda: calls.append(2), paid=True) == {'answer': 7}
    assert calls == [1]
    with pytest.raises(SystemExit):
        rt.operation('model:unknown', lambda: (_ for _ in ()).throw(SystemExit()), paid=True)
    expire('investigation', claimed['id'])
    resumed = durability.Runtime('investigation', durability.claim('investigation'))
    with pytest.raises(durability.UnknownResult):
        resumed.operation('model:unknown', lambda: calls.append(3), paid=True)
    assert calls == [1]

def test_collection_and_kb_request_ids_are_idempotent(client):
    body = {'lab':'fault', 'request_id':str(uuid4())}
    a = client.post('/api/v1/collections', json=body)
    assert a.status_code == 202
    assert client.post('/api/v1/collections', json=body).json()['id'] == a.json()['id']
    assert client.post('/api/v1/collections', json=body|{'lab':'healthy'}).status_code == 409
    body = {'mode':'keyword', 'request_id':str(uuid4())}
    a = client.post('/api/v1/kb/indexes', json=body)
    assert a.status_code == 202
    assert client.post('/api/v1/kb/indexes', json=body).json()['id'] == a.json()['id']
    assert client.post('/api/v1/kb/indexes', json=body|{'mode':'semantic'}).status_code == 409

@pytest.mark.parametrize('mode,cut',[('single','tools'),('multi','dispatch'),('verified','plan'),('verified','candidate'),('verified','verify')])
def test_graph_checkpoint_resumes_without_repeating_saved_calls(client,monkeypatch,mode,cut):
    import json
    from opslens.investigation import worker
    from test_investigation_core import FakeTools,report
    from test_collaboration_core import Model as MultiModel,FakeTools as MultiTools
    from test_verification_core import Model as VerifiedModel
    class Single:
        def generate(self,messages,definitions):
            if messages[-1]['role']=='tool':return {'role':'assistant','content':json.dumps(report())}
            return {'role':'assistant','content':'','tool_calls':[{'id':'t','type':'function','function':{'name':'collect_runtime','arguments':'{}'}}]}
    base=Single() if mode=='single' else MultiModel() if mode=='multi' else VerifiedModel()
    class Counting:
        def __init__(self):self.calls=0
        def generate(self,*args):self.calls+=1;return base.generate(*args)
    model=Counting();tools=FakeTools() if mode=='single' else MultiTools()
    body={'request_id':str(uuid4()),'question':'worker waiting','target':'fault','mode':mode}
    task=client.post('/api/v1/investigations',json=body).json()
    original=durability.Runtime.stage;interrupted=False
    def stop_after(self,key,fn,snapshot,restore):
        nonlocal interrupted
        result=original(self,key,fn,snapshot,restore)
        # stage keys are hashes; identify the node from its returned state.
        state=snapshot()['state']
        match=(mode=='single' and result.get('tool_calls')==1) if cut=='tools' else (
          mode=='multi' and bool(state.get('collaboration',{}).get('tasks')) and all(t['status']=='completed' for t in state['collaboration']['tasks']) and not result.get('report')) if cut=='dispatch' else (
          state.get('audit',{}).get('status')=='waiting' and result.get('decision')=='candidate') if cut=='plan' else (
          result.get('decision')=='verify' and 'candidate' in result) if cut=='candidate' else state.get('audit',{}).get('status')=='confirmed'
        if match and not interrupted:interrupted=True;raise SystemExit('controlled interruption after committed checkpoint')
        return result
    monkeypatch.setattr(durability.Runtime,'stage',stop_after)
    stopped=False
    try:worker.process(worker.claim(),model,tools)
    except SystemExit:stopped=True
    assert stopped,client.get('/api/v1/investigations/'+task['id']).json()
    old_calls=model.calls;old_tools=len(tools.records if mode=='single' else tools.order)
    assert interrupted
    db.initialize() # live leases survive repeated initialization
    assert client.get('/api/v1/investigations/'+task['id']).json()['status']=='running'
    expire('investigation',task['id'])
    monkeypatch.setattr(durability.Runtime,'stage',original)
    worker.process(worker.claim(),model,tools)
    saved=client.get('/api/v1/investigations/'+task['id']).json()
    assert saved['status']=='completed',saved
    assert saved['report']['assessment']=='supported'
    assert saved['recovery']['recovery_count']==1
    assert len(tools.records if mode=='single' else tools.order)==old_tools
    assert saved['metrics']['model_calls']==model.calls
    if cut=='verify':assert model.calls==old_calls

def test_total_timeout_survives_takeover_and_cannot_issue_model(client):
    from opslens.investigation import worker
    from test_investigation_core import FakeModel,FakeTools
    _,task=submit(client);claimed=worker.claim()
    with db.connect() as c:c.execute('UPDATE durable_tasks SET elapsed_seconds=241 WHERE task_id=%s',(task['id'],))
    expire('investigation',task['id']);model=FakeModel()
    worker.process(worker.claim(),model,FakeTools())
    saved=client.get('/api/v1/investigations/'+task['id']).json()
    assert saved['status']=='failed' and model.n==0

def test_search_request_keeps_original_index_and_rejects_changed_input(client):
    body={'request_id':str(uuid4()),'query':'worker','mode':'keyword'}
    from opslens.rag import store as kb
    from opslens.rag.service import build_index
    index=kb.create_index('keyword');build_index(index['input']['index_id'])
    first=client.post('/api/v1/kb/searches',json=body)
    assert first.status_code==202
    new=kb.create_index('keyword');build_index(new['input']['index_id'])
    again=client.post('/api/v1/kb/searches',json=body)
    assert again.json()['id']==first.json()['id']
    assert again.json()['input']['index_id']==str(index['input']['index_id'])
    assert client.post('/api/v1/kb/searches',json=body|{'query':'changed'}).status_code==409

@pytest.mark.parametrize('cut',['before_action','after_action','probe_response','before_result'])
def test_recovery_takeover_never_reissues_action_or_probe(client,recovery,monkeypatch,cut):
    from opslens.recovery import store,worker
    from opslens.recovery.contracts import Approve
    a,inv=recovery;plan=propose(client,inv).json()
    store.approve(plan['id'],Approve(**approval(plan)))
    claimed=store.claim();original_mutate=a.mutate;original_submit=a.submit_probe;original_verify=a.verify
    jobs={}
    def mutate(action,current):
        original_mutate(action,current)
        if cut=='after_action':raise SystemExit('process ended after actual action')
    def submit_probe(request_id):
        result=original_submit(request_id);jobs[request_id]=result
        if cut=='probe_response':raise SystemExit('process ended after probe submission')
        return result
    def verify(i):
        result=original_verify(i)
        if cut=='before_result':raise SystemExit('process ended after verified result')
        return result
    a.mutate=mutate;a.submit_probe=submit_probe;a.verify=verify
    a.find_probe=lambda rid:jobs.get(rid)
    if cut!='before_action':
        with pytest.raises(SystemExit):worker.process(claimed,a)
    expire('recovery',plan['id'])
    a.mutate=original_mutate;a.submit_probe=original_submit;a.verify=original_verify
    worker.process(store.claim(),a)
    saved=client.get('/api/v1/recovery/plans/'+plan['id']).json()
    assert saved['status']=='completed' and saved['result']['passed'],saved
    assert len(a.actions)==len(a.probes)==1
    with db.connect() as c:assert c.execute('SELECT count(*) n FROM recovery_actions WHERE plan_id=%s',(plan['id'],)).fetchone()['n']==1

def test_probe_intent_without_findable_job_stops_without_resubmitting(client,recovery):
    from opslens.recovery import store,worker
    from opslens.recovery.contracts import Approve
    a,inv=recovery;p=propose(client,inv).json();store.approve(p['id'],Approve(**approval(p)))
    def lost(_):raise SystemExit('stopped before request was sent')
    a.submit_probe=lost;a.find_probe=lambda _:None
    with pytest.raises(SystemExit):worker.process(store.claim(),a)
    expire('recovery',p['id']);worker.process(store.claim(),a)
    saved=client.get('/api/v1/recovery/plans/'+p['id']).json()
    assert saved['status']=='manual' and len(a.actions)==1 and a.probes==[]

def test_collection_resumes_only_missing_sources(client,monkeypatch):
    from opslens import collector,queue
    import hashlib
    from datetime import datetime,timezone
    read=[]
    def collect(lab,sink,skip):
        for source in ('docker.services','lab.diagnostics'):
            if source in skip:continue
            read.append(source)
            item={'lab':lab,'source':source,'success':True,'content':'real collector fixture','collected_at':datetime.now(timezone.utc),'sha256':hashlib.sha256(b'real collector fixture').hexdigest()}
            sink(item)
            if len(read)==1:raise SystemExit('stopped after committed source')
    monkeypatch.setattr(queue,'collect',collect)
    task=client.post('/api/v1/collections',json={'lab':'fault'}).json()
    with pytest.raises(SystemExit):queue.process(queue.claim())
    expire('collection',task['id']);queue.process(queue.claim())
    saved=client.get('/api/v1/collections/'+task['id']).json()
    assert saved['status']=='completed' and read==['docker.services','lab.diagnostics']
    assert len(saved['evidence'])==2

def test_embedding_batch_response_survives_crash_before_cache_write(client,monkeypatch):
    from opslens.rag import service
    _,task=submit(client);claimed=durability.claim('investigation');calls=[]
    class Provider:
        def embed(self,texts,text_type='document'):
            calls.append(list(texts));return [[.1]*1024 for _ in texts]
    original=durability.Runtime.operation
    def cut(self,key,fn,paid=False):
        result=original(self,key,fn,paid)
        if key.startswith('embedding:'):raise SystemExit('saved model response before embedding cache')
        return result
    monkeypatch.setattr(durability.Runtime,'operation',cut)
    rt=durability.Runtime('investigation',claimed)
    with pytest.raises(SystemExit):
        with rt.live():service.vectors(['one','two'],provider=Provider())
    expire('investigation',task['id']);monkeypatch.setattr(durability.Runtime,'operation',original)
    rt=durability.Runtime('investigation',durability.claim('investigation'))
    with rt.live():assert len(service.vectors(['one','two'],provider=Provider()))==2
    assert len(calls)==1

def test_partial_parallel_subtask_resumes_without_repeating_completed_role(client,monkeypatch):
    import threading
    from opslens.investigation import worker
    from test_collaboration_core import Model,FakeTools
    completed=threading.Event();original=durability.Runtime.record
    original_generate=durability.SavedModel.generate
    def interrupt(self,messages,definitions):
        if '运行调查' in messages[0]['content']:
            assert completed.wait(5)
            raise SystemExit('runtime role stopped before issuing its request')
        return original_generate(self,messages,definitions)
    def record(self,kind,payload,fn):
        original(self,kind,payload,fn)
        if kind=='subtask_finished' and payload.get('task_id')=='manual':completed.set()
    monkeypatch.setattr(durability.Runtime,'record',record)
    monkeypatch.setattr(durability.SavedModel,'generate',interrupt)
    task=client.post('/api/v1/investigations',json={'request_id':str(uuid4()),'question':'worker','target':'fault','mode':'multi'}).json()
    class Counting(Model):
        def __init__(self):super().__init__();self.calls=0
        def generate(self,*args):self.calls+=1;return super().generate(*args)
    model=Counting();tools=FakeTools()
    stopped=False
    try:worker.process(worker.claim(),model,tools)
    except SystemExit:stopped=True
    saved=client.get('/api/v1/investigations/'+task['id']).json()
    assert stopped,(saved['error'],saved['collaboration'],model.calls,tools.order)
    assert completed.is_set() and tools.order==['manual'] and model.calls==3
    expire('investigation',task['id']);monkeypatch.setattr(durability.Runtime,'record',original)
    monkeypatch.setattr(durability.SavedModel,'generate',original_generate)
    worker.process(worker.claim(),model,tools)
    saved=client.get('/api/v1/investigations/'+task['id']).json()
    assert saved['status']=='completed' and saved['metrics']['model_calls']==model.calls==6, (saved.get('error'),saved['metrics'],saved['collaboration'],tools.order,model.calls)
    assert tools.order.count('manual')==tools.order.count('runtime')==1
