"""Immutable project document snapshots and code-point evidence. No model calls."""
import copy
import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid5

from opslens import db
from . import files, store

PARSER_SIGNATURE = 'caseflow-source-1:clean-lf:codepoints:paragraphs'
EVIDENCE_NAMESPACE = UUID('8a1c82c4-f319-4d43-b7e9-099f925604b6')
SEPARATOR = '\n[…片段分隔…]\n'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def source_url(value):
    value = value.strip()
    if not value:
        return ''
    parsed = urlsplit(value)
    if (len(value) > 2000 or parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username or parsed.password or any(ord(c) < 32 for c in value)):
        raise ValueError('来源网址需为不含凭据的 HTTP/HTTPS 地址')
    return value


def make_snapshot(pages, fmt, unit_kinds=None):
    """Offsets always reference this exact canonical pages array, never raw bytes."""
    units = []
    for page, text in enumerate(pages, 1):
        if fmt in ('pdf', 'legacy_text'):
            spans = [(0, len(text))]
        elif fmt == 'docx':
            spans = [(m.start(), m.end()) for m in re.finditer(r'[^\n]+', text)]
        else:
            spans = [(m.start(), m.end()) for m in re.finditer(r'[^\n]+(?:\n(?![ \t]*\n)[^\n]+)*', text)]
        for start, end in spans:
            n = len(units)
            kind = 'pdf_page' if fmt == 'pdf' else 'legacy_segment' if fmt == 'legacy_text' else 'paragraph'
            if fmt == 'docx' and unit_kinds and n < len(unit_kinds):
                kind = unit_kinds[n]
            label = f'第{page}页' if fmt == 'pdf' else f'历史文本段{page}' if fmt == 'legacy_text' else f'段落{n+1}'
            units.append({'id': f'u{n+1}', 'kind': kind, 'page': page,
                          'page_number': page if fmt == 'pdf' else None,
                          'start': start, 'end': end, 'text': text[start:end], 'label': label})
    return {'version': 1, 'offset_unit': 'unicode_code_point', 'pages': list(pages), 'units': units}


def snapshot_from_document(doc):
    return make_snapshot(doc['pages'], doc.get('format', 'legacy_text'), doc.get('unit_kinds'))


def index_snapshot(doc):
    """An index projects the frozen version, never reparses it with today's rules."""
    with db.connect() as c:
        row = c.execute('SELECT * FROM cf_document_versions WHERE document_id=%s', (doc['id'],)).fetchone()
        if row:
            return version(c, {'id': row['project_id']}, doc)['snapshot']
        if doc.get('original_sha256'):
            raise ValueError('文档版本存储缺失，不能重建索引快照')
        return make_snapshot(doc['pages'], 'legacy_text')


def projection_signature(mode):
    from opslens.rag.ranking import PARAMETERS
    from opslens.rag.provider import EMBED_MODEL, DIMENSIONS
    return store.digest({'projection': 2, 'parser': PARSER_SIGNATURE, 'mode': mode,
                         'parameters': PARAMETERS, 'embedding': None if mode == 'keyword' else [EMBED_MODEL, DIMENSIONS]})


def _project_document(c, pid, did, *, writable=False):
    p = store.get(c, pid, writable)
    if writable and p['state'].get('archived'):
        raise FileExistsError('归档项目只读，请先恢复项目')
    return p, store.find(p['state']['documents'], str(did))


def version(c, p, doc):
    row = c.execute('SELECT * FROM cf_document_versions WHERE project_id=%s AND document_id=%s',
                    (p['id'], doc['id'])).fetchone()
    if not row:
        if doc.get('original_sha256'):
            raise ValueError('文档版本存储缺失，不能冒充旧文本或重建原件')
        # Read-only fallback. Only explicit migration / evidence creation persists it.
        snapshot = make_snapshot(doc['pages'], 'legacy_text')
        return {'document_id': doc['id'], 'project_id': p['id'], 'source_kind': 'legacy',
                'format': 'legacy_text', 'original_bytes': None, 'original_sha256': None,
                'snapshot': snapshot, 'snapshot_sha256': store.digest(snapshot),
                'parser_signature': 'legacy-preserved-text-1', 'persisted': False}
    if store.digest(row['snapshot']) != row['snapshot_sha256'] or row['snapshot']['pages'] != doc['pages']:
        raise ValueError('文档快照校验失败，未重新解析或替换原文')
    return dict(row) | {'persisted': True}


