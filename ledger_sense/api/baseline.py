# ledger_sense/api/baseline.py
"""Baseline study API. The billable week-one instrument.

Money arrives as strings. A JSON float in the rate body is REJECTED at the
schema, never coerced — this is the money path.

Mounted through api.deps.register() in api/app.py, same as every other
router: require_session is applied there, not here.
"""

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, StrictStr

from ledger_sense.baseline.report import render_report
from ledger_sense.baseline.study import BaselineStudy, LoadedRate, TimedSample
from ledger_sense.domain.money import money

router = APIRouter(prefix="/api/baseline")

# ponytail: in-process store. Swap for the Postgres session in Task 43, when the
# inbox needs studies to survive a restart.
_STUDIES: dict[str, BaselineStudy] = {}


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


def _get(study_id: str) -> BaselineStudy:
    if study_id not in _STUDIES:
        raise HTTPException(status_code=404, detail="study not found")
    return _STUDIES[study_id]


@router.post("/studies", status_code=201)
def create_study(body: NewStudy) -> dict[str, str]:
    study_id = str(uuid.uuid4())
    _STUDIES[study_id] = BaselineStudy(
        samples=[],
        loaded_rate=LoadedRate(hourly=money("0.00"), overhead_multiplier=Decimal(1)),
        population_size=body.population_size, sampling_frame=body.sampling_frame,
        collection_period=body.collection_period)
    return {"study_id": study_id}


@router.post("/studies/{study_id}/samples", status_code=201)
def add_sample(study_id: str, body: NewSample) -> dict[str, int]:
    study = _get(study_id)
    study.samples.append(TimedSample(**body.model_dump()))
    return {"count": len(study.samples)}


@router.put("/studies/{study_id}/rate")
def set_rate(study_id: str, body: NewRate) -> dict[str, str]:
    study = _get(study_id)
    study.loaded_rate = LoadedRate(
        hourly=money(body.hourly),
        overhead_multiplier=Decimal(body.overhead_multiplier))
    return {"effective_hourly": str(study.loaded_rate.effective_hourly())}


@router.get("/studies/{study_id}/report")
def get_report(study_id: str) -> Response:
    study = _get(study_id)
    try:
        html = render_report(study)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(content=html, media_type="text/html")
