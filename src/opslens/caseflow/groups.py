"""Durable small-leaf groups. Project writes and item completion share one transaction."""
import copy
from uuid import uuid4, uuid5, UUID
from opslens import db, durability
from opslens.diagnostic import model
from opslens.rag.provider import ModelBlocked
from . import store, points

ACTIVE = ('queued', 'running')


def signature():
    from .agent import processing_signature
    from .case_trace import VERSION
    return store.digest([points.VERSION, VERSION, processing_signature()])


def get(c, pid, gid, lock=False):
    row = c.execute('SELECT * FROM cf_task_groups WHERE project_id=%s AND id=%s'+(' FOR UPDATE' if lock else ''), (pid, gid)).fetchone()
    if not row: raise LookupError('任务组不属于当前项目')
    return row


def unknown(c, pid):
    return bool(c.execute("""SELECT 1 FROM cf_tasks t WHERE t.project_id=%s AND
      (EXISTS(SELECT 1 FROM durable_operations o WHERE o.family='caseflow' AND o.task_id=t.id AND o.paid AND o.status='issued')
       OR EXISTS(SELECT 1 FROM cf_requests r WHERE r.task_id=t.id AND r.status IN ('issued','unknown','usage_unknown'))) LIMIT 1""", (pid,)).fetchone())


def ensure_idle(c, pid, *, ignore_group=None):
    if unknown(c, pid): raise FileExistsError('项目存在外部结果或用量未知的调用，禁止创建替代请求')
    if c.execute("SELECT 1 FROM cf_tasks WHERE project_id=%s AND status IN ('queued','running')", (pid,)).fetchone():
        raise FileExistsError('项目已有处理任务')
    if c.execute("SELECT 1 FROM cf_task_groups WHERE project_id=%s AND status IN ('queued','running')"+(' AND id<>%s' if ignore_group else ''), (pid, *([ignore_group] if ignore_group else []))).fetchone():
        raise FileExistsError('项目已有活跃任务组')


def item(key, kind, payload):
    return {'key': key, 'kind': kind, 'input': payload, 'status': 'pending', 'attempts': [], 'result': None}