def _insert_version(c, value):
    c.execute('''INSERT INTO cf_document_versions
        (document_id,project_id,source_kind,format,original_bytes,original_sha256,snapshot,snapshot_sha256,parser_signature)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(document_id) DO NOTHING''',
        (value['document_id'], value['project_id'], value['source_kind'], value['format'],
         value['original_bytes'], value['original_sha256'], store.jsonb(value['snapshot']),
         value['snapshot_sha256'], value['parser_signature']))
    saved = c.execute('SELECT project_id,snapshot_sha256,original_sha256 FROM cf_document_versions WHERE document_id=%s',
                      (value['document_id'],)).fetchone()
    if (str(saved['project_id']) != str(value['project_id']) or saved['snapshot_sha256'] != value['snapshot_sha256']
            or saved['original_sha256'] != value['original_sha256']):
        raise FileExistsError('同一文档版本已有不同快照或原件')


def save(pid, revision, name, raw, role='requirement', previous_id=None, url='', *, pasted=False):
    url = source_url(url)
    if pasted:
        text = raw.decode('utf-8')
        if not text.strip() or len(text) > 100000:
            raise ValueError('粘贴正文需要1至100000字符')
        pages, warnings, kinds, fmt = [text], [], None, 'txt'
    else:
        pages, warnings, kinds = files.inspect_details(name, raw)
        fmt = Path(name).suffix.lower()[1:]
    pages = [store.clean(p) for p in pages]
    # Drop empty DOCX paragraphs from the kind list just as the snapshot does.
    snapshot = make_snapshot(pages, fmt, kinds)
    identity = {'source_kind': 'paste' if pasted else 'file', 'format': fmt,
                'source_url': url, 'original_sha256': sha(raw),
                'snapshot_sha256': store.digest(snapshot), 'parser_signature': PARSER_SIGNATURE}
    if kinds:
        identity['unit_kinds'] = kinds
    with db.connect() as c:
        p = store.get(c, pid, True)
        if p['state'].get('archived'):
            raise FileExistsError('归档项目只读，请先恢复项目')
        if p['revision'] != revision:
            raise FileExistsError('资料已被更新，请刷新后再操作；你的输入尚未覆盖已有内容')
        state = copy.deepcopy(p['state'])
        result = store.add_document(state, name, pages, role, str(previous_id) if previous_id else None,
                                    warnings, identity=identity, canonical=True)
        value = identity | {'project_id': p['id'], 'document_id': result['document_id'],
                            'original_bytes': raw, 'snapshot': snapshot}
        _insert_version(c, value)
        c.execute('UPDATE cf_projects SET state=%s,revision=revision+1,updated_at=now() WHERE id=%s',
                  (store.jsonb(state), pid))
        c.execute('INSERT INTO cf_audit(project_id,action,payload) VALUES (%s,%s,%s)',
                  (pid, 'document', store.jsonb({'revision': revision+1, 'result': result})))
        return store.public(store.get(c, pid))


