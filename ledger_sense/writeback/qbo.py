"""QuickBooks Online adapter. Sandbox in weeks 5-12.

PLATFORM REALITY: QBO Bills have no draft state. v1 therefore creates the Bill
UNPAID, stamped AWAITING REVIEW, and NEVER creates a BillPayment. The human
approves by paying it inside QBO. That is a true sentence to tell a client, and
a better one than a claim about a draft state the platform does not have.

DOCNUMBER / PRIVATENOTE SPLIT (F-40, resolved 2026-08-29): `DocNumber` on a
QBO Bill is the VENDOR'S OWN invoice number — what a bookkeeper reconciles
against paper, and what QBO's own duplicate-document-number warning keys on.
Overwriting it with our idempotency key would silently disable a duplicate
control the client already has, in a product whose pitch is catching
duplicates. So:

  * When `request.vendor_invoice_number` is present: `DocNumber` keeps that
    invoice number unchanged, and the idempotency key is embedded in
    `PrivateNote` (see `_KEY_PATTERN` / `extract_key_from_private_note`).
  * When it is absent (nothing was extracted): `DocNumber` falls back to the
    idempotency key, as the original design did, and `PrivateNote` records in
    plain language that this fallback happened, so a human reading the books
    later understands why DocNumber looks like a key rather than an invoice
    number.

CRASH SAFETY: the key is always recoverable, but the query used to find it
after a crash differs by path:
  * Fallback (no invoice number): DocNumber IS the key, so a retry that finds
    a `pending` record queries QBO directly by DocNumber, as before.
  * Normal (invoice number present): the key is not in any queryable field,
    because QBO cannot query on PrivateNote. A retry instead queries
    `Bill WHERE TxnDate = ... AND VendorRef = ...` (there won't be many bills
    for one vendor on one date) and matches the key against each candidate's
    PrivateNote client-side.
"""

import re
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Protocol

from ledger_sense.writeback.idempotency import IdempotencyLedger
from ledger_sense.writeback.interface import (
    ClosedPeriodError,
    ReversalResult,
    WriteRequest,
    WriteResult,
)

if TYPE_CHECKING:
    from ledger_sense.writeback.interface import WritebackPort

AWAITING_REVIEW = (
    "AWAITING REVIEW — Ledger Sense. Approve by paying this bill in QuickBooks."
)

# The delimiter embedded in PrivateNote so the idempotency key can be found
# and parsed back out reliably. Anchored on both sides (start-of-string-or-
# whitespace before, whitespace-or-end after) so it matches the `key=`
# TOKEN, not `key=` as a substring of some other word in the free-text memo
# (e.g. "turnkey=YES" must not be mistaken for a key marker). The key itself
# is alphanumeric (see interface.build_idempotency_key).
_KEY_PATTERN = re.compile(r"(?:^|\s)key=([A-Za-z0-9]+)(?=\s|$)")


def extract_key_from_private_note(private_note: str) -> str | None:
    """Recover the idempotency key embedded in a Bill's PrivateNote, or None
    if this note does not carry one (e.g. a bill Ledger Sense did not write)."""
    match = _KEY_PATTERN.search(private_note)
    return match.group(1) if match else None


class QBORejected(Exception):
    """QBO rejected the write outright (e.g. a 400 for an unknown VendorRef).
    Distinct from a transport failure: the write did NOT land, and never
    will without a correction, so it fails cleanly rather than sitting
    `pending` forever (F-44)."""


class QBOClient(Protocol):
    def post_bill(self, payload: dict[str, object]) -> dict[str, object]: ...
    def find_bill_by_docnumber(
        self, doc_number: str) -> dict[str, object] | None: ...
    def find_bills_by_date_and_vendor(
        self, txn_date: date, vendor_ref: str) -> list[dict[str, object]]: ...
    def get_book_close_date(self) -> date | None: ...
    def delete_bill(self, remote_id: str) -> None: ...


def _build_payload(request: WriteRequest) -> dict[str, object]:
    """Build the QBO Bill payload, applying the DocNumber/PrivateNote split."""
    if request.vendor_invoice_number:
        doc_number = request.vendor_invoice_number
        private_note = (
            f"{AWAITING_REVIEW} key={request.idempotency_key} {request.memo}")
    else:
        doc_number = request.idempotency_key
        private_note = (
            f"{AWAITING_REVIEW} key={request.idempotency_key} "
            f"(no vendor invoice number extracted; DocNumber holds the "
            f"Ledger Sense idempotency key instead) {request.memo}")

    return {
        "DocNumber": doc_number,
        "PrivateNote": private_note,
        "TxnDate": request.txn_date.isoformat(),
        "VendorRef": {"value": request.vendor_ref},
        "Line": [
            {
                "DetailType": "AccountBasedExpenseLineDetail",
                "Amount": str(item.amount),
                "Description": item.description,
                "AccountBasedExpenseLineDetail": {
                    "AccountRef": {"value": item.account_ref}},
            }
            for item in request.line_items
        ],
    }


