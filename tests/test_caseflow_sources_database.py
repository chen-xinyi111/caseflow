"""Real PostgreSQL source tests in the existing isolated-schema fixture; no model calls."""
import copy
import hashlib
from uuid import UUID, uuid4
import psycopg
import pytest
from test_caseflow_database import client, text_pdf
from test_caseflow_sources import docx
from opslens import db
from opslens.caseflow import documents, store, agent


def new(client):
    p=client.post('/api/v1/caseflow/projects',json={'name':'第三阶段隔离验证'}).json()
    return p,'/api/v1/caseflow/projects/'+p['id']


def paste(client,base,p,text='重复规则。\n\n😀第二段规则。',previous=None):
    return client.post(base+'/texts',json={'revision':p['revision'],'name':'资料', 'content':text,
        'previous_id':previous,'source_url':'https://example.com/source'}).json()


@pytest.mark.parametrize('name,raw,fmt', [('rules.pdf',text_pdf(),'pdf'),('rules.docx',docx(),'docx'),
    ('rules.md','段落一\n\n段落二'.encode(),'md'),('rules.txt','文本😀'.encode(),'txt')],ids=['pdf','docx','md','txt'])
def test_original_bytes_snapshot_formats_and_no_implicit_model_or_index(client,name,raw,fmt):
    p,base=new(client)
    response=client.post(base+'/files',data={'revision':p['revision']},files={'file':(name,raw)})
    assert response.status_code==200,response.text
    p=response.json();d=p['state']['documents'][0]
    saved=client.get(base+'/documents/'+d['id']).json()
    assert saved['format']==fmt and saved['original_available']
    assert saved['snapshot']['pages']==d['pages']
    original=client.get(base+'/documents/'+d['id']+'/original')
    assert original.content==raw and saved['original_sha256']==hashlib.sha256(raw).hexdigest()
    assert original.headers['x-content-type-options']=='nosniff'
    assert all(u['page_number'] is None for u in saved['snapshot']['units']) if fmt!='pdf' else saved['snapshot']['units'][0]['page_number']==1
    with db.connect() as c:
        assert c.execute('SELECT count(*) n FROM cf_requests').fetchone()['n']==0
        assert c.execute('SELECT count(*) n FROM kb_indexes').fetchone()['n']==0


def test_multiple_occurrences_pin_second_refresh_and_old_version_survive(client,monkeypatch):
    p,base=new(client);p=paste(client,base,p,'😀相同规则。相同规则。');did=p['state']['documents'][0]['id']
    before=copy.deepcopy(p['state'])
    result=client.post(base+'/evidence/resolve-legacy',json={'document_id':did,'page':1,'quote':'相同规则。'}).json()
    assert result['location_status']=='ambiguous' and len(result['candidates'])==2
    chosen=result['candidates'][1]
    body={'document_id':did,'snapshot_sha256':result['snapshot_sha256'],'segments':chosen['segments'],'quote':chosen['quote']}
    saved=client.post(base+'/evidence',json=body);assert saved.status_code==200,saved.text
    evidence=saved.json();assert evidence['segments'][0]['start']==6
    frozen=client.get(base+'/documents/'+did).json()['snapshot']
    with monkeypatch.context() as changed:
        changed.setattr(documents,'make_snapshot',lambda *a,**k:pytest.fail('must read frozen snapshot'))
        assert documents.index_snapshot(before['documents'][0])==frozen
    assert client.post(base+'/evidence',json=body).json()['id']==evidence['id']
    assert client.get(base+'/evidence/'+evidence['id']).json()['quote']=='相同规则。'
    assert client.get(base).json()['state']==before
    updated=paste(client,base,p,'新规则。',did)
    assert updated['state']['documents'][0]==before['documents'][0]
    assert client.get(base+'/evidence/'+evidence['id']).json()['viewer_url']==evidence['viewer_url']
    assert client.get(base+'/documents/'+did).json()['historical'] is True
    client.post(base+'/archive',json={'revision':updated['revision'],'archived':True})
    assert client.get(base+'/evidence/'+evidence['id']).status_code==200
    assert client.post(base+'/evidence',json=body).status_code==409


def test_project_scope_snapshot_mismatch_and_quote_tampering(client):
    p,base=new(client);p=paste(client,base,p);did=p['state']['documents'][0]['id']
    result=client.post(base+'/evidence/resolve-legacy',json={'document_id':did,'page':1,'quote':'重复规则。'}).json()
    candidate=result['candidates'][0]
    body={'document_id':did,'snapshot_sha256':result['snapshot_sha256'],'segments':candidate['segments'],'quote':candidate['quote']}
    eid=client.post(base+'/evidence',json=body).json()['id']
    _,other=new(client)
    assert client.get(other+'/documents/'+did).status_code==404
    assert client.get(other+'/documents/'+did+'/original').status_code==404
    assert client.get(other+'/evidence/'+eid).status_code==404
    assert client.post(other+'/evidence',json=body).status_code==404
    assert client.post(base+'/evidence',json=body|{'snapshot_sha256':'f'*64}).status_code==409
    assert client.post(base+'/evidence',json=body|{'quote':'伪造原文'}).status_code==400


