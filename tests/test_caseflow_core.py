import copy
import io
import zipfile
import pytest
from opslens.caseflow import store,files

@pytest.fixture
def state(monkeypatch):
    monkeypatch.setattr(store,'clean',lambda value:str(value))
    s=store.empty();store.add_document(s,'预约需求',['预约时长为30至120分钟。\n管理员可以查看全部预约。'])
    did=s['documents'][0]['id']
    store.apply_extraction(s,did,[{'text':'预约时长为30至120分钟。','citations':[{'document_id':did,'page':1,'quote':'预约时长为30至120分钟。'}],'questions':[]}])
    r=s['requirements'][0];store.edit_requirement(s,r['id'],r['text'],'confirmed')
    return s

def case(s,title='检查30分钟边界'):
    r=s['requirements'][0]
    return {'title':title,'requirement_ids':[r['id']],'preconditions':'用户已登录，有可预约时段','data':'30分钟','steps':['选择空闲会议室','提交30分钟预约'],'expected':'满足其他规则时允许该时长','category':'边界','basis':'explicit','citations':r['citations']}

def test_scope_and_literal_citations(state):
    q=state['requirements'][0]['citations'][0]
    store.quote_valid(state,q,[q['document_id']])
    with pytest.raises(ValueError):store.quote_valid(state,q,[])
    with pytest.raises(ValueError):store.quote_valid(state,q|{'quote':'不存在的密码规则'})
    with pytest.raises(ValueError):store.quote_valid(state,q|{'page':2})

def test_manual_edits_approval_and_historical_execution(state):
    r=state['requirements'][0];store.add_cases(state,[case(state)],[r['id']],[r['document_id']]);c=state['cases'][0]
    with pytest.raises(ValueError):store.edit_case(state,c['id'],{'status':'approved','review_note':''})
    store.edit_case(state,c['id'],{'status':'approved','review_note':'核对了时长规则'})
    store.record_run(state,'第一次',[{'case_id':c['code'],'revision':1,'status':'通过','note':'实际执行记录'}],'run-one')
    store.edit_requirement(state,r['id'],'预约时长为30至180分钟。','confirmed')
    assert c['status']=='needs_review' and c['expected']=='满足其他规则时允许该时长'
    assert state['runs'][0]['results'][0]['requirement_revisions'][r['id']]==1
    assert store.summary(state)['linked']==0
    store.edit_case(state,c['id'],{'expected':'按新规则核对允许30分钟','status':'approved','review_note':'复查新规则'})
    assert c['revision']==2
    assert store.public({'state':state})['state']['runs'][0]['results'][0]['current'] is False

def test_edit_requires_fresh_review_and_late_historical_result_is_not_current(state):
    r=state['requirements'][0];store.add_cases(state,[case(state)],[r['id']],[r['document_id']]);c=state['cases'][0]
    store.edit_case(state,c['id'],{'status':'approved','review_note':'已核对旧规则'})
    store.edit_requirement(state,r['id'],'预约时长为30至180分钟。','confirmed')
    store.record_run(state,'补录旧执行',[{'case_id':c['code'],'revision':1,'status':'未执行','note':'旧修订记录'}],'late')
    assert store.public({'state':state})['state']['runs'][0]['results'][0]['current'] is False
    store.edit_case(state,c['id'],{'status':'approved','review_note':'复查新规则'})
    store.edit_case(state,c['id'],{'expected':'变更后人工编辑的预期'})
    assert c['status']=='draft' and c['review_note']==''

def test_read_document_checks_scope_and_bounds_but_allows_short_page(state):
    from opslens.caseflow.agent import Tools
    did=state['documents'][0]['id'];tools=Tools(state,{'input':{'document_ids':[did],'requirement_ids':[]}},None)
    args={'document_id':did,'page':1,'start_line':1,'end_line':10}
    assert tools.run('read_document',args)['text']==state['documents'][0]['pages'][0]
    with pytest.raises(ValueError):tools.run('read_document',args|{'end_line':61})
    with pytest.raises(ValueError):tools.run('read_document',args|{'start_line':True})
    with pytest.raises(ValueError):tools.run('read_document',args|{'document_id':'other-project'})

