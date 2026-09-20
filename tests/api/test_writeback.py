"""Writeback API — the human-triggered seam from a cleared review queue to a
draft bill. tests/api/conftest.py's TenantClient.seed_postable_document()
gives every test here a document with a complete claim set and no open
exceptions, matching what the review queue looks like once cleared."""

from pathlib import Path

import pytest
import sqlalchemy as sa

from ledger_sense.store import session as store_session
from ledger_sense.store.retention import findings
from tests.api.conftest import TenantClient


@pytest.fixture(autouse=True)
def _writeback_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SENSE_WRITEBACK_DIR", str(tmp_path / "writeback"))
    monkeypatch.setenv("LEDGER_SENSE_DEFAULT_ACCOUNT_REF", "54")


def test_status_reports_not_posted_before_any_post(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    alice, _bob = two_tenants
    doc_id = alice.seed_postable_document()
    response = alice.get(f"/api/writeback/documents/{doc_id}/status")
    assert response.status_code == 200, response.text
    assert response.json() == {"state": "not_posted", "remote_id": None}


def test_status_on_a_document_with_no_content_hash_is_422(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    alice, _bob = two_tenants
    doc_id = alice.seed_unrun_document()  # no content_sha256, no claims
    response = alice.get(f"/api/writeback/documents/{doc_id}/status")
    assert response.status_code == 422, response.text


def test_status_on_a_cross_tenant_document_is_404(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    alice, bob = two_tenants
    doc_id = alice.seed_postable_document()
    response = bob.get(f"/api/writeback/documents/{doc_id}/status")
    assert response.status_code == 404, response.text


def test_post_creates_a_bill_and_updates_status(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    alice, _bob = two_tenants
    doc_id = alice.seed_postable_document()
    response = alice.post_json(f"/api/writeback/documents/{doc_id}/post",
                               {"adapter": "export", "vendor_ref": "V-9"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["adapter"] == "export"
    assert body["was_replay"] is False

    status_response = alice.get(f"/api/writeback/documents/{doc_id}/status")
    assert status_response.json() == {"state": "posted", "remote_id": body["remote_id"]}


def test_post_twice_with_the_same_vendor_replays(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    alice, _bob = two_tenants
    doc_id = alice.seed_postable_document()
    body: dict[str, object] = {"adapter": "export", "vendor_ref": "V-9"}
    first = alice.post_json(f"/api/writeback/documents/{doc_id}/post", body).json()
    second = alice.post_json(f"/api/writeback/documents/{doc_id}/post", body).json()
    assert second["was_replay"] is True
    assert second["remote_id"] == first["remote_id"]


def test_post_refuses_a_document_with_an_open_exception(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    alice, _bob = two_tenants
    # seed_exception() returns the finding's own id (it's built for
    # /api/review, which addresses findings directly) — the document it
    # attaches to still ships with the OPEN finding this test needs.
    exc_id = alice.seed_exception()
    with store_session.app_engine().connect() as conn:
        doc_id = conn.execute(
            sa.select(findings.c.document_id).where(findings.c.id == exc_id)
        ).scalar_one()
    response = alice.post_json(f"/api/writeback/documents/{doc_id}/post",
                               {"adapter": "export", "vendor_ref": "V-9"})
    assert response.status_code == 422, response.text
    assert "open" in response.json()["detail"]


def test_post_refuses_a_document_never_run(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    alice, _bob = two_tenants
    doc_id = alice.seed_unrun_document()  # a real documents row, no findings at all
    response = alice.post_json(f"/api/writeback/documents/{doc_id}/post",
                               {"adapter": "export", "vendor_ref": "V-9"})
    assert response.status_code == 422, response.text


def test_post_on_a_cross_tenant_document_is_404(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    alice, bob = two_tenants
    doc_id = alice.seed_postable_document()
    response = bob.post_json(f"/api/writeback/documents/{doc_id}/post",
                             {"adapter": "export", "vendor_ref": "V-9"})
    assert response.status_code == 404, response.text
