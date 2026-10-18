import asyncio
import copy
import json
from typing import Literal
from uuid import UUID,uuid4
from fastapi import APIRouter,UploadFile,File,Form,HTTPException,Request
from fastapi.responses import Response,StreamingResponse
from pydantic import BaseModel,ConfigDict,Field
from opslens import db
from opslens.diagnostic import model
from opslens.diagnostic.contracts import ModelSettings
from . import store,files

router=APIRouter(prefix='/api/v1/caseflow',tags=['CaseFlow'])
class Strict(BaseModel):model_config=ConfigDict(extra='forbid')
class Create(Strict):name:str=Field(min_length=1,max_length=100)
class Revision(Strict):revision:int=Field(ge=1)
class Archive(Revision):archived:bool
class Text(Revision):
    name:str=Field(default='需求说明',min_length=1,max_length=200)
    content:str=Field(min_length=1,max_length=100000)
    role:Literal['requirement','context']='requirement'
    previous_id:UUID|None=None
class RequirementEdit(Revision):
    text:str=Field(min_length=1,max_length=3000)
    status:Literal['draft','confirmed','excluded']
    answer:str=Field(default='',max_length=3000)
class ManualRequirement(Revision):
    text:str=Field(min_length=1,max_length=3000)
    document_id:UUID
    page:int=Field(ge=1)
    quote:str=Field(min_length=2,max_length=500)
class CaseEdit(Revision):
    title:str|None=Field(default=None,min_length=1,max_length=200)
    preconditions:str|None=Field(default=None,max_length=1500)
    data:str|None=Field(default=None,max_length=1500)
    steps:list[str]|None=Field(default=None,max_length=15)
    expected:str|None=Field(default=None,max_length=2000)
    status:Literal['draft','approved','rejected','needs_review']|None=None
    review_note:str=Field(default='',max_length=2000)
    acknowledge_grounding:bool=False
class Batch(Revision):
    ids:list[UUID]=Field(min_length=1,max_length=100)
    status:Literal['approved','rejected']
    review_note:str=Field(min_length=1,max_length=2000)
    acknowledge_grounding:bool=False
class Change(Revision):
    action:Literal['add','replace','remove','reject']
    target_ids:list[UUID]=Field(default_factory=list,max_length=10)
class ClarificationInput(Revision):
    question:str=Field(min_length=1,max_length=1000)
    requirement_ids:list[UUID]=Field(min_length=1,max_length=20)
    kind:Literal['ambiguity','conflict']='ambiguity'
class ClarificationAnswer(Revision):answer:str=Field(min_length=1,max_length=3000)
class TaskInput(Revision):
    request_id:UUID
    kind:Literal['extract','generate','index','review']
    document_ids:list[UUID]=Field(min_length=1,max_length=100)
    requirement_ids:list[UUID]=Field(default_factory=list,max_length=20)
    consent:bool
    case_ids:list[UUID]=Field(default_factory=list,max_length=3)
class NumericRule(Revision):
    name:str=Field(min_length=1,max_length=80)
    metric:str=Field(min_length=1,max_length=50)
    unit:str=Field(default='',max_length=10)
    kind:Literal['range','discount']
    minimum:str=Field(default='',max_length=30)
    maximum:str=Field(default='',max_length=30)
    threshold:str=Field(default='',max_length=30)
    discount:str=Field(default='',max_length=30)
    requirement_ids:list[UUID]=Field(min_length=1,max_length=20)
    confirmed:bool=False
class Strategy(Revision):mode:Literal['keyword','vector','hybrid','rerank']
class RunItem(Strict):
    case_id:str;revision:int=Field(ge=1)
    status:Literal['通过','失败','阻塞','未执行'];note:str=Field(default='',max_length=2000)
class RunInput(Revision):
    request_id:UUID
    name:str=Field(min_length=1,max_length=100)
    results:list[RunItem]=Field(min_length=1,max_length=2000)

