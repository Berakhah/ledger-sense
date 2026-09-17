# tests/api/test_reconciliation.py
"""Task 32: continuation sheet against the summary, arithmetic shown inline.

Bare FastAPI() + include_router, same isolation as test_baseline.py: this
suite is the router's business logic, not api.deps.register()'s wiring (that
is tests/api/test_auth.py's job). It is NOT session-free, though: the handler
depends on require_session directly (api/reconciliation.py's CurrentSession),
because owned() needs the tenant off the session and that has to hold even
against this bare app, not only once register() wraps it. _session() mints a
real tenant/session the same way tests/api/conftest.py's TenantClient does,
bypassing /api/auth/login (not mounted on this bare app).

The demo seed hatch (POST /api/reconciliation/seed) is not part of `router` —
it is mounted by hand in api/app.py only under LEDGER_SENSE_PROFILE=demo,
mirroring api/fixtures.py's seed(). It is exercised at the bottom of this
file through build_app(), the same pattern tests/api/test_fixtures.py uses.
"""
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ledger_sense.api.reconciliation import router, seed_from_corpus
from ledger_sense.auth.models import create_tenant, create_user
from ledger_sense.auth.session import issue_session

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def _session() -> tuple[str, dict[str, str]]:
    suffix = uuid.uuid4().hex[:8]
    tenant_id = create_tenant(f"reconciliation-test-{suffix}")
    user_id = create_user(tenant_id, f"reconciliation-test-{suffix}@example.test", "irrelevant")
    return tenant_id, {"session": issue_session(user_id, tenant_id)}


def test_a_clean_document_reports_no_discrepancy() -> None:
    tenant_id, cookies = _session()
    seed_from_corpus("doc-clean", seed=1, error="clean", tenant_id=tenant_id)
    resp = client.get("/api/reconciliation/doc-clean", cookies=cookies)
    assert resp.json()["discrepancies"] == []


def test_a_footing_error_is_located_at_the_exact_field() -> None:
    tenant_id, cookies = _session()
    seed_from_corpus("doc-a1", seed=1, error="A1_footing", tenant_id=tenant_id)
    discrepancies = client.get("/api/reconciliation/doc-a1", cookies=cookies).json()["discrepancies"]
    assert [d["rule_id"] for d in discrepancies] == ["A1"]
    assert discrepancies[0]["field"] == "total_completed_and_stored"


def test_every_discrepancy_shows_its_arithmetic() -> None:
    tenant_id, cookies = _session()
    seed_from_corpus("doc-a1", seed=1, error="A1_footing", tenant_id=tenant_id)
    discrepancy = client.get("/api/reconciliation/doc-a1", cookies=cookies).json()["discrepancies"][0]
    assert "=" in discrepancy["arithmetic"]
    assert discrepancy["expected"] in discrepancy["arithmetic"]


def test_the_delta_is_exact() -> None:
    tenant_id, cookies = _session()
    seed_from_corpus("doc-a1", seed=1, error="A1_footing", tenant_id=tenant_id)
    discrepancy = client.get("/api/reconciliation/doc-a1", cookies=cookies).json()["discrepancies"][0]
    assert discrepancy["delta"] == "0.01"


def test_lines_and_summary_are_both_returned_for_side_by_side_rendering() -> None:
    tenant_id, cookies = _session()
    seed_from_corpus("doc-clean", seed=1, error="clean", tenant_id=tenant_id)
    body = client.get("/api/reconciliation/doc-clean", cookies=cookies).json()
    assert body["lines"] and body["summary"]
    assert "total_completed_and_stored" in body["summary"]


def test_an_unknown_document_is_a_404_not_an_empty_reconciliation() -> None:
    _tenant_id, cookies = _session()
    assert client.get("/api/reconciliation/doc-missing", cookies=cookies).status_code == 404


def test_an_anonymous_caller_is_refused() -> None:
    """Depends on require_session directly (not only through
    api.deps.register()'s wrapping in the real app) — proven here against the
    bare router alone, with no cookie at all."""
    assert client.get("/api/reconciliation/doc-clean").status_code == 401


def test_a_tenant_cannot_read_another_tenants_document() -> None:
    """404, never 403 — a 403 confirms the document exists, which is the leak."""
    owner_tenant, _owner_cookies = _session()
    _other_tenant, other_cookies = _session()
    seed_from_corpus("doc-cross-tenant", seed=1, error="clean", tenant_id=owner_tenant)
    resp = client.get("/api/reconciliation/doc-cross-tenant", cookies=other_cookies)
    assert resp.status_code == 404


# ---- the demo seed hatch (mirrors tests/api/test_fixtures.py) ----


def _demo_client(monkeypatch: pytest.MonkeyPatch, profile: str) -> TestClient:
    monkeypatch.setenv("LEDGER_SENSE_PROFILE", profile)
    from ledger_sense.api.app import build_app
    return TestClient(build_app())


def test_the_reconciliation_seed_route_exists_under_the_demo_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    demo = _demo_client(monkeypatch, "demo")
    resp = demo.post("/api/reconciliation/seed",
                     json={"doc_id": "demo-doc-a1", "seed": 1, "error": "A1_footing"})
    assert resp.status_code == 201
    doc_id = resp.json()["doc_id"]
    assert demo.get(f"/api/reconciliation/{doc_id}").json()["discrepancies"] != []


def test_the_reconciliation_seed_route_does_not_exist_under_the_client_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No document-minting POST succeeds outside the demo profile.

    Not a bare 404 like /api/fixtures/seed's own control (test_fixtures.py):
    /api/reconciliation/{doc_id} is a real GET route in every profile, and
    "seed" is a syntactically valid doc_id, so the path itself matches — only
    the POST method does not. Starlette answers that 405, not 404. Either way
    the seed never runs: no document is minted and no session is issued.
    """
    client_profile = _demo_client(monkeypatch, "client")
    resp = client_profile.post("/api/reconciliation/seed", json={"doc_id": "x"})
    assert resp.status_code in (404, 405)
    assert "set-cookie" not in resp.headers
