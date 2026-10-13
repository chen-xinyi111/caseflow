"""Local evaluation CLI. No execution/approval endpoint is exposed in the UI."""
import argparse,json,hashlib,sys
from pathlib import Path
from uuid import UUID,uuid4
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from opslens import db
from opslens.config import LABS,secrets
from opslens.collector import collect,docker
from opslens.security import redact
from opslens.rag.store import jsonb
from opslens.evaluation import store
from opslens.evaluation.dataset import digest
from opslens.evaluation.runner import run
STATE=ROOT/'.runtime/evaluation-3.2.json'
def current():return json.loads(STATE.read_text(encoding='utf-8'))['run_id']
def initialize():
    db.initialize()
    if STATE.exists():
        print('Existing run '+current());return
    with db.connect() as c:index=c.execute("SELECT id FROM kb_indexes WHERE mode='semantic' AND status='ready' AND active ORDER BY finished_at DESC LIMIT 1").fetchone()
    if not index:raise ValueError('请先准备内置手册语义索引')
    row=store.create(uuid4(),index['id'])
    STATE.write_text(json.dumps({'run_id':str(row['id'])},indent=2),encoding='utf-8');print('Created '+str(row['id']))
def isolation():
    result={}
    for project in ('opslens-lab-healthy','agent-commerce-stage3'):
        ids=docker(['ps','-aq','--filter','label=com.docker.compose.project='+project]).split()
        for i in ids:
            obj=json.loads(docker(['inspect',i]))[0]
            result[i]={'state':obj['State']['Status'],'started_at':obj['State']['StartedAt'],'image':obj['Image']}
    return result
def capture():
    import httpx,lab_control
    from opslens.evaluation.catalog import FAULTS
    run_id=current();before=isolation()
    # Capture directly; do not overwrite the original fault_cases pointers.
    def evidence_case(case_id,job_id):
        evidence=collect('fault');cid=uuid4()
        if job_id:
            with httpx.Client(trust_env=False,timeout=8) as c:
                response=c.get(LABS['fault']['url']+'/jobs/'+job_id)
                response.raise_for_status();value=response.json()
            assert value['text']==lab_control.TEXT
            content=redact(json.dumps(value,ensure_ascii=False,indent=2),secrets())
            evidence.append({'source':'lab.job_snapshot','success':True,'content':content,'collected_at':datetime.now(timezone.utc),'sha256':hashlib.sha256(content.encode()).hexdigest(),'lab':'fault'})
        records=[]
        with db.connect() as c:
            c.execute("INSERT INTO collections(id,lab,status,finished_at) VALUES (%s,'fault','completed',now())",(cid,))
            for e in evidence:
                i=uuid4();c.execute('INSERT INTO evidence(id,collection_id,lab,source,collected_at,success,content,sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',(i,cid,'fault',e['source'],e['collected_at'],e['success'],e['content'],e['sha256']))
                records.append(e|{'id':str(i),'collected_at':str(e['collected_at'])})
            payload={'evidence':records,'job_id':job_id,'collection_id':str(cid)}
            c.execute('INSERT INTO evaluation_snapshots(run_id,source_group,payload,sha256) VALUES (%s,%s,%s,%s)',(run_id,case_id,jsonb(payload),digest(payload)))
        return {'id':str(cid),'status':'completed','evidence':records}
    lab_control.evidence_case=evidence_case
    try:
        for group in FAULTS:
            with db.connect() as c:exists=c.execute('SELECT 1 FROM evaluation_snapshots WHERE run_id=%s AND source_group=%s',(run_id,group)).fetchone()
            if exists:continue
            print('Capturing actual fault '+group,flush=True)
            prepared=lab_control.prepare(group)
            out=ROOT/'.runtime'/('evaluation-observation-'+group+'-'+uuid4().hex+'.json')
            out.write_text(redact(json.dumps(prepared,ensure_ascii=False,indent=2,default=str),secrets()),encoding='utf-8')
    finally:
        restored=lab_control.restore();after=isolation()
        proof={'before':before,'after':after,'other_projects_unchanged':before==after,'fault_restored_probe':restored,'scope':'实验准备后复位，不是平台审批处置评测'}
        (ROOT/'.runtime/evaluation-isolation-3.2.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        if before!=after:raise ValueError('非故障环境的隔离检查不一致')
        lab_control.client.close()
    print('Six real snapshots saved; fault restored; other projects unchanged.')
def export():
    with db.connect() as c:report=store.report(c,current());items=store.rows(c,current())
    (ROOT/'.runtime/evaluation-report-3.2.json').write_text(json.dumps({'report':report,'items':items},ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'run_id':current(),'status':report['status'],'summary':report['summary'],'paired':report['paired']},ensure_ascii=False,default=str))
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['init','capture','run','report']);parser.add_argument('--kind',choices=['retrieval','investigation'],default='retrieval');parser.add_argument('--split',choices=['dev','test','all'],default='dev');parser.add_argument('--modes',nargs='+');parser.add_argument('--limit',type=int);parser.add_argument('--pilot',action='store_true');args=parser.parse_args()
    if args.action=='init':initialize()
    elif args.action=='capture':capture()
    elif args.action=='report':export()
    else:run(UUID(current()),args.kind,args.split,args.modes or list(store.MODES[args.kind]),args.limit,args.pilot)
