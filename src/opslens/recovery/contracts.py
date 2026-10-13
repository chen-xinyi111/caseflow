from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel,ConfigDict,Field

Action=Literal['start_worker','start_postgres','start_redis','start_api','restore_worker_config','rollback_worker_version','release_injected_blocker']
class Propose(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:UUID
    investigation_id:UUID
    action:Action
class Approve(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:UUID
    version:int=Field(ge=1)
    digest:str=Field(pattern=r'^[0-9a-f]{64}$')
    confirm:Literal[True]
class Plan(BaseModel):
    recovery:dict=Field(default_factory=dict)
    id:UUID
    request_id:UUID
    investigation_id:UUID
    version:int
    action:Action
    target:Literal['fault']='fault'
    title:str
    explanation:str
    risk:str
    digest:str
    before:dict
    status:str
    approval:dict|None=None
    execution:dict=Field(default_factory=dict)
    result:dict|None=None
    error:str|None=None
    created_at:datetime
    expires_at:datetime
    finished_at:datetime|None=None
