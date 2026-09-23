from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from ledger_sense.domain.vendor import PaymentDetail, VendorLedger, payment_hmac
from ledger_sense.store import session as store_session
from ledger_sense.store.retention import vendors
from ledger_sense.store.vendors import load_vendor_ledger, save_vendor_ledger
from tests.store.conftest import SeededDocuments

OBSERVED_AT = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)


def test_an_empty_tenant_round_trips_to_an_empty_ledger(
    seeded_documents: SeededDocuments,
) -> None:
    with store_session.app_engine().connect() as conn:
        ledger = load_vendor_ledger(conn, seeded_documents.tenant_id)
    assert ledger.get("nope") is None


def test_a_vendor_with_an_alias_and_a_payment_detail_round_trips(
    seeded_documents: SeededDocuments, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEDGER_SENSE_PAYMENT_HMAC_KEY", "test-key")
    ledger = VendorLedger()
    vendor_id = ledger.create("Acme Steel Company")
    ledger.confirm_alias(vendor_id, "Acme Steel Co.", source_document="doc-1",
                         confirmed_by="reviewer-1")
    ledger.record_payment_detail(
        vendor_id,
        PaymentDetail(account_last4="1234", routing_last4="5678",
                      account_hmac=payment_hmac("00001234", "00005678"),
                      observed_at=OBSERVED_AT, source_document="doc-1"),
        confirmed_by="")

    with store_session.app_engine().begin() as conn:
        save_vendor_ledger(conn, seeded_documents.tenant_id, ledger)
    with store_session.app_engine().connect() as conn:
        reloaded = load_vendor_ledger(conn, seeded_documents.tenant_id)

    vendor = reloaded.get(vendor_id)
    assert vendor is not None
    assert vendor.name == "Acme Steel Company"
    assert [a.name for a in vendor.aliases] == ["Acme Steel Co."]
    assert vendor.aliases[0].confirmed_by == "reviewer-1"
    assert len(vendor.payment_history) == 1
    assert vendor.payment_history[0].account_last4 == "1234"


def test_another_tenants_vendors_are_invisible(
    seeded_documents: SeededDocuments,
) -> None:
    ledger = VendorLedger()
    ledger.create("Acme Steel Company")
    with store_session.app_engine().begin() as conn:
        save_vendor_ledger(conn, seeded_documents.tenant_id, ledger)

    with store_session.app_engine().connect() as conn:
        other_tenants_ledger = load_vendor_ledger(conn, "someone-else")
        # sanity: the row does exist, just not for "someone-else"
        assert conn.execute(sa.select(vendors)).all() != []
    assert other_tenants_ledger.get("nope") is None
    assert other_tenants_ledger.all_vendors() == []


def test_resaving_replaces_rather_than_accumulates(
    seeded_documents: SeededDocuments,
) -> None:
    ledger = VendorLedger()
    vendor_id = ledger.create("Acme Steel Company")
    with store_session.app_engine().begin() as conn:
        save_vendor_ledger(conn, seeded_documents.tenant_id, ledger)
        save_vendor_ledger(conn, seeded_documents.tenant_id, ledger)
    with store_session.app_engine().connect() as conn:
        reloaded = load_vendor_ledger(conn, seeded_documents.tenant_id)
    assert len(reloaded.all_vendors()) == 1
    assert reloaded.get(vendor_id) is not None
