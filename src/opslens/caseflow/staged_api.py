import copy
from uuid import UUID
from fastapi import APIRouter, HTTPException
from . import store, points, groups, documents
from .staged_contracts import GroupInput, WorkflowInput, PointInput, PointEdit, PointReviews, StagedRevision, RuleEvidenceInput
from opslens import db

router = APIRouter(tags=['CaseFlow basis and points'])


def checked(fn, *args, **kwargs):
    try: return fn(*args, **kwargs)
    except points.ReviewFailure as exc: raise HTTPException(400, exc.detail) from None
    except LookupError as exc: raise HTTPException(404, str(exc)) from None
    except FileExistsError as exc: raise HTTPException(409, str(exc)) from None
    except (ValueError, KeyError, TypeError, IndexError) as exc: raise HTTPException(400, str(exc) or '输入无效') from None


@router.post('/projects/{pid}/workflow')
def workflow(pid: UUID, data: WorkflowInput):
    return checked(lambda: store.public(store.update(pid, data.revision, 'workflow', lambda s: points.switch(s, data.mode))))


@router.post('/projects/{pid}/task-groups', status_code=202)
def create_group(pid: UUID, data: GroupInput):
    return checked(groups.create, pid, data.model_dump(mode='json'))


@router.get('/projects/{pid}/task-groups')
def group_list(pid: UUID, request_id: UUID | None = None):
    with db.connect() as c:
        checked(store.get, c, pid)
        rows = c.execute('SELECT * FROM cf_task_groups WHERE project_id=%s'+(' AND request_id=%s' if request_id else '')+' ORDER BY created_at DESC,id DESC'+('' if request_id else ' LIMIT 50'), (pid, *([request_id] if request_id else []))).fetchall()
        return [groups.present(g) for g in rows]


@router.get('/projects/{pid}/task-groups/{gid}')
def group_detail(pid: UUID, gid: UUID):
    with db.connect() as c:
        checked(store.get, c, pid)
        return groups.present(checked(groups.get, c, pid, gid))


@router.post('/projects/{pid}/task-groups/{gid}/resume')
def resume_group(pid: UUID, gid: UUID, data: StagedRevision):
    return checked(groups.resume, pid, gid, data.revision)


@router.get('/projects/{pid}/basis-bundles/{bid}')
def bundle_detail(pid: UUID, bid: UUID):
    with db.connect() as c:
        p = checked(store.get, c, pid)
        bundle = checked(store.find, p['state'].get('basis_bundles', []), bid)
        reasons = points.bundle_reasons(p['state'], bundle)
        return bundle | {'current': not reasons, 'stale_reasons': reasons,
                         'warning': '已选范围的处理完成不代表语义无遗漏，请核对依据与问题'}


@router.get('/projects/{pid}/test-points')
def point_list(pid: UUID, bundle_id: UUID | None = None, search: str = '', group: str = '', type: str = '',
               module: str = '', priority: str = '', status: str = '', needs_attention: bool = False):
    with db.connect() as c:
        p = checked(store.get, c, pid); s = p['state']
        rows = [points.present(s,r) for r in s.get('test_points', [])]
        if bundle_id: checked(store.find, s.get('basis_bundles', []), bundle_id)
        return [r for r in rows if (not bundle_id or r['basis_bundle_id']==str(bundle_id))
            and (not search or search.casefold() in (r['code']+' '+r['intent']+' '+' '.join(r['tags'])).casefold())
            and (not group or r['group']==group) and (not type or type in r['types']) and (not module or r['module']==module)
            and (not priority or r['priority']==priority) and (not status or r['status']==status) and (not needs_attention or r['needs_attention'])]


def validate_evidence(c, p, values):
    for eid in values['evidence_ids']: documents._evidence(c, p, eid)


@router.post('/projects/{pid}/test-points')
def add_point(pid: UUID, data: PointInput):
    values = data.model_dump(mode='json', exclude={'revision'})
    def write():
        with db.connect() as c:
            p = store.get(c, pid); validate_evidence(c, p, values)
        return store.public(store.update(pid, data.revision, 'manual_test_point', lambda s: {'point_id': points.add(s, values)['id']}))
    return checked(write)


@router.post('/projects/{pid}/test-points/{tid}')
def edit_point(pid: UUID, tid: UUID, data: PointEdit):
    values = data.model_dump(mode='json', exclude={'revision','point_revision','dependency_fingerprint'})
    def write():
        with db.connect() as c:
            p=store.get(c,pid);validate_evidence(c,p,values)
        return store.public(store.update(pid, data.revision, 'edit_test_point', lambda s: {'point_id': points.edit(s,str(tid),data.point_revision,data.dependency_fingerprint,values)['id']}))
    return checked(write)


@router.post('/projects/{pid}/test-point-reviews')
def review_points(pid: UUID, data: PointReviews):
    def write():
        with db.connect() as c:
            p=store.get(c,pid)
            failures=[]
            for item in data.items:
                try:
                    row=store.find(p['state'].get('test_points',[]),item.id);validate_evidence(c,p,row)
                except (LookupError,ValueError) as exc: failures.append({'id':str(item.id),'reason':str(exc)})
            if failures: raise points.ReviewFailure(failures)
        return store.public(store.update(pid,data.revision,'test_point_review',lambda s:points.review(s,
            [i.model_dump(mode='json') for i in data.items],data.decision,data.note)))
    return checked(write)


@router.post('/projects/{pid}/basis-bundles/{bid}/rule-evidence')
def bind_rule_evidence(pid: UUID, bid: UUID, data: RuleEvidenceInput):
    def write():
        with db.connect() as c:
            p=store.get(c,pid);validate_evidence(c,p,{'evidence_ids':data.evidence_ids})
        def fork(s):
            old=store.find(s.get('basis_bundles',[]),bid);rid=str(data.rule_id)
            rule=store.find(s['requirements'],rid);ids=[str(e) for e in data.evidence_ids]
            if rid not in old['rule_evidence']: raise ValueError('规则不在准备包')
            if any(e not in old['evidence_catalog'] or old['evidence_catalog'][e]['document_id']!=rule['document_id'] for e in ids):
                raise ValueError('请明确选择这条规则所属版本的已回读证据')
            if not any(i.get('kind')=='ambiguous_reference' and rid in i.get('requirement_ids',[]) for i in old['issues']):
                raise ValueError('当前规则没有待消除的定位歧义')
            new=copy.deepcopy(old);new.update(id=store.uid(),previous_bundle_id=old['id'],created_at=store.now())
            new['rule_evidence'][rid]=ids
            new['issues']=[i for i in new['issues'] if not (i.get('kind')=='ambiguous_reference' and rid in i.get('requirement_ids',[]))]
            new.setdefault('human_bindings',[]).append({'rule_id':rid,'evidence_ids':ids,'note':store.clean(data.note),'actor':'local_user','at':store.now()})
            s['basis_bundles'].append(new);return {'basis_bundle_id':new['id']}
        return store.public(store.update(pid,data.revision,'bind_rule_evidence',fork))
    return checked(write)
