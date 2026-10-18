"""The vendors/vendor_aliases/payment_details tables' one writer and one
reader (§4, same discipline as store/documents.py).

`load_vendor_ledger` hydrates every vendor a tenant has ever recorded into an
in-memory VendorLedger; `save_vendor_ledger` replaces that tenant's rows
across all three tables wholesale. VendorLedger keeps no dirty-tracking of
its own (domain/vendor.py's docstring) and a tenant's vendor count is small
at this project's scale, so a snapshot replace is simpler — and harder to
get subtly wrong — than incremental diffing against three tables at once.

tests/boundary/test_structure.py enforces that vendors.insert()/
vendor_aliases.insert()/payment_details.insert() appear nowhere else under
ledger_sense/ — this is the only door.
"""

from __future__ import annotations

from collections import defaultdict

import sqlalchemy as sa
from sqlalchemy import Connection

from ledger_sense.domain.vendor import CanonicalVendor, PaymentDetail, VendorAlias, VendorLedger
from ledger_sense.store.retention import payment_details, vendor_aliases, vendors

__all__ = ["load_vendor_ledger", "load_vendor_ledger_for_update", "save_vendor_ledger"]


def load_vendor_ledger_for_update(conn: Connection, tenant_id: str) -> VendorLedger:
    """Same as `load_vendor_ledger`, but first serialises concurrent writers
    for this tenant so a subsequent `save_vendor_ledger` cannot lose one.

    `save_vendor_ledger` replaces a tenant's whole vendor/alias/payment-detail
    row set from an in-memory snapshot with no row-level tracking of its own
    (module docstring). Two callers who each load -> mutate -> save for the
    SAME tenant can both load before either saves; the second save then
    silently overwrites the first's write. A Postgres advisory transaction
    lock, not `SELECT ... FOR UPDATE`, is the guard: `FOR UPDATE` has no rows
    to lock when a tenant's vendor tables are still empty, which is exactly
    the state a tenant's very first confirm-vendor call runs against.
    `pg_advisory_xact_lock` needs no row and releases automatically at this
    transaction's commit or rollback, so callers get the same lifetime the
    tables' own locks would give them, without depending on rows existing.

    A no-op on any other dialect (SQLite in tests): SQLite's own
    concurrency limits are already an accepted, documented gap for the test
    suite (store/session.py's `app_engine` "ponytail" comment), and it has no
    matching advisory-lock primitive.
    """
    if conn.dialect.name == "postgresql":
        conn.execute(sa.text("SELECT pg_advisory_xact_lock(hashtext(:tenant_id))"),
                     {"tenant_id": tenant_id})
    return load_vendor_ledger(conn, tenant_id)


def load_vendor_ledger(conn: Connection, tenant_id: str) -> VendorLedger:
    vendor_rows = conn.execute(
        sa.select(vendors).where(vendors.c.tenant_id == tenant_id)).all()

    aliases_by_vendor: dict[str, list[VendorAlias]] = defaultdict(list)
    alias_rows = conn.execute(
        sa.select(vendor_aliases)
        .where(vendor_aliases.c.tenant_id == tenant_id)
        .order_by(vendor_aliases.c.first_seen)
    ).all()
    for row in alias_rows:
        aliases_by_vendor[str(row.vendor_id)].append(VendorAlias(
            id=str(row.id), name=row.name, first_seen=row.first_seen,
            source_document=row.source_document, confirmed_by=row.confirmed_by,
            merged_from=row.merged_from))

    details_by_vendor: dict[str, list[PaymentDetail]] = defaultdict(list)
    detail_rows = conn.execute(
        sa.select(payment_details)
        .where(payment_details.c.tenant_id == tenant_id)
        .order_by(payment_details.c.observed_at)
    ).all()
    for row in detail_rows:
        details_by_vendor[str(row.vendor_id)].append(PaymentDetail(
            id=str(row.id), account_last4=row.account_last4, routing_last4=row.routing_last4,
            account_hmac=row.account_hmac, observed_at=row.observed_at,
            source_document=row.source_document, confirmed_by=row.confirmed_by,
            confirmed_at=row.confirmed_at,
            confirmation_channel=row.confirmation_channel,
            confirmation_contact=row.confirmation_contact,
            confirmation_note=row.confirmation_note))

    canon = [
        CanonicalVendor(
            id=str(row.id), name=row.name,
            aliases=aliases_by_vendor[str(row.id)],
            payment_history=details_by_vendor[str(row.id)])
        for row in vendor_rows
    ]
    return VendorLedger.from_vendors(canon)


def save_vendor_ledger(conn: Connection, tenant_id: str, ledger: VendorLedger) -> None:
    conn.execute(payment_details.delete().where(payment_details.c.tenant_id == tenant_id))
    conn.execute(vendor_aliases.delete().where(vendor_aliases.c.tenant_id == tenant_id))
    conn.execute(vendors.delete().where(vendors.c.tenant_id == tenant_id))

    canon = ledger.all_vendors()
    if not canon:
        return

    conn.execute(vendors.insert(), [
        {"id": v.id, "tenant_id": tenant_id, "name": v.name} for v in canon])

    alias_rows = [
        {"id": a.id, "tenant_id": tenant_id, "vendor_id": v.id, "name": a.name,
         "first_seen": a.first_seen, "source_document": a.source_document,
         "confirmed_by": a.confirmed_by, "merged_from": a.merged_from}
        for v in canon for a in v.aliases]
    if alias_rows:
        conn.execute(vendor_aliases.insert(), alias_rows)

    detail_rows = [
        {"id": d.id, "tenant_id": tenant_id, "vendor_id": v.id,
         "account_last4": d.account_last4, "routing_last4": d.routing_last4,
         "account_hmac": d.account_hmac, "observed_at": d.observed_at,
         "source_document": d.source_document, "confirmed_by": d.confirmed_by,
         "confirmed_at": d.confirmed_at,
         "confirmation_channel": d.confirmation_channel,
         "confirmation_contact": d.confirmation_contact,
         "confirmation_note": d.confirmation_note}
        for v in canon for d in v.payment_history]
    if detail_rows:
        conn.execute(payment_details.insert(), detail_rows)
