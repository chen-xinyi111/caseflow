from uuid import UUID
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from . import documents

router = APIRouter(tags=['CaseFlow documents'])


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class SourceSegment(Strict):
    unit_id: str = Field(min_length=1, max_length=40)
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(ge=1)


class EvidenceInput(Strict):
    document_id: UUID
    snapshot_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    segments: list[SourceSegment] = Field(min_length=1, max_length=32)
    quote: str = Field(min_length=1, max_length=101000)


class LegacyInput(Strict):
    document_id: UUID
    page: StrictInt = Field(ge=1)
    quote: str = Field(min_length=1, max_length=4000)


def checked(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from None
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise HTTPException(400, str(exc) or '输入无效') from None


@router.get('/projects/{pid}/documents')
def listing(pid: UUID):
    return checked(documents.listing, pid)


@router.get('/projects/{pid}/documents/{did}')
def detail(pid: UUID, did: UUID):
    return checked(documents.detail, pid, did)


@router.get('/projects/{pid}/documents/{did}/original')
def original(pid: UUID, did: UUID):
    raw, fmt = checked(documents.original, pid, did)
    disposition = 'inline' if fmt == 'pdf' else 'attachment'
    media = 'application/pdf' if fmt == 'pdf' else 'application/octet-stream'
    return Response(raw, media_type=media, headers={
        'Content-Disposition': f'{disposition}; filename="document-{did}.{fmt}"',
        'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'})


@router.post('/projects/{pid}/evidence')
def create_evidence(pid: UUID, data: EvidenceInput):
    return checked(documents.create_evidence, pid, data.document_id, data.snapshot_sha256,
                   [s.model_dump() for s in data.segments], data.quote)


@router.post('/projects/{pid}/evidence/resolve-legacy')
def resolve_legacy(pid: UUID, data: LegacyInput):
    return checked(documents.resolve_legacy, pid, data.document_id, data.page, data.quote)


@router.get('/projects/{pid}/evidence/{eid}')
def evidence(pid: UUID, eid: UUID):
    return checked(documents.evidence, pid, eid)
