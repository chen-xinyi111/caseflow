import asyncio
import json
from pathlib import Path
from datetime import datetime,timezone
from uuid import UUID,uuid5
from fastapi import APIRouter,HTTPException,UploadFile,File,Form,Request
from fastapi.responses import StreamingResponse,Response
from opslens import db
from opslens.rag.provider import ModelBlocked
from . import store,model,intake,documents
from .contracts import SessionInput,Session,SourceInput,Source,Original,PreviewInput,Preview,AnalyzeInput,Turn,FeedbackInput,ModelSettings,Settings,LinkInput,ReferenceInput,DockerInput

router=APIRouter(prefix='/api/v1/diagnostic',tags=['troubleshooting'])
def checked(fn,*args):
    try:return fn(*args)
    except LookupError as e:raise HTTPException(404,str(e)) from None
    except FileExistsError as e:raise HTTPException(409,str(e)) from None
    except ModelBlocked as e:raise HTTPException(409,str(e)) from None
    except ValueError as e:raise HTTPException(400,str(e)) from None

@router.get('/submissions/{request_id}')
def submission(request_id:UUID):
    # Recovery uses identifiers, never raw request bodies in browser storage.
    with db.connect() as c:
        for table,kind,dto in [('diagnostic_sessions','session',Session),('diagnostic_sources','source',Source),('diagnostic_previews','preview',Preview),('diagnostic_turns','turn',Turn)]:
            row=c.execute('SELECT * FROM '+table+' WHERE request_id=%s',(request_id,)).fetchone()
            if row:return {'kind':kind,'value':dto.model_validate(store.preview_dto(row) if kind=='preview' else row).model_dump(mode='json')}
        feedback=c.execute('SELECT session_id FROM diagnostic_feedback WHERE request_id=%s',(request_id,)).fetchone()
        if feedback:return {'kind':'session','value':Session.model_validate(store.get_session(c,feedback['session_id'])).model_dump(mode='json')}
        rows=c.execute('SELECT * FROM diagnostic_sources WHERE request_id=ANY(%s::uuid[]) ORDER BY created_at,id',([uuid5(request_id,str(i)) for i in range(4)],)).fetchall()
        if rows:
            expected=rows[0]['provenance'].get('batch_count',len(rows))
            if len(rows)!=expected:raise HTTPException(409,'上传批次不完整，请保留原文件，不将部分资料当作完成')
            return {'kind':'files','value':[Source.model_validate(row).model_dump(mode='json') for row in rows]}
    raise HTTPException(404,'尚未找到提交记录，请保留原输入后重试')

@router.post('/sessions',response_model=Session,status_code=201)
def create(data:SessionInput):return checked(store.create_session,data)

@router.get('/sessions',response_model=list[Session])
def sessions():
    with db.connect() as c:return c.execute('SELECT * FROM diagnostic_sessions ORDER BY created_at DESC LIMIT 100').fetchall()

@router.get('/sessions/{sid}',response_model=Session)
def session(sid:UUID):
    with db.connect() as c:return checked(store.get_session,c,sid)

@router.get('/sessions/{sid}/sources',response_model=list[Source])
def sources(sid:UUID):
    with db.connect() as c:
        checked(store.get_session,c,sid)
        return c.execute('SELECT * FROM diagnostic_sources WHERE session_id=%s ORDER BY created_at,id',(sid,)).fetchall()

@router.get('/sessions/{sid}/sources/{i}',response_model=Original)
def original(sid:UUID,i:UUID):
    with db.connect() as c:return checked(store.get_source,c,sid,i)

@router.post('/sessions/{sid}/sources',response_model=Source,status_code=201)
def source(sid:UUID,data:SourceInput):
    provenance={'source':'用户提供的文本','recorded_at':'未明确；不代表当前实时状态'}
    if data.check_reference:provenance['check_reference']=data.check_reference.model_dump(mode='json')
    return checked(store.add_source,sid,data.request_id,data.name,data.kind,data.content,provenance)

@router.post('/sessions/{sid}/files',response_model=list[Source],status_code=201)
async def files(sid:UUID,request_id:UUID=Form(...),kind:str=Form('log'),files:list[UploadFile]=File(...)):
    if not 1<=len(files)<=4 or kind not in ('log','document'):raise HTTPException(400,'每次最多4个日志或项目资料文件')
    decoded=[]
    for file in files:
        raw=bytearray()
        while chunk:=await file.read(65536):
            raw.extend(chunk)
            if len(raw)>intake.MAX_FILE:raise HTTPException(400,'单文件超过5MiB')
        name=Path(file.filename or 'log.txt').name
        decoded.append((name,checked(intake.decode_file,name,bytes(raw))))
    with db.connect() as c:
        secrets=checked(model.dynamic_secrets,c)
        batch_digest=intake.sha(json.dumps([(name,kind,intake.sha(store.clean(text,secrets))) for name,text in decoded],ensure_ascii=False))
        provenance={'source':'用户上传文本','recorded_at':'未明确；不代表当前实时状态','batch_count':len(decoded),'batch_digest':batch_digest}
        return [checked(store.add_source,sid,uuid5(request_id,str(n)),name,kind,text,provenance,c,secrets) for n,(name,text) in enumerate(decoded)]

@router.post('/sessions/{sid}/previews',response_model=Preview)
def prepare(sid:UUID,data:PreviewInput):return checked(store.create_preview,sid,data)

@router.get('/sessions/{sid}/previews/{i}',response_model=Preview)
def get_preview(sid:UUID,i:UUID):
    with db.connect() as c:return checked(store.preview,c,i,sid)

