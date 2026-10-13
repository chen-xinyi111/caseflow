import asyncio
import json
from pathlib import Path
from uuid import UUID
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from opslens import db
from opslens.rag import store
from opslens.rag.contracts import Citation, Document, DocumentDetail, Index, IndexInput, KBStatus, SearchInput, Task
from opslens.rag.provider import QuotaGuard, EMBED_MODEL, RERANK_MODEL
from opslens.rag.text import MAX_BYTES

router=APIRouter(prefix="/api/v1/kb",tags=["knowledge"])


def translate(exc):
    if isinstance(exc,FileExistsError): return HTTPException(409,str(exc))
    if isinstance(exc,LookupError): return HTTPException(404,str(exc))
    return HTTPException(400,str(exc))


@router.get("/documents",response_model=list[Document])
def documents():
    with db.connect() as conn: return store.document_rows(conn)


@router.post("/documents",response_model=Document,status_code=201)
def upload(file:UploadFile=File(...),title:str=Form(min_length=1,max_length=200),source:str=Form(min_length=1,max_length=500),doc_version:str=Form(default="v1",min_length=1,max_length=100),applicable_versions:str=Form(default="*",max_length=2000)):
    raw=file.file.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES: raise HTTPException(413,"文件超过 5 MiB")
    filename=Path((file.filename or "document.txt").replace("\\","/")).name[:200]
    try:
        return store.ingest(filename,raw,title,source,doc_version,applicable_versions.split(","))
    except (ValueError,FileExistsError,LookupError) as exc: raise translate(exc) from None


@router.get("/documents/{document_id}",response_model=DocumentDetail)
def detail(document_id:UUID):
    try:
        with db.connect() as conn: return store.document(conn,document_id)
    except (ValueError,LookupError,FileExistsError) as exc: raise translate(exc) from None


@router.get("/documents/{document_id}/original")
def original(document_id:UUID):
    with db.connect() as conn:
        row=conn.execute("SELECT original_bytes,format FROM kb_documents WHERE id=%s",(document_id,)).fetchone()
    if not row: raise HTTPException(404,"原文件不存在")
    return Response(bytes(row["original_bytes"]),media_type="application/octet-stream",headers={"Content-Disposition":f'attachment; filename="document-{document_id}.{row["format"]}"',"X-Content-Type-Options":"nosniff"})


@router.get("/indexes",response_model=list[Index])
def indexes():
    with db.connect() as conn: return conn.execute("SELECT * FROM kb_indexes ORDER BY created_at DESC,id").fetchall()


@router.post("/indexes",response_model=Task,status_code=202)
def create_index(data:IndexInput):
    try: return store.create_index(data.mode,data.document_ids,data.request_id)
    except (ValueError,LookupError,FileExistsError) as exc: raise translate(exc) from None


@router.post("/searches",response_model=Task,status_code=202)
def create_search(data:SearchInput):
    try: return store.create_search(data)
    except (ValueError,LookupError,FileExistsError) as exc: raise translate(exc) from None


@router.get("/tasks/{task_id}",response_model=Task)
def task(task_id:UUID):
    try:
        with db.connect() as conn: return store.task(conn,task_id)
    except LookupError as exc: raise translate(exc) from None


@router.get("/citations/{chunk_id}",response_model=Citation)
def citation(chunk_id:UUID):
    try:
        with db.connect() as conn: return store.citation(conn,chunk_id)
    except (ValueError,LookupError,FileExistsError) as exc: raise translate(exc) from None


@router.get("/status",response_model=KBStatus)
def status():
    import os
    guard=QuotaGuard(key=os.environ.get("DASHSCOPE_APIKEY",""))
    models={}
    for model in (EMBED_MODEL,RERANK_MODEL):
        try:
            guard.check(model)
            models[model]={"available":True,"reason":"免费额度及用完即停已核验"}
        except Exception as exc:
            models[model]={"available":False,"reason":str(exc)}
    with db.connect() as conn:
        return {"documents":conn.execute("SELECT count(*) AS n FROM kb_documents").fetchone()["n"],"chunks":conn.execute("SELECT count(*) AS n FROM kb_chunks").fetchone()["n"],"indexes":conn.execute("SELECT * FROM kb_indexes ORDER BY created_at DESC,id").fetchall(),"models":models}


@router.get("/tasks/{task_id}/events",response_class=StreamingResponse,responses={200:{"content":{"text/event-stream":{}},"description":"持久化进度，支持 after 和 Last-Event-ID"}})
async def events(task_id:UUID,request:Request,after:int=0):
    try:
        header=int(request.headers.get("last-event-id","0"))
        if after<0 or header<0: raise ValueError
        cursor=max(after,header)
    except ValueError: raise HTTPException(400,"事件游标必须为非负整数") from None
    try:
        with db.connect() as conn: store.task(conn,task_id)
    except LookupError as exc: raise translate(exc) from None
    async def generate():
        nonlocal cursor
        while not await request.is_disconnected():
            with db.connect() as conn:
                current=store.task(conn,task_id)
                rows=conn.execute("SELECT * FROM kb_events WHERE task_id=%s AND id>%s ORDER BY id",(task_id,cursor)).fetchall()
            for row in rows:
                cursor=row["id"]
                yield f"id: {cursor}\ndata: {json.dumps(row,ensure_ascii=False,default=str)}\n\n"
            if current["status"] in ("completed","failed","blocked"): return
            yield ": heartbeat\n\n"
            await asyncio.sleep(.5)
    return StreamingResponse(generate(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})
