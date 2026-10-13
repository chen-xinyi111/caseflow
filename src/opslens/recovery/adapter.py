"""No user-provided commands, paths, configuration or SQL reach this adapter."""
import hashlib,json,time,os
from datetime import datetime,timezone
import httpx
from urllib.parse import urlparse
from opslens.config import ROOT,LABS
from opslens.collector import containers,docker,diagnostics
from opslens.recovery.policy import digest,eligible

TEXT='OpsLens AI makes evidence clear'
COMPOSE_SHA='768dfc17c151cf70cc3cf03cf72771351b02d685c7157b01022522c362f00740'
class Adapter:
    def artifacts(self):
        compose=ROOT/'compose/lab.yaml';envfile=ROOT/'.runtime/fault.env'
        if hashlib.sha256(compose.read_bytes()).hexdigest()!=COMPOSE_SHA:raise ValueError('已保存的恢复配置文件被修改，禁止执行')
        env=dict(line.split('=',1) for line in envfile.read_text(encoding='utf-8-sig').splitlines() if '=' in line)
        if set(env)!= {'LAB_INSTANCE','LAB_HTTP_PORT','LAB_DB_PASSWORD','LAB_DIAGNOSTIC_KEY'} or env['LAB_INSTANCE']!='fault' or env['LAB_HTTP_PORT']!='18182':raise ValueError('实验环境配置身份不符')
        return {'compose':COMPOSE_SHA,'env':hashlib.sha256(envfile.read_bytes()).hexdigest()}
    def snapshot(self):
        artifacts=self.artifacts();rows=containers('fault');bindings={}
        for cid,service,obj in rows:
            if obj['HostConfig'].get('Privileged') or obj['HostConfig'].get('NetworkMode')=='host' or obj['HostConfig'].get('PidMode')=='host':raise ValueError('容器隔离配置超出实验范围')
            if set(obj['NetworkSettings'].get('Networks',{}))!={'opslens-lab-fault_default'}:raise ValueError('容器连接了实验外网络，禁止执行')
            ports=obj['HostConfig'].get('PortBindings') or {}
            expected_ports={'8000/tcp':[{'HostIp':'127.0.0.1','HostPort':'18182'}]} if service=='api' else {}
            if ports!=expected_ports:raise ValueError('容器端口超出本机实验范围')
            allowed_mounts={'postgres':{'opslens-lab-fault_lab-data'},'redis':{'opslens-lab-fault_queue-data'}}.get(service,set())
            if any(m.get('Type')!='volume' or m.get('Name') not in allowed_mounts for m in obj.get('Mounts',[])):raise ValueError('容器挂载超出已知实验范围')
            env=dict(v.split('=',1) for v in obj['Config'].get('Env',[]) if '=' in v)
            if service in ('api','worker','blocker') and (env.get('LAB_INSTANCE')!='fault' or obj['Config']['Image']!='opslens-lab:0.1.1'):raise ValueError('应用容器不属于已知实验配置')
            if service in ('api','worker','blocker'):
                database=urlparse(env.get('LAB_DATABASE_URL',''))
                if database.hostname!='postgres' or database.port!=5432 or database.path!='/lab' or obj['Config'].get('Entrypoint'):raise ValueError('应用依赖或入口超出已保存的实验范围')
            commands={'api':['python','-m','uvicorn','lab.app:app','--host','0.0.0.0','--port','8000'],'worker':['python','-m','lab.worker'],'blocker':['python','-m','lab.blocker']}
            if service in commands and obj['Config']['Cmd']!=commands[service]:raise ValueError('应用启动命令不属于已知实验定义')
            pinned={'postgres':'postgres:16@sha256:ca0bd484cb98bf4b24eb1010e73fb3fcbd6714d240fbc1a10eea5b7dbecb641d','redis':'redis:7.4.9@sha256:a8f08480e1f88f2647fed492d1178c06abb0d0c1fbf02c682a61e2f483fb3954'}
            if service in pinned and obj['Config']['Image']!=pinned[service]:raise ValueError('依赖镜像不属于已知固定版本')
            bindings[service]={'id':cid,'state':obj['State']['Status'],'health':obj['State'].get('Health',{}).get('Status'),
             'image_id':obj['Image'],'config_sha256':digest(obj['Config']),
             'environment':{k:env[k] for k in ('LAB_INSTANCE','LAB_REDIS_URL','LAB_WORKER_VERSION') if k in env}}
        if any(s not in bindings for s in ('api','worker','postgres','redis')):raise ValueError('缺少已知实验容器，不能自动创建未知服务')
        local_image=json.loads(docker(['image','inspect','opslens-lab:0.1.1']))[0]['Id']
        if local_image!=bindings['api']['image_id']:raise ValueError('恢复镜像标签与已知实验应用不一致，禁止执行')
        artifacts['application_image_id']=local_image
        try:
            diag=diagnostics('fault')
            deps={'database':diag['database']['reachable'],'redis':diag['redis']['reachable'],
              'injected_lock':any(v['application_name']=='opslens-worker' and v['blocking_pids'] for v in diag['database'].get('activity',[]))}
        except httpx.HTTPError:
            if bindings['api']['state']=='running':raise ValueError('当前业务诊断不可达，不能生成或执行恢复方案') from None
            deps={'database':bindings['postgres']['health']=='healthy','redis':bindings['redis']['health']=='healthy','injected_lock':False}
        return {'collected_at':datetime.now(timezone.utc).isoformat(),'bindings':bindings,'dependencies':deps,'artifacts':artifacts}
    def mutate(self,action,state):
        # Recheck identity on the exact IDs immediately before the fixed command.
        current=self.snapshot()
        from opslens.recovery.policy import require_current
        require_current(state,current,action)
        if action.startswith('start_'):
            service=action.removeprefix('start_')
            return docker(['start',current['bindings'][service]['id']],timeout=45)
        if action=='release_injected_blocker':return docker(['stop','--time','5',current['bindings']['blocker']['id']],timeout=30)
        if action in ('restore_worker_config','rollback_worker_version'):
            # Only a trusted saved worker definition, no dependency creation, build or pull.
            return docker(['compose','-p','opslens-lab-fault','--env-file',str(ROOT/'.runtime/fault.env'),'-f',str(ROOT/'compose/lab.yaml'),
             'up','-d','--no-deps','--force-recreate','--no-build','--pull','never','worker'],timeout=60)
        raise ValueError('恢复动作不在白名单')
    def submit_probe(self,request_id):
        with httpx.Client(timeout=8,trust_env=False) as c:
            r=c.post(LABS['fault']['url']+'/jobs',json={'request_id':str(request_id),'text':TEXT});r.raise_for_status();return r.json()
    def read_probe(self,job_id):
        with httpx.Client(timeout=5,trust_env=False) as c:
            r=c.get(LABS['fault']['url']+'/jobs/'+str(job_id));r.raise_for_status();return r.json()
    def find_probe(self,request_id):
        with httpx.Client(timeout=5,trust_env=False) as c:
            r=c.get(LABS['fault']['url']+'/jobs/by-request/'+str(request_id),headers={'X-OpsLens-Key':os.environ[LABS['fault']['key_env']]})
            if r.status_code==404:return None
            r.raise_for_status();row=r.json()
        if row.get('request_id')!=str(request_id) or row.get('text')!=TEXT:raise ValueError('探针请求或内容与已保存意图不一致')
        return row
    def verify(self,job_id,timeout=35):
        end=time.monotonic()+timeout;row=None
        while time.monotonic()<end:
            try:
                row=self.read_probe(job_id)
                if row['status']=='completed':
                    return {'passed':row['worker_version']=='v1' and row['result']=={'char_count':31,'word_count':5},'probe':row,'reason':'实际业务结果与正常版本规则核对'}
            except httpx.HTTPError:pass
            time.sleep(.5)
        return {'passed':False,'probe':row,'reason':'业务探针未在35秒内完成，不能确认恢复'}
