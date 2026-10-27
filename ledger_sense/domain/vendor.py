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
from typing import Literal

from pydantic import Field, field_validator
from rapidfuzz import fuzz, utils

from ledger_sense.domain.models import _Base

# int, not float literals: this module lives under ledger_sense/domain, which
# scripts/check_no_float.py's AST scan walks for the money path (Never-rule
# #2). These thresholds are fuzzy-match scores (0-100), not money, but the
# scanner is a blunt literal check with no semantic awareness — int compares
# against rapidfuzz's float scores without needing a float literal here.
#
# CODE REVIEW FIX: CONFIRM_THRESHOLD was 85, tuned against fuzz.partial_ratio.
# partial_ratio scores 100 (the EXACT/no-confirmation band) whenever the
# SHORTER string is merely CONTAINED in the longer one — "Acme" inside "Acme
# Steel Company" also hits 100 — so it silently auto-resolved two genuinely
# different vendors with no human confirmation (Never-rule #6). Reverted to
# token_sort_ratio, which has no such containment pathology (see
# test_a_substring_name_does_not_silently_auto_resolve), and lowered the
# threshold from 85 to 80 so it still catches token_sort_ratio's own score of
# 81 on "Acme Steel Co." vs "Acme Steel Company" — the abbreviation variant
# the threshold exists to confirm.
EXACT = 100
CONFIRM_THRESHOLD = 80