def test_change_proposals_require_decision_and_preserve_case(state):
    r=state['requirements'][0];did=r['document_id'];store.add_cases(state,[case(state)],[r['id']],[did]);c=state['cases'][0]
    r['answers']=[{'text':'旧修订补充规则','at':store.now()}]
    store.add_document(state,'预约需求',['预约时长为30至180分钟。'],'requirement',did);new=state['documents'][-1]['id']
    store.apply_extraction(state,new,[{'text':'预约时长为30至180分钟。','citations':[{'document_id':new,'page':1,'quote':'预约时长为30至180分钟。'}],'questions':[]}])
    change=next(x for x in state['changes'] if x['kind']=='possible_change')
    assert not any(x['kind']=='possibly_removed' and r['id'] in x['old_ids'] for x in state['changes'])
    assert r['text']=='预约时长为30至120分钟。'
    original=copy.deepcopy(c)
    store.decide_change(state,change['id'],'replace',[r['id']])
    assert r['revision']==2 and r['status']=='draft' and c['status']=='needs_review'
    assert r['answers']==[] and any(h['kind']=='requirement' and h['value']['answers'] for h in state['history'])
    assert c['steps']==original['steps'] and c['expected']==original['expected']

def test_proposal_application_keeps_original_identity(state):
    r=state['requirements'][0];did=r['document_id'];store.add_cases(state,[case(state)],[r['id']],[did]);c=state['cases'][0]
    store.edit_requirement(state,r['id'],'预约时长为30至180分钟。','confirmed')
    proposal=case(state,'检查180分钟边界')|{'replaces_case_id':c['id'],'expected':'新需求允许180分钟'}
    store.add_cases(state,[proposal],[r['id']],[did]);new=state['cases'][-1]
    store.accept_proposal(state,new['id'])
    assert c['code']=='T001' and c['revision']==2 and c['status']=='draft'
    assert new['status']=='rejected' and state['history'][-1]['value']['title']=='检查30分钟边界'
    with pytest.raises(ValueError):store.edit_case(state,new['id'],{'status':'approved','review_note':'不应重复批准已应用建议'})

