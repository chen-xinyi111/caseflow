import json
from contextlib import nullcontext
from pathlib import Path
from uuid import uuid4,uuid5,NAMESPACE_URL
from opslens import db
from opslens.config import ROOT
from opslens.rag.store import jsonb
from opslens.external import digest
from . import intake,model
from .contracts import ModelSettings
from .risk import changes_state

def initialize():
    with db.connect() as c:
        c.execute((ROOT/'src/opslens/diagnostic/schema.sql').read_text(encoding='utf-8'))
        c.execute("INSERT INTO diagnostic_configs(id,version,settings) VALUES (%s,1,%s) ON CONFLICT DO NOTHING",(uuid5(NAMESPACE_URL,'opslens:diagnostic:default'),jsonb(ModelSettings().model_dump())))

def clean(text,secrets=None):return intake.clean(text,model.dynamic_secrets() if secrets is None else secrets)

def get_session(c,i):
    row=c.execute('SELECT * FROM diagnostic_sessions WHERE id=%s',(i,)).fetchone()
    if not row:raise LookupError('排查记录不存在')
    return row

def create_session(data):
    with db.connect() as c:
        secrets=model.dynamic_secrets(c)
        payload={k:clean(v,secrets) for k,v in data.model_dump(exclude={'request_id'}).items()}
        sha=digest(payload)
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,5001))',(str(data.request_id),))
        old=c.execute('SELECT * FROM diagnostic_sessions WHERE request_id=%s',(data.request_id,)).fetchone()
        if old:
            if old['input_sha256']!=sha:raise FileExistsError('请求编号已用于不同排查内容')
            return old
        return c.execute('INSERT INTO diagnostic_sessions(id,request_id,input_sha256,problem,project,environment) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *',
                         (uuid4(),data.request_id,sha,payload['problem'],payload['project'],payload['environment'])).fetchone()

