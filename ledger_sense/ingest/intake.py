"""Document intake — the door (§2) and the only caller of `parse_within_limits` (Rule 2).

The label attaches where the file is OPENED, and it is derived from where the
bytes came from. `accept_document` has no sensitivity parameter: a client
upload cannot be called SYNTHETIC because there is nothing to type it into.
If the label attached late, the leak would happen early.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ledger_sense.boundary import Labelled, Origin, Sensitivity, SensitivityViolation
from ledger_sense.ingest.limits import DEFAULT_LIMITS, Limits, ParseResult, parse_within_limits

__all__ = ["RedactionVerdict", "accept_document", "intake"]


@dataclass(frozen=True)
class RedactionVerdict:
    """The outcome of a redaction verification pass.

    No verifier ships with Ledger Sense: the brief accepts bok-core's pass and
    nothing else. Until it exists, every real upload is CLIENT_CONFIDENTIAL —
    the honest state, and the product works that way (more routed to review).
    """

    verified: bool
    verifier: str
    detail: str = ""


# Origin → label. Exhaustive over Origin; tests/boundary/test_intake.py checks.
# PUBLIC is deliberately absent: nothing in this product produces it.
_LABEL_OF: Final[dict[Origin, Sensitivity]] = {
    Origin.UPLOAD: Sensitivity.CLIENT_CONFIDENTIAL,
    Origin.CORPUS: Sensitivity.SYNTHETIC,
    Origin.REDACTED_UPLOAD: Sensitivity.REDACTED,
}


def accept_document(
    payload: bytes,
    *,
    origin: Origin,
    provenance: str,
    redaction: RedactionVerdict | None = None,
) -> Labelled[bytes]:
    """Label `payload` from its `origin`. The single entry point for every
    externally-supplied document; one of the two places a Labelled is built."""
    # Coerce, as gateway._check_label does for Sensitivity: a raw "UPLOAD"
    # string would otherwise hash into _LABEL_OF and be stored un-coerced.
    origin = Origin(origin)
    sensitivity = _LABEL_OF[origin]
    if sensitivity is Sensitivity.REDACTED:
        if redaction is None:
            raise SensitivityViolation(
                "REDACTED_UPLOAD requires a passing redaction verification pass; "
                "none was supplied")
        if not redaction.verified:
            raise SensitivityViolation(
                "REDACTED_UPLOAD requires a passing redaction verification pass; "
                f"{redaction.verifier} did not verify it: {redaction.detail or 'no detail'}")
    elif redaction is not None:
        raise SensitivityViolation(
            f"a redaction verdict does not change the label of origin {origin.value}; "
            "a verified redaction enters as REDACTED_UPLOAD")
    return Labelled(payload=payload, sensitivity=sensitivity, origin=origin,
                    provenance=provenance)


def intake(payload: bytes, limits: Limits | None = None) -> ParseResult:
    """Parse an uploaded document within resource limits."""
    return parse_within_limits(payload, limits if limits is not None else DEFAULT_LIMITS)