def test_legacy_read_is_nonmutating_and_explicit_migration_is_idempotent(client):
    p,base=new(client)
    p=store.update(p['id'],p['revision'],'legacy_fixture',lambda s:store.add_document(s,'legacy.pdf',['旧文本😀','旧第二段']))
    before=copy.deepcopy(p['state']);did=before['documents'][0]['id']
    info=client.get(base+'/documents/'+did).json()
    assert info['format']=='legacy_text' and not info['original_available']
    assert info['snapshot']['units'][0]['page_number'] is None
    assert client.get(base+'/documents/'+did+'/original').status_code==404
    with db.connect() as c:assert c.execute('SELECT count(*) n FROM cf_document_versions').fetchone()['n']==0
    assert documents.migrate_legacy(p['id'])['documents']==1
    with db.connect() as c:assert c.execute('SELECT count(*) n FROM cf_document_versions').fetchone()['n']==0
    assert documents.migrate_legacy(p['id'],apply=True)['documents']==1
    assert documents.migrate_legacy(p['id'],apply=True)['documents']==0
    assert client.get(base).json()['state']==before


def test_cross_units_and_database_immutability(client):
    p,base=new(client);p=paste(client,base,p);did=p['state']['documents'][0]['id']
    info=client.get(base+'/documents/'+did).json();units=info['snapshot']['units']
    segments=[{'unit_id':u['id'],'start':0,'end':len(u['text'])} for u in units]
    quote=documents.SEPARATOR.join(u['text'] for u in units)
    saved=client.post(base+'/evidence',json={'document_id':did,'snapshot_sha256':info['snapshot_sha256'],'segments':segments,'quote':quote})
    assert saved.status_code==200,saved.text
    eid=saved.json()['id'];assert len(saved.json()['pieces'])==2
    for sql,args in [('UPDATE cf_document_versions SET snapshot_sha256=%s WHERE document_id=%s',('f'*64,did)),
                     ('DELETE FROM cf_evidence WHERE id=%s',(eid,))]:
        with pytest.raises(psycopg.errors.RaiseException):
            with db.connect() as c:c.execute(sql,args)
    assert client.get(base+'/evidence/'+eid).status_code==200


def test_failure_and_stale_upload_are_atomic_and_original_identity_matters(client):
    p,base=new(client)
    a=client.post(base+'/files',data={'revision':p['revision']},files={'file':('secret.txt',b'password=first')}).json()
    b=client.post(base+'/files',data={'revision':a['revision']},files={'file':('secret.txt',b'password=second')}).json()
    assert len(b['state']['documents'])==2
    assert b['state']['documents'][0]['pages']==b['state']['documents'][1]['pages']
    assert b['state']['documents'][0]['id']!=b['state']['documents'][1]['id']
    assert client.post(base+'/files',data={'revision':p['revision']},files={'file':('stale.txt',b'unchanged')}).status_code==409
    assert client.post(base+'/files',data={'revision':b['revision']},files={'file':('broken.pdf',b'not a pdf')}).status_code==400
    assert len(client.get(base).json()['state']['documents'])==2
    with db.connect() as c:assert c.execute('SELECT count(*) n FROM cf_document_versions').fetchone()['n']==2


def test_keyword_projection_keeps_pages_offsets_and_does_not_use_external_models(client):
    p,base=new(client)
    p=store.update(p['id'],p['revision'],'legacy_fixture',lambda s:store.add_document(s,'two pages',['预约时长30分钟。','预约时长120分钟。']))
    did=p['state']['documents'][0]['id']
    class Offline:
        def rag(self):pytest.fail('keyword indexing must not call any model')
    first,mapping=agent.index_documents(p['state'],[did],'keyword',Offline())
    second,_=agent.index_documents(p['state'],[did],'keyword',Offline())
    assert first==second
    with db.connect() as c:
        chunks=c.execute('SELECT * FROM kb_chunks ORDER BY page').fetchall()
        assert [x['page'] for x in chunks]==[1,2]
        for x in chunks:assert p['state']['documents'][0]['pages'][x['page']-1][x['start_char']:x['end_char']]==x['content']
        assert c.execute('SELECT active FROM kb_indexes WHERE id=%s',(first,)).fetchone()['active'] is False
        assert c.execute('SELECT count(*) n FROM cf_requests').fetchone()['n']==0