def add_source(sid,request_id,name,kind,text,provenance=None,connection=None,secrets=None):
    if len(text.encode())>intake.MAX_FILE and kind!='official_doc':raise ValueError('文本超过5MiB')
    with (db.connect() if connection is None else nullcontext(connection)) as c:
        secrets=model.dynamic_secrets(c) if secrets is None else secrets
        content=clean(text,secrets)
        if not content.strip():raise ValueError('资料内容为空')
        name=clean(name,secrets)[:200];sha=intake.sha(content);summary=intake.summarize(content)
        metadata=provenance or {'source':'用户提供的文本','recorded_at':'未明确；不代表当前实时状态'}
        reference=metadata.get('check_reference')
        if reference:
            turn=get_turn(c,reference['turn_id'])
            if kind!='check_result' or str(turn['session_id'])!=str(sid) or turn['status']!='completed':raise ValueError('检查关联必须属于本次排查的已完成轮次')
            if not 1<=reference['check_index']<=len(turn['report']['next_checks']):raise ValueError('关联的检查编号不存在')
        get_session(c,sid);c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,5002))',(str(sid),))
        old=c.execute('SELECT * FROM diagnostic_sources WHERE request_id=%s',(request_id,)).fetchone()
        if old:
            if str(old['session_id'])!=str(sid) or old['sha256']!=sha or old['name']!=name or old['kind']!=kind:raise FileExistsError('资料请求编号已用于其他内容')
            if metadata.get('check_reference')!=old['provenance'].get('check_reference'):raise FileExistsError('资料请求编号已用于不同检查关联')
            if provenance and provenance.get('batch_digest')!=old['provenance'].get('batch_digest'):raise FileExistsError('上传请求编号已用于不同文件批次')
            return old
        version=c.execute('SELECT coalesce(max(version),0)+1 AS v FROM diagnostic_sources WHERE session_id=%s AND name=%s',(sid,name)).fetchone()['v']
        return c.execute('INSERT INTO diagnostic_sources(id,session_id,request_id,name,kind,version,content,sha256,summary,provenance) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                         (uuid4(),sid,request_id,name,kind,version,content,sha,jsonb(summary),jsonb(metadata))).fetchone()

def get_source(c,sid,i):
    row=c.execute('SELECT * FROM diagnostic_sources WHERE id=%s AND session_id=%s',(i,sid)).fetchone()
    if not row:raise LookupError('资料不属于此排查')
    if intake.sha(row['content'])!=row['sha256']:raise ValueError('资料摘要不一致，禁止沿用')
    return row

def create_preview(sid,data):
    from .worker import messages,PROMPT_VERSION
    with db.connect() as c:
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,5012))',(str(data.request_id),))
        session=get_session(c,sid);cfg=model.config(c)
        old=c.execute('SELECT * FROM diagnostic_previews WHERE request_id=%s',(data.request_id,)).fetchone()
        signature=digest({'session':str(sid),'selection':data.model_dump(mode='json',exclude={'request_id'})})
        if old:
            if old['input_sha256']!=signature:raise FileExistsError('预览请求编号已用于其他范围')
            return preview_dto(old)
        if len(set(data.source_ids))!=len(data.source_ids):raise ValueError('资料选择重复')
        sources=[get_source(c,sid,i) for i in data.source_ids]
        latest=c.execute("SELECT id,report FROM diagnostic_turns WHERE session_id=%s AND status='completed' ORDER BY created_at DESC,id DESC LIMIT 1",(sid,)).fetchone()
        pieces=intake.segments(sources,data.ranges,session['problem'])
        if not pieces:raise ValueError('没有选中可分析的文本')
        continuation={'previous_turn_id':str(latest['id']) if latest else None,
            'previous_hypothesis':latest['report']['summary'][:600] if latest else None,
            'previous_checks':latest['report']['next_checks'] if latest else [],
            'provided_results':[{'source_id':str(s['id']),'check_reference':s['provenance'].get('check_reference'),'status':'用户提供，未独立核验'} for s in sources if s['kind']=='check_result'],
            'boundary':'仅包含仍选中的检查结果原文；有结果不等于已验证根因。上一轮检查是建议，不证明已执行。'}
        context={'problem':session['problem'],'project':session['project'],'environment':session['environment'],
            'previous_hypothesis':latest['report']['summary'][:600] if latest else None,'continuation':continuation,
            'analysis_prompt_version':PROMPT_VERSION,'sources':pieces,
            'boundary':'所有资料只代表所述时间；手动日志时间可能不明确。历史结论不是当前运行事实。'}
        secrets=model.dynamic_secrets(c)
        # Historical suggestions may predate the current secret configuration.
        continuation['previous_checks']=[{k:clean(v,secrets) if isinstance(v,str) else v for k,v in step.items()} for step in continuation['previous_checks']]
        context['previous_hypothesis']=clean(context['previous_hypothesis'],secrets) if latest else None
        continuation['previous_hypothesis']=context['previous_hypothesis']
        def size():return len(json.dumps(messages(context,enforce=False),ensure_ascii=False).encode())
        intake.fit_context(context,size)
        model.guard_messages(messages(context),secrets)
        coverage={'model_input_bytes':size(),'limit_bytes':intake.MAX_CONTEXT,'correction_margin_bytes':1024,'total_source_bytes':sum(s['summary']['bytes'] for s in sources),
            'selected_text_bytes':sum(len(p['content'].encode()) for p in pieces),'sources':[
                {'id':str(s['id']),'name':s['name'],'total_lines':s['summary']['lines'],
                 'selected_ranges':[[p['start_line'],p['end_line']] for p in pieces if p['source_id']==str(s['id'])]} for s in sources]}
        hashes={str(s['id']):s['sha256'] for s in sources}
        sha=digest({'context':context,'config_id':str(cfg['id']),'source_hashes':hashes})
        stored_context=context|{'_source_hashes':hashes}
        row=c.execute('INSERT INTO diagnostic_previews(id,request_id,session_id,config_id,input_sha256,digest,context,segments,coverage) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
            (uuid5(data.request_id,'preview'),data.request_id,sid,cfg['id'],signature,sha,jsonb(stored_context),jsonb(pieces),jsonb(coverage))).fetchone()
        return preview_dto(row)


def preview_dto(row):
    return row|{'continuation':row['context'].get('continuation',{}),'analysis_prompt_version':row['context'].get('analysis_prompt_version')}


def require_current_prompt(p):
    from .worker import PROMPT_VERSION
    if p['context'].get('analysis_prompt_version')!=PROMPT_VERSION:raise ValueError('分析提示版本已变化，请重新整理预览并确认；旧记录仍可查看')

def preview(c,i,sid=None):
    row=c.execute('SELECT * FROM diagnostic_previews WHERE id=%s',(i,)).fetchone()
    if not row or(sid is not None and str(row['session_id'])!=str(sid)):raise LookupError('预览不属于此排查')
    selection=c.execute('SELECT id,sha256,content FROM diagnostic_sources WHERE session_id=%s',(row['session_id'],)).fetchall()
    # Pin the hash manifest in the preview itself instead of relying on mutable source lists.
    context=row['context']
    expected=context.get('_source_hashes')
    if expected is not None:
        for name,sha in expected.items():
            found=next((s for s in selection if str(s['id'])==name),None)
            if not found or intake.sha(found['content'])!=sha:raise ValueError('已确认资料发生变化，请重新预览')
        if digest({'context':{k:v for k,v in context.items() if k!='_source_hashes'},'config_id':str(row['config_id']),'source_hashes':expected})!=row['digest']:
            raise ValueError('预览内容摘要不一致')
        if row['segments']!=context['sources']:raise ValueError('预览引用与正文不一致')
    else:raise ValueError('预览缺少资料摘要，禁止发送')
    return preview_dto(row)

