from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ledger_sense.domain.vendor import Attestation, PaymentDetail, VendorLedger, payment_hmac


@pytest.fixture(autouse=True)
def _hmac_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SENSE_PAYMENT_HMAC_KEY", "test-key-not-a-secret")


def _detail(account: str = "4321", doc: str = "doc://a") -> PaymentDetail:
    return PaymentDetail(account_last4=account[-4:], routing_last4="8765",
                         account_hmac=payment_hmac(account, "8765"),
                         observed_at=datetime.now(UTC), source_document=doc)


def test_an_attestation_must_name_who_was_spoken_to() -> None:
    with pytest.raises(ValidationError):
        Attestation(channel="phone", contact="   ")


def test_an_unknown_channel_is_refused() -> None:
    with pytest.raises(ValidationError):
        Attestation(channel="carrier-pigeon", contact="J. Vendor")  # type: ignore[arg-type]


def test_the_attestation_is_stored_on_the_recorded_detail() -> None:
    led = VendorLedger()
    vendor_id = led.create("Acme Steel Company")
    led.record_payment_detail(
        vendor_id, _detail(), confirmed_by="reviewer-1",
        attestation=Attestation(channel="phone", contact="J. Vendor", note="known-good number"))
    vendor = led.get(vendor_id)
    assert vendor is not None
    stored = vendor.payment_history[-1]
    assert (stored.confirmation_channel, stored.confirmation_contact,
            stored.confirmation_note) == ("phone", "J. Vendor", "known-good number")


def test_find_and_undo_work_by_source_document_and_identity() -> None:
    led = VendorLedger()
    vendor_id = led.create("Acme Steel Company")
    led.record_payment_detail(vendor_id, _detail("4321", "doc://one"), confirmed_by="r")
    led.record_payment_detail(vendor_id, _detail("9999", "doc://two"), confirmed_by="r")
    found = led.find_payment_detail("doc://two")
    assert found is not None
    assert found[0] == vendor_id
    led.undo_payment_detail(found[0], found[1].id)
    vendor = led.get(vendor_id)
    assert vendor is not None
    assert [d.source_document for d in vendor.payment_history] == ["doc://one"]
    assert led.find_payment_detail("doc://two") is None


def test_an_unconfirmed_entry_is_never_found_or_used_as_a_baseline() -> None:
    led = VendorLedger()
    vendor_id = led.create("Acme Steel Company")
    led.record_payment_detail(vendor_id, _detail("4321", "doc://one"), confirmed_by="")
    assert led.find_payment_detail("doc://one") is None
