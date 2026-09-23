"""Read-only vendor ledger API — the vendor ledger screen's data source.

payment_history is always [] until a payment-detail ingest path exists (none
does yet — see docs/superpowers/plans/2026-09-23-vendor-persistence.md's
Architecture note). The response model is still fully typed rather than a
bare [] placeholder, so the frontend and any future ingest path share one
contract; account_hmac is never a field on PaymentDetailOut — it must never
leave the server.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ledger_sense.api.deps import require_session
from ledger_sense.auth.session import Session
from ledger_sense.store.session import app_engine
from ledger_sense.store.vendors import load_vendor_ledger

router = APIRouter(prefix="/api/vendors")

CurrentSession = Annotated[Session, Depends(require_session)]


class VendorAliasOut(BaseModel):
    id: str
    name: str
    first_seen: datetime
    source_document: str
    confirmed_by: str


class PaymentDetailOut(BaseModel):
    account_last4: str
    routing_last4: str
    observed_at: datetime
    source_document: str
    confirmed_by: str | None
    confirmed_at: datetime | None


class VendorLedgerOut(BaseModel):
    id: str
    name: str
    aliases: list[VendorAliasOut]
    payment_history: list[PaymentDetailOut]


@router.get("/{vendor_id}/ledger")
def vendor_ledger(vendor_id: str, session: CurrentSession) -> VendorLedgerOut:
    with app_engine().connect() as conn:
        ledger = load_vendor_ledger(conn, session.tenant_id)
    vendor = ledger.get(vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail="not found")
    return VendorLedgerOut(
        id=vendor.id, name=vendor.name,
        aliases=[VendorAliasOut(id=a.id, name=a.name, first_seen=a.first_seen,
                                source_document=a.source_document,
                                confirmed_by=a.confirmed_by) for a in vendor.aliases],
        payment_history=[PaymentDetailOut(
            account_last4=p.account_last4, routing_last4=p.routing_last4,
            observed_at=p.observed_at, source_document=p.source_document,
            confirmed_by=p.confirmed_by, confirmed_at=p.confirmed_at)
            for p in vendor.payment_history],
    )
