import hashlib,json
from uuid import uuid4
from opslens import db
from opslens.rag.store import jsonb
from opslens.config import secrets
from opslens.security import redact
def event(c,i,kind,payload):
    c.execute("INSERT INTO investigation_events(investigation_id,kind,payload) VALUES (%s,%s,%s)",(i,kind,jsonb(payload)))
def get(c,i):
    row=c.execute("SELECT *,reference_items AS \"references\" FROM investigations WHERE id=%s",(i,)).fetchone()
    if not row:raise LookupError("调查任务不存在")
    from opslens.durability import describe
    row['recovery']=describe(c,'investigation',i)
    return row
def create(data):
    payload=data.model_dump(mode="json",exclude={"request_id"})
    payload["question"]=redact(payload["question"],secrets())
    with db.connect() as c:
        c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,1301))",(str(data.request_id),))
        old=c.execute("SELECT *,reference_items AS \"references\" FROM investigations WHERE request_id=%s",(data.request_id,)).fetchone()
        if old:
            if {k:old["input"].get(k,"single" if k=="mode" else None) for k in payload}!=payload:raise FileExistsError("该请求编号已用于另一项调查")
            return old
        if data.snapshot_id:
            if data.mode!='verified':raise ValueError('历史快照仅用于核验模式')
            snapshot=c.execute("SELECT lab,source,success FROM evidence WHERE id=%s",(data.snapshot_id,)).fetchone()
            if not snapshot or snapshot['lab']!=data.target or snapshot['source']!='docker.services' or not snapshot['success']:
                raise ValueError('只能使用同一实例成功采集的服务状态快照')
        if data.target=='external':
            from opslens.external import authorized
            row,_=authorized(c,payload)
            from opslens.rag.provider import QuotaGuard
            from opslens.investigation.model import MODEL
            import os
            QuotaGuard(key=os.environ.get('DASHSCOPE_APIKEY','')).check(MODEL)
            index={'id':row['id'],'manifest_sha256':row['sha256']}
        else:
            index=c.execute("SELECT id,manifest_sha256 FROM kb_indexes WHERE mode='semantic' AND status='ready' AND active ORDER BY finished_at DESC LIMIT 1").fetchone()
            if not index:raise ValueError("请先构建可用语义索引")
        payload|={"index_id":str(index["id"]),"manifest_sha256":index["manifest_sha256"],"baseline":{'multi':'multi-agent-v1','single':'single-agent-v1','verified':'verified-agent-v1'}[data.mode]}
        payload['record_origin']='daily'
        row=c.execute("INSERT INTO investigations(id,request_id,status,input) VALUES (%s,%s,'queued',%s) RETURNING *,reference_items AS \"references\"",(uuid4(),data.request_id,jsonb(payload))).fetchone()
        event(c,row["id"],"queued",{"message":"调查已排队，仅读取目标实例"})
        return row
def record(i,kind,payload,connection=None):
    clean=json.loads(redact(json.dumps(payload,ensure_ascii=False,default=str),secrets()))
    from contextlib import nullcontext
    with nullcontext(connection) if connection is not None else db.connect() as c:
        c.execute("INSERT INTO investigation_steps(investigation_id,kind,payload) VALUES (%s,%s,%s)",(i,kind,jsonb(clean)))
        if "collaboration" in clean:
            c.execute("UPDATE investigations SET collaboration=%s WHERE id=%s",(jsonb(clean["collaboration"]),i))
        if "progress_metrics" in clean:
            c.execute("UPDATE investigations SET metrics=%s WHERE id=%s",(jsonb(clean["progress_metrics"]),i))
        if 'verification' in clean:
            c.execute('UPDATE investigations SET verification=%s WHERE id=%s',(jsonb(clean['verification']),i))
        event(c,i,kind,{"message":clean.get("message",kind),**{k:clean[k] for k in ("task_id","role") if k in clean}})
