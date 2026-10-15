"""The purge removes the bank-detail history a document contributed.

`payment_details` carries the account and routing last-4 and an HMAC of the
full numbers, each tagged with the document it came from. Deleting the document
while that history survives is the same half-deletion the claims test guards
against — so the history goes with its source, and only with its source.
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from ledger_sense.store import session as store_session
from ledger_sense.store.retention import (
    documents,
    payment_details,
    purge_expired,
    vendor_aliases,
    vendors,
)
from tests.store.conftest import EXPIRED_DOCUMENT_ID, SeededDocuments

KEEPER = "doc-keeper"


def _vendor(tenant_id: str, name: str) -> str:
    vendor_id = uuid.uuid4().hex
    with store_session.app_engine().begin() as conn:
        conn.execute(vendors.insert().values(id=vendor_id, tenant_id=tenant_id, name=name))
    return vendor_id


def _detail(tenant_id: str, vendor_id: str, source_document: str) -> str:
    detail_id = uuid.uuid4().hex
    with store_session.app_engine().begin() as conn:
        conn.execute(payment_details.insert().values(
            id=detail_id, tenant_id=tenant_id, vendor_id=vendor_id,
            account_last4="6789", routing_last4="0021", account_hmac="ab" * 32,
            observed_at=datetime.now(UTC), source_document=source_document))
    return detail_id


def _alias(tenant_id: str, vendor_id: str, source_document: str) -> str:
    alias_id = uuid.uuid4().hex
    with store_session.app_engine().begin() as conn:
        conn.execute(vendor_aliases.insert().values(
            id=alias_id, tenant_id=tenant_id, vendor_id=vendor_id, name="ACME Elec.",
            first_seen=datetime.now(UTC), source_document=source_document,
            confirmed_by="reviewer"))
    return alias_id


def _ids(table: sa.Table) -> set[str]:
    with store_session.app_engine().connect() as conn:
        return {str(r.id) for r in conn.execute(sa.select(table.c.id))}


def test_payment_details_from_a_purged_document_are_deleted(
    seeded_documents: SeededDocuments,
) -> None:
    tenant = seeded_documents.tenant_id
    vendor = _vendor(tenant, "Acme Electric")
    gone = _detail(tenant, vendor, EXPIRED_DOCUMENT_ID)

    report = purge_expired(now=datetime.now(UTC))

    assert gone not in _ids(payment_details)
    assert report.payment_details_purged == 1


def test_payment_details_from_a_surviving_document_are_kept(
    seeded_documents: SeededDocuments,
) -> None:
    tenant = seeded_documents.tenant_id
    seeded_documents.add_document(KEEPER, age_days=1)
    vendor = _vendor(tenant, "Acme Electric")
    kept = _detail(tenant, vendor, KEEPER)
    _detail(tenant, vendor, EXPIRED_DOCUMENT_ID)

    purge_expired(now=datetime.now(UTC))

    assert _ids(payment_details) == {kept}
    assert vendor in _ids(vendors)


def test_aliases_first_seen_in_a_purged_document_are_deleted(
    seeded_documents: SeededDocuments,
) -> None:
    tenant = seeded_documents.tenant_id
    seeded_documents.add_document(KEEPER, age_days=1)
    vendor = _vendor(tenant, "Acme Electric")
    gone = _alias(tenant, vendor, EXPIRED_DOCUMENT_ID)
    kept = _alias(tenant, vendor, KEEPER)

    purge_expired(now=datetime.now(UTC))

    assert _ids(vendor_aliases) == {kept}
    assert gone not in _ids(vendor_aliases)


def test_a_vendor_left_with_no_history_is_deleted(
    seeded_documents: SeededDocuments,
) -> None:
    tenant = seeded_documents.tenant_id
    vendor = _vendor(tenant, "Acme Electric")
    _detail(tenant, vendor, EXPIRED_DOCUMENT_ID)
    _alias(tenant, vendor, EXPIRED_DOCUMENT_ID)

    report = purge_expired(now=datetime.now(UTC))

    assert vendor not in _ids(vendors)
    assert report.vendors_purged == 1


def test_a_vendor_a_surviving_document_still_references_is_kept(
    seeded_documents: SeededDocuments,
) -> None:
    tenant = seeded_documents.tenant_id
    seeded_documents.add_document(KEEPER, age_days=1)
    vendor = _vendor(tenant, "Acme Electric")
    with store_session.app_engine().begin() as conn:
        conn.execute(documents.update().where(documents.c.id == KEEPER).values(vendor_id=vendor))
    _detail(tenant, vendor, EXPIRED_DOCUMENT_ID)

    purge_expired(now=datetime.now(UTC))

    assert vendor in _ids(vendors)


def test_a_vendor_whose_expired_document_pointed_at_it_can_be_deleted(
    seeded_documents: SeededDocuments,
) -> None:
    """documents.vendor_id is a foreign key: the document must go first."""
    tenant = seeded_documents.tenant_id
    vendor = _vendor(tenant, "Acme Electric")
    with store_session.app_engine().begin() as conn:
        conn.execute(documents.update().where(
            documents.c.id == EXPIRED_DOCUMENT_ID).values(vendor_id=vendor))

    purge_expired(now=datetime.now(UTC))

    assert vendor not in _ids(vendors)


def test_another_tenants_vendor_ledger_is_untouched(
    seeded_documents: SeededDocuments,
) -> None:
    tenant = seeded_documents.tenant_id
    other = uuid.uuid4().hex
    with store_session.app_engine().begin() as conn:
        from ledger_sense.auth.models import tenants
        conn.execute(tenants.insert().values(
            id=other, name="other", created_at=datetime.now(UTC), retention_days=365))
    foreign_vendor = _vendor(other, "Other Co")
    foreign_detail = _detail(other, foreign_vendor, EXPIRED_DOCUMENT_ID)
    _detail(tenant, _vendor(tenant, "Acme"), EXPIRED_DOCUMENT_ID)

    purge_expired(now=datetime.now(UTC))

    assert foreign_detail in _ids(payment_details)
    assert foreign_vendor in _ids(vendors)
