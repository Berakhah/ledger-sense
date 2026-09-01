# tests/api/test_baseline.py
"""Task 13b brief, adapted on one point: ledger_sense.baseline.study now
refuses an interval below MIN_SAMPLES_FOR_AN_INTERVAL = 8 (F-54 — a t-interval
on fewer right-skewed observations is not defensible; see
tests/baseline/test_study.py::test_too_few_samples_refuses_to_report_an_interval).
The brief's Step 1 text predates that revision and used 3 samples for the
happy path and "at least 2" for the refusal message. Sample counts and the
expected detail text below reflect the current, correct rule rather than
reintroducing the interval F-54 removed.

Bare FastAPI() + include_router, no full app: this suite is the router's
business logic in isolation — api.deps.register() (wiring it into the real
app, see api/app.py) is covered by tests/api/test_auth.py, not here. It is
NOT session-free, though: every handler below depends on require_session
directly (api/baseline.py's CurrentSession), because owned() needs the
tenant off the session and that has to hold even against this bare app, not
only once register() wraps it. _session_cookie() mints a real tenant/session
the same way tests/api/conftest.py's TenantClient does, bypassing
/api/auth/login (not mounted on this bare app) — see that class's docstring
for why cookies are attached per-request rather than left to the client's
jar (the session cookie is Secure; TestClient's http:// base URL won't store
one).
"""
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ledger_sense.api.baseline import router
from ledger_sense.auth.models import create_tenant, create_user
from ledger_sense.auth.session import issue_session

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def _session_cookie() -> dict[str, str]:
    suffix = uuid.uuid4().hex[:8]
    tenant_id = create_tenant(f"baseline-test-{suffix}")
    user_id = create_user(tenant_id, f"baseline-test-{suffix}@example.test", "irrelevant")
    return {"session": issue_session(user_id, tenant_id)}


def _new_study(cookies: dict[str, str]) -> str:
    resp = client.post("/api/baseline/studies", json={
        "population_size": 500,
        "sampling_frame": "all AP documents received Aug 2026",
        "collection_period": ["2026-08-01", "2026-08-31"]}, cookies=cookies)
    assert resp.status_code == 201
    return str(resp.json()["study_id"])


def test_samples_accumulate_and_report_renders() -> None:
    cookies = _session_cookie()
    study_id = _new_study(cookies)
    # 8 samples: MIN_SAMPLES_FOR_AN_INTERVAL, not the brief's original 3.
    for i, seconds in enumerate([300, 360, 420, 380, 340, 400, 320, 360]):
        assert client.post(f"/api/baseline/studies/{study_id}/samples", json={
            "document_id": str(i), "doc_type": "pay_application",
            "touch_seconds": seconds, "exceptions": 0}, cookies=cookies).status_code == 201
    client.put(f"/api/baseline/studies/{study_id}/rate",
               json={"hourly": "30.00", "overhead_multiplier": "1.4"}, cookies=cookies)
    report = client.get(f"/api/baseline/studies/{study_id}/report", cookies=cookies)
    assert report.status_code == 200
    assert "95% confidence interval" in report.text


def test_report_refuses_below_the_minimum_sample_size() -> None:
    cookies = _session_cookie()
    study_id = _new_study(cookies)
    client.post(f"/api/baseline/studies/{study_id}/samples", json={
        "document_id": "1", "doc_type": "pay_application",
        "touch_seconds": 360, "exceptions": 0}, cookies=cookies)
    client.put(f"/api/baseline/studies/{study_id}/rate",
               json={"hourly": "30.00", "overhead_multiplier": "1.4"}, cookies=cookies)
    resp = client.get(f"/api/baseline/studies/{study_id}/report", cookies=cookies)
    assert resp.status_code == 409
    assert "at least 8" in resp.json()["detail"]


def test_rate_rejects_a_json_float_rather_than_coercing_it() -> None:
    cookies = _session_cookie()
    study_id = _new_study(cookies)
    resp = client.put(f"/api/baseline/studies/{study_id}/rate",
                      json={"hourly": 30.0, "overhead_multiplier": "1.4"}, cookies=cookies)
    assert resp.status_code == 422


