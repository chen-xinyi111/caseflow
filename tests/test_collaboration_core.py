import json
import threading
import pytest
from opslens.investigation.collaboration import Plan, Budget, run_collaboration
from opslens.investigation.engine import LimitReached
from opslens.rag.provider import ModelBlocked

R={"id":"E1","kind":"runtime","source":"docker.services","content":"worker exited","url":"/api/v1/evidence/x"}
K={"id":"K1","kind":"knowledge","source":"manual","content":"停止后队列无人消费","url":"/api/v1/kb/citations/x"}

def tasks(dependent=False):
    return [{"id":"runtime","role":"runtime","objective":"核对状态","depends_on":[]},
            {"id":"manual","role":"knowledge","objective":"检索机制","depends_on":["runtime"] if dependent else []}]

def contribution(ref):
    return {"summary":"查到证据","findings":[{"statement":ref["content"],"references":[{"id":ref["id"],"quote":ref["content"]}]}],"missing_information":[]}

class FakeTools:
    def __init__(self, barrier=None):self.refs=[];self.barrier=barrier;self.order=[]
    def fork(self, task_id, role):
        parent=self
        class Child:
            def __init__(self):self.refs=[]
            def execute(self,name,args):
                assert name==("collect_runtime" if role=="runtime" else "search_knowledge")
                if parent.barrier:parent.barrier.wait(timeout=3)
                parent.order.append(task_id)
                ref=dict(R if role=="runtime" else K)
                ref["id"]+= "-"+task_id
                self.refs.append(ref)
                return {"references":[ref]}
        return Child()

class Model:
    def __init__(self, plan=None, blocked=False):self.plan=tasks() if plan is None else plan;self.blocked=blocked
    def generate(self,messages,definitions):
        prompt=messages[0]["content"]
        if "规划协调" in prompt:
            data={"reason":"结合当前事实与手册","tasks":self.plan}
        elif "合并协调" in prompt:
            context=json.loads(messages[1]["content"])
            refs=context["references"]
            data={"assessment":"supported","summary":"worker 已停止","root_cause":"worker 停止导致等待","findings":[f for o in context["outputs"] for f in o["findings"]],"missing_information":[],"next_steps":[]}
            if not any(r["kind"]=="runtime" for r in refs):data|={"assessment":"insufficient","root_cause":None,"missing_information":["需要当前运行状态"]}
        elif messages[-1]["role"]=="tool":
            data=contribution(json.loads(messages[-1]["content"])["references"][0])
        else:
            role="runtime" if "运行调查" in prompt else "knowledge"
            if self.blocked and role=="runtime":raise ModelBlocked("quota exhausted fixture")
            name="collect_runtime" if role=="runtime" else "search_knowledge"
            return {"role":"assistant","content":"","tool_calls":[{"id":"tool","type":"function","function":{"name":name,"arguments":json.dumps({} if role=="runtime" else {"query":"worker"})}}]}
        return {"role":"assistant","content":json.dumps(data,ensure_ascii=False)}

def test_parallel_independent_tasks_and_cited_merge():
    tools=FakeTools(threading.Barrier(2))
    result=run_collaboration({"target":"fault","question":"任务等待"},Model(),tools)
    assert result["report"]["assessment"]=="supported"
    assert result["model_calls"]==6 and result["tool_calls"]==2
    assert result["agent_count"]==3
    assert {r["kind"] for r in tools.refs}=={"runtime","knowledge"}
    assert result["collaboration"]["merge_status"]=="completed"

def test_dependency_waits_and_simple_question_uses_one_role():
    tools=FakeTools()
    result=run_collaboration({"target":"fault","question":"x"},Model(tasks(True)),tools)
    assert tools.order==["runtime","manual"]
    result=run_collaboration({"target":"fault","question":"手册规则"},Model([tasks()[1]]),FakeTools())
    assert result["agent_count"]==2 and len(result["collaboration"]["tasks"])==1

