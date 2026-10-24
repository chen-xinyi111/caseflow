"""Project-scoped state with optimistic writes and immutable business snapshots."""
import copy
import hashlib
import json
from datetime import datetime,timezone
from difflib import SequenceMatcher
from pathlib import Path
from uuid import uuid4
from opslens import db
from opslens.rag.store import jsonb
from opslens.diagnostic.intake import clean

def now():return datetime.now(timezone.utc).isoformat()
def uid():return str(uuid4())
def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()
def initialize():
    with db.connect() as c:c.execute(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))

def empty():return {'documents':[],'requirements':[],'cases':[],'changes':[],'clarifications':[],'runs':[],'exports':[],'history':[]}
def get(c,pid,lock=False):
    row=c.execute('SELECT * FROM cf_projects WHERE id=%s'+(' FOR UPDATE' if lock else ''),(pid,)).fetchone()
    if not row:raise LookupError('项目不存在')
    return row
def find(items,i):
    value=next((v for v in items if v['id']==str(i)),None)
    if value is None:raise LookupError('记录不属于当前项目')
    return value
def create(name):
    name=clean(name).strip()
    if not name or len(name)>100:raise ValueError('项目名称需要1至100字')
    with db.connect() as c:return c.execute('INSERT INTO cf_projects(id,name,state) VALUES (%s,%s,%s) RETURNING *',(uuid4(),name,jsonb(empty()))).fetchone()

def set_archived(s,archived):
    # Hiding a project never changes its documents, reviews, runs or task results.
    s['archived']=archived
    return {'archived':archived}
def update(pid,revision,action,fn):
    with db.connect() as c:
        p=get(c,pid,True)
        if p['revision']!=revision:raise FileExistsError('资料已被更新，请刷新后再操作；你的输入尚未覆盖已有内容')
        s=copy.deepcopy(p['state']);result=fn(s)
        c.execute('UPDATE cf_projects SET state=%s,revision=revision+1,updated_at=now() WHERE id=%s',(jsonb(s),pid))
        c.execute('INSERT INTO cf_audit(project_id,action,payload) VALUES (%s,%s,%s)',(pid,action,jsonb({'revision':revision+1,'result':result})))
        return get(c,pid)

def add_document(s,name,pages,role='requirement',previous_id=None,warnings=None,*,identity=None,canonical=False):
    if len(s['documents'])>=100:raise ValueError('一个项目最多100份资料')
    if role not in ('requirement','context'):raise ValueError('资料用途无效')
    if previous_id and find(s['documents'],previous_id)['role']!=role:raise ValueError('更新资料用途需要与原资料一致')
    pages=list(pages) if canonical else [clean(p) for p in pages]
    if not pages or not any(p.strip() for p in pages):raise ValueError('资料没有可提取正文，请提供有效文字内容')
    warnings=list(dict.fromkeys(warnings or []))
    for i,p in enumerate(pages):
        if not p.strip() and not any(w.startswith(f'第{i+1}页') for w in warnings):warnings.append(f'第{i+1}页没有可提取正文，请核对该页资料。')
    existing=next((d for d in s['documents'] if d['name']==name and d['sha256']==digest(pages) and d['role']==role and d['previous_id']==previous_id and (identity is None or all(d.get(k)==v for k,v in identity.items()))),None)
    if existing:
        existing['warnings']=list(dict.fromkeys([*existing.get('warnings',[]),*warnings]))
        return {'document_id':existing['id'],'reused':True}
    row={'id':uid(),'name':clean(name)[:200],'pages':pages,'warnings':warnings,'role':role,'previous_id':previous_id,'sha256':digest(pages),'created_at':now(),'kb_document_id':None}
    if identity:row.update(identity)
    s['documents'].append(row);return {'document_id':row['id']}

def quote_valid(s,citation,allowed=None):
    if allowed is not None and citation.get('document_id') not in allowed:raise ValueError('引用不属于本轮授权资料')
    d=find(s['documents'],citation.get('document_id'));page=citation.get('page',0);quote=citation.get('quote','')
    if not isinstance(page,int) or not 1<=page<=len(d['pages']) or len(quote.strip())<2 or quote not in d['pages'][page-1]:raise ValueError('引用必须是对应页原文中的连续文字')
    return citation

