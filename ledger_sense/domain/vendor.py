"""Canonical vendors, aliases and payment-detail history.

B6: auto-merging two vendors is UNRECOVERABLE. Every first-time fuzzy match
requires human confirmation; merges are reversible; every alias keeps provenance.

B5: a bank-detail change is a HARD STOP. This module surfaces and records; it
never auto-approves and never auto-updates. Invoice redirection fraud is the
single most expensive failure mode in AP.

Only the last four digits are ever stored. A full account number in our store
is a breach waiting to happen.
"""

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

# int, not float literals: this module lives under ledger_sense/domain, which
# scripts/check_no_float.py's AST scan walks for the money path (Never-rule
# #2). These thresholds are fuzzy-match scores (0-100), not money, but the
# scanner is a blunt literal check with no semantic awareness — int compares
# against rapidfuzz's float scores without needing a float literal here.
EXACT = 100
CONFIRM_THRESHOLD = 85


class PaymentDetail(BaseModel):
    """REVISED (F-35). Two defects in the highest-consequence rule in the product.

    DETECTION. Comparing four digits gives a 1-in-10,000 chance that a fraudulent
    redirection is invisible, and the comparison ran only against
    payment_history[-1] — so an attacker who changes details, obtains one
    confirmation, then changes back was never flagged. `account_hmac` is
    HMAC-SHA256 over the full normalised account+routing under a key held only in
    the environment: an exact comparison with no collisions, and still no
    recoverable account number in our store.

    PROVENANCE. `record_payment_detail` took `confirmed_by` and DISCARDED it.
    Spec §8 item 5 requires the vendor ledger to render bank-detail changes "as a
    red-lined diff with the date and source document", and B5 requires human
    confirmation ON THE RECORD. It was not on the record.
    """
    account_last4: str
    routing_last4: str
    account_hmac: str            # HMAC of the full details; never the details
    observed_at: datetime
    source_document: str
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None


def payment_hmac(account: str, routing: str) -> str:
    """Key from LEDGER_SENSE_PAYMENT_HMAC_KEY. Never logged, never stored beside
    the digest, never returned by any API."""
    import hmac
    import os
    key = os.environ["LEDGER_SENSE_PAYMENT_HMAC_KEY"].encode()
    normalised = f"{account.strip()}|{routing.strip()}".encode()
    return hmac.new(key, normalised, "sha256").hexdigest()


class VendorAlias(BaseModel):
    # REVISED (pre-flight scan, 2026-09-07). `id` and `merged_from` were read
    # by `merge`/`unmerge` below (`alias.id`, `merged_from=snapshot.id`,
    # `a.id != token.alias_id`) but never declared as fields — the same class
    # of gap F-67 found elsewhere in this plan: the fix described in prose,
    # never applied to the code. `unmerge` needs `id` to remove the merge
    # alias BY IDENTITY, which is the whole point of the F-36 fix.
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    first_seen: datetime
    source_document: str
    confirmed_by: str
    merged_from: str | None = None


class CanonicalVendor(BaseModel):
    id: str
    name: str
    aliases: list[VendorAlias] = Field(default_factory=list)
    payment_history: list[PaymentDetail] = Field(default_factory=list)


class Match(BaseModel):
    vendor_id: str | None
    score: float
    needs_confirmation: bool


class MergeToken(BaseModel):
    # REVISED (pre-flight scan, 2026-09-07). `alias_id` and `migrated_details`
    # are set by `merge` and read by `unmerge` below, but were missing here —
    # `merge()`'s own MergeToken(...) call already passes them.
    keeper: str
    absorbed_snapshot: CanonicalVendor
    confirmed_by: str
    alias_id: str
    migrated_details: list[datetime]


class BankDetailChangeError(Exception):
    pass


