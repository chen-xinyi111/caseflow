import asyncio,time
from datetime import datetime,timezone
from uuid import uuid4
from opslens import db
from opslens.rag.store import jsonb
from opslens.config import secrets
from opslens.security import redact
from opslens.recovery import store
from opslens.recovery.adapter import Adapter
from opslens.recovery.policy import require_current

from opslens.durability import Runtime,LeaseLost

def effect_confirmed(plan,current):
    """A trusted snapshot and action-specific postcondition; no new mutation."""
    before=plan['before'];action=plan['action']
    if before['artifacts']!=current['artifacts']:return False
    for service,old in before['bindings'].items():
        new=current['bindings'].get(service)
        if not new:return False
        if service=='worker' and action in ('restore_worker_config','rollback_worker_version'):
            env=new.get('environment',{})
            if new['state']!='running' or env.get('LAB_REDIS_URL')!='redis://redis:6379/0' or env.get('LAB_WORKER_VERSION')!='v1' or new.get('image_id')!=old.get('image_id'):return False
        elif any(new.get(k)!=old.get(k) for k in ('id','config_sha256','image_id')):return False
    if action.startswith('start_'):return current['bindings'][action.removeprefix('start_')]['state']=='running'
    if action=='release_injected_blocker':return current['bindings']['blocker']['state']=='exited' and not current['dependencies'].get('injected_lock')
    return action in ('restore_worker_config','rollback_worker_version')

def process(plan,adapter=None):
    adapter=adapter or Adapter();i=plan['id'];rt=Runtime('recovery',plan);issued=False
    try:
        with rt.live():
            with rt.transaction() as c:
                plan=store.get(c,i)
                record=c.execute('SELECT * FROM recovery_actions WHERE plan_id=%s',(i,)).fetchone()
            approval=plan['approval']
            if not approval or approval['version']!=plan['version'] or approval['digest']!=plan['digest']:raise ValueError('没有此版本的有效批准，禁止执行')
            execution=plan['execution']
            if not record:
                if plan['expires_at']<=datetime.now(timezone.utc):raise ValueError('方案已过期，请重新生成并批准')
                current=adapter.snapshot();require_current(plan['before'],current,plan['action'])
                execution={'precheck':current,'checked_at':datetime.now(timezone.utc).isoformat(),'probe_request_id':str(uuid4())}
                with rt.transaction() as c:
                    saved=store.get(c,i,True)
                    if saved['digest']!=plan['digest'] or saved['approval']!=approval:raise ValueError('方案或批准与已保存记录不一致')
                    if c.execute('SELECT id FROM recovery_actions WHERE plan_id=%s',(i,)).fetchone():return
                    c.execute("INSERT INTO recovery_actions(id,plan_id,action,status) VALUES (%s,%s,%s,'issued')",(uuid4(),i,plan['action']))
                    c.execute('UPDATE recovery_plans SET execution=%s WHERE id=%s',(jsonb(execution),i))
                    store.event(c,i,'action_issued',{'message':'执行前检查通过，已保存唯一动作记录','action':plan['action']})
                issued=True
                adapter.mutate(plan['action'],current)
                with rt.transaction() as c:
                    c.execute("UPDATE recovery_actions SET status='returned',finished_at=now(),result=%s WHERE plan_id=%s",(jsonb({'returned':True}),i))
                    c.execute("UPDATE recovery_plans SET status='verifying' WHERE id=%s",(i,))
                    store.event(c,i,'verifying',{'message':'动作已返回，等待依赖就绪并验证真实业务'})
            else:
                issued=True
                if record['action']!=plan['action'] or record['status']=='not_sent':raise ValueError('动作记录无法支持继续验证，需要人工确认')
                if not effect_confirmed(plan,adapter.snapshot()):raise ValueError('已发出的动作结果无法确认；禁止重发，请人工确认')
                with rt.transaction() as c:
                    c.execute("UPDATE recovery_plans SET status='verifying' WHERE id=%s",(i,))
                    store.event(c,i,'action_reconciled',{'message':'已只读确认目标状态，未重发恢复命令','action_reissued':False})
            end=time.monotonic()+20;after=None
            while True:
                try:
                    after=adapter.snapshot();b,d=after['bindings'],after['dependencies']
                    if d['database'] and d['redis'] and b['worker']['state']=='running' and b['api']['state']=='running':break
                except Exception:pass
                if time.monotonic()>=end:
                    rt.finish('failed',result={'after':after,'passed':False},error='服务仍未就绪，未提交探针，不能确认恢复');return
                time.sleep(.5)
            job_id=execution.get('probe_job_id')
            if not job_id:
                if execution.get('probe_issued'):
                    probe=adapter.find_probe(execution['probe_request_id'])
                    if not probe:raise ValueError('探针已提交但无法找回，禁止重复提交；需要人工确认')
                else:
                    # A durable send intent is deliberately conservative across this crash window.
                    execution['probe_issued']=True
                    with rt.transaction() as c:
                        c.execute('UPDATE recovery_plans SET execution=%s WHERE id=%s',(jsonb(execution),i))
                        store.event(c,i,'probe_issued',{'message':'固定探针发送意图已保存，响应丢失时只读找回'})
                    probe=adapter.submit_probe(execution['probe_request_id'])
                job_id=str(probe['id']);execution['probe_job_id']=job_id
                with rt.transaction() as c:
                    c.execute('UPDATE recovery_plans SET execution=%s WHERE id=%s',(jsonb(execution),i))
                    store.event(c,i,'probe_submitted',{'message':'固定样例任务已保存，正在核对实际结果'})
            result=adapter.verify(job_id);result['after']=adapter.snapshot()
            result['scope_note']='验证固定新任务的处理能力；已有历史任务和错误结果不改写，未入队任务可能仍需人工处理。'
            rt.finish('completed' if result['passed'] else 'failed',result=result,error=None if result['passed'] else result['reason'])
    except LeaseLost:return
    except Exception as exc:
        error=redact(str(exc),secrets())[:1000]
        try:rt.finish('manual' if issued else 'blocked',error=('操作或响应结果待确认，不自动重试；请只读复核。' if issued else '执行前检查失败，未修改服务。')+error)
        except LeaseLost:return