def checked(fn,*args,**kwargs):
    try:return fn(*args,**kwargs)
    except LookupError as e:raise HTTPException(404,str(e)) from None
    except FileExistsError as e:raise HTTPException(409,str(e)) from None
    except (ValueError,KeyError,TypeError,IndexError) as e:raise HTTPException(400,str(e) or '输入无效') from None

@router.get('/projects')
def projects():
    with db.connect() as c:
        return [{k:p[k] for k in ('id','name','revision','updated_at')}|{'summary':store.summary(p['state']),'archived':bool(p['state'].get('archived',False))} for p in c.execute('SELECT * FROM cf_projects ORDER BY updated_at DESC').fetchall()]
@router.post('/projects',status_code=201)
def create(data:Create):return checked(lambda:store.public(store.create(data.name)))
@router.get('/projects/{pid}')
def project(pid:UUID):
    with db.connect() as c:return checked(lambda:store.public(store.get(c,pid)))
@router.post('/projects/{pid}/archive')

def archive(pid:UUID,data:Archive):
    return checked(lambda:store.public(store.update(pid,data.revision,'archive',lambda s:store.set_archived(s,data.archived))))
@router.get('/projects/{pid}/history')
def history(pid:UUID):
    with db.connect() as c:
        checked(store.get,c,pid)
        return c.execute('SELECT action,payload,created_at FROM cf_audit WHERE project_id=%s ORDER BY id DESC LIMIT 200',(pid,)).fetchall()
@router.post('/projects/{pid}/texts')
def text(pid:UUID,data:Text):
    return checked(lambda:store.public(store.update(pid,data.revision,'document',lambda s:store.add_document(s,data.name,[data.content],data.role,str(data.previous_id) if data.previous_id else None))))
@router.post('/projects/{pid}/files')
async def upload(pid:UUID,file:UploadFile=File(...),revision:int=Form(...),role:Literal['requirement','context']=Form('requirement'),previous_id:UUID|None=Form(None)):
    raw=await file.read(5*1024*1024+1);name=(file.filename or '资料.txt').replace('\\','/').split('/')[-1]
    pages,warnings=checked(files.inspect,name,raw)
    return checked(lambda:store.public(store.update(pid,revision,'document',lambda s:store.add_document(s,name,pages,role,str(previous_id) if previous_id else None,warnings))))
@router.post('/projects/{pid}/requirements')
def manual_requirement(pid:UUID,data:ManualRequirement):
    return checked(lambda:store.public(store.update(pid,data.revision,'manual_requirement',lambda s:store.manual_requirement(s,data.text,str(data.document_id),data.page,data.quote))))
@router.post('/projects/{pid}/requirements/{rid}')
def requirement(pid:UUID,rid:UUID,data:RequirementEdit):
    return checked(lambda:store.public(store.update(pid,data.revision,'requirement_review',lambda s:store.edit_requirement(s,str(rid),data.text,data.status,data.answer))))
@router.post('/projects/{pid}/cases/{cid}')
def case(pid:UUID,cid:UUID,data:CaseEdit):
    values={k:store.clean(v) if isinstance(v,str) else [store.clean(x) for x in v] if isinstance(v,list) else v for k,v in data.model_dump(exclude_none=True).items() if k!='revision'}
    return checked(lambda:store.public(store.update(pid,data.revision,'case_review',lambda s:store.edit_case(s,str(cid),copy.deepcopy(values)))))
@router.post('/projects/{pid}/clarifications')
def clarify(pid:UUID,data:ClarificationInput):
    return checked(lambda:store.public(store.update(pid,data.revision,'clarification',lambda s:store.add_clarification(s,data.question,[str(i) for i in data.requirement_ids],data.kind))))
@router.post('/projects/{pid}/clarifications/{cid}')
def clarify_answer(pid:UUID,cid:UUID,data:ClarificationAnswer):
    return checked(lambda:store.public(store.update(pid,data.revision,'clarification_answer',lambda s:store.resolve_clarification(s,str(cid),data.answer))))