def test_an_anonymous_caller_is_refused() -> None:
    """Every handler depends on require_session directly (not only through
    api.deps.register()'s wrapping in the real app) — proven here against
    the bare router alone, with no cookie at all."""
    resp = client.post("/api/baseline/studies", json={
        "population_size": 1, "sampling_frame": "x",
        "collection_period": ["2026-08-01", "2026-08-31"]})
    assert resp.status_code == 401


def test_a_tenant_cannot_touch_another_tenants_study() -> None:
    """Code review finding: _STUDIES carried no tenant_id and no handler
    called owned(), so a second tenant could POST a sample into (201) or
    overwrite the rate of (200) a study it does not own. All three
    operations — sample, rate, report — must 404 instead."""
    owner_cookies = _session_cookie()
    other_cookies = _session_cookie()
    study_id = _new_study(owner_cookies)

    sample_resp = client.post(f"/api/baseline/studies/{study_id}/samples", json={
        "document_id": "intruder", "doc_type": "pay_application",
        "touch_seconds": 300, "exceptions": 0}, cookies=other_cookies)
    assert sample_resp.status_code == 404

    rate_resp = client.put(f"/api/baseline/studies/{study_id}/rate",
                           json={"hourly": "999.00", "overhead_multiplier": "1.0"},
                           cookies=other_cookies)
    assert rate_resp.status_code == 404

    report_resp = client.get(f"/api/baseline/studies/{study_id}/report", cookies=other_cookies)
    assert report_resp.status_code == 404

    # The owner's own data is untouched by the rejected cross-tenant calls.
    assert client.put(f"/api/baseline/studies/{study_id}/rate",
                      json={"hourly": "30.00", "overhead_multiplier": "1.4"},
                      cookies=owner_cookies).status_code == 200


def test_the_report_escapes_a_script_tag_in_the_sampling_frame() -> None:
    """Code review finding: method_statement() carries sampling_frame — free
    text from NewStudy, no length or content constraint — straight into the
    report's <pre> block. <pre> is an ordinary HTML container, not a
    raw-text element: an unescaped "</pre><script>...</script>" breaks out
    of it and executes."""
    cookies = _session_cookie()
    resp = client.post("/api/baseline/studies", json={
        "population_size": 500,
        "sampling_frame": "</pre><script>alert(1)</script>",
        "collection_period": ["2026-08-01", "2026-08-31"]}, cookies=cookies)
    assert resp.status_code == 201
    study_id = str(resp.json()["study_id"])
    for i, seconds in enumerate([300, 360, 420, 380, 340, 400, 320, 360]):
        client.post(f"/api/baseline/studies/{study_id}/samples", json={
            "document_id": str(i), "doc_type": "pay_application",
            "touch_seconds": seconds, "exceptions": 0}, cookies=cookies)
    client.put(f"/api/baseline/studies/{study_id}/rate",
               json={"hourly": "30.00", "overhead_multiplier": "1.4"}, cookies=cookies)
    report = client.get(f"/api/baseline/studies/{study_id}/report", cookies=cookies)
    assert report.status_code == 200
    assert "<script>alert(1)</script>" not in report.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in report.text


def test_an_empty_rate_string_is_rejected_with_a_clean_422() -> None:
    """Code review finding: money("") / Decimal("") raise
    decimal.InvalidOperation uncaught, surfacing as a raw 500 rather than a
    validation error — reachable in normal use, since the frontend gates
    "Produce report" on sample count only, not on the rate fields being
    filled."""
    cookies = _session_cookie()
    study_id = _new_study(cookies)
    resp = client.put(f"/api/baseline/studies/{study_id}/rate",
                      json={"hourly": "", "overhead_multiplier": "1.4"}, cookies=cookies)
    assert resp.status_code == 422

    resp = client.put(f"/api/baseline/studies/{study_id}/rate",
                      json={"hourly": "30.00", "overhead_multiplier": "not-a-number"},
                      cookies=cookies)
    assert resp.status_code == 422
