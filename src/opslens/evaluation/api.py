from uuid import UUID
from fastapi import APIRouter,HTTPException
from opslens import db
from opslens.evaluation import store
from opslens.evaluation.contracts import Run,Item
router=APIRouter(prefix='/api/v1/evaluations',tags=['evaluation'])
@router.get('',response_model=list[Run])
def runs():
    with db.connect() as c:return c.execute('SELECT * FROM evaluation_runs ORDER BY created_at DESC LIMIT 30').fetchall()
@router.get('/{run_id}',response_model=Run)
def report(run_id:UUID):
    try:
        with db.connect() as c:return store.report(c,run_id)
    except LookupError as e:raise HTTPException(404,str(e)) from None
@router.get('/{run_id}/items',response_model=list[Item])
def items(run_id:UUID,kind:str|None=None,split:str|None=None,mode:str|None=None,status:str|None=None):
    with db.connect() as c:
        if not c.execute('SELECT 1 FROM evaluation_runs WHERE id=%s',(run_id,)).fetchone():raise HTTPException(404,'评测运行不存在')
        rows=store.rows(c,run_id)
        return [r for r in rows if all(v is None or r[k]==v for k,v in [('kind',kind),('split',split),('mode',mode),('status',status)])]
@router.get('/{run_id}/items/{item_id}',response_model=Item)
def item(run_id:UUID,item_id:UUID):
    try:
        with db.connect() as c:return store.detail(c,run_id,item_id)
    except LookupError as e:raise HTTPException(404,str(e)) from None