def reconcile(i,adapter=None):
    adapter=adapter or Adapter()
    with db.connect() as c:
        plan=store.get(c,i)
        action_record=c.execute('SELECT * FROM recovery_actions WHERE plan_id=%s',(i,)).fetchone()
    value={'current':adapter.snapshot(),'action_record':action_record,'action_reissued':False,'probe':None}
    job_id=plan['execution'].get('probe_job_id')
    if job_id:
        value['probe']=adapter.read_probe(job_id)
        value['business_passed']=value['probe']['status']=='completed' and value['probe']['worker_version']=='v1' and value['probe']['result']=={'char_count':31,'word_count':5}
    elif plan['execution'].get('probe_issued'):
        value['probe']=adapter.find_probe(plan['execution']['probe_request_id'])
        value['business_passed']=bool(value['probe'] and value['probe']['status']=='completed' and value['probe']['worker_version']=='v1' and value['probe']['result']=={'char_count':31,'word_count':5})
    else:value['note']='尚无可读取的探针任务编号；状态变化不能单独证明业务恢复，需人工确认，未重新发动作或任务。'
    with db.connect() as c:store.event(c,i,'readonly_recheck',{'message':'只读检查实际状态与已有探针，未重发动作','observation':value})
    return value
async def worker():
    while True:
        try:
            plan=await asyncio.to_thread(store.claim)
            if plan:await asyncio.to_thread(process,plan)
            else:await asyncio.sleep(.5)
        except asyncio.CancelledError:raise
        except Exception:
            import logging
            logging.getLogger(__name__).error('Recovery queue unavailable');await asyncio.sleep(1)
