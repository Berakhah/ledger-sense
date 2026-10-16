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


def test_post_with_a_different_vendor_ref_after_posting_is_409(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    alice, _bob = two_tenants
    doc_id = alice.seed_postable_document()
    alice.post_json(f"/api/writeback/documents/{doc_id}/post",
                    {"adapter": "export", "vendor_ref": "V-9"})
    response = alice.post_json(f"/api/writeback/documents/{doc_id}/post",
                               {"adapter": "export", "vendor_ref": "V-DIFFERENT"})
    assert response.status_code == 409, response.text
    assert "vendor" in response.json()["detail"]


# NOTE: test_post_after_reversal_with_a_new_vendor_is_not_a_conflict is NOT
# added here. The brief's original draft included it in this task, but it
# calls POST .../reverse, which does not exist until Task 11 — a plan defect
# (see docs/superpowers/plans/2026-09-20-writeback-pipeline-integration.md,
# Task 10 step 1). It is relocated to Task 11's test list, where /reverse
# actually exists to make it true.


def test_post_maps_concurrent_write_in_progress_to_409_with_retry_after(
        two_tenants: tuple[TenantClient, TenantClient], monkeypatch: pytest.MonkeyPatch) -> None:
    import ledger_sense.api.writeback as writeback_mod
    from ledger_sense.writeback.qbo import ConcurrentWriteInProgressError

    def _raise(name: object, ledger: object) -> object:
        class _Raiser:
            name = "export"
            def create_unapproved_bill(self, request: object) -> object:
                raise ConcurrentWriteInProgressError("in progress")
            def is_period_closed(self, txn_date: object) -> bool:
                return False
            def reverse(self, remote_id: object, idempotency_key: object) -> object:
                raise NotImplementedError
        return _Raiser()

    monkeypatch.setattr(writeback_mod, "_inner_adapter", _raise)
    alice, _bob = two_tenants
    doc_id = alice.seed_postable_document()
    response = alice.post_json(f"/api/writeback/documents/{doc_id}/post",
                               {"adapter": "export", "vendor_ref": "V-9"})
    assert response.status_code == 409, response.text
    assert response.headers["retry-after"] == "2"


def test_post_maps_closed_period_to_422(
        two_tenants: tuple[TenantClient, TenantClient], monkeypatch: pytest.MonkeyPatch) -> None:
    import ledger_sense.api.writeback as writeback_mod
    from ledger_sense.writeback.interface import ClosedPeriodError

    def _raise(name: object, ledger: object) -> object:
        class _Raiser:
            name = "export"
            def create_unapproved_bill(self, request: object) -> object:
                raise ClosedPeriodError("books closed through 2026-01-01")
            def is_period_closed(self, txn_date: object) -> bool:
                return True
            def reverse(self, remote_id: object, idempotency_key: object) -> object:
                raise NotImplementedError
        return _Raiser()

    monkeypatch.setattr(writeback_mod, "_inner_adapter", _raise)
    alice, _bob = two_tenants
    doc_id = alice.seed_postable_document()
    response = alice.post_json(f"/api/writeback/documents/{doc_id}/post",
                               {"adapter": "export", "vendor_ref": "V-9"})
    assert response.status_code == 422, response.text
    assert "books closed" in response.json()["detail"]


def test_reverse_clears_the_posted_status(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    alice, _bob = two_tenants
    doc_id = alice.seed_postable_document()
    posted = alice.post_json(f"/api/writeback/documents/{doc_id}/post",
                             {"adapter": "export", "vendor_ref": "V-9"}).json()
    response = alice.post_json(f"/api/writeback/documents/{doc_id}/reverse",
                               {"adapter": "export", "remote_id": posted["remote_id"],
                                "idempotency_key": posted["idempotency_key"]})
    assert response.status_code == 200, response.text
    assert response.json()["method"] == "deleted"

    status_response = alice.get(f"/api/writeback/documents/{doc_id}/status")
    assert status_response.json()["state"] == "not_posted"


def test_reverse_an_unknown_remote_id_is_404(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    """Final whole-branch review, I-1: a remote_id with no audited write at
    all now fails the same tenancy/ownership check that a remote_id
    belonging to a DIFFERENT document fails — `document_for_remote` returns
    None either way, and None can never equal a real `doc_id`. This
    supersedes the route's own 422 (used to be reached via
    AuditedAdapter.reverse's ValueError) for this specific case: the check
    now runs first, so an unposted remote_id never gets far enough to hit
    that branch — the route's `except ValueError` is retained as a defensive
    fallback (AuditedAdapter.reverse is also called directly in
    tests/writeback/test_audit.py) but is no longer reachable through this
    endpoint."""
    alice, _bob = two_tenants
    doc_id = alice.seed_postable_document()
    response = alice.post_json(f"/api/writeback/documents/{doc_id}/reverse",
                               {"adapter": "export", "remote_id": "never-posted",
                                "idempotency_key": "LSbogus"})
    assert response.status_code == 404, response.text


def test_reverse_on_a_cross_tenant_document_is_404(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    alice, bob = two_tenants
    doc_id = alice.seed_postable_document()
    posted = alice.post_json(f"/api/writeback/documents/{doc_id}/post",
                             {"adapter": "export", "vendor_ref": "V-9"}).json()
    response = bob.post_json(f"/api/writeback/documents/{doc_id}/reverse",
                             {"adapter": "export", "remote_id": posted["remote_id"],
                              "idempotency_key": posted["idempotency_key"]})
    assert response.status_code == 404, response.text


def test_post_after_reversal_with_a_new_vendor_is_not_a_conflict(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    """A reversal clears the way for a genuinely new post — including a
    changed vendor — the same way IdempotencyLedger.begin() already treats a
    `reversed` record as absent (F-39). Relocated here from Task 10 (a plan
    defect: this test needs `/reverse`, which does not exist until this
    task)."""
    alice, _bob = two_tenants
    doc_id = alice.seed_postable_document()
    first = alice.post_json(f"/api/writeback/documents/{doc_id}/post",
                            {"adapter": "export", "vendor_ref": "V-9"}).json()
    alice.post_json(f"/api/writeback/documents/{doc_id}/reverse",
                    {"adapter": "export", "remote_id": first["remote_id"],
                     "idempotency_key": first["idempotency_key"]})
    response = alice.post_json(f"/api/writeback/documents/{doc_id}/post",
                               {"adapter": "export", "vendor_ref": "V-DIFFERENT"})
    assert response.status_code == 200, response.text


def test_reverse_a_remote_id_belonging_to_another_tenants_document_is_404(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    """Final whole-branch review, I-1. Bob posts and owns his own document;
    the fix must not let Alice reverse Bob's remote_id just because she
    passes it alongside a document id (her own) that IS hers — before the
    fix, `reverse()` only checked that the PATH doc_id belonged to the
    caller, never that `body.remote_id` actually named that document."""
    alice, bob = two_tenants
    alice_doc = alice.seed_postable_document()
    bob_doc = bob.seed_postable_document()
    bob_posted = bob.post_json(f"/api/writeback/documents/{bob_doc}/post",
                               {"adapter": "export", "vendor_ref": "V-9"}).json()
    response = alice.post_json(f"/api/writeback/documents/{alice_doc}/reverse",
                               {"adapter": "export", "remote_id": bob_posted["remote_id"],
                                "idempotency_key": bob_posted["idempotency_key"]})
    assert response.status_code == 404, response.text


def test_reverse_a_remote_id_belonging_to_a_different_document_same_tenant_is_404(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    """Final whole-branch review, I-1 (same-tenant variant). Two documents
    owned by the SAME tenant: reversing document B's remote_id via document
    A's `{doc_id}` path parameter must still be refused — tenancy alone
    (both documents belong to alice) is not enough; the remote_id must
    belong to the document named in the path."""
    alice, _bob = two_tenants
    doc_a = alice.seed_postable_document()
    doc_b = alice.seed_postable_document()
    posted_b = alice.post_json(f"/api/writeback/documents/{doc_b}/post",
                               {"adapter": "export", "vendor_ref": "V-9"}).json()
    response = alice.post_json(f"/api/writeback/documents/{doc_a}/reverse",
                               {"adapter": "export", "remote_id": posted_b["remote_id"],
                                "idempotency_key": posted_b["idempotency_key"]})
    assert response.status_code == 404, response.text


def test_reverse_with_another_documents_idempotency_key_is_refused_and_deletes_nothing(
        two_tenants: tuple[TenantClient, TenantClient], tmp_path: Path) -> None:
    """LS-01. The ownership check covers `remote_id`, but the export adapter
    deletes by `idempotency_key`. A caller who owns document A must not be able
    to pair A's remote_id with B's key and delete B's export. The key to reverse
    is the one recorded by the audited write for that remote_id, never the
    caller's."""
    alice, bob = two_tenants
    doc_a = alice.seed_postable_document()
    doc_b = bob.seed_postable_document(content_sha256="def456")
    posted_a = alice.post_json(f"/api/writeback/documents/{doc_a}/post",
                               {"adapter": "export", "vendor_ref": "V-9"}).json()
    posted_b = bob.post_json(f"/api/writeback/documents/{doc_b}/post",
                             {"adapter": "export", "vendor_ref": "V-9"}).json()
    assert posted_a["idempotency_key"] != posted_b["idempotency_key"]

    export_file = next((tmp_path / "writeback").rglob("*.csv"))
    rows_before = export_file.read_text(encoding="utf-8")

    response = alice.post_json(f"/api/writeback/documents/{doc_a}/reverse",
                               {"adapter": "export", "remote_id": posted_a["remote_id"],
                                "idempotency_key": posted_b["idempotency_key"]})

    assert response.status_code == 422, response.text
    assert export_file.read_text(encoding="utf-8") == rows_before
    assert bob.get(f"/api/writeback/documents/{doc_b}/status").json()["state"] == "posted"
    assert alice.get(f"/api/writeback/documents/{doc_a}/status").json()["state"] == "posted"


def test_identical_documents_in_two_tenants_are_posted_and_reversed_independently(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    """LS-02. Both tenants' documents have the same content hash, contract and
    application number. The key, the audit lookups and the export must all be
    tenant-scoped: Bob's post is not a replay of Alice's, and Alice reversing
    hers neither fails nor touches Bob's."""
    alice, bob = two_tenants
    doc_a = alice.seed_postable_document()
    doc_b = bob.seed_postable_document()
    body: dict[str, object] = {"adapter": "export", "vendor_ref": "V-9"}

    posted_a = alice.post_json(f"/api/writeback/documents/{doc_a}/post", body).json()
    posted_b = bob.post_json(f"/api/writeback/documents/{doc_b}/post", body).json()

    assert posted_a["was_replay"] is False
    assert posted_b["was_replay"] is False
    assert posted_a["remote_id"] != posted_b["remote_id"]

    response = alice.post_json(f"/api/writeback/documents/{doc_a}/reverse",
                               {"adapter": "export", "remote_id": posted_a["remote_id"],
                                "idempotency_key": posted_a["idempotency_key"]})
    assert response.status_code == 200, response.text
    assert alice.get(f"/api/writeback/documents/{doc_a}/status").json()["state"] == "not_posted"
    assert bob.get(f"/api/writeback/documents/{doc_b}/status").json() == {
        "state": "posted", "remote_id": posted_b["remote_id"]}


def test_reverse_maps_qbo_rejected_to_502_without_leaking_the_provider_body(
        two_tenants: tuple[TenantClient, TenantClient], monkeypatch: pytest.MonkeyPatch) -> None:
    """Final whole-branch review, I-2. `reverse()` used to catch only
    ValueError, so a QBORejected raised by `adapter.reverse(...)` would
    surface as an uncaught 500 instead of the same 502 mapping `post()`
    already has for the identical provider-rejection case."""
    import ledger_sense.api.writeback as writeback_mod
    from ledger_sense.writeback.qbo import QBORejected

    alice, _bob = two_tenants
    doc_id = alice.seed_postable_document()
    # Posted via the real "export" adapter (no external config needed) — the
    # tenancy/ownership check (`document_for_remote`) does not filter on
    # which adapter originally posted, only which document did, so the
    # remote_id is valid for a *reverse* call against a different adapter
    # name, which is monkeypatched below to avoid needing real QBO config.
    posted = alice.post_json(f"/api/writeback/documents/{doc_id}/post",
                             {"adapter": "export", "vendor_ref": "V-9"}).json()

    def _raise(name: object, ledger_factory: object) -> object:
        class _Raiser:
            name = "qbo"
            def create_unapproved_bill(self, request: object) -> object:
                raise NotImplementedError
            def is_period_closed(self, txn_date: object) -> bool:
                return False
            def reverse(self, remote_id: object, idempotency_key: object) -> object:
                raise QBORejected("QBO rejected: 400 {'bank_account': '...CLIENT_CONFIDENTIAL...'}")
        return _Raiser()

    monkeypatch.setattr(writeback_mod, "_inner_adapter", _raise)
    response = alice.post_json(f"/api/writeback/documents/{doc_id}/reverse",
                               {"adapter": "qbo", "remote_id": posted["remote_id"],
                                "idempotency_key": posted["idempotency_key"]})
    assert response.status_code == 502, response.text
    assert "CLIENT_CONFIDENTIAL" not in response.text


def test_post_maps_qbo_rejected_to_502_without_leaking_the_provider_body(
        two_tenants: tuple[TenantClient, TenantClient], monkeypatch: pytest.MonkeyPatch) -> None:
    import ledger_sense.api.writeback as writeback_mod
    from ledger_sense.writeback.qbo import QBORejected

    def _raise(name: object, ledger: object) -> object:
        class _Raiser:
            name = "qbo"
            def create_unapproved_bill(self, request: object) -> object:
                raise QBORejected("QBO rejected: 400 {'bank_account': '...CLIENT_CONFIDENTIAL...'}")
            def is_period_closed(self, txn_date: object) -> bool:
                return False
            def reverse(self, remote_id: object, idempotency_key: object) -> object:
                raise NotImplementedError
        return _Raiser()

    monkeypatch.setattr(writeback_mod, "_inner_adapter", _raise)
    alice, _bob = two_tenants
    doc_id = alice.seed_postable_document()
    response = alice.post_json(f"/api/writeback/documents/{doc_id}/post",
                               {"adapter": "qbo", "vendor_ref": "V-9"})
    assert response.status_code == 502, response.text
    assert "CLIENT_CONFIDENTIAL" not in response.text
