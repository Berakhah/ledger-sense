"""I3 — bank-detail change is a HARD STOP.

Invoice redirection fraud is the single most expensive failure mode in AP.
Catching one instance pays for the product for years.

B5 cuts both ways: flagging a legitimate change delays a payment; missing a
fraudulent one sends money to an attacker. Position: SURFACE and REQUIRE HUMAN
CONFIRMATION. Never approve, never auto-update. Terms must say the client is
responsible for verification.
"""

from ledger_sense.domain.money import money
from ledger_sense.domain.vendor import BankDetailChangeError, PaymentDetail, VendorLedger
from ledger_sense.rules.engine import Finding

__all__ = ["BankDetailChangeError", "check_payment_detail"]


def check_payment_detail(ledger: VendorLedger, vendor_id: str,
                         incoming: PaymentDetail) -> list[Finding]:
    # REVISED (pre-flight scan, 2026-09-07): compares `account_hmac`, not
    # last4. F-35's whole point is that four digits alone give a 1-in-10,000
    # miss rate on this control; `record_payment_detail` below already
    # compares on the hmac (`current.account_hmac != detail.account_hmac`) —
    # this function, the one that actually raises the I3 finding, was left
    # on the old comparison, silently keeping the exact miss rate F-35 set
    # out to close.
    vendor = ledger.get(vendor_id)
    if vendor is None or not vendor.payment_history:
        return []
    current = vendor.payment_history[-1]
    if current.account_hmac == incoming.account_hmac:
        return []
    return [Finding(
        "I3", "vendor.payment_detail",
        f"HARD STOP: payment details for {vendor.name} differ from the record on "
        f"file (account ....{current.account_last4} -> ....{incoming.account_last4}). "
        f"Verify with the vendor using a known-good phone number before paying. "
        f"This system will not update the record without your confirmation.",
        money("0.00"), money("0.00"))]
