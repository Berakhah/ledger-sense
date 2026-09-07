from datetime import date, timedelta

from ledger_sense.domain.money import money
from ledger_sense.rules.integrity.duplicates import DocumentFingerprint, find_duplicates


# REVISED (pre-flight scan, 2026-09-07): `vendor_id=` (matching key), not
# `vendor=` — matches the DocumentFingerprint.vendor_id fix below (F-37:
# matching must be scoped to the RESOLVED vendor id, not a raw name two
# different vendors could share). `vendor` stays as an optional DISPLAY-ONLY
# passthrough, since two tests below construct fingerprints by raw vendor
# name without caring about resolution.
def _fp(vendor_id: str = "Acme Steel", vendor: str | None = None,
       amount: str = "1000.00", days: int = 0, invoice: str | None = "INV-1",
       line_hash: str = "h1") -> DocumentFingerprint:
    return DocumentFingerprint(vendor_id=vendor_id, vendor=vendor, amount=money(amount),
                               doc_date=date(2026, 8, 1) + timedelta(days=days),
                               invoice_number=invoice, line_hash=line_hash)


def test_clean_control_distinct_documents_produce_no_duplicate() -> None:
    history = [_fp(invoice="INV-1"),
               _fp(amount="2000.00", invoice="INV-2", line_hash="h2")]
    assert find_duplicates(
        _fp(amount="3000.00", invoice="INV-3", line_hash="h3"), history) == []


def test_same_invoice_number_is_a_duplicate() -> None:
    matches = find_duplicates(_fp(invoice="INV-1"), [_fp(invoice="INV-1")])
    assert matches and matches[0].reason == "same invoice number"


def test_same_vendor_amount_within_the_date_window_is_a_duplicate() -> None:
    """Detection spans FULL history, not just the current period."""
    matches = find_duplicates(_fp(days=5, invoice="INV-9", line_hash="h9"),
                              [_fp(days=0, invoice="INV-1")])
    assert matches and matches[0].reason == "vendor+amount within date window"


def test_outside_the_date_window_is_not_a_duplicate() -> None:
    assert find_duplicates(_fp(days=400, invoice="INV-9", line_hash="h9"),
                           [_fp(days=0, invoice="INV-1")]) == []


def test_identical_line_item_fingerprint_is_a_duplicate() -> None:
    matches = find_duplicates(_fp(vendor="Acme Steel Co", amount="999.00",
                                  days=200, invoice="INV-9", line_hash="h1"),
                              [_fp(line_hash="h1")])
    assert matches and matches[0].reason == "identical line-item fingerprint"


def test_an_empty_history_produces_no_duplicates() -> None:
    assert find_duplicates(_fp(), []) == []


def test_the_same_invoice_number_from_a_different_vendor_is_not_a_duplicate() -> None:
    """REGRESSION — F-37. Two vendors both numbering from INV-1001 produced a
    permanent false duplicate, and an obviously-wrong duplicate finding is how a
    reviewer learns to dismiss duplicate findings."""
    assert find_duplicates(_fp(vendor_id="v2", invoice="INV-1"),
                           [_fp(vendor_id="v1", invoice="INV-1")]) == []


def test_the_same_line_hash_from_a_different_vendor_is_not_a_duplicate() -> None:
    assert find_duplicates(_fp(vendor_id="v2", invoice="INV-9", line_hash="h1"),
                           [_fp(vendor_id="v1", line_hash="h1")]) == []


def test_matching_uses_the_resolved_vendor_id_not_the_raw_name() -> None:
    """'Acme Steel Co.' and 'Acme Steel Company' must collide once I2 has
    resolved them; matching on the raw string is the same defect one layer down."""
    assert find_duplicates(_fp(vendor_id="v1", vendor="Acme Steel Co.", invoice="INV-1"),
                           [_fp(vendor_id="v1", vendor="Acme Steel Company",
                                invoice="INV-1")])