@router.post('/projects/{pid}/reviews')
def review(pid:UUID,data:Batch):
    def apply(s):
        for cid in data.ids:store.edit_case(s,str(cid),{'status':data.status,'review_note':data.review_note,'acknowledge_grounding':data.acknowledge_grounding})
        return {'count':len(data.ids),'status':data.status}
    return checked(lambda:store.public(store.update(pid,data.revision,'batch_review',apply)))
@router.post('/projects/{pid}/proposals/{cid}')
def proposal(pid:UUID,cid:UUID,data:Revision):
    return checked(lambda:store.public(store.update(pid,data.revision,'apply_proposal',lambda s:store.accept_proposal(s,str(cid)))))
@router.post('/projects/{pid}/changes/{cid}')
def change(pid:UUID,cid:UUID,data:Change):
    return checked(lambda:store.public(store.update(pid,data.revision,'change_review',lambda s:store.decide_change(s,str(cid),data.action,[str(i) for i in data.target_ids]))))
@router.post('/projects/{pid}/strategy')
def strategy(pid:UUID,data:Strategy):
    return checked(lambda:store.public(store.update(pid,data.revision,'strategy',lambda s:s.update(retrieval_mode=data.mode))))
@router.post('/projects/{pid}/tasks',status_code=202)
def submit(pid:UUID,data:TaskInput):return checked(store.create_task,pid,data.revision,data.request_id,data.kind,[str(i) for i in data.document_ids],[str(i) for i in data.requirement_ids],data.consent,[str(i) for i in data.case_ids])
@router.post('/projects/{pid}/numeric-rules')
def numeric_rule(pid:UUID,data:NumericRule):
    from . import grounding
    values=data.model_dump(exclude={'revision'});values['requirement_ids']=[str(i) for i in data.requirement_ids]
    return checked(lambda:store.public(store.update(pid,data.revision,'numeric_rule',lambda s:grounding.add_rule(s,values))))
@router.post('/projects/{pid}/numeric-rules/{rid}/disable')
def disable_numeric_rule(pid:UUID,rid:UUID,data:Revision):
    from . import grounding
    return checked(lambda:store.public(store.update(pid,data.revision,'numeric_rule_disabled',lambda s:grounding.disable_rule(s,str(rid)))))
@router.get('/projects/{pid}/tasks')
def tasks(pid:UUID):
    with db.connect() as c:
        checked(store.get,c,pid)
        return c.execute('SELECT * FROM cf_tasks WHERE project_id=%s ORDER BY created_at DESC LIMIT 50',(pid,)).fetchall()
@router.get('/tasks/{tid}')
def task(tid:UUID):
    with db.connect() as c:return checked(store.task,c,tid)
@router.post('/tasks/{tid}/resume')
def resume(tid:UUID,data:Revision):
    with db.connect() as c:
        t=checked(store.task,c,tid);p=checked(store.get,c,t['project_id'],True)
        from .agent import processing_signature
        if t['status'] not in ('failed','blocked'):raise HTTPException(409,'任务当前不能继续')
        if p['revision']!=data.revision or p['revision']!=t['input']['revision'] or str(model.config(c)['id'])!=t['input']['config_id'] or t['input'].get('processing_signature')!=processing_signature():raise HTTPException(409,'资料、模型或索引规则已变化，保存结果仅供查看，请重新确认处理范围')
        unknown=c.execute("SELECT 1 FROM durable_operations WHERE family='caseflow' AND task_id=%s AND status='issued' AND paid",(tid,)).fetchone()
        if unknown:raise HTTPException(409,'存在结果未知的外部调用，不能自动重发；已保存步骤可查看')
        if c.execute("SELECT 1 FROM cf_tasks WHERE project_id=%s AND status IN ('queued','running')",(p['id'],)).fetchone():raise HTTPException(409,'项目已有处理任务')
        c.execute("UPDATE cf_tasks SET status='queued',error=NULL,finished_at=NULL WHERE id=%s",(tid,))
        return store.task(c,tid)
