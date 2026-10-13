import asyncio,json,os
from typing import Literal
from uuid import UUID
from fastapi import APIRouter,HTTPException,Request
from fastapi.responses import StreamingResponse
from opslens import db
from opslens.investigation import store
from opslens.investigation.contracts import Input,Investigation,ConflictExample
from opslens.investigation.model import MODEL
from opslens.rag.provider import QuotaGuard,ModelBlocked
router=APIRouter(prefix="/api/v1/investigations",tags=["investigation"])
@router.get("/status")
def readiness():
    try:QuotaGuard(key=os.environ.get("DASHSCOPE_APIKEY","")).check(MODEL);return {"model":MODEL,"available":True,"reason":"免费额度及用完即停已核验"}
    except Exception as exc:return {"model":MODEL,"available":False,"reason":str(exc)}
@router.get('/examples/conflict',response_model=ConflictExample)
def conflict_example():
    # Real local historical evidence; never synthesize an old service status.
    with db.connect() as c:
        case=c.execute("SELECT sample_job_id FROM fault_cases WHERE id='worker_stopped'").fetchone()
        rows=c.execute("SELECT id,collected_at,content FROM evidence WHERE lab='fault' AND source='docker.services' AND success ORDER BY collected_at DESC LIMIT 200").fetchall()
    for row in rows:
        try:
            values=json.loads(row['content'])
            found=next((v for v in values if v.get('service')=='worker' and v.get('state')=='running'),None)
        except (ValueError,TypeError,AttributeError):continue
        if found and case and case['sample_job_id']:
            return {'available':True,'target':'fault','snapshot_id':str(row['id']),'collected_at':row['collected_at'],
                'job_id':str(case['sample_job_id']),'question':'一份历史状态快照与当前任务现象可能不一致。请结合当前运行证据与手册调查指定任务一直等待的原因，核对新旧状态；有冲突先补查，仍无法确定请说明缺少什么。',
                'reason':'使用该实例真实历史快照与现在的只读采集，不修改服务。'}
    return {'available':False,'reason':'尚无同一实例的真实历史运行快照或样例任务；请由Codex准备，不使用伪造状态。'}
@router.post("",response_model=Investigation,status_code=202)
def create(data:Input):
    try:return store.create(data)
    except ModelBlocked as exc:raise HTTPException(409,str(exc)) from None
    except FileExistsError as exc:raise HTTPException(409,str(exc)) from None
    except ValueError as exc:raise HTTPException(400,str(exc)) from None
@router.get("",response_model=list[Investigation])
def recent(view:Literal['all','daily']='all'):
    from opslens.durability import describe
    with db.connect() as c:
        where=" WHERE (input->>'target'='external' OR input->>'record_origin'='daily') AND coalesce(metrics->>'model','') NOT LIKE 'controlled-%'" if view=='daily' else ''
        rows=c.execute('SELECT *,reference_items AS "references" FROM investigations'+where+' ORDER BY created_at DESC LIMIT 30').fetchall()
        for row in rows:row['recovery']=describe(c,'investigation',row['id'])
        return rows
@router.get("/{i}",response_model=Investigation)
def get(i:UUID):
    try:
        with db.connect() as c:return store.get(c,i)
    except LookupError as exc:raise HTTPException(404,str(exc)) from None
@router.get("/{i}/events",response_class=StreamingResponse,responses={200:{"content":{"text/event-stream":{}},"description":"持久化调查进度，支持 Last-Event-ID 与 after"}})
async def events(i:UUID,request:Request,after:int=0):
    try:
        cursor=max(after,int(request.headers.get("last-event-id","0")))
        if after<0 or int(request.headers.get("last-event-id","0"))<0:raise ValueError()
    except ValueError:raise HTTPException(400,"事件游标必须为非负整数") from None
    try:
        with db.connect() as c:store.get(c,i)
    except LookupError as exc:raise HTTPException(404,str(exc)) from None
    async def generate():
        nonlocal cursor
        while not await request.is_disconnected():
            with db.connect() as c:
                row=store.get(c,i)
                items=c.execute("SELECT * FROM investigation_events WHERE investigation_id=%s AND id>%s ORDER BY id",(i,cursor)).fetchall()
            for item in items:
                cursor=item["id"]
                yield f"id: {cursor}\ndata: {json.dumps(item,ensure_ascii=False,default=str)}\n\n"
            if row["status"] in ("completed","blocked","failed"):return
            yield ": heartbeat\n\n"
            await asyncio.sleep(.5)
    return StreamingResponse(generate(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})
