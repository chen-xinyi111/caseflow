"""Point-bound case qualification, immutable trace snapshots and human revisions."""
import copy
from . import store, points, grounding

VERSION = 'point-cases-1'
TRACE_FIELDS = ('trace_mode','test_point_id','test_point_revision','point_fingerprint','basis_bundle_id',
                'basis_item_ids','evidence_ids','source_versions','requirement_bindings','trace_snapshot')
CONTENT_FIELDS = ('title','preconditions','data','steps','expected','category','basis','citations',
                  'requirement_ids','requirement_revisions','numeric_input','calculation')


def is_point(row):
    return row.get('trace_mode') == 'point'


def require_point(s, point, binding=None):
    reasons = points.qualification(s, point)
    fp = points.dependency_fingerprint(s, point)
    if point['status'] != 'approved' or point.get('approval_fingerprint') != fp:
        reasons.append('测试点尚未按当前依据人工批准')
    if binding and (str(binding['id']) != point['id'] or binding['revision'] != point['revision'] or binding['fingerprint'] != fp):
        reasons.append('所选测试点修订或指纹已变化')
    if reasons: raise FileExistsError('；'.join(dict.fromkeys(reasons)))
    return fp


def context(s, point):
    """Exactly the adopted point scope; never all items in a larger bundle."""
    require_point(s,point)
    b = store.find(s['basis_bundles'],point['basis_bundle_id'])
    rids = [r['id'] for r in point['requirement_bindings']]
    items = [store.find(b['items'],iid) for iid in point['basis_item_ids']]
    sources = [b['evidence_catalog'][eid] for eid in point['evidence_ids']]
    dids = {e['document_id'] for e in sources}
    return {'target':b['target'],'point':{k:copy.deepcopy(point[k]) for k in ('id','code','revision','intent','group','types','module','priority')},
        'rules':[{k:copy.deepcopy(store.find(s['requirements'],rid).get(k)) for k in ('id','code','revision','text','status','answers')} for rid in rids],
        'public_conditions_and_context':copy.deepcopy(items),'sources':copy.deepcopy(sources),
        'confirmed_answers':[{k:q.get(k) for k in ('id','question','answer','requirement_ids','basis_item_ids','point_ids')}
            for q in s.get('clarifications',[]) if q['status']=='resolved' and
            (set(q.get('requirement_ids',[])) & set(rids) or set(q.get('basis_item_ids',[])) & set(point['basis_item_ids']) or point['id'] in q.get('point_ids',[]))],
        'source_versions':[copy.deepcopy(v) for v in b['source_versions'] if v['id'] in dids],
        'numeric_rules':copy.deepcopy(grounding.rules_for(s,rids,dids))}


def trace(s, point):
    ctx = context(s,point)
    return {'trace_mode':'point','test_point_id':point['id'],'test_point_revision':point['revision'],
        'point_fingerprint':points.dependency_fingerprint(s,point),'basis_bundle_id':point['basis_bundle_id'],
        'basis_item_ids':copy.deepcopy(point['basis_item_ids']),'evidence_ids':copy.deepcopy(point['evidence_ids']),
        'source_versions':ctx['source_versions'],'requirement_bindings':copy.deepcopy(point['requirement_bindings']),
        'trace_snapshot':ctx}


def content_fingerprint(row):
    return store.digest({k:row.get(k) for k in ('id','revision','review_note',*CONTENT_FIELDS,*TRACE_FIELDS)})


def evidence_fingerprint(s,row):
    point=store.find(s.get('test_points',[]),row['test_point_id'])
    return store.digest([{k:row.get(k) for k in TRACE_FIELDS},point['revision'],point['status'],points.dependency_fingerprint(s,point)])


def fingerprint(s, row):
    p = store.find(s.get('test_points',[]),row['test_point_id'])
    return store.digest([content_fingerprint(row),row['status'],p['revision'],p['status'],p.get('approval_fingerprint'),points.dependency_fingerprint(s,p)])


