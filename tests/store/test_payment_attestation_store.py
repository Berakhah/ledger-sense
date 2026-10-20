from datetime import UTC, datetime

import pytest

from ledger_sense.domain.vendor import Attestation, PaymentDetail, VendorLedger, payment_hmac
from ledger_sense.store import session as store_session
from ledger_sense.store.vendors import load_vendor_ledger, save_vendor_ledger
from tests.store.conftest import SeededDocuments


def test_the_attestation_round_trips(
        seeded_documents: SeededDocuments, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SENSE_PAYMENT_HMAC_KEY", "test-key")
    ledger = VendorLedger()
    vendor_id = ledger.create("Acme Steel Company")
    ledger.record_payment_detail(
        vendor_id,
        PaymentDetail(account_last4="1234", routing_last4="5678",
                      account_hmac=payment_hmac("00001234", "00005678"),
                      observed_at=datetime(2026, 9, 15, tzinfo=UTC), source_document="doc-1"),
        confirmed_by="reviewer-1",
        attestation=Attestation(channel="video_call", contact="J. Vendor", note="n"))
    with store_session.app_engine().begin() as conn:
        save_vendor_ledger(conn, seeded_documents.tenant_id, ledger)
    with store_session.app_engine().connect() as conn:
        reloaded = load_vendor_ledger(conn, seeded_documents.tenant_id)
    vendor = reloaded.get(vendor_id)
    assert vendor is not None
    stored = vendor.payment_history[0]
    assert (stored.confirmation_channel, stored.confirmation_contact,
            stored.confirmation_note) == ("video_call", "J. Vendor", "n")


def test_a_reversal_round_trips_and_keeps_the_evidence(
        seeded_documents: SeededDocuments, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every save deletes and re-inserts the tenant's rows: a field the store does
    not carry is silently lost. A reversal must survive, with the attestation."""
    monkeypatch.setenv("LEDGER_SENSE_PAYMENT_HMAC_KEY", "test-key")
    ledger = VendorLedger()
    vendor_id = ledger.create("Acme Steel Company")
    ledger.record_payment_detail(
        vendor_id,
        PaymentDetail(account_last4="1234", routing_last4="5678",
                      account_hmac=payment_hmac("00001234", "00005678"),
                      observed_at=datetime(2026, 9, 15, tzinfo=UTC), source_document="doc-1"),
        confirmed_by="reviewer-1",
        attestation=Attestation(channel="phone", contact="J. Vendor", note="n"))
    found = ledger.find_payment_detail("doc-1")
    assert found is not None
    ledger.undo_payment_detail(found[0], found[1].id, reversed_by="reviewer-2")
    with store_session.app_engine().begin() as conn:
        save_vendor_ledger(conn, seeded_documents.tenant_id, ledger)
    with store_session.app_engine().connect() as conn:
        reloaded = load_vendor_ledger(conn, seeded_documents.tenant_id)
    vendor = reloaded.get(vendor_id)
    assert vendor is not None
    stored = vendor.payment_history[0]
    assert stored.reversed_by == "reviewer-2"
    assert stored.reversed_at is not None
    assert (stored.confirmed_by, stored.confirmation_contact) == ("reviewer-1", "J. Vendor")
    assert not stored.is_active
