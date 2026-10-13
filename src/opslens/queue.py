import asyncio
import logging
from uuid import uuid4
from opslens import db
from opslens.collector import collect
from opslens.config import secrets
from opslens.security import redact

logger = logging.getLogger(__name__)


from opslens import durability
from opslens.durability import Runtime,LeaseLost
from opslens.rag.store import jsonb

def claim():return durability.claim('collection')

def process(task):
    rt=Runtime('collection',task)
    try:
        with rt.live():
            with rt.transaction() as c:
                saved=c.execute('SELECT source FROM evidence WHERE collection_id=%s',(task['id'],)).fetchall()
            def persist(item):
                with rt.transaction() as c:
                    eid=uuid4()
                    c.execute('INSERT INTO evidence(id,collection_id,lab,source,collected_at,success,content,sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',(eid,task['id'],item['lab'],item['source'],item['collected_at'],item['success'],item['content'],item['sha256']))
                    c.execute('UPDATE durable_tasks SET checkpoint=%s WHERE family=%s AND task_id=%s',(jsonb({'step':item['source'],'state':'completed'}),'collection',task['id']))
                    db.append_event(c,task['id'],'evidence',{'evidence_id':str(eid),'source':item['source'],'success':item['success'],'message':'采集来源已保存'})
            collect(task['lab'],sink=persist,skip=[r['source'] for r in saved])
            with rt.transaction() as c:
                rows=c.execute('SELECT success FROM evidence WHERE collection_id=%s',(task['id'],)).fetchall()
            success=sum(r['success'] for r in rows)
            rt.finish('completed' if success==len(rows) and rows else 'partial' if success else 'failed')
    except LeaseLost:return
    except Exception as exc:
        try:rt.finish('failed',error=redact(str(exc),secrets())[:2000])
        except LeaseLost:return

async def worker():
    while True:
        try:
            task = await asyncio.to_thread(claim)
            if task:
                await asyncio.to_thread(process, task)
            else:
                await asyncio.sleep(0.4)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Evidence queue temporarily unavailable")
            await asyncio.sleep(1)
