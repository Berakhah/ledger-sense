# ledger_sense/api/baseline.py
"""Baseline study API. The billable week-one instrument.

Money arrives as strings. A JSON float in the rate body is REJECTED at the
schema, never coerced — this is the money path.

Mounted through api.deps.register() in api/app.py, same as every other
router: register() applies require_session again on top of this (free —
FastAPI caches a dependency per request), same belt-and-braces the stub
routers use. It has to be depended on here too, not only via register(),
because owned() needs the tenant off the session, and this router's tests
build a bare app that skips register() entirely (see tests/api/test_baseline.py).
"""

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, StrictStr

from ledger_sense.api.deps import owned, require_session
from ledger_sense.auth.session import Session
from ledger_sense.baseline.report import render_report
from ledger_sense.baseline.study import BaselineStudy, LoadedRate, TimedSample
from ledger_sense.domain.money import money

router = APIRouter(prefix="/api/baseline")

CurrentSession = Annotated[Session, Depends(require_session)]

# ponytail: in-process store. Swap for the Postgres session in Task 43, when the
# inbox needs studies to survive a restart. A parallel dict rather than a
# field on BaselineStudy: that model belongs to the study engine (Task 10),
# not the API layer's tenancy concern.
_STUDIES: dict[str, BaselineStudy] = {}
_STUDY_TENANTS: dict[str, str] = {}


class NewStudy(BaseModel):
    population_size: int
    sampling_frame: str
    collection_period: tuple[date, date]


class NewSample(BaseModel):
    document_id: str
    doc_type: str
    touch_seconds: int
    exceptions: int


class NewRate(BaseModel):
    hourly: StrictStr
    overhead_multiplier: StrictStr


def _get(study_id: str, session: Session) -> BaselineStudy:
    # owned() alone: a study_id absent from _STUDY_TENANTS gives .get() None,
    # which owned() treats identically to "belongs to someone else" — 404
    # either way, no separate "study not found" branch to keep in sync.
    owned(session, _STUDY_TENANTS.get(study_id))
    return _STUDIES[study_id]


@router.post("/studies", status_code=201)
def create_study(body: NewStudy, session: CurrentSession) -> dict[str, str]:
    study_id = str(uuid.uuid4())
    _STUDIES[study_id] = BaselineStudy(
        samples=[],
        loaded_rate=LoadedRate(hourly=money("0.00"), overhead_multiplier=Decimal(1)),
        population_size=body.population_size, sampling_frame=body.sampling_frame,
        collection_period=body.collection_period)
    _STUDY_TENANTS[study_id] = session.tenant_id
    return {"study_id": study_id}


@router.post("/studies/{study_id}/samples", status_code=201)
def add_sample(study_id: str, body: NewSample, session: CurrentSession) -> dict[str, int]:
    study = _get(study_id, session)
    study.samples.append(TimedSample(**body.model_dump()))
    return {"count": len(study.samples)}


@router.put("/studies/{study_id}/rate")
def set_rate(study_id: str, body: NewRate, session: CurrentSession) -> dict[str, str]:
    study = _get(study_id, session)
    try:
        study.loaded_rate = LoadedRate(
            hourly=money(body.hourly),
            overhead_multiplier=Decimal(body.overhead_multiplier))
    except InvalidOperation as exc:
        # money("") / Decimal("") raise decimal.InvalidOperation, not a
        # pydantic ValidationError — StrictStr only rejects the wrong JSON
        # *type*, not an empty or non-numeric *string*, so this is the
        # schema's own gap, closed here rather than left to surface as a 500.
        raise HTTPException(
            status_code=422,
            detail="hourly and overhead_multiplier must be non-empty decimal numbers",
        ) from exc
    return {"effective_hourly": str(study.loaded_rate.effective_hourly())}


@router.get("/studies/{study_id}/report")
def get_report(study_id: str, session: CurrentSession) -> Response:
    study = _get(study_id, session)
    try:
        html = render_report(study)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(content=html, media_type="text/html")
