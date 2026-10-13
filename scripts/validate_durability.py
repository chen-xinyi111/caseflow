"""Real process-loss self-check; controlled responses, real evidence and actions.

Run phases while the platform queue workers are paused. No model API is used.
The two interrupted phases exit abruptly with code 73 after durable progress.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from opslens import db,durability
from opslens.investigation import store as inv_store,worker as inv_worker
from opslens.investigation.contracts import Input
from opslens.investigation.tools import Tools,ToolInput
from opslens.rag.service import search
from opslens.recovery import store,worker
from opslens.recovery.adapter import Adapter
from opslens.recovery.contracts import Propose,Approve
from opslens.collector import docker

STATE=ROOT/'.runtime/durability-3.1.json'
def read():return json.loads(STATE.read_text(encoding='utf-8'))
def save(value):STATE.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
def isolation():
    value={}
    for project in ('opslens-lab-healthy','agent-commerce-stage3'):
        for cid in docker(['ps','-aq','--filter','label=com.docker.compose.project='+project]).split():
            obj=json.loads(docker(['inspect',cid]))[0]
            value[cid]={'state':obj['State']['Status'],'started_at':obj['State']['StartedAt'],'image':obj['Image']}
    return value

class OfflineTools(Tools):
    def fork(self,i,role):return OfflineTools(self.payload,i+'-',{'collect_runtime','read_job'} if role=='runtime' else {'search_knowledge'})
    def execute(self,name,args):
        if name!='search_knowledge':return super().execute(name,args)
        if self.allowed is not None and name not in self.allowed:raise ValueError('角色越权')
        args=ToolInput.parse(name,args)
        result=search({'query':args['query'],'mode':'keyword','index_id':self.payload['index_id'],'app_version':'v1'})
        hits=[h for h in result['hits'] if 'worker' in h['document']['source'].lower()]
        assert hits,'真实 worker 手册没有检索到'
        refs=[self.add({'source':h['document']['source'],'content':h['chunk']['content']},h['citation_url'],'knowledge',doc_version=h['document']['doc_version'],index_id=result['index_id'],applicable_versions=h['document']['applicable_versions']) for h in hits]
        return {'references':refs,'counts':result['counts'],'index_id':result['index_id']}

class ControlledModel:
    name='controlled-durability-selfcheck'
    def generate(self,messages,definitions):
        prompt=messages[0]['content'];context=json.loads(messages[1]['content'])
        for field in ('references','collected_evidence'):
            by_id={}
            for ref in context.get(field,[]):
                if 'content_same_as' in ref:ref['content']=by_id[ref['content_same_as']]['content']
                by_id[ref['id']]=ref
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
            value={'reason':'中断恢复自检：读取真实当前状态和真实手册；响应由可控测试提供。','tasks':[{'id':'runtime','role':'runtime','objective':'检查 worker 与依赖状态','depends_on':[]},{'id':'knowledge','role':'knowledge','objective':'worker 停止后任务一直等待 没有后台进程消费队列','depends_on':[]}]}
        elif '独立调查' in prompt:
            assert findings
            value={'summary':'已核对真实原文（可控测试响应）','findings':findings,'missing_information':[]}
        else:
            assert runtime and manual
            report={'assessment':'supported','summary':'中断恢复演示：采集时 worker 已停止；本报告使用可控测试响应，依据来自真实实例与手册。','root_cause':'worker 停止导致队列没有后台处理程序消费。','findings':findings,'missing_information':[],'next_steps':['恢复需另行审阅并批准固定方案。']}
            if '候选协调' in prompt:value=report
            elif '核验 Agent' in prompt:value={'decision':'confirmed','reason':'真实采集状态与手册机制一致；此核验响应为可控测试。','report':report,'issues':[]}
            else:raise AssertionError('未定义的自检模型阶段')
        # Audit external-generation count independently of platform metadata.
        with db.connect() as c:
            c.execute("INSERT INTO investigation_steps(investigation_id,kind,payload) VALUES (%s,'controlled_response',%s)",(read()['investigation_id'],inv_store.jsonb({'message':'可控测试响应；没有调用真实模型'})))
        return {'role':'assistant','content':json.dumps(value,ensure_ascii=False)}

def wait_expired(family,i):
    # Exercise the real 30-second lease, without altering its deadline in the DB.
    while True:
        with db.connect() as c:row=c.execute('SELECT lease_until<=now() expired FROM durable_tasks WHERE family=%s AND task_id=%s',(family,i)).fetchone()
        if row and row['expired']:return
        time.sleep(.5)

def main(phase):
    if phase=='prepare':
        db.initialize();assert Adapter().snapshot()['bindings']['worker']['state']=='exited'
        with db.connect() as c:case=c.execute("SELECT sample_job_id FROM fault_cases WHERE id='worker_stopped'").fetchone()
        assert case['sample_job_id']
        row=inv_store.create(Input(job_id=case['sample_job_id'],request_id=uuid4(),question='3.1 中断恢复演示：调查当前 worker 停止与手册机制（可控响应，真实原文）。',target='fault',mode='verified'))
        save({'investigation_id':str(row['id']),'model_api_calls':0,'response_source':'controlled-durability-selfcheck','isolation_before':isolation()})
    elif phase in ('investigation-break','investigation-resume'):
        state=read()
        if phase.endswith('resume'):wait_expired('investigation',state['investigation_id'])
        task=inv_worker.claim();assert task and str(task['id'])==state['investigation_id']
        if phase.endswith('break'):
            original=durability.Runtime.stage
            def stop(self,key,fn,snapshot,restore):
                result=original(self,key,fn,snapshot,restore)
                if result.get('decision')=='candidate':os._exit(73)
                return result
            durability.Runtime.stage=stop
        inv_worker.process(task,ControlledModel(),OfflineTools(task['input']))
        with db.connect() as c:
            report=inv_store.get(c,task['id'])
            calls=c.execute("SELECT count(*) n FROM investigation_steps WHERE investigation_id=%s AND kind='controlled_response'",(task['id'],)).fetchone()['n']
        assert report['status']=='completed' and report['verification']['status']=='confirmed',report
        assert calls==report['metrics']['model_calls']==5
        assert report['recovery']['recovery_count']==1
        state['investigation_passed']=True;state['controlled_response_count']=calls;save(state)
    elif phase=='recovery-break':
        state=read();p=store.create(Propose(request_id=uuid4(),investigation_id=state['investigation_id'],action='start_worker'))
        store.approve(p['id'],Approve(request_id=uuid4(),version=p['version'],digest=p['digest'],confirm=True),actor='development_selfcheck')
        state['plan_id']=str(p['id']);save(state)
        class Interrupt(Adapter):
            def submit_probe(self,request_id):
                result=super().submit_probe(request_id)
                snapshot=self.snapshot()
                started=json.loads(docker(['inspect',snapshot['bindings']['worker']['id']]))[0]['State']['StartedAt']
                with db.connect() as c:store.event(c,p['id'],'selfcheck_probe_sent',{'message':'真实探针已接受，马上中断进程以丢失响应','worker_started_at':started})
                os._exit(73) # real POST accepted; response and job id intentionally lost
        worker.process(store.claim(),Interrupt())
    elif phase=='recovery-resume':
        state=read();wait_expired('recovery',state['plan_id']);worker.process(store.claim())
        with db.connect() as c:
            plan=store.get(c,state['plan_id'])
            actions=c.execute('SELECT count(*) n FROM recovery_actions WHERE plan_id=%s',(state['plan_id'],)).fetchone()['n']
            sends=c.execute("SELECT payload FROM recovery_events WHERE plan_id=%s AND kind='selfcheck_probe_sent'",(state['plan_id'],)).fetchall()
        assert plan['status']=='completed' and plan['result']['passed'],plan
        assert actions==1 and plan['recovery']['recovery_count']==1
        current=Adapter().snapshot()
        started=json.loads(docker(['inspect',current['bindings']['worker']['id']]))[0]['State']['StartedAt']
        assert len(sends)==1 and sends[0]['payload']['worker_started_at']==started
        found=Adapter().find_probe(plan['execution']['probe_request_id']);assert str(found['id'])==plan['execution']['probe_job_id']
        state.update(recovery_passed=True,action_count=actions,probe_send_count=len(sends),worker_start_unchanged_after_takeover=True,probe_job_id=plan['execution']['probe_job_id'],probe_result=plan['result']['probe']['result'],isolation_after=isolation())
        assert state['isolation_before']==state['isolation_after']
        state['other_projects_unchanged']=True;save(state)
    elif phase=='verify':
        state=read()
        assert state['investigation_passed'] and state['recovery_passed'] and state['other_projects_unchanged']
        assert state['isolation_before']==isolation()
    print(phase+' passed',flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['prepare','investigation-break','investigation-resume','recovery-break','recovery-resume','verify']);main(parser.parse_args().phase)
