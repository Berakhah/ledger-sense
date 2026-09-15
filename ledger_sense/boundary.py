"""The data boundary, as types (`00` R4, `02` Never #1).

A leaf module: stdlib only, so the door (ingest/intake.py), the seam
(gateway.py) and the store (store/documents.py) can all import it without a
cycle. `bok_core.boundary` will carry the same names; when it ships, this
module is where the import is redirected, and nothing that imports from here
changes.

`Sensitivity` is `00`'s vocabulary verbatim, including PUBLIC — nothing in
Ledger Sense produces PUBLIC (no `Origin` maps to it), but the column CHECK
carries the full vocabulary so a label written by bok-core is storable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = ["Labelled", "Origin", "Sensitivity", "SensitivityViolation"]


class Sensitivity(str, Enum):
    """What a payload may be allowed to reach. `00` R4."""

    PUBLIC = "PUBLIC"
    SYNTHETIC = "SYNTHETIC"
    REDACTED = "REDACTED"
    CLIENT_CONFIDENTIAL = "CLIENT_CONFIDENTIAL"


class Origin(str, Enum):
    """Where the bytes came from — the fact the label is derived from."""

    UPLOAD = "UPLOAD"  # a client's file, as received
    CORPUS = "CORPUS"  # our synthetic generator
    REDACTED_UPLOAD = "REDACTED_UPLOAD"  # a client's file after a verified redaction pass


class SensitivityViolation(Exception):
    """A payload was offered somewhere its label forbids. Never caught and
    retried; the caller was wrong to ask."""


@dataclass(frozen=True)
class Labelled[U]:
    """A payload that cannot be separated from its label.

    Constructed in exactly two places — `ingest.intake.accept_document`
    (where the file is opened) and `store.documents.load_document` (where a
    stored label is rebuilt) — and a test enforces that.
    """

    payload: U
    sensitivity: Sensitivity
    origin: Origin
    provenance: str
