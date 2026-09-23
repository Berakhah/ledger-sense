"""tests/api/test_vendors.py

Bare FastAPI() + include_router, same pattern as tests/api/test_review.py:
this suite is the router's business logic in isolation. api.deps.register()
(wiring it into the real app with require_session) is covered by
tests/api/test_auth.py, not here.

payment_history is always [] in this plan — no ingest path writes
payment_details rows through the API yet (see the vendor-persistence plan's
Architecture note) — but test_vendor_ledger_never_includes_the_account_hmac
still seeds one directly against the real table, because account_hmac must
never leave the server regardless of how a row got there.
"""

import uuid
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ledger_sense.api.vendors import router
from ledger_sense.auth.models import create_tenant, create_user
from ledger_sense.auth.session import issue_session
from ledger_sense.domain.vendor import VendorLedger
from ledger_sense.store.retention import payment_details
from ledger_sense.store.session import app_engine
from ledger_sense.store.vendors import save_vendor_ledger

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def _session_cookie() -> tuple[dict[str, str], str]:
    suffix = uuid.uuid4().hex[:8]
    tenant_id = create_tenant(f"vendors-test-{suffix}")
    user_id = create_user(tenant_id, f"vendors-test-{suffix}@example.test", "irrelevant")
    return {"session": issue_session(user_id, tenant_id)}, tenant_id


def test_vendor_ledger_returns_name_and_aliases() -> None:
    cookies, tenant_id = _session_cookie()
    ledger = VendorLedger()
    vendor_id = ledger.create("Acme Steel Company")
    ledger.confirm_alias(vendor_id, "Acme Steel Co.", source_document="doc-1",
                         confirmed_by="reviewer-1")
    with app_engine().begin() as conn:
        save_vendor_ledger(conn, tenant_id, ledger)

    response = client.get(f"/api/vendors/{vendor_id}/ledger", cookies=cookies)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == vendor_id
    assert body["name"] == "Acme Steel Company"
    assert [a["name"] for a in body["aliases"]] == ["Acme Steel Co."]


def test_vendor_ledger_payment_history_is_an_honest_empty_list() -> None:
    # no payment_details rows exist anywhere yet — this plan adds no ingest path.
    cookies, tenant_id = _session_cookie()
    ledger = VendorLedger()
    vendor_id = ledger.create("Acme Steel Company")
    with app_engine().begin() as conn:
        save_vendor_ledger(conn, tenant_id, ledger)

    response = client.get(f"/api/vendors/{vendor_id}/ledger", cookies=cookies)
    assert response.status_code == 200, response.text
    assert response.json()["payment_history"] == []


def test_vendor_ledger_never_includes_the_account_hmac() -> None:
    # a payment_details row seeded directly (bypassing the ledger API) to
    # prove the response model excludes account_hmac regardless of how the
    # row got there.
    cookies, tenant_id = _session_cookie()
    ledger = VendorLedger()
    vendor_id = ledger.create("Acme Steel Company")
    with app_engine().begin() as conn:
        save_vendor_ledger(conn, tenant_id, ledger)
        conn.execute(payment_details.insert().values(
            id=uuid.uuid4().hex, tenant_id=tenant_id, vendor_id=vendor_id,
            account_last4="1234", routing_last4="5678",
            account_hmac="deadbeef" * 8, observed_at=datetime.now(UTC),
            source_document="doc-1", confirmed_by=None, confirmed_at=None))

    response = client.get(f"/api/vendors/{vendor_id}/ledger", cookies=cookies)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "account_hmac" not in str(body)
    assert len(body["payment_history"]) == 1
    assert body["payment_history"][0]["account_last4"] == "1234"


def test_an_unknown_vendor_id_is_404() -> None:
    cookies, _ = _session_cookie()
    response = client.get("/api/vendors/nope/ledger", cookies=cookies)
    assert response.status_code == 404


def test_another_tenants_vendor_is_404_not_403() -> None:
    cookies_a, tenant_a = _session_cookie()
    cookies_b, tenant_b = _session_cookie()

    ledger = VendorLedger()
    vendor_id = ledger.create("Tenant A Vendor")
    with app_engine().begin() as conn:
        save_vendor_ledger(conn, tenant_a, ledger)

    response = client.get(f"/api/vendors/{vendor_id}/ledger", cookies=cookies_b)
    assert response.status_code == 404

    response = client.get(f"/api/vendors/{vendor_id}/ledger", cookies=cookies_a)
    assert response.status_code == 200


def test_requires_a_session() -> None:
    ledger = VendorLedger()
    vendor_id = ledger.create("No Session Vendor")
    response = client.get(f"/api/vendors/{vendor_id}/ledger")
    assert response.status_code == 401
