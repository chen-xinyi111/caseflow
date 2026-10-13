"""No paid model calls. Real fault/evidence, explicitly controlled graph responses.
Run only while platform workers are stopped, through local/Demo.ps1.
"""
import argparse,json,sys,os
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from opslens import db,durability
from opslens.investigation import store as inv_store,worker as inv_worker
from opslens.investigation.contracts import Input
from opslens.recovery import store,worker
from opslens.recovery.contracts import Propose,Approve
from opslens.recovery.adapter import Adapter
from validate_durability import OfflineTools,isolation
import lab_control as lab
STATE=ROOT/'.runtime/demo-3.3.json'
class DemoModel:
    name='controlled-demo-3.3'
    def generate(self,messages,definitions):
        prompt=messages[0]['content'];context=json.loads(messages[1]['content'])
        for field in ('references','collected_evidence'):
            by_id={}
            for ref in context.get(field,[]):
                if 'content_same_as' in ref:ref['content']=by_id[ref['content_same_as']]['content']
                by_id[ref['id']]=ref
        if context.get('input',context).get('target')=='unconnected':
            missing=['请补充该实例的容器状态、近期日志、脱敏依赖地址摘要和任务状态；不要提供密钥。']
            report={'assessment':'insufficient','summary':'未连接实例，缺少运行事实，无法确认原因（可控演示响应）。','root_cause':None,'findings':[],'missing_information':missing,'next_steps':['补充资料后重新提交只读调查；此记录不能生成恢复方案。']}
            if '初始规划协调' in prompt:value={'reason':'没有目标运行接口，不能猜测故障；可控演示响应。','tasks':[]}
            elif '候选协调' in prompt:value=report
            elif '核验 Agent' in prompt:value={'decision':'uncertain','reason':'缺少当前运行事实，不能确认。','report':report,'issues':[]}
            elif '重新规划协调' in prompt:value={'action':'clarify','reason':'没有可安全补查的运行接口。','tasks':[],'questions':missing}
            else:raise AssertionError('未知演示阶段')
            return {'role':'assistant','content':json.dumps(value,ensure_ascii=False)}
        refs=context.get('references',context.get('collected_evidence',[]))
        runtime=next((r for r in refs if r['source']=='docker.services'),None)
        job=next((r for r in refs if r['source']=='lab.job_snapshot'),None)
        deps=next((r for r in refs if r['source']=='lab.diagnostics'),None)
        manual=next((r for r in refs if r['kind']=='knowledge' and '没有后台进程消费队列' in r['content']),None)
        findings=[]
        if runtime:
            states=json.loads(runtime['content'])
            assert next(r for r in states if r['service']=='worker')['state']=='exited'
            assert all(next(r for r in states if r['service']==s)['state']=='running' for s in ('api','postgres','redis'))
            findings.append({'statement':'采集时 worker 已停止，API、数据库和 Redis 仍运行。','references':[{'id':runtime['id'],'quote':'"state": "exited"'}]})
        if deps:
            observed=json.loads(deps['content']);assert observed['database']['reachable'] and observed['redis']['reachable']
            findings.append({'statement':'采集时数据库与 Redis 可达。','references':[{'id':deps['id'],'quote':'"reachable": true'}]})
        if job:
            assert json.loads(job['content'])['status']=='queued'
            findings.append({'statement':'指定样例任务在采集时仍排队。','references':[{'id':job['id'],'quote':'"status": "queued"'}]})
        if manual:
            findings.append({'statement':'手册说明 worker 停止时没有后台进程消费队列。','references':[{'id':manual['id'],'quote':'worker 容器为 exited 时，没有后台进程消费队列。'}]})
        if '初始规划协调' in prompt:
            value={'reason':'3.3闭环演示：读取真实当前状态和真实手册；响应由可控测试提供。','tasks':[{'id':'runtime','role':'runtime','objective':'检查 worker 与依赖状态','depends_on':[]},{'id':'knowledge','role':'knowledge','objective':'worker 停止后任务一直等待 没有后台进程消费队列','depends_on':[]}]}
        elif '独立调查' in prompt:
            assert findings
            value={'summary':'已核对真实原文（可控测试响应）','findings':findings,'missing_information':[]}
        else:
            assert runtime and manual
            report={'assessment':'supported','summary':'3.3闭环演示：采集时 worker 已停止；本报告使用可控测试响应，依据来自真实实例与手册。','root_cause':'worker 停止导致队列没有后台处理程序消费。','findings':findings,'missing_information':[],'next_steps':['恢复需另行审阅并批准固定方案。']}
            if '候选协调' in prompt:value=report
            elif '核验 Agent' in prompt:value={'decision':'confirmed','reason':'真实采集状态与手册机制一致；此核验响应为可控测试。','report':report,'issues':[]}
            else:raise AssertionError('未定义的自检模型阶段')
        return {'role':'assistant','content':json.dumps(value,ensure_ascii=False)}