def test_docx_visible_text_and_bounded_office():
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:z.writestr('word/document.xml','<w:document xmlns:w="'+files.W+'"><w:body><w:p><w:r><w:t>预约需求</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>最多7天</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
    assert files.parse('requirement.docx',stream.getvalue())==['预约需求\n最多7天']
    with pytest.raises(ValueError):files.parse('bad.pdf',b'notpdf')
    with pytest.raises(ValueError):files.parse('bad.docx',b'notzip')

def test_spreadsheet_roundtrip_literals_and_result_validation():
    raw=files.xlsx(files.RESULT_HEADERS,[['T001',1,'通过','=literal-note']])
    assert files.result_rows('results.xlsx',raw)[0]['note']=='=literal-note'
    with zipfile.ZipFile(io.BytesIO(raw)) as z:assert b'<f>' not in z.read('xl/worksheets/sheet1.xml')
    with pytest.raises(ValueError):files.result_rows('bad.csv','用例编号,用例修订,执行结果,备注\nT001,1,成功,说明'.encode())
    with pytest.raises(ValueError):files.result_rows('empty.csv',','.join(files.RESULT_HEADERS).encode())

def test_duplicate_document_and_requirement_status_invalidation(state):
    d=state['documents'][0];store.add_document(state,d['name'],d['pages']);assert len(state['documents'])==1
    r=state['requirements'][0];store.add_cases(state,[case(state)],[r['id']],[d['id']]);c=state['cases'][0]
    store.edit_case(state,c['id'],{'status':'approved','review_note':'已审核'})
    store.edit_requirement(state,r['id'],r['text'],'draft')
    assert c['status']=='needs_review'

def test_manual_conflict_requires_resolution_and_fresh_confirmation(state):
    r=state['requirements'][0];did=r['document_id'];store.add_cases(state,[case(state)],[r['id']],[did]);c=state['cases'][0]
    store.edit_case(state,c['id'],{'status':'approved','review_note':'已核对原规则'})
    store.add_document(state,'另一份说明',['预约时长为60至90分钟。']);other=state['documents'][-1]
    b=store.new_requirement(state,{'text':other['pages'][0],'citations':[{'document_id':other['id'],'page':1,'quote':other['pages'][0]}]},other['id'])
    result=store.add_clarification(state,'两份资料的时长范围应以哪份为准？',[r['id'],b['id']],'conflict')
    assert c['status']=='needs_review' and r['status']=='draft'
    assert r['text']=='预约时长为30至120分钟。' and b['text']=='预约时长为60至90分钟。'
    assert store.add_clarification(state,'两份资料的时长范围应以哪份为准？',[b['id'],r['id']],'conflict')['reused']
    with pytest.raises(ValueError):store.edit_requirement(state,r['id'],r['text'],'confirmed')
    store.resolve_clarification(state,result['clarification_id'],'采用第一份时长范围；另一条需求排除，等待负责人核对。')
    assert r['status']=='draft' and r['origin']=='user' and r['answers'][-1]['clarification_id']==result['clarification_id']
    store.edit_requirement(state,b['id'],b['text'],'excluded');store.edit_requirement(state,r['id'],r['text'],'confirmed')
    assert not store.unresolved(state,[r['id']]) and c['status']=='needs_review'

def test_foreign_clarification_and_empty_text_do_not_mutate_assets(state):
    original=copy.deepcopy(state)
    with pytest.raises(LookupError):store.add_clarification(state,'待确认',[state['requirements'][0]['id'],'foreign'])
    assert state==original
    with pytest.raises(ValueError):store.add_document(state,'空白资料',[' \n '])
    assert state==original

def test_docx_images_are_explicitly_flagged():
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:z.writestr('word/document.xml','<w:document xmlns:w="'+files.W+'"><w:body><w:p><w:r><w:t>可提取规则</w:t><w:drawing/></w:r></w:p></w:body></w:document>')
    pages,warnings=files.inspect('mixed.docx',stream.getvalue())
    assert pages==['可提取规则'] and len(warnings)==1 and '图片' in warnings[0]


def test_reapproval_with_new_requirements_creates_revision_without_text_change(state):
    r=state['requirements'][0];store.add_cases(state,[case(state)],[r['id']],[r['document_id']]);c=state['cases'][0]
    store.edit_case(state,c['id'],{'status':'approved','review_note':'核对原规则'})
    store.record_run(state,'原规则执行',[{'case_id':c['id'],'revision':1,'status':'通过','note':'原规则结果'}],'old-run')
    store.edit_requirement(state,r['id'],'预约时长为30至180分钟。','confirmed')
    store.edit_case(state,c['id'],{'status':'approved','review_note':'30分钟仍适用，重新核对新规则'})
    assert c['revision']==2 and c['requirement_revisions']=={r['id']:2}
    assert not store.public({'state':state})['state']['runs'][0]['results'][0]['current']
    store.record_run(state,'补录原结果',[{'case_id':c['code'],'revision':1,'status':'通过','note':'旧修订补录'}],'late-run')
    assert state['runs'][-1]['results'][0]['requirement_revisions']=={r['id']:1}
    store.record_run(state,'新规则执行',[{'case_id':c['code'],'revision':2,'status':'未执行','note':''}],'new-run')
    assert store.public({'state':state})['state']['runs'][-1]['results'][0]['current']


def test_proposal_cannot_overwrite_later_manual_edits_or_missing_baseline(state):
    r=state['requirements'][0];store.add_cases(state,[case(state)],[r['id']],[r['document_id']]);c=state['cases'][0]
    store.edit_requirement(state,r['id'],'预约时长为30至180分钟。','confirmed')
    store.add_cases(state,[case(state,'检查180分钟')|{'replaces_case_id':c['id'],'replaces_case_revision':999}],[r['id']],[r['document_id']])
    proposal=state['cases'][-1];assert proposal['replaces_case_revision']==1
    store.edit_case(state,c['id'],{'expected':'人工补充，不得覆盖','status':'needs_review'})
    before=copy.deepcopy(state)
    with pytest.raises(FileExistsError):store.accept_proposal(state,proposal['id'])
    assert state==before and c['expected']=='人工补充，不得覆盖'
    proposal.pop('replaces_case_revision')
    with pytest.raises(FileExistsError):store.accept_proposal(state,proposal['id'])


def test_execution_alias_duplicates_are_rejected_atomically(state):
    r=state['requirements'][0];store.add_cases(state,[case(state)],[r['id']],[r['document_id']]);c=state['cases'][0]
    store.edit_case(state,c['id'],{'status':'approved','review_note':'核对'})
    rows=[{'case_id':value,'revision':1,'status':'通过','note':''} for value in (c['id'],c['code'])]
    with pytest.raises(ValueError):store.record_run(state,'重复编号',rows,'dup')
    assert not state['runs']


def test_change_add_remove_split_merge_and_stale_decision(state):
    r=state['requirements'][0];did=r['document_id'];store.add_cases(state,[case(state)],[r['id']],[did]);c=state['cases'][0]
    store.add_document(state,'拆分更新',['预约时长至少30分钟。\n预约时长最多180分钟。'],'requirement',did);new=state['documents'][-1]['id']
    items=[{'text':text,'citations':[{'document_id':new,'page':1,'quote':text}]} for text in state['documents'][-1]['pages'][0].splitlines()]
    store.apply_extraction(state,new,items)
    proposals=list(state['changes']);assert len(proposals)==2 and all(p['status']=='pending' for p in proposals)
    for p in proposals:store.decide_change(state,p['id'],'add',[])
    assert len(state['requirements'])==3 and r['status']=='confirmed' and c['status']=='draft'
    # User explicitly removes original after accepting both split items; no automatic split equivalence.
    store.edit_requirement(state,r['id'],r['text'],'excluded');assert c['status']=='needs_review'
    for item in state['requirements'][1:]:store.edit_requirement(state,item['id'],item['text'],'confirmed')
    store.add_document(state,'合并更新',['预约时长为30至180分钟。'],'requirement',new);merged=state['documents'][-1]['id']
    store.apply_extraction(state,merged,[{'text':'预约时长为30至180分钟。','citations':[{'document_id':merged,'page':1,'quote':'预约时长为30至180分钟。'}]}])
    proposal=state['changes'][-1];targets=proposal['old_ids'];assert len(targets)==2
    with pytest.raises(ValueError):store.decide_change(state,proposal['id'],'replace',targets)
    store.decide_change(state,proposal['id'],'add',[])
    for item in state['requirements'][1:3]:store.edit_requirement(state,item['id'],item['text'],'excluded')
    assert state['requirements'][-1]['status']=='draft'
    # Empty updated document extraction yields possible removals requiring explicit choice.
    store.add_document(state,'移除说明',['本文件不再定义预约时长。'],'requirement',merged);deleted=state['documents'][-1]['id']
    store.apply_extraction(state,deleted,[]);removal=state['changes'][-1]
    assert removal['kind']=='possibly_removed'
    target=state['requirements'][-1];store.edit_requirement(state,target['id'],target['text']+' 用户补充','draft')
    with pytest.raises(FileExistsError):store.decide_change(state,removal['id'],'remove',[target['id']])


def test_manual_missing_requirement_keeps_literal_source_and_review_gate(state):
    did=state['documents'][0]['id'];text='管理员可以查看全部预约。'
    result=store.manual_requirement(state,text,did,1,text);r=state['requirements'][-1]
    assert r['id']==result['requirement_id'] and r['origin']=='user' and r['status']=='draft'
    assert r['citations']==[{'document_id':did,'page':1,'quote':text}]
    assert store.manual_requirement(state,text,did,1,text)['reused'] and len(state['requirements'])==2
    before=copy.deepcopy(state)
    with pytest.raises(LookupError):store.manual_requirement(state,'编造','foreign',1,'不存在的规则')
    assert state==before


@pytest.mark.parametrize('fault',['joined_quote','unauthorized_document','missing_field'])
@pytest.mark.parametrize('fixed',[True,False])
def test_agent_validation_feedback_is_bounded_and_never_saves_bad_batch(state,monkeypatch,fault,fixed):
    import json
    from opslens.caseflow import agent
    from uuid import uuid4
    did=state['documents'][0]['id'];rid=state['requirements'][0]['id']
    store.add_document(state,'本轮未授权资料',['独立的参考说明。'])
    foreign=state['documents'][-1]['id'];before=copy.deepcopy(state)
    invalid=case(state,'非法引用候选')
    if fault=='joined_quote':
        invalid['citations']=[{'document_id':did,'page':1,'quote':'预约时长为30至120分钟。管理员可以查看全部预约。'}]
    elif fault=='unauthorized_document':
        invalid['citations']=[{'document_id':foreign,'page':1,'quote':'独立的参考说明。'}]
    else:invalid.pop('expected')
    events=[]
    monkeypatch.setattr(agent,'emit',lambda rt,message,kind='progress',**kw:events.append((kind,kw)))
    class Runtime:
        def operation(self,key,fn):return fn()
    class Provider:
        def __init__(self):self.keys=[]
        def generate(self,messages,tools,key):
            self.keys.append(key);assert state==before
            n=int(key.split(':')[-1])
            if n==0:
                return {'role':'assistant','content':'','tool_calls':[{'id':str(uuid4()),'function':{'name':name,'arguments':'{}'}} for name in ('read_requirements','existing_cases')],'usage':{'total_tokens':10}}
            if n==2:
                feedback=json.loads(messages[-1]['content'])
                assert feedback['verified_requirement_citations']==state['requirements'][0]['citations']
                assert all(c['document_id']!=foreign for c in feedback['verified_requirement_citations'])
            output=[case(state)] if fixed and n==2 else [case(state),invalid]
            return {'role':'assistant','content':json.dumps({'cases':output},ensure_ascii=False),'usage':{'total_tokens':10}}
    provider=Provider();task={'input':{'document_ids':[did],'requirement_ids':[rid]}}
    if fixed:
        result=agent.generate(task,state,provider,Runtime())
        assert result==[case(state)|{'replaces_case_id':None}]
    else:
        with pytest.raises(ValueError,match='生成结果未通过校验，未保存用例'):
            agent.generate(task,state,provider,Runtime())
    assert provider.keys==['generate:0','generate:1','generate:2']
    assert state==before and len([e for e in events if e[0]=='validation'])==1


def test_agent_validation_repair_cannot_exceed_total_model_call_limit(state,monkeypatch):
    import json
    from opslens.caseflow import agent
    calls=[];before=copy.deepcopy(state)
    monkeypatch.setattr(agent,'emit',lambda *a,**k:None)
    class Runtime:
        def operation(self,key,fn):return fn()
    class Provider:
        def generate(self,messages,tools,key):
            calls.append(key);n=int(key.split(':')[-1])
            if n<5:
                names=('read_requirements','existing_cases') if n==0 else ('read_requirements',)
                return {'role':'assistant','content':'','tool_calls':[{'id':key+name,'function':{'name':name,'arguments':'{}'}} for name in names],'usage':{'total_tokens':10}}
            bad=copy.deepcopy(case(state));bad['citations'][0]=bad['citations'][0]|{'quote':'不存在的原文'}
            return {'role':'assistant','content':json.dumps({'cases':[bad]}),'usage':{'total_tokens':10}}
    with pytest.raises(ValueError,match='生成结果未通过校验'):
        agent.generate({'input':{'document_ids':[state['documents'][0]['id']],'requirement_ids':[state['requirements'][0]['id']]}},state,Provider(),Runtime())
    assert len(calls)==6 and state==before
