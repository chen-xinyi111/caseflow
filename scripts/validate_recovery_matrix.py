"""Real fixed actions, with synthetic approval fixtures only in disposable schemas.

This verifies the recovery adapter, approval and probes, not model diagnosis.
No artificial investigation is inserted into production history.
"""
import json,sys
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
import lab_control as control
from validate_recovery import isolation
from opslens import db
from opslens.config import platform_dsn,secrets
from opslens.security import redact
from opslens.rag.store import jsonb
from opslens.recovery import store,worker
from opslens.recovery.contracts import Propose,Approve
from opslens.recovery.adapter import Adapter

def run():
    original=db.connect;baseline=isolation();records=[]
    cases=[('database_stopped','start_postgres'),('redis_stopped','start_redis'),('wrong_address','restore_worker_config'),
      ('wrong_version','rollback_worker_version'),('transaction_blocked','release_injected_blocker'),('api_stopped','start_api')]
    try:
        for case,action in cases:
            print('Real adapter check '+action,flush=True)
            if case=='api_stopped':control.restore();control.compose(['stop','api'])
            else:control.prepare(case)
            schema='recovery_live_'+uuid4().hex
            with original() as c:c.execute(psycopg.sql.SQL('CREATE SCHEMA {}').format(psycopg.sql.Identifier(schema)))
            def connect():return psycopg.connect(platform_dsn(),row_factory=dict_row,options=f'-c search_path={schema},public')
            db.connect=connect
            try:
                with connect() as c:
                    for file in ('schema.sql','rag/schema.sql','investigation/schema.sql','recovery/schema.sql'):c.execute((ROOT/'src/opslens'/file).read_text(encoding='utf-8'))
                    inv=uuid4()
                    c.execute("INSERT INTO investigations(id,request_id,status,input,report,verification) VALUES (%s,%s,'completed',%s,%s,%s)",
                     (inv,uuid4(),jsonb({'target':'fault','baseline':'controlled_recovery_test_fixture'}),jsonb({'assessment':'supported','summary':'Artificial approval prerequisite fixture, not a model diagnosis'}),jsonb({'status':'confirmed','source':'test_fixture'})))
                adapter=Adapter();p=store.create(Propose(request_id=uuid4(),investigation_id=inv,action=action),adapter)
                assert store.claim() is None
                store.approve(p['id'],Approve(request_id=uuid4(),version=p['version'],digest=p['digest'],confirm=True),actor='development_selfcheck')
                worker.process(store.claim(),adapter)
                with connect() as c:
                    saved=store.get(c,p['id']);n=c.execute('SELECT count(*) n FROM recovery_actions WHERE plan_id=%s',(p['id'],)).fetchone()['n']
                assert saved['status']=='completed',saved['error']
                assert saved['result']['passed'] and n==1
                records.append({'case':case,'action':action,'passed':True,'probe':saved['result']['probe'],'approval_actor':'development_selfcheck','prerequisite':'synthetic fixture in disposable schema, not real model output'})
            finally:
                db.connect=original
                with original() as c:c.execute(psycopg.sql.SQL('DROP SCHEMA {} CASCADE').format(psycopg.sql.Identifier(schema)))
        assert baseline==isolation(),'Other projects changed'
    finally:
        db.connect=original;control.client.close()
    path=ROOT/'.runtime'/('recovery-matrix-2.3-'+uuid4().hex+'.json')
    path.write_text(redact(json.dumps({'records':records,'model_calls':0,'production_investigation_fixtures':0,'other_projects_unchanged':True},ensure_ascii=False,default=str,indent=2),secrets()),encoding='utf-8')
    print('Six additional real recovery actions and business probes passed. '+path.name,flush=True)
if __name__=='__main__':run()
