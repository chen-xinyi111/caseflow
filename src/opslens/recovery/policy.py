import hashlib,json

TITLES={'start_worker':'启动后台处理程序 worker','start_postgres':'启动实验数据库','start_redis':'启动实验队列 Redis','start_api':'启动实验 API',
 'restore_worker_config':'恢复 worker 已保存的正常连接配置','rollback_worker_version':'将 worker 回退到正常版本 v1','release_injected_blocker':'解除专门注入的阻塞事务'}
RISK='仅修改项目二故障实验实例。后台程序恢复后会处理等待任务，并提交固定文本的新任务验证结果。历史任务与报告保留；恢复失败不会自动执行其他动作。'
def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()
def eligible(state,action):
    b=state['bindings'];d=state['dependencies'];w=b.get('worker',{});env=w.get('environment',{})
    normal=env.get('LAB_REDIS_URL')=='redis://redis:6379/0' and env.get('LAB_WORKER_VERSION')=='v1'
    deps=d.get('database') and d.get('redis') and b.get('api',{}).get('state')=='running'
    if action.startswith('start_'):
        service=action.removeprefix('start_');v=b.get(service,{})
        stopped=v.get('state') in ('exited','created')
        if service=='worker':return bool(stopped and normal and deps)
        if service=='api':return bool(stopped and d.get('database') and d.get('redis'))
        if service in ('postgres','redis'):return bool(stopped)
        return False
    if action=='restore_worker_config':return bool(deps and env.get('LAB_WORKER_VERSION')=='v1' and env.get('LAB_REDIS_URL')=='redis://missing-opslens-redis.invalid:6379/0')
    if action=='rollback_worker_version':return bool(deps and env.get('LAB_REDIS_URL')=='redis://redis:6379/0' and env.get('LAB_WORKER_VERSION')=='v0-bug')
    if action=='release_injected_blocker':return bool(deps and normal and b.get('blocker',{}).get('state')=='running' and d.get('injected_lock'))
    return False
def fingerprint(state):return digest({k:state[k] for k in ('bindings','dependencies','artifacts')})
def require_current(before,current,action):
    if fingerprint(before)!=fingerprint(current):raise ValueError('当前状态、容器或已保存配置已变化；请重新生成方案并批准')
    if not eligible(current,action):raise ValueError('当前状态不适用该恢复动作；停止执行')
