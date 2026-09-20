"""Writeback API — the human-triggered seam from a cleared review queue to a
draft bill. tests/api/conftest.py's TenantClient.seed_postable_document()
gives every test here a document with a complete claim set and no open
exceptions, matching what the review queue looks like once cleared."""

from pathlib import Path

import pytest

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
