"""Local raw archive; excluded from Git and never sent to any service."""
import gzip,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from opslens import db
from opslens.evaluation import store
def main():
    run_id=json.loads((ROOT/'.runtime/evaluation-3.2.json').read_text())['run_id']
    with db.connect() as c:
        report=store.report(c,run_id);development=report['manifest']['development_run_id'];ids=[run_id,development]
        archive={'reports':[store.report(c,i) for i in ids],
          'items':c.execute('SELECT * FROM evaluation_items WHERE run_id=ANY(%s::uuid[]) ORDER BY run_id,case_id,mode',(ids,)).fetchall(),
          'events':c.execute('SELECT e.* FROM evaluation_events e JOIN evaluation_items i ON i.id=e.item_id WHERE i.run_id=ANY(%s::uuid[]) ORDER BY e.id',(ids,)).fetchall(),
          'snapshots':c.execute('SELECT * FROM evaluation_snapshots WHERE run_id=ANY(%s::uuid[])',(ids,)).fetchall(),
          'reviews':c.execute('SELECT r.* FROM evaluation_reviews r JOIN evaluation_items i ON i.id=r.item_id WHERE i.run_id=ANY(%s::uuid[])',(ids,)).fetchall(),
          'model_calls':c.execute('SELECT m.* FROM kb_model_calls m WHERE m.id IN (SELECT e.call_id FROM evaluation_calls e JOIN evaluation_items i ON i.id=e.item_id WHERE i.run_id=ANY(%s::uuid[])) ORDER BY m.created_at',(ids,)).fetchall()}
    target=ROOT/'.runtime/evaluation-raw-3.2.json.gz'
    with gzip.open(target,'wt',encoding='utf-8') as f:json.dump(archive,f,ensure_ascii=False,default=str)
    print('Local raw archive saved:',len(archive['model_calls']),'model requests;',len(archive['reviews']),'explicit fact reviews')
if __name__=='__main__':main()
