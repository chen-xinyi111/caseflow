"""Project-scoped point qualification. No model approval and no implicit rule confirmation."""
import copy
from . import store

VERSION = 'caseflow-basis-points-1'
REVIEW_FIELDS = {'intent', 'expected_direction', 'rule_applicability', 'citations'}


class ReviewFailure(ValueError):
    def __init__(self, items):
        self.detail = {'message': '所选测试点未全部通过校验，本批未保存', 'items': items}
        super().__init__(self.detail['message'])


def mode(s):
    return s.get('workflow_mode', 'strict')


def rule_fingerprint(r):
    return store.digest({k: r.get(k) for k in ('id', 'revision', 'text', 'status', 'answers', 'citations', 'document_id', 'questions')})


def binding(r):
    return {'id': r['id'], 'revision': r['revision'], 'fingerprint': rule_fingerprint(r)}


def descendants(s, did):
    result = []
    for d in s['documents']:
        current, seen = d, set()
        while current.get('previous_id') and current['id'] not in seen:
            seen.add(current['id'])
            if current['previous_id'] == did:
                result.append(d['id']); break
            current = next((x for x in s['documents'] if x['id'] == current['previous_id']), {})
    return sorted(result)


def source_versions(s, ids):
    from . import documents
    return [{'id': did, 'snapshot_sha256': store.digest(documents.index_snapshot(store.find(s['documents'], did))),
             'successors': descendants(s, did)} for did in ids]


def bundle_reasons(s, bundle, rule_ids=None, evidence_ids=None, item_ids=None):
    reasons = []
    if bundle['status'] != 'ready': reasons.append('资料存在未读或失败段，准备包尚未完成')
    # A problem in an unrelated rule must not globally block this point.
    chosen = set(rule_ids) if rule_ids is not None else {b['id'] for b in bundle['requirement_bindings']}
    for b in bundle['requirement_bindings']:
        if b['id'] in chosen:
            r = store.find(s['requirements'], b['id'])
            if binding(r) != b: reasons.append(r['code']+'的正文、状态、回答或来源已变化')
    evidence = set(evidence_ids) if evidence_ids is not None else set(bundle.get('evidence_catalog', {}))
    used_docs = {bundle['evidence_catalog'][eid]['document_id'] for eid in evidence if eid in bundle.get('evidence_catalog', {})}
    if evidence_ids is None: used_docs = {v['id'] for v in bundle['source_versions']}
    for v in bundle['source_versions']:
        if v['id'] in used_docs:
            d = store.find(s['documents'], v['id'])
            if d.get('snapshot_sha256', v['snapshot_sha256']) != v['snapshot_sha256'] or descendants(s, d['id']) != v['successors']:
                reasons.append(d['name']+'的来源版本已更新，请重新准备并绑定')
    chosen_items = set(item_ids) if item_ids is not None else {i['id'] for i in bundle['items']}
    for iid in chosen_items:
        frozen = bundle.get('item_answer_fingerprints', {}).get(iid)
        if frozen is not None and frozen != item_answers_fingerprint(s,iid): reasons.append('公共条件的问题或回答已变化，请重新准备并绑定')
    for frozen in bundle.get('question_bindings', []):
        if chosen_items & set(frozen['basis_item_ids']):
            q = store.find(s.get('clarifications', []), frozen['id'])
            if answer_fingerprint(q) != frozen['fingerprint']: reasons.append('公共条件的回答已变化，请重新准备并绑定')
    if bundle.get('retrieval_mode', 'keyword') != s.get('retrieval_mode', 'keyword'):
        reasons.append('检索策略已变化，请重新准备')
    return list(dict.fromkeys(reasons))


def item_answers_fingerprint(s, iid):
    return store.digest(sorted((q['id'],answer_fingerprint(q)) for q in s.get('clarifications',[]) if iid in q.get('basis_item_ids',[])))


def answer_fingerprint(q):
    return store.digest({k:q.get(k) for k in ('id','status','answer')})


def question_open(s, ref):
    q = next((x for x in s.get('clarifications', []) if x['id'] == ref), None)
    return q is None or q['status'] != 'resolved'


def relevant_questions(s, point, bundle):
    rids = {b['id'] for b in point['requirement_bindings']}; item_ids = set(point['basis_item_ids'])
    output = []
    for q in s.get('clarifications', []):
        if q['status'] != 'open': continue
        related = bool(set(q.get('requirement_ids', [])) & rids or point['id'] in q.get('point_ids', []) or
                       (q.get('basis_bundle_id') == bundle['id'] and set(q.get('basis_item_ids', [])) & item_ids))
        if related or q['id'] in point.get('question_ids', []): output.append(q['question'])
    for b in point['requirement_bindings']:
        r = store.find(s['requirements'], b['id'])
        for text in r.get('questions', []):
            mapped = [q for q in s.get('clarifications', []) if q.get('question_reference') ==
                      {'kind': 'requirement', 'id': r['id'], 'document_id': r['document_id'], 'text': text}]
            if not mapped or any(q['status'] != 'resolved' for q in mapped): output.append(text)
    for text in point.get('questions', []):
        mapped = [q for q in s.get('clarifications', []) if q.get('question_reference') == {'kind': 'point', 'id': point['id'], 'text': text}]
        if not mapped or any(q['status'] != 'resolved' for q in mapped): output.append(text)
    for issue in bundle.get('issues', []):
        if set(issue.get('requirement_ids', [])) & rids or set(issue.get('basis_item_ids', [])) & item_ids:
            if not issue.get('question_id') or question_open(s, issue['question_id']): output.append(issue['text'])
    return list(dict.fromkeys(output))