def reasons(s, row):
    if not is_point(row): return []
    try:
        p = store.find(s.get('test_points',[]),row['test_point_id'])
        errors = points.qualification(s,p)
        if p['status']!='approved' or p.get('approval_fingerprint')!=points.dependency_fingerprint(s,p):errors.append('关联测试点需重新人工批准')
        if row['test_point_revision']!=p['revision'] or row['point_fingerprint']!=points.dependency_fingerprint(s,p):errors.append('采用的测试点修订或依据已变化')
        if row['basis_bundle_id']!=p['basis_bundle_id'] or row['basis_item_ids']!=p['basis_item_ids'] or set(row['evidence_ids'])!=set(p['evidence_ids']):errors.append('采用的依据绑定已过期')
        if row['requirement_bindings']!=p['requirement_bindings']:errors.append('规则绑定已变化')
        if row.get('replaces_case_id') and not row.get('applied_to'):
            target=store.find(s['cases'],row['replaces_case_id'])
            if target['revision']!=row.get('replaces_case_revision') or content_fingerprint(target)!=row.get('replaces_case_fingerprint'):
                errors.append('原用例已经人工修改，建议已过期')
        return list(dict.fromkeys(errors))
    except (LookupError,KeyError):return ['追溯绑定缺失，不能作为当前用例批准']


def require_current(s, row):
    errors=reasons(s,row)
    if errors:raise FileExistsError('；'.join(errors))


def invalidate(s):
    for row in s['cases']:
        if is_point(row) and not row.get('applied_to') and row['status'] not in ('rejected','needs_review') and reasons(s,row):
            store.snapshot(s,'case',row);row['status']='needs_review'


def present(s, row):
    if not is_point(row):return {'trace_mode':'legacy_direct','trace_current':None,'trace_reasons':[], 'binding_fingerprint':None}
    errors=reasons(s,row)
    return {'trace_current':not errors,'trace_reasons':errors,'binding_fingerprint':fingerprint(s,row)}


def decorate(s, point, item):
    from .preparation import compatible_citations
    require_point(s,point)
    b=store.find(s['basis_bundles'],point['basis_bundle_id'])
    if set(item['evidence_ids'])!=set(point['evidence_ids']):raise ValueError('每条用例必须保留当前点采用的完整精确依据，不能添加其他点来源')
    result=copy.deepcopy(item)|trace(s,point)
    result.update(requirement_ids=[r['id'] for r in point['requirement_bindings']],
        requirement_revisions={r['id']:r['revision'] for r in point['requirement_bindings']},
        citations=compatible_citations(s,b['evidence_catalog'],point['evidence_ids']))
    if not store.case_complete(result):raise ValueError('用例需有完整前提、步骤和预期')
    return grounding.prepare_numeric(s,result,[v['id'] for v in result['source_versions']])


def add(s,g,result):
    point=store.find(s['test_points'],result['point_id']);require_point(s,point,result['point_binding'])
    added=[]
    for n,value in enumerate(result['cases']):
        key=store.digest([str(g['id']),result['item_key'],point['id'],point['revision'],n])
        old=next((c for c in s['cases'] if c.get('generation_key')==key),None)
        if old:added.append(old['id']);continue
        row=copy.deepcopy(value)
        replaces=row.get('replaces_case_id')
        if replaces:
            previous=store.find(s['cases'],replaces)
            if not is_point(previous) or previous['test_point_id']!=point['id'] or previous['status']!='needs_review':raise ValueError('建议只能更新同一测试点当前待复审的原用例')
            frozen=result['replace_targets'].get(replaces)
            if not frozen or previous['revision']!=frozen['revision'] or content_fingerprint(previous)!=frozen['fingerprint']:raise FileExistsError('原用例已修改，未保存过期建议')
            row.update(replaces_case_revision=previous['revision'],replaces_case_fingerprint=frozen['fingerprint'])
        require_current(s,row)
        number=max([int(c['code'][1:]) for c in s['cases']]+[0])+1
        row.update(id=store.uid(),code=f'T{number:03}',revision=1,status='draft',created_at=store.now(),review_note='',generation_key=key)
        s['cases'].append(row);added.append(row['id'])
    for issue in result.get('blocking_issues',[]):
        points.question(s,issue,point_ids=[point['id']],reference={'kind':'case_generation','id':str(g['id']),'text':issue})
    return result|{'case_ids':added}


def selected_check(s,row,revision,binding_fingerprint):
    if revision!=row['revision'] or binding_fingerprint!=fingerprint(s,row):raise FileExistsError('用例修订或依据指纹已变化，本次输入未覆盖服务器')


def numeric_valid(s,row):
    docs=[v['id'] for v in row['source_versions']]
    canonical=grounding.prepare_numeric(s,row,docs)
    if canonical.get('data')!=row.get('data') or canonical.get('expected')!=row.get('expected'):
        raise ValueError('数值输入或预期与当前已确认规则不一致，请明确重新计算后核对')


