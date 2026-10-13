"""Actual frozen-input evaluation; no oracle is visible to the model."""
import json,time,os
from uuid import UUID
from opslens import db,durability
from opslens.config import secrets,ROOT
from opslens.security import redact
from opslens.rag.store import jsonb
from opslens.rag.provider import DashScopeProvider,DatabaseLedger,ModelBlocked,EMBED_MODEL,RERANK_MODEL
from opslens.rag.service import search
from opslens.investigation.model import GenerationProvider
from opslens.evaluation.store import MODEL
from opslens.investigation.tools import Tools,ToolInput
from opslens.investigation.engine import run_agent
from opslens.investigation.collaboration import run_collaboration
from opslens.evaluation.dataset import load_dataset,verify_freeze,digest
from opslens.evaluation.scoring import retrieval_score,investigation_score

class Ledger(DatabaseLedger):
    def __init__(self,item_id):self.item_id=item_id
    def reserve(self,*args):
        i=super().reserve(*args)
        with durability.write() as c:c.execute('INSERT INTO evaluation_calls(item_id,call_id) VALUES (%s,%s)',(self.item_id,i))
        return i

class FrozenTools(Tools):
    def __init__(self,payload,snapshot,provider,namespace='',allowed=None):
        super().__init__(payload,namespace,allowed);self.snapshot=snapshot;self.provider=provider
    def fork(self,task_id,role):
        return FrozenTools(self.payload,self.snapshot,self.provider,task_id+'-',{'collect_runtime','read_job'} if role=='runtime' else {'search_knowledge'})
    def execute(self,name,args):
        if self.allowed is not None and name not in self.allowed:raise ValueError('角色工具越权')
        args=ToolInput.parse(name,args)
        if name in ('collect_runtime','read_job'):
            if self.payload['target']=='unconnected':return {'unavailable':True,'references':[],'missing':'未接入实例，没有运行状态或业务记录，不能确认原因。'}
            if name=='read_job' and args['job_id']!=self.payload.get('job_id'):raise ValueError('只能读取冻结案例的指定任务')
            allowed={'docker.services','docker.config_summary','lab.diagnostics','docker.logs.worker','docker.logs.api'}
            wanted=[r for r in self.snapshot['evidence'] if r['source']=='lab.job_snapshot'] if name=='read_job' else [r for r in self.snapshot['evidence'] if r['source'] in allowed]
            if name=='collect_runtime' and self.collected:return {'already_collected':True,'references':[r for r in self.refs if r['kind']=='runtime']}
            if name=='collect_runtime':self.collected=True
            values=[self.add(r,'/api/v1/evidence/'+r['id'],scope='current') if r['success'] else {'source':r['source'],'error':r['content']} for r in wanted]
            return {'references':values,'note':'本次比较绑定同一采集时刻的真实快照；不是当前实时状态。'}
        result=search({'query':args['query'],'mode':'rerank','index_id':self.payload['index_id'],'app_version':'v1'},provider=self.provider)
        return {'references':[self.add({'source':hit['document']['source'],'content':hit['chunk']['content']},hit['citation_url'],'knowledge',doc_version=hit['document']['doc_version'],index_id=result['index_id']) for hit in result['hits']]}

def metrics(c,item_id):
    calls=c.execute('SELECT m.model,m.status,m.estimated_tokens,m.actual_tokens FROM kb_model_calls m JOIN evaluation_calls e ON e.call_id=m.id WHERE e.item_id=%s',(item_id,)).fetchall()
    return {'actual_tokens':sum(r['actual_tokens'] for r in calls if r['actual_tokens'] is not None),'unknown_usage_requests':sum(r['actual_tokens'] is None for r in calls),'request_count':len(calls),'reserved_tokens':sum(r['estimated_tokens'] for r in calls),'models':sorted({r['model'] for r in calls})}
def preflight(row):
    provider=DashScopeProvider()
    models=[]
    if row['kind']=='investigation':models=[MODEL,EMBED_MODEL,RERANK_MODEL]
    elif row['mode']!='keyword':models=[EMBED_MODEL]+([RERANK_MODEL] if row['mode']=='rerank' else [])
    for model in models:provider.guard.check(model)
