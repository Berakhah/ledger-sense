"""I3 — bank-detail change is a HARD STOP.

Invoice redirection fraud is the single most expensive failure mode in AP.
Catching one instance pays for the product for years.

B5 cuts both ways: flagging a legitimate change delays a payment; missing a
fraudulent one sends money to an attacker. Position: SURFACE and REQUIRE HUMAN
CONFIRMATION. Never approve, never auto-update. Terms must say the client is
responsible for verification.
"""

from typing import Literal, NamedTuple

from ledger_sense.domain.money import money
from ledger_sense.domain.vendor import BankDetailChangeError, PaymentDetail, VendorLedger
from ledger_sense.rules.engine import Finding

__all__ = ["BankAssessment", "BankDetailChangeError", "assess_payment_detail",
           "check_payment_detail"]


class BankAssessment(NamedTuple):
    kind: Literal["first_seen", "changed", "changed_back"]
    message: str
    expected_last4: str | None
    actual_last4: str


def assess_payment_detail(ledger: VendorLedger, vendor_id: str | None,
                          incoming: PaymentDetail) -> BankAssessment | None:
    """None only when the incoming detail equals the vendor's last CONFIRMED
    one. Everything else is a hard stop: a first-seen detail has nothing on file
    to be compared with (no trust-on-first-use), a changed one may be a
    redirection, and a change BACK to an earlier account is a documented
    redirection pattern (F-35).

    Compares `account_hmac`, not last4 (F-35: four digits alone miss 1 in
    10,000). An unconfirmed history entry is never the baseline."""
    vendor = ledger.get(vendor_id) if vendor_id is not None else None
    name = vendor.name if vendor is not None else "this vendor"
    confirmed = [d for d in vendor.payment_history if d.confirmed_by] if vendor else []
    if not confirmed:
        return BankAssessment(
            "first_seen",
            f"HARD STOP: first bank details seen for {name} "
            f"(account ....{incoming.account_last4}); nothing on file confirms them. "
            f"Verify with the vendor using a known-good phone number before paying. "
            f"This system will not record them without your attestation.",
            None, incoming.account_last4)
    current = confirmed[-1]
    if current.account_hmac == incoming.account_hmac:
        return None
    came_back = any(d.account_hmac == incoming.account_hmac for d in confirmed[:-1])
    lead = (f"HARD STOP: payment details for {name} changed BACK to an account seen earlier"
            if came_back else
            f"HARD STOP: payment details for {name} differ from the record on file")
    return BankAssessment(
        "changed_back" if came_back else "changed",
        lead + f" (account ....{current.account_last4} -> ....{incoming.account_last4}). "
        f"Verify with the vendor using a known-good phone number before paying. "
        f"This system will not update the record without your confirmation.",
        current.account_last4, incoming.account_last4)


def check_payment_detail(ledger: VendorLedger, vendor_id: str,
                         incoming: PaymentDetail) -> list[Finding]:
    assessment = assess_payment_detail(ledger, vendor_id, incoming)
    if assessment is None:
        return []
    return [Finding("I3", "vendor.payment_detail", assessment.message,
                    money("0.00"), money("0.00"))]
