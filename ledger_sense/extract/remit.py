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

from ledger_sense.extract.anchors import SAME_BAND_PT, find_labels, value_after_label_merged
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
    account = _read_number(words, account_labels[0], _ACCOUNT)
    routing = _read_number(words, routing_labels[0], _ROUTING)
    if account is _AMBIGUOUS or routing is _AMBIGUOUS:
        return RemitResult(None, "bank-detail value ambiguous; refusing to guess")
    if account is None or routing is None:
        return RemitResult(None, "bank-detail value not found beside its label")
    assert isinstance(account, Word) and isinstance(routing, Word)
    return RemitResult(RemitHit(_normalise(account.text), _normalise(routing.text),
                                account.bbox, account.page), None)


def _normalise(text: str) -> str:
    return text.replace(" ", "").replace("-", "")


_AMBIGUOUS = object()
_DIGIT_GROUP = re.compile(r"[\d-]*\d[\d-]*")


def _read_number(words: list[Word], label: list[Word], shape: re.Pattern[str]
                 ) -> Word | None | object:
    """Every digit/hyphen group on the label's band, joined: pdf text layers
    split a spaced number into words, and keeping only the first group would
    let two different accounts that share it hash identically. A number-shaped
    token that is not part of one clean number is ambiguous, never guessed."""
    value = value_after_label_merged(words, label, lambda w: bool(_DIGIT_GROUP.fullmatch(w.text)))
    if value is None:
        return None
    label_right = max(w.bbox.x1 for w in label)
    label_top = min(w.bbox.top for w in label)
    for w in words:
        if (w.page == value.page and w.bbox.x0 >= label_right
                and abs(w.bbox.top - label_top) <= SAME_BAND_PT
                and any(c.isdigit() for c in w.text)
                and not (w.bbox.x0 >= value.bbox.x0 and w.bbox.x1 <= value.bbox.x1)):
            return _AMBIGUOUS
    if not shape.fullmatch(_normalise(value.text)):
        return _AMBIGUOUS
    return value
