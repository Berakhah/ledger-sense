from datetime import UTC, datetime

import pytest

from ledger_sense.domain.vendor import PaymentDetail, VendorLedger, payment_hmac
from ledger_sense.rules.integrity.vendors import check_vendor_resolution

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


def _detail(account="4321", routing="8765", observed_at=None):
    return PaymentDetail(account_last4=account, routing_last4=routing,
                         account_hmac=payment_hmac(account, routing),
                         observed_at=observed_at or datetime.now(UTC),
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


def test_a_substring_name_does_not_silently_auto_resolve(ledger):
    """CODE REVIEW FIX. A partial-ratio-style scorer scores a short name at
    100 -- the EXACT/no-confirmation band -- whenever it is merely CONTAINED
    in a longer one ('Turner' inside 'Turner Construction Co.'), silently
    auto-resolving two genuinely different vendors with no human
    confirmation (Never-rule #6). token_sort_ratio has no such containment
    pathology; assert the fix holds."""
    ledger.create("Turner Construction Co.")
    match = ledger.resolve("Turner")
    assert not (match.vendor_id is not None and match.needs_confirmation is False)


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


def test_unmerge_does_not_delete_the_keepers_own_payment_history_on_a_timestamp_collision(
        ledger):
    """CODE REVIEW FIX. merge/unmerge tracked migrated payment_history entries
    by `observed_at` VALUE, not identity -- the same bug class F-36 already
    fixed for aliases. If the keeper's own entry happened to share an
    observed_at with one migrated in from the absorbed vendor, `unmerge`
    deleted BOTH by value equality: the keeper's legitimate record along with
    the migrated one. Force the collision explicitly rather than relying on
    two `datetime.now()` calls happening to differ."""
    collision_at = datetime(2026, 1, 1, tzinfo=UTC)
    keeper = ledger.resolve("Acme Steel Company").vendor_id
    ledger.record_payment_detail(
        keeper, _detail(account="1111", observed_at=collision_at),
        confirmed_by="A. Bookkeeper")
    other = ledger.create("ACME Steel Co")
    ledger.record_payment_detail(
        other, _detail(account="9999", observed_at=collision_at),
        confirmed_by="A. Bookkeeper")

    token = ledger.merge(keeper=keeper, absorbed=other, confirmed_by="A. Bookkeeper")
    assert len(ledger.get(keeper).payment_history) == 2

    ledger.unmerge(token)
    after = ledger.get(keeper).payment_history
    assert len(after) == 1
    assert after[0].account_last4 == "1111"


def test_check_vendor_resolution_flags_an_unrecognised_name(ledger):
    """I2 tripping fixture: no canonical vendor is close enough to match."""
    findings = check_vendor_resolution(ledger, "Zenith Glazing")
    assert [f.rule_id for f in findings] == ["I2"]


def test_check_vendor_resolution_flags_a_name_needing_confirmation(ledger):
    """I2 tripping fixture: a close-but-not-exact name must not resolve silently."""
    findings = check_vendor_resolution(ledger, "Acme Steel Co.")
    assert [f.rule_id for f in findings] == ["I2"]


def test_check_vendor_resolution_clean_control_produces_no_finding(ledger):
    """I2 clean control: an exact, already-resolved name produces no finding."""
    assert check_vendor_resolution(ledger, "Acme Steel Company") == []