class VendorLedger:
    def __init__(self) -> None:
        self._vendors: dict[str, CanonicalVendor] = {}

    def create(self, name: str) -> str:
        vendor_id = str(uuid.uuid4())
        self._vendors[vendor_id] = CanonicalVendor(id=vendor_id, name=name)
        return vendor_id

    def get(self, vendor_id: str) -> CanonicalVendor | None:
        return self._vendors.get(vendor_id)

    def resolve(self, name: str) -> Match:
        best_id: str | None = None
        best_score: float = 0
        for vendor in self._vendors.values():
            candidates = [vendor.name] + [a.name for a in vendor.aliases]
            # partial_ratio, not token_sort_ratio: this fuzzy-matches "Co."
            # against "Company" (a routine AP abbreviation variant) at 96,
            # inside the confirm band. token_sort_ratio scores that same pair
            # 81 — below CONFIRM_THRESHOLD — which would silently miss the
            # exact abbreviation case B6's human-confirmation gate exists for.
            score = max(fuzz.partial_ratio(name, c) for c in candidates)
            if score > best_score:
                best_id, best_score = vendor.id, score
        if best_score >= EXACT:
            return Match(vendor_id=best_id, score=best_score, needs_confirmation=False)
        if best_score >= CONFIRM_THRESHOLD:
            return Match(vendor_id=best_id, score=best_score, needs_confirmation=True)
        return Match(vendor_id=None, score=best_score, needs_confirmation=False)

    def confirm_alias(self, vendor_id: str, name: str, source_document: str,
                      confirmed_by: str) -> None:
        if not confirmed_by:
            raise ValueError("confirm_alias requires confirmed_by")
        self._vendors[vendor_id].aliases.append(VendorAlias(
            name=name, first_seen=datetime.now(UTC),
            source_document=source_document, confirmed_by=confirmed_by))

    def merge(self, keeper: str, absorbed: str, confirmed_by: str) -> MergeToken:
        """REVISED (F-36). The original popped the absorbed vendor and appended a
        single alias — so `payment_history` went with the popped record, and the
        keeper had NO knowledge of the absorbed record's bank details. The very
        next invoice against the keeper then compared against the wrong baseline.

        The fraud control silently lost its evidence at exactly the moment two
        records were reconciled. B6 requires "merges are reversible with
        provenance for every alias"; neither half held.
        """
        if not confirmed_by:
            raise ValueError("merge requires confirmed_by — B6, merges without "
                             "provenance are unrecoverable")
        snapshot = self._vendors.pop(absorbed)
        keeper_record = self._vendors[keeper]

        alias = VendorAlias(
            name=snapshot.name, first_seen=datetime.now(UTC),
            source_document="merge", confirmed_by=confirmed_by,
            merged_from=snapshot.id)
        keeper_record.aliases.append(alias)

        # The payment history MIGRATES. It is the I3 baseline.
        moved = list(snapshot.payment_history)
        keeper_record.payment_history = sorted(
            keeper_record.payment_history + moved, key=lambda d: d.observed_at)

        return MergeToken(keeper=keeper, absorbed_snapshot=snapshot,
                          confirmed_by=confirmed_by,
                          alias_id=alias.id,
                          migrated_details=[d.observed_at for d in moved])

    def unmerge(self, token: MergeToken) -> None:
        """REVISED (F-36). The original removed keeper aliases BY NAME, which
        destroys a pre-existing alias that happened to share the absorbed
        vendor's name. Remove by identity, and hand the migrated history back."""
        self._vendors[token.absorbed_snapshot.id] = token.absorbed_snapshot
        keeper = self._vendors[token.keeper]
        keeper.aliases = [a for a in keeper.aliases if a.id != token.alias_id]
        migrated = set(token.migrated_details)
        keeper.payment_history = [d for d in keeper.payment_history
                                  if d.observed_at not in migrated]

    def record_payment_detail(self, vendor_id: str, detail: PaymentDetail,
                              confirmed_by: str) -> None:
        """Append-only. A CHANGED detail requires a named human confirmer, and
        the confirmation is STORED (F-35) — spec §8 item 5 renders it.

        Comparison is on the HMAC, not on four digits: last4 gives a 1-in-10,000
        miss rate on a fraud control, and comparing only against the most recent
        entry misses change-then-change-back.
        """
        vendor = self._vendors[vendor_id]
        current = vendor.payment_history[-1] if vendor.payment_history else None
        changed = current is not None and current.account_hmac != detail.account_hmac
        if changed and not confirmed_by:
            raise BankDetailChangeError(
                "a bank-detail change requires human confirmation; this system "
                "never auto-approves and never auto-updates a payment record")
        vendor.payment_history.append(detail.model_copy(update={
            "confirmed_by": confirmed_by or None,
            "confirmed_at": datetime.now(UTC) if confirmed_by else None}))

    def seen_before(self, vendor_id: str, detail: PaymentDetail) -> bool:
        """Has this account EVER been on file for this vendor? Change-then-
        change-back is a documented redirection pattern and comparing only
        against payment_history[-1] cannot see it (F-35)."""
        vendor = self._vendors[vendor_id]
        return any(d.account_hmac == detail.account_hmac
                   for d in vendor.payment_history[:-1])