class Attestation(_Base):
    """How a human satisfied the I3 hard stop: an out-of-band verification, with
    enough evidence on the record to be audited later (B5, Never-rule 6)."""
    channel: Literal["phone", "in_person", "video_call", "other"]
    contact: str
    note: str | None = None

    @field_validator("contact")
    @classmethod
    def _contact_named(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("an attestation must name who was spoken to")
        return value.strip()


class PaymentDetail(_Base):
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
    # CODE REVIEW FIX: `merge`/`unmerge` tracked migrated entries by
    # `observed_at` VALUE, not identity — the same bug class F-36 already
    # fixed for VendorAlias. Two entries can share a timestamp (the keeper's
    # own pre-existing record and one migrated in from the absorbed vendor),
    # and `unmerge` then deleted the keeper's legitimate entry alongside the
    # migrated one. `id` fixes it the same way VendorAlias.id does.
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    account_last4: str
    routing_last4: str
    account_hmac: str            # HMAC of the full details; never the details
    observed_at: datetime
    source_document: str
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    confirmation_channel: str | None = None
    confirmation_contact: str | None = None
    confirmation_note: str | None = None
    # Undo keeps the record as evidence and stamps who reversed it and when.
    # A reversed entry is no baseline (is_active) but was still on file.
    reversed_by: str | None = None
    reversed_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        """Confirmed by a human and not since reversed: the only kind of entry
        that may serve as the vendor's bank-detail baseline."""
        return bool(self.confirmed_by) and self.reversed_at is None


def payment_hmac(account: str, routing: str) -> str:
    """Key from LEDGER_SENSE_PAYMENT_HMAC_KEY. Never logged, never stored beside
    the digest, never returned by any API."""
    import hmac
    import os
    key = os.environ["LEDGER_SENSE_PAYMENT_HMAC_KEY"].encode()
    normalised = f"{account.strip()}|{routing.strip()}".encode()
    return hmac.new(key, normalised, "sha256").hexdigest()


class VendorAlias(_Base):
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


class CanonicalVendor(_Base):
    id: str
    name: str
    aliases: list[VendorAlias] = Field(default_factory=list)
    payment_history: list[PaymentDetail] = Field(default_factory=list)


class Match(_Base):
    vendor_id: str | None
    # float, not Decimal: a rapidfuzz match score (0-100), not money. See
    # tests/test_no_floats.py's explicit allow-list for this field.
    score: float
    needs_confirmation: bool


class MergeToken(_Base):
    # REVISED (pre-flight scan, 2026-09-07). `alias_id` and `migrated_details`
    # are set by `merge` and read by `unmerge` below, but were missing here —
    # `merge()`'s own MergeToken(...) call already passes them.
    keeper: str
    absorbed_snapshot: CanonicalVendor
    confirmed_by: str
    alias_id: str
    migrated_detail_ids: list[str]


class BankDetailChangeError(Exception):
    pass


class VendorLedger:
    def __init__(self) -> None:
        self._vendors: dict[str, CanonicalVendor] = {}

    def all_vendors(self) -> list[CanonicalVendor]:
        """Every canonical vendor this ledger currently holds — the whole
        state a repository snapshot-replace needs to persist. Read-only:
        callers get the live CanonicalVendor objects, not copies, matching
        `get()`'s existing behaviour."""
        return list(self._vendors.values())

    @classmethod
    def from_vendors(cls, vendors: list[CanonicalVendor]) -> "VendorLedger":
        """Rehydrate a ledger from a repository read. The inverse of
        `all_vendors()` — round-trips exactly, including each vendor's
        aliases and payment_history, since CanonicalVendor already carries
        both."""
        ledger = cls()
        ledger._vendors = {v.id: v for v in vendors}
        return ledger

    def create(self, name: str) -> str:
        vendor_id = str(uuid.uuid4())
        self._vendors[vendor_id] = CanonicalVendor(id=vendor_id, name=name)
        return vendor_id

    def create_confirmed(self, name: str, alias_name: str, source_document: str,
                         confirmed_by: str) -> str:
        """The only path that may create a NEW vendor in production
        (api/review.py's confirm_vendor, on its new-vendor branch) —
        deliberately, so a vendor can never enter the ledger with zero
        evidence. `undo_confirmation`'s own "delete a vendor left with no
        evidence" cleanup depends on that: a bare `create()` with no
        following alias would be indistinguishable from a vendor undo just
        emptied, and get silently deleted even if it meant something on its
        own. `create()` itself stays public and unconstrained — tests use it
        directly to model a vendor with no aliases yet, which is a real
        state for `resolve()` to be tested against, just never a state this
        method's own caller may produce."""
        vendor_id = self.create(name)
        self.confirm_alias(vendor_id, alias_name, source_document=source_document,
                           confirmed_by=confirmed_by)
        return vendor_id

    def get(self, vendor_id: str) -> CanonicalVendor | None:
        return self._vendors.get(vendor_id)

    def resolve(self, name: str) -> Match:
        # CODE REVIEW FIX: `processor=utils.default_process` case- and
        # whitespace-normalises both sides before scoring. Without it an
        # ALL-CAPS rendering of a canonical name (the norm in OCR'd document
        # headers) scores far below its own canonical name and I2 reports a
        # known vendor as new — a second empty-history canonical record then
        # gets created. I3 no longer depends on history being non-empty: an
        # empty or unconfirmed payment_history yields a first-seen I3 hard stop,
        # so such a record is stopped rather than waved through.
        best_ids: list[str] = []
        best_score: float = 0
        for vendor in self._vendors.values():
            candidates = [vendor.name] + [a.name for a in vendor.aliases]
            score = max(fuzz.token_sort_ratio(name, c, processor=utils.default_process)
                        for c in candidates)
            if score > best_score:
                best_ids, best_score = [vendor.id], score
            elif score == best_score and score > 0:
                best_ids.append(vendor.id)
        if best_score >= CONFIRM_THRESHOLD:
            # CODE REVIEW FIX: two canonical records can legitimately tie on
            # name (that's the state merge() exists to fix) — a tie at the
            # top score must never silently bind to whichever vendor was
            # inserted first, even when the tied score is EXACT.
            ambiguous = len(best_ids) > 1
            return Match(vendor_id=best_ids[0], score=best_score,
                        needs_confirmation=ambiguous or best_score < EXACT)
        return Match(vendor_id=None, score=best_score, needs_confirmation=False)

    def confirm_alias(self, vendor_id: str, name: str, source_document: str,
                      confirmed_by: str) -> None:
        if not confirmed_by:
            raise ValueError("confirm_alias requires confirmed_by")
        self._vendors[vendor_id].aliases.append(VendorAlias(
            name=name, first_seen=datetime.now(UTC),
            source_document=source_document, confirmed_by=confirmed_by))

    def alias_confirmed_from(self, vendor_id: str, source_document: str) -> VendorAlias | None:
        """The alias — if any — that one document's confirm-vendor call added
        to this vendor. A document confirms at most once (the review
        endpoint's own compare-and-swap guarantees that), so at most one
        alias ever carries this source_document for a given vendor; the last
        match is returned defensively rather than asserting uniqueness.

        Undo's lookup: returns the alias itself (identity), not merely
        whether a name matched — the same discipline `merge`/`unmerge`
        above apply, since removing by value instead of identity is exactly
        the class of bug F-36/F-67 fixed twice in this file already."""
        vendor = self._vendors.get(vendor_id)
        if vendor is None:
            return None
        matches = [a for a in vendor.aliases if a.source_document == source_document]
        return matches[-1] if matches else None

    def undo_confirmation(self, vendor_id: str, alias_id: str) -> None:
        """Reverse one confirm-vendor call: remove the alias by identity, and
        delete the vendor entirely if it now carries no evidence at all — a
        vendor created solely for this confirmation leaves no orphan record
        behind once undone. A no-op if the vendor no longer exists."""
        vendor = self._vendors.get(vendor_id)
        if vendor is None:
            return
        vendor.aliases = [a for a in vendor.aliases if a.id != alias_id]
        if not vendor.aliases and not vendor.payment_history:
            del self._vendors[vendor_id]

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
        # CODE REVIEW FIX: both ids must exist, and must be different, BEFORE
        # anything is popped. The original popped `absorbed` first — a
        # stale/typo'd `keeper` id then raised KeyError on the next line with
        # the absorbed vendor (and its payment_history, I3's fraud baseline)
        # already gone and no MergeToken to undo it.
        if keeper not in self._vendors or absorbed not in self._vendors:
            raise KeyError(f"merge needs two existing vendors: {keeper!r}, {absorbed!r}")
        if keeper == absorbed:
            raise ValueError("cannot merge a vendor into itself")
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
                          migrated_detail_ids=[d.id for d in moved])

    def unmerge(self, token: MergeToken) -> None:
        """REVISED (F-36). The original removed keeper aliases BY NAME, which
        destroys a pre-existing alias that happened to share the absorbed
        vendor's name. Remove by identity, and hand the migrated history back."""
        self._vendors[token.absorbed_snapshot.id] = token.absorbed_snapshot
        keeper = self._vendors[token.keeper]
        keeper.aliases = [a for a in keeper.aliases if a.id != token.alias_id]
        migrated = set(token.migrated_detail_ids)
        keeper.payment_history = [d for d in keeper.payment_history
                                  if d.id not in migrated]

    def record_payment_detail(self, vendor_id: str, detail: PaymentDetail,
                              confirmed_by: str,
                              attestation: Attestation | None = None) -> None:
        """Append-only. A CHANGED detail requires a named human confirmer, and
        the confirmation is STORED (F-35) — spec §8 item 5 renders it. When an
        `attestation` is given its evidence is stored with it (LS-10).

        Comparison is on the HMAC, not on four digits, and against the last
        CONFIRMED entry: an unconfirmed entry is never a baseline."""
        vendor = self._vendors[vendor_id]
        confirmed = [d for d in vendor.payment_history if d.is_active]
        current = confirmed[-1] if confirmed else None
        changed = current is not None and current.account_hmac != detail.account_hmac
        if changed and not confirmed_by:
            raise BankDetailChangeError(
                "a bank-detail change requires human confirmation; this system "
                "never auto-approves and never auto-updates a payment record")
        update: dict[str, object] = {
            "confirmed_by": confirmed_by or None,
            "confirmed_at": datetime.now(UTC) if confirmed_by else None}
        if attestation is not None:
            update.update(confirmation_channel=attestation.channel,
                          confirmation_contact=attestation.contact,
                          confirmation_note=attestation.note)
        vendor.payment_history.append(detail.model_copy(update=update))

    def find_payment_detail(self, source_document: str) -> tuple[str, PaymentDetail] | None:
        """The (vendor_id, confirmed detail) that one document's bank-change
        confirmation recorded. Undo's lookup, by identity not by value."""
        for vendor in self._vendors.values():
            for detail in vendor.payment_history:
                if detail.source_document == source_document and detail.is_active:
                    return vendor.id, detail
        return None

    def undo_payment_detail(self, vendor_id: str, detail_id: str, reversed_by: str) -> None:
        """Reverse one confirmation by identity (F-36's lesson). The entry is KEPT
        — attestation, confirmer and all — and stamped with who reversed it and
        when: a wrong confirmation is the case an audit needs the evidence for.
        It stops being the baseline (`is_active`), so the previous confirmed
        entry is the baseline again. A no-op if the vendor is gone."""
        if not reversed_by.strip():
            raise ValueError("undoing a bank-detail confirmation requires reversed_by")
        vendor = self._vendors.get(vendor_id)
        if vendor is None:
            return
        now = datetime.now(UTC)
        vendor.payment_history = [
            d.model_copy(update={"reversed_by": reversed_by, "reversed_at": now})
            if d.id == detail_id and d.reversed_at is None else d
            for d in vendor.payment_history]

    def seen_before(self, vendor_id: str, detail: PaymentDetail) -> bool:
        """Has this account EVER been on file for this vendor? Change-then-
        change-back is a documented redirection pattern and comparing only
        against payment_history[-1] cannot see it (F-35). A reversed entry was
        on file, so it counts however recently it was reversed."""
        vendor = self._vendors[vendor_id]
        return (any(d.account_hmac == detail.account_hmac
                    for d in vendor.payment_history[:-1])
                or any(d.reversed_at is not None and d.account_hmac == detail.account_hmac
                       for d in vendor.payment_history))
