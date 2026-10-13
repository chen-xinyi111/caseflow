"""Read-only pointers to prepared local demos; no mutation endpoint."""
import json
from pathlib import Path
from typing import Literal
from uuid import UUID
from fastapi import APIRouter
from pydantic import BaseModel,ConfigDict,ValidationError
from opslens.config import ROOT
class Demo(BaseModel):
    model_config=ConfigDict(extra='ignore')
    available:bool=False
    reason:str='演示尚未准备，请运行scripts/local/Demo.ps1。'
    response_source:Literal['controlled-demo-3.3']='controlled-demo-3.3'
    success_id:UUID|None=None
    insufficient_id:UUID|None=None
    checked_plan_id:UUID|None=None
    prepared_at:str|None=None
    model_api_calls:int=0

def read_demo(path=None):
    try:
        row=Demo.model_validate_json(Path(path or ROOT/'.runtime/demo-3.3.json').read_text(encoding='utf-8'))
        if row.available and (not row.success_id or not row.insufficient_id or row.model_api_calls!=0):return Demo(reason='演示清单不完整，请重新运行Demo.ps1。')
        return row
    except (OSError,ValidationError):return Demo()
router=APIRouter(prefix='/api/v1')
@router.get('/demo',response_model=Demo)
def get_demo():return read_demo()
