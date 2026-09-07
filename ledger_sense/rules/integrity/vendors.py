"""I2 — vendor resolution requires human confirmation on first fuzzy match."""

from ledger_sense.domain.money import money
from ledger_sense.domain.vendor import VendorLedger
from ledger_sense.rules.engine import Finding


def check_vendor_resolution(ledger: VendorLedger, name: str) -> list[Finding]:
    match = ledger.resolve(name)
    if match.vendor_id is None:
        return [Finding("I2", "vendor.name",
                        f"{name!r} does not match any canonical vendor; confirm "
                        f"whether this is a new vendor",
                        money("0.00"), money("0.00"))]
    if match.needs_confirmation:
        canonical = ledger.get(match.vendor_id)
        # resolve() only ever returns a vendor_id it found in the ledger, so
        # this is always present; the assert is for mypy's narrowing, not a
        # real runtime possibility.
        assert canonical is not None
        return [Finding("I2", "vendor.name",
                        f"{name!r} closely matches {canonical.name!r} "
                        f"(score {match.score:.0f}). Confirm before linking — "
                        f"merging two genuinely different vendors is unrecoverable",
                        money("0.00"), money("0.00"))]
    return []
