from datetime import datetime,timezone,timedelta
from uuid import uuid4
from opslens import db
from opslens.rag.store import jsonb
from opslens.recovery.adapter import Adapter
from opslens.recovery.policy import TITLES,RISK,digest,eligible,require_current

def event(c,i,kind,payload):c.execute('INSERT INTO recovery_events(plan_id,kind,payload) VALUES (%s,%s,%s)',(i,kind,jsonb(payload)))
def get(c,i,lock=False):
    row=c.execute('SELECT * FROM recovery_plans WHERE id=%s'+(' FOR UPDATE' if lock else ''),(i,)).fetchone()
    if not row:raise LookupError('恢复方案不存在')
    from opslens.durability import describe
    row['recovery']=describe(c,'recovery',i)
    return row
def lineage_lock(c,i):c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,2301))',(str(i),))
def create(data,adapter=None):
    adapter=adapter or Adapter()
    with db.connect() as c:
        lineage_lock(c,data.investigation_id)
        old=c.execute('SELECT * FROM recovery_plans WHERE request_id=%s',(data.request_id,)).fetchone()
        if old:
            if str(old['investigation_id'])!=str(data.investigation_id) or old['action']!=data.action:raise FileExistsError('请求编号已用于不同方案')
            return old
        inv=c.execute('SELECT * FROM investigations WHERE id=%s',(data.investigation_id,)).fetchone()
        if not inv or inv['input'].get('target')!='fault' or inv['status']!='completed' or (inv['report'] or {}).get('assessment')!='supported' or inv['verification'].get('status')!='confirmed':
            raise ValueError('只能从已核验通过的故障实验调查创建恢复方案；信息不足和其他实例不能执行')
        active=c.execute("SELECT * FROM recovery_plans WHERE investigation_id=%s AND status IN ('approved','executing','verifying')",(data.investigation_id,)).fetchone()
        if active:raise FileExistsError('该调查已有正在执行的已批准方案，不能替换')
        # Fixed global target: do not let independent investigation lineages race.
        if c.execute("SELECT id FROM recovery_plans WHERE status IN ('approved','executing','verifying') LIMIT 1").fetchone():raise FileExistsError('故障实验实例已有恢复操作，请等待结束')
        before=adapter.snapshot()
        if not eligible(before,data.action):raise ValueError('当前状态不适用此动作；请刷新可用动作')
        version=c.execute('SELECT coalesce(max(version),0)+1 v FROM recovery_plans WHERE investigation_id=%s',(data.investigation_id,)).fetchone()['v']
        expiry=datetime.now(timezone.utc)+timedelta(minutes=10)
        explanation='依据已核验调查，重新读取故障实验实例的真实状态。只执行所选的一项固定恢复动作，再提交固定样例任务验证；旧报告不改写。'
        body={'investigation_id':str(data.investigation_id),'version':version,'action':data.action,'target':'fault','title':TITLES[data.action],'risk':RISK,'explanation':explanation,'before':before,'expires_at':expiry.isoformat()}
        c.execute("UPDATE recovery_plans SET status='superseded',error='已生成新版本，请批准新方案',finished_at=now() WHERE investigation_id=%s AND status='proposed'",(data.investigation_id,))
        row=c.execute("INSERT INTO recovery_plans(id,request_id,investigation_id,version,action,title,explanation,risk,digest,before,status,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'proposed',%s) RETURNING *",
          (uuid4(),data.request_id,data.investigation_id,version,data.action,TITLES[data.action],explanation,RISK,digest(body),jsonb(before),expiry)).fetchone()
        event(c,row['id'],'proposed',{'message':'方案已生成，尚未批准，服务未修改','version':version});return row
def approve(i,data,actor='human_ui'):
    with db.connect() as c:
        c.execute('SELECT pg_advisory_xact_lock(2302)')
        row=get(c,i);lineage_lock(c,row['investigation_id']);row=get(c,i,True)
        if row['version']!=data.version or row['digest']!=data.digest:raise ValueError('批准必须对应页面中的方案版本和摘要')
        if row['approval']:
            if row['approval']['request_id']!=str(data.request_id):
                # Repeated clicks are observations, never fresh executions.
                return row
            return row
        if row['status']!='proposed':raise ValueError('此方案已失效或不能批准，请重新生成')
        if row['expires_at']<=datetime.now(timezone.utc):raise ValueError('方案超过十分钟，请重新生成再批准')
        if c.execute("SELECT id FROM recovery_plans WHERE status IN ('approved','executing','verifying') LIMIT 1").fetchone():raise FileExistsError('实例已有执行中的恢复方案')
        approval={'request_id':str(data.request_id),'version':row['version'],'digest':row['digest'],'actor':actor,'approved_at':datetime.now(timezone.utc).isoformat(),'includes_fixed_business_probe':True}
        row=c.execute("UPDATE recovery_plans SET status='approved',approval=%s WHERE id=%s RETURNING *",(jsonb(approval),i)).fetchone()
        event(c,i,'approved',{'message':'此版本已批准，准备执行前复核','actor':actor});return row
def cancel(i,data):
    with db.connect() as c:
        row=get(c,i);lineage_lock(c,row['investigation_id']);row=get(c,i,True)
        if row['version']!=data.version or row['digest']!=data.digest or row['status']!='proposed':raise ValueError('只能取消尚未批准的当前方案')
        row=c.execute("UPDATE recovery_plans SET status='cancelled',finished_at=now() WHERE id=%s RETURNING *",(i,)).fetchone();event(c,i,'cancelled',{'message':'方案已取消，未执行'});return row
def claim():
    from opslens.durability import claim as durable_claim
    return durable_claim('recovery')
def finish(i,status,result=None,error=None):
    with db.connect() as c:
        c.execute('UPDATE recovery_plans SET status=%s,result=%s,error=%s,finished_at=now() WHERE id=%s',(status,jsonb(result) if result else None,error,i))
        event(c,i,'finished',{'message':error or ('业务探针通过，服务恢复已确认' if status=='completed' else '恢复未能确认'),'status':status})
