"""QBOAdapter: unpaid bills only, crash-safe idempotency.

DocNumber/PrivateNote split (F-40, resolved 2026-08-29): the idempotency key
NEVER overwrites the vendor's own invoice number in QBO `DocNumber`, because
that field is what QBO's own duplicate-document-number warning keys on and
what a bookkeeper reconciles against paper. When a vendor invoice number was
extracted, DocNumber holds it unchanged and the idempotency key goes into
PrivateNote instead. Only when no vendor invoice number was extracted does
DocNumber fall back to holding the key, as before.

That split means crash reconciliation is two different queries: DocNumber
lookup in the fallback case, and a bounded TxnDate+VendorRef scan matching
PrivateNote client-side in the normal case (QBO cannot query on PrivateNote).
"""

import ast
import pathlib
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

from ledger_sense.domain.money import money
from ledger_sense.writeback.idempotency import IdempotencyLedger
from ledger_sense.writeback.interface import (
    ClosedPeriodError,
    LineItem,
    WritebackPort,
    WriteRequest,
    build_idempotency_key,
)
from ledger_sense.writeback.qbo import (
    AWAITING_REVIEW,
    ConcurrentWriteInProgressError,
    QBOAdapter,
    extract_key_from_private_note,
    qbo_adapter_from_environment,
)


