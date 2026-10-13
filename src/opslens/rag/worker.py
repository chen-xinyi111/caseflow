import asyncio
import logging
from opslens import db
from opslens.config import secrets
from opslens.security import redact
from opslens.rag import store
from opslens.rag.service import build_index, search
from opslens.rag.provider import ModelBlocked

logger=logging.getLogger(__name__)


from opslens import durability
from opslens.durability import Runtime,LeaseLost

def claim():return durability.claim('knowledge')

def process(task):
    rt=Runtime('knowledge',task)
    try:
        with rt.live():
            result=rt.operation('knowledge:result',lambda:build_index(task['input']['index_id']) if task['kind']=='index' else search(task['input']))
            rt.finish('completed',result=result)
    except LeaseLost:return
    except Exception as exc:
        status='blocked' if isinstance(exc,ModelBlocked) else 'failed'
        error=redact(str(exc),secrets())[:1500]
        try:
            with rt.transaction(deadline=False) as conn:
                if task['kind']=='index':conn.execute("UPDATE kb_indexes SET status='failed',error=%s,finished_at=now() WHERE id=%s AND status='building'",(error,task['input']['index_id']))
            rt.finish(status,error=error)
        except LeaseLost:return

async def worker():
    while True:
        try:
            task=await asyncio.to_thread(claim)
            if task: await asyncio.to_thread(process,task)
            else: await asyncio.sleep(.4)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("Knowledge queue unavailable; retrying without model calls")
            await asyncio.sleep(1)
