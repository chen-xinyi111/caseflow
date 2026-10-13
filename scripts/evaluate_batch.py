"""Two independent case pairs at most; immutable engine, fixed free models."""
import argparse,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from opslens import db,durability
from opslens.evaluation import store
from opslens.evaluation.runner import process
from opslens.evaluation.batch import execute_pairs
from opslens.evaluation.dataset import verify_freeze
from opslens.rag.store import jsonb
def main(kind,split):
    run_id=json.loads((ROOT/'.runtime/evaluation-3.2.json').read_text())['run_id'];verify_freeze();store.seal_execution(run_id)
    with db.connect() as lock:
        if not lock.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,3202)) ok',(run_id,)).fetchone()['ok']:raise ValueError('该评测已有执行进程')
        try:
            with db.connect() as c:
                row=c.execute('SELECT manifest FROM evaluation_runs WHERE id=%s FOR UPDATE',(run_id,)).fetchone();manifest=row['manifest']
                executor={'workers':2,'files':{str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),ROOT/'src/opslens/evaluation/batch.py']},'note':'案例内方式顺序固定，最多两组独立案例并行；耗时包含共享连接池等待，不视为独占资源性能基准'}
                if manifest.get('batch_executor') and manifest['batch_executor']!=executor:raise ValueError('批处理执行器已变化')
                manifest['batch_executor']=executor;c.execute('UPDATE evaluation_runs SET manifest=%s WHERE id=%s',(jsonb(manifest),run_id))
                rows=[r for r in store.rows(c,run_id) if r['kind']==kind and (split=='all' or r['split']==split)]
            groups=[]
            for n,case_id in enumerate(sorted({r['case_id'] for r in rows})):
                order=list(store.MODES[kind]);order=order[n%len(order):]+order[:n%len(order)]
                groups.append(sorted([r for r in rows if r['case_id']==case_id],key=lambda r:order.index(r['mode'])))
            def run_one(row):
                if row['status'] not in ('pending','running'):return True
                task=durability.claim('evaluation',row['id'])
                if task:process(task)
                with db.connect() as c:current=c.execute('SELECT status,error FROM evaluation_items WHERE id=%s',(row['id'],)).fetchone()
                print(row['case_id']+' '+row['mode']+' '+current['status'],flush=True)
                if current['status']=='blocked':print(current['error'],flush=True);return False
                return True
            execute_pairs(groups,run_one)
        finally:
            lock.execute('SELECT pg_advisory_unlock(hashtextextended(%s,3202))',(run_id,));store.finalize(run_id)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--kind',choices=['retrieval','investigation'],required=True);p.add_argument('--split',choices=['dev','test','all'],default='all');a=p.parse_args();main(a.kind,a.split)
