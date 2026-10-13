import hashlib,json
from datetime import datetime,timezone,timedelta
import pytest
from opslens.investigation.model import GenerationProvider,MODEL
from opslens.rag.provider import QuotaGuard,ModelBlocked,ModelFailure
from test_rag_provider import Ledger
def provider(tmp_path,response,valid=True):
    path=tmp_path/"quota.json";now=datetime.now(timezone.utc)
    row={"region":"cn-beijing","remaining_tokens":100000,"verified_at":now.isoformat(),"expires_at":(now+timedelta(days=1)).isoformat(),"free_only_effective":valid}
    path.write_text(json.dumps({"key_sha256":hashlib.sha256(b"fixture-key").hexdigest(),"api_base":"https://dashscope.aliyuncs.com/api/v1","models":{MODEL:row}}),encoding="utf-8")
    calls=[]
    def transport(route,payload):calls.append((route,payload));return response
    return GenerationProvider(key="fixture-key",guard=QuotaGuard(path,"fixture-key"),transport=transport,ledger=Ledger()),calls
def test_compatible_generation_fixed_model_tools_and_no_thinking(tmp_path):
    value={"choices":[{"message":{"role":"assistant","content":"{}"},"finish_reason":"stop"}],"usage":{"total_tokens":5},"id":"fixture"}
    p,calls=provider(tmp_path,value)
    assert p.generate([{"role":"user","content":"demo"}],[])["provider_request_id"]=="fixture"
    route,payload=calls[0]
    assert p._url(route)=="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions" and payload["model"]==MODEL
    assert payload["enable_thinking"] is False and payload["enable_search"] is False
def test_generation_guard_prevents_request(tmp_path):
    p,calls=provider(tmp_path,{},False)
    with pytest.raises(ModelBlocked):p.generate([],[])
    assert calls==[]

def test_evaluation_model_selection_is_explicit_and_guarded(tmp_path):
    response={"choices":[{"message":{"role":"assistant","content":"{}"},"finish_reason":"stop"}],"usage":{"total_tokens":5},"id":"fixture"}
    p,calls=provider(tmp_path,response)
    # A model without its own verified quota must never borrow the default quota.
    p.name='deepseek-v4.1-flash'
    with pytest.raises(ModelBlocked):p.generate([],[])
    assert calls==[]
    record=json.loads(p.guard.path.read_text(encoding='utf-8'));record['models'][p.name]=record['models'][MODEL]
    p.guard.path.write_text(json.dumps(record),encoding='utf-8')
    assert p.generate([],[])['provider_request_id']=='fixture'
    assert calls[0][1]['model']=='deepseek-v4.1-flash' and 'preserve_thinking' not in calls[0][1]
    assert GenerationProvider(key='fixture-key').name==MODEL
def test_truncated_output_is_not_valid_diagnosis(tmp_path):
    p,_=provider(tmp_path,{"choices":[{"message":{"role":"assistant","content":"{}"},"finish_reason":"length"}]})
    with pytest.raises(ModelFailure):p.generate([],[])

def test_connection_diagnostics_stay_local_and_mask_key(tmp_path):
    import httpx
    p,_=provider(tmp_path,{})
    saved=[]
    class LocalLedger:
        def reserve(self,*args):return 'fixture-id'
        def finish(self,*args,**kwargs):saved.append(kwargs)
    p.ledger=LocalLedger()
    def broken(*args):raise httpx.ConnectError('connection failed fixture-key')
    p.transport=broken
    with pytest.raises(ModelFailure):p.generate([],[])
    assert 'fixture-key' not in json.dumps(saved)
    assert saved[0]['result']['diagnostic'][0]['type']=='ConnectError'
    assert saved[0]['error_code']=='model_exception_ConnectError'

def test_connection_precheck_has_no_credentials_body_or_model_call(tmp_path,monkeypatch):
    from opslens.investigation import model as module
    from types import SimpleNamespace
    p,_=provider(tmp_path,{})
    calls=[]
    class Client:
        def get(self,url,**kwargs):calls.append((url,kwargs));return SimpleNamespace(status_code=400)
    monkeypatch.setattr(module,'model_http_client',lambda:Client())
    assert p.prepare_parallel()==[400,400]
    assert len(calls)==2 and all(kwargs=={'timeout':10} for _,kwargs in calls)
    assert not p.ledger.rows
    blocked,_=provider(tmp_path,{},False)
    with pytest.raises(ModelBlocked):blocked.prepare_parallel()
    assert len(calls)==2
