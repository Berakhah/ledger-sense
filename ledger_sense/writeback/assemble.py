"""Claims -> WriteRequest assembly.

Every value a bill needs comes from the `claims` table Phase 2/4 already
populate — nothing here re-derives a value from the document itself, and
nothing guesses one that was never extracted (B11). Where a field has more
than one claim (extraction, then a human accept/edit), the LATEST by
`observed_at` wins — the same rule api/review.py::_value_type_for uses, so a
reviewer's correction is picked up automatically.

The idempotency key (derive_idempotency_key) is deliberately independent of
vendor identity: it comes from `build_idempotency_key(document_hash,
contract_id, application_no)` only. Nothing extracts a vendor today, and a
caller-supplied vendor_ref must never change which remote object a retry
resolves to.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import date
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from ledger_sense.domain.money import Money, money
from ledger_sense.store.retention import claims, documents
from ledger_sense.writeback.interface import LineItem, WriteRequest, build_idempotency_key


class MissingClaimError(Exception):
    """No claim exists for a field this write needs. Never guessed (B11) —
    the money path refuses rather than defaulting."""

    def __init__(self, field: str) -> None:
        super().__init__(f"no claim for {field!r}")
        self.field = field


def _latest_claims(conn: Connection, tenant_id: str,
                   document_id: str) -> dict[str, sa.Row[Any]]:
    """One row per field: the claim with the latest observed_at wins."""
    rows = conn.execute(
        sa.select(claims)
        .where(claims.c.tenant_id == tenant_id, claims.c.document_id == document_id)
        .order_by(claims.c.observed_at.asc())
    ).all()
    latest: dict[str, sa.Row[Any]] = {}
    for row in rows:
        latest[row.field] = row  # a later observed_at overwrites an earlier one
    return latest


def _require(latest: dict[str, sa.Row[Any]], field: str) -> sa.Row[Any]:
    row = latest.get(field)
    if row is None:
        raise MissingClaimError(field)
    return row


def derive_idempotency_key(conn: Connection, *, document_id: str) -> str:
    """The document's idempotency key, independent of vendor identity.

    Reads only documents.content_sha256 plus the latest contract_id and
    application_no claims. A document with no content hash (a
    pre-migration-0005 row) or no contract_id/application_no claim raises
    MissingClaimError — there is no safe fallback for a key that must stay
    stable across retries.
    """
    doc = conn.execute(
        sa.select(documents.c.tenant_id, documents.c.content_sha256)
        .where(documents.c.id == document_id)
    ).one_or_none()
    if doc is None or doc.content_sha256 is None:
        raise MissingClaimError("content_sha256")
    latest = _latest_claims(conn, str(doc.tenant_id), document_id)
    contract_id = str(_require(latest, "contract_id").value)
    application_no = int(_require(latest, "application_no").value)
    return build_idempotency_key(str(doc.content_sha256), contract_id, application_no)


_ACCOUNT_REF_ENV = "LEDGER_SENSE_DEFAULT_ACCOUNT_REF"

_TOP_LEVEL_MONEY_FIELDS = ("total_completed_and_stored", "retainage_total",
                          "previous_payments", "change_orders_net")

_LINE_ITEM_FIELD = re.compile(r"\Aline\[(?P<item_no>[^\]]+)\]\.(?P<field>.+)\Z")


def _money_of(row: sa.Row[Any]) -> Money:
    return money(str(row.value))


def build_write_request(
    conn: Connection, *, tenant_id: str, document_id: str,
    vendor_ref: str, vendor_invoice_number: str | None,
) -> WriteRequest:
    """Reconstruct a WriteRequest from this document's persisted claims.

    account_ref is one tenant-wide default (LEDGER_SENSE_DEFAULT_ACCOUNT_REF),
    applied to every line — real per-vendor/per-line account mapping is
    deferred (Phase 6 spec, §2). Every required field's absence raises
    MissingClaimError rather than defaulting (B11).
    """
    idempotency_key = derive_idempotency_key(conn, document_id=document_id)
    account_ref = os.environ.get(_ACCOUNT_REF_ENV)
    if not account_ref:
        raise RuntimeError(
            f"{_ACCOUNT_REF_ENV} is unset: refusing to post a bill with no "
            f"default account.")

    latest = _latest_claims(conn, tenant_id, document_id)
    application_no = int(_require(latest, "application_no").value)
    contract_id = str(_require(latest, "contract_id").value)
    period_to = date.fromisoformat(str(_require(latest, "period_to").value))

    for field in _TOP_LEVEL_MONEY_FIELDS:
        _require(latest, field)  # presence only — extraction must have captured a total

    lines_by_item: dict[str, dict[str, sa.Row[Any]]] = defaultdict(dict)
    for field, row in latest.items():
        match = _LINE_ITEM_FIELD.match(field)
        if match is not None:
            lines_by_item[match.group("item_no")][match.group("field")] = row
    if not lines_by_item:
        raise MissingClaimError("lines")

    line_items: list[LineItem] = []
    for item_no in sorted(lines_by_item):
        fields = lines_by_item[item_no]
        if "description" not in fields:
            raise MissingClaimError(f"line[{item_no}].description")
        if "this_period" not in fields:
            raise MissingClaimError(f"line[{item_no}].this_period")
        line_items.append(LineItem(
            description=str(fields["description"].value),
            amount=_money_of(fields["this_period"]),
            account_ref=account_ref,
        ))

    memo = f"Pay application {application_no}, contract {contract_id}"
    return WriteRequest(
        idempotency_key=idempotency_key, doc_id=document_id, vendor_ref=vendor_ref,
        vendor_invoice_number=vendor_invoice_number, txn_date=period_to,
        line_items=line_items, memo=memo)
