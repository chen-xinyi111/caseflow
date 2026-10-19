"""Isolated schema integration; controlled model responses never count as live model evidence."""
import json
import io
import zipfile
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

def test_archive_is_reversible_preserves_assets_and_rejects_stale_writes(client):
    p=client.post('/api/v1/caseflow/projects',json={'name':'归档契约检查'}).json()
    base='/api/v1/caseflow/projects/'+p['id']
    p=client.post(base+'/texts',json={'revision':p['revision'],'name':'原资料','content':'现有正文'}).json()
    before=p['state']
    archived=client.post(base+'/archive',json={'revision':p['revision'],'archived':True})
    assert archived.status_code==200
    saved=archived.json()
    assert saved['state']==before|{'archived':True}
    assert next(x for x in client.get('/api/v1/caseflow/projects').json() if x['id']==p['id'])['archived'] is True
    assert client.post(base+'/archive',json={'revision':p['revision'],'archived':False}).status_code==409
    restored=client.post(base+'/archive',json={'revision':saved['revision'],'archived':False}).json()
    assert restored['state']==before|{'archived':False}

class ControlledProvider:
    def __init__(self,rt,cfg):self.rt=rt;self.calls=0
    def generate(self,messages,tools,key):
        def response():
            if key=='grounding:0':
                payload=json.loads(messages[-1]['content']);reviews=[]
                for i,item in enumerate(payload['cases']):
                    refs=[j for j,r in enumerate(payload['sources']) if r['id'] in item['requirement_ids']]
                    fields=[{'field':f,'status':'test_data' if f=='data' else 'test_action' if f=='steps' else 'supported','reason':'受控复核','source_indexes':refs if f in ('preconditions','expected') else []} for f in ('preconditions','data','steps','expected')]
                    reviews.append({'case_index':i,'fields':fields})
                return {'content':json.dumps({'reviews':reviews}),'usage':{'total_tokens':10}}
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
    p=client.post(url+'/cases/'+case['id'],json={'revision':p['revision'],'expected':'人工确认：其他条件满足时允许30分钟预约','status':'approved','review_note':'控制检查，核对了原文和步骤','acknowledge_grounding':True}).json()
    assert p['state']['summary']['approved']==1
    case=p['state']['cases'][0]
    export=client.get(url+'/export?format=xlsx');assert export.status_code==200 and export.content[:2]==b'PK'
    md=client.get(url+'/export?format=md');assert 'T001' in md.text and '已审核' in md.text and case['expected'] in md.text
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(io.BytesIO(export.content)) as z:
        ns={'x':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        rows=[[ ''.join(cell.itertext()) for cell in row] for row in ET.fromstring(z.read('xl/worksheets/sheet1.xml')).findall('.//x:row',ns)]
    assert rows[1][8]==case['expected'] and rows[1][11]==case['review_note']
    p=client.post(url+'/runs',json={'revision':p['revision'],'request_id':str(uuid4()),'name':'隔离检查','results':[{'case_id':case['id'],'revision':case['revision'],'status':'通过','note':'仅工程检查记录'}]}).json()
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
    assert len(rows)==len(agent.extraction_segments(pages))
    assert {x['page'] for row in rows for r in json.loads(row['result']['content'])['requirements'] for x in r['citations']}=={1,2,3}


def test_dense_requirements_are_read_in_small_batches_without_losing_source(client,monkeypatch):
    seen=[]
    class DenseProvider(ControlledProvider):
        def generate(self,messages,tools,key):
            if key.startswith('extract:'):
                payload=json.loads(messages[-1]['content'])
                seen.append(payload['text'])
                assert len(payload['text'])<=400
            return super().generate(messages,tools,key)
    monkeypatch.setattr(agent,'Provider',DenseProvider)
    p=client.post('/api/v1/caseflow/projects',json={'name':'密集规则读取'}).json()
    url='/api/v1/caseflow/projects/'+p['id']
    body='\n\n'.join(f'R{i:02d}：第{i}条会议室预约规则，人数、时间边界和权限均须满足，未知条件不得自行补充。' for i in range(1,18))
    p=client.post(url+'/texts',json={'revision':p['revision'],'content':body}).json()
    t=client.post(url+'/tasks',json=task_body(p,'extract')).json()
    agent.process(durability.claim('caseflow'))
    assert client.get('/api/v1/caseflow/tasks/'+t['id']).json()['status']=='completed'
    assert len(seen)>1 and ''.join(seen)==body
    assert client.get(url).json()['state']['documents'][0]['pages']==[body]


def test_extraction_truncation_keeps_original_and_partial_results_with_correct_message(client,monkeypatch):
    from opslens.rag.provider import ModelFailure
    class TruncatedProvider(ControlledProvider):
        def generate(self,messages,tools,key):
            if key.endswith(':1'):raise ModelFailure('模型输出被截断，请减少所选需求后分批生成')
            return super().generate(messages,tools,key)
    monkeypatch.setattr(agent,'Provider',TruncatedProvider)
    p=client.post('/api/v1/caseflow/projects',json={'name':'读取截断检查'}).json()
    url='/api/v1/caseflow/projects/'+p['id']
    body='\n\n'.join(f'R{i}：'+('预约规则。'*20) for i in range(1,8))
    p=client.post(url+'/texts',json={'revision':p['revision'],'content':body}).json()
    before=p['state']
    t=client.post(url+'/tasks',json=task_body(p,'extract')).json()
    agent.process(durability.claim('caseflow'))
    saved=client.get('/api/v1/caseflow/tasks/'+t['id']).json()
    assert saved['status']=='failed' and '读取需求' in saved['error']
    assert '所选需求' not in saved['error']
    assert saved['result']['partial'] and saved['result']['completed_segments']==1
    assert client.get(url).json()['state']==before

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


@pytest.mark.parametrize('error_kind',['json','quote','unknown_usage','twice'])
def test_extraction_repairs_known_invalid_output_once_and_never_invents_citations(client,monkeypatch,error_kind):
    calls=[]
    class RepairProvider(ControlledProvider):
        def generate(self,messages,tools,key):
            calls.append(key)
            if not key.endswith(':repair') or error_kind=='twice':
                if error_kind=='quote':
                    data=json.loads(messages[-1]['content'])
                    content=json.dumps({'requirements':[{'text':'预约规则','citations':[{'document_id':data['document_id'],'page':1,'quote':'原文中不存在的引用'}]}]})
                else:content='{"requirements":[{"text":"状态为"维护中""}]}'
                return self.rt.operation(key,lambda:{'content':content,'usage':{} if error_kind=='unknown_usage' else {'total_tokens':10}},paid=True)
            # The repair conversation must contain the source and actual validation feedback.
            assert 'error' in json.loads(messages[-1]['content'])
            return super().generate(messages[:2],tools,key)
    monkeypatch.setattr(agent,'Provider',RepairProvider)
    p=project(client);url='/api/v1/caseflow/projects/'+p['id']
    before=p['state']
    t=client.post(url+'/tasks',json=task_body(p,'extract')).json()
    agent.process(durability.claim('caseflow'))
    saved=client.get('/api/v1/caseflow/tasks/'+t['id']).json()
    assert len(calls)==(1 if error_kind=='unknown_usage' else 2)
    after=client.get(url).json()['state']
    if error_kind in ('json','quote'):
        assert saved['status']=='completed',saved
        assert after['requirements'][0]['citations'][0]['quote']==before['documents'][0]['pages'][0]
    else:
        assert saved['status']==('blocked' if error_kind=='unknown_usage' else 'failed')
        assert after==before

def text_pdf(text='Meeting duration is 30 to 120 minutes.'):
    stream=('BT /F1 12 Tf 40 750 Td ('+text+') Tj ET').encode()
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    out=b'%PDF-1.4\n';offsets=[0]
    for i,obj in enumerate(objects,1):offsets.append(len(out));out+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    start=len(out);out+=b'xref\n0 6\n0000000000 65535 f \n'+b''.join(f'{n:010d} 00000 n \n'.encode() for n in offsets[1:])
    return out+b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(start).encode()+b'\n%%EOF\n'

def test_requirement_upload_formats_and_explicit_parse_failure(client):
    p=client.post('/api/v1/caseflow/projects',json={'name':'文件导入验收'}).json();url='/api/v1/caseflow/projects/'+p['id']
    word=io.BytesIO()
    with zipfile.ZipFile(word,'w') as z:z.writestr('word/document.xml','<w:document xmlns:w="'+'http://schemas.openxmlformats.org/wordprocessingml/2006/main'+'"><w:body><w:p><w:r><w:t>预约时长为30至120分钟。</w:t></w:r></w:p></w:body></w:document>')
    for name,raw,expected in [('需求.md','预约时长为30至120分钟。'.encode(),'预约时长'),('需求.txt','预约时长为30至120分钟。'.encode(),'预约时长'),('需求.docx',word.getvalue(),'预约时长'),('需求.pdf',text_pdf(),'Meeting duration')]:
        response=client.post(url+'/files',data={'revision':p['revision']},files={'file':(name,raw,'application/octet-stream')});assert response.status_code==200,response.text
        p=response.json();assert expected in p['state']['documents'][-1]['pages'][0]
    response=client.post(url+'/files',data={'revision':p['revision']},files={'file':('扫描.pdf',text_pdf(''),'application/pdf')})
    assert response.status_code==400 and '没有可提取正文' in response.json()['detail']
    assert client.get(url).json()['revision']==p['revision']

def test_conflict_entry_checks_project_scope_and_preserves_review_gate(client,monkeypatch):
    monkeypatch.setattr(agent,'Provider',ControlledProvider);p=project(client);url='/api/v1/caseflow/projects/'+p['id']
    t=client.post(url+'/tasks',json=task_body(p,'extract'));agent.process(durability.claim('caseflow'))
    p=client.get(url).json();r=p['state']['requirements'][0]
    foreign=project(client)
    response=client.post(url+'/clarifications',json={'revision':p['revision'],'kind':'conflict','question':'两份资料谁优先？','requirement_ids':[r['id'],foreign['id']]})
    assert response.status_code==404 and client.get(url).json()['revision']==p['revision']
    p=client.post(url+'/clarifications',json={'revision':p['revision'],'kind':'ambiguity','question':'是否按整数分钟计？','requirement_ids':[r['id']]}).json()
    assert client.post(url+'/requirements/'+r['id'],json={'revision':p['revision'],'text':r['text'],'status':'confirmed'}).status_code==400
    q=p['state']['clarifications'][0]
    p=client.post(url+'/clarifications/'+q['id'],json={'revision':p['revision'],'answer':'按整数分钟计；范围仍为30至120分钟。'}).json()
    assert p['state']['requirements'][0]['status']=='draft'
    p=client.post(url+'/requirements/'+r['id'],json={'revision':p['revision'],'text':r['text'],'status':'confirmed'}).json()
    assert p['state']['requirements'][0]['status']=='confirmed'



def test_resume_validates_processing_signature_and_never_resends_unknown(client,monkeypatch):
    p=project(client);t=client.post('/api/v1/caseflow/projects/'+p['id']+'/tasks',json=task_body(p,'extract')).json()
    claimed=durability.claim('caseflow');rt=durability.Runtime('caseflow',claimed)
    rt.operation('saved',lambda:{'content':'kept'},paid=True);rt.finish('blocked',error='可继续的本地停止')
    resume='/api/v1/caseflow/tasks/'+t['id']+'/resume'
    original=agent.processing_signature
    monkeypatch.setattr(agent,'processing_signature',lambda:'changed-index-rules')
    assert client.post(resume,json={'revision':p['revision']}).status_code==409
    monkeypatch.setattr(agent,'processing_signature',original)
    assert client.post(resume,json={'revision':p['revision']}).status_code==200
    takeover=durability.claim('caseflow');new=durability.Runtime('caseflow',takeover)
    assert new.operation('saved',lambda:pytest.fail('completed external result repeated'),paid=True)['content']=='kept'
    with pytest.raises(RuntimeError):new.operation('unknown',lambda:(_ for _ in ()).throw(RuntimeError('connection lost')),paid=True)
    new.finish('failed',error='外部响应未知')
    response=client.post(resume,json={'revision':p['revision']});assert response.status_code==409 and '未知' in response.json()['detail']


def test_budget_stop_keeps_partial_steps_and_explicit_resume_reuses_them(client,monkeypatch):
    from opslens.rag.provider import ModelBlocked
    sent=[];stop=[True]
    class BudgetProvider(ControlledProvider):
        def generate(self,messages,tools,key):
            if key.endswith(':1') and stop[0]:raise ModelBlocked('模拟额度不足：尚未发送下一段')
            if key not in sent:sent.append(key)
            return super().generate(messages,tools,key)
    monkeypatch.setattr(agent,'Provider',BudgetProvider)
    p=project(client);url='/api/v1/caseflow/projects/'+p['id']
    with db.connect() as c:
        current=store.get(c,p['id'],True);current['state']['documents'][0]['pages']=['第一段预约规则。'+('背景。'*900)]
        c.execute('UPDATE cf_projects SET state=%s WHERE id=%s',(store.jsonb(current['state']),p['id']))
    t=client.post(url+'/tasks',json=task_body(p,'extract')).json();agent.process(durability.claim('caseflow'))
    status=client.get('/api/v1/caseflow/tasks/'+t['id']).json()
    assert status['status']=='blocked' and status['result']['completed_segments']==1
    assert client.get(url).json()['state']['requirements']==[]
    with db.connect() as c:
        completed=c.execute("SELECT count(*) n FROM durable_operations WHERE task_id=%s AND status='completed' AND paid",(t['id'],)).fetchone()['n']
        assert completed==1
        events=c.execute("SELECT payload FROM cf_events WHERE task_id=%s AND kind='progress'",(t['id'],)).fetchall()
        assert any(e['payload'].get('completed')==1 and e['payload'].get('total',0)>1 for e in events)
    stop[0]=False
    assert client.post('/api/v1/caseflow/tasks/'+t['id']+'/resume',json={'revision':p['revision']}).status_code==200
    agent.process(durability.claim('caseflow'))
    assert client.get('/api/v1/caseflow/tasks/'+t['id']).json()['status']=='completed'
    with db.connect() as c:assert c.execute("SELECT count(*) n FROM durable_operations WHERE task_id=%s AND operation_key=%s",(t['id'],sent[0])).fetchone()['n']==1
    assert len(client.get(url).json()['state']['requirements'])>0


def test_sse_reconnect_replays_only_later_saved_events(client,monkeypatch):
    monkeypatch.setattr(agent,'Provider',ControlledProvider);p=project(client)
    t=client.post('/api/v1/caseflow/projects/'+p['id']+'/tasks',json=task_body(p,'extract')).json();agent.process(durability.claim('caseflow'))
    with db.connect() as c:rows=c.execute('SELECT id FROM cf_events WHERE task_id=%s ORDER BY id',(t['id'],)).fetchall()
    cursor=rows[1]['id'];response=client.get('/api/v1/caseflow/tasks/'+t['id']+'/events?after=0',headers={'Last-Event-ID':str(cursor)})
    ids=[int(line[4:]) for line in response.text.splitlines() if line.startswith('id: ')]
    assert ids==[r['id'] for r in rows if r['id']>cursor]


def test_configuration_changed_during_final_call_blocks_result_commit(client,monkeypatch):
    from opslens.diagnostic import model
    from opslens.diagnostic.contracts import ModelSettings
    class ChangingProvider(ControlledProvider):
        def generate(self,messages,tools,key):
            result=super().generate(messages,tools,key)
            model.save_settings(ModelSettings(daily_limit=27))
            return result
    monkeypatch.setattr(agent,'Provider',ChangingProvider);p=project(client);url='/api/v1/caseflow/projects/'+p['id']
    t=client.post(url+'/tasks',json=task_body(p,'extract')).json();agent.process(durability.claim('caseflow'))
    result=client.get('/api/v1/caseflow/tasks/'+t['id']).json()
    assert result['status']=='blocked' and '模型或索引' in result['error']
    assert client.get(url).json()['state']['requirements']==[]
    assert client.post('/api/v1/caseflow/tasks/'+t['id']+'/resume',json={'revision':p['revision']}).status_code==409


def test_execution_import_binds_revision_and_preserves_all_or_nothing(client,monkeypatch):
    from opslens.caseflow import files
    monkeypatch.setattr(agent,'Provider',ControlledProvider);p=project(client);url='/api/v1/caseflow/projects/'+p['id']
    client.post(url+'/tasks',json=task_body(p,'extract'));agent.process(durability.claim('caseflow'));p=client.get(url).json();r=p['state']['requirements'][0]
    p=client.post(url+'/requirements/'+r['id'],json={'revision':p['revision'],'text':r['text'],'status':'confirmed'}).json()
    client.post(url+'/tasks',json=task_body(p,'generate'));agent.process(durability.claim('caseflow'));p=client.get(url).json();case=p['state']['cases'][0]
    p=client.post(url+'/cases/'+case['id'],json={'revision':p['revision'],'status':'approved','review_note':'导入链路检查'}).json()
    raw=files.xlsx(files.RESULT_HEADERS,[[case['code'],1,'未执行','旧规则批次']])
    template=client.get(url+'/result-template');assert template.status_code==200 and template.content.startswith(b'PK')
    p=client.post(url+'/runs/import',data={'revision':p['revision'],'name':'旧批次','request_id':str(uuid4())},files={'file':('results.xlsx',raw)}).json()
    assert p['state']['runs'][-1]['results'][0]['current']
    p=client.post(url+'/requirements/'+r['id'],json={'revision':p['revision'],'text':'预约时长为30至180分钟。','status':'confirmed'}).json()
    p=client.post(url+'/cases/'+case['id'],json={'revision':p['revision'],'status':'approved','review_note':'重新核对新规则','acknowledge_grounding':True}).json()
    assert p['state']['cases'][0]['revision']==2
    late=','.join(files.RESULT_HEADERS)+'\nT001,1,未执行,补录旧规则'
    response=client.post(url+'/runs/import',data={'revision':p['revision'],'name':'旧批次补录','request_id':str(uuid4())},files={'file':('late.csv',late.encode())})
    assert response.status_code==200;p=response.json();assert not p['state']['runs'][-1]['results'][0]['current']
    invalid=files.xlsx(files.RESULT_HEADERS,[['T001',2,'未执行',''],['T999',1,'通过','错误编号']])
    response=client.post(url+'/runs/import',data={'revision':p['revision'],'name':'拒绝整批','request_id':str(uuid4())},files={'file':('invalid.xlsx',invalid)})
    assert response.status_code==400 and client.get(url).json()['revision']==p['revision']


def test_manual_requirement_scope_quote_and_confirmation(client):
    p=project(client);foreign=project(client);url='/api/v1/caseflow/projects/'+p['id'];doc=p['state']['documents'][0]
    body={'revision':p['revision'],'text':'预约时长为30至120分钟。','document_id':doc['id'],'page':1,'quote':doc['pages'][0]}
    assert client.post(url+'/requirements',json=body|{'document_id':foreign['state']['documents'][0]['id']}).status_code==404
    assert client.post(url+'/requirements',json=body|{'quote':'未出现的规则'}).status_code==400
    assert client.get(url).json()['revision']==p['revision']
    response=client.post(url+'/requirements',json=body);assert response.status_code==200
    r=response.json()['state']['requirements'][0];assert r['origin']=='user' and r['status']=='draft'


def test_agent_repairs_invalid_quote_before_atomic_commit(client,monkeypatch):
    class RepairProvider(ControlledProvider):
        def generate(self,messages,tools,key):
            if key.startswith('extract:') or key in ('generate:0','generate:1','grounding:0'):
                return super().generate(messages,tools,key)
            def response():
                req=json.loads(next(m['content'] for m in messages if m.get('tool_call_id')=='req'))[0]
                quote=req['citations'][0] if key=='generate:3' else req['citations'][0]|{'quote':'模型改写的预约规则'}
                if key=='generate:3':
                    assert json.loads(messages[-1]['content'])['verified_requirement_citations']==req['citations']
                item={'title':'核对30分钟边界','requirement_ids':[req['id']],'preconditions':'存在空闲时段','data':'30分钟','steps':['提交30分钟预约'],'expected':'满足其他规则时允许该时长','category':'边界','basis':'explicit','citations':[quote]}
                return {'role':'assistant','content':json.dumps({'cases':[item]},ensure_ascii=False),'usage':{'total_tokens':10}}
            return self.rt.operation(key,response,paid=True)
    monkeypatch.setattr(agent,'Provider',RepairProvider)
    p=project(client);url='/api/v1/caseflow/projects/'+p['id']
    client.post(url+'/tasks',json=task_body(p,'extract'));agent.process(durability.claim('caseflow'))
    p=client.get(url).json();r=p['state']['requirements'][0]
    p=client.post(url+'/requirements/'+r['id'],json={'revision':p['revision'],'text':r['text'],'status':'confirmed'}).json()
    t=client.post(url+'/tasks',json=task_body(p,'generate')).json();agent.process(durability.claim('caseflow'))
    assert client.get('/api/v1/caseflow/tasks/'+t['id']).json()['status']=='completed'
    saved=client.get(url).json()['state']['cases'];assert len(saved)==1 and saved[0]['citations']==r['citations']
    with db.connect() as c:
        invalid=c.execute("SELECT result FROM durable_operations WHERE task_id=%s AND operation_key='generate:2'",(t['id'],)).fetchone()
        assert json.loads(invalid['result']['content'])['cases'][0]['citations'][0]['quote']=='模型改写的预约规则'
        assert c.execute("SELECT count(*) n FROM cf_events WHERE task_id=%s AND kind='validation'",(t['id'],)).fetchone()['n']==1
        assert c.execute('SELECT count(*) n FROM cf_requests').fetchone()['n']==0


def confirmed_project(client,monkeypatch,provider=ControlledProvider):
    monkeypatch.setattr(agent,'Provider',provider)
    p=project(client);url='/api/v1/caseflow/projects/'+p['id']
    client.post(url+'/tasks',json=task_body(p,'extract'));agent.process(durability.claim('caseflow'))
    p=client.get(url).json();r=p['state']['requirements'][0]
    return client.post(url+'/requirements/'+r['id'],json={'revision':p['revision'],'text':r['text'],'status':'confirmed'}).json()


def test_numeric_rule_confirmation_source_scope_and_stale_requirement(client,monkeypatch):
    p=confirmed_project(client,monkeypatch);r=p['state']['requirements'][0];url='/api/v1/caseflow/projects/'+p['id']
    rule={'revision':p['revision'],'name':'预约时长范围','metric':'预约时长','unit':'分钟','kind':'range','minimum':'30','maximum':'120','requirement_ids':[r['id']],'confirmed':True}
    assert client.post(url+'/numeric-rules',json=rule|{'confirmed':False}).status_code==400
    assert client.post(url+'/numeric-rules',json=rule|{'maximum':'999'}).status_code==400
    foreign=project(client)['state']['documents'][0]['id']
    assert client.post(url+'/numeric-rules',json=rule|{'requirement_ids':[foreign]}).status_code==404
    response=client.post(url+'/numeric-rules',json=rule);assert response.status_code==200,response.text
    p=response.json();numeric=p['state']['numeric_rules'][0];assert numeric['current']
    p=client.post(url+'/requirements/'+r['id'],json={'revision':p['revision'],'text':'预约时长为30至180分钟。','status':'confirmed'}).json()
    assert not p['state']['numeric_rules'][0]['current']
    p=client.post(url+'/numeric-rules/'+numeric['id']+'/disable',json={'revision':p['revision']}).json()
    assert not p['state']['numeric_rules'][0]['enabled']


def test_saved_case_evidence_task_is_scoped_and_requires_warning_acknowledgement(client,monkeypatch):
    class WarningProvider(ControlledProvider):
        def generate(self,messages,tools,key):
            if key!='grounding:0':return super().generate(messages,tools,key)
            def warning():
                fields=[{'field':f,'status':'test_data' if f=='data' else 'test_action' if f=='steps' else 'supported','reason':'受控复核','source_indexes':[0] if f in ('preconditions','expected') else []} for f in ('preconditions','data','steps','expected')]
                fields[-1].update(status='unsupported',reason='原文未要求提示')
                return {'content':json.dumps({'reviews':[{'case_index':0,'fields':fields}]}),'usage':{'total_tokens':10}}
            return self.rt.operation(key,warning,paid=True)
    p=confirmed_project(client,monkeypatch,WarningProvider);r=p['state']['requirements'][0];url='/api/v1/caseflow/projects/'+p['id']
    item={'title':'人工构造检查','preconditions':'存在可预约时段','data':'30分钟','steps':['尝试预约'],'expected':'提示固定文案','requirement_ids':[r['id']],'citations':r['citations'],'basis':'explicit','category':'正常'}
    p=store.public(store.update(p['id'],p['revision'],'controlled_fixture',lambda s:store.add_cases(s,[item],[r['id']],[r['document_id']])))
    case=p['state']['cases'][0];body=task_body(p,'review')|{'case_ids':[case['id']]}
    assert client.post(url+'/tasks',json=body|{'case_ids':[str(uuid4())]}).status_code==404
    assert client.post(url+'/tasks',json=body|{'requirement_ids':[]}).status_code==400
    t=client.post(url+'/tasks',json=body);assert t.status_code==202,t.text
    assert client.post(url+'/tasks',json=body|{'case_ids':[]}).status_code==409
    agent.process(durability.claim('caseflow'))
    assert client.get('/api/v1/caseflow/tasks/'+t.json()['id']).json()['status']=='completed'
    p=client.get(url).json();checked=p['state']['cases'][0]
    assert checked['expected']==item['expected'] and checked['revision']==case['revision']
    assert checked['grounding']['status']=='needs_confirmation' and checked['grounding']['current']
    review={'revision':p['revision'],'status':'approved','review_note':'人工核对说明'}
    assert client.post(url+'/cases/'+case['id'],json=review).status_code==400
    approved=client.post(url+'/cases/'+case['id'],json=review|{'acknowledge_grounding':True})
    assert approved.status_code==200 and approved.json()['state']['cases'][0]['grounding_ack']['note']


def test_unavailable_semantic_review_keeps_only_structurally_valid_drafts(client,monkeypatch):
    from opslens.rag.provider import ModelBlocked
    class Unavailable(ControlledProvider):
        def generate(self,messages,tools,key):
            if key=='grounding:0':raise ModelBlocked('受控复核预算不足')
            return super().generate(messages,tools,key)
    p=confirmed_project(client,monkeypatch,Unavailable);url='/api/v1/caseflow/projects/'+p['id']
    t=client.post(url+'/tasks',json=task_body(p,'generate')).json();agent.process(durability.claim('caseflow'))
    result=client.get('/api/v1/caseflow/tasks/'+t['id']).json()
    assert result['status']=='completed' and '自动依据复核未完成' in result['result']['message']
    p=client.get(url).json();case=p['state']['cases'][0]
    assert case['status']=='draft' and case['grounding']['status']=='unavailable'
    assert client.post(url+'/cases/'+case['id'],json={'revision':p['revision'],'status':'approved','review_note':'未勾选核对'}).status_code==400


def test_generated_numeric_expectation_is_computed_and_manual_edits_drop_calculation(client,monkeypatch):
    class NumericProvider(ControlledProvider):
        def generate(self,messages,tools,key):
            if key!='generate:2':return super().generate(messages,tools,key)
            def result():
                req=json.loads(next(m['content'] for m in messages if m.get('tool_call_id')=='req'))[0]
                rule=json.loads(messages[1]['content'])['numeric_rules'][0]
                item={'title':'检查121分钟上界之外','preconditions':'存在空闲时段','data':'模型输入文字','steps':['尝试预约'],'expected':'模型误写允许预约','category':'边界','basis':'explicit','requirement_ids':[req['id']],'citations':req['citations'],'numeric_input':{'rule_id':rule['id'],'value':'121'}}
                return {'role':'assistant','content':json.dumps({'cases':[item]}),'usage':{'total_tokens':10}}
            return self.rt.operation(key,result,paid=True)
    p=confirmed_project(client,monkeypatch,NumericProvider);r=p['state']['requirements'][0];url='/api/v1/caseflow/projects/'+p['id']
    rule={'revision':p['revision'],'name':'时长','metric':'预约时长','unit':'分钟','kind':'range','minimum':'30','maximum':'120','requirement_ids':[r['id']],'confirmed':True}
    p=client.post(url+'/numeric-rules',json=rule).json()
    t=client.post(url+'/tasks',json=task_body(p,'generate')).json();agent.process(durability.claim('caseflow'))
    assert client.get('/api/v1/caseflow/tasks/'+t['id']).json()['status']=='completed'
    p=client.get(url).json();case=p['state']['cases'][0]
    assert '不符合' in case['expected'] and case['data']=='预约时长：121分钟'
    assert case['calculation']['original_expected']=='模型误写允许预约'
    response=client.post(url+'/cases/'+case['id'],json={'revision':p['revision'],'data':'人工输入','status':'draft'})
    changed=response.json()['state']['cases'][0]
    assert changed['data']=='人工输入' and 'calculation' not in changed and not changed['grounding']['current']