@router.post('/sessions/{sid}/turns',response_model=Turn,status_code=202)
def analyze(sid:UUID,data:AnalyzeInput):return checked(store.create_turn,sid,data)

@router.get('/sessions/{sid}/turns',response_model=list[Turn])
def turns(sid:UUID):
    with db.connect() as c:
        checked(store.get_session,c,sid)
        return c.execute('SELECT * FROM diagnostic_turns WHERE session_id=%s ORDER BY created_at,id',(sid,)).fetchall()

@router.get('/turns/{i}',response_model=Turn)
def get_turn(i:UUID):
    with db.connect() as c:return checked(store.get_turn,c,i)

@router.get('/turns/{i}/events',response_class=StreamingResponse)
async def events(i:UUID,request:Request,after:int=0):
    try:
        cursor=max(after,int(request.headers.get('last-event-id','0')))
        if min(after,int(request.headers.get('last-event-id','0')))<0:raise ValueError()
    except ValueError:raise HTTPException(400,'事件游标必须非负') from None
    with db.connect() as c:checked(store.get_turn,c,i)
    async def stream():
        nonlocal cursor
        while not await request.is_disconnected():
            with db.connect() as c:
                t=store.get_turn(c,i);rows=c.execute('SELECT * FROM diagnostic_events WHERE turn_id=%s AND id>%s ORDER BY id',(i,cursor)).fetchall()
            for row in rows:
                cursor=row['id'];yield 'id: '+str(cursor)+'\ndata: '+json.dumps(row,ensure_ascii=False,default=str)+'\n\n'
            if t['status'] not in ('queued','running'):return
            yield ': heartbeat\n\n';await asyncio.sleep(.5)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

@router.post('/sessions/{sid}/feedback',response_model=Session)
def feedback(sid:UUID,data:FeedbackInput):return checked(store.feedback,sid,data)

@router.get('/sessions/{sid}/export')
def export(sid:UUID):return Response(checked(store.export,sid),media_type='text/markdown; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="opslens-{sid}.md"','X-Content-Type-Options':'nosniff'})

@router.get('/settings',response_model=Settings)
def settings():return model.settings_status()

@router.post('/settings',response_model=Settings)
def save(data:ModelSettings):
    checked(model.save_settings,data)
    return model.settings_status()

@router.post('/sessions/{sid}/official-document',response_model=Source,status_code=201)
def document(sid:UUID,data:LinkInput):
    with db.connect() as c:
        checked(store.get_session,c,sid);cfg=model.config(c)
        existing=c.execute('SELECT * FROM diagnostic_sources WHERE request_id=%s',(data.request_id,)).fetchone()
        if existing:
            if str(existing['session_id'])!=str(sid) or existing['provenance'].get('requested_url')!=data.url:raise HTTPException(409,'文档请求编号已用于其他内容')
            return existing
    text,url=checked(documents.fetch,data.url,cfg['settings']['official_hosts'])
    return checked(store.add_source,sid,data.request_id,'官方文档 '+urlsplit_host(url),'official_doc',text,{'source':'官方链接正文','url':url,'requested_url':data.url,'fetched_at':datetime.now(timezone.utc).isoformat()})

def urlsplit_host(url):
    from urllib.parse import urlsplit
    return urlsplit(url).hostname or '文档'

@router.get('/sessions/{sid}/related',response_model=list[Session])
def related(sid:UUID):
    from opslens.rag.ranking import bm25
    with db.connect() as c:
        current=checked(store.get_session,c,sid)
        rows=c.execute('SELECT * FROM diagnostic_sessions WHERE id<>%s ORDER BY created_at DESC LIMIT 100',(sid,)).fetchall()
        signatures=c.execute('SELECT summary FROM diagnostic_sources WHERE session_id=%s',(sid,)).fetchall()
        history=c.execute('SELECT session_id,summary FROM diagnostic_sources WHERE session_id=ANY(%s::uuid[])',([r['id'] for r in rows],)).fetchall()
    query=current['problem']+' '+' '.join(g['signature'] for s in signatures for g in s['summary']['groups'][:3])
    features={str(r['id']):r['problem']+' '+r['project'] for r in rows}
    for source in history:features[str(source['session_id'])]+=' '+' '.join(g['signature'] for g in source['summary']['groups'][:10])
    ranked=bm25(query,[{'id':i,'text':text} for i,text in features.items()],5);lookup={str(r['id']):r for r in rows}
    return [lookup[h['id']] for h in ranked]

@router.post('/sessions/{sid}/history-reference',response_model=Source,status_code=201)
def reference(sid:UUID,data:ReferenceInput):
    if sid==data.reference_session_id:raise HTTPException(400,'不能把当前排查作为自身历史参考')
    text=checked(store.export,data.reference_session_id)
    return checked(store.add_source,sid,data.request_id,'历史排查 '+str(data.reference_session_id),'history',text[:100000],{'source':'用户选择的历史排查，不能证明当前问题','reference_session_id':str(data.reference_session_id)})

@router.post('/sessions/{sid}/docker',response_model=Source,status_code=201)
def docker(sid:UUID,data:DockerInput):
    from opslens.external import capture
    with db.connect() as c:checked(store.get_session,c,sid)
    snapshot=checked(capture,data.target_id,data.request_id)
    content='\n\n'.join(item['source']+'\n'+item['content'] for item in snapshot['items'])
    return checked(store.add_source,sid,data.request_id,'Docker只读采集','docker',content,{'source':'Docker只读快照','captured_at':str(snapshot['captured_at']),'errors':snapshot['errors']})
