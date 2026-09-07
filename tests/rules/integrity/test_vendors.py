from datetime import UTC, datetime

import pytest

from ledger_sense.domain.vendor import PaymentDetail, VendorLedger, payment_hmac

# GAP not covered by the brief's four pre-flight fixes: `_detail()` is called
# below (F-36 regression tests) but never defined in the plan text, and
# PaymentDetail.account_hmac is a required field (F-35). Mirrors `_incoming()`
# in test_bank_details.py and the same monkeypatch-scoped-env-var pattern.


@pytest.fixture(autouse=True)
def _hmac_key(monkeypatch):
    monkeypatch.setenv("LEDGER_SENSE_PAYMENT_HMAC_KEY", "test-key-not-a-secret")


@pytest.fixture()
def ledger():
    led = VendorLedger()
    led.create("Acme Steel Company")
    return led


def _detail(account="4321", routing="8765"):
    return PaymentDetail(account_last4=account, routing_last4=routing,
                         account_hmac=payment_hmac(account, routing),
                         observed_at=datetime.now(UTC),
                         source_document="doc://vendor-test")


def test_an_exact_name_resolves_without_confirmation(ledger):
    match = ledger.resolve("Acme Steel Company")
    assert match.vendor_id is not None and match.needs_confirmation is False


def test_a_close_name_requires_human_confirmation_on_first_match(ledger):
    """B6: auto-merging two vendors is unrecoverable."""
    match = ledger.resolve("Acme Steel Co.")
    assert match.vendor_id is not None
    assert match.needs_confirmation is True


def test_a_confirmed_alias_resolves_without_confirmation_next_time(ledger):
    match = ledger.resolve("Acme Steel Co.")
    ledger.confirm_alias(match.vendor_id, "Acme Steel Co.",
                         source_document="doc://1", confirmed_by="A. Bookkeeper")
    assert ledger.resolve("Acme Steel Co.").needs_confirmation is False


def test_an_unrelated_name_does_not_resolve(ledger):
    assert ledger.resolve("Zenith Glazing").vendor_id is None


def test_every_alias_carries_provenance(ledger):
    match = ledger.resolve("Acme Steel Co.")
    ledger.confirm_alias(match.vendor_id, "Acme Steel Co.",
                         source_document="doc://1", confirmed_by="A. Bookkeeper")
    alias = ledger.get(match.vendor_id).aliases[-1]
    assert alias.source_document == "doc://1"
    assert alias.confirmed_by == "A. Bookkeeper"


def test_confirm_alias_refuses_without_a_named_confirmer(ledger):
    match = ledger.resolve("Acme Steel Co.")
    with pytest.raises(ValueError, match="confirmed_by"):
        ledger.confirm_alias(match.vendor_id, "Acme Steel Co.",
                             source_document="doc://1", confirmed_by="")


def test_a_merge_is_reversible(ledger):
    other = ledger.create("ACME Steel Co")
    keeper = ledger.resolve("Acme Steel Company").vendor_id
    token = ledger.merge(keeper=keeper, absorbed=other, confirmed_by="A. Bookkeeper")
    assert ledger.get(other) is None
    ledger.unmerge(token)
    assert ledger.get(other) is not None


def test_a_merge_carries_the_absorbed_payment_history_across(ledger):
    """REGRESSION — F-36. The original popped the absorbed vendor and its
    payment_history went with it, so the keeper had no knowledge of the absorbed
    record's bank details and the next invoice compared against the wrong
    baseline. The fraud control lost its evidence at the moment two records were
    reconciled."""
    other = ledger.create("ACME Steel Co")
    ledger.record_payment_detail(other, _detail(account="9999"),
                                 confirmed_by="A. Bookkeeper")
    keeper = ledger.resolve("Acme Steel Company").vendor_id
    ledger.merge(keeper=keeper, absorbed=other, confirmed_by="A. Bookkeeper")
    assert any(d.account_last4 == "9999"
               for d in ledger.get(keeper).payment_history)


def test_unmerge_is_the_exact_inverse_of_merge(ledger):
    other = ledger.create("ACME Steel Co")
    ledger.record_payment_detail(other, _detail(account="9999"),
                                 confirmed_by="A. Bookkeeper")
    keeper = ledger.resolve("Acme Steel Company").vendor_id
    before_keeper = ledger.get(keeper).model_dump_json()
    before_other = ledger.get(other).model_dump_json()

    token = ledger.merge(keeper=keeper, absorbed=other, confirmed_by="A. Bookkeeper")
    ledger.unmerge(token)

    assert ledger.get(keeper).model_dump_json() == before_keeper
    assert ledger.get(other).model_dump_json() == before_other


def test_unmerge_does_not_destroy_a_legitimate_alias_of_the_same_name(ledger):
    """F-36. `unmerge` removed keeper aliases BY NAME."""
    keeper = ledger.resolve("Acme Steel Company").vendor_id
    ledger.confirm_alias(keeper, "ACME Steel Co", source_document="doc://earlier",
                         confirmed_by="A. Bookkeeper")
    other = ledger.create("ACME Steel Co")
    token = ledger.merge(keeper=keeper, absorbed=other, confirmed_by="A. Bookkeeper")
    ledger.unmerge(token)
    assert any(a.source_document == "doc://earlier"
               for a in ledger.get(keeper).aliases)


def test_merge_refuses_without_a_named_confirmer(ledger):
    other = ledger.create("ACME Steel Co")
    keeper = ledger.resolve("Acme Steel Company").vendor_id
    with pytest.raises(ValueError, match="confirmed_by"):
        ledger.merge(keeper=keeper, absorbed=other, confirmed_by="")
