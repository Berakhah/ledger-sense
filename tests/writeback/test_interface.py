from datetime import UTC, date, datetime

import pytest

from ledger_sense.writeback.interface import (
    LineItem,
    ReversalResult,
    WritebackPort,
    WriteRequest,
    WriteResult,
    build_idempotency_key,
)


def test_idempotency_key_is_deterministic():
    a = build_idempotency_key(document_hash="abc123", contract_id="C-1", application_no=2)
    b = build_idempotency_key(document_hash="abc123", contract_id="C-1", application_no=2)
    assert a == b


def test_idempotency_key_changes_with_the_application_number():
    a = build_idempotency_key(document_hash="abc123", contract_id="C-1", application_no=2)
    b = build_idempotency_key(document_hash="abc123", contract_id="C-1", application_no=3)
    assert a != b


def test_idempotency_key_fits_a_qbo_docnumber_field():
    """QBO DocNumber is capped at 21 characters. A key that will not fit cannot
    be stamped, and an unstamped key cannot be queried back after a crash."""
    key = build_idempotency_key(document_hash="a" * 64, contract_id="C-1",
                                application_no=2)
    assert len(key) <= 21


def test_line_item_rejects_a_float_amount():
    """VERIFIED FAILING as originally written (F-01). Pydantic 2.13 in lax mode
    accepts a float into a Decimal field and coerces it:

        LineItem(description="x", amount=1000.0, account_ref="54")
        -> LineItem(amount=Decimal('1000.0'))    # no exception

    `Money` now carries `Strict()` and `LineItem` sets `strict=True`, which is
    Layer 4 of the no-float rule. Assert the specific exception, not `Exception`
    — `pytest.raises(Exception)` would also have passed on a TypeError from a
    missing argument, which is how a test can be green and prove nothing."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        LineItem(description="x", amount=1000.0, account_ref="54")


def test_line_item_rejects_a_float_that_is_exact_in_binary_too():
    """0.5 and 1024.0 are exactly representable, so a "does it round-trip" check
    would pass them. The rule is no float, not no lossy float."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        LineItem(description="x", amount=1024.0, account_ref="54")


def test_a_conforming_adapter_satisfies_the_protocol():
    class FakeAdapter:
        name = "fake"

        def create_unapproved_bill(self, request: WriteRequest) -> WriteResult:
            return WriteResult(remote_id="1", idempotency_key=request.idempotency_key,
                               adapter=self.name,
                               created_at=datetime.now(UTC), was_replay=False)

        def is_period_closed(self, txn_date: date) -> bool:
            return False

        def reverse(self, remote_id: str, idempotency_key: str) -> ReversalResult:
            return ReversalResult(remote_id=remote_id,
                                  reversed_at=datetime.now(UTC),
                                  method="deleted")

    assert isinstance(FakeAdapter(), WritebackPort)


def test_a_missing_method_fails_the_protocol():
    class Incomplete:
        name = "incomplete"

        def create_unapproved_bill(self, request):
            raise NotImplementedError

    assert not isinstance(Incomplete(), WritebackPort)


def test_write_request_carries_an_optional_vendor_invoice_number():
    """DocNumber on a QBO Bill is the vendor's own invoice number — the field
    bookkeepers reconcile against paper. Ledger Sense's idempotency key must
    NOT overwrite it when a vendor invoice number was actually extracted; the
    key goes in PrivateNote instead, with DocNumber as a fallback only when no
    vendor invoice number exists (Task 36 consumes this; Task 34 only needs the
    field to exist so nothing has to be retrofitted)."""
    with_number = WriteRequest(
        idempotency_key="LSabc123", doc_id="doc-1", vendor_ref="V-1",
        vendor_invoice_number="INV-4471", txn_date=date(2026, 1, 15),
        line_items=[], memo="progress bill",
    )
    assert with_number.vendor_invoice_number == "INV-4471"

    without_number = WriteRequest(
        idempotency_key="LSabc123", doc_id="doc-1", vendor_ref="V-1",
        vendor_invoice_number=None, txn_date=date(2026, 1, 15),
        line_items=[], memo="progress bill",
    )
    assert without_number.vendor_invoice_number is None
