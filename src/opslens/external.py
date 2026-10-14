"""Local Compose evidence. No execution, environment access, or business database access."""
import hashlib
import json
import re
from datetime import datetime, timezone
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from opslens import db
from opslens.config import ROOT, secrets
from opslens.security import redact
from opslens.collector import docker
from opslens.rag.store import jsonb
from opslens.rag.ranking import bm25

DEFAULT_ID = uuid5(NAMESPACE_URL, 'opslens:external:qinghe-after-sales')
NAME = r'^[a-z0-9][a-z0-9_-]{0,62}$'
MAX_LOG_BYTES = 65536
INSPECT_FORMAT = '{"project":{{json (index .Config.Labels "com.docker.compose.project")}},"service":{{json (index .Config.Labels "com.docker.compose.service")}},"image":{{json .Config.Image}},"state":{{json .State.Status}},"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}},"started_at":{{json .State.StartedAt}}}'

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(',', ':')).encode()).hexdigest()

def sanitize(text):
    value = redact(text, secrets())
    value = re.sub(r'(?i)(?<![A-Za-z0-9])([a-z]:[\\/](?:[^\s"<>]+))', '[LOCAL_PATH]', value)
    value = re.sub(r'(?<![\w.+-])[\w.+-]{1,128}@[\w.-]{1,253}\.[A-Za-z]{2,}', '[EMAIL]', value)
    value = re.sub(r'''(?i)(["']?(?:authorization|cookie|set-cookie)["']\s*:\s*["'])[^"'\r\n]+''', r'\1[REDACTED]', value)
    value = re.sub(r'(?<!\d)1[3-9]\d{9}(?!\d)', '[PHONE]', value)
    value = re.sub(r'(?i)(\b(?:cookie|set-cookie)\s*:\s*)[^\r\n]+', r'\1[REDACTED]', value)
    return value

class TargetInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)
    compose_project: str = Field(pattern=NAME)
    services: list[str] = Field(min_length=1, max_length=20)
    log_services: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def valid(self):
        if any(not re.fullmatch(NAME, s) for s in self.services + self.log_services):
            raise ValueError('服务名格式不正确')
        if len(set(self.services)) != len(self.services) or not set(self.log_services) <= set(self.services):
            raise ValueError('日志服务必须属于登记服务且服务不能重复')
        if not self.name.strip(): raise ValueError('请填写项目名称')
        return self

class CaptureInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID

class ConsentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    selected_ids: list[str] = Field(min_length=1, max_length=100)
    confirm: bool

class DocumentInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title:str=Field(min_length=1,max_length=200)
    content:str=Field(min_length=1,max_length=200000)

class ExternalTarget(BaseModel):
    id:UUID
    name:str
    compose_project:str
    services:list[str]
    log_services:list[str]
    created_at:datetime

class ExternalItem(BaseModel):
    id:UUID
    kind:Literal['runtime','knowledge']
    source:str
    content:str
    sha256:str
    success:bool|None=None
    collected_at:datetime|None=None
    document_id:UUID|None=None
    doc_version:str|None=None

class ExternalSnapshotSummary(BaseModel):
    id:UUID
    target_id:UUID
    captured_at:datetime
    sha256:str
    errors:list[str]

class ExternalSnapshot(ExternalSnapshotSummary):
    request_id:UUID
    items:list[ExternalItem]
    documents:list[ExternalItem]

class ExternalConsent(BaseModel):
    id:UUID
    digest:str
    selected_count:int

class ExternalOriginal(ExternalItem):
    page_text:str

class ExternalService(BaseModel):
    service:str
    state:str
    health:str|None=None
    image:str
    started_at:str|None=None

class ExternalStatus(BaseModel):
    checked_at:datetime
    services:list[ExternalService]
    missing_services:list[str]
    error:str|None=None

class AuthorizedModel:
    def __init__(self,model,payload):self.model,self.payload=model,payload
    def generate(self,*args,**kwargs):
        with db.connect() as c:authorized(c,self.payload)
        return self.model.generate(*args,**kwargs)
    def prepare_parallel(self):return self.model.prepare_parallel()
    def __getattr__(self,key):return getattr(self.model,key)