def create(pid, data):
    from . import preparation
    with db.connect() as c:
        p = store.get(c, pid, True)
        previous = c.execute('SELECT * FROM cf_task_groups WHERE request_id=%s', (data['request_id'],)).fetchone()
        digest = store.digest({k:v for k,v in data.items() if k!='point_bindings' or v})
        if previous:
            if str(previous['project_id']) != str(pid) or previous['input_digest'] != digest: raise FileExistsError('请求编号已用于不同任务组输入')
            return present(previous)
        if p['state'].get('archived'): raise FileExistsError('归档项目只读')
        if p['revision'] != data['revision']: raise FileExistsError('项目已变化，请刷新后重新确认')
        if data['consent'] is not True: raise ValueError('请明确允许处理本次资料')
        ensure_idle(c, pid)
        cfg = model.config(c); frozen = copy.deepcopy(data)
        if data['kind'] != 'expand_cases' and data.get('point_bindings'):raise ValueError('此任务不接受测试点展开绑定')
        if data['kind'] == 'prepare_basis':
            if not data['target'].strip() or not data['document_ids'] or data.get('basis_bundle_id'): raise ValueError('请填写目标并选择资料，不附已有准备包')
            if len(set(data['document_ids'])) != len(data['document_ids']): raise ValueError('资料选择不能重复')
            for did in data['document_ids']: store.find(p['state']['documents'], did)
            for rid in data['rule_ids']:
                r = store.find(p['state']['requirements'], rid)
                if r['document_id'] not in data['document_ids'] or r['status'] == 'excluded': raise ValueError('目标规则不在授权资料或已排除')
            frozen['source_versions'] = points.source_versions(p['state'], data['document_ids'])
            frozen['retrieval_mode'] = p['state'].get('retrieval_mode', 'keyword')
            frozen['answers'] = [{k:r.get(k) for k in ('id','answers','questions')} for r in p['state']['requirements'] if r['document_id'] in data['document_ids']]
            items = preparation.segment_items(p['state'], frozen)
        elif data['kind'] == 'extract_points':
            if not data.get('basis_bundle_id') or data['target'] or data['document_ids']: raise ValueError('提取测试点仅接受准备包及目标规则')
            bundle = store.find(p['state'].get('basis_bundles', []), data['basis_bundle_id'])
            rids = data['rule_ids'] or [b['id'] for b in bundle['requirement_bindings']]
            if not rids or not set(rids) <= {b['id'] for b in bundle['requirement_bindings']}: raise ValueError('请选择包内目标规则')
            reasons = points.bundle_reasons(p['state'], bundle, rids)
            if reasons: raise FileExistsError('；'.join(reasons))
            frozen.update(target=bundle['target'], document_ids=[v['id'] for v in bundle['source_versions']], rule_ids=rids,
                          source_versions=bundle['source_versions'], retrieval_mode=bundle['retrieval_mode'])
            items = preparation.point_items(p['state'], bundle, rids)
        else:
            from . import case_expansion
            if not data.get('point_bindings') or data['target'] or data['document_ids'] or data['rule_ids'] or data.get('basis_bundle_id'):raise ValueError('展开仅接受明确的测试点修订及指纹')
            if len({b['id'] for b in data['point_bindings']})!=len(data['point_bindings']):raise ValueError('测试点选择不能重复')
            items=case_expansion.items(p['state'],data['point_bindings'])
            frozen['target']='由人工批准的测试点展开用例'
        row = c.execute('''INSERT INTO cf_task_groups(id,project_id,request_id,kind,input,input_digest,
            expected_project_revision,config_id,processing_signature,items) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
            (uuid4(), pid, data['request_id'], data['kind'], store.jsonb(frozen), digest, p['revision'], cfg['id'], signature(), store.jsonb(items))).fetchone()
        return present(row)


def present(g):
    counts = {state: sum(i['status'] == state for i in g['items']) for state in ('pending','running','completed','failed')}
    return dict(g) | {'counts': counts, 'estimated_leaf_tasks': len(g['items']),
        'can_resume': g['status'] in ('partial','failed','blocked'),
        'warning': '完成仅表示已选范围处理结束，不保证语义找全；模型内容仍须人工评审'}


def guard(c, p, g):
    if p['state'].get('archived') or p['revision'] != g['expected_project_revision']:
        raise FileExistsError('项目已由其他操作更新，任务组停止；已有结果保留')
    if str(model.config(c)['id']) != str(g['config_id']) or signature() != g['processing_signature']:
        raise FileExistsError('模型配置或处理规则已变化，禁止沿用本组上下文')


def schedule():
    """Only queue work; the existing CaseFlow worker owns leases and external calls."""
    from . import preparation
    with db.connect() as c:
        ids = c.execute("SELECT id,project_id FROM cf_task_groups WHERE status IN ('queued','running') ORDER BY created_at,id").fetchall()
    for identity in ids:
        with db.connect() as c:
            # Project first, then group: same order as creation/commit, no reversed-lock deadlock.
            p = store.get(c, identity['project_id'], True); g = get(c, p['id'], identity['id'], True)
            if g['status'] not in ACTIVE: continue
            try: guard(c, p, g)
            except FileExistsError as exc:
                c.execute("UPDATE cf_task_groups SET status='stale',error=%s,finished_at=now() WHERE id=%s", (str(exc), g['id'])); continue
            # Reconcile a runtime lease exhausted outside our worker.
            for i in g['items']:
                if i['status'] == 'running':
                    t = c.execute('SELECT status,error FROM cf_tasks WHERE id=%s', (i['task_id'],)).fetchone()
                    if t and t['status'] not in ('queued','running'):
                        i.update(status='failed', error=t['error']); i['attempts'][-1].update(status=t['status'], error=t['error'])
            if any(i['status'] == 'running' for i in g['items']): continue
            if unknown(c, p['id']):
                c.execute("UPDATE cf_task_groups SET status='blocked',error=%s WHERE id=%s", ('外部结果或用量未知，禁止后续调用', g['id'])); continue
            if g['kind'] == 'prepare_basis' and all(i['status'] in ('completed','failed') for i in g['items']):
                kinds = {i['kind'] for i in g['items']}
                if 'prepare_retrieve' not in kinds:
                    g['items'].extend(preparation.query_items(p['state'], g))
                elif 'prepare_finalize' not in kinds:
                    g['items'].append(item('finalize', 'prepare_finalize', {}))
            pending = next((i for i in g['items'] if i['status'] == 'pending'), None)
            if not pending:
                failed = any(i['status'] == 'failed' for i in g['items'])
                status = 'partial' if failed and any(i['status'] == 'completed' for i in g['items']) else 'failed' if failed else 'completed'
                c.execute('UPDATE cf_task_groups SET status=%s,items=%s,finished_at=now() WHERE id=%s', (status, store.jsonb(g['items']), g['id'])); continue
            request = uuid5(g['id'], pending['key']+':'+str(len(pending['attempts'])))
            task_id = uuid4()
            payload = {'group_id': str(g['id']), 'item_key': pending['key'], 'config_id': str(g['config_id']),
                       'processing_signature': g['processing_signature'], 'revision': g['expected_project_revision']}
            c.execute('INSERT INTO cf_tasks(id,project_id,request_id,kind,input) VALUES (%s,%s,%s,%s,%s)',
                      (task_id, p['id'], request, pending['kind'], store.jsonb(payload)))
            pending.update(status='running', task_id=str(task_id), error=None)
            pending['attempts'].append({'task_id': str(task_id), 'status': 'queued', 'at': store.now()})
            c.execute("UPDATE cf_task_groups SET status='running',items=%s,error=NULL WHERE id=%s", (store.jsonb(g['items']), g['id']))
            durability.event(c, 'caseflow', task_id, 'queued', {'message': '任务组下一批已保存', 'group_id': str(g['id'])})


def resume(pid, gid, revision):
    with db.connect() as c:
        p = store.get(c, pid, True); g = get(c, pid, gid, True)
        if revision != p['revision']: raise FileExistsError('项目已变化，请刷新')
        guard(c, p, g); ensure_idle(c, pid, ignore_group=g['id'])
        if g['status'] not in ('partial','failed','blocked'): raise FileExistsError('此任务组不可继续')
        for i in g['items']:
            if i['status'] != 'failed': continue
            # Reuse the original leaf/checkpoints, never replace an unknown external request.
            t = store.task(c, i['task_id'])
            lease = c.execute("SELECT attempt FROM durable_tasks WHERE family='caseflow' AND task_id=%s", (t['id'],)).fetchone()
            if lease and lease['attempt'] >= 3: raise FileExistsError('本批已达到接管上限，请保留记录并重新确认新的任务范围')
            c.execute("UPDATE cf_tasks SET status='queued',error=NULL,finished_at=NULL WHERE id=%s", (t['id'],))
            i['status'] = 'running'; i['error'] = None
            i['attempts'].append({'task_id': str(t['id']), 'status': 'resumed', 'at': store.now()})
            break  # Sequential scheduling; subsequent known failures can be resumed separately.
        # A partial preparation package needs a fresh finalization, never mutate the old bundle.
        if g['kind'] == 'prepare_basis':
            for i in g['items']:
                if i['kind'] == 'prepare_finalize' and i['status'] == 'completed':
                    i['status'] = 'pending'; i['key'] = 'finalize:'+store.uid()
        c.execute("UPDATE cf_task_groups SET status='queued',items=%s,error=NULL,finished_at=NULL WHERE id=%s", (store.jsonb(g['items']), gid))
        return present(get(c, pid, gid))


def atomic_commit(rt, task, result, mutate=None):
    with rt.transaction() as c:
        p = store.get(c, task['project_id'], True); g = get(c, p['id'], task['input']['group_id'], True)
        guard(c, p, g)
        if g['status'] not in ACTIVE: raise FileExistsError('任务组已停止')
        i = next(x for x in g['items'] if x['key'] == task['input']['item_key'])
        if i['task_id'] != str(task['id']): raise FileExistsError('叶任务绑定已变化')
        if mutate:
            state = copy.deepcopy(p['state']); result = mutate(state, g, result)
            points.invalidate(state)
            c.execute('UPDATE cf_projects SET state=%s,revision=revision+1,updated_at=now() WHERE id=%s', (store.jsonb(state), p['id']))
            c.execute('INSERT INTO cf_audit(project_id,action,payload) VALUES (%s,%s,%s)', (p['id'], task['kind'], store.jsonb({'task_id': str(task['id']), 'group_id': str(g['id'])})))
            g['expected_project_revision'] += 1
        i.update(status='completed', result=result, committed_revision=g['expected_project_revision'])
        if result.get('stop_reason'):
            c.execute("UPDATE cf_task_groups SET status='blocked',error=%s WHERE id=%s",(result['stop_reason'],g['id']))
        i['attempts'][-1]['status'] = 'completed'
        c.execute('UPDATE cf_task_groups SET items=%s,expected_project_revision=%s WHERE id=%s', (store.jsonb(g['items']), g['expected_project_revision'], g['id']))
        c.execute("UPDATE cf_tasks SET status='completed',result=%s,finished_at=now() WHERE id=%s", (store.jsonb(result), task['id']))
        durability.event(c, 'caseflow', task['id'], 'finished', {'status': 'completed', 'message': '本批结果和任务标记已原子保存'})


def fail(rt, task, exc):
    with rt.transaction(deadline=False) as c:
        p = store.get(c, task['project_id'], True); g = get(c, p['id'], task['input']['group_id'], True)
        i = next(x for x in g['items'] if x['key'] == task['input']['item_key'])
        error = store.clean(str(exc))[:1000]
        stale = isinstance(exc, FileExistsError)
        blocked = isinstance(exc, ModelBlocked) or unknown(c, p['id'])
        status = 'blocked' if blocked else 'failed'
        i.update(status='failed', error=error); i['attempts'][-1].update(status=status, error=error)
        c.execute('UPDATE cf_tasks SET status=%s,error=%s,finished_at=now() WHERE id=%s', (status, error, task['id']))
        c.execute('UPDATE cf_task_groups SET status=%s,items=%s,error=%s WHERE id=%s', ('stale' if stale else 'blocked' if blocked else 'running', store.jsonb(g['items']), error, g['id']))
        durability.event(c, 'caseflow', task['id'], 'finished', {'status': status, 'message': error})