def create_turn(sid,data):
    with db.connect() as c:
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,5003))',(str(sid),));session=get_session(c,sid)
        old=c.execute('SELECT * FROM diagnostic_turns WHERE request_id=%s',(data.request_id,)).fetchone()
        if old:
            if str(old['session_id'])!=str(sid) or str(old['preview_id'])!=str(data.preview_id):raise FileExistsError('分析请求编号已用于其他内容')
            p=preview(c,data.preview_id,sid)
            if not data.confirm or p['digest']!=data.digest:raise FileExistsError('分析请求编号已用于不同确认内容')
            return old
        if session['status']!='open':raise ValueError('请先将排查状态改为仍在排查，再开始新分析')
        p=preview(c,data.preview_id,sid);require_current_prompt(p);cfg=model.config(c)
        if not data.confirm or p['digest']!=data.digest:raise ValueError('请确认当前预览资料后分析')
        if cfg['id']!=p['config_id']:raise ValueError('模型设置已变化，请重新预览和确认')
        if c.execute("SELECT id FROM diagnostic_turns WHERE session_id=%s AND status IN ('queued','running')",(sid,)).fetchone():raise FileExistsError('此排查已有分析正在进行')
        model.readiness(cfg)
        row=c.execute('INSERT INTO diagnostic_turns(id,session_id,request_id,preview_id) VALUES (%s,%s,%s,%s) RETURNING *',(uuid4(),sid,data.request_id,p['id'])).fetchone()
        c.execute("INSERT INTO diagnostic_events(turn_id,kind,payload) VALUES (%s,'queued',%s)",(row['id'],jsonb({'message':'已确认选中资料，等待分析'})))
        return row

def get_turn(c,i):
    row=c.execute('SELECT * FROM diagnostic_turns WHERE id=%s',(i,)).fetchone()
    if not row:raise LookupError('分析轮次不存在')
    return row

def feedback(sid,data):
    with db.connect() as c:
        note=clean(data.note,model.dynamic_secrets(c))
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,5003))',(str(sid),))
        get_session(c,sid)
        if c.execute("SELECT id FROM diagnostic_turns WHERE session_id=%s AND status IN ('queued','running')",(sid,)).fetchone():raise FileExistsError('分析进行中，请等待结果后保存反馈')
        old=c.execute('SELECT * FROM diagnostic_feedback WHERE request_id=%s',(data.request_id,)).fetchone()
        if old:
            if str(old['session_id'])!=str(sid) or old['status']!=data.status or old['note']!=note:raise FileExistsError('反馈编号已用于其他内容')
        else:
            c.execute('INSERT INTO diagnostic_feedback(id,request_id,session_id,status,note) VALUES (%s,%s,%s,%s,%s)',(uuid4(),data.request_id,sid,data.status,note))
            c.execute('UPDATE diagnostic_sessions SET status=%s WHERE id=%s',(data.status,sid))
        return get_session(c,sid)

def export(sid):
    with db.connect() as c:
        secrets=model.dynamic_secrets(c)
        s=get_session(c,sid)
        turns=c.execute('SELECT * FROM diagnostic_turns WHERE session_id=%s ORDER BY created_at,id',(sid,)).fetchall()
        sources=c.execute('SELECT * FROM diagnostic_sources WHERE session_id=%s ORDER BY created_at,id',(sid,)).fetchall()
        feedback=c.execute('SELECT * FROM diagnostic_feedback WHERE session_id=%s ORDER BY created_at,id',(sid,)).fetchall()
    lines=['# 排查记录',s['problem'],'项目：'+s['project'],'环境：'+s['environment'],'状态：'+{'open':'仍在排查','solved':'用户反馈已解决','paused':'用户暂停'}[s['status']]]
    for t in turns:
        lines+=['\n## 分析 '+str(t['created_at']), '状态：'+t['status']]
        if not t['report']:
            lines+=[t['error'] or '尚无结果'];continue
        r=t['report'];lines+=[r['summary'],'\n### 已观察到的事实']
        with db.connect() as c:p=preview(c,t['preview_id'])
        refs={r['id']:r for r in p['segments']}
        for fact in r['facts']:
            lines+=['- '+fact['text']]
            for q in fact['citations']:
                source=refs[q['id']];lines+=['  - '+source['name']+' '+str(source['start_line'])+'–'+str(source['end_line'])+'行：'+q['quote']]
        lines+=['\n### 候选原因（尚未验证）']+['- '+c['title']+'：'+c['reason'] for c in r['candidate_causes']]
        lines+=['\n### 下一步检查']+['- '+x['purpose']+'：'+x['operation']+'；预期：'+x['expected']+'；改变状态：'+str(changes_state(x)) for x in r['next_checks']]
        lines+=['\n### 尚缺资料']+['- '+x for x in r['missing_information']]
    lines+=['\n## 用户反馈']+['- '+str(f['created_at'])+' '+f['status']+'（用户反馈）：'+f['note'] for f in feedback]
    lines+=['\n## 原始资料（脱敏版本）']
    for source in sources:lines+=['\n### '+source['name']+' v'+str(source['version'])+' · '+source['kind'],'```text',source['content'],'```']
    return clean('\n\n'.join(lines),secrets)
