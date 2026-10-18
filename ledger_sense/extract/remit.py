"""Remit-to bank details by label anchor plus geometry (LS-10 / I3).

Same discipline as `anchors.py`: an absent label means an absent field, a
repeated label is refused rather than resolved, and a label with no value
beside it is a recorded problem, never a guess.

The full account and routing numbers are returned ONLY to the caller that
reduces them to a last-four plus HMAC. They are `repr=False` so a stray log of a
result cannot leak them. extract/ must not import ledger_sense.corpus, so the
label strings are this module's own copy."""

import re
from dataclasses import dataclass, field

from ledger_sense.extract.anchors import find_labels, value_after_label
from ledger_sense.extract.textlayer import BBox, Word

_ACCOUNT_LABEL = "Account Number"
_ROUTING_LABEL = "Routing Number"
_ACCOUNT = re.compile(r"\d{4,17}")
_ROUTING = re.compile(r"\d{9}")


@dataclass(frozen=True)
class RemitHit:
    account: str = field(repr=False)
    routing: str = field(repr=False)
    bbox: BBox
    """Where the ACCOUNT value sits — the located box the I3 finding points at."""
    page: int


@dataclass(frozen=True)
class RemitResult:
    hit: RemitHit | None
    problem: str | None


def extract_remit(words: list[Word]) -> RemitResult:
    account_labels = find_labels(words, _ACCOUNT_LABEL)
    routing_labels = find_labels(words, _ROUTING_LABEL)
    if not account_labels and not routing_labels:
        return RemitResult(None, None)  # no bank block: nothing to compare
    if len(account_labels) != 1 or len(routing_labels) != 1:
        return RemitResult(None, "bank-detail label missing or repeated; refusing to guess")
    account = value_after_label(
        words, account_labels[0], lambda w: bool(_ACCOUNT.fullmatch(w.text)))
    routing = value_after_label(
        words, routing_labels[0], lambda w: bool(_ROUTING.fullmatch(w.text)))
    if account is None or routing is None:
        return RemitResult(None, "bank-detail value not found beside its label")
    return RemitResult(RemitHit(account.text, routing.text, account.bbox, account.page), None)
