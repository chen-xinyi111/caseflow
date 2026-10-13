import json
from uuid import uuid4
import pytest
from opslens.external import sanitize,TargetInput,ConsentInput,selected,digest,MAX_LOG_BYTES

def test_private_values_are_redacted():
    value=sanitize('Authorization: Bearer abc\npassword=secret\nredis://user:pass@redis/0\nC:\\Users\\person\\file.txt a@company.test 13812345678\nCookie: session=123')
    assert not any(x in value for x in ['Bearer abc','password=secret',':pass@','person','a@company.test','13812345678','session=123'])

def test_target_registration_rejects_commands_and_unregistered_log_services():
    for data in [dict(name='x',compose_project='x; rm',services=['api']),dict(name='x',compose_project='x',services=['api'],log_services=['db']),dict(name='x',compose_project='x',services=['api','api'])]:
        with pytest.raises(ValueError):TargetInput(**data)

def test_selection_must_include_successful_runtime_and_exact_ids():
    row={'items':[{'id':'a','kind':'runtime','success':True},{'id':'b','kind':'runtime','success':False}], 'documents':[{'id':'c','kind':'knowledge'}]}
    assert len(selected(row,['a','c']))==2
    for ids in [['c'],['b'],['a','a'],['unknown']]:
        with pytest.raises(ValueError):selected(row,ids)

def test_manifest_binds_content_and_document_version():
    data={'target_id':str(uuid4()),'items':[{'content':'one'}],'documents':[{'sha256':'a'}]}
    assert digest(data)!=digest(data|{'items':[{'content':'two'}]})
    assert digest(data)!=digest(data|{'documents':[{'sha256':'b'}]})

def test_external_verification_requires_new_user_confirmed_evidence_for_recheck():
    from opslens.investigation.verification import run_verified
    from test_verification_core import Model
    from test_collaboration_core import FakeTools
    model=Model(uncertain_rounds=1)
    result=run_verified({'target':'external','question':'waiting'},model,FakeTools())
    assert result['report']['assessment']=='insufficient'
    assert result['report']['root_cause'] is None and model.verifies==1
    assert result['tool_calls']==2 and result['verification']['replans']==[]
    assert '重新采集' in result['verification']['stop_reason']
