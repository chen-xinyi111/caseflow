import json
import socket
from uuid import uuid4
import httpx
import pytest
from opslens.diagnostic import intake,documents
from opslens.diagnostic.contracts import ModelSettings
from opslens.diagnostic.model import validate_settings
from opslens.diagnostic.worker import validate_report,messages

def test_all_lines_scanned_repeated_errors_and_late_stack_preserved():
    text='INFO request complete\n'*5000+'ERROR connection refused 127.0.0.1:5432\n'*200+'Traceback (most recent call last):\n  connect()\nConnectionRefusedError: [Errno 111] Connection refused\n'
    s=intake.summarize(text)
    assert s['lines']==5203 and sum(g['count'] for g in s['groups'])==202
    assert any(g['count']==200 for g in s['groups'])
    source={'id':uuid4(),'name':'server.log','kind':'log','version':1,'content':text,'summary':s,'provenance':{}}
    parts=intake.segments([source],[],'数据库连接失败')
    assert 'ConnectionRefusedError' in '\n'.join(p['content'] for p in parts)
    assert any(p['start_line']>4900 for p in parts)

def test_no_error_does_not_manufacture_clues_and_bad_files_rejected():
    assert intake.summarize('INFO startup complete\n0 errors')['groups']==[]
    for name,raw in [('binary.txt',b'a\x00b'),('x.log',b'\xff\xfe'),('script.py',b'print(1)'),('x.log',b'a'*(intake.MAX_FILE+1))]:
        with pytest.raises(ValueError):intake.decode_file(name,raw)
    assert intake.decode_file('x.jsonl',b'\xef\xbb\xbf{"level":"ERROR"}')=='{"level":"ERROR"}'

def test_secrets_and_private_identifiers_mask_without_changing_lines():
    text='{"password":"private", "api_key":"key-value"}\nAuthorization: Bearer my-secret\nC:\\Users\\person\\x a@company.test 13812345678'
    result=intake.clean(text,['my-secret'])
    assert len(result.splitlines())==3
    assert all(s not in result for s in ['private','key-value','my-secret','person','a@company.test','13812345678'])

def test_reference_and_runtime_fact_validation():
    parts=[{'id':'S1','kind':'log','content':'Connection refused'}]
    report={'summary':'连接被拒绝，原因待核对','facts':[{'text':'记录了连接被拒绝','citations':[{'id':'S1','quote':'Connection refused'}]}],'candidate_causes':[],'next_checks':[],'missing_information':[]}
    assert validate_report(report,parts)['facts']
    with pytest.raises(ValueError):validate_report(report,[parts[0]|{'kind':'history'}])
    with pytest.raises(ValueError):validate_report(report,[parts[0]|{'content':'normal'}])
    with pytest.raises(ValueError):validate_report(report|{'facts':[]},parts)
    with pytest.raises(ValueError):validate_report(report|{'next_checks':[{'purpose':'delete','operation':'docker compose down -v','expected':'deleted','changes_state':True}]},parts)

def test_model_input_is_bounded():
    with pytest.raises(ValueError):messages({'problem':'x'*33000,'sources':[]})
    assert len(json.dumps(messages({'problem':'connect refused','sources':[]})).encode())<=32768


def test_attachment_uuid_citation_resolves_only_unique_selected_exact_quote():
    source=str(uuid4());parts=[{'id':'S'+source+':1-2','source_id':source,'kind':'log','content':'ERROR Connection refused'}]
    raw={'summary':'需要核对','facts':[{'text':'连接报错','citations':[{'id':source,'quote':'Connection refused'}]}]}
    result=validate_report(raw,parts)
    assert result['facts'][0]['citations'][0]['id']==parts[0]['id']
    assert raw['facts'][0]['citations'][0]['id']==source
    with pytest.raises(ValueError):validate_report(raw,parts+[parts[0]|{'id':'S'+source+':3-4'}])
    with pytest.raises(ValueError):validate_report(raw,[parts[0]|{'content':'INFO ready'}])
    with pytest.raises(ValueError):validate_report(raw,[parts[0]|{'source_id':str(uuid4())}])

def public_dns(*args,**kwargs):return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]

