"""Evidence boundaries, confirmed arithmetic, and stale-review protection.

All reviewer responses here are controlled fixtures, not model quality results.
"""
import copy
import json
import pytest
from opslens.caseflow import store,grounding

@pytest.fixture
def sample(monkeypatch):
    monkeypatch.setattr(store,'clean',lambda s:str(s))
    s=store.empty();text='商品总额达到100元才能使用20元券；不足100元不得抵扣，应付金额保持不变。'
    store.add_document(s,'优惠说明',[text]);d=s['documents'][0]['id']
    store.new_requirement(s,{'text':text,'citations':[{'document_id':d,'page':1,'quote':text}]},d)
    r=s['requirements'][0];store.edit_requirement(s,r['id'],r['text'],'confirmed')
    item={'title':'不足100元不得抵扣','preconditions':'存在待结算订单','data':'99.99元','steps':['尝试使用优惠券','核对应付金额'],'expected':'未抵扣，应付99.99元','requirement_ids':[r['id']],'citations':copy.deepcopy(r['citations']),'basis':'explicit','category':'边界'}
    return s,d,r,item

def rule_values(r,**more):
    return {'name':'满100减20','kind':'discount','metric':'订单商品总额','unit':'元','threshold':'100','discount':'20','requirement_ids':[r['id']],'confirmed':True}|more

def reviewer(fields=None,mutate=None):
    class Provider:
        def generate(self,messages,tools,key):
            assert key=='grounding:0' and not tools
            payload=json.loads(messages[-1]['content'])
            reports=[]
            for i,_ in enumerate(payload['cases']):
                values=copy.deepcopy(fields) if fields is not None else [
                    {'field':f,'status':'test_data' if f=='data' else 'test_action' if f=='steps' else 'supported','reason':'受控依据核对','source_indexes':[0] if f in ('preconditions','expected') else []}
                    for f in grounding.FIELDS]
                reports.append({'case_index':i,'fields':values})
            result={'reviews':reports}
            if mutate:mutate(result)
            return {'content':json.dumps(result,ensure_ascii=False),'usage':{'total_tokens':20}}
    return Provider()

@pytest.mark.parametrize('value,eligible,payable',[('99.99',False,'99.99'),('100',True,'80'),('100.01',True,'80.01')])
def test_confirmed_discount_uses_decimal_and_retains_original_ai_text(sample,value,eligible,payable):
    s,d,r,item=sample;rid=grounding.add_rule(s,rule_values(r))['rule_id']
    item['numeric_input']={'rule_id':rid,'value':value};item['expected']='AI错误要求：弹窗并支付0元'
    before=copy.deepcopy(item);out=grounding.prepare_numeric(s,item,[d])
    assert item==before and out['calculation']['result']=={'eligible':eligible,'payable':payable}
    assert '弹窗' not in out['expected'] and payable+'元' in out['expected']
    assert out['calculation']['original_expected']==before['expected']

@pytest.mark.parametrize('value,ok',[('0',False),('1',True),('60',True),('61',False)])
def test_closed_range_boundaries(sample,value,ok):
    s,d,r,item=sample
    r['text']='文件名长度为1至60个字符。';s['documents'][0]['pages']=[r['text']];r['citations'][0]['quote']=r['text']
    item['citations']=copy.deepcopy(r['citations'])
    rid=grounding.add_rule(s,rule_values(r,name='长度范围',kind='range',metric='文件名长度',unit='字符',minimum='1',maximum='60'))['rule_id']
    out=grounding.prepare_numeric(s,item|{'numeric_input':{'rule_id':rid,'value':value}},[d])
    assert out['calculation']['result']=={'within_range':ok} and '其他规则需另行检查' in out['expected']

@pytest.mark.parametrize('value',['NaN','Infinity','1e10000','0;exec(1)','1.0000001','9'*21])
def test_number_inputs_cannot_escape_fixed_decimal_interpreter(value):
    with pytest.raises(ValueError):grounding.number(value)

def test_rule_confirmation_scope_and_changed_requirement_disable_calculation(sample):
    s,d,r,item=sample
    with pytest.raises(ValueError):grounding.add_rule(s,rule_values(r,confirmed=False))
    with pytest.raises(ValueError):grounding.add_rule(s,rule_values(r,threshold='200'))
    with pytest.raises(LookupError):grounding.add_rule(s,rule_values(r,requirement_ids=['foreign']))
    rid=grounding.add_rule(s,rule_values(r))['rule_id'];item['numeric_input']={'rule_id':rid,'value':'99.99'}
    with pytest.raises(ValueError,match='有适用的已确认数值规则'):
        grounding.prepare_numeric(s,{k:v for k,v in item.items() if k!='numeric_input'},[d])
    with pytest.raises(ValueError):grounding.prepare_numeric(s,item,[])
    r['revision']+=1
    assert grounding.rules_for(s,[r['id']])==[]
    with pytest.raises(ValueError):grounding.prepare_numeric(s,item,[d])