def dependency_fingerprint(s, point):
    bundle = store.find(s.get('basis_bundles', []), point['basis_bundle_id'])
    rids = [b['id'] for b in point['requirement_bindings']]
    rules = [store.find(s['requirements'], rid) for rid in rids]
    numeric = [r for r in s.get('numeric_rules', []) if set(r['requirement_ids']) & set(rids)]
    items = [store.find(bundle['items'], i) for i in point['basis_item_ids']]
    return store.digest([mode(s), point['basis_bundle_id'], [binding(r) for r in rules], items,
        point['evidence_ids'], relevant_questions(s, point, bundle),
        [answer_fingerprint(q) for q in s.get('clarifications', []) if set(q.get('basis_item_ids', [])) & set(point['basis_item_ids']) or point['id'] in q.get('point_ids',[])],
        [(v['id'], descendants(s, v['id'])) for v in bundle['source_versions']
         if any(bundle['evidence_catalog'].get(e, {}).get('document_id') == v['id'] for e in point['evidence_ids'])], numeric])


def qualification(s, point):
    bundle = store.find(s.get('basis_bundles', []), point['basis_bundle_id'])
    rids = [b['id'] for b in point['requirement_bindings']]
    reasons = bundle_reasons(s, bundle, rids, point['evidence_ids'], point['basis_item_ids'])
    if not rids: reasons.append('至少关联一个目标规则')
    if not point['evidence_ids']: reasons.append('缺少有效原文证据')
    if not set(rids) <= {b['id'] for b in bundle['requirement_bindings']}: reasons.append('规则超出准备包')
    for b in point['requirement_bindings']:
        r = store.find(s['requirements'], b['id'])
        if binding(r) != b: reasons.append(r['code']+'的绑定已过期')
        if r['status'] == 'excluded': reasons.append(r['code']+'已排除')
        elif mode(s) == 'strict' and r['status'] != 'confirmed': reasons.append(r['code']+'在严格流程中尚未确认')
    allowed = set(bundle.get('evidence_catalog', {}))
    if not set(point['evidence_ids']) <= allowed: reasons.append('证据超出准备包')
    required = set()
    for rid in rids:
        ids = bundle.get('rule_evidence', {}).get(rid, [])
        if not ids: reasons.append('目标规则缺少可回读的精确依据')
        required.update(ids)
    for iid in point['basis_item_ids']:
        item = store.find(bundle['items'], iid); required.update(item['evidence_ids'])
        if item.get('requirement_id'):
            r = store.find(s['requirements'], item['requirement_id'])
            if mode(s) == 'strict' and r['status'] != 'confirmed': reasons.append('规则类公共条件尚未确认')
    if not required <= set(point['evidence_ids']): reasons.append('尚未包含所选规则及公共条件的全部依据')
    reasons.extend('待澄清：'+q for q in relevant_questions(s, point, bundle))
    return list(dict.fromkeys(reasons))


def present(s, point):
    reasons = qualification(s, point); current = dependency_fingerprint(s, point)
    semantic = point.get('semantic_review', {'status': 'unavailable', 'reason': '尚未进行模型复核'})
    return copy.deepcopy(point) | {'dependency_fingerprint': current, 'current': not reasons,
        'blocking_reasons': reasons, 'location_status': 'valid' if point['evidence_ids'] and set(point['evidence_ids']) <= set(store.find(s['basis_bundles'],point['basis_bundle_id'])['evidence_catalog']) else 'invalid',
        'needs_attention': bool(reasons or semantic['status'] != 'checked' or point['status'] == 'needs_review'),
        'semantic_review': semantic}


def question(s, text, *, requirement_ids=(), point_ids=(), bundle_id=None, item_ids=(), reference=None, kind='ambiguity'):
    rows = s.setdefault('clarifications', [])
    old = next((q for q in rows if reference and q.get('question_reference') == reference), None)
    if old: return old['id']
    qid = store.uid()
    rows.append({'id': qid, 'question': store.clean(text).strip(), 'kind': kind, 'requirement_ids': list(requirement_ids),
                 'point_ids': list(point_ids), 'basis_bundle_id': bundle_id, 'basis_item_ids': list(item_ids),
                 'question_reference': reference, 'status': 'open', 'answer': '', 'created_at': store.now()})
    return qid