def new_requirement(s,item,doc_id):
    for citation in item.get('citations',[]):quote_valid(s,citation,[doc_id])
    if not item.get('citations'):raise ValueError('提取的需求必须附原文依据')
    number=max([int(r['code'][1:]) for r in s['requirements']]+[0])+1
    row={'id':uid(),'code':f'R{number:03}','revision':1,'text':item['text'],'origin':'explicit',
         'status':'draft','citations':item['citations'],'document_id':doc_id,'questions':item.get('questions',[]),'answers':[], 'created_at':now()}
    s['requirements'].append(row);return row

def manual_requirement(s,text,document_id,page,quote):
    text=clean(text).strip();quote=clean(quote).strip()
    if not text or len(text)>3000:raise ValueError('需求内容需要1至3000字')
    citation={'document_id':document_id,'page':page,'quote':quote}
    quote_valid(s,citation,[document_id])
    existing=next((r for r in s['requirements'] if r['status']!='excluded' and r['text']==text and citation in r['citations']),None)
    if existing:return {'requirement_id':existing['id'],'reused':True}
    r=new_requirement(s,{'text':text,'citations':[citation],'questions':[]},document_id);r['origin']='user'
    return {'requirement_id':r['id']}

def apply_extraction(s,doc_id,items):
    doc=find(s['documents'],doc_id)
    if any(r['document_id']==doc_id for r in s['requirements']) or any(x.get('document_id')==doc_id for x in s['changes']):raise FileExistsError('这份资料已整理，请查看已有结果')
    # Duplicate chunks can overlap; deduplicate exact requirements without inventing semantic equivalence.
    items=list({digest(x['text'].strip()):x for x in items}.values())
    old=[r for r in s['requirements'] if r['document_id']==doc.get('previous_id') and r['status']!='excluded'] if doc.get('previous_id') else []
    if not old:
        for item in items:new_requirement(s,item,doc_id)
        return {'requirements':len(items)}
    used=set();possible=set();remaining=[]
    for item in items:
        exact=next((r for r in old if r['text'].strip()==item['text'].strip() and r['id'] not in used),None)
        if exact:
            used.add(exact['id'])
            # A proposal still requires confirmation; update source identity without rewriting manual content.
            s['changes'].append({'id':uid(),'kind':'unchanged','document_id':doc_id,'old_ids':[exact['id']], 'old_revisions':{exact['id']:exact['revision']},'proposed':item,'status':'pending','created_at':now()})
        else:remaining.append(item)
    for item in remaining:
        ranked=sorted([(SequenceMatcher(None,item['text'],r['text']).ratio(),r) for r in old if r['id'] not in used],key=lambda p:p[0],reverse=True)
        candidates=[r['id'] for score,r in ranked[:3] if score>=.35]
        possible.update(candidates)
        # Candidate matches are suggestions, never automatic mappings. Split/merge ambiguity stays visible.
        s['changes'].append({'id':uid(),'kind':'possible_change' if candidates else 'added','document_id':doc_id,'old_ids':candidates,'old_revisions':{r['id']:r['revision'] for _,r in ranked if r['id'] in candidates},'proposed':item,'status':'pending','created_at':now()})
    for r in old:
        if r['id'] not in used and r['id'] not in possible:s['changes'].append({'id':uid(),'kind':'possibly_removed','document_id':doc_id,'old_ids':[r['id']],'old_revisions':{r['id']:r['revision']},'proposed':None,'status':'pending','created_at':now()})
    return {'changes':sum(x['document_id']==doc_id for x in s['changes'])}

def snapshot(s,kind,row):s['history'].append({'kind':kind,'id':row['id'],'revision':row['revision'],'at':now(),'value':copy.deepcopy(row)})
def invalidate(s,rid):
    for case in s['cases']:
        if rid in case['requirement_ids'] and case['status'] not in ('rejected','needs_review'):
            snapshot(s,'case',case);case['status']='needs_review'

def unresolved(s,ids):
    return [q for q in s.get('clarifications',[]) if q['status']=='open' and set(q['requirement_ids'])&set(ids)]