def test_official_fetch_rejects_private_addresses_and_external_redirects():
    with pytest.raises(ValueError):documents.validate_url('https://docs.python.org/x',['docs.python.org'],lambda *a,**kw:[(2,1,6,'',('127.0.0.1',443))])
    for url in ['http://docs.python.org/x','https://evil.test/x','https://user:pass@docs.python.org/x']:
        with pytest.raises(ValueError):documents.validate_url(url,['docs.python.org'],public_dns)
    seen=[]
    def transport(request):
        seen.append(request)
        return httpx.Response(302,headers={'location':'http://127.0.0.1/private'})
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(ValueError):documents.fetch('https://docs.python.org/x',['docs.python.org'],client,public_dns)
    assert len(seen)==1 and not seen[0].content

def test_official_fetch_extracts_body_and_limits_size():
    def transport(request):return httpx.Response(200,headers={'content-type':'text/html; charset=utf-8'},text='<h1>Connection</h1><script>secret()</script><p>Check address</p>')
    with httpx.Client(transport=httpx.MockTransport(transport)) as c:
        text,url=documents.fetch('https://docs.python.org/x#section',['docs.python.org'],c,public_dns)
    assert 'Check address' in text and 'secret()' not in text and '#' not in url
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,headers={'content-type':'text/plain'},content=b'x'*(1024*1024+1)))) as c:
        with pytest.raises(ValueError):documents.fetch('https://docs.python.org/x',['docs.python.org'],c,public_dns)

def test_explicit_profile_no_credentials_in_address_or_implicit_remote_http():
    for url in ['http://remote.test/v1','https://user:secret@remote.test/v1','https://remote.test/v1?key=x']:
        with pytest.raises(ValueError):validate_settings(ModelSettings(mode='compatible',base_url=url))
    assert not validate_settings(ModelSettings(mode='compatible',base_url='http://127.0.0.1:11434/v1')).enabled


def test_document_production_transport_pins_validated_ip_and_tls_hostname(monkeypatch):
    original=httpx.Client;seen=[]
    def handler(request):
        seen.append(request)
        assert request.url.host=='93.184.216.34'
        assert request.headers['host']=='docs.python.org'
        assert request.extensions['sni_hostname']=='docs.python.org'
        return httpx.Response(200,headers={'content-type':'text/plain'},text='official fixture body')
    monkeypatch.setattr(documents.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    text,url=documents.fetch('https://docs.python.org/x',['docs.python.org'],resolver=public_dns)
    assert len(seen)==1 and text=='official fixture body' and url=='https://docs.python.org/x'


def test_missing_or_unsupported_context_has_specific_nonempty_missing_items():
    report={'summary':'资料不足，无法确认运行原因','facts':[],'candidate_causes':[],'next_checks':[],'missing_information':['请提供错误发生时间与完整启动堆栈']}
    assert validate_report(report,[])['facts']==[]
    with pytest.raises(ValueError):validate_report(report|{'missing_information':[' ']},[])


def test_real_pilot_regressions_preserve_urls_and_mark_restart_risk():
    from opslens.diagnostic.risk import changes_state
    text='curl -v http://localhost:8080/metrics\nhttps://docs.python.org/3/\nC:\\Users\\private\\app.py'
    safe=intake.clean(text)
    assert 'http://localhost:8080/metrics' in safe and 'https://docs.python.org/3/' in safe and 'private' not in safe
    for operation in ['确保使用修正后的配置重新启动Python HTTP服务，然后GET /metrics','修改第二实例的配置文件或启动参数，将端口改为63363，重新启动','在命令行直接运行后端启动命令（如 python main.py）']:
        assert changes_state({'operation':operation,'changes_state':False})
    assert not changes_state({'operation':'查看启动脚本；执行 netstat -ano 或 systemctl status app','changes_state':False})
    raw={'summary':'基于提供日志，需实际检查','facts':[],'missing_information':['具体监听地址'],'next_checks':[{'purpose':'验证服务','operation':'重新启动Python HTTP服务','expected':'服务返回200','changes_state':False}]}
    assert validate_report(raw,[])['next_checks'][0]['changes_state'] is True
