from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator

class Chunk(BaseModel):
    id: UUID
    document_id: UUID
    ordinal: int
    page: int
    start_char: int
    end_char: int
    line_start: int
    line_end: int
    heading: str
    content: str
    sha256: str

class Document(BaseModel):
    id: UUID
    title: str
    source: str
    doc_version: str
    applicable_versions: list[str]
    filename: str
    format: str
    sha256: str
    created_at: datetime
    chunk_count: int = 0

class DocumentDetail(Document):
    pages: list[str]
    chunks: list[Chunk]

class IndexInput(BaseModel):
    request_id:UUID|None=None
    model_config = ConfigDict(extra="forbid")
    mode: Literal["keyword","semantic"] = "keyword"
    document_ids: list[UUID] | None = Field(default=None,max_length=100)

class SearchInput(BaseModel):
    request_id:UUID|None=None
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1,max_length=1000)
    mode: Literal["keyword","vector","hybrid","rerank"] = "keyword"
    app_version: str = Field(default="v1",min_length=1,max_length=100)
    index_id: UUID | None = None

    @field_validator("query")
    @classmethod
    def meaningful(cls,value):
        if not value.strip(): raise ValueError("请输入有效检索问题")
        return value.strip()

class Index(BaseModel):
    id: UUID
    mode: str
    status: str
    document_ids: list[UUID]
    parameters: dict
    manifest_sha256: str
    embedding_model: str | None
    dimensions: int | None
    active: bool
    created_at: datetime
    finished_at: datetime | None
    error: str | None

class SearchHit(BaseModel):
    chunk: Chunk
    document: Document
    index_id: UUID
    citation_url: str
    routes: list[str]
    bm25_score: float | None = None
    vector_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None

class SearchResult(BaseModel):
    query: str
    mode: str
    app_version: str
    index_id: UUID
    hits: list[SearchHit]
    counts: dict[str,int]
    elapsed_ms: float
    embedding_model: str | None = None
    rerank_model: str | None = None
    note: str

class IndexResult(BaseModel):
    index_id: UUID
    mode: str
    chunks: int
    embedding_model: str | None
    dimensions: int | None

class ModelReadiness(BaseModel):
    available: bool
    reason: str

class Task(BaseModel):
    recovery:dict=Field(default_factory=dict)
    id: UUID
    kind: str
    status: str
    input: dict
    result: SearchResult | IndexResult | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None

class Citation(BaseModel):
    chunk: Chunk
    document: Document
    page_text: str

class KBStatus(BaseModel):
    documents: int
    chunks: int
    indexes: list[Index]
    models: dict[str,ModelReadiness]
