from datetime import datetime
from enum import StrEnum
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator


class LabName(StrEnum):
    healthy = "healthy"
    fault = "fault"


class JobInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=10000)
    request_id: UUID

    @field_validator("text")
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError("请输入有效文本")
        return value


class SubmitJob(JobInput):
    lab: LabName = LabName.healthy


class JobResult(BaseModel):
    char_count: int
    word_count: int


class LabJob(BaseModel):
    id: UUID
    request_id: UUID
    text: str
    status: str
    result: JobResult | None = None
    worker_version: str | None = None
    queue_error: str | None = None
    created_at: datetime
    updated_at: datetime


class ServiceStatus(BaseModel):
    service: str
    state: str
    health: str | None = None


class LabStatus(BaseModel):
    lab: LabName
    collected_at: datetime
    services: list[ServiceStatus]
    diagnostics: dict
    collection_error: str | None = None


class CollectionInput(BaseModel):
    request_id:UUID|None=None
    model_config = ConfigDict(extra="forbid")
    lab: LabName = LabName.fault


class Evidence(BaseModel):
    id: UUID
    collection_id: UUID
    lab: LabName
    source: str
    collected_at: datetime
    success: bool
    content: str
    sha256: str


class Collection(BaseModel):
    recovery:dict=Field(default_factory=dict)
    id: UUID
    lab: LabName
    status: str
    created_at: datetime
    finished_at: datetime | None = None
    error: str | None = None
    evidence: list[Evidence] = []


class ProgressEvent(BaseModel):
    id: int
    kind: str
    payload: dict
    created_at: datetime


class FaultCase(BaseModel):
    id: str
    title: str
    description: str
    collection_id: UUID | None = None
    sample_job_id: UUID | None = None
    observed_at: datetime | None = None


class Health(BaseModel):
    status: str
    delivery: str
    version: str|None=None
