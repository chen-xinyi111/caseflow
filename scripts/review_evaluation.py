"""Append explicit Codex fact reviews; never alter original answers or rule scores."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from opslens import db
from opslens.evaluation.dataset import digest
from opslens.rag.store import jsonb
def current():return json.loads((ROOT/'.runtime/evaluation-3.2.json').read_text())['run_id']
def show(group):
    with db.connect() as c:
        rows=c.execute("SELECT id,case_id,mode,status,result,error FROM evaluation_items WHERE run_id=%s AND kind='investigation' AND source_group=%s AND status IN ('completed','failed','blocked') ORDER BY case_id,mode",(current(),group)).fetchall()
    for row in rows:
        report=(row.pop('result') or {}).get('report') or {}
        row.update(assessment=report.get('assessment'),root=report.get('root_cause'),summary=report.get('summary'),missing=report.get('missing_information'),findings=[{'index':i,'statement':f['statement'],'quotes':f['references']} for i,f in enumerate(report.get('findings',[]))])
        print(json.dumps(row,ensure_ascii=False,default=str))
def save(path):
    decisions=json.loads(Path(path).read_text(encoding='utf-8'))
    with db.connect() as c:
        for key,review in decisions.items():
            case_id,mode=key.rsplit(':',1)
            item=c.execute("SELECT * FROM evaluation_items WHERE run_id=%s AND case_id=%s AND mode=%s AND status='completed'",(current(),case_id,mode)).fetchone()
            if not item:raise ValueError('复核对象未完成：'+key)
            findings=item['result']['report']['findings'];flags=review['finding_support']
            if len(flags)!=len(findings) or not all(isinstance(v,bool) for v in flags) or not isinstance(review['root_correct'],bool):raise ValueError('逐条复核数量/类型不符：'+key)
            snap=c.execute('SELECT sha256 FROM evaluation_snapshots WHERE run_id=%s AND source_group=%s',(current(),item['source_group'])).fetchone()
            result=review|{'quote_reviewed':sum(len(f['references']) for f in findings),'quote_supported':sum(len(f['references']) for f,s in zip(findings,flags) if s),'basis_snapshot_sha256':snap['sha256'],'original_result_sha256':digest(item['result']),'method':'Codex逐条对照实际故障快照、任务原文与来源手册；按完整引用原文是否支持声明核对，不使用被测模型自评，不等于外部人工专家审稿'}
            old=c.execute('SELECT review FROM evaluation_reviews WHERE item_id=%s',(item['id'],)).fetchone()
            if old and old['review']!=result:raise ValueError('已有复核不可覆盖：'+key)
            c.execute('INSERT INTO evaluation_reviews(item_id,reviewer,review) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',(item['id'],'Codex事实复核',jsonb(result)))
    print('Saved explicit reviews:',len(decisions))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['show','save']);p.add_argument('value');a=p.parse_args()
    show(a.value) if a.action=='show' else save(a.value)