@router.get('/tasks/{tid}/events')
async def events(tid:UUID,request:Request,after:int=0):
    try:
        header=int(request.headers.get('last-event-id','0'));cursor=max(after,header)
    except ValueError:raise HTTPException(400,'事件编号无效') from None
    if after<0 or header<0:raise HTTPException(400,'事件编号无效')
    with db.connect() as c:checked(store.task,c,tid)
    async def stream():
        nonlocal cursor
        while not await request.is_disconnected():
            with db.connect() as c:
                t=store.task(c,tid);rows=c.execute('SELECT * FROM cf_events WHERE task_id=%s AND id>%s ORDER BY id',(tid,cursor)).fetchall()
            for row in rows:
                cursor=row['id'];yield 'id: '+str(cursor)+'\ndata: '+json.dumps(row,ensure_ascii=False,default=str)+'\n\n'
            if t['status'] not in ('queued','running'):return
            yield ': keepalive\n\n';await asyncio.sleep(.5)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
@router.post('/projects/{pid}/runs')
def run(pid:UUID,data:RunInput):
    return checked(lambda:store.public(store.update(pid,data.revision,'execution',lambda s:store.record_run(s,data.name,[r.model_dump() for r in data.results],str(data.request_id)))))
@router.post('/projects/{pid}/runs/import')
async def import_run(pid:UUID,file:UploadFile=File(...),revision:int=Form(...),name:str=Form(...),request_id:UUID=Form(...)):
    raw=await file.read(5*1024*1024+1);rows=checked(files.result_rows,file.filename or '',raw)
    return checked(lambda:store.public(store.update(pid,revision,'execution_import',lambda s:store.record_run(s,name,rows,str(request_id)))))