class FakeQBOClient:
    """Stands in for the sandbox. `fail_after_post` reproduces the crash that
    happens between QBO accepting the bill and us recording it."""

    def __init__(self, book_close: date | None = None,
                 fail_after_post: bool = False) -> None:
        self.bills: dict[str, dict[str, Any]] = {}
        self.payments: list[dict[str, Any]] = []
        self.book_close = book_close
        self.fail_after_post = fail_after_post
        self._next_id = 100

    def post_bill(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        remote_id = str(self._next_id)
        self.bills[remote_id] = {**payload, "Id": remote_id, "Balance": "1000.00"}
        if self.fail_after_post:
            raise ConnectionError("connection dropped after QBO accepted the bill")
        return {"Id": remote_id}

    def find_bill_by_docnumber(self, doc_number: str) -> dict[str, Any] | None:
        return next((b for b in self.bills.values()
                     if b.get("DocNumber") == doc_number), None)

    def find_bills_by_date_and_vendor(self, txn_date: date, vendor_ref: str) -> list[dict[str, Any]]:
        iso = txn_date.isoformat()
        return [b for b in self.bills.values()
                if b.get("TxnDate") == iso
                and b.get("VendorRef", {}).get("value") == vendor_ref]

    def get_book_close_date(self) -> date | None:
        return self.book_close

    def delete_bill(self, remote_id: str) -> None:
        self.bills.pop(remote_id, None)

    def post_bill_payment(self, payload: dict[str, Any]) -> dict[str, Any]:  # must never be called
        self.payments.append(payload)
        return {"Id": "pay-1"}


@pytest.fixture()
def ledger(tmp_path: Path) -> Iterator[IdempotencyLedger]:
    led = IdempotencyLedger(str(tmp_path / "writes.db"))
    yield led
    led.close()


def _request(application_no: int = 2, txn_date: date = date(2026, 8, 31), vendor_invoice_number: str | None = "INV-4471") -> WriteRequest:
    return WriteRequest(
        idempotency_key=build_idempotency_key("abc123", "C-1", application_no, tenant_id="t-test"),
        doc_id="doc-1", vendor_ref="V-9", vendor_invoice_number=vendor_invoice_number,
        txn_date=txn_date,
        line_items=[LineItem(description="Sitework", amount=money("1000.00"),
                             account_ref="54")],
        memo="Application 2")


# ---------------------------------------------------------------------------
# Baseline behavior — unpaid bills only, never a BillPayment
# ---------------------------------------------------------------------------

def test_a_bill_is_created_unpaid_and_marked_awaiting_review(ledger: IdempotencyLedger) -> None:
    client = FakeQBOClient()
    result = QBOAdapter(client, ledger, realm_id="r-1").create_unapproved_bill(_request())
    assert AWAITING_REVIEW in client.bills[result.remote_id]["PrivateNote"]


def test_qbo_adapter_satisfies_the_writeback_port(ledger: IdempotencyLedger) -> None:
    """I-2. The brief requires QBOAdapter to satisfy WritebackPort; only
    FakeAdapter (test_interface.py) was ever checked against the protocol.
    WritebackPort is @runtime_checkable, so this is a real isinstance check,
    not just a type-checker-only assertion (see the TYPE_CHECKING-guarded
    static check in qbo.py for the mypy-time counterpart)."""
    adapter = QBOAdapter(FakeQBOClient(), ledger, realm_id="r-1")
    assert isinstance(adapter, WritebackPort)


def test_no_bill_payment_is_ever_created(ledger: IdempotencyLedger) -> None:
    """v1 never posts an approved transaction, no matter what the client asks."""
    client = FakeQBOClient()
    QBOAdapter(client, ledger, realm_id="r-1").create_unapproved_bill(_request())
    assert client.payments == []


def test_no_code_path_calls_post_bill_payment() -> None:
    """AST guard. The absence of a call today is not a guarantee tomorrow.

    Resolved from __file__ (I-3.2), not cwd, so the scan is not silently
    empty when pytest is invoked from a different working directory. Catches
    both `obj.post_bill_payment(...)` (ast.Attribute) and a bare
    `post_bill_payment(...)` (ast.Name) call, plus flags any
    "post_bill_payment" string literal anywhere under ledger_sense/ as a
    belt-and-braces check against a `getattr(obj, "post_bill_payment")(...)`
    evasion (F-32 warns this project's own audit history against exactly
    this class of stringly-typed dodge). A file that fails to parse (I-3.3,
    e.g. a stray BOM) fails with a readable message naming the file instead
    of turning this policy test into an opaque SyntaxError.
    """
    ledger_sense_root = pathlib.Path(__file__).resolve().parents[2] / "ledger_sense"
    assert ledger_sense_root.is_dir(), f"expected package root at {ledger_sense_root}"

    scanned = list(ledger_sense_root.rglob("*.py"))
    assert len(scanned) >= 10, (
        f"only found {len(scanned)} files under {ledger_sense_root} — the scan "
        f"looks empty or truncated, which would make this guard pass vacuously")

    offenders = []
    for path in scanned:
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise AssertionError(
                f"could not parse {path} for the post_bill_payment AST guard: "
                f"{exc}") from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (
                    getattr(node.func, "attr", "") == "post_bill_payment"
                    or getattr(node.func, "id", "") == "post_bill_payment"):
                offenders.append(f"{path}:{node.lineno}")
            if (isinstance(node, ast.Constant) and node.value == "post_bill_payment"):
                offenders.append(
                    f"{path}:{node.lineno} (string literal — possible getattr evasion)")
    assert offenders == [], f"BillPayment created at: {offenders}"


# ---------------------------------------------------------------------------
# DocNumber / PrivateNote split (F-40)
# ---------------------------------------------------------------------------

def test_vendor_invoice_number_present_keeps_docnumber_and_keys_privatenote(ledger: IdempotencyLedger) -> None:
    client = FakeQBOClient()
    request = _request(vendor_invoice_number="INV-4471")
    result = QBOAdapter(client, ledger, realm_id="r-1").create_unapproved_bill(request)
    bill = client.bills[result.remote_id]
    assert bill["DocNumber"] == "INV-4471", (
        "the vendor's own invoice number must survive unchanged in DocNumber")
    assert extract_key_from_private_note(bill["PrivateNote"]) == request.idempotency_key


def test_no_vendor_invoice_number_falls_back_to_key_in_docnumber(ledger: IdempotencyLedger) -> None:
    client = FakeQBOClient()
    request = _request(vendor_invoice_number=None)
    result = QBOAdapter(client, ledger, realm_id="r-1").create_unapproved_bill(request)
    bill = client.bills[result.remote_id]
    assert bill["DocNumber"] == request.idempotency_key
    # The fallback is recorded so a human reading the books later understands
    # why DocNumber looks like a key rather than an invoice number.
    assert "no vendor invoice number" in bill["PrivateNote"].lower()


def test_fallback_privatenote_still_carries_a_recoverable_key(ledger: IdempotencyLedger) -> None:
    client = FakeQBOClient()
    request = _request(vendor_invoice_number=None)
    result = QBOAdapter(client, ledger, realm_id="r-1").create_unapproved_bill(request)
    bill = client.bills[result.remote_id]
    assert extract_key_from_private_note(bill["PrivateNote"]) == request.idempotency_key


# ---------------------------------------------------------------------------
# Replay / crash reconciliation — WITH a vendor invoice number
# (queries by TxnDate + VendorRef, then matches PrivateNote client-side)
# ---------------------------------------------------------------------------

def test_a_retry_after_success_replays_rather_than_creating_a_second_bill(ledger: IdempotencyLedger) -> None:
    client = FakeQBOClient()
    adapter = QBOAdapter(client, ledger, realm_id="r-1")
    request = _request(vendor_invoice_number="INV-4471")
    first = adapter.create_unapproved_bill(request)
    second = adapter.create_unapproved_bill(request)
    assert second.remote_id == first.remote_id
    assert second.was_replay is True
    assert len(client.bills) == 1


def test_force_failing_mid_write_then_retrying_creates_exactly_one_bill(ledger: IdempotencyLedger) -> None:
    """THE TEST THIS PHASE EXISTS FOR. QBO accepted the bill; the connection
    died before we recorded it. The retry must find the stamped key via the
    TxnDate+VendorRef scan (DocNumber holds the vendor invoice number, not the
    key, so a DocNumber query cannot find it), not post again."""
    request = _request(vendor_invoice_number="INV-4471")
    crashing = FakeQBOClient(fail_after_post=True)
    with pytest.raises(ConnectionError):
        QBOAdapter(crashing, ledger, realm_id="r-1").create_unapproved_bill(request)
    assert len(crashing.bills) == 1

    crashing.fail_after_post = False
    result = QBOAdapter(crashing, ledger, realm_id="r-1").create_unapproved_bill(request)

    assert len(crashing.bills) == 1, "the retry created a duplicate bill"
    assert result.was_replay is True


def test_the_bounded_scan_does_not_match_a_different_vendors_bill_same_day(ledger: IdempotencyLedger) -> None:
    """A different vendor's bill on the same date must not be mistaken for a
    match just because it shares TxnDate; VendorRef narrows the scan, and the
    PrivateNote key match is the final word."""
    client = FakeQBOClient()
    adapter = QBOAdapter(client, ledger, realm_id="r-1")
    other_vendor_request = WriteRequest(
        idempotency_key=build_idempotency_key("other", "C-2", 1, tenant_id="t-test"),
        doc_id="doc-2", vendor_ref="V-OTHER", vendor_invoice_number="INV-9999",
        txn_date=date(2026, 8, 31),
        line_items=[LineItem(description="Demo", amount=money("500.00"),
                             account_ref="54")],
        memo="Application 1")
    adapter.create_unapproved_bill(other_vendor_request)

    request = _request(vendor_invoice_number="INV-4471")
    result = adapter.create_unapproved_bill(request)
    assert len(client.bills) == 2
    assert result.remote_id != next(iter(client.bills))


def test_multiple_same_vendor_same_date_bills_match_by_key_not_position(ledger: IdempotencyLedger) -> None:
    """I-1. The realistic construction-billing case this guard exists for:
    two different applications for the SAME subcontractor, both dated
    month-end (e.g. two contracts, or two applications posted the same day).
    `find_bills_by_date_and_vendor` legitimately returns more than one
    candidate here, so matching cannot stop at the first result — it must
    compare the embedded key. A mutant that replaced the key comparison with
    an unconditional `return candidate` passed every other test in this file
    but fails this one: it would silently adopt request_a's bill as though
    it were request_b's, never call post_bill for request_b, and commit
    request_b's idempotency key against request_a's remote_id."""
    client = FakeQBOClient()
    adapter = QBOAdapter(client, ledger, realm_id="r-1")

    request_a = _request(application_no=1, vendor_invoice_number="INV-1001")
    request_b = _request(application_no=2, vendor_invoice_number="INV-1002")
    assert request_a.vendor_ref == request_b.vendor_ref
    assert request_a.txn_date == request_b.txn_date
    assert request_a.idempotency_key != request_b.idempotency_key

    first = adapter.create_unapproved_bill(request_a)
    # At this point find_bills_by_date_and_vendor(date, vendor) for request_b
    # returns exactly one candidate: request_a's bill, keyed under a
    # DIFFERENT idempotency key. The match must reject it as a false replay
    # and go on to create a genuinely new bill.
    second = adapter.create_unapproved_bill(request_b)

    assert second.was_replay is False, (
        "matched a same-vendor/same-date bill belonging to a DIFFERENT "
        "idempotency key as though it were a replay")
    assert second.remote_id != first.remote_id
    assert len(client.bills) == 2
    record = ledger.lookup(request_a.idempotency_key)
    assert record is not None
    assert record.remote_id == first.remote_id
    record = ledger.lookup(request_b.idempotency_key)
    assert record is not None
    assert record.remote_id == second.remote_id


# ---------------------------------------------------------------------------
# Replay / crash reconciliation — WITHOUT a vendor invoice number
# (direct DocNumber query, unchanged from the original design)
# ---------------------------------------------------------------------------

def test_a_retry_after_success_replays_in_the_no_invoice_number_case(ledger: IdempotencyLedger) -> None:
    client = FakeQBOClient()
    adapter = QBOAdapter(client, ledger, realm_id="r-1")
    request = _request(vendor_invoice_number=None)
    first = adapter.create_unapproved_bill(request)
    second = adapter.create_unapproved_bill(request)
    assert second.remote_id == first.remote_id
    assert second.was_replay is True
    assert len(client.bills) == 1


def test_force_failing_mid_write_then_retrying_in_the_no_invoice_number_case(ledger: IdempotencyLedger) -> None:
    request = _request(vendor_invoice_number=None)
    crashing = FakeQBOClient(fail_after_post=True)
    with pytest.raises(ConnectionError):
        QBOAdapter(crashing, ledger, realm_id="r-1").create_unapproved_bill(request)
    assert len(crashing.bills) == 1

    crashing.fail_after_post = False
    result = QBOAdapter(crashing, ledger, realm_id="r-1").create_unapproved_bill(request)

    assert len(crashing.bills) == 1, "the retry created a duplicate bill"
    assert result.was_replay is True


# ---------------------------------------------------------------------------
# Closed period
# ---------------------------------------------------------------------------

def test_a_closed_period_is_refused_with_an_explanation(ledger: IdempotencyLedger) -> None:
    client = FakeQBOClient(book_close=date(2026, 9, 30))
    with pytest.raises(ClosedPeriodError) as exc:
        QBOAdapter(client, ledger, realm_id="r-1").create_unapproved_bill(
            _request(txn_date=date(2026, 8, 31)))
    assert "2026-09-30" in str(exc.value)
    assert client.bills == {}, "a bill was created despite the closed period"


def test_the_close_date_is_checked_before_anything_is_recorded(ledger: IdempotencyLedger) -> None:
    client = FakeQBOClient(book_close=date(2026, 12, 31))
    request = _request()
    with pytest.raises(ClosedPeriodError):
        QBOAdapter(client, ledger, realm_id="r-1").create_unapproved_bill(request)
    assert ledger.lookup(request.idempotency_key) is None


def test_a_date_after_the_close_is_permitted(ledger: IdempotencyLedger) -> None:
    client = FakeQBOClient(book_close=date(2026, 7, 31))
    result = QBOAdapter(client, ledger, realm_id="r-1").create_unapproved_bill(
        _request(txn_date=date(2026, 8, 31)))
    assert result.remote_id in client.bills


# ---------------------------------------------------------------------------
# Reversal — F-39 regression, both DocNumber paths
# ---------------------------------------------------------------------------

def test_reversal_deletes_the_bill(ledger: IdempotencyLedger) -> None:
    client = FakeQBOClient()
    adapter = QBOAdapter(client, ledger, realm_id="r-1")
    request = _request()
    result = adapter.create_unapproved_bill(request)
    reversal = adapter.reverse(result.remote_id, request.idempotency_key)
    assert reversal.method == "deleted"
    assert result.remote_id not in client.bills


def test_a_write_after_a_reversal_creates_a_new_bill_not_a_dead_replay(ledger: IdempotencyLedger) -> None:
    """REGRESSION — F-39, the worst defect in this phase.

    `reverse` deleted the remote bill and left the ledger row saying `committed`
    with the now-dead remote_id. The next write with the same key hit the
    committed branch and returned WriteResult(remote_id=<deleted id>,
    was_replay=True): a success response pointing at an object that no longer
    exists in QuickBooks, and an audit entry recording a write that did not
    happen. Write -> reverse -> write was impossible, and it failed silently.

    Exercised WITH a vendor invoice number, the code path this phase's
    correction actually changed."""
    client = FakeQBOClient()
    adapter = QBOAdapter(client, ledger, realm_id="r-1")
    request = _request(vendor_invoice_number="INV-4471")

    first = adapter.create_unapproved_bill(request)
    adapter.reverse(first.remote_id, request.idempotency_key)
    assert client.bills == {}

    second = adapter.create_unapproved_bill(request)
    assert second.was_replay is False, "replayed a bill that had been reversed"
    assert second.remote_id != first.remote_id
    assert second.remote_id in client.bills


def test_a_write_after_a_reversal_creates_a_new_bill_in_the_no_invoice_number_case(ledger: IdempotencyLedger) -> None:
    client = FakeQBOClient()
    adapter = QBOAdapter(client, ledger, realm_id="r-1")
    request = _request(vendor_invoice_number=None)

    first = adapter.create_unapproved_bill(request)
    adapter.reverse(first.remote_id, request.idempotency_key)
    assert client.bills == {}

    second = adapter.create_unapproved_bill(request)
    assert second.was_replay is False, "replayed a bill that had been reversed"
    assert second.remote_id != first.remote_id
    assert second.remote_id in client.bills


def test_the_ledger_records_the_reversal(ledger: IdempotencyLedger) -> None:
    client = FakeQBOClient()
    adapter = QBOAdapter(client, ledger, realm_id="r-1")
    request = _request()
    result = adapter.create_unapproved_bill(request)
    adapter.reverse(result.remote_id, request.idempotency_key)
    record = ledger.lookup(request.idempotency_key)
    assert record is not None
    assert record.state == "reversed"
    assert record.remote_id is None


# ---------------------------------------------------------------------------
# Rejected write — F-44
# ---------------------------------------------------------------------------

def test_a_rejected_write_records_its_reason_rather_than_sitting_pending(ledger: IdempotencyLedger) -> None:
    """F-44. `fail()` was dead code with a passing test; a QBO 400 looked exactly
    like a crash, so the record sat `pending` forever with no reason."""
    from ledger_sense.writeback.qbo import QBORejected

    class Rejecting(FakeQBOClient):
        def post_bill(self, payload: dict[str, Any]) -> dict[str, Any]:
            raise QBORejected("400: VendorRef 9999 does not exist")

    request = _request()
    with pytest.raises(QBORejected):
        QBOAdapter(Rejecting(), ledger, realm_id="r-1").create_unapproved_bill(request)
    record = ledger.lookup(request.idempotency_key)
    assert record is not None
    assert record.state == "failed"
    assert "VendorRef" in (record.reason or "")


# ---------------------------------------------------------------------------
# Concurrent writers, same idempotency key — C-1
# ---------------------------------------------------------------------------

def test_concurrent_writers_with_the_same_key_create_only_one_bill(ledger: IdempotencyLedger) -> None:
    """C-1, the critical finding from the final whole-branch review.

    `IdempotencyLedger.begin()`'s read-then-insert used to be two separate
    implicit SQLite transactions: two concurrent callers with the SAME
    idempotency key could both find no existing record, both open/find a
    `pending` row, both reach `create_unapproved_bill`'s
    reconcile-then-POST branch, both find nothing posted yet, and both
    POST — creating two bills in the client's real books under one key.

    `post_bill` sleeps briefly to stand in for a real HTTP round trip to
    QBO (the near-instant in-memory fake hides the race entirely, because
    without I/O the GIL rarely yields mid-write) — this is what actually
    reproduced the duplicate deterministically before the fix, and mirrors
    the concurrency test pattern in
    `tests/writeback/test_audit.py::test_concurrent_appends_do_not_fork_the_chain`
    (Task 38).

    After the fix (`BEGIN IMMEDIATE` in `begin()`, plus the in-process
    `ConcurrentWriteInProgressError` guard in `create_unapproved_bill`),
    exactly one caller wins and posts; every other concurrent caller is
    refused outright rather than allowed to race the winner's own
    reconcile-then-POST.
    """
    import threading
    import time

    class SlowFakeQBOClient(FakeQBOClient):
        def post_bill(self, payload: dict[str, Any]) -> dict[str, Any]:
            time.sleep(0.01)
            return super().post_bill(payload)

    client = SlowFakeQBOClient()
    request = _request(vendor_invoice_number="INV-4471")
    threads_n = 8
    results: list[object] = []
    errors: list[BaseException] = []
    results_lock = threading.Lock()

    def _writer() -> None:
        try:
            result = QBOAdapter(client, ledger, realm_id="r-1").create_unapproved_bill(request)
            with results_lock:
                results.append(result)
        except BaseException as exc:  # noqa: BLE001 - surfaced via `errors`
            with results_lock:
                errors.append(exc)

    threads = [threading.Thread(target=_writer) for _ in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(client.bills) == 1, (
        f"expected exactly one bill, found {len(client.bills)}: {client.bills}")
    # Every thread either won (posted the one bill) or was refused outright —
    # nothing silently no-ops or raises some other, unexpected error.
    assert len(results) + len(errors) == threads_n
    assert all(isinstance(exc, ConcurrentWriteInProgressError) for exc in errors), (
        f"unexpected error types: {[type(exc).__name__ for exc in errors]}")
    assert len(results) >= 1, "no caller ever succeeded in writing the bill"


# ---------------------------------------------------------------------------
# Environment factory
# ---------------------------------------------------------------------------

def test_qbo_adapter_from_environment_builds_a_real_adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ledger: IdempotencyLedger) -> None:
    monkeypatch.setenv("LEDGER_SENSE_KEK", Fernet.generate_key().decode())
    monkeypatch.setenv("LEDGER_SENSE_TOKEN_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("LEDGER_SENSE_WRITEBACK_DIR", str(tmp_path))
    monkeypatch.setenv("QBO_REALM_ID", "realm-1")
    adapter = qbo_adapter_from_environment(ledger)
    assert isinstance(adapter, QBOAdapter)
    assert adapter.name == "qbo"


def test_qbo_adapter_from_environment_raises_when_realm_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ledger: IdempotencyLedger) -> None:
    monkeypatch.setenv("LEDGER_SENSE_KEK", Fernet.generate_key().decode())
    monkeypatch.setenv("LEDGER_SENSE_WRITEBACK_DIR", str(tmp_path))
    monkeypatch.delenv("QBO_REALM_ID", raising=False)
    with pytest.raises(RuntimeError, match="QBO_REALM_ID"):
        qbo_adapter_from_environment(ledger)


def test_extract_key_from_private_note_is_robust_to_surrounding_text() -> None:
    note = f"{AWAITING_REVIEW} key=LSabcdef1234567890abc Application 2"
    assert extract_key_from_private_note(note) == "LSabcdef1234567890abc"


def test_extract_key_from_private_note_returns_none_when_absent() -> None:
    assert extract_key_from_private_note("no key here") is None


def test_extract_key_from_private_note_does_not_match_a_word_containing_key(ledger: IdempotencyLedger) -> None:
    """M-1. `extract_key_from_private_note("turnkey=YES job")` used to return
    "YES" because the pattern matched `key=` as a substring of `turnkey=`.
    Anchored on whitespace/string boundaries now. Exercised both directly
    and through a memo that legitimately contains a `key=`-like substring —
    the REAL key, embedded before the memo by _build_payload, must still be
    the one recovered."""
    assert extract_key_from_private_note("turnkey=YES job") is None

    client = FakeQBOClient()
    request = _request(vendor_invoice_number="INV-4471")
    request = request.model_copy(update={"memo": "Master turnkey=YES delivery"})
    result = QBOAdapter(client, ledger, realm_id="r-1").create_unapproved_bill(request)
    bill = client.bills[result.remote_id]
    assert "turnkey=YES" in bill["PrivateNote"]
    assert extract_key_from_private_note(bill["PrivateNote"]) == request.idempotency_key