def test_field_warning_requires_explicit_acknowledgement_and_edit_invalidates_it(sample):
    s,d,r,item=sample
    def warning(result):
        result['reviews'][0]['fields'][-1].update(status='unsupported',reason='原文未要求弹窗')
    reviewed=grounding.review_cases(s,[item],reviewer(mutate=warning),[d])[0]
    assert reviewed['grounding']['status']=='needs_confirmation'
    store.add_cases(s,[reviewed],[r['id']],[d]);case=s['cases'][0]
    with pytest.raises(ValueError,match='明确确认'):
        store.edit_case(copy.deepcopy(s),case['id'],{'status':'approved','review_note':'已看过'})
    store.edit_case(s,case['id'],{'status':'approved','review_note':'逐项核对并修正无依据提示要求','acknowledge_grounding':True})
    assert case['grounding_ack']['note'] and grounding.report_current(s,case)
    store.edit_case(s,case['id'],{'expected':'人工进一步限定预期'})
    assert case['status']=='draft' and not grounding.report_current(s,case) and 'grounding_ack' not in case

@pytest.mark.parametrize('fault',['foreign_source','empty_support','duplicate_field','data_disguise','missing_case'])
def test_reviewer_cannot_claim_support_without_valid_field_coverage(sample,fault):
    s,d,r,item=sample
    def mutate(result):
        fields=result['reviews'][0]['fields']
        if fault=='foreign_source':fields[-1]['source_indexes']=[99]
        elif fault=='empty_support':fields[-1]['source_indexes']=[]
        elif fault=='duplicate_field':fields[-1]['field']='preconditions'
        elif fault=='data_disguise':fields[-1].update(status='test_data',source_indexes=[])
        else:result['reviews'][0]['case_index']=1
    before=copy.deepcopy(s)
    with pytest.raises(ValueError):grounding.review_cases(s,[item],reviewer(mutate=mutate),[d])
    assert s==before

def test_review_saved_manual_case_keeps_content_and_finds_stale_input(sample):
    s,d,r,item=sample;store.add_cases(s,[item],[r['id']],[d]);case=s['cases'][0]
    original={f:copy.deepcopy(case[f]) for f in grounding.FIELDS}
    reviewed=grounding.review_cases(s,[case],reviewer(),[d]);grounding.save_checks(s,reviewed)
    assert {f:case[f] for f in grounding.FIELDS}==original and grounding.report_current(s,case)
    old=copy.deepcopy(reviewed);store.edit_case(s,case['id'],{'expected':'人工修改后的内容'})
    with pytest.raises(FileExistsError):grounding.save_checks(s,old)

def test_manual_edit_never_reuses_old_calculation_badge(sample):
    s,d,r,item=sample;rid=grounding.add_rule(s,rule_values(r))['rule_id']
    item=grounding.prepare_numeric(s,item|{'numeric_input':{'rule_id':rid,'value':'99.99'}},[d])
    item=grounding.review_cases(s,[item],reviewer(),[d])[0];store.add_cases(s,[item],[r['id']],[d]);case=s['cases'][0]
    store.edit_case(s,case['id'],{'data':'人工输入100.00元'})
    assert 'calculation' not in case and case['numeric_input'] is None and not grounding.report_current(s,case)

def test_new_warning_reopens_approved_case_and_preserves_execution_history(sample):
    s,d,r,item=sample;store.add_cases(s,[item],[r['id']],[d]);case=s['cases'][0]
    store.edit_case(s,case['id'],{'status':'approved','review_note':'人工核对'})
    store.record_run(s,'旧执行',[{'case_id':case['id'],'revision':1,'status':'未执行','note':''}],'run')
    def warn(result):result['reviews'][0]['fields'][-1].update(status='needs_clarification',reason='提示要求需确认')
    reviewed=grounding.review_cases(s,[case],reviewer(mutate=warn),[d]);grounding.save_checks(s,reviewed)
    assert case['status']=='needs_review' and not store.public({'state':s})['state']['runs'][0]['results'][0]['current']


def test_precondition_test_input_needs_applicable_rule_but_not_literal_input_number(sample):
    s,d,r,item=sample
    item['preconditions']='存在商品总额99.99元的订单'
    def setup(result):result['reviews'][0]['fields'][0].update(status='test_data',source_indexes=[0])
    checked=grounding.review_cases(s,[item],reviewer(mutate=setup),[d])[0]
    assert checked['grounding']['fields'][0]['status']=='test_data'
    def unscoped(result):result['reviews'][0]['fields'][0].update(status='test_data',source_indexes=[])
    with pytest.raises(ValueError,match='仍需关联适用规则'):
        grounding.review_cases(s,[item],reviewer(mutate=unscoped),[d])