@pytest.mark.parametrize("plan",[
    tasks()+[tasks()[0]],
    [tasks()[0]|{"role":"shell"}],
    [tasks()[0]|{"depends_on":["missing"]}],
    [tasks()[0]|{"depends_on":["manual"]},tasks()[1]|{"depends_on":["runtime"]}],
    [tasks()[0]|{"id":str(i)} for i in range(7)],
])
def test_invalid_plan_rejected_before_tools(plan):
    tools=FakeTools()
    with pytest.raises(ValueError):run_collaboration({"target":"fault","question":"x"},Model(plan),tools)
    assert tools.order==[]

def test_atomic_budget_has_no_overrun():
    budget=Budget(max_model=2)
    from concurrent.futures import ThreadPoolExecutor
    def reserve():
        try:budget.take("model");return True
        except LimitReached:return False
    with ThreadPoolExecutor(max_workers=8) as pool:assert sum(pool.map(lambda _:reserve(),range(8)))==2

def test_partial_failure_skips_dependencies_preserves_existing_evidence():
    class Failing(Model):
        def generate(self,messages,definitions):
            if "运行调查" in messages[0]["content"]:raise RuntimeError("fixture failure")
            return super().generate(messages,definitions)
    tools=FakeTools()
    result=run_collaboration({"target":"fault","question":"x"},Failing(tasks(True)),tools)
    assert result["report"]["assessment"]=="insufficient"
    assert [t["status"] for t in result["collaboration"]["tasks"]]==["failed","skipped"]
    assert tools.order==[]

def test_shared_budget_returns_insufficient_without_extra_model_calls():
    result=run_collaboration({"target":"fault","question":"x"},Model(),FakeTools(),max_model=3)
    assert result["model_calls"]==3 and result["report"]["assessment"]=="insufficient"

def test_quota_failure_stops_further_dispatch_and_merge():
    with pytest.raises(ModelBlocked):run_collaboration({"target":"fault","question":"x"},Model(blocked=True),FakeTools())

def test_unconnected_plan_can_use_no_specialists():
    result=run_collaboration({"target":"unconnected","question":"x"},Model([]),FakeTools())
    assert result["agent_count"]==1 and result["tool_calls"]==0
    assert result["report"]["assessment"]=="insufficient"

def test_role_tool_boundary_and_reference_namespaces():
    from opslens.investigation.tools import Tools
    tools=Tools({'target':'fault'})
    runtime=tools.fork('a','runtime');knowledge=tools.fork('b','knowledge')
    with pytest.raises(ValueError,match='无权'):runtime.execute('search_knowledge',{'query':'x'})
    with pytest.raises(ValueError,match='无权'):knowledge.execute('collect_runtime',{})
    with pytest.raises(ValueError):knowledge.execute('shell',{})
    assert runtime.add(R,'/x')['id']=='Ea-1'
    assert knowledge.add(K,'/y','knowledge')['id']=='Kb-1'
    with pytest.raises(ValueError):
        from opslens.investigation.collaboration import validate_contribution
        validate_contribution(contribution(runtime.refs[0]),knowledge.refs,'fault')

def test_failed_task_preserves_other_independent_evidence_and_never_confirms_cause():
    class Failing(Model):
        def generate(self,messages,definitions):
            if '运行调查' in messages[0]['content']:raise RuntimeError('fixture failure')
            return super().generate(messages,definitions)
    tools=FakeTools()
    value=run_collaboration({'target':'fault','question':'x'},Failing(),tools)
    assert value['report']['assessment']=='insufficient' and value['report']['root_cause'] is None
    assert len(value['report']['findings'])==1 and tools.refs[0]['kind']=='knowledge'

def test_deadline_blocks_before_first_request():
    tools=FakeTools()
    with pytest.raises(LimitReached):run_collaboration({'target':'fault','question':'x'},Model(),tools,timeout=-1)
    assert not tools.order

def test_merge_cannot_drop_one_kind_of_evidence_from_combined_diagnosis():
    class MissingKnowledge(Model):
        def generate(self,messages,definitions):
            value=super().generate(messages,definitions)
            if '合并协调' in messages[0]['content']:
                data=json.loads(value['content'])
                data['findings']=[f for f in data['findings'] if f['references'][0]['id'].startswith('E')]
                value['content']=json.dumps(data)
            return value
    tools=FakeTools()
    with pytest.raises(ValueError,match='两类'):run_collaboration({'target':'fault','question':'x'},MissingKnowledge(),tools)
    assert {r['kind'] for r in tools.refs}=={'runtime','knowledge'}

