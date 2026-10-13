from datetime import datetime
from uuid import UUID
from pydantic import BaseModel,Field
class Run(BaseModel):
    id:UUID
    title:str
    status:str
    created_at:datetime
    finished_at:datetime|None=None
    manifest:dict
    summary:list[dict]=Field(default_factory=list)
    paired:list[dict]=Field(default_factory=list)
    historical_recovery:dict=Field(default_factory=dict)
    failure_counts:dict=Field(default_factory=dict)
class Item(BaseModel):
    id:UUID
    case_id:str
    kind:str
    mode:str
    split:str
    source_group:str
    status:str
    input:dict
    result:dict|None=None
    references:list[dict]=Field(default_factory=list)
    score:dict|None=None
    metrics:dict=Field(default_factory=dict)
    error:str|None=None
    steps:list[dict]=Field(default_factory=list)
    expected:dict=Field(default_factory=dict)
    review:dict=Field(default_factory=dict)