def add_clarification(s,question,requirement_ids,kind='ambiguity'):
    question=clean(question).strip();ids=list(dict.fromkeys(requirement_ids))
    if not question or len(question)>1000 or not ids or kind not in ('ambiguity','conflict'):raise ValueError('请填写问题并选择关联需求')
    requirements=[find(s['requirements'],rid) for rid in ids]
    if any(r['status']=='excluded' for r in requirements):raise ValueError('请选择尚未排除的需求')
    rows=s.setdefault('clarifications',[])
    old=next((q for q in rows if q['status']=='open' and q['question']==question and q['kind']==kind and set(q['requirement_ids'])==set(ids)),None)
    if old:return {'clarification_id':old['id'],'reused':True}
    row={'id':uid(),'question':question,'kind':kind,'requirement_ids':ids,'status':'open','answer':'','created_at':now()}
    rows.append(row)
    for r in requirements:
        snapshot(s,'requirement',r);r['status']='draft';invalidate(s,r['id'])
    return {'clarification_id':row['id']}

def resolve_clarification(s,cid,answer):
    row=find(s.get('clarifications',[]),cid);answer=clean(answer).strip()
    if not answer or len(answer)>3000:raise ValueError('请填写已确定的规则或处理决定')
    if row['status']=='resolved':
        if row['answer']!=answer:raise FileExistsError('该问题已处理；新的问题请重新登记')
        return {'clarification_id':cid,'reused':True}
    for rid in row['requirement_ids']:
        r=find(s['requirements'],rid);snapshot(s,'requirement',r)
        r['answers'].append({'text':answer,'at':now(),'clarification_id':cid})
        r['revision']+=1;r['origin']='user'
        if r['status']!='excluded':r['status']='draft'
        invalidate(s,rid)
    row.update(status='resolved',answer=answer,resolved_at=now())
    return {'clarification_id':cid}
def edit_requirement(s,rid,text,status,answer=''):
    r=find(s['requirements'],rid)
    if status not in ('draft','confirmed','excluded'):raise ValueError('需求状态无效')
    if status=='confirmed' and unresolved(s,[rid]):raise ValueError('请先处理这条需求关联的澄清或冲突问题')
    text=clean(text).strip();answer=clean(answer).strip()
    if not text or len(text)>3000:raise ValueError('需求内容需要1至3000字')
    snapshot(s,'requirement',r)
    if text!=r['text'] or answer:
        r['origin']='user';r['revision']+=1;invalidate(s,rid)
    if status!=r['status'] and status!='confirmed':invalidate(s,rid)
    r.update(text=text,status=status)
    if answer:r['answers'].append({'text':answer,'at':now()})
    if status=='excluded':invalidate(s,rid)
    return {'requirement_id':rid}

def decide_change(s,cid,action,target_ids):
    change=find(s['changes'],cid)
    if change['status']!='pending':raise FileExistsError('该变更已经处理')
    if action not in ('reject','add','replace','remove'):raise ValueError('变更操作无效')
    if action=='reject':change['status']='rejected';return {'change_id':cid}
    for rid in target_ids:
        r=find(s['requirements'],rid)
        if rid not in change['old_revisions'] or r['revision']!=change['old_revisions'][rid]:raise FileExistsError('关联需求已变化，请检查其他变更处理结果')
    if action in ('replace','remove') and not target_ids:raise ValueError('请选择要更新的原需求')
    if action=='replace' and len(target_ids)!=1:raise ValueError('多条需求合并请先新增需求，再逐条确认移除原需求')
    item=change['proposed']
    if action in ('add','replace') and not item:raise ValueError('此变更没有新需求内容')
    if action=='add':new_requirement(s,item,change['document_id'])
    elif action=='replace':
        r=find(s['requirements'],target_ids[0]);snapshot(s,'requirement',r)
        r.update(text=item['text'],citations=item['citations'],document_id=change['document_id'],questions=item.get('questions',[]))
        if change['kind']!='unchanged':
            r['revision']+=1;r['status']='draft';r['origin']='explicit';r['answers']=[];invalidate(s,r['id'])
    elif action=='remove':
        for rid in target_ids:
            r=find(s['requirements'],rid);snapshot(s,'requirement',r);r['revision']+=1;r['status']='excluded';invalidate(s,rid)
    change.update(status='accepted',decision=action,selected_ids=target_ids,decided_at=now());return {'change_id':cid}