def _metadata(p, doc, value):
    chain, seen, current = [], set(), doc
    while current and current['id'] not in seen:
        chain.append(current['id']); seen.add(current['id'])
        current = next((d for d in p['state']['documents'] if d['id'] == current.get('previous_id')), None)
    index = p['state'].get('knowledge_index')
    index_status = 'not_built'
    if index and doc['id'] in index.get('document_ids', []):
        index_status = 'legacy' if index.get('projection_version') != 2 else 'ready'
        if index.get('source_hashes', {}).get(doc['id']) != value['snapshot_sha256']:
            index_status = 'stale'
        if index.get('mode') != p['state'].get('retrieval_mode', 'keyword') or index.get('projection_signature') != projection_signature(p['state'].get('retrieval_mode', 'keyword')):
            index_status = 'stale'
    return {k: doc.get(k) for k in ('id', 'name', 'role', 'previous_id', 'created_at')} | {
        'source_url': doc.get('source_url', ''), 'source_kind': value['source_kind'],
        'format': value['format'], 'original_available': value['original_bytes'] is not None,
        'original_sha256': value['original_sha256'], 'snapshot_sha256': value['snapshot_sha256'],
        'parser_signature': value['parser_signature'], 'version_number': len(chain),
        'version_chain': list(reversed(chain)), 'warnings': doc.get('warnings', []),
        'historical': any(d.get('previous_id') == doc['id'] for d in p['state']['documents']),
        'parse_status': 'legacy_text' if value['source_kind'] == 'legacy' else 'ready_with_warnings' if doc.get('warnings') else 'ready',
        'index_status': index_status, 'original_missing_reason': '历史文本，原文件未保存' if value['original_bytes'] is None else '',
        'viewer_url': f"/#/projects/{p['id']}/documents/{doc['id']}"}


def _index_state(c, p, rows):
    # A saved project pointer alone is not proof that an index is ready.
    index = p['state'].get('knowledge_index')
    if index:
        saved = c.execute('SELECT status,error FROM kb_indexes WHERE id=%s', (index['id'],)).fetchone()
        for row in rows:
            if row['id'] in index.get('document_ids', []) and row['index_status'] == 'ready':
                row['index_status'] = saved['status'] if saved else 'missing'
                row['index_error'] = saved.get('error') if saved else '索引记录不存在'
    attempts = c.execute("SELECT status,error,input FROM cf_tasks WHERE project_id=%s AND kind='index' ORDER BY created_at DESC,id DESC", (p['id'],)).fetchall()
    for row in rows:
        latest = next((t for t in attempts if row['id'] in t['input'].get('document_ids', [])), None)
        if latest:
            row['index_attempt_status'] = latest['status']
            row['index_attempt_error'] = latest['error']
    return rows


def listing(pid):
    with db.connect() as c:
        p = store.get(c, pid)
        return _index_state(c, p, [_metadata(p, d, version(c, p, d)) for d in p['state']['documents']])


def detail(pid, did):
    with db.connect() as c:
        p, doc = _project_document(c, pid, did)
        value = version(c, p, doc)
        return _index_state(c, p, [_metadata(p, doc, value)])[0] | {'snapshot': value['snapshot']}


def original(pid, did):
    with db.connect() as c:
        p, doc = _project_document(c, pid, did)
        value = version(c, p, doc)
        raw = value['original_bytes']
        if raw is None:
            raise LookupError('历史文本，原文件未保存')
        raw = bytes(raw)
        if sha(raw) != value['original_sha256']:
            raise ValueError('原文件哈希校验失败')
        return raw, value['format']


def validate_segments(snapshot, segments):
    if not isinstance(segments, list) or not 1 <= len(segments) <= 32:
        raise ValueError('引用需要1至32个连续原文片段')
    by_id = {u['id']: (i, u) for i, u in enumerate(snapshot['units'])}
    pieces, last = [], (-1, -1)
    for segment in segments:
        if set(segment) != {'unit_id', 'start', 'end'} or segment['unit_id'] not in by_id:
            raise ValueError('引用文本单元不存在或字段无效')
        ordinal, unit = by_id[segment['unit_id']]
        a, b = segment['start'], segment['end']
        if type(a) is not int or type(b) is not int or not 0 <= a < b <= len(unit['text']):
            raise ValueError('引用字符范围无效，使用从0开始的Unicode字符区间')
        if ordinal < last[0] or (ordinal == last[0] and a < last[1]):
            raise ValueError('引用片段须按原文顺序且不能重叠')
        last = (ordinal, b)
        pieces.append(segment | {'text': unit['text'][a:b], 'label': unit['label'], 'page_number': unit['page_number']})
    return pieces


def segments_for_range(snapshot, page, start, end):
    return [{'unit_id': u['id'], 'start': max(start, u['start'])-u['start'],
             'end': min(end, u['end'])-u['start']} for u in snapshot['units']
            if u['page'] == page and max(start, u['start']) < min(end, u['end'])]


