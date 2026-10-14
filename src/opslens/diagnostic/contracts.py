from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel,ConfigDict,Field

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')

class SessionInput(Strict):
    request_id:UUID
    problem:str=Field(default='',max_length=4000)
    project:str=Field(default='',max_length=100)
    environment:str=Field(default='',max_length=1000)

class Session(BaseModel):
    id:UUID
    problem:str
    project:str
    environment:str
    status:Literal['open','solved','paused']
    created_at:datetime

class CheckReference(Strict):
    turn_id:UUID
    check_index:int=Field(ge=1,le=3)

class SourceInput(Strict):
    request_id:UUID
    name:str=Field(default='粘贴的报错',min_length=1,max_length=200)
    kind:Literal['log','document','check_result']='log'
    content:str=Field(min_length=1,max_length=5242880)
    check_reference:CheckReference|None=None

class ErrorGroup(BaseModel):
    signature:str
    count:int
    first_line:int
    last_line:int

class Summary(BaseModel):
    lines:int
    bytes:int
    groups:list[ErrorGroup]
    message:str
    links:list[str]

class Source(BaseModel):
    id:UUID
    session_id:UUID
    name:str
    kind:str
    version:int
    sha256:str
    summary:Summary
    provenance:dict
    created_at:datetime

class Original(Source):
    content:str

class LineRange(Strict):
    source_id:UUID
    start_line:int=Field(ge=1)
    end_line:int=Field(ge=1)

class PreviewInput(Strict):
    request_id:UUID
    source_ids:list[UUID]=Field(min_length=1,max_length=20)
    ranges:list[LineRange]=Field(default_factory=list,max_length=40)

class Segment(BaseModel):
    id:str
    source_id:UUID
    name:str
    kind:str
    version:int
    start_line:int
    end_line:int
    content:str
    omitted_characters:int
    provenance:dict

class Preview(BaseModel):
    id:UUID
    session_id:UUID
    config_id:UUID
    digest:str
    segments:list[Segment]
    coverage:dict
    created_at:datetime
    continuation:dict=Field(default_factory=dict)
    analysis_prompt_version:str|None=None

class AnalyzeInput(Strict):
    request_id:UUID
    preview_id:UUID
    digest:str=Field(pattern=r'^[a-f0-9]{64}$')
    confirm:bool

class DiagnosticCitation(Strict):
    id:str
    quote:str=Field(min_length=2,max_length=300)

class Fact(Strict):
    text:str=Field(min_length=1,max_length=800)
    citations:list[DiagnosticCitation]=Field(min_length=1,max_length=6)

class Cause(Strict):
    title:str=Field(min_length=1,max_length=300)
    reason:str=Field(min_length=1,max_length=800)
    citations:list[DiagnosticCitation]=Field(default_factory=list,max_length=6)

class Check(Strict):
    purpose:str=Field(min_length=1,max_length=500)
    operation:str=Field(min_length=1,max_length=1500)
    expected:str=Field(min_length=1,max_length=800)
    changes_state:bool

class Report(Strict):
    summary:str=Field(min_length=1,max_length=1000)
    facts:list[Fact]=Field(default_factory=list,max_length=8)
    candidate_causes:list[Cause]=Field(default_factory=list,max_length=5)
    next_checks:list[Check]=Field(default_factory=list,max_length=3)
    missing_information:list[str]=Field(default_factory=list,max_length=5)

class Turn(BaseModel):
    id:UUID
    session_id:UUID
    preview_id:UUID
    status:str
    report:Report|None=None
    metrics:dict
    error:str|None=None
    created_at:datetime
    finished_at:datetime|None=None

class FeedbackInput(Strict):
    request_id:UUID
    status:Literal['open','solved','paused']
    note:str=Field(default='',max_length=4000)

class ModelSettings(Strict):
    mode:Literal['free','compatible']='free'
    enabled:bool=False
    base_url:str=Field(default='https://dashscope.aliyuncs.com/api/v1',max_length=500)
    model:str=Field(default='qwen3.8-max',min_length=1,max_length=100)
    key_env:str=Field(default='DASHSCOPE_APIKEY',pattern=r'^[A-Z][A-Z0-9_]{0,63}$')
    daily_limit:int=Field(default=20,ge=1,le=200)
    max_output:int=Field(default=2048,ge=256,le=2048)
    official_hosts:list[str]=Field(default_factory=lambda:['docs.python.org','fastapi.tiangolo.com','www.uvicorn.org','nodejs.org','docs.docker.com','www.postgresql.org','redis.io','docs.spring.io'],max_length=30)

class Settings(BaseModel):
    id:UUID
    version:int
    settings:ModelSettings
    available:bool
    reason:str
    used_today:int

class LinkInput(Strict):
    request_id:UUID
    url:str=Field(min_length=1,max_length=2000)

class ReferenceInput(Strict):
    request_id:UUID
    reference_session_id:UUID

class DockerInput(Strict):
    request_id:UUID
    target_id:UUID
