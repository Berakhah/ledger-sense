from datetime import date

import pytest

from ledger_sense.domain.money import money
from ledger_sense.writeback.audit import AuditedAdapter, AuditLog
from ledger_sense.writeback.idempotency import IdempotencyLedger
from ledger_sense.writeback.interface import (
    LineItem,
    WriteRequest,
    build_idempotency_key,
)
from ledger_sense.writeback.qbo import QBOAdapter
from tests.writeback.test_qbo import FakeQBOClient


def _request():
    return WriteRequest(
        idempotency_key=build_idempotency_key("abc123", "C-1", 2),
        doc_id="doc-1", vendor_ref="V-9", vendor_invoice_number=None,
        txn_date=date(2026, 8, 31),
        line_items=[LineItem(description="Sitework", amount=money("1000.00"),
                             account_ref="54")],
        memo="Application 2")


@pytest.fixture()
def audited(tmp_path):
    ledger = IdempotencyLedger(str(tmp_path / "writes.db"))
    audit = AuditLog(str(tmp_path / "audit.db"))
    client = FakeQBOClient()
    adapter = AuditedAdapter(QBOAdapter(client, ledger, realm_id="r-1"),
                             audit=audit, approved_by="A. Bookkeeper")
    yield adapter, audit, client
    ledger.close()
    audit.close()


def test_a_write_records_document_approver_and_remote_id(audited):
    adapter, audit, _client = audited
    request = _request()
    result = adapter.create_unapproved_bill(request)
    entry = audit.for_document("doc-1")[0]
    assert entry.action == "write"
    assert entry.approved_by == "A. Bookkeeper"
    assert entry.remote_id == result.remote_id


def test_a_reversal_is_recorded_against_the_same_document(audited):
    adapter, audit, _client = audited
    request = _request()
    result = adapter.create_unapproved_bill(request)
    adapter.reverse(result.remote_id, request.idempotency_key)
    assert [e.action for e in audit.for_document("doc-1")] == ["write", "reversal"]


def test_reversal_removes_the_bill_from_the_accounting_system(audited):
    adapter, _audit, client = audited
    request = _request()
    result = adapter.create_unapproved_bill(request)
    adapter.reverse(result.remote_id, request.idempotency_key)
    assert result.remote_id not in client.bills


def test_reversal_restores_the_prior_state_exactly(audited):
    adapter, _audit, client = audited
    before = dict(client.bills)
    request = _request()
    result = adapter.create_unapproved_bill(request)
    adapter.reverse(result.remote_id, request.idempotency_key)
    assert client.bills == before


def test_a_write_without_a_named_approver_is_refused(tmp_path):
    ledger = IdempotencyLedger(str(tmp_path / "w.db"))
    audit = AuditLog(str(tmp_path / "a.db"))
    adapter = AuditedAdapter(QBOAdapter(FakeQBOClient(), ledger, realm_id="r-1"),
                             audit=audit, approved_by="")
    with pytest.raises(ValueError, match="approved_by"):
        adapter.create_unapproved_bill(_request())
    ledger.close()
    audit.close()


def test_the_audit_trail_has_no_delete_or_update_method(audited):
    """An audit log you can edit is not an audit log."""
    _adapter, audit, _client = audited
    for forbidden in ("delete_entry", "update_entry", "purge", "clear"):
        assert not hasattr(audit, forbidden)


def test_an_unmodified_chain_verifies(audited):
    """F-43. The test above asserts four method names are absent from a Python
    class, while the SQLite file is writable by the same process. What the client
    hears — "full audit trail" — implies tamper evidence."""
    adapter, audit, _client = audited
    adapter.create_unapproved_bill(_request())
    assert audit.verify_chain() == []


def test_an_edited_entry_is_detected_and_named(audited, tmp_path):
    adapter, audit, _client = audited
    request = _request()
    result = adapter.create_unapproved_bill(request)
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "audit.db"))
    conn.execute("UPDATE audit SET detail = 'nothing to see here'")
    conn.commit()
    conn.close()
    broken = audit.verify_chain()
    assert broken, "an edited audit entry verified clean"
    assert result.remote_id in str(broken)


def test_a_reversal_after_a_restart_still_names_the_document(tmp_path):
    """F-42. `_doc_by_remote` was an in-memory dict, so a reversal after a
    process restart audited doc_id='unknown' — and a reversal is exactly the
    operation most likely to happen a day later."""
    ledger = IdempotencyLedger(str(tmp_path / "writes.db"))
    audit = AuditLog(str(tmp_path / "audit.db"))
    client = FakeQBOClient()
    request = _request()

    first = AuditedAdapter(QBOAdapter(client, ledger, realm_id="r-1"),
                           audit=audit, approved_by="A. Bookkeeper")
    result = first.create_unapproved_bill(request)

    del first                                   # the process restarts
    reopened = AuditedAdapter(QBOAdapter(client, ledger, realm_id="r-1"),
                              audit=AuditLog(str(tmp_path / "audit.db")),
                              approved_by="A. Bookkeeper")
    reopened.reverse(result.remote_id, request.idempotency_key)

    entries = AuditLog(str(tmp_path / "audit.db")).for_document("doc-1")
    assert [e.action for e in entries] == ["write", "reversal"]
    ledger.close()


def test_reversing_an_unaudited_remote_id_is_refused(audited):
    """We do not delete objects in a client's books that we have no record of
    creating."""
    adapter, _audit, _client = audited
    with pytest.raises(ValueError, match="no audited write"):
        adapter.reverse("999", "LSsomekeywedidnotwrite")


def test_a_replayed_write_is_audited_as_a_replay_not_a_second_write(audited):
    adapter, audit, _client = audited
    adapter.create_unapproved_bill(_request())
    adapter.create_unapproved_bill(_request())
    details = [e.detail for e in audit.for_document("doc-1")]
    assert len(details) == 2
    assert "replay" in details[1].lower()


# --- Additional tests beyond the brief: hash chain internals ---


def test_verify_chain_is_empty_on_a_fresh_empty_log(tmp_path):
    audit = AuditLog(str(tmp_path / "audit.db"))
    assert audit.verify_chain() == []
    audit.close()


def test_entries_carry_a_populated_hash_chain(audited):
    """Each row must have prev_hash/entry_hash populated by _append — not left
    to a schema default — since verify_chain's correctness depends on the
    chain actually being computed, not merely declared in the schema."""
    adapter, audit, _client = audited
    adapter.create_unapproved_bill(_request())
    row = audit._conn.execute(
        "SELECT prev_hash, entry_hash FROM audit ORDER BY rowid").fetchone()
    assert row[0] == "0" * 64
    assert row[1] and row[1] != "0" * 64
    assert len(row[1]) == 64


def test_second_entry_chains_from_the_first(audited):
    adapter, audit, _client = audited
    request = _request()
    result = adapter.create_unapproved_bill(request)
    adapter.reverse(result.remote_id, request.idempotency_key)
    rows = audit._conn.execute(
        "SELECT prev_hash, entry_hash FROM audit ORDER BY rowid").fetchall()
    assert len(rows) == 2
    assert rows[1][0] == rows[0][1]


def test_document_for_remote_returns_none_when_unknown(tmp_path):
    audit = AuditLog(str(tmp_path / "audit.db"))
    assert audit.document_for_remote("nope") is None
    audit.close()


def test_document_for_remote_finds_the_write_entry(audited):
    adapter, audit, _client = audited
    request = _request()
    result = adapter.create_unapproved_bill(request)
    assert audit.document_for_remote(result.remote_id) == "doc-1"
