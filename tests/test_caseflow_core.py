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
