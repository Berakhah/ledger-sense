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
    led.undo_payment_detail(found[0], found[1].id, reversed_by="reviewer-2")
    vendor = led.get(vendor_id)
    assert vendor is not None
    # Undo keeps the record (evidence); it only stops being found or trusted.
    assert [d.source_document for d in vendor.payment_history] == ["doc://one", "doc://two"]
    assert led.find_payment_detail("doc://two") is None


def test_undo_keeps_the_attestation_and_records_who_reversed_it() -> None:
    led = VendorLedger()
    vendor_id = led.create("Acme Steel Company")
    led.record_payment_detail(
        vendor_id, _detail("4321", "doc://one"), confirmed_by="reviewer-1",
        attestation=Attestation(channel="phone", contact="J. Vendor", note="known-good"))
    found = led.find_payment_detail("doc://one")
    assert found is not None
    led.undo_payment_detail(found[0], found[1].id, reversed_by="reviewer-2")
    vendor = led.get(vendor_id)
    assert vendor is not None
    kept = vendor.payment_history[0]
    assert (kept.confirmed_by, kept.confirmation_channel, kept.confirmation_contact,
            kept.confirmation_note) == ("reviewer-1", "phone", "J. Vendor", "known-good")
    assert kept.reversed_by == "reviewer-2"
    assert kept.reversed_at is not None


def test_undo_requires_a_named_reverser() -> None:
    led = VendorLedger()
    vendor_id = led.create("Acme Steel Company")
    led.record_payment_detail(vendor_id, _detail("4321", "doc://one"), confirmed_by="r")
    found = led.find_payment_detail("doc://one")
    assert found is not None
    with pytest.raises(ValueError):
        led.undo_payment_detail(found[0], found[1].id, reversed_by="")


def test_a_reversed_entry_is_still_seen_before() -> None:
    led = VendorLedger()
    vendor_id = led.create("Acme Steel Company")
    led.record_payment_detail(vendor_id, _detail("4321", "doc://one"), confirmed_by="r")
    led.record_payment_detail(vendor_id, _detail("9999", "doc://two"), confirmed_by="r")
    found = led.find_payment_detail("doc://two")
    assert found is not None
    led.undo_payment_detail(found[0], found[1].id, reversed_by="r2")
    # 9999 was confirmed then reversed: it is no baseline, yet it was on file.
    assert led.seen_before(vendor_id, _detail("9999", "doc://three")) is True


def test_a_second_undo_of_the_same_confirmation_finds_nothing() -> None:
    led = VendorLedger()
    vendor_id = led.create("Acme Steel Company")
    led.record_payment_detail(vendor_id, _detail("4321", "doc://one"), confirmed_by="r")
    found = led.find_payment_detail("doc://one")
    assert found is not None
    led.undo_payment_detail(found[0], found[1].id, reversed_by="r2")
    assert led.find_payment_detail("doc://one") is None


def test_reconfirming_after_undo_adds_a_new_entry() -> None:
    led = VendorLedger()
    vendor_id = led.create("Acme Steel Company")
    led.record_payment_detail(vendor_id, _detail("4321", "doc://one"), confirmed_by="r")
    first = led.find_payment_detail("doc://one")
    assert first is not None
    led.undo_payment_detail(first[0], first[1].id, reversed_by="r2")
    led.record_payment_detail(vendor_id, _detail("4321", "doc://one"), confirmed_by="r")
    second = led.find_payment_detail("doc://one")
    assert second is not None
    assert second[1].id != first[1].id
    vendor = led.get(vendor_id)
    assert vendor is not None
    assert len(vendor.payment_history) == 2


def test_an_unconfirmed_entry_is_never_found_or_used_as_a_baseline() -> None:
    led = VendorLedger()
    vendor_id = led.create("Acme Steel Company")
    led.record_payment_detail(vendor_id, _detail("4321", "doc://one"), confirmed_by="")
    assert led.find_payment_detail("doc://one") is None
