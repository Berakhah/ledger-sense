"""Identify the producing software so a learned profile can be reused.

Keys on geometry, never on content. Two pay applications from the same
accounting package share a key regardless of vendor, amounts or period.
"""

import hashlib
import re
from typing import NamedTuple

from ledger_sense.extract.document import ParsedPDF, parse

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


class ProfileKey(NamedTuple):
    producer: str
    column_hash: str

    def as_str(self) -> str:
        return f"{self.producer}:{self.column_hash}"


def document_hash(pdf: bytes) -> str:
    """Content address for the raw document. F-16: Phase 5's idempotency key took
    this as a parameter and nothing produced it."""
    return hashlib.sha256(pdf).hexdigest()[:32]


def _producer(doc: ParsedPDF) -> str:
    raw = doc.metadata.get("Producer") or "unknown"
    return _UNSAFE.sub("-", raw)[:64]


def _column_hash(doc: ParsedPDF) -> str:
    """Hash the distinct x-offsets on the busiest row band. Rounded to 2pt so
    sub-point rendering jitter does not fork the profile."""
    bands: dict[float, list[float]] = {}
    for word in doc.words:
        if word.page != 0:
            continue
        bands.setdefault(round(word.bbox.top / 4.0) * 4.0, []).append(word.bbox.x0)
    if not bands:
        return hashlib.sha256(b"").hexdigest()[:16]
    busiest = max(bands.values(), key=len)
    offsets = sorted({round(x / 2.0) * 2.0 for x in busiest})
    payload = ",".join(f"{x:.1f}" for x in offsets).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def fingerprint(pdf: bytes) -> ProfileKey:
    """Thin wrapper over the shared `parse()` (F-21): the document is opened
    exactly once, not once per helper."""
    doc = parse(pdf)
    return ProfileKey(producer=_producer(doc), column_hash=_column_hash(doc))
