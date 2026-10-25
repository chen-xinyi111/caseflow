import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4, uuid5
from psycopg.types.json import Jsonb
from opslens import db
from opslens.rag.text import extract, split_pages
from opslens.rag.ranking import PARAMETERS
from opslens.rag.provider import EMBED_MODEL, DIMENSIONS


def jsonb(value):
    return Jsonb(value,dumps=lambda obj:json.dumps(obj,ensure_ascii=False,default=str))


def embedding_key(text, kind="document"):
    return hashlib.sha256((kind+"\0"+text).encode()).hexdigest()


def document_rows(conn):
    return conn.execute("SELECT d.id,d.title,d.source,d.doc_version,d.applicable_versions,d.filename,d.format,d.sha256,d.created_at,count(c.id)::integer AS chunk_count FROM kb_documents d LEFT JOIN kb_chunks c ON c.document_id=d.id GROUP BY d.id ORDER BY d.created_at DESC,d.id").fetchall()


def document(conn, document_id):
    row=conn.execute("SELECT id,title,source,doc_version,applicable_versions,filename,format,sha256,created_at,pages FROM kb_documents WHERE id=%s",(document_id,)).fetchone()
    if not row: raise LookupError("文档不存在")
    row["chunks"]=conn.execute("SELECT * FROM kb_chunks WHERE document_id=%s ORDER BY ordinal",(document_id,)).fetchall()
    row["chunk_count"]=len(row["chunks"])
    return row


def ingest(filename, raw, title, source, version, applicable_versions):
    if not all(isinstance(value,str) and value.strip() and not any(ord(c)<32 for c in value) for value in (title,source,version)):
        raise ValueError("标题、来源和文档版本不能为空")
    if not applicable_versions or len(applicable_versions)>20 or any(not v.strip() or len(v)>100 for v in applicable_versions):
        raise ValueError("请填写有效的适用应用版本")
    versions=sorted(set(v.strip() for v in applicable_versions))
    title,source,version=title.strip(),source.strip(),version.strip()
    extracted=extract(filename,raw)
    digest=hashlib.sha256(raw).hexdigest()
    with db.connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,1201))",(json.dumps([source,version]),))
        old=conn.execute("SELECT * FROM kb_documents WHERE source=%s AND doc_version=%s",(source,version)).fetchone()
        if old:
            if old["sha256"]!=digest or old["title"]!=title or old["applicable_versions"]!=versions or old["format"]!=extracted.format:
                raise FileExistsError("该来源和版本已有不同内容，请使用新的文档版本")
            return document(conn,old["id"])
        document_id=uuid4()
        conn.execute("INSERT INTO kb_documents(id,title,source,doc_version,applicable_versions,filename,format,original_bytes,pages,sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",(document_id,title,source,version,jsonb(versions),filename,extracted.format,raw,jsonb(extracted.pages),digest))
        for ordinal,chunk in enumerate(split_pages(extracted.pages)):
            retrieval=title+"\n"+chunk["content"]
            chunk_id=uuid5(document_id,str(ordinal))
            conn.execute("INSERT INTO kb_chunks(id,document_id,ordinal,page,start_char,end_char,line_start,line_end,heading,content,retrieval_text,sha256,embedding_key) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",(chunk_id,document_id,ordinal,chunk["page"],chunk["start"],chunk["end"],chunk["line_start"],chunk["line_end"],chunk["heading"],chunk["content"],retrieval,hashlib.sha256(chunk["content"].encode()).hexdigest(),embedding_key(retrieval)))
        return document(conn,document_id)


