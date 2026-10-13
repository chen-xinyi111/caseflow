import asyncio,logging,time
from opslens import db
from opslens.investigation import store
from opslens.investigation.engine import run_agent
from opslens.investigation.collaboration import run_collaboration
from opslens.investigation.verification import run_verified
from opslens.investigation.model import GenerationProvider,MODEL
from opslens.investigation.tools import Tools
from opslens.rag.provider import ModelBlocked
from opslens.rag.store import jsonb
from opslens.security import redact
from opslens.config import secrets
from opslens.durability import Runtime, SavedModel, SavedTools, LeaseLost
from opslens import durability

def claim():return durability.claim('investigation')

def process(task,model=None,tools=None):
    rt=Runtime('investigation',task)
    started=time.monotonic()
    try:
        with rt.live():
            if task['input'].get('target')=='external':
                from opslens.external import authorized
                with db.connect() as c:authorized(c,task['input'])
            real=model is None
            model_name=MODEL if real else getattr(model,'name','controlled-test-response')
            from opslens.investigation.engine import PROMPT_SHA256
            from opslens.investigation.collaboration import PROMPT_HASH as MULTI_PROMPT
            from opslens.investigation.verification import PROMPT_HASH as VERIFIED_PROMPT
            mode=task['input'].get('mode','single')
            identity={'checkpoint_format':1,'model':model_name,'prompt':VERIFIED_PROMPT if mode=='verified' else MULTI_PROMPT if mode=='multi' else PROMPT_SHA256,'manifest':task['input'].get('manifest_sha256')}
            saved_identity=rt.operation('execution:identity',lambda:identity)
            if saved_identity!=identity:raise ModelBlocked('模型、提示或索引版本已变化，不能自动沿用旧保存点；已有记录保留')
            raw_model=model or GenerationProvider()
            if task['input'].get('target')=='external':
                from opslens.external import AuthorizedModel
                raw_model=AuthorizedModel(raw_model,task['input'])
            model=SavedModel(raw_model,rt)
            tools=SavedTools(tools or Tools(task['input']),rt)
            multi=task['input'].get('mode') in ('multi','verified')
            if real and multi:
                rt.record('connection_check',{'message':'正在检查模型服务连接'},store.record)
                status=model.prepare_parallel()
                rt.record('connection_ready',{'message':'模型服务连接已就绪','http_status':status},store.record)
            runner=run_verified if task['input'].get('mode')=='verified' else run_collaboration if multi else run_agent
            result=runner(task['input'],model,tools,lambda k,p:rt.record(k,p,store.record),timeout=max(0,240-rt.elapsed-(time.monotonic()-started)))
            result['elapsed_ms']=round((rt.elapsed+time.monotonic()-started)*1000,2)
            metrics={k:v for k,v in result.items() if k not in ('report','collaboration','verification','references')}|{'model':model_name,'agent_count':result.get('agent_count',1)}
            rt.finish('completed',report=result['report'],reference_items=tools.refs,metrics=metrics,collaboration=result.get('collaboration',{}),verification=result.get('verification',{}))
    except LeaseLost:return
    except Exception as exc:
        status='blocked' if isinstance(exc,ModelBlocked) else 'failed'
        error=redact(str(exc),secrets())[:1500]
        try:
            with rt.transaction(deadline=False) as c:
                if task['input'].get('mode')=='verified':
                    audit=c.execute('SELECT verification FROM investigations WHERE id=%s',(task['id'],)).fetchone()['verification']
                    if audit.get('status','waiting') in ('waiting','running'):
                        audit.update(status=status,stop_reason=error)
                        c.execute('UPDATE investigations SET verification=%s WHERE id=%s',(jsonb(audit),task['id']))
            rt.finish(status,error=error,reference_items=tools.refs if tools is not None else [])
        except LeaseLost:return
async def worker():
    while True:
        try:
            task=await asyncio.to_thread(claim)
            if task:await asyncio.to_thread(process,task)
            else:await asyncio.sleep(.5)
        except asyncio.CancelledError:raise
        except Exception:
            logging.getLogger(__name__).error("Investigation queue unavailable")
            await asyncio.sleep(1)