def case_complete(case):
    return bool(case.get('title','').strip() and case.get('preconditions','').strip() and case.get('steps') and all(str(x).strip() for x in case['steps']) and case.get('expected','').strip())
def add_cases(s,items,allowed_requirements,allowed_documents):
    added=0
    for item in items:
        ids=item['requirement_ids']
        if not ids or not set(ids)<=set(allowed_requirements):raise ValueError('用例关联需求超出本轮范围')
        reqs=[find(s['requirements'],r) for r in ids]
        if any(r['status']!='confirmed' for r in reqs):raise ValueError('请先确认需求再生成用例')
        for citation in item.get('citations',[]):quote_valid(s,citation,allowed_documents)
        basis=item.get('basis','suggestion')
        if basis=='explicit' and not item.get('citations'):raise ValueError('明确依据用例必须包含引用')
        if not case_complete(item):raise ValueError('用例需要完整的前置条件、步骤和预期结果')
        replaces=item.get('replaces_case_id')
        if replaces:
            previous=find(s['cases'],replaces)
            if previous['status']!='needs_review' or not set(previous['requirement_ids'])<=set(ids):raise ValueError('修改建议必须关联当前待复审用例及其需求')
        if not replaces and any(c['title'].strip()==item['title'].strip() and set(c['requirement_ids'])==set(ids) and c['status']!='rejected' for c in s['cases']):continue
        number=max([int(c['code'][1:]) for c in s['cases']]+[0])+1
        s['cases'].append(item|{'id':uid(),'code':f'T{number:03}','revision':1,'requirement_revisions':{r['id']:r['revision'] for r in reqs},'status':'draft','created_at':now(),'review_note':'','replaces_case_revision':previous['revision'] if replaces else None});added+=1
    return {'cases_added':added}

def edit_case(s,cid,values):
    row=find(s['cases'],cid)
    acknowledge=values.pop('acknowledge_grounding',False)
    if row.get('applied_to'):raise ValueError('此建议已应用，请查看并编辑原用例')
    snapshot(s,'case',row)
    explicit_approval=values.get('status')=='approved'
    status=values.pop('status',row['status']);note=values.pop('review_note',row.get('review_note',''))
    if status not in ('draft','approved','rejected','needs_review'):raise ValueError('用例状态无效')
    changed=any(row.get(k)!=v for k,v in values.items())
    if changed:
        row.update(values);row['revision']+=1
        row.pop('calculation',None);row['numeric_input']=None;row.pop('grounding_ack',None)
        if status=='approved' and not explicit_approval:status='draft';note=''
    if status=='approved':
        if row.get('replaces_case_id') and not row.get('applied_to'):raise ValueError('请先将修改建议应用到原用例，再审核原用例')
        if not case_complete(row):raise ValueError('请补齐前置条件、操作步骤与预期结果')
        reqs=[find(s['requirements'],i) for i in row['requirement_ids']]
        if any(r['status']!='confirmed' for r in reqs):raise ValueError('关联需求尚未确认或已移除')
        if not note.strip():raise ValueError('审核通过前请填写核对说明，确认规则与预期结果')
        from . import grounding
        if grounding.acknowledgement_needed(s,row) and not acknowledge:
            raise ValueError('依据检查存在待确认内容或已失效；请重新检查，或逐项核对后明确确认并填写审核说明')
        if acknowledge:row['grounding_ack']={'at':now(),'note':clean(note),'fingerprint':grounding.fingerprint(s,row)}
        current_revisions={r['id']:r['revision'] for r in reqs}
        if current_revisions!=row['requirement_revisions'] and not changed:row['revision']+=1
        row['requirement_revisions']=current_revisions;row['reviewed_at']=now()
    row.update(status=status,review_note=clean(note));return {'case_id':cid}