def initialize():
    with db.connect() as c:
        c.execute((ROOT / 'src/opslens/external_schema.sql').read_text(encoding='utf-8'))
        c.execute('INSERT INTO external_targets(id,name,compose_project,services,log_services) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                  (DEFAULT_ID, '青禾电商售后', 'qinghe-after-sales', jsonb(['agent_service','postgres','redis','kafka']), jsonb(['agent_service'])))
        for relative, title in [('README.md','青禾项目说明'),('docs/RUNNING.md','青禾运行说明')]:
            source = ROOT / 'data/external/qinghe' / relative
            if source.is_file():
                content = sanitize(source.read_text(encoding='utf-8'))
                c.execute('INSERT INTO external_documents(id,target_id,title,content,sha256) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                          (uuid4(), DEFAULT_ID, title, content, hashlib.sha256(content.encode()).hexdigest()))

def target(c, i):
    row = c.execute('SELECT * FROM external_targets WHERE id=%s', (i,)).fetchone()
    if not row: raise LookupError('项目不存在')
    return row

def snapshot(c, i):
    row = c.execute('SELECT * FROM external_snapshots WHERE id=%s', (i,)).fetchone()
    if not row: raise LookupError('证据快照不存在')
    # Every load checks both content and its stored manifest.
    content = {'target_id': str(row['target_id']), 'items': row['items'], 'documents': row['documents']}
    if digest(content) != row['sha256']: raise ValueError('快照资料已变化，请重新采集并确认')
    return row

def selected(snapshot_row, ids):
    all_items = snapshot_row['items'] + snapshot_row['documents']
    if len(set(ids)) != len(ids) or not set(ids) <= {r['id'] for r in all_items}:
        raise ValueError('选中资料不存在或重复')
    values = [r for r in all_items if r['id'] in ids]
    if not any(r['kind']=='runtime' and r.get('success') for r in values):
        raise ValueError('至少选择一份成功采集的运行证据')
    return values

def capture(i, request_id, run=docker):
    with db.connect() as c:
        cfg = target(c, i)
        old = c.execute('SELECT * FROM external_snapshots WHERE request_id=%s', (request_id,)).fetchone()
        if old:
            if old['target_id'] != i: raise FileExistsError('请求编号已用于其他项目')
            return snapshot(c, old['id'])
        # Latest version of each target-specific document is pinned in the snapshot.
        docs = c.execute('SELECT DISTINCT ON (title) * FROM external_documents WHERE target_id=%s ORDER BY title,created_at DESC,id DESC',(i,)).fetchall()
    now = datetime.now(timezone.utc).isoformat()
    sid = uuid5(request_id, 'external-snapshot')
    items, errors, states = [], [], []
    def add(source, text, success=True):
        text = sanitize(text)
        items.append({'id': str(uuid5(sid, source)), 'kind':'runtime', 'source':source,'content':text,
                      'success':success,'collected_at':now,'sha256':hashlib.sha256(text.encode()).hexdigest()})
    try:
        ids = run(['ps','-aq','--filter','label=com.docker.compose.project='+cfg['compose_project']]).split()
        for cid in ids[:100]:
            if not re.fullmatch(r'[a-f0-9]{12,64}', cid): raise ValueError('容器编号无效')
            obj = json.loads(run(['inspect','--format',INSPECT_FORMAT,cid]))
            if obj.get('project') != cfg['compose_project']: raise ValueError('容器项目标签不匹配')
            service = obj.get('service')
            if service not in cfg['services']: continue
            states.append({k:obj.get(k) for k in ['service','state','health','image','started_at']})
            if service in cfg['log_services']:
                try:
                    log = run(['logs','--timestamps','--tail','80',cid])
                    limited = sanitize(log).encode('utf-8')[-MAX_LOG_BYTES:].decode('utf-8', errors='ignore')
                    add('docker.logs.'+service+'.'+cid, limited or '（日志为空）')
                except Exception as exc:
                    message = sanitize(str(exc))[:500]; errors.append(message); add('docker.logs.'+service+'.'+cid,message,False)
        present = {s['service'] for s in states}
        missing = sorted(set(cfg['services'])-present)
        add('docker.services', json.dumps({'project':cfg['compose_project'],'services':states,'missing_services':missing},ensure_ascii=False,indent=2))
        if missing: errors.append('未发现登记服务：'+ '、'.join(missing))
    except Exception as exc:
        message = sanitize(str(exc))[:500]; errors.append(message); add('docker.services',message,False)
    documents = [{'id':str(uuid5(sid, 'document:'+str(d['id']))),'kind':'knowledge','source':d['title'],'content':d['content'],
                  'sha256':d['sha256'],'document_id':str(d['id']),'doc_version':d['sha256'][:12]} for d in docs]
    manifest = digest({'target_id':str(i),'items':items,'documents':documents})
    with db.connect() as c:
        c.execute('INSERT INTO external_snapshots(id,request_id,target_id,items,documents,sha256,errors) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                  (sid,request_id,i,jsonb(items),jsonb(documents),manifest,jsonb(errors)))
        return snapshot(c,sid)

def consent(c, sid, data):
    row = snapshot(c,sid)
    if not data.confirm or data.digest != row['sha256']: raise ValueError('请确认当前版本资料后发送')
    values = selected(row,data.selected_ids)
    sha = digest({'snapshot':row['sha256'],'selected_ids':sorted(data.selected_ids),'destination':'dashscope-beijing'})
    c.execute('INSERT INTO external_consents(id,snapshot_id,digest,selected_ids) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',
              (uuid5(sid,sha),sid,sha,jsonb(sorted(data.selected_ids))))
    return {'id':str(uuid5(sid,sha)), 'digest':sha, 'selected_count':len(values)}

def authorized(c, payload):
    row = snapshot(c, payload['external_snapshot_id'])
    if str(row['target_id']) != str(payload['external_target_id']): raise ValueError('快照不属于调查项目')
    proof = c.execute('SELECT * FROM external_consents WHERE id=%s AND snapshot_id=%s',(payload['external_consent_id'],row['id'])).fetchone()
    if not proof: raise ValueError('尚未确认向百炼发送当前资料')
    if proof['digest'] != digest({'snapshot':row['sha256'],'selected_ids':sorted(proof['selected_ids']),'destination':'dashscope-beijing'}):
        raise ValueError('发送确认与资料不一致')
    return row, selected(row,proof['selected_ids'])

def execute(tools, name, args):
    with db.connect() as c: row, values = authorized(c,tools.payload)
    if name=='read_job': return {'unavailable':True,'missing':'本项目未开放业务任务读取，请补充经过脱敏的相关资料。'}
    if name=='collect_runtime':
        if tools.collected: return {'unavailable':True,'missing':'已读取固定确认快照；需要新证据请重新采集、预览并确认。不能将重复读取视为新的补查。'}
        tools.collected=True
        chosen=[r for r in values if r['kind']=='runtime' and r.get('success')]
    else:
        documents=[r for r in values if r['kind']=='knowledge']
        chunks=[]
        for d in documents:
            for n in range(0,len(d['content']),680):
                chunks.append({'id':d['id']+':'+str(n),'text':d['content'][n:n+800],'document':d})
        ranked=bm25(args['query'],chunks,limit=6); by_id={r['id']:r for r in chunks}
        chosen=[by_id[r['id']]['document']|{'content':by_id[r['id']]['text']} for r in ranked]
    refs=[]
    for item in chosen:
        url='/api/v1/external-snapshots/'+str(row['id'])+'/evidence/'+item['id']
        refs.append(tools.add(item,url,item['kind'],doc_version=item.get('doc_version'),scope='current',applicable_versions=['*']))
    return {'references':refs,'note':'只代表已确认快照采集时刻，不代表当前实时状态；未发现容器不能确定停机原因。','captured_at':str(row['captured_at'])}

router=APIRouter(prefix='/api/v1',tags=['external'])

@router.get('/external-targets',response_model=list[ExternalTarget])
def list_targets():
    with db.connect() as c: return c.execute('SELECT * FROM external_targets ORDER BY created_at,id').fetchall()

@router.post('/external-targets',status_code=201,response_model=ExternalTarget)
def register(data:TargetInput):
    with db.connect() as c:
        old=c.execute('SELECT * FROM external_targets WHERE compose_project=%s',(data.compose_project,)).fetchone()
        if old:
            if old['services']!=data.services or old['log_services']!=data.log_services: raise HTTPException(409,'项目已登记，请使用已有范围')
            return old
        return c.execute('INSERT INTO external_targets(id,name,compose_project,services,log_services) VALUES (%s,%s,%s,%s,%s) RETURNING *',
                         (uuid4(),data.name.strip(),data.compose_project,jsonb(data.services),jsonb(data.log_services))).fetchone()

@router.get('/external-targets/{i}/status',response_model=ExternalStatus)
def current_status(i:UUID):
    try:
        with db.connect() as c:cfg=target(c,i)
    except LookupError as exc:raise HTTPException(404,str(exc)) from None
    states=[]
    try:
        ids=docker(['ps','-aq','--filter','label=com.docker.compose.project='+cfg['compose_project']]).split()
        for cid in ids[:100]:
            if not re.fullmatch(r'[a-f0-9]{12,64}',cid):raise ValueError('容器编号无效')
            obj=json.loads(docker(['inspect','--format',INSPECT_FORMAT,cid]))
            if obj.get('project')!=cfg['compose_project']:raise ValueError('容器项目标签不匹配')
            if obj.get('service') in cfg['services']:states.append({k:obj.get(k) for k in ['service','state','health','image','started_at']})
        missing=sorted(set(cfg['services'])-{r['service'] for r in states})
        return {'checked_at':datetime.now(timezone.utc),'services':states,'missing_services':missing,'error':None}
    except Exception as exc:return {'checked_at':datetime.now(timezone.utc),'services':[],'missing_services':cfg['services'],'error':sanitize(str(exc))[:500]}

@router.post('/external-targets/{i}/snapshots',response_model=ExternalSnapshot)
def collect_snapshot(i:UUID,data:CaptureInput):
    try:return capture(i,data.request_id)
    except LookupError as exc:raise HTTPException(404,str(exc)) from None
    except FileExistsError as exc:raise HTTPException(409,str(exc)) from None
    except ValueError as exc:raise HTTPException(400,str(exc)) from None

@router.post('/external-targets/{i}/documents',status_code=201)
def add_document(i:UUID,data:DocumentInput):
    try:
        with db.connect() as c:
            target(c,i)
            content=sanitize(data.content)
            if not content.strip() or not data.title.strip():raise ValueError('请填写有效文档内容和标题')
            sha=hashlib.sha256(content.encode()).hexdigest()
            c.execute('INSERT INTO external_documents(id,target_id,title,content,sha256) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(uuid4(),i,data.title.strip(),content,sha))
            return {'sha256':sha,'title':data.title.strip()}
    except LookupError as exc:raise HTTPException(404,str(exc)) from None
    except ValueError as exc:raise HTTPException(400,str(exc)) from None

@router.get('/external-targets/{i}/snapshots',response_model=list[ExternalSnapshotSummary])
def list_snapshots(i:UUID):
    with db.connect() as c:
        return c.execute('SELECT id,target_id,captured_at,sha256,errors FROM external_snapshots WHERE target_id=%s ORDER BY captured_at DESC LIMIT 20',(i,)).fetchall()

@router.get('/external-snapshots/{i}',response_model=ExternalSnapshot)
def get_snapshot(i:UUID):
    try:
        with db.connect() as c:return snapshot(c,i)
    except LookupError as exc:raise HTTPException(404,str(exc)) from None
    except ValueError as exc:raise HTTPException(409,str(exc)) from None

@router.post('/external-snapshots/{i}/consent',response_model=ExternalConsent)
def confirm(i:UUID,data:ConsentInput):
    try:
        with db.connect() as c:return consent(c,i,data)
    except LookupError as exc:raise HTTPException(404,str(exc)) from None
    except ValueError as exc:raise HTTPException(400,str(exc)) from None

@router.get('/external-snapshots/{i}/evidence/{eid}',response_model=ExternalOriginal)
def original(i:UUID,eid:UUID):
    row=get_snapshot(i)
    item=next((r for r in row['items']+row['documents'] if r['id']==str(eid)),None)
    if not item:raise HTTPException(404,'原文不存在')
    return item|{'page_text':item['content']}
