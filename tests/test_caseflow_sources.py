"""Deterministic source-location tests; no database or model calls."""
import io
import zipfile
import pytest
from opslens.caseflow import documents, files, store
from opslens.caseflow.documents_api import EvidenceInput


def docx():
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('word/document.xml', '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
          <w:p><w:r><w:t>第一段😀规则</w:t></w:r></w:p><w:p/>
          <w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格条件</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
        </w:body></w:document>''')
    return out.getvalue()


@pytest.mark.parametrize('fmt', ['txt', 'md'])
def test_paragraphs_keep_offsets_and_not_physical_pages(fmt):
    pages = ['\n第一段😀规则\n同一段下一行\n\n第二段 e\u0301\n']
    snapshot = documents.make_snapshot(pages, fmt)
    assert [u['text'] for u in snapshot['units']] == ['第一段😀规则\n同一段下一行', '第二段 e\u0301']
    assert all(u['page_number'] is None for u in snapshot['units'])
    for unit in snapshot['units']:
        assert pages[0][unit['start']:unit['end']] == unit['text']


def test_docx_table_blocks_preserve_text_and_kind():
    pages, warnings, kinds = files.inspect_details('spec.docx', docx())
    snapshot = documents.make_snapshot(pages, 'docx', kinds)
    assert warnings == []
    assert [u['kind'] for u in snapshot['units']] == ['paragraph', 'table_paragraph']
    assert snapshot['units'][1]['text'] == '表格条件'
    assert all(u['page_number'] is None for u in snapshot['units'])


def test_legacy_filename_is_not_evidence_of_pdf_pages():
    d = {'name': 'old.pdf', 'pages': ['旧文本一', '旧文本二']}
    s = documents.snapshot_from_document(d)
    assert [u['kind'] for u in s['units']] == ['legacy_segment', 'legacy_segment']
    assert all(u['page_number'] is None for u in s['units'])


def test_pdf_keeps_blank_physical_page_and_cross_page_pieces():
    s = documents.make_snapshot(['第一条规则', '', '第三页规则'], 'pdf')
    assert [u['page_number'] for u in s['units']] == [1, 2, 3]
    segments = [{'unit_id': 'u1', 'start': 0, 'end': 5}, {'unit_id': 'u3', 'start': 0, 'end': 5}]
    pieces = documents.validate_segments(s, segments)
    assert [p['text'] for p in pieces] == ['第一条规则', '第三页规则']
    assert documents.SEPARATOR.join(p['text'] for p in pieces) == '第一条规则\n[…片段分隔…]\n第三页规则'


def test_repeated_text_never_silently_picks_first_occurrence():
    s = documents.make_snapshot(['规则相同。中间文字。规则相同。'], 'txt')
    hits = documents.resolve(s, 1, '规则相同。')
    assert len(hits) == 2 and hits[0]['segments'] != hits[1]['segments']
    for h in hits:
        assert documents.validate_segments(s, h['segments'])[0]['text'] == '规则相同。'


def test_cross_paragraph_old_quote_returns_separate_verified_segments():
    s = documents.make_snapshot(['段落甲\n\n段落乙'], 'md')
    hit = documents.resolve(s, 1, '段落甲\n\n段落乙')[0]
    assert len(hit['segments']) == 2
    assert hit['quote'] == '段落甲'+documents.SEPARATOR+'段落乙'


def test_codepoint_offsets_include_non_bmp_and_combining_characters():
    s = documents.make_snapshot(['A😀e\u0301规则'], 'txt')
    assert documents.validate_segments(s, [{'unit_id': 'u1', 'start': 1, 'end': 4}])[0]['text'] == '😀e\u0301'


@pytest.mark.parametrize('segments', [[], [{'unit_id': 'missing', 'start': 0, 'end': 1}],
    [{'unit_id': 'u1', 'start': True, 'end': 2}], [{'unit_id': 'u1', 'start': 0, 'end': False}],
    [{'unit_id': 'u1', 'start': -1, 'end': 1}], [{'unit_id': 'u1', 'start': 1, 'end': 1}],
    [{'unit_id': 'u1', 'start': 0, 'end': 100}],
    [{'unit_id': 'u1', 'start': 1, 'end': 3}, {'unit_id': 'u1', 'start': 2, 'end': 4}]])
def test_invalid_or_overlapping_evidence_is_rejected(segments):
    with pytest.raises(ValueError):
        documents.validate_segments(documents.make_snapshot(['一二三四'], 'txt'), segments)


def test_api_rejects_bool_offsets_before_any_database_access():
    with pytest.raises(ValueError):
        EvidenceInput.model_validate({'document_id': '00000000-0000-0000-0000-000000000001',
            'snapshot_sha256': 'a'*64, 'segments': [{'unit_id': 'u1', 'start': True, 'end': 2}], 'quote': 'x'})


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'file:///secret', 'https://user:password@example.com',
                                'https://example.com/\nsecret', 'ftp://example.com'])
def test_source_urls_are_metadata_not_executable_or_authenticated_urls(url):
    with pytest.raises(ValueError): documents.source_url(url)


def test_current_upload_identity_keeps_distinct_originals_with_same_text(monkeypatch):
    monkeypatch.setattr(store, 'clean', lambda t: t)
    s = store.empty()
    a = store.add_document(s, 'rules.txt', ['same'], identity={'original_sha256': 'a'})
    b = store.add_document(s, 'rules.txt', ['same'], identity={'original_sha256': 'b'})
    again = store.add_document(s, 'rules.txt', ['same'], identity={'original_sha256': 'b'})
    assert a['document_id'] != b['document_id']
    assert again == {'document_id': b['document_id'], 'reused': True}


def test_cleaning_and_crlf_are_frozen_before_location_calculation():
    text = store.clean('规则😀\r\n\r\npassword=secretvalue\r\n后续规则')
    s = documents.make_snapshot([text], 'txt')
    assert '\r' not in text and 'secretvalue' not in text
    assert documents.resolve(s, 1, '[REDACTED]')


def test_projection_reads_frozen_snapshot_even_after_parser_changes(monkeypatch):
    from contextlib import contextmanager
    snapshot=documents.make_snapshot(['旧版😀原文'],'txt')
    doc={'id':'fixed-id','pages':snapshot['pages'],'original_sha256':'a'*64}
    row={'project_id':'project','snapshot':snapshot,'snapshot_sha256':store.digest(snapshot)}
    class Connection:
        def execute(self,*args):return self
        def fetchone(self):return row
    @contextmanager
    def connect():yield Connection()
    monkeypatch.setattr(documents.db,'connect',connect)
    monkeypatch.setattr(documents,'make_snapshot',lambda *a,**k:pytest.fail('must not reparse'))
    assert documents.index_snapshot(doc)==snapshot
    row['snapshot_sha256']='b'*64
    with pytest.raises(ValueError,match='快照校验失败'):documents.index_snapshot(doc)


def test_index_parameters_invalidate_metadata_without_rewriting_source():
    snapshot=documents.make_snapshot(['规则'],'txt');digest=store.digest(snapshot)
    doc={'id':'d','pages':['规则'],'format':'txt','previous_id':None}
    value={'snapshot_sha256':digest,'source_kind':'file','format':'txt',
        'original_bytes':b'raw','original_sha256':'a'*64,'parser_signature':'frozen'}
    project={'id':'p','state':{'documents':[doc],'knowledge_index':{
        'id':'i','document_ids':['d'],'projection_version':2,'source_hashes':{'d':digest},
        'mode':'keyword','projection_signature':documents.projection_signature('keyword')}}}
    assert documents._metadata(project,doc,value)['index_status']=='ready'
    project['state']['knowledge_index']['projection_signature']='old-parameters'
    assert documents._metadata(project,doc,value)['index_status']=='stale'
    assert doc['pages']==['规则'] and value['parser_signature']=='frozen'
