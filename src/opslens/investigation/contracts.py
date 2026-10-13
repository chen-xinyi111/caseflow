from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
class Input(BaseModel):
    model_config=ConfigDict(extra="forbid")
    request_id:UUID
    question:str=Field(min_length=1,max_length=1000)
    target:Literal["fault","healthy","unconnected","external"]="fault"
    job_id:UUID|None=None
    mode:Literal["single","multi","verified"]="single"
    snapshot_id:UUID|None=None
    external_target_id:UUID|None=None
    external_snapshot_id:UUID|None=None
    external_consent_id:UUID|None=None
    @model_validator(mode='after')
    def external_fields(self):
        fields=[self.external_target_id,self.external_snapshot_id,self.external_consent_id]
        if self.target=='external':
            if not all(fields) or self.job_id or self.snapshot_id:raise ValueError('外部调查需要项目、快照和发送确认，不支持业务任务或实验快照')
        elif any(fields):raise ValueError('外部资料只能用于外部项目调查')
        return self
    @field_validator("question")
    @classmethod
    def meaningful(cls,v):
        if not v.strip():raise ValueError("请填写调查问题")
        return v.strip()
class ConflictExample(BaseModel):
    available:bool
    reason:str
    target:str='fault'
    question:str|None=None
    snapshot_id:UUID|None=None
    job_id:UUID|None=None
    collected_at:datetime|None=None
class Quote(BaseModel):
    model_config=ConfigDict(extra="forbid")
    id:str=Field(min_length=1,max_length=100)
    quote:str=Field(min_length=2,max_length=300)
class Finding(BaseModel):
    model_config=ConfigDict(extra="forbid")
    statement:str=Field(min_length=1,max_length=800)
    references:list[Quote]=Field(min_length=1,max_length=6)
class Report(BaseModel):
    model_config=ConfigDict(extra="forbid")
    assessment:Literal["supported","insufficient"]
    summary:str=Field(min_length=1,max_length=1500)
    root_cause:str|None=Field(default=None,max_length=1000)
    findings:list[Finding]=Field(default_factory=list,max_length=8)
    missing_information:list[str]=Field(default_factory=list,max_length=8)
    next_steps:list[str]=Field(default_factory=list,max_length=6)
class Reference(BaseModel):
    id:str
    kind:Literal["runtime","knowledge"]
    source:str
    content:str
    url:str
    collected_at:datetime|None=None
    doc_version:str|None=None
    index_id:UUID|None=None
    truncated:bool=False
    scope:Literal['current','historical']='current'
    applicable_versions:list[str]=Field(default_factory=list)
class SpecialistOutput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    summary:str=Field(min_length=1,max_length=1000)
    findings:list[Finding]=Field(default_factory=list,max_length=4)
    missing_information:list[str]=Field(default_factory=list,max_length=6)
class SubtaskProgress(BaseModel):
    id:str
    role:Literal["runtime","knowledge"]
    objective:str
    depends_on:list[str]
    status:Literal["pending","scheduled","running","completed","failed","skipped"]
    started_at:datetime|None=None
    finished_at:datetime|None=None
    output:SpecialistOutput|None=None
    error:str|None=None
class Collaboration(BaseModel):
    reason:str=""
    tasks:list[SubtaskProgress]=Field(default_factory=list)
    merge_status:Literal["waiting","running","completed","insufficient","failed","blocked"]="waiting"
class VerificationIssue(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind:Literal['stale_snapshot','runtime_conflict','unsupported','version','missing']
    statement:str=Field(min_length=1,max_length=800)
    references:list[Quote]=Field(default_factory=list,max_length=6)
    resolved:bool=False
    resolution:str|None=Field(default=None,max_length=800)
class Verdict(BaseModel):
    model_config=ConfigDict(extra='forbid')
    decision:Literal['confirmed','rejected','uncertain']
    reason:str=Field(min_length=1,max_length=1200)
    report:Report
    issues:list[VerificationIssue]=Field(default_factory=list,max_length=8)
class VerificationRound(BaseModel):
    number:int
    candidate:Report
    started_at:datetime
    finished_at:datetime|None=None
    verdict:Verdict|None=None
    error:str|None=None
class ReplanRecord(BaseModel):
    number:int
    action:str
    reason:str
    task_ids:list[str]=Field(default_factory=list)
    questions:list[str]=Field(default_factory=list)
    status:str='planned'
class Verification(BaseModel):
    status:Literal['waiting','running','confirmed','needs_information','not_required','failed','blocked','interrupted']='waiting'
    rounds:list[VerificationRound]=Field(default_factory=list)
    replans:list[ReplanRecord]=Field(default_factory=list)
    conflicts:list[VerificationIssue]=Field(default_factory=list)
    questions:list[str]=Field(default_factory=list)
    stop_reason:str|None=None
class Investigation(BaseModel):
    recovery:dict=Field(default_factory=dict)
    id:UUID
    request_id:UUID
    status:str
    input:dict
    report:Report|None
    references:list[Reference]
    metrics:dict
    collaboration:Collaboration=Field(default_factory=Collaboration)
    verification:Verification=Field(default_factory=Verification)
    error:str|None
    created_at:datetime
    finished_at:datetime|None
