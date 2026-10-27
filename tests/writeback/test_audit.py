from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from ledger_sense.domain.money import money
from ledger_sense.writeback.audit import AuditedAdapter, AuditLog
from ledger_sense.writeback.export import ExportAdapter
from ledger_sense.writeback.idempotency import IdempotencyLedger
from ledger_sense.writeback.interface import (
    LineItem,
    WritebackPort,
    WriteRequest,
    WriteResult,
    build_idempotency_key,
)
from ledger_sense.writeback.qbo import QBOAdapter
from tests.writeback.test_qbo import FakeQBOClient


def _request() -> WriteRequest:
    return WriteRequest(
        idempotency_key=build_idempotency_key("abc123", "C-1", 2, tenant_id="t-test"),
        doc_id="doc-1", vendor_ref="V-9", vendor_invoice_number=None,
        txn_date=date(2026, 8, 31),
        line_items=[LineItem(description="Sitework", amount=money("1000.00"),
                             account_ref="54")],
        memo="Application 2")


@pytest.fixture()
def audited(tmp_path: Path) -> Iterator[tuple[AuditedAdapter, AuditLog, FakeQBOClient]]:
    ledger = IdempotencyLedger(str(tmp_path / "writes.db"))
    audit = AuditLog(str(tmp_path / "audit.db"))
    client = FakeQBOClient()
    adapter = AuditedAdapter(QBOAdapter(client, ledger, realm_id="r-1"),
                             audit=audit, approved_by="A. Bookkeeper")
    yield adapter, audit, client
    ledger.close()
    audit.close()


def test_a_write_records_document_approver_and_remote_id(audited: tuple[AuditedAdapter, AuditLog, FakeQBOClient]) -> None:
    adapter, audit, _client = audited
    request = _request()
    result = adapter.create_unapproved_bill(request)
    entry = audit.for_document("doc-1")[0]
    assert entry.action == "write"
    assert entry.approved_by == "A. Bookkeeper"
    assert entry.remote_id == result.remote_id


def test_a_reversal_is_recorded_against_the_same_document(audited: tuple[AuditedAdapter, AuditLog, FakeQBOClient]) -> None:
    adapter, audit, _client = audited
    request = _request()
    result = adapter.create_unapproved_bill(request)
    adapter.reverse(result.remote_id, request.idempotency_key)
    assert [e.action for e in audit.for_document("doc-1")] == ["write", "reversal"]


def test_reversal_removes_the_bill_from_the_accounting_system(audited: tuple[AuditedAdapter, AuditLog, FakeQBOClient]) -> None:
    adapter, _audit, client = audited
    request = _request()
    result = adapter.create_unapproved_bill(request)
    adapter.reverse(result.remote_id, request.idempotency_key)
    assert result.remote_id not in client.bills


def test_reversal_restores_the_prior_state_exactly(audited: tuple[AuditedAdapter, AuditLog, FakeQBOClient]) -> None:
    adapter, _audit, client = audited
    before = dict(client.bills)
    request = _request()
    result = adapter.create_unapproved_bill(request)
    adapter.reverse(result.remote_id, request.idempotency_key)
    assert client.bills == before


def test_a_write_without_a_named_approver_is_refused(tmp_path: Path) -> None:
    ledger = IdempotencyLedger(str(tmp_path / "w.db"))
    audit = AuditLog(str(tmp_path / "a.db"))
    adapter = AuditedAdapter(QBOAdapter(FakeQBOClient(), ledger, realm_id="r-1"),
                             audit=audit, approved_by="")
    with pytest.raises(ValueError, match="approved_by"):
        adapter.create_unapproved_bill(_request())
    ledger.close()
    audit.close()


def test_the_audit_trail_has_no_delete_or_update_method(audited: tuple[AuditedAdapter, AuditLog, FakeQBOClient]) -> None:
    """An audit log you can edit is not an audit log."""
    _adapter, audit, _client = audited
    for forbidden in ("delete_entry", "update_entry", "purge", "clear"):
        assert not hasattr(audit, forbidden)


