import json,hashlib
from uuid import uuid4,uuid5,UUID
from opslens import db
from opslens.evaluation.dataset import load_dataset,verify_freeze,digest
from opslens.evaluation.scoring import summarize
from opslens.rag.store import jsonb
from opslens.config import ROOT
from opslens.evaluation.analysis import analysis_rows
MODES={'retrieval':('keyword','vector','hybrid','rerank'),'investigation':('single','multi')}
MODEL='deepseek-v4.1-flash'
def create(request_id,index_id,case_ids=None):
    manifest=verify_freeze();data=load_dataset()
    selected=sorted(case_ids if case_ids is not None else [r['id'] for r in data['investigation']])
    known={r['id'] for r in data['investigation']}
    if not selected or len(set(selected))!=len(selected) or not set(selected)<=known:raise ValueError('调查子集编号无效')
    from opslens.investigation.engine import PROMPT_SHA256
    from opslens.investigation.collaboration import PROMPT_HASH
    with db.connect() as c:
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,3201))',(str(request_id),))
        old=c.execute('SELECT * FROM evaluation_runs WHERE request_id=%s',(request_id,)).fetchone()
        if old:
            if old['manifest']['index_id']!=str(index_id) or old['manifest']['dataset_sha256']!=manifest['dataset_sha256'] or old['manifest'].get('investigation_case_ids',sorted(known))!=selected:raise ValueError('评测请求编号内容冲突')
            return old
        index=c.execute("SELECT * FROM kb_indexes WHERE id=%s AND status='ready' AND mode='semantic'",(index_id,)).fetchone()
        if not index:raise ValueError('需要已保存的语义索引')
        documents=c.execute('SELECT source,sha256,doc_version FROM kb_documents WHERE id=ANY(%s)',([UUID(i) for i in index['document_ids']],)).fetchall()
        expected={f'opslens-ai/{name}':sha for name,sha in manifest['files'].items() if name.startswith('data/knowledge/')}
        if len(documents)!=len(expected) or {d['source']:d['sha256'] for d in documents}!=expected:raise ValueError('评测只能绑定九份内容核验的内置手册')
        # Snapshot versions separately; the mutable active pointer is never used by runs.
        manifest|={'index_id':str(index_id),'index_manifest_sha256':index['manifest_sha256'],'model':MODEL,'model_authorization':'用户于2026-10-13明确授权3.2直接使用deepseek-v4.1-flash','embedding_model':'text-embedding-v4','rerank_model':'gte-rerank-v2','prompts':{'single':PROMPT_SHA256,'multi':PROMPT_HASH},'index_parameters':index['parameters'],'response_source':'real_model_only','historical_recovery_scope':'已有明确批准的历史开发处置；不计入冻结调查对照','planned_items':600,'runtime_sources':['docker.services','docker.config_summary','lab.diagnostics','docker.logs.worker','docker.logs.api']}
        manifest.update(investigation_case_ids=selected,planned_items=480+2*len(selected),investigation_sample_count=len(selected))
        i=uuid4()
        c.execute('INSERT INTO evaluation_runs(id,request_id,title,manifest) VALUES (%s,%s,%s,%s)',(i,request_id,'3.2 冻结对照评测 v1',jsonb(manifest)))
        for kind,modes in MODES.items():
            for case in data[kind]:
                if kind=='investigation' and case['id'] not in selected:continue
                for mode in modes:
                    payload={'query':case['query'],'app_version':'v1','index_id':str(index_id),'mode':mode} if kind=='retrieval' else {'question':case['question'],'target':case['target'],'index_id':str(index_id),'manifest_sha256':index['manifest_sha256'],'mode':mode}
                    c.execute('INSERT INTO evaluation_items(id,run_id,case_id,kind,mode,split,source_group,input) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',(uuid5(i,case['id']+':'+mode),i,case['id'],kind,mode,case['split'],case['group'],jsonb(payload)))
        return c.execute('SELECT * FROM evaluation_runs WHERE id=%s',(i,)).fetchone()
