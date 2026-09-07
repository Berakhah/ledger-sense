"""I1 — duplicate detection across FULL history, by three fingerprints."""

from datetime import date, timedelta
from typing import NamedTuple

from pydantic import BaseModel

from ledger_sense.domain.money import Money

DATE_WINDOW = timedelta(days=30)


class DocumentFingerprint(BaseModel):
    # REVISED (pre-flight scan, 2026-09-07): `vendor_id`, not `vendor` — see
    # the note below this code block and the F-37 fix it describes. The
    # field must hold the RESOLVED canonical vendor id from I2, not the raw
    # extracted name; `find_duplicates` below already compares
    # `candidate.vendor_id == prior.vendor_id`, and the tests added under
    # "Add to the test module" already construct `_fp(vendor_id=...)`.
    vendor_id: str
    vendor: str | None = None    # display only; matching uses vendor_id
    amount: Money
    doc_date: date
    invoice_number: str | None
    line_hash: str


class DuplicateMatch(NamedTuple):
    reason: str
    against: DocumentFingerprint


def find_duplicates(candidate: DocumentFingerprint,
                    history: list[DocumentFingerprint]) -> list[DuplicateMatch]:
    """REVISED (F-37). Two defects.

    INVOICE NUMBER WAS NOT SCOPED TO THE VENDOR. Two vendors both numbering from
    INV-1001 produced a permanent false duplicate — and a duplicate finding that
    is obviously wrong is how a reviewer learns to dismiss duplicate findings.
    `line_hash` had the same defect.

    `line_hash` ALSO HAD NO PRODUCER anywhere in the six plan documents. It is
    now emitted by extract/assemble.py::line_hash — order-independent, over
    (item_no, description, total).

    Persistence and indexing (F-38, Phase 0 Task P7): "across full history"
    cannot be a Python loop over a list. The signature below stays for the unit
    tests; the production caller passes an indexed query result, not the world.
    """
    matches: list[DuplicateMatch] = []
    for prior in history:
        same_vendor = candidate.vendor_id == prior.vendor_id
        if (same_vendor and candidate.invoice_number is not None
                and candidate.invoice_number == prior.invoice_number):
            matches.append(DuplicateMatch("same invoice number", prior))
            continue
        if (same_vendor and candidate.amount == prior.amount
                and abs(candidate.doc_date - prior.doc_date) <= DATE_WINDOW):
            matches.append(DuplicateMatch("vendor+amount within date window", prior))
            continue
        if same_vendor and candidate.line_hash == prior.line_hash:
            matches.append(DuplicateMatch("identical line-item fingerprint", prior))
    return matches