class QBOAdapter:
    name = "qbo"

    def __init__(self, client: QBOClient, ledger: IdempotencyLedger,
                 realm_id: str) -> None:
        self._client = client
        self._ledger = ledger
        self._realm_id = realm_id

    def is_period_closed(self, txn_date: date) -> bool:
        close_date = self._client.get_book_close_date()
        return close_date is not None and txn_date <= close_date

    def _find_existing_bill(self, request: WriteRequest) -> dict[str, object] | None:
        """Crash reconciliation. Which query runs depends on where the key
        was stamped (see module docstring)."""
        if request.vendor_invoice_number:
            candidates = self._client.find_bills_by_date_and_vendor(
                request.txn_date, request.vendor_ref)
            for candidate in candidates:
                note = str(candidate.get("PrivateNote", ""))
                if extract_key_from_private_note(note) == request.idempotency_key:
                    return candidate
            return None
        return self._client.find_bill_by_docnumber(request.idempotency_key)

    def create_unapproved_bill(self, request: WriteRequest) -> WriteResult:
        # The closed-period check runs BEFORE anything is recorded or posted.
        close_date = self._client.get_book_close_date()
        if close_date is not None and request.txn_date <= close_date:
            raise ClosedPeriodError(
                f"the books are closed through {close_date.isoformat()}; this "
                f"transaction is dated {request.txn_date.isoformat()}. Ask the "
                f"client to reopen the period or re-date the bill — Ledger Sense "
                f"will not write into a closed period.")

        record = self._ledger.begin(request.idempotency_key, adapter=self.name)

        if record.state == "committed":
            return WriteResult(
                remote_id=record.remote_id or "", adapter=self.name,
                idempotency_key=request.idempotency_key,
                created_at=record.committed_at or record.attempted_at,
                was_replay=True)

        # `pending` means a prior attempt may or may not have landed. Go and look.
        existing = self._find_existing_bill(request)
        if existing is not None:
            existing_id = str(existing["Id"])
            self._ledger.commit(request.idempotency_key, remote_id=existing_id,
                                adapter=self.name)
            return WriteResult(remote_id=existing_id, adapter=self.name,
                               idempotency_key=request.idempotency_key,
                               created_at=datetime.now(UTC), was_replay=True)

        payload = _build_payload(request)
        try:
            response = self._client.post_bill(payload)
        except QBORejected as exc:
            # F-44: `fail()` was dead code with a passing test. A QBO 400 was
            # indistinguishable from a crash, so a permanently-rejected write sat
            # `pending` forever with no reason recorded.
            self._ledger.fail(request.idempotency_key, reason=str(exc),
                              adapter=self.name)
            raise
        response_id = str(response["Id"])
        self._ledger.commit(request.idempotency_key, remote_id=response_id,
                            adapter=self.name)
        return WriteResult(remote_id=response_id, adapter=self.name,
                           idempotency_key=request.idempotency_key,
                           created_at=datetime.now(UTC), was_replay=False)

    def reverse(self, remote_id: str, idempotency_key: str) -> ReversalResult:
        """REVISED (F-39). Takes the key, not just the remote id, and transitions
        the ledger record — otherwise the row stays `committed` pointing at a
        deleted bill and the next write with that key returns a confident
        `was_replay=True` with a dead id."""
        self._client.delete_bill(remote_id)
        self._ledger.mark_reversed(idempotency_key, adapter=self.name)
        return ReversalResult(remote_id=remote_id,
                              reversed_at=datetime.now(UTC), method="deleted")


if TYPE_CHECKING:
    # Static conformance check (I-2): the brief requires QBOAdapter to satisfy
    # WritebackPort. This assignment costs nothing at runtime (it never
    # executes; TYPE_CHECKING is False) but mypy --strict will fail this
    # module the moment QBOAdapter's shape drifts from the protocol. The
    # dynamic counterpart — isinstance(adapter, WritebackPort) — lives in
    # tests/writeback/test_qbo.py.
    def _qbo_adapter_satisfies_writebackport(adapter: QBOAdapter) -> None:
        _conforms: WritebackPort = adapter
