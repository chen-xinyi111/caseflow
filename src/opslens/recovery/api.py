import asyncio,json
from uuid import UUID
from fastapi import APIRouter,HTTPException,Request
from fastapi.responses import StreamingResponse
from opslens import db
from opslens.recovery import store
from opslens.recovery.contracts import Propose,Approve,Plan
from opslens.recovery.adapter import Adapter
from opslens.recovery.policy import TITLES,eligible
from opslens.recovery.worker import reconcile

router=APIRouter(prefix='/api/v1/recovery',tags=['recovery'])
def checked(call,*args):
    try:return call(*args)
    except LookupError as e:raise HTTPException(404,str(e)) from None
    except FileExistsError as e:raise HTTPException(409,str(e)) from None
    except ValueError as e:raise HTTPException(400,str(e)) from None
@router.get('/options')
def options():
    try:
        state=Adapter().snapshot();actions=[{'id':a,'title':t} for a,t in TITLES.items() if eligible(state,a)]
    except Exception:return {'available':False,'reason':'实验状态暂时不可用，请稍后刷新；不执行恢复','actions':[],'investigations':[]}
    with db.connect() as c:
        rows=c.execute("SELECT id,created_at,report->>'summary' summary FROM investigations WHERE status='completed' AND input->>'target'='fault' AND report->>'assessment'='supported' AND verification->>'status'='confirmed' ORDER BY created_at DESC LIMIT 20").fetchall()
    return {'available':True,'reason':'生成方案只读取状态，批准前不修改服务','actions':actions,'investigations':rows}
@router.post('/plans',response_model=Plan,status_code=201)
def create(data:Propose):return checked(store.create,data)
@router.get('/plans',response_model=list[Plan])
def recent():
    from opslens.durability import describe
    with db.connect() as c:
        rows=c.execute('SELECT * FROM recovery_plans ORDER BY created_at DESC LIMIT 30').fetchall()
        for row in rows:row['recovery']=describe(c,'recovery',row['id'])
        return rows
@router.get('/plans/{i}',response_model=Plan)
def get(i:UUID):
    with db.connect() as c:return checked(store.get,c,i)
@router.post('/plans/{i}/approve',response_model=Plan,status_code=202)
def approve(i:UUID,data:Approve):return checked(store.approve,i,data)
@router.post('/plans/{i}/cancel',response_model=Plan)
def cancel(i:UUID,data:Approve):return checked(store.cancel,i,data)
@router.post('/plans/{i}/recheck')
def recheck(i:UUID):return checked(reconcile,i)
@router.get('/plans/{i}/events',response_class=StreamingResponse,responses={200:{'content':{'text/event-stream':{}},'description':'持久化处置进度与原始检查，支持Last-Event-ID'}})
async def events(i:UUID,request:Request,after:int=0):
    try:
        cursor=max(after,int(request.headers.get('last-event-id','0')))
        if after<0 or int(request.headers.get('last-event-id','0'))<0:raise ValueError()
    except ValueError:raise HTTPException(400,'事件游标必须为非负整数') from None
    with db.connect() as c:checked(store.get,c,i)
    async def generate():
        nonlocal cursor
        while not await request.is_disconnected():
            with db.connect() as c:
                plan=store.get(c,i);items=c.execute('SELECT * FROM recovery_events WHERE plan_id=%s AND id>%s ORDER BY id',(i,cursor)).fetchall()
            for item in items:
                cursor=item['id'];yield f"id: {cursor}\ndata: {json.dumps(item,ensure_ascii=False,default=str)}\n\n"
            if plan['status'] not in ('approved','executing','verifying'):return
            yield ': heartbeat\n\n';await asyncio.sleep(.5)
    return StreamingResponse(generate(),media_type='text/event-stream',headers={'Cache-Control':'no-cache'})
