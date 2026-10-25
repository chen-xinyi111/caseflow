"""Controlled UI fixture only. No database, workers, models or user projects."""
import copy
import sys
from pathlib import Path
from uuid import UUID, uuid5
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests')]
from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import FileResponse,Response
from fastapi.staticfiles import StaticFiles
from opslens.caseflow import store,documents,files
from test_caseflow_database import text_pdf
from test_caseflow_sources import docx

app=FastAPI()
PID='00000000-0000-0000-0000-000000000001'
OTHER='00000000-0000-0000-0000-000000000009'
projects={p:{'id':p,'name':'第三阶段页面夹具（离线验证）' if p==PID else '隔离项目','revision':1,'state':store.empty()} for p in [PID,OTHER]}
versions={};evidences={};model_attempts=0

def project(pid):
    if pid not in projects:raise HTTPException(404,'项目不存在')
    return projects[pid]

def save(pid,name,raw,previous=None,pasted=False):
    p=project(pid)
    if pasted:pages,warnings,kinds,fmt=[raw.decode()],[],None,'txt'
    else:pages,warnings,kinds=files.inspect_details(name,raw);fmt=Path(name).suffix[1:]
    pages=[store.clean(t) for t in pages];snapshot=documents.make_snapshot(pages,fmt,kinds)
    identity={'source_kind':'paste' if pasted else 'file','format':fmt,'original_sha256':documents.sha(raw),
        'snapshot_sha256':store.digest(snapshot),'parser_signature':documents.PARSER_SIGNATURE,'source_url':''}
    if kinds:identity['unit_kinds']=kinds
    result=store.add_document(p['state'],name,pages,previous_id=previous,warnings=warnings,identity=identity,canonical=True)
    versions[result['document_id']]=identity|{'original_bytes':raw,'snapshot':snapshot,'document_id':result['document_id'],'project_id':pid}
    p['revision']+=1
    return result['document_id']

old=save(PID,'预约规则.pdf',text_pdf('Duration is 30 to 180 minutes.'))
new=save(PID,'预约规则.pdf',text_pdf('Duration is 30 to 120 minutes.'),old)
md=save(PID,'重复引用.md','规则😀：单次时长不超过120分钟。\n\n重复依据：时长上限120分钟。\n\n重复依据：时长上限120分钟。'.encode())
save(PID,'表格说明.docx',docx())
store.add_document(projects[PID]['state'],'历史资料.pdf',['历史正文保留，原件未保存。'])
store.apply_extraction(projects[PID]['state'],md,[{'text':'时长上限120分钟。','citations':[{'document_id':md,'page':1,'quote':'重复依据：时长上限120分钟。'}],'questions':[]}])

def value(pid,did):
    p=project(pid);doc=next((d for d in p['state']['documents'] if d['id']==did),None)
    if not doc:raise HTTPException(404,'记录不属于当前项目')
    v=versions.get(did)
    if v is None:
        s=documents.make_snapshot(doc['pages'],'legacy_text')
        v={'source_kind':'legacy','format':'legacy_text','original_bytes':None,'original_sha256':None,
            'snapshot':s,'snapshot_sha256':store.digest(s),'parser_signature':'legacy-preserved-text-1'}
    return p,doc,v

@app.get('/api/v1/caseflow/projects')
def listing():return [store.public(copy.deepcopy(p)) for p in projects.values()]
@app.get('/api/v1/caseflow/projects/{pid}')
def detail(pid:str):return store.public(copy.deepcopy(project(pid)))
@app.get('/api/v1/caseflow/projects/{pid}/tasks')
def tasks(pid:str):project(pid);return []
@app.get('/api/v1/caseflow/projects/{pid}/documents')
def docs(pid:str):return [documents._metadata(*value(pid,d['id'])) for d in project(pid)['state']['documents']]
@app.get('/api/v1/caseflow/projects/{pid}/documents/{did}')
def doc(pid:str,did:str):
    p,d,v=value(pid,did);return documents._metadata(p,d,v)|{'snapshot':v['snapshot']}
@app.get('/api/v1/caseflow/projects/{pid}/documents/{did}/original')
def original(pid:str,did:str):
    p,d,v=value(pid,did)
    if v['original_bytes'] is None:raise HTTPException(404,'历史文本，原文件未保存')
    return Response(v['original_bytes'],media_type='application/pdf' if v['format']=='pdf' else 'application/octet-stream')
@app.post('/api/v1/caseflow/projects/{pid}/evidence/resolve-legacy')
async def resolve(pid:str,request:Request):
    data=await request.json();p,d,v=value(pid,data['document_id']);hits=documents.resolve(v['snapshot'],data['page'],data['quote'])
    return {'candidates':hits,'snapshot_sha256':v['snapshot_sha256'],'location_status':'not_found' if not hits else 'unique' if len(hits)==1 else 'ambiguous'}
@app.post('/api/v1/caseflow/projects/{pid}/evidence')
async def pin(pid:str,request:Request):
    data=await request.json();p,d,v=value(pid,data['document_id'])
    if v['snapshot_sha256']!=data['snapshot_sha256']:raise HTTPException(409,'版本不同')
    pieces=documents.validate_segments(v['snapshot'],data['segments'])
    if documents.SEPARATOR.join(x['text'] for x in pieces)!=data['quote']:raise HTTPException(400,'原文不一致')
    eid=str(uuid5(documents.EVIDENCE_NAMESPACE,store.digest([pid,d['id'],v['snapshot_sha256'],data['segments']])))
    result={'id':eid,'project_id':pid,'document_id':d['id'],'snapshot_sha256':v['snapshot_sha256'],'segments':data['segments'],
        'quote':data['quote'],'pieces':pieces,'location_status':'valid','viewer_url':f"/#/projects/{pid}/documents/{d['id']}?evidence={eid}"}
    evidences[eid]=result;return result
@app.get('/api/v1/caseflow/projects/{pid}/evidence/{eid}')
def evidence(pid:str,eid:str):
    project(pid);v=evidences.get(eid)
    if not v or v['project_id']!=pid:raise HTTPException(404,'引用不属于当前项目')
    return v
@app.post('/api/v1/caseflow/projects/{pid}/texts')
async def paste(pid:str,request:Request):
    d=await request.json();p=project(pid)
    if d['revision']!=p['revision']:raise HTTPException(409,'修订过期')
    save(pid,d['name'],d['content'].encode(),d.get('previous_id'),True);return store.public(copy.deepcopy(p))
@app.post('/api/v1/caseflow/projects/{pid}/tasks')
async def no_model(pid:str):
    global model_attempts
    model_attempts+=1;raise HTTPException(409,'离线夹具禁止模型调用')
@app.get('/fixture-status')
def proof():return {'fixture':True,'database_used':False,'model_attempts':model_attempts,'evidence_count':len(evidences),'documents':{d['name']+':'+d['id']:d['id'] for d in projects[PID]['state']['documents']}}
@app.get('/')
def home():return FileResponse(ROOT/'frontend/dist/index.html')
app.mount('/assets',StaticFiles(directory=ROOT/'frontend/dist/assets'),name='assets')
if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=18186,log_level='warning')