def add(s, values, *, origin='user', semantic=None, identity=None):
    bundle = store.find(s.get('basis_bundles', []), values['basis_bundle_id'])
    rids = values.get('rule_ids', [])
    if not set(rids) <= {b['id'] for b in bundle['requirement_bindings']}: raise ValueError('规则超出准备包')
    if not set(values['basis_item_ids']) <= {x['id'] for x in bundle['items']}: raise ValueError('公共条件超出准备包')
    if not set(values['evidence_ids']) <= set(bundle['evidence_catalog']): raise ValueError('证据超出准备包')
    row = {k: copy.deepcopy(v) for k, v in values.items() if k != 'rule_ids'}
    for key in ('intent', 'group', 'module'): row[key] = store.clean(row[key]).strip()
    row['tags'] = [store.clean(t).strip() for t in row['tags']]
    row['questions'] = [store.clean(t).strip() for t in row['questions']]
    if not row['intent']: raise ValueError('请填写测试意图')
    rows = s.setdefault('test_points', [])
    number = max([int(r['code'][2:]) for r in rows]+[0])+1
    row.update(id=identity or store.uid(), code=f'TP{number:03}', revision=1, requirement_bindings=[binding(store.find(s['requirements'], r)) for r in rids],
               status='pending', origin=origin, created_at=store.now(), semantic_review=semantic or {'status': 'unavailable', 'reason': '人工补充，尚未进行模型复核'})
    rows.append(row)
    row['question_ids'] = [question(s, text, point_ids=[row['id']], bundle_id=bundle['id'],
        reference={'kind': 'point', 'id': row['id'], 'text': text}) for text in row['questions']]
    return row


def edit(s, tid, expected, fingerprint, values):
    old = store.find(s.get('test_points', []), tid)
    if old['revision'] != expected or dependency_fingerprint(s, old) != fingerprint: raise FileExistsError('测试点或依据已变化，未覆盖本次编辑')
    store.snapshot(s, 'test_point', old)
    # Validate using the same constructor, then preserve stable identity/number.
    row = add(s, values, identity=tid, semantic={'status':'unavailable','reason':'人工编辑后尚未进行模型复核'})
    s['test_points'].pop()
    old_status = old['status']; identity = {k: old[k] for k in ('id', 'code', 'created_at', 'origin')}
    old.clear(); old.update(row | identity | {'revision': expected+1, 'status': 'needs_review' if old_status in ('approved', 'needs_review') else 'pending'})
    old['edited_by'] = 'local_user'
    return old


def review(s, items, decision, note):
    failures = []; selected = []
    if len({x['id'] for x in items}) != len(items): raise ValueError('评审列表不能重复')
    if not note.strip(): raise ValueError('请填写评审说明')
    for item in items:
        try:
            row = store.find(s.get('test_points', []), item['id'])
            if row['revision'] != item['point_revision'] or dependency_fingerprint(s, row) != item['dependency_fingerprint']:
                raise FileExistsError('测试点修订或依据指纹已过期')
            if decision == 'approved':
                reasons = qualification(s, row)
                if reasons: raise ValueError('；'.join(reasons))
                if row.get('semantic_review', {}).get('status') != 'checked':
                    if set(item.get('reviewed_fields', [])) != REVIEW_FIELDS or not item.get('semantic_resolution', '').strip() or set(item.get('evidence_ids', [])) != set(row['evidence_ids']):
                        raise ValueError('须逐点核对四项内容和当前证据，并填写带依据的人工解决说明')
            selected.append((row, item))
        except (LookupError, ValueError, FileExistsError) as exc:
            failures.append({'id': item['id'], 'reason': str(exc)})
    if failures: raise ReviewFailure(failures)
    for row, item in selected:
        store.snapshot(s, 'test_point', row)
        fp = dependency_fingerprint(s, row)
        s.setdefault('point_reviews', []).append({'id': store.uid(), 'point_id': row['id'], 'point_revision': row['revision'],
            'dependency_fingerprint': fp, 'decision': decision, 'note': store.clean(note), 'reviewed_fields': item.get('reviewed_fields', []),
            'semantic_resolution': store.clean(item.get('semantic_resolution', '')), 'evidence_ids': item.get('evidence_ids', []),
            'actor': 'local_user', 'at': store.now()})
        row.update(status=decision, approval_fingerprint=fp if decision == 'approved' else None)
    return {'reviewed': len(selected)}


def invalidate(s):
    for point in s.get('test_points', []):
        if point['status'] == 'approved' and (qualification(s, point) or point.get('approval_fingerprint') != dependency_fingerprint(s, point)):
            store.snapshot(s, 'test_point', point); point['status'] = 'needs_review'
    from . import case_trace
    case_trace.invalidate(s)


def switch(s, value):
    if value not in ('standard', 'strict'): raise ValueError('流程无效')
    s['workflow_mode'] = value
    return {'workflow_mode': value}
