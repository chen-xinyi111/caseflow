import hashlib
import json
from datetime import datetime,timezone,timedelta
import pytest
from opslens.rag.provider import QuotaGuard,DashScopeProvider,ModelBlocked,ModelFailure,EMBED_MODEL,RERANK_MODEL

class Ledger:
    def __init__(self): self.rows=[];self.finished=[]
    def reserve(self,*args): self.rows.append(args);return "fixture-call"
    def finish(self,*args,**kwargs): self.finished.append((args,kwargs))

def setup_provider(tmp_path, response):
    now=datetime.now(timezone.utc)
    row={"region":"cn-beijing","free_only_effective":True,"remaining_tokens":100000,"verified_at":now.isoformat(),"expires_at":(now+timedelta(days=1)).isoformat()}
    record={"key_sha256":hashlib.sha256(b"fixture-key").hexdigest(),"api_base":"https://dashscope.aliyuncs.com/api/v1","models":{EMBED_MODEL:row.copy(),RERANK_MODEL:row.copy()}}
    path=tmp_path/"quota.json";path.write_text(json.dumps(record),encoding="utf-8")
    calls=[];ledger=Ledger()
    def transport(route,payload): calls.append((route,payload));return response
    provider=DashScopeProvider(key="fixture-key",guard=QuotaGuard(path,"fixture-key"),transport=transport,ledger=ledger)
    return provider,path,record,calls,ledger

@pytest.mark.parametrize("field,value",[("free_only_effective",False),("remaining_tokens",0),("region","ap-southeast-1"),("expires_at","2000-01-01T00:00:00+00:00"),("verified_at","2000-01-01T00:00:00+00:00")])
def test_quota_rejects_unsafe_or_expired_records_before_request(tmp_path,field,value):
    provider,path,record,calls,ledger=setup_provider(tmp_path,{})
    record["models"][EMBED_MODEL][field]=value;path.write_text(json.dumps(record))
    with pytest.raises(ModelBlocked): provider.embed(["hello"])
    assert calls==[] and ledger.rows==[]

def test_guard_is_bound_to_key_and_beijing_endpoint(tmp_path):
    provider,path,record,calls,_=setup_provider(tmp_path,{})
    record["key_sha256"]="wrong";path.write_text(json.dumps(record))
    with pytest.raises(ModelBlocked): provider.embed(["hello"])
    assert calls==[]
    with pytest.raises(ModelBlocked): QuotaGuard(path,"fixture-key","https://dashscope.aliyuncs.com/api/v1").check(EMBED_MODEL)

def test_embedding_native_shape_dimension_and_order(tmp_path):
    one=[1.]+[0.]*1023;two=[0.,1.]+[0.]*1022
    provider,_,_,calls,ledger=setup_provider(tmp_path,{"output":{"embeddings":[{"text_index":1,"embedding":two},{"text_index":0,"embedding":one}]},"usage":{"total_tokens":4}})
    assert provider.embed(["first","second"],text_type="query")==[one,two]
    route,payload=calls[0]
    assert route.endswith("/text-embedding/text-embedding")
    assert payload["model"]==EMBED_MODEL and payload["parameters"]["dimension"]==1024 and payload["parameters"]["text_type"]=="query"
    assert ledger.finished

@pytest.mark.parametrize("vector",[[1.,0.],[float("nan")]+[0.]*1023,[0.]*1024])
def test_bad_embedding_never_becomes_index_data(tmp_path,vector):
    provider,_,_,_,_=setup_provider(tmp_path,{"output":{"embeddings":[{"text_index":0,"embedding":vector}]}})
    with pytest.raises(ModelFailure):provider.embed(["hello"])

def test_rerank_fixed_model_and_valid_response(tmp_path):
    provider,_,_,calls,_=setup_provider(tmp_path,{"output":{"results":[{"index":1,"relevance_score":.9},{"index":0,"relevance_score":.1}]}})
    assert provider.rerank("question",["one","two"])[0]["index"]==1
    assert calls[0][1]["model"]==RERANK_MODEL
    assert calls[0][1]["parameters"]=={"top_n":2,"return_documents":False}

def test_no_remote_host_or_singapore_endpoint(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_BASE","https://dashscope-intl.aliyuncs.com/api/v1")
    with pytest.raises(ModelBlocked):DashScopeProvider(key="fixture-key")

def test_real_http_client_reuses_connections_and_closes(monkeypatch):
    from opslens.rag import provider as module
    made=[]
    class Client:
        def __init__(self,**kwargs):self.is_closed=False;made.append(kwargs)
        def close(self):self.is_closed=True
    monkeypatch.setattr(module,'_model_http_client',None)
    monkeypatch.setattr(module.httpx,'Client',Client)
    one=module.model_http_client()
    assert module.model_http_client() is one and len(made)==1
    assert made[0]['trust_env'] is False and made[0]['follow_redirects'] is False
    assert made[0]['timeout'].read==90 and made[0]['timeout'].connect==10
    module.close_model_http_client()
    assert one.is_closed and module._model_http_client is None

def test_exact_payload_review_blocks_private_content_before_network(tmp_path,monkeypatch):
    from opslens.rag import provider as module
    monkeypatch.setenv('OPSLENS_DEMO_REVIEW','1')
    monkeypatch.setenv('USERNAME','fixture-user')
    monkeypatch.setattr(module,'ROOT',tmp_path)
    (tmp_path/'.runtime').mkdir()
    module.review_demo_payload({'input':{'texts':['OpsLens AI makes evidence clear','redis://redis:6379/0']}},'fixture-key')
    files=list((tmp_path/'.runtime').glob('outbound-payload-*.json'))
    assert len(files)==1 and 'fixture-key' not in files[0].read_text(encoding='utf-8')
    for body in [{'text':'fixture-key'},{'text':'person@example.com'},{'text':'C:/Users/person/private.txt'},{'text':'fixture-user'}]:
        with pytest.raises(ModelBlocked):module.review_demo_payload(body,'fixture-key')
    assert len(list((tmp_path/'.runtime').glob('*.json')))==1
