"""The writeback port. QBO and CSV/IIF export are PEER adapters.

Export is not a fallback bolted on after the API failed — it is a first-class
adapter satisfying the same protocol, which is what makes B12 survivable: the
product is useful on day one with no API approval at all.

`WritebackPort.reverse` takes the idempotency key as well as the remote id
(Ruling 1, phase pre-flight): reversal must transition the idempotency ledger
record, and doing that requires the key. Without it a reversed-then-rewritten
bill would incorrectly report itself as a replay of the deleted bill.

`WriteRequest.vendor_invoice_number` exists so a later adapter (Task 36) can
put the client's own invoice number in QBO `DocNumber` and the Ledger Sense
idempotency key in `PrivateNote` instead — never the other way around
(Ruling 2, phase pre-flight). `DocNumber` is the field bookkeepers reconcile
against paper and that QBO's own duplicate-detection keys on; overwriting it
with our key would silently disable the client's duplicate-invoice-number
control. `DocNumber` falls back to the idempotency key only when no vendor
invoice number was extracted. Task 34 does not consume this field itself —
it only has to exist so Task 36 does not need to retrofit it.
"""

import hashlib
from datetime import date, datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, field_serializer

from ledger_sense.domain.money import Money

# QBO DocNumber accepts up to 21 characters. The key must fit so it can be
# stamped on the remote object and queried back after a crash.
KEY_LENGTH = 21


class ClosedPeriodError(Exception):
    """The books are closed through this date. Refuse and explain."""


class ApprovedTransactionError(Exception):
    """Someone tried to post an approved transaction. v1 never does this."""


def build_idempotency_key(document_hash: str, contract_id: str,
                          application_no: int, *, tenant_id: str) -> str:
    """LS-02: the tenant is part of the key, so byte-identical documents in
    two tenants never share a key (and so never replay, or reverse, each
    other's write). Required, so a caller cannot silently omit it."""
    payload = f"{tenant_id}|{document_hash}|{contract_id}|{application_no}".encode()
    return "LS" + hashlib.sha256(payload).hexdigest()[:KEY_LENGTH - 2]


class _Base(BaseModel):
    # strict=True is LAYER 4 of the no-float rule (F-01), matching
    # ledger_sense.domain.models._Base: without it a JSON float is coerced
    # into Decimal and every other layer stays green while the money is wrong.
    model_config = ConfigDict(arbitrary_types_allowed=False, strict=True,
                              extra="forbid")


class LineItem(_Base):
    description: str
    amount: Money
    account_ref: str

    @field_serializer("amount")
    def _as_string(self, value: Money) -> str:
        return str(value)


class WriteRequest(_Base):
    idempotency_key: str
    doc_id: str
    vendor_ref: str
    vendor_invoice_number: str | None
    txn_date: date
    line_items: list[LineItem]
    memo: str


class WriteResult(_Base):
    remote_id: str
    idempotency_key: str
    adapter: str
    created_at: datetime
    was_replay: bool


class ReversalResult(_Base):
    remote_id: str
    reversed_at: datetime
    method: Literal["deleted", "voided"]


@runtime_checkable
class WritebackPort(Protocol):
    name: str

    def create_unapproved_bill(self, request: WriteRequest) -> WriteResult: ...

    def is_period_closed(self, txn_date: date) -> bool: ...

    def reverse(self, remote_id: str, idempotency_key: str) -> ReversalResult: ...