def ingest_snapshot(filename, pages, title, source, version):
    """Index canonical pages without flattening/reparsing. Bytes are derived TXT, not a user original."""
    if not pages or not all(isinstance(p,str) for p in pages) or not any(p.strip() for p in pages):
        raise ValueError('索引快照没有可用正文')
    raw='\n\n'.join(pages).encode('utf-8');digest=hashlib.sha256(raw).hexdigest()
    with db.connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,1201))',(json.dumps([source,version]),))
        old=conn.execute('SELECT * FROM kb_documents WHERE source=%s AND doc_version=%s',(source,version)).fetchone()
        if old:
            if old['pages']!=pages or old['sha256']!=digest or old['title']!=title:
                raise FileExistsError('索引快照版本已存在不同内容')
            return document(conn,old['id'])
        did=uuid4()
        conn.execute('''INSERT INTO kb_documents(id,title,source,doc_version,applicable_versions,filename,format,original_bytes,pages,sha256)
            VALUES (%s,%s,%s,%s,%s,%s,'txt',%s,%s,%s)''',(did,title,source,version,jsonb(['*']),filename,raw,jsonb(pages),digest))
        for ordinal,chunk in enumerate(split_pages(pages)):
            retrieval=title+'\n'+chunk['content'];cid=uuid5(did,str(ordinal))
            conn.execute('''INSERT INTO kb_chunks(id,document_id,ordinal,page,start_char,end_char,line_start,line_end,heading,content,retrieval_text,sha256,embedding_key)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (cid,did,ordinal,chunk['page'],chunk['start'],chunk['end'],chunk['line_start'],chunk['line_end'],chunk['heading'],chunk['content'],retrieval,hashlib.sha256(chunk['content'].encode()).hexdigest(),embedding_key(retrieval)))
        return document(conn,did)


def append_event(conn, task_id, kind, payload):
    conn.execute("INSERT INTO kb_events(task_id,kind,payload) VALUES (%s,%s,%s)",(task_id,kind,jsonb(payload)))


def requested_task(conn,request_id,payload):
    if not request_id:return None
    conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,3103))',(str(request_id),))
    old=conn.execute('SELECT * FROM kb_tasks WHERE request_id=%s',(request_id,)).fetchone()
    if old and old['request_input']!=payload:raise FileExistsError('请求编号已用于不同知识库任务')
    return old


def create_index(mode, selected=None, request_id=None):
    with db.connect() as conn:
        requested={'mode':mode,'document_ids':sorted(str(i) for i in selected) if selected is not None else None}
        old=requested_task(conn,request_id,requested)
        if old:return old
        if selected is None:
            ids=[str(row["id"]) for row in conn.execute("SELECT DISTINCT ON(source) id FROM kb_documents ORDER BY source,created_at DESC,id DESC").fetchall()]
        else:
            ids=list(dict.fromkeys(str(value) for value in selected))
        if not ids: raise ValueError("请先导入文档")
        known=conn.execute("SELECT id,sha256 FROM kb_documents WHERE id=ANY(%s)",([UUID(value) for value in ids],)).fetchall()
        if len(known)!=len(ids): raise LookupError("指定的文档不存在")
        manifest={"documents":sorted((str(row["id"]),row["sha256"]) for row in known),"parameters":PARAMETERS,"embedding_model":EMBED_MODEL if mode=="semantic" else None}
        digest=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
        index_id,task_id=uuid4(),uuid4()
        conn.execute("INSERT INTO kb_indexes(id,mode,status,document_ids,parameters,manifest_sha256,embedding_model,dimensions) VALUES (%s,%s,'building',%s,%s,%s,%s,%s)",(index_id,mode,jsonb(ids),jsonb(PARAMETERS),digest,EMBED_MODEL if mode=="semantic" else None,DIMENSIONS if mode=="semantic" else None))
        conn.execute("INSERT INTO kb_index_chunks(index_id,chunk_id) SELECT %s,id FROM kb_chunks WHERE document_id=ANY(%s)",(index_id,[UUID(value) for value in ids]))
        row=conn.execute("INSERT INTO kb_tasks(id,kind,status,input,request_id,request_input) VALUES (%s,'index','queued',%s,%s,%s) RETURNING *",(task_id,jsonb({"index_id":str(index_id)}),request_id,jsonb(requested))).fetchone()
        append_event(conn,task_id,"queued",{"message":"索引构建已排队"})
        return row


def create_search(data):
    with db.connect() as conn:
        requested=data.model_dump(mode='json',exclude={'request_id'})
        old=requested_task(conn,data.request_id,requested)
        if old:return old
        mode="keyword" if data.mode=="keyword" else "semantic"
        if data.index_id:
            index=conn.execute("SELECT * FROM kb_indexes WHERE id=%s",(data.index_id,)).fetchone()
        else:
            index=conn.execute("SELECT * FROM kb_indexes WHERE mode=%s AND status='ready' AND active ORDER BY finished_at DESC,id DESC LIMIT 1",(mode,)).fetchone()
        if not index or index["status"]!="ready":
            raise LookupError("尚无可用的对应索引，请先构建索引")
        if data.mode!="keyword" and index["mode"]!="semantic":
            raise ValueError("该索引没有真实向量，不能用于语义或混合检索")
        task_id=uuid4()
        payload=data.model_dump(mode="json",exclude={"request_id"})|{"index_id":str(index["id"])}
        row=conn.execute("INSERT INTO kb_tasks(id,kind,status,input,request_id,request_input) VALUES (%s,'search','queued',%s,%s,%s) RETURNING *",(task_id,jsonb(payload),data.request_id,jsonb(requested))).fetchone()
        append_event(conn,task_id,"queued",{"message":"检索任务已排队"})
        return row


def task(conn, task_id):
    row=conn.execute("SELECT * FROM kb_tasks WHERE id=%s",(task_id,)).fetchone()
    if not row: raise LookupError("知识库任务不存在")
    from opslens.durability import describe
    row['recovery']=describe(conn,'knowledge',task_id)
    return row


def citation(conn, chunk_id):
    row=conn.execute("SELECT * FROM kb_chunks WHERE id=%s",(chunk_id,)).fetchone()
    if not row: raise LookupError("引用不存在")
    doc=document(conn,row["document_id"])
    page=doc["pages"][row["page"]-1]
    if page[row["start_char"]:row["end_char"]]!=row["content"] or hashlib.sha256(row["content"].encode()).hexdigest()!=row["sha256"]:
        raise ValueError("引用位置或内容校验失败")
    return {"chunk":row,"document":doc,"page_text":page}