def approve(s,row,values,note):
    require_current(s,row)
    if row.get('replaces_case_id') and not row.get('applied_to'):raise ValueError('请先应用建议，再审核原用例')
    if not store.case_complete(row) or not note.strip():raise ValueError('请补齐用例内容并填写人工审核说明')
    numeric_valid(s,row)
    if not grounding.report_current(s,row) or row.get('grounding',{}).get('status')!='checked':
        if not values.get('acknowledge_grounding') or set(values.get('reviewed_fields',[]))!=set(grounding.FIELDS) or not values.get('semantic_resolution','').strip() or set(values.get('evidence_ids',[]))!=set(row['evidence_ids']):
            raise ValueError('须逐例核对四个字段、当前证据，填写带依据的人工解决说明')
        row['grounding_ack']={'at':store.now(),'note':store.clean(values['semantic_resolution']),'fields':values['reviewed_fields'],
                              'evidence_ids':values['evidence_ids'],'fingerprint':grounding.fingerprint(s,row)}


def edit(s,cid,values):
    row=store.find(s['cases'],cid)
    selected_check(s,row,values.get('case_revision'),values.get('binding_fingerprint'))
    if row.get('applied_to'):raise ValueError('建议已应用，请编辑原用例')
    store.snapshot(s,'case',row)
    old=copy.deepcopy(row);status=values.get('status',row['status']);note=values.get('review_note',row.get('review_note',''))
    binding=values.get('point_binding')
    if binding:
        if str(binding['id'])!=row['test_point_id']:raise ValueError('不能把原用例静默改绑到其他测试点')
        point=store.find(s['test_points'],row['test_point_id']);require_point(s,point,binding)
        row.update(trace(s,point));row['requirement_ids']=[r['id'] for r in point['requirement_bindings']]
        row['requirement_revisions']={r['id']:r['revision'] for r in point['requirement_bindings']}
        from .preparation import compatible_citations
        b=store.find(s['basis_bundles'],point['basis_bundle_id']);row['citations']=compatible_citations(s,b['evidence_catalog'],point['evidence_ids'])
    for field in ('title','preconditions','data','steps','expected','numeric_input'):
        if field in values:row[field]=copy.deepcopy(values[field])
    if 'numeric_input' in values and values['numeric_input'] is None:row.pop('calculation',None)
    if values.get('recalculate'):
        require_current(s,row);row.update(grounding.prepare_numeric(s,row,[v['id'] for v in row['source_versions']]))
    changed=any(old.get(k)!=row.get(k) for k in (*CONTENT_FIELDS,*TRACE_FIELDS))
    if changed:
        row['revision']+=1;row.pop('grounding_ack',None)
        if values.get('status')!='approved':status='needs_review' if old['status'] in ('approved','needs_review') else 'draft'
    if status=='approved':approve(s,row,values,note);row['reviewed_at']=store.now()
    row.update(status=status,review_note=store.clean(note))
    return {'case_id':cid}


def apply_proposal(s,cid,values):
    proposal=store.find(s['cases'],cid);selected_check(s,proposal,values.get('case_revision'),values.get('binding_fingerprint'))
    if not proposal.get('replaces_case_id') or proposal.get('applied_to') or proposal['status']=='rejected':raise ValueError('不是待应用建议')
    require_current(s,proposal);target=store.find(s['cases'],proposal['replaces_case_id'])
    if target['status']!='needs_review' or target['revision']!=proposal['replaces_case_revision'] or content_fingerprint(target)!=proposal['replaces_case_fingerprint']:
        raise FileExistsError('原用例已有人工修改或已处理，未应用过期建议')
    store.snapshot(s,'case',target)
    for k in (*CONTENT_FIELDS,*TRACE_FIELDS,'grounding'):
        if k in proposal:target[k]=copy.deepcopy(proposal[k])
        else:target.pop(k,None)
    target.pop('grounding_ack',None);target['revision']+=1;target.update(status='draft',review_note='')
    store.snapshot(s,'case',proposal);proposal.update(status='rejected',applied_to=target['id'])
    return {'case_id':target['id']}


def execution_current(s,case,result):
    return not is_point(case) or (not reasons(s,case) and all(case.get(k)==result['snapshot'].get(k) for k in TRACE_FIELDS))
