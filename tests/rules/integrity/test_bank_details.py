# REVISED (pre-flight scan, 2026-09-07): three defects, all in this fixture
# block. (1) `account_hmac` is a REQUIRED field on `PaymentDetail` (no
# default — deliberately: a blank default would make two blank hmacs
# spuriously "match", defeating the whole control) but neither `ledger` nor
# `_incoming()` supplied one — every test using either would fail with a
# Pydantic ValidationError before any comparison logic ran at all. (2)
# `payment_hmac` is called later in this file but was never imported. (3)
# `payment_hmac` reads `os.environ["LEDGER_SENSE_PAYMENT_HMAC_KEY"]` with no
# default (by design — see the docstring below) so it raises KeyError
# without it; `tests/store/conftest.py` already establishes this repo's
# pattern of scoping a required env var to the fixture that needs it via
# `monkeypatch`, followed here. The hmac inputs below use the last4 strings
# directly rather than a fabricated "full" account number — this module
# tests COMPARISON behaviour, not hmac computation, so what matters is only
# that identical inputs hmac identically and different ones don't.
import ast
import pathlib
from datetime import UTC, datetime

import pytest

from ledger_sense.domain.vendor import PaymentDetail, VendorLedger, payment_hmac
from ledger_sense.rules.integrity.bank_details import (
    BankDetailChangeError,
    assess_payment_detail,
    check_payment_detail,
)


@pytest.fixture(autouse=True)
def _hmac_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SENSE_PAYMENT_HMAC_KEY", "test-key-not-a-secret")


@pytest.fixture()
def ledger() -> tuple[VendorLedger, str]:
    led = VendorLedger()
    vendor_id = led.create("Acme Steel Company")
    led.record_payment_detail(vendor_id, PaymentDetail(
        account_last4="4321", routing_last4="8765",
        account_hmac=payment_hmac("4321", "8765"),
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        source_document="doc://original"), confirmed_by="A. Bookkeeper")
    return led, vendor_id


def _incoming(account: str = "4321", routing: str = "8765") -> PaymentDetail:
    return PaymentDetail(account_last4=account, routing_last4=routing,
                         account_hmac=payment_hmac(account, routing),
                         observed_at=datetime.now(UTC),
                         source_document="doc://current")


def test_matching_payment_details_produce_no_finding(
        ledger: tuple[VendorLedger, str]) -> None:
    led, vendor_id = ledger
    assert check_payment_detail(led, vendor_id, _incoming()) == []


def test_a_changed_account_number_is_a_hard_stop(
        ledger: tuple[VendorLedger, str]) -> None:
    led, vendor_id = ledger
    findings = check_payment_detail(led, vendor_id, _incoming(account="9999"))
    assert [f.rule_id for f in findings] == ["I3"]
    assert "hard stop" in findings[0].message.lower()


def test_a_changed_routing_number_is_also_a_hard_stop(
        ledger: tuple[VendorLedger, str]) -> None:
    led, vendor_id = ledger
    assert [f.rule_id for f in
            check_payment_detail(led, vendor_id, _incoming(routing="0000"))] == ["I3"]


def test_recording_a_changed_detail_without_confirmation_raises(
        ledger: tuple[VendorLedger, str]) -> None:
    """Never auto-approve and never auto-update. B5."""
    led, vendor_id = ledger
    with pytest.raises(BankDetailChangeError, match="human confirmation"):
        led.record_payment_detail(vendor_id, _incoming(account="9999"), confirmed_by="")


def test_history_is_append_only_so_the_diff_survives(
        ledger: tuple[VendorLedger, str]) -> None:
    led, vendor_id = ledger
    led.record_payment_detail(vendor_id, _incoming(account="9999"),
                              confirmed_by="A. Bookkeeper")
    canonical = led.get(vendor_id)
    assert canonical is not None
    history = canonical.payment_history
    assert len(history) == 2
    assert history[0].account_last4 == "4321"


def test_no_code_path_mutates_payment_history_outside_the_ledger() -> None:
    """record_payment_detail is the only writer, and it takes confirmed_by."""
    offenders: list[str] = []
    for path in pathlib.Path("ledger_sense").rglob("*.py"):
        if path.name == "vendor.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            attr = getattr(node.func, "attr", "")
            owner = getattr(getattr(node.func, "value", None), "attr", "")
            if attr in {"append", "extend", "clear"} and owner == "payment_history":
                offenders.append(f"{path}:{node.lineno}")
    assert offenders == [], f"payment_history mutated outside vendor.py: {offenders}"


def test_no_full_account_number_is_ever_stored() -> None:
    """Last four plus an HMAC. A full account number in our store is a breach
    waiting; four digits alone is a 1-in-10,000 miss on a fraud control (F-35).

    `id` was added by the code-review fix below (merge/unmerge must track
    migrated entries by identity, not by `observed_at` value) — it is a
    record identifier, not account data, so it belongs in this allow-list."""
    assert set(PaymentDetail.model_fields) == {
        "id", "account_last4", "routing_last4", "account_hmac", "observed_at",
        "source_document", "confirmed_by", "confirmed_at",
        "confirmation_channel", "confirmation_contact", "confirmation_note",
        "reversed_by", "reversed_at"}
    for field in ("account_number", "routing_number", "iban", "account"):
        assert field not in PaymentDetail.model_fields