def rows(c,run_id):return c.execute('SELECT id,run_id,case_id,kind,mode,split,source_group,status,input,score,metrics,error,created_at,finished_at FROM evaluation_items WHERE run_id=%s ORDER BY split,case_id,mode',(run_id,)).fetchall()
def paired(items):
    groups={}
    for r in items:
        if r['kind']=='investigation':groups.setdefault(r['split'],{}).setdefault(r['case_id'],{})[r['mode']]=r
    result=[]
    for split,cases in sorted(groups.items()):
        completed=[p for p in cases.values() if all(p[m]['status']=='completed' for m in ('single','multi'))]
        wins={'single_only':0,'multi_only':0,'both_correct':0,'both_incorrect':0}
        for pair in completed:
            a=bool(pair['single']['score']['rule_correct']);b=bool(pair['multi']['score']['rule_correct'])
            wins['both_correct' if a and b else 'single_only' if a else 'multi_only' if b else 'both_incorrect']+=1
        result.append({'split':split,'planned_pairs':len(cases),'completed_pairs':len(completed),'pending_pairs':len(cases)-len(completed),**wins,'note':'配对差异按规则判定；组内样本相关，不提供独立样本显著性结论'})
    return result
def report(c,i):
    run=c.execute('SELECT * FROM evaluation_runs WHERE id=%s',(i,)).fetchone()
    if not run:raise LookupError('评测运行不存在')
    items=rows(c,i);failures={}
    for r in items:
        if r['status']=='completed' and not (r['score'] or {}).get('rule_correct',(r['score'] or {}).get('hit_at_6',False)):key='规则未通过'
        elif r['status'] in ('failed','blocked'):key=r['status']+':'+(r['error'] or '未知')[:120]
        else:continue
        failures[key]=failures.get(key,0)+1
    partial=c.execute("SELECT result->'collaboration' AS collaboration FROM evaluation_items WHERE run_id=%s AND kind='investigation' AND result->'collaboration'->>'merge_status'='insufficient'",(i,)).fetchall()
    for row in partial:
        tasks=row['collaboration'].get('tasks',[])
        errors=[t.get('error') for t in tasks if t.get('status')=='failed']
        if errors:
            for error in errors:
                key='子任务失败后安全退回：'+str(error)[:100];failures[key]=failures.get(key,0)+1
        else:failures['调用预算触发后安全退回']=failures.get('调用预算触发后安全退回',0)+1
    recovery=c.execute("SELECT p.id,p.action,p.status,p.result,count(a.id) action_count FROM recovery_plans p LEFT JOIN recovery_actions a ON a.plan_id=p.id WHERE p.approval IS NOT NULL AND p.status IN ('completed','failed','manual') GROUP BY p.id ORDER BY p.created_at DESC").fetchall()
    analyzed=analysis_rows(items);summary=summarize(analyzed)
    reviews=c.execute("SELECT i.mode,CASE WHEN i.input->>'target'='unconnected' THEN 'control' ELSE i.split END AS split,r.review FROM evaluation_reviews r JOIN evaluation_items i ON i.id=r.item_id WHERE i.run_id=%s",(i,)).fetchall()
    failures['事实复核未满足根因/拒答预期']=sum(not r['review']['root_correct'] for r in reviews)
    failures['附引用未充分支持至少一项声明']=sum(not all(r['review']['finding_support']) for r in reviews)
    failures={k:v for k,v in failures.items() if v}
    for group in summary:
        selected=[r['review'] for r in reviews if r['mode']==group['mode'] and r['split']==group['split']]
        if group['kind']=='investigation':
            n=sum(r.get('quote_reviewed',0) for r in selected);supported=sum(r.get('quote_supported',0) for r in selected)
            group.update(reviewed=len(selected),review_root_correct=sum(bool(r.get('root_correct')) for r in selected),quote_reviewed=n,quote_supported=supported,review_support_rate=supported/n if n else None,semantic_review='codex_fact_reviewed' if len(selected)==group['completed'] and selected else 'pending')
    counts={split:len({r['case_id'] for r in analyzed if r['kind']=='investigation' and r['split']==split}) for split in ('dev','test','control')}
    run['manifest']=run['manifest']|{'input_split_audit':'未连接的两种输入跨原开发/测试机制标签重复。分析层将全部拒答案例单列共享控制，原始分组/输出不改；控制不能作为独立冻结测试。','control_unique_inputs':2,'analysis_scope':f"分析样本：开发诊断{counts['dev']}＋测试诊断{counts['test']}＋共享拒答控制{counts['control']}；共享控制实际仅2种输入，属于重复试次。"}
    return run|{'summary':summary,'paired':paired(analyzed),'failure_counts':failures,'historical_recovery':{'label':'历史开发验证，不是本次冻结评测','records':[dict(r) for r in recovery],'completed':sum(r['status']=='completed' and bool((r['result'] or {}).get('passed')) for r in recovery),'total':len(recovery)}}
