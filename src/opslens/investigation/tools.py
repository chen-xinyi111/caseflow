import json,hashlib
from datetime import datetime,timezone
from uuid import UUID,uuid4,uuid5
import httpx
from pydantic import BaseModel,ConfigDict,Field,ValidationError
from opslens import db
from opslens.durability import write,ACTIVE,CURRENT_OPERATION
from opslens.config import LABS,secrets
from opslens.security import redact
from opslens.rag.store import jsonb
from opslens.rag.service import search
class Empty(BaseModel):
    model_config=ConfigDict(extra="forbid")
class JobArgs(Empty):
    job_id:UUID
class SearchArgs(Empty):
    query:str=Field(min_length=1,max_length=1000)
class ToolInput:
    @staticmethod
    def parse(name,args):
        schemas={"collect_runtime":Empty,"read_job":JobArgs,"search_knowledge":SearchArgs}
        if name not in schemas:raise ValueError("拒绝未知或执行类工具")
        try:return schemas[name].model_validate(args).model_dump(mode="json")
        except ValidationError:raise ValueError("工具参数未通过白名单校验") from None
DEFINITIONS=[{"type":"function","function":{"name":name,"description":description,"parameters":schema.model_json_schema()}} for name,description,schema in [
 ("collect_runtime","采集输入target实验实例的当前容器状态、日志与依赖诊断；不修改服务。",Empty),
 ("read_job","只读查询输入target实例的指定业务任务，并保存当时快照。",JobArgs),
 ("search_knowledge","检索输入绑定的知识索引；手册只说明机制，不代表当前运行事实。",SearchArgs)]]
