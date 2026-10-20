"""Reviewer actions become calibration labels.

The review queue is the training signal.  This is the whole reason the product
improves with use at zero inference cost.
"""
from __future__ import annotations

import re

from ledger_sense.review.actions import ReviewAction
from ledger_sense.route.calibration import Label, LabelBasis

_ROW_INDEX = re.compile(r"\[\d+\]")


def field_type_of(field: str) -> str:
    """A calibration curve belongs to a field TYPE, not to row 3 of one
    document: 'line[3].work_completed' -> 'line[].work_completed'. Labels and
    routing lookups must both go through this, or they never meet."""
    return _ROW_INDEX.sub("[]", field)


_CORRECTNESS: dict[str, bool | None] = {
    "accept": True,
    "edit": False,
    "reject": None,   # unusable document, not a wrong field — see the test's docstring
}


def labels_from_action(action: ReviewAction, *, basis: LabelBasis) -> list[Label]:
    if action.kind not in _CORRECTNESS:
        raise ValueError(f"unknown review action kind: {action.kind!r}")
    correct = _CORRECTNESS[action.kind]
    if correct is None:
        return []
    return [Label(field_type=field_type_of(action.field),
                  predicted_confidence=action.predicted_confidence,
                  was_correct=correct,
                  observed_at=action.at,
                  source_claim_id=action.claim_id,
                  basis=basis)]