def accept_proposal(s,cid):
    proposal=find(s['cases'],cid)
    if not proposal.get('replaces_case_id') or proposal['status']=='rejected':raise ValueError('此用例不是待处理修改建议')
    target=find(s['cases'],proposal['replaces_case_id'])
    if target['status']!='needs_review':raise FileExistsError('原用例已处理，请重新核对建议')
    if proposal.get('replaces_case_revision')!=target['revision']:raise FileExistsError('原用例已有人工修改或建议缺少原修订，请重新生成修改建议')
    for rid,rev in proposal['requirement_revisions'].items():
        r=find(s['requirements'],rid)
        if r['revision']!=rev or r['status']!='confirmed':raise FileExistsError('建议依据的需求已经更新或尚未确认')
    snapshot(s,'case',target)
    for key in ('title','preconditions','data','steps','expected','category','basis','citations','requirement_ids','requirement_revisions','grounding','numeric_input','calculation'):
        if key in proposal:target[key]=copy.deepcopy(proposal[key])
        else:target.pop(key,None)
    target.pop('grounding_ack',None)
    target['revision']+=1;target['status']='draft';target['review_note']='';proposal['status']='rejected';proposal['applied_to']=target['id']
    return {'case_id':target['id']}

def record_run(s,name,rows,request_id):
    old=next((r for r in s['runs'] if r['request_id']==request_id),None)
    if old:
        if old['digest']!=digest([name,rows]):raise FileExistsError('执行批次编号已经用于其他内容')
        return {'run_id':old['id']}
    result=[];seen=set()
    for item in rows:
        case=next((c for c in s['cases'] if c['code']==item['case_id'] or c['id']==item['case_id']),None)
        if case is None:raise ValueError('用例编号不属于当前项目')
        if case['id'] in seen:raise ValueError('同一批次不能重复填写同一用例')
        seen.add(case['id'])
        rev=item['revision']
        historical=next((h['value'] for h in reversed(s['history']) if h['kind']=='case' and h['id']==case['id'] and h['revision']==rev and h['value']['status']=='approved'),None)
        if rev!=case['revision'] and historical is None:raise ValueError('用例修订不存在')
        value=case if rev==case['revision'] and case['status']=='approved' else historical
        if value is None:raise ValueError('只能记录已审核用例修订的执行结果')
        if value['status']!='approved':raise ValueError('只能记录已审核用例修订的执行结果')
        if item['status'] not in ('通过','失败','阻塞','未执行'):raise ValueError('执行状态无效')
        result.append(item|{'case_id':case['id'],'code':case['code'],'requirement_revisions':value['requirement_revisions'],'snapshot':copy.deepcopy(value)})
    if not result:raise ValueError('请填写至少一项执行结果')
    row={'id':uid(),'request_id':request_id,'digest':digest([name,rows]),'name':clean(name),'results':result,'created_at':now()};s['runs'].append(row);return {'run_id':row['id']}

def summary(s):
    confirmed=[r for r in s['requirements'] if r['status']=='confirmed']
    linked={rid for c in s['cases'] if c['status'] not in ('rejected','needs_review') for rid in c['requirement_ids'] if c['requirement_revisions'].get(rid)==find(s['requirements'],rid)['revision']}
    return {'confirmed':len(confirmed),'linked':sum(r['id'] in linked for r in confirmed),'unlinked':[r['id'] for r in confirmed if r['id'] not in linked],
            'draft_cases':sum(c['status']=='draft' for c in s['cases']),'approved':sum(c['status']=='approved' for c in s['cases']),
            'review':sum(c['status']=='needs_review' for c in s['cases']),'pending_changes':sum(c['status']=='pending' for c in s['changes'])}

def public(p):
    s=copy.deepcopy(p['state']);s['summary']=summary(s)
    from . import grounding
    for rule in s.get('numeric_rules',[]):rule['current']=grounding.current_rule(s,rule)
    # Compute against stored state: presentation-only 'current' flags are not fingerprint inputs.
    for case in s['cases']:
        if case.get('grounding'):case['grounding']['current']=grounding.report_current(p['state'],case)
    for run in s['runs']:
        for result in run['results']:
            case=find(s['cases'],result['case_id'])
            result['current']=result['revision']==case['revision'] and case['status']=='approved' and result['requirement_revisions']==case['requirement_revisions']
    return dict(p)|{'state':s}

