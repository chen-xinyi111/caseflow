import json
from pathlib import Path
from uuid import uuid4
import pytest
from opslens import db
from opslens.config import ROOT
from opslens.rag import store as kb
from opslens.evaluation import store
from opslens.evaluation.runner import FrozenTools,process
from opslens import durability
from test_investigation_database import client
pytestmark=pytest.mark.integration
@pytest.fixture
def evaluation(client):
    with db.connect() as c:
        c.execute(Path('src/opslens/evaluation/schema.sql').read_text(encoding='utf-8'))
        c.execute(Path('src/opslens/evaluation/review.sql').read_text(encoding='utf-8'))
    documents=[]
    for m in json.loads((ROOT/'data/knowledge/manifest.json').read_text(encoding='utf-8')):
        documents.append(kb.ingest(m['slug']+'.md',(ROOT/'data/knowledge'/(m['slug']+'.md')).read_bytes(),m['title'],m['source'],'manual-1.1',['v1'])['id'])
    task=kb.create_index('semantic',documents)
    with db.connect() as c:c.execute("UPDATE kb_indexes SET status='ready' WHERE id=%s",(task['input']['index_id'],))
    return client,task['input']['index_id']
def test_idempotent_run_has_all_planned_rows_and_no_model_calls(evaluation):
    client,index=evaluation;request=uuid4();run=store.create(request,index)
    assert store.create(request,index)['id']==run['id']
    detail=client.get('/api/v1/evaluations/'+str(run['id']))
    assert detail.status_code==200,detail.text
    data=detail.json();assert sum(r['planned'] for r in data['summary'])==600
    assert all(r['completed']==0 and r['success_rate']==0 for r in data['summary'])
    rows=client.get('/api/v1/evaluations/'+str(run['id'])+'/items?kind=investigation&split=test&mode=single').json()
    assert len(rows)==30
    assert not {'expected','root_groups','source_group','case_id'} & rows[0]['input'].keys()
    assert client.get('/api/v1/evaluations/'+str(run['id'])+'/items/'+rows[0]['id']).json()['expected']
    assert client.post('/api/v1/evaluations',json={}).status_code==405
    assert client.get('/api/v1/evaluations/'+str(uuid4())).status_code==404
def test_frozen_tools_never_read_live_target_or_arbitrary_job():
    payload={'target':'fault','job_id':str(uuid4()),'index_id':str(uuid4())}
    snapshot={'evidence':[{'id':str(uuid4()),'source':'docker.services','success':True,'content':'worker exited','collected_at':'2026-10-13T00:00:00Z'}]}
    tool=FrozenTools(payload,snapshot,None)
    first=tool.execute('collect_runtime',{})
    assert first['references'][0]['content']=='worker exited'
    assert tool.execute('collect_runtime',{})['already_collected']
    with pytest.raises(ValueError):tool.execute('read_job',{'job_id':str(uuid4())})
    child=tool.fork('manual','knowledge')
    with pytest.raises(ValueError):child.execute('collect_runtime',{})
def test_partial_pair_does_not_claim_completed_comparison():
    rows=[{'kind':'investigation','mode':m,'split':'test','case_id':'one','status':s,'score':{'rule_correct':True}} for m,s in [('single','completed'),('multi','blocked')]]
    report=store.paired(rows)[0]
    assert report['completed_pairs']==0 and report['pending_pairs']==1

def test_keyword_result_persists_and_late_writer_cannot_replace_it(evaluation):
    client,index=evaluation;run=store.create(uuid4(),index)
    with db.connect() as c:row=c.execute("SELECT id FROM evaluation_items WHERE run_id=%s AND kind='retrieval' AND mode='keyword' LIMIT 1",(run['id'],)).fetchone()
    task=durability.claim('evaluation',row['id']);rt=durability.Runtime('evaluation',task)
    process(task)
    detail=client.get('/api/v1/evaluations/'+str(run['id'])+'/items/'+str(row['id'])).json()
    assert detail['status']=='completed' and detail['result']['hits']
    assert detail['metrics']['request_count']==0 and detail['metrics']['actual_tokens']==0
    assert durability.claim('evaluation',row['id']) is None
    with pytest.raises(durability.LeaseLost):rt.finish('failed',error='late writer')
    assert client.get('/api/v1/evaluations/'+str(run['id'])+'/items/'+str(row['id'])).json()['status']=='completed'

def test_execution_seal_is_stable_and_model_requests_remain_zero(evaluation):
    _,index=evaluation;run=store.create(uuid4(),index)
    store.seal_execution(run['id']);store.seal_execution(run['id'])
    with db.connect() as c:
        manifest=c.execute('SELECT manifest FROM evaluation_runs WHERE id=%s',(run['id'],)).fetchone()['manifest']
        assert manifest['execution_files'] and manifest['model']=='deepseek-v4.1-flash'
        assert not c.execute('SELECT 1 FROM evaluation_calls WHERE item_id IN (SELECT id FROM evaluation_items WHERE run_id=%s)',(run['id'],)).fetchone()

def test_user_approved_subset_keeps_full_library_and_fair_pairs(evaluation):
    from opslens.evaluation.dataset import load_dataset
    _,index=evaluation;data=load_dataset();selected=[r['id'] for r in data['investigation'] if r['id'].endswith(('-03','-08','-09','-10'))]
    request=uuid4();run=store.create(request,index,selected)
    with db.connect() as c:report=store.report(c,run['id'])
    assert len(data['investigation'])==60 and run['manifest']['investigation_sample_count']==24
    assert sum(s['planned'] for s in report['summary'])==528
    assert {p['split']:p['planned_pairs'] for p in report['paired']}=={'dev':6,'test':6,'control':12}
    with pytest.raises(ValueError):store.create(request,index)

def test_fact_review_keeps_the_original_rule_grade(evaluation):
    from opslens.rag.store import jsonb
    client,index=evaluation;run=store.create(uuid4(),index)
    with db.connect() as c:
        item=c.execute("SELECT id FROM evaluation_items WHERE run_id=%s AND kind='investigation' AND mode='single' AND split='dev' LIMIT 1",(run['id'],)).fetchone()
        c.execute("UPDATE evaluation_items SET status='completed',score=%s WHERE id=%s",(jsonb({'rule_correct':False}),item['id']))
        c.execute('INSERT INTO evaluation_reviews(item_id,reviewer,review) VALUES (%s,%s,%s)',(item['id'],'controlled-test-only',jsonb({'root_correct':True,'finding_support':[],'quote_reviewed':0,'quote_supported':0,'reason':'controlled review fixture'})))
        report=store.report(c,run['id']);group=next(s for s in report['summary'] if s['kind']=='investigation' and s['mode']=='single' and s['split']=='dev')
        assert group['correct']==0 and group['review_root_correct']==1 and group['reviewed']==1
    detail=client.get('/api/v1/evaluations/'+str(run['id'])+'/items/'+str(item['id'])).json()
    assert detail['score']['rule_correct'] is False and detail['review']['reviewer']=='controlled-test-only'