def resolve(snapshot, page, quote):
    if type(page) is not int or not 1 <= page <= len(snapshot['pages']) or not quote or len(quote) > 4000:
        raise ValueError('引用页/文本段或原文无效')
    text, offset, candidates = snapshot['pages'][page-1], 0, []
    while len(candidates) < 100:
        start = text.find(quote, offset)
        if start < 0:
            break
        segments = segments_for_range(snapshot, page, start, start+len(quote))
        if segments:
            pieces = validate_segments(snapshot, segments)
            candidates.append({'segments': segments, 'quote': SEPARATOR.join(p['text'] for p in pieces),
                               'label': '、'.join(dict.fromkeys(p['label'] for p in pieces)), 'start': start, 'end': start+len(quote)})
        offset = start+1
    return candidates


def resolve_legacy(pid, did, page, quote):
    value = detail(pid, did)
    candidates = resolve(value['snapshot'], page, quote)
    return {'document_id': str(did), 'snapshot_sha256': value['snapshot_sha256'], 'candidates': candidates,
            'location_status': 'not_found' if not candidates else 'unique' if len(candidates) == 1 else 'ambiguous',
            'truncated': len(candidates) == 100}


def create_evidence(pid, did, snapshot_sha256, segments, quote):
    with db.connect() as c:
        p, doc = _project_document(c, pid, did, writable=True)
        value = version(c, p, doc)
        if value['snapshot_sha256'] != snapshot_sha256:
            raise FileExistsError('文档快照已变化，请重新打开原文')
        pieces = validate_segments(value['snapshot'], segments)
        if quote != SEPARATOR.join(v['text'] for v in pieces):
            raise ValueError('引用文字与精确区间不一致')
        eid = uuid5(EVIDENCE_NAMESPACE, store.digest([str(pid), str(did), snapshot_sha256, segments]))
        if not value['persisted']:
            _insert_version(c, value)
        c.execute('''INSERT INTO cf_evidence(id,project_id,document_id,snapshot_sha256,segments,quote,quote_sha256)
            VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING''',
                  (eid, pid, did, snapshot_sha256, store.jsonb(segments), quote, sha(quote.encode())))
        return _evidence(c, p, eid)


def _evidence(c, p, eid):
    row = c.execute('SELECT * FROM cf_evidence WHERE project_id=%s AND id=%s', (p['id'], eid)).fetchone()
    if not row:
        raise LookupError('引用不属于当前项目')
    doc = store.find(p['state']['documents'], str(row['document_id']))
    value = version(c, p, doc)
    pieces = validate_segments(value['snapshot'], row['segments'])
    if (row['snapshot_sha256'] != value['snapshot_sha256'] or row['quote'] != SEPARATOR.join(v['text'] for v in pieces)
            or row['quote_sha256'] != sha(row['quote'].encode())):
        raise ValueError('引用位置或文字哈希校验失败')
    return dict(row) | {'pieces': pieces, 'location_status': 'valid',
                        'location_label': '、'.join(dict.fromkeys(v['label'] for v in pieces)),
                        'viewer_url': f"/#/projects/{p['id']}/documents/{doc['id']}?evidence={eid}"}


def evidence(pid, eid):
    with db.connect() as c:
        return _evidence(c, store.get(c, pid), eid)


def migrate_legacy(pid=None, *, apply=False):
    """Explicit, idempotent sidecar-only migration; never reimport or reparse."""
    count = 0
    with db.connect() as c:
        rows = c.execute('SELECT * FROM cf_projects'+(' WHERE id=%s' if pid else '')+' ORDER BY id FOR UPDATE',
                         (pid,) if pid else ()).fetchall()
        if pid and not rows:
            raise LookupError('项目不存在')
        for p in rows:
            for doc in p['state']['documents']:
                value = version(c, p, doc)
                if not value['persisted']:
                    count += 1
                    if apply:
                        _insert_version(c, value)
        return {'apply': apply, 'documents': count, 'projects': len(rows)}