def test_the_hmac_distinguishes_accounts_that_share_their_last_four(
        ledger: tuple[VendorLedger, str]) -> None:
    """F-35. Comparing four digits misses one redirection in ten thousand — on
    the rule the brief calls the single most expensive failure mode in AP."""
    led, vendor_id = ledger
    colliding = PaymentDetail(
        account_last4="4321", routing_last4="8765",
        account_hmac=payment_hmac("999994321", "128765"),
        observed_at=datetime.now(UTC), source_document="doc://current")
    findings = check_payment_detail(led, vendor_id, colliding)
    assert [f.rule_id for f in findings] == ["I3"]


def test_the_confirmation_is_stored_on_the_record(
        ledger: tuple[VendorLedger, str]) -> None:
    """F-35. record_payment_detail took confirmed_by and DISCARDED it, while
    spec §8 item 5 renders the change with its date and B5 requires the human
    confirmation to be on the record."""
    led, vendor_id = ledger
    led.record_payment_detail(vendor_id, _incoming(account="9999"),
                              confirmed_by="A. Bookkeeper")
    canonical = led.get(vendor_id)
    assert canonical is not None
    latest = canonical.payment_history[-1]
    assert latest.confirmed_by == "A. Bookkeeper"
    assert latest.confirmed_at is not None


def test_a_change_back_to_a_previously_seen_account_is_still_surfaced(
        ledger: tuple[VendorLedger, str]) -> None:
    """F-35. Comparing only against payment_history[-1] cannot see
    change-then-change-back, which is a documented redirection pattern."""
    led, vendor_id = ledger
    led.record_payment_detail(vendor_id, _incoming(account="9999"),
                              confirmed_by="A. Bookkeeper")
    reverted = _incoming(account="4321")
    assert led.seen_before(vendor_id, reverted) is True
    assert [f.rule_id for f in check_payment_detail(led, vendor_id, reverted)] == ["I3"]


def _undo(led: VendorLedger, doc: str) -> None:
    found = led.find_payment_detail(doc)
    assert found is not None
    led.undo_payment_detail(found[0], found[1].id, reversed_by="B. Reviewer")


def test_undoing_a_confirmation_restores_the_previous_baseline(
        ledger: tuple[VendorLedger, str]) -> None:
    """Undo keeps the reversed record as evidence, so the baseline must be
    computed from the entries still in force, not from the last one stored."""
    led, vendor_id = ledger
    changed = _incoming(account="9999")
    changed.source_document = "doc://second"
    led.record_payment_detail(vendor_id, changed, confirmed_by="A. Bookkeeper")
    assert check_payment_detail(led, vendor_id, _incoming(account="4321")) != []
    _undo(led, "doc://second")
    assert check_payment_detail(led, vendor_id, _incoming(account="4321")) == []


def test_a_reversed_account_is_never_trusted_again_but_is_recognised(
        ledger: tuple[VendorLedger, str]) -> None:
    """A confirmed-then-reversed account was on file: coming back to it is the
    change-back pattern, a hard stop, never a silent match."""
    led, vendor_id = ledger
    changed = _incoming(account="9999")
    changed.source_document = "doc://second"
    led.record_payment_detail(vendor_id, changed, confirmed_by="A. Bookkeeper")
    _undo(led, "doc://second")
    assessment = assess_payment_detail(led, vendor_id, _incoming(account="9999"))
    assert assessment is not None
    assert assessment.kind == "changed_back"
    assert assessment.expected_last4 == "4321"


def test_undoing_the_only_confirmation_makes_the_account_first_seen_again(
        ledger: tuple[VendorLedger, str]) -> None:
    led, vendor_id = ledger
    _undo(led, "doc://original")
    assessment = assess_payment_detail(led, vendor_id, _incoming(account="4321"))
    assert assessment is not None
    assert assessment.kind == "first_seen"


def test_first_seen_details_are_a_hard_stop_too() -> None:
    led = VendorLedger()
    vendor_id = led.create("Acme Steel Company")
    assessment = assess_payment_detail(led, vendor_id, _incoming())
    assert assessment is not None
    assert assessment.kind == "first_seen"
    assert assessment.expected_last4 is None
    assert "hard stop" in assessment.message.lower()


def test_a_vendor_that_does_not_exist_yet_is_first_seen() -> None:
    assessment = assess_payment_detail(VendorLedger(), None, _incoming())
    assert assessment is not None
    assert assessment.kind == "first_seen"


def test_an_unconfirmed_history_entry_is_not_a_baseline() -> None:
    led = VendorLedger()
    vendor_id = led.create("Acme Steel Company")
    led.record_payment_detail(vendor_id, _incoming(), confirmed_by="")
    assessment = assess_payment_detail(led, vendor_id, _incoming())
    assert assessment is not None
    assert assessment.kind == "first_seen"


def test_a_changed_account_reports_both_last_fours(
        ledger: tuple[VendorLedger, str]) -> None:
    led, vendor_id = ledger
    assessment = assess_payment_detail(led, vendor_id, _incoming(account="9999"))
    assert assessment is not None
    assert (assessment.kind, assessment.expected_last4, assessment.actual_last4) == (
        "changed", "4321", "9999")


def test_change_then_change_back_is_labelled_as_such(
        ledger: tuple[VendorLedger, str]) -> None:
    led, vendor_id = ledger
    led.record_payment_detail(vendor_id, _incoming(account="9999"), confirmed_by="A. Bookkeeper")
    assessment = assess_payment_detail(led, vendor_id, _incoming(account="4321"))
    assert assessment is not None
    assert assessment.kind == "changed_back"


def test_a_matching_confirmed_detail_is_clean(ledger: tuple[VendorLedger, str]) -> None:
    led, vendor_id = ledger
    assert assess_payment_detail(led, vendor_id, _incoming()) is None