def process(row):
    verify_freeze()
    case=next(c for c in load_dataset()[row['kind']] if c['id']==row['case_id'])
    rt=durability.Runtime('evaluation',row);started=time.monotonic();tools=None
    try:
        with rt.live():
            preflight(row)
            ledger=Ledger(row['id']);provider=DashScopeProvider(ledger=ledger)
            identity={'freeze':verify_freeze()['dataset_sha256'],'model':MODEL,'mode':row['mode'],'index_id':row['input']['index_id']}
            if rt.operation('execution:identity',lambda:identity)!=identity:raise ModelBlocked('评测配置变化，禁止重放旧执行')
            if row['kind']=='retrieval':
                result=rt.operation('retrieval:result',lambda:search(row['input'],provider=provider))
                score=retrieval_score(case,result['hits']);refs=[];measured={}
                with db.connect() as c:
                    for hit in result['hits']:
                        chunk=c.execute('SELECT content FROM kb_chunks WHERE id=%s',(hit['chunk']['id'],)).fetchone()
                        if not chunk or chunk['content']!=hit['chunk']['content']:raise ValueError('检索引用原文不一致')
            else:
                with db.connect() as c:saved=c.execute('SELECT * FROM evaluation_snapshots WHERE run_id=%s AND source_group=%s',(row['run_id'],row['source_group'])).fetchone()
                if not saved and case['target']!='unconnected':raise ModelBlocked('真实故障快照尚未准备，未调用模型')
                snapshot=saved['payload'] if saved else {'evidence':[],'job_id':None}
                if saved and digest(snapshot)!=saved['sha256']:raise ValueError('冻结运行快照摘要不符')
                payload=dict(row['input'])
                if payload['target']!='unconnected':payload['job_id']=snapshot['job_id']
                tools=durability.SavedTools(FrozenTools(payload,snapshot,provider),rt)
                model=durability.SavedModel(GenerationProvider(ledger=ledger,model=MODEL),rt)
                def record(kind,value):
                    clean=json.loads(redact(json.dumps(value,ensure_ascii=False,default=str),secrets()))
                    with rt.transaction() as c:durability.event(c,'evaluation',row['id'],kind,clean)
                runner=run_agent if row['mode']=='single' else run_collaboration
                result=runner(payload,model,tools,record,timeout=max(0,240-rt.elapsed-(time.monotonic()-started)))
                refs=tools.refs;score=investigation_score(case,result['report'],refs)
                # Independently open stored original sources, not only tool snippets.
                from opslens.rag.store import citation
                by_id={r['id']:r for r in refs}
                with db.connect() as c:
                    for finding in result['report']['findings']:
                        for quote in finding['references']:
                            ref=by_id[quote['id']]
                            if ref['kind']=='runtime':
                                original=c.execute('SELECT content FROM evidence WHERE id=%s',(ref['url'].rsplit('/',1)[1],)).fetchone()['content']
                            else:original=citation(c,ref['url'].rsplit('/',1)[1])['page_text']
                            if quote['quote'] not in original:raise ValueError('引用无法在已保存原文中核对')
                measured={k:v for k,v in result.items() if k not in ('report','collaboration')}
                measured['response_source']='real_model';measured['model']=MODEL
            with db.connect() as c:measured.update(metrics(c,row['id']))
            measured['elapsed_ms']=round((rt.elapsed+time.monotonic()-started)*1000,2)
            measured['cache_note']='调用与缓存均按实际记录计数；案例内对照顺序交替，未将缓存请求当作模型调用'
            rt.finish('completed',result=result,reference_items=refs,score=score,metrics=measured)
    except durability.LeaseLost:return
    except Exception as e:
        error=redact(str(e),secrets())[:1000]
        try:
            with db.connect() as c:measured=metrics(c,row['id'])
            measured.update(elapsed_ms=round((rt.elapsed+time.monotonic()-started)*1000,2),response_source='real_model' if measured['request_count'] else 'no_model_call')
            rt.finish('blocked' if isinstance(e,ModelBlocked) else 'failed',error=error,reference_items=tools.refs if tools else [],metrics=measured)
        except durability.LeaseLost:return

def run(run_id,kind,split,modes,limit=None,pilot=False):
    from opslens.evaluation import store
    verify_freeze();store.seal_execution(run_id);completed=0
    # A run-wide session lock prevents concurrent CLI instances from changing order.
    with db.connect() as lock:
        if not lock.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,3202)) ok',(str(run_id),)).fetchone()['ok']:raise ValueError('该评测已有执行进程')
        try:
            with db.connect() as c:
                report=store.report(c,run_id)
                if report['manifest']['dataset_sha256']!=verify_freeze()['dataset_sha256'] or report['manifest']['model']!=MODEL:raise ValueError('冻结模型或数据集不同')
                rows=[r for r in store.rows(c,run_id) if r['kind']==kind and (split=='all' or r['split']==split) and r['mode'] in modes]
                if pilot:rows=[r for r in rows if r['split']=='dev' and r['case_id'].endswith(('-01','-02'))]
            cases=sorted({r['case_id'] for r in rows})
            for n,case_id in enumerate(cases):
                pair=[r for r in rows if r['case_id']==case_id]
                order=list(store.MODES[kind]);order=order[n%len(order):]+order[:n%len(order)]
                for row in sorted(pair,key=lambda r:order.index(r['mode'])):
                    if row['status'] not in ('pending','running','blocked'):continue
                    if limit is not None and completed>=limit:return
                    # Blocks with no outbound request can be retried after quota verification.
                    if row['status']=='blocked':
                        with db.connect() as c:
                            unknown=c.execute("SELECT 1 FROM durable_operations WHERE family='evaluation' AND task_id=%s AND paid AND status='issued'",(row['id'],)).fetchone()
                            if unknown:continue
                            c.execute("UPDATE evaluation_items SET status='pending',error=NULL,finished_at=NULL WHERE id=%s",(row['id'],))
                    task=durability.claim('evaluation',row['id'])
                    if task:process(task);completed+=1
                    with db.connect() as c:current=c.execute('SELECT status,error FROM evaluation_items WHERE id=%s',(row['id'],)).fetchone()
                    print(case_id+' '+row['mode']+' '+current['status'],flush=True)
                    if current['status']=='blocked':
                        print(current['error'],flush=True);return
        finally:
            lock.execute('SELECT pg_advisory_unlock(hashtextextended(%s,3202))',(str(run_id),));store.finalize(run_id)