def test_an_unmodified_chain_verifies(audited: tuple[AuditedAdapter, AuditLog, FakeQBOClient]) -> None:
    """F-43. The test above asserts four method names are absent from a Python
    class, while the SQLite file is writable by the same process. What the client
    hears — "full audit trail" — implies tamper evidence."""
    adapter, audit, _client = audited
    adapter.create_unapproved_bill(_request())
    assert audit.verify_chain() == []


def test_an_edited_entry_is_detected_and_named(audited: tuple[AuditedAdapter, AuditLog, FakeQBOClient], tmp_path: Path) -> None:
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


def test_an_edited_approved_by_is_detected_and_named(audited: tuple[AuditedAdapter, AuditLog, FakeQBOClient], tmp_path: Path) -> None:
    """I-3. The only prior tamper test edits `detail`, so a hash function
    that (by bug) only ever covered `detail` would still pass the whole
    suite. `approved_by` is literally "the named human on the record" that
    `create_unapproved_bill` refuses to proceed without — forging it after
    the fact (a bookkeeper's name swapped onto a write they never approved)
    is exactly the tamper this chain exists to catch, and needs its own
    explicit regression guard rather than incidental coverage via the
    payload's field list.
    """
    adapter, audit, _client = audited
    request = _request()
    result = adapter.create_unapproved_bill(request)
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "audit.db"))
    # Forged directly via raw SQL, NOT by recomputing the hash — a real
    # tamper never gets to update entry_hash to match.
    conn.execute("UPDATE audit SET approved_by = 'Someone Else'")
    conn.commit()
    conn.close()
    broken = audit.verify_chain()
    assert broken, "an entry with a forged approved_by verified clean"
    assert result.remote_id in str(broken)


def test_a_reversal_after_a_restart_still_names_the_document(tmp_path: Path) -> None:
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


def test_reversing_an_unaudited_remote_id_is_refused(audited: tuple[AuditedAdapter, AuditLog, FakeQBOClient]) -> None:
    """We do not delete objects in a client's books that we have no record of
    creating."""
    adapter, _audit, _client = audited
    with pytest.raises(ValueError, match="no audited write"):
        adapter.reverse("999", "LSsomekeywedidnotwrite")


def test_a_replayed_write_is_audited_as_a_replay_not_a_second_write(audited: tuple[AuditedAdapter, AuditLog, FakeQBOClient]) -> None:
    adapter, audit, _client = audited
    adapter.create_unapproved_bill(_request())
    adapter.create_unapproved_bill(_request())
    details = [e.detail for e in audit.for_document("doc-1")]
    assert len(details) == 2
    assert "replay" in details[1].lower()


# --- Additional tests beyond the brief: hash chain internals ---


def test_verify_chain_is_empty_on_a_fresh_empty_log(tmp_path: Path) -> None:
    audit = AuditLog(str(tmp_path / "audit.db"))
    assert audit.verify_chain() == []
    audit.close()


def test_entries_carry_a_populated_hash_chain(audited: tuple[AuditedAdapter, AuditLog, FakeQBOClient]) -> None:
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


def test_second_entry_chains_from_the_first(audited: tuple[AuditedAdapter, AuditLog, FakeQBOClient]) -> None:
    adapter, audit, _client = audited
    request = _request()
    result = adapter.create_unapproved_bill(request)
    adapter.reverse(result.remote_id, request.idempotency_key)
    rows = audit._conn.execute(
        "SELECT prev_hash, entry_hash FROM audit ORDER BY rowid").fetchall()
    assert len(rows) == 2
    assert rows[1][0] == rows[0][1]


def test_document_for_remote_returns_none_when_unknown(tmp_path: Path) -> None:
    audit = AuditLog(str(tmp_path / "audit.db"))
    assert audit.document_for_remote("nope") is None
    audit.close()


def test_document_for_remote_finds_the_write_entry(audited: tuple[AuditedAdapter, AuditLog, FakeQBOClient]) -> None:
    adapter, audit, _client = audited
    request = _request()
    result = adapter.create_unapproved_bill(request)
    assert audit.document_for_remote(result.remote_id) == "doc-1"


