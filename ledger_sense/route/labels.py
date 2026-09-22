"""Reviewer actions become calibration labels.

The review queue is the training signal.  This is the whole reason the product
improves with use at zero inference cost.
"""
from __future__ import annotations

from ledger_sense.review.actions import ReviewAction
from ledger_sense.route.calibration import Basis, Label

_CORRECTNESS: dict[str, bool | None] = {
    "accept": True,
    "edit": False,
    "reject": None,   # unusable document, not a wrong field — see the test's docstring
}


def labels_from_action(action: ReviewAction, *, basis: Basis) -> list[Label]:
    if action.kind not in _CORRECTNESS:
        raise ValueError(f"unknown review action kind: {action.kind!r}")
    correct = _CORRECTNESS[action.kind]
    if correct is None:
        return []
    return [Label(field_type=action.field,
                  predicted_confidence=action.predicted_confidence,
                  was_correct=correct,
                  observed_at=action.at,
                  source_claim_id=action.claim_id,
                  basis=basis)]