def test_internal_mode_and_baseline_are_not_business_context():
    class Observed(Model):
        def generate(self,messages,definitions):
            assert 'multi-agent-v1' not in json.dumps(messages)
            assert 'private-index-bookkeeping' not in json.dumps(messages)
            return super().generate(messages,definitions)
    run_collaboration({'target':'fault','question':'x','baseline':'multi-agent-v1','index_id':'private-index-bookkeeping','mode':'multi'},Observed(),FakeTools())


@pytest.mark.parametrize('repair_succeeds',[True,False])
def test_disjoint_manual_quote_gets_source_specific_bounded_repair(repair_succeeds):
    # Reproduce the user's failure: the model joined non-adjacent manual sentences,
    # then a generic correction still joined them. Never accept the joined quote.
    source='postgres 停止后不能保存任务。\nAPI 返回 HTTP 503。\n\n## 只读核对\n不要将锁等待等同于数据库停止。'
    joined='postgres 停止后不能保存任务。不要将锁等待等同于数据库停止。'
    class ManualTools(FakeTools):
        def fork(self,task_id,role):
            child=super().fork(task_id,role)
            execute=child.execute
            def read(name,args):
                result=execute(name,args)
                if role=='knowledge':result['references'][0]['content']=source
                return result
            child.execute=read
            return child
    class RepairModel(Model):
        repairs=0
        def generate(self,messages,definitions):
            if '知识调查' in messages[0]['content']:
                if messages[-1]['role']=='tool':
                    data=contribution(json.loads(messages[-1]['content'])['references'][0])
                    data['findings'][0]['references'][0]['quote']=joined
                    return {'role':'assistant','content':json.dumps(data,ensure_ascii=False)}
                if len(messages)>2 and messages[-1]['role']=='user':
                    self.repairs+=1
                    text=messages[-1]['content']
                    assert source in json.loads(text.split('引用更正资料：',1)[1])['citation_errors'][0]['source_content']
                    assert '分成两条' in text and 'K1-manual' in text
                    ref={'id':'K1-manual','content':source}
                    data=contribution(ref)
                    data['findings'][0]['references']=[{'id':ref['id'],'quote':q} for q in
                        (['postgres 停止后不能保存任务。','不要将锁等待等同于数据库停止。'] if repair_succeeds else [joined])]
                    return {'role':'assistant','content':json.dumps(data,ensure_ascii=False)}
            return super().generate(messages,definitions)
    model=RepairModel();tools=ManualTools()
    result=run_collaboration({'target':'fault','question':'当前状态与手册'},model,tools)
    assert model.repairs==1 and result['tool_calls']==2 and result['model_calls']<=8
    if repair_succeeds:
        assert result['report']['assessment']=='supported'
        assert result['collaboration']['merge_status']=='completed'
        assert all(q['quote'] in source for f in result['collaboration']['tasks'][1]['output']['findings'] for q in f['references'])
    else:
        assert result['collaboration']['tasks'][1]['status']=='failed'
        assert '与原文不一致' in result['collaboration']['tasks'][1]['error']
        assert result['report']['assessment']=='insufficient' and result['report']['root_cause'] is None


def test_repair_feedback_exposes_all_bad_quotes_but_only_collected_role_sources():
    from opslens.investigation.engine import citation_repair_context
    refs=[K|{'content':'第一句。\n中间内容。\n最后一句。'},K|{'id':'K2','content':'真实文字。'}]
    data={'findings':[{'references':[{'id':'K1','quote':'第一句。最后一句。'},
        {'id':'K2','quote':'虚构文字。'},{'id':'E-other-role','quote':'worker exited'},
        {'id':'K2','quote':'真实文字。'}]}]}
    errors=citation_repair_context(data,refs)['citation_errors']
    assert [e['id'] for e in errors]==['K1','K2','E-other-role']
    assert errors[0]['source_content']==refs[0]['content']
    assert errors[1]['source_content']==refs[1]['content']
    assert errors[2]['source_content'] is None
