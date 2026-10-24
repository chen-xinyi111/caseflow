from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StrictInt


class StagedStrict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class PointContent(StagedStrict):
    intent: str = Field(min_length=1, max_length=1200)
    group: str = Field(default='未分组', min_length=1, max_length=100)
    types: list[Literal['正常','异常','边界','权限','状态','界面','接口','性能','安全']] = Field(default_factory=lambda:['正常'], min_length=1, max_length=3)
    module: str = Field(default='未分组', min_length=1, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=10)
    priority: Literal['P1','P2','P3'] = 'P2'
    rule_ids: list[UUID] = Field(default_factory=list, max_length=8)
    basis_item_ids: list[UUID] = Field(default_factory=list, max_length=30)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=60)
    questions: list[str] = Field(default_factory=list, max_length=20)


class PointInput(PointContent):
    revision: StrictInt = Field(ge=1)
    basis_bundle_id: UUID


class PointEdit(PointInput):
    point_revision: StrictInt = Field(ge=1)
    dependency_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')


class PointReviewItem(StagedStrict):
    id: UUID
    point_revision: StrictInt = Field(ge=1)
    dependency_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    reviewed_fields: list[Literal['intent','expected_direction','rule_applicability','citations']] = Field(default_factory=list, max_length=4)
    semantic_resolution: str = Field(default='', max_length=2000)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=60)


class PointReviews(StagedStrict):
    revision: StrictInt = Field(ge=1)
    items: list[PointReviewItem] = Field(min_length=1, max_length=100)
    decision: Literal['approved','rejected']
    note: str = Field(min_length=1, max_length=2000)


class GroupInput(StagedStrict):
    revision: StrictInt = Field(ge=1)
    request_id: UUID
    kind: Literal['prepare_basis','extract_points']
    consent: bool
    target: str = Field(default='', max_length=1000)
    document_ids: list[UUID] = Field(default_factory=list, max_length=100)
    rule_ids: list[UUID] = Field(default_factory=list, max_length=100)
    basis_bundle_id: UUID | None = None


class WorkflowInput(StagedStrict):
    revision: StrictInt = Field(ge=1)
    mode: Literal['standard','strict']


class StagedRevision(StagedStrict):
    revision: StrictInt = Field(ge=1)


class PreparedRule(StagedStrict):
    text: str = Field(min_length=1, max_length=3000)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=8)
    questions: list[str] = Field(default_factory=list, max_length=6)


class PreparedItem(PreparedRule):
    applicability: str = Field(min_length=1, max_length=1000)


class SegmentResponse(StagedStrict):
    rules: list[PreparedRule] = Field(default_factory=list, max_length=20)
    public_conditions: list[PreparedItem] = Field(default_factory=list, max_length=20)
    context_items: list[PreparedItem] = Field(default_factory=list, max_length=20)


class ExtractedPoints(StagedStrict):
    points: list[PointContent] = Field(max_length=3)


class PointOpinion(StagedStrict):
    index: StrictInt = Field(ge=0, le=2)
    status: Literal['checked','needs_confirmation']
    reason: str = Field(min_length=1, max_length=1200)
    missing_rule: bool = False
    conflict: bool = False


class PointOpinions(StagedStrict):
    reviews: list[PointOpinion] = Field(max_length=3)


class RuleEvidenceInput(StagedRevision):
    rule_id: UUID
    evidence_ids: list[UUID] = Field(min_length=1,max_length=20)
    note: str = Field(min_length=1,max_length=1500)