def task(c,i):
    t=c.execute('SELECT * FROM cf_tasks WHERE id=%s',(i,)).fetchone()
    if not t:raise LookupError('任务不存在')
    return t

def create_task(pid,revision,request_id,kind,document_ids,requirement_ids,consent,case_ids=None):
    case_ids=case_ids or []
    if kind not in ('extract','generate','index','review'):raise ValueError('任务类型无效')
    if not consent:raise ValueError('请确认本轮允许处理的资料范围')
    from opslens.diagnostic import model
    with db.connect() as c:
        p=get(c,pid,True);cfg=model.config(c)
        previous=c.execute('SELECT * FROM cf_tasks WHERE request_id=%s',(request_id,)).fetchone()
        if previous:
            expected={'revision':revision,'document_ids':document_ids,'requirement_ids':requirement_ids}
            expected['case_ids']=case_ids
            if str(previous['project_id'])!=str(pid) or previous['kind']!=kind or any(previous['input'].get(k,[] if k=='case_ids' else None)!=v for k,v in expected.items()):raise FileExistsError('请求编号已经用于其他任务')
            return previous
        if p['revision']!=revision:raise FileExistsError('资料已变化，请重新确认')
        for i in document_ids:find(p['state']['documents'],i)
        if kind=='review':
            if not 1<=len(case_ids)<=3:raise ValueError('一次检查1至3条已保存用例')
            if len(set(case_ids))!=len(case_ids):raise ValueError('同一批检查不能重复选择用例')
            selected=[find(p['state']['cases'],cid) for cid in case_ids]
            if any(row.get('applied_to') for row in selected):raise ValueError('请检查原用例，已应用建议只保留历史')
            if set(requirement_ids)!={rid for row in selected for rid in row['requirement_ids']}:raise ValueError('请完整选择被检查用例的关联需求')
        elif case_ids:raise ValueError('只有依据检查任务可指定已有用例')
        for i in requirement_ids:
            if find(p['state']['requirements'],i)['status']!='confirmed':raise ValueError('请先确认所选需求')
        if not document_ids:raise ValueError('请选择本轮允许处理的资料')
        if kind in ('generate','review'):
            for rid in requirement_ids:
                r=find(p['state']['requirements'],rid)
                if not {q['document_id'] for q in r['citations']}<=set(document_ids):raise ValueError('请选择关联需求的全部依据资料，或调整本轮需求范围')
        if kind=='extract' and any(find(p['state']['documents'],i)['role']!='requirement' for i in document_ids):raise ValueError('整理需求仅选择需求资料；参考资料用于检索')
        if kind=='generate' and not requirement_ids:raise ValueError('请选择至少一条已确认需求')
        from .agent import PROMPT_VERSION,processing_signature
        payload={'revision':revision,'document_ids':document_ids,'requirement_ids':requirement_ids,'config_id':str(cfg['id']),'scope_digest':digest([p['state'],document_ids,requirement_ids]),'consent':True,'prompt_version':PROMPT_VERSION,'processing_signature':processing_signature()}
        if case_ids:payload['case_ids']=case_ids
        old=c.execute('SELECT * FROM cf_tasks WHERE request_id=%s',(request_id,)).fetchone()
        if old:
            if str(old['project_id'])!=str(pid) or old['kind']!=kind or old['input']!=payload:raise FileExistsError('请求编号已经用于其他任务')
            return old
        if c.execute("SELECT 1 FROM cf_tasks WHERE project_id=%s AND status IN ('queued','running')",(pid,)).fetchone():raise FileExistsError('当前项目已有任务，请等待完成')
        row=c.execute('INSERT INTO cf_tasks(id,project_id,request_id,kind,input) VALUES (%s,%s,%s,%s,%s) RETURNING *',(uuid4(),pid,request_id,kind,jsonb(payload))).fetchone()
        c.execute("INSERT INTO cf_events(task_id,kind,payload) VALUES (%s,'queued',%s)",(row['id'],jsonb({'message':'任务已保存，等待处理'})))
        return row