@router.get('/projects/{pid}/result-template')
def template(pid:UUID):
    with db.connect() as c:p=checked(store.get,c,pid)
    rows=[[c['code'],c['revision'],'未执行',''] for c in p['state']['cases'] if c['status']=='approved']
    return Response(files.xlsx(files.RESULT_HEADERS,rows),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="caseflow-results.xlsx"'})
@router.get('/projects/{pid}/export')
def export(pid:UUID,format:Literal['xlsx','md']='xlsx'):
    with db.connect() as c:
        p=checked(store.get,c,pid,True);s=p['state'];rows=[]
        labels={'draft':'待审核','approved':'已审核','rejected':'已驳回','needs_review':'待复审'}
        for case in s['cases']:
            refs='\n'.join(store.find(s['requirements'],r)['code'] for r in case['requirement_ids'])
            citations='\n'.join(store.find(s['documents'],q['document_id'])['name']+' 第'+str(q['page'])+'页：'+q['quote'] for q in case.get('citations',[]))
            rows.append([case['code'],case['revision'],'已应用建议' if case.get('applied_to') else labels[case['status']],refs,case['title'],case['preconditions'],case.get('data',''),'\n'.join(f'{i+1}. {step}' for i,step in enumerate(case['steps'])),case['expected'],'明确依据' if case['basis']=='explicit' else '补充建议',citations,case.get('review_note','')])
        from . import grounding
        for row,case in zip(rows,s['cases']):
            report=case.get('grounding');current=grounding.report_current(s,case)
            label='尚未复核' if not report else '检查已失效' if not current else '自动复核未发现疑点' if report['status']=='checked' else '需人工核对'
            details='\n'.join(grounding.LABELS[f['field']]+'：'+f['reason'] for f in (report or {}).get('fields',[]))
            row.extend([label,details,'按已确认数值规则计算' if case.get('calculation') else ''])
        headers=['用例编号','用例修订','审核状态','关联需求','标题','前置条件','输入数据','操作步骤','预期结果','依据类型','原文依据','审核说明','依据检查','检查说明','数值计算']
        export_id=store.uid();s['exports'].append({'id':export_id,'at':store.now(),'format':format,'project_revision':p['revision'],'rows':copy.deepcopy(rows)})
        c.execute('UPDATE cf_projects SET state=%s WHERE id=%s',(store.jsonb(s),pid))
        c.execute('INSERT INTO cf_audit(project_id,action,payload) VALUES (%s,%s,%s)',(pid,'export',store.jsonb({'id':export_id,'count':len(rows),'format':format})))
    if format=='xlsx':body=files.xlsx(headers,rows);media='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    else:
        parts=['# '+p['name'],'生成的是测试设计；审核与执行状态分别记录。']
        for row in rows:
            parts.extend(['\n## '+row[0]+' '+row[4],*[f'\n**{h}**\n\n{v}' for h,v in zip(headers,row)]])
        body='\n'.join(parts).encode();media='text/markdown; charset=utf-8'
    return Response(body,media_type=media,headers={'Content-Disposition':f'attachment; filename="caseflow-cases.{format}"','X-Content-Type-Options':'nosniff'})
@router.get('/settings')
def settings():
    value=model.settings_status()
    with db.connect() as c:value['used_today']+=c.execute('SELECT count(*) n FROM cf_requests WHERE created_at>='+model.DAY).fetchone()['n']
    if value['used_today']>=value['settings']['daily_limit']:value.update(available=False,reason='今日模型请求预算已用完')
    value['reason']=value['reason'].replace('新排查模型','模型').replace('开始分析','开始处理')
    return value
@router.post('/settings')
def settings_save(data:ModelSettings):checked(model.save_settings,data);return settings()

EXAMPLES={
 'reservation':('会议室预约', '员工可预约未来7天内的会议室，预约时长为30至120分钟。\n同一会议室的预约时间不得重叠。\n预约创建者可以在开始前取消预约。\n管理员可以查看全部预约，普通员工只能查看自己的预约。\n未说明：是否按自然日计算7天，取消后的通知方式。'),
 'permission':('文件共享', '文件所有者可以授予其他用户只读或编辑权限。\n只读用户可以查看文件，不能修改内容。\n所有者撤销授权后，该用户不能继续访问该文件。\n文件名长度为1至80个字符。\n未说明：多人同时编辑冲突的处理方式。'),
 'registration':('活动报名', '活动报名仅向已登录用户开放。\n每名用户对同一活动只能报名一次。\n活动报名人数不能超过设置的名额。\n报名截止后不允许新增报名。\n未说明：退出报名是否立即释放名额。')}
@router.post('/examples/{key}',status_code=201)
def example(key:Literal['reservation','permission','registration']):
    name,text=EXAMPLES[key];p=store.create(name+' · 示例')
    def seed(s):
        store.add_document(s,name+'需求说明',[text]);did=s['documents'][0]['id'];s['example']=True
        for line in text.splitlines():
            if line.startswith('未说明'):continue
            r=store.new_requirement(s,{'text':line,'citations':[{'document_id':did,'page':1,'quote':line}],'questions':[]},did)
            r['status']='confirmed'
        r=s['requirements'][0]
        sample={
            'reservation':('预约时长下边界','已登录员工，未来7天内有可用会议室','30分钟',['选择未来7天内的空闲时段','提交30分钟预约'],'其他规则满足时，该时长符合30至120分钟限制'),
            'permission':('授予只读权限','文件所有者已登录，另一用户尚未获授权','只读权限',['所有者为另一用户授予只读权限','被授权用户打开文件并尝试修改'],'允许查看，禁止修改文件内容'),
            'registration':('未登录用户报名','用户未登录，活动处于报名期且有余量','未登录会话',['打开活动报名入口','尝试提交报名'],'不能完成报名，需要登录后操作')
        }[key]
        title,preconditions,data,steps,expected=sample
        # Curated sample authored with the fixture, never labelled as a model result.
        ids=[x['id'] for x in s['requirements'][:2]] if key=='permission' else [r['id']]
        cites=[q for x in s['requirements'] if x['id'] in ids for q in x['citations']]
        store.add_cases(s,[{'title':title,'requirement_ids':ids,'preconditions':preconditions,'data':data,'steps':steps,'expected':expected,'category':'正常','basis':'explicit','citations':cites}],ids,[did])
        s['cases'][0]['origin']='curated_example'
        for r in s['requirements']:r['status']='draft'
        return {'example':key,'note':'人工预置示例，不是真实模型结果'}
    return store.public(store.update(p['id'],p['revision'],'example',seed))