class Tools:
    def __init__(self,payload,namespace="",allowed=None):
        self.payload=payload;self.refs=[];self.collection_id=None;self.collected=False
        self.namespace=namespace;self.allowed=allowed
    def fork(self,task_id,role):
        allowed={"collect_runtime","read_job"} if role=="runtime" else {"search_knowledge"}
        return Tools(self.payload,task_id+"-",allowed)
    def load_snapshot(self,snapshot_id):
        with db.connect() as c:row=c.execute('SELECT * FROM evidence WHERE id=%s',(snapshot_id,)).fetchone()
        if not row or not row['success'] or row['lab']!=self.payload['target'] or row['source']!='docker.services':
            raise ValueError('历史快照不属于目标实例或不是有效服务状态')
        return self.add(row,'/api/v1/evidence/'+str(row['id']),scope='historical')
    def add(self,row,url,kind="runtime",**metadata):
        limit=9000 if row["source"]=="lab.diagnostics" else 3000
        content=row["content"][-limit:] if row["source"].startswith("docker.logs.") else row["content"][:limit]
        ref={"id":("E" if kind=="runtime" else "K")+self.namespace+str(len(self.refs)+1),"kind":kind,"source":row["source"],"content":content,"url":url,"collected_at":row.get("collected_at"),"truncated":len(row["content"])>limit,**metadata}
        self.refs.append(ref)
        return ref
    def collection(self):
        if not self.collection_id:
            rt=ACTIVE.get()
            self.collection_id=uuid5(rt.id,'evidence:'+self.namespace) if rt else uuid4()
            with write() as c:c.execute("INSERT INTO collections(id,lab,status) VALUES (%s,%s,'completed') ON CONFLICT DO NOTHING",(self.collection_id,self.payload['target']))
        return self.collection_id
    def save_runtime(self,item,stable=False):
        op=CURRENT_OPERATION.get()
        eid=uuid5(UUID(str(self.collection())),item['source'] if stable else op+':'+item['source']) if stable or op else uuid4()
        collection_id=self.collection()
        with write() as c:
            c.execute("INSERT INTO evidence(id,collection_id,lab,source,collected_at,success,content,sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",(eid,collection_id,self.payload["target"],item["source"],item["collected_at"],item["success"],item["content"],item["sha256"]))
            rt=ACTIVE.get()
            if rt:c.execute('UPDATE durable_tasks SET checkpoint=%s WHERE family=%s AND task_id=%s',(jsonb({'step':item['source'],'state':'completed','evidence_id':str(eid)}),rt.family,rt.id))
        return self.add(item,"/api/v1/evidence/"+str(eid)) if item["success"] else {"source":item["source"],"error":item["content"]}
    def execute(self,name,args):
        if self.allowed is not None and name not in self.allowed:raise ValueError("该角色无权使用此工具")
        args=ToolInput.parse(name,args)
        target=self.payload["target"]
        if target=='external':
            from opslens.external import execute
            return execute(self,name,args)
        if name in ("collect_runtime","read_job") and target not in LABS:
            return {"unavailable":True,"references":[],"citation_note":"本提示没有引用编号，不可用工具名当证据。findings可为空，missing_information说明所缺资料。","missing":"该实例尚未连接；请补充容器状态、运行日志、依赖地址摘要和业务任务信息。"}
        if name=="collect_runtime":
            if self.collected:return {"already_collected":True,"references":[r for r in self.refs if r["kind"]=="runtime"]}
            from opslens.collector import collect
            self.collected=True
            if ACTIVE.get():
                previous_refs=list(self.refs)
                cid=self.collection()
                with db.connect() as c:saved=c.execute('SELECT * FROM evidence WHERE collection_id=%s ORDER BY collected_at,id',(cid,)).fetchall()
                def persist(item):self.save_runtime(item,stable=True)
                collect(target,sink=persist,skip=[r['source'] for r in saved])
                with db.connect() as c:items=c.execute('SELECT * FROM evidence WHERE collection_id=%s ORDER BY collected_at,id',(cid,)).fetchall()
                self.refs[:]=previous_refs
                refs=[self.add(item,'/api/v1/evidence/'+str(item['id'])) if item['success'] else {'source':item['source'],'error':item['content']} for item in items]
            else:
                items=collect(target)
                refs=[self.save_runtime(item) for item in items]
            with write() as c:
                status="completed" if all(item["success"] for item in items) else "partial" if any(item["success"] for item in items) else "failed"
                c.execute("UPDATE collections SET status=%s,finished_at=now() WHERE id=%s",(status,self.collection_id))
            return {"references":refs,"note":"当前采集时间为事实快照；失败项不能证明服务正常。"}
        if name=="read_job":
            if self.payload.get("job_id") and args["job_id"]!=str(self.payload["job_id"]):raise ValueError("只允许读取用户指定任务")
            op=CURRENT_OPERATION.get()
            if op:
                eid=uuid5(UUID(str(self.collection())),op+':lab.job_snapshot')
                with db.connect() as c:saved=c.execute('SELECT * FROM evidence WHERE id=%s',(eid,)).fetchone()
                if saved:return {'reference':self.add(saved,'/api/v1/evidence/'+str(eid))}
            with httpx.Client(trust_env=False,timeout=7) as client:
                response=client.get(LABS[target]["url"]+"/jobs/"+args["job_id"]);response.raise_for_status()
            content=redact(json.dumps(response.json(),ensure_ascii=False,default=str,indent=2),secrets())
            item={"source":"lab.job_snapshot","content":content,"collected_at":datetime.now(timezone.utc),"success":True,"sha256":hashlib.sha256(content.encode()).hexdigest()}
            return {"reference":self.save_runtime(item)}
        result=search({"query":args["query"],"mode":"rerank","index_id":self.payload["index_id"],"app_version":"v1"})
        references=[]
        for hit in result["hits"]:
            references.append(self.add({"source":hit["document"]["source"],"content":hit["chunk"]["content"]},hit["citation_url"],"knowledge",doc_version=hit["document"]["doc_version"],index_id=result["index_id"],applicable_versions=hit['document']['applicable_versions']))
        return {"references":references,"counts":result["counts"],"index_id":result["index_id"]}