def prepare(selfcheck=False):
    if durability.ACTIVE.get():raise RuntimeError('不能在其他任务中准备演示')
    db.initialize()
    with db.connect() as c:
        active=c.execute("SELECT count(*) n FROM investigations WHERE status IN ('queued','running')").fetchone()['n']
        recovering=c.execute("SELECT count(*) n FROM recovery_plans WHERE status IN ('approved','executing','verifying')").fetchone()['n']
        before_calls=c.execute('SELECT count(*) n FROM kb_model_calls').fetchone()['n']
        history=c.execute('SELECT id,collection_id,sample_job_id,observed_at FROM fault_cases ORDER BY id').fetchall()
    if active or recovering:raise RuntimeError('还有未完成调查或恢复任务，请完成后再准备演示；已有任务不改写。')
    baseline=isolation()
    STATE.write_text(json.dumps({'available':False,'reason':'正在重新准备演示；原调查记录仍保留。'},ensure_ascii=False),encoding='utf-8')
    lab.restore()
    lab.compose(['stop','worker'])
    sample=lab.submit();sample.raise_for_status();sample=sample.json()
    assert sample['status']=='queued'
    model=DemoModel()
    ids=[]
    for target,question,job in [('fault','3.3 闭环演示：指定任务持续排队，请结合当前运行状态和手册说明原因（可控测试响应）。',sample['id']),('unconnected','3.3 信息不足演示：未连接实例的任务没有完成，请说明还需哪些资料（可控测试响应）。',None)]:
        row=inv_store.create(Input(request_id=uuid4(),target=target,question=question,job_id=job,mode='verified'))
        task=durability.claim('investigation',row['id']);assert task
        inv_worker.process(task,model,OfflineTools(task['input']))
        with db.connect() as c:result=inv_store.get(c,row['id'])
        assert result['status']=='completed',result.get('error')
        assert result['metrics']['model']=='controlled-demo-3.3'
        if target=='fault':assert result['verification']['status']=='confirmed' and result['report']['assessment']=='supported'
        else:
            assert result['report']['assessment']=='insufficient' and result['report']['root_cause'] is None
            try:store.create(Propose(request_id=uuid4(),investigation_id=row['id'],action='start_worker'))
            except ValueError:pass
            else:raise AssertionError('信息不足不应生成恢复方案')
        ids.append(str(row['id']))
    value={'available':True,'reason':'已准备真实worker停止和未连接案例；调查与核验为可控演示响应，非真实模型效果评测。','success_id':ids[0],'insufficient_id':ids[1],'prepared_at':datetime.now(timezone.utc).isoformat(),'response_source':'controlled-demo-3.3','model_api_calls':0,'sample_job_id':sample['id'],'isolation_before':baseline}
    if selfcheck:
        plan=store.create(Propose(request_id=uuid4(),investigation_id=ids[0],action='start_worker'))
        with db.connect() as c:assert c.execute('SELECT count(*) n FROM recovery_actions WHERE plan_id=%s',(plan['id'],)).fetchone()['n']==0
        assert Adapter().snapshot()['bindings']['worker']['state']=='exited'
        approval=Approve(request_id=uuid4(),version=plan['version'],digest=plan['digest'],confirm=True)
        store.approve(plan['id'],approval,actor='development_selfcheck')
        worker.process(durability.claim('recovery',plan['id']))
        store.approve(plan['id'],approval,actor='development_selfcheck')
        assert durability.claim('recovery',plan['id']) is None
        with db.connect() as c:
            completed=store.get(c,plan['id'])
            count=c.execute('SELECT count(*) n FROM recovery_actions WHERE plan_id=%s',(plan['id'],)).fetchone()['n']
        assert completed['status']=='completed' and completed['result']['passed'] and count==1
        assert completed['result']['probe']['result']=={'char_count':31,'word_count':5}
        value.update(checked_plan_id=str(plan['id']),action_count=count,probe_result=completed['result']['probe']['result'],approval_source='development_selfcheck')
    with db.connect() as c:
        assert history==c.execute('SELECT id,collection_id,sample_job_id,observed_at FROM fault_cases ORDER BY id').fetchall()
        assert before_calls==c.execute('SELECT count(*) n FROM kb_model_calls').fetchone()['n']
    assert baseline==isolation()
    value.update(other_projects_unchanged=True,historical_case_pointers_unchanged=True)
    destination=ROOT/'.runtime/demo-selfcheck-3.3.json' if selfcheck else STATE
    if not selfcheck and (ROOT/'.runtime/demo-selfcheck-3.3.json').exists():
        value['checked_plan_id']=json.loads((ROOT/'.runtime/demo-selfcheck-3.3.json').read_text())['checked_plan_id']
    destination.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print('演示已准备；真实模型调用0次。'+str(destination.name))
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--selfcheck',action='store_true');prepare(parser.parse_args().selfcheck)
