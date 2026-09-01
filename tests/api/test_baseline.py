# tests/api/test_baseline.py
"""Task 13b brief, adapted on one point: ledger_sense.baseline.study now
refuses an interval below MIN_SAMPLES_FOR_AN_INTERVAL = 8 (F-54 — a t-interval
on fewer right-skewed observations is not defensible; see
tests/baseline/test_study.py::test_too_few_samples_refuses_to_report_an_interval).
The brief's Step 1 text predates that revision and used 3 samples for the
happy path and "at least 2" for the refusal message. Sample counts and the
expected detail text below reflect the current, correct rule rather than
reintroducing the interval F-54 removed.

Bare FastAPI() + include_router, no session: this suite is the router's
business logic in isolation. The real app wires this router through
api.deps.register() (see api/app.py), which is what applies require_session —
covered by tests/api/test_auth.py, not here.
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ledger_sense.api.baseline import router

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def _new_study() -> str:
    resp = client.post("/api/baseline/studies", json={
        "population_size": 500,
        "sampling_frame": "all AP documents received Aug 2026",
        "collection_period": ["2026-08-01", "2026-08-31"]})
    assert resp.status_code == 201
    return str(resp.json()["study_id"])


def test_samples_accumulate_and_report_renders() -> None:
    study_id = _new_study()
    # 8 samples: MIN_SAMPLES_FOR_AN_INTERVAL, not the brief's original 3.
    for i, seconds in enumerate([300, 360, 420, 380, 340, 400, 320, 360]):
        assert client.post(f"/api/baseline/studies/{study_id}/samples", json={
            "document_id": str(i), "doc_type": "pay_application",
            "touch_seconds": seconds, "exceptions": 0}).status_code == 201
    client.put(f"/api/baseline/studies/{study_id}/rate",
               json={"hourly": "30.00", "overhead_multiplier": "1.4"})
    report = client.get(f"/api/baseline/studies/{study_id}/report")
    assert report.status_code == 200
    assert "95% confidence interval" in report.text


def test_report_refuses_below_the_minimum_sample_size() -> None:
    study_id = _new_study()
    client.post(f"/api/baseline/studies/{study_id}/samples", json={
        "document_id": "1", "doc_type": "pay_application",
        "touch_seconds": 360, "exceptions": 0})
    client.put(f"/api/baseline/studies/{study_id}/rate",
               json={"hourly": "30.00", "overhead_multiplier": "1.4"})
    resp = client.get(f"/api/baseline/studies/{study_id}/report")
    assert resp.status_code == 409
    assert "at least 8" in resp.json()["detail"]


def test_rate_rejects_a_json_float_rather_than_coercing_it() -> None:
    study_id = _new_study()
    resp = client.put(f"/api/baseline/studies/{study_id}/rate",
                      json={"hourly": 30.0, "overhead_multiplier": "1.4"})
    assert resp.status_code == 422