def detail(c,run_id,item_id):
    row=c.execute('SELECT *,reference_items AS "references" FROM evaluation_items WHERE id=%s AND run_id=%s',(item_id,run_id)).fetchone()
    if not row:raise LookupError('评测案例不存在')
    row['steps']=c.execute('SELECT kind,payload,created_at FROM evaluation_events WHERE item_id=%s ORDER BY id',(item_id,)).fetchall()
    case=next(x for x in load_dataset()[row['kind']] if x['id']==row['case_id'])
    row['expected']={k:case.get(k) for k in ('relevant_sources','expected','root_groups','evidence_source') if k in case}
    review=c.execute('SELECT review,reviewer FROM evaluation_reviews WHERE item_id=%s',(item_id,)).fetchone()
    row['review']=review['review']|{'reviewer':review['reviewer']} if review else {}
    return row
def finalize(run_id):
    with db.connect() as c:
        counts=c.execute('SELECT status,count(*) n FROM evaluation_items WHERE run_id=%s GROUP BY status',(run_id,)).fetchall()
        complete=all(r['status'] in ('completed','failed') for r in counts)
        c.execute('UPDATE evaluation_runs SET status=%s,finished_at=CASE WHEN %s THEN now() ELSE NULL END WHERE id=%s',('completed' if complete else 'partial',complete,run_id))

def seal_execution(run_id):
    files=['src/opslens/evaluation/runner.py','src/opslens/evaluation/scoring.py','src/opslens/investigation/model.py','src/opslens/investigation/tools.py','src/opslens/rag/service.py','src/opslens/rag/provider.py']
    current={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in files}
    with db.connect() as c:
        row=c.execute('SELECT manifest FROM evaluation_runs WHERE id=%s FOR UPDATE',(run_id,)).fetchone()
        if not row:raise LookupError('评测运行不存在')
        manifest=row['manifest']
        if manifest.get('execution_files'):
            if manifest['execution_files']!=current:raise ValueError('运行冻结的实现发生变化，停止当前对照；不能自动改写历史结果')
        else:
            if c.execute('SELECT 1 FROM evaluation_calls e JOIN evaluation_items i ON i.id=e.item_id WHERE i.run_id=%s',(run_id,)).fetchone():raise ValueError('已有真实模型调用，不能事后补冻结')
            manifest.update(execution_files=current,generation_parameters={'temperature':.1,'max_tokens':1800,'enable_thinking':False,'enable_search':False},runtime_source_limit='原工具截断：diagnostics 9000字符，其余3000字符；日志取末尾',order='案例内方式顺序轮换；调查单/多顺序交替；6条开发案例试跑先于其他调查')
            c.execute('UPDATE evaluation_runs SET manifest=%s WHERE id=%s',(jsonb(manifest),run_id))