def test_concurrent_appends_do_not_fork_the_chain(tmp_path: Path) -> None:
    """I-1. `_last_hash` (a SELECT) and the following INSERT used to be two
    separate implicit transactions, so two threads could both read the same
    chain tip and append from it — corrupting the chain and making
    `verify_chain` report a FALSE tamper accusation against an entry nobody
    touched. Several threads hammering one `AuditLog` concurrently, with the
    chain verifying clean afterward, is the regression guard: the previous
    (single-threaded) suite could not see this class of bug at all.
    """
    import threading

    audit = AuditLog(str(tmp_path / "audit.db"))
    threads_n, appends_per_thread = 4, 25
    errors: list[BaseException] = []

    def _hammer(worker: int) -> None:
        try:
            for i in range(appends_per_thread):
                result = WriteResult(
                    remote_id=f"w{worker}-{i}", idempotency_key=f"key-{worker}-{i}",
                    adapter="qbo", created_at=datetime.now(UTC), was_replay=False)
                audit.record_write(f"doc-{worker}-{i}", "qbo", result,
                                   approved_by="A. Bookkeeper", vendor_ref="V-1")
        except BaseException as exc:  # noqa: BLE001 - surfaced via `errors`
            errors.append(exc)

    threads = [threading.Thread(target=_hammer, args=(w,)) for w in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"a writer thread raised: {errors}"
    rows = audit._conn.execute("SELECT COUNT(*) FROM audit").fetchone()
    assert rows[0] == threads_n * appends_per_thread
    assert audit.verify_chain() == []
    audit.close()


# ---------------------------------------------------------------------------
# Final whole-branch review findings
# ---------------------------------------------------------------------------

def test_audited_adapter_satisfies_the_writeback_port(audited: tuple[AuditedAdapter, AuditLog, FakeQBOClient]) -> None:
    """I-1. QBOAdapter and ExportAdapter both had this isinstance check
    (plus a mypy-time static counterpart in their own modules); AuditedAdapter
    — the composition root production code would actually inject — had
    neither. WritebackPort is @runtime_checkable, so this is a real
    isinstance check, not just a type-checker-only assertion (see the
    TYPE_CHECKING-guarded static check in audit.py for the mypy-time
    counterpart)."""
    adapter, _audit, _client = audited
    assert isinstance(adapter, WritebackPort)


def test_reversal_audit_entry_records_the_actual_idempotency_key(audited: tuple[AuditedAdapter, AuditLog, FakeQBOClient]) -> None:
    """M-C. `record_reversal` used to hardcode `idempotency_key=None` even
    though `AuditedAdapter.reverse` already has the key on hand — dropping
    it from the reversal's own audit row is an unnecessary omission in a
    phase about reversal<->key linkage."""
    adapter, audit, _client = audited
    request = _request()
    result = adapter.create_unapproved_bill(request)
    adapter.reverse(result.remote_id, request.idempotency_key)
    reversal_entry = audit.for_document("doc-1")[1]
    assert reversal_entry.action == "reversal"
    assert reversal_entry.idempotency_key == request.idempotency_key


def test_write_reverse_write_through_audited_qbo_adapter_produces_a_new_bill(tmp_path: Path) -> None:
    """I-2. The reviewer manually verified this path (AuditedAdapter wrapping
    QBOAdapter) outside the test suite; it had zero committed coverage
    despite being the exact composition production code would inject.
    Write -> reverse -> write must produce a NEW bill (not a dead replay),
    with a clean 3-entry audit trail."""
    ledger = IdempotencyLedger(str(tmp_path / "writes.db"))
    audit = AuditLog(str(tmp_path / "audit.db"))
    client = FakeQBOClient()
    adapter = AuditedAdapter(QBOAdapter(client, ledger, realm_id="r-1"),
                             audit=audit, approved_by="A. Bookkeeper")
    request = _request()

    first = adapter.create_unapproved_bill(request)
    adapter.reverse(first.remote_id, request.idempotency_key)
    second = adapter.create_unapproved_bill(request)

    assert second.remote_id != first.remote_id, "the second write replayed the dead bill"
    assert second.was_replay is False

    entries = audit.for_document("doc-1")
    assert [e.action for e in entries] == ["write", "reversal", "write"]
    assert audit.verify_chain() == []
    ledger.close()
    audit.close()


def test_record_write_carries_the_requests_vendor_fields(tmp_path: Path) -> None:
    """F-40's DocNumber/PrivateNote split lives in qbo.py; this is the
    audit-side half — the vendor identity a bill was posted under must be
    recoverable later without re-deriving it, because the idempotency key
    (build_idempotency_key) deliberately excludes vendor identity (Ruling 5,
    Phase 6 spec) and a later call with different vendor fields needs
    something to compare against."""
    audit = AuditLog(str(tmp_path / "audit.db"))
    adapter = AuditedAdapter(ExportAdapter(out_dir=str(tmp_path / "export")),
                             audit=audit, approved_by="reviewer@example.test")
    request = WriteRequest(
        idempotency_key=build_idempotency_key("h1", "C-1", 1, tenant_id="t-test"), doc_id="doc-1",
        vendor_ref="V-9", vendor_invoice_number="INV-1", txn_date=date(2026, 1, 1),
        line_items=[LineItem(description="Sitework", amount=money("100.00"), account_ref="54")],
        memo="m")
    adapter.create_unapproved_bill(request)

    entries = audit.for_document("doc-1")
    assert len(entries) == 1
    assert entries[0].vendor_ref == "V-9"
    assert entries[0].vendor_invoice_number == "INV-1"
    audit.close()


def test_record_reversal_carries_no_vendor_fields(tmp_path: Path) -> None:
    """A reversal call (remote_id + idempotency_key only) has no vendor data
    of its own to record — None, not a stale copy of the original write's."""
    audit = AuditLog(str(tmp_path / "audit.db"))
    adapter = AuditedAdapter(ExportAdapter(out_dir=str(tmp_path / "export")),
                             audit=audit, approved_by="reviewer@example.test")
    request = WriteRequest(
        idempotency_key=build_idempotency_key("h1", "C-1", 1, tenant_id="t-test"), doc_id="doc-1",
        vendor_ref="V-9", vendor_invoice_number=None, txn_date=date(2026, 1, 1),
        line_items=[LineItem(description="Sitework", amount=money("100.00"), account_ref="54")],
        memo="m")
    result = adapter.create_unapproved_bill(request)
    adapter.reverse(result.remote_id, result.idempotency_key)

    entries = audit.for_document("doc-1")
    reversal = next(e for e in entries if e.action == "reversal")
    assert reversal.vendor_ref is None
    assert reversal.vendor_invoice_number is None
    audit.close()


def test_write_reverse_write_through_audited_export_adapter_produces_a_new_bill(tmp_path: Path) -> None:
    """I-2, the ExportAdapter composition. Not the QBO adapter — the export
    peer adapter, which reuses the idempotency key itself as the remote id."""
    audit = AuditLog(str(tmp_path / "audit.db"))
    adapter = AuditedAdapter(ExportAdapter(out_dir=str(tmp_path / "export")),
                             audit=audit, approved_by="A. Bookkeeper")
    request = _request()

    first = adapter.create_unapproved_bill(request)
    adapter.reverse(first.remote_id, request.idempotency_key)
    second = adapter.create_unapproved_bill(request)

    assert second.was_replay is False, "the second write replayed instead of writing fresh"

    entries = audit.for_document("doc-1")
    assert [e.action for e in entries] == ["write", "reversal", "write"]
    assert audit.verify_chain() == []
    audit.close()


def test_audit_log_from_environment_builds_a_log_under_the_configured_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ledger_sense.writeback.audit import audit_log_from_environment
    monkeypatch.setenv("LEDGER_SENSE_WRITEBACK_DIR", str(tmp_path))
    audit = audit_log_from_environment("t-test")
    assert audit.verify_chain() == []
    assert (tmp_path / "audit.db").exists()


def test_audit_log_from_environment_raises_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    from ledger_sense.writeback.audit import audit_log_from_environment
    monkeypatch.delenv("LEDGER_SENSE_WRITEBACK_DIR", raising=False)
    with pytest.raises(RuntimeError, match="LEDGER_SENSE_WRITEBACK_DIR"):
        audit_log_from_environment("t-test")
