"""Read-only posting preview (LS-04).

What `build_write_request` would write is the sum of the lines' `this_period`.
The payment chain the rules already define (A5/A6) gives a payable of
total completed and stored, less retainage, less previous payments. This
module computes both from the document's persisted claims and reports whether
they agree. It never writes anything and does not decide which figure the
books should carry: that is an accounting-policy decision (retainage, stored
materials, prior payments) a person makes, not a default chosen here.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.engine import Connection

from ledger_sense.domain.money import Money, money
from ledger_sense.writeback.assemble import (
    _LINE_ITEM_FIELD,
    MissingClaimError,
    _latest_claims,
    _money_of,
    _require,
)


@dataclass(frozen=True)
class PostingPreview:
    amount_to_write: Money
    total_completed_and_stored: Money
    retainage_total: Money
    previous_payments: Money
    expected_payable: Money
    difference: Money
    reconciles: bool


def build_posting_preview(conn: Connection, *, tenant_id: str,
                          document_id: str) -> PostingPreview:
    latest = _latest_claims(conn, tenant_id, document_id)
    completed = _money_of(_require(latest, "total_completed_and_stored"))
    retainage = _money_of(_require(latest, "retainage_total"))
    previous = _money_of(_require(latest, "previous_payments"))

    amount = money("0")
    line_count = 0
    for field, row in latest.items():
        match = _LINE_ITEM_FIELD.match(field)
        if match is not None and match.group("field") == "this_period":
            amount = money(amount + _money_of(row))
            line_count += 1
    if line_count == 0:
        raise MissingClaimError("lines")

    expected = money(completed - retainage - previous)
    difference = money(amount - expected)
    return PostingPreview(
        amount_to_write=amount, total_completed_and_stored=completed,
        retainage_total=retainage, previous_payments=previous,
        expected_payable=expected, difference=difference,
        reconciles=difference == money("0"))
