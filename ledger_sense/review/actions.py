"""`ReviewAction`: what a reviewer did, in the shape calibration labelling needs.

Deliberately not a restatement of `ledger_sense.api.review`'s internals (the
`findings`/`claims` rows, the `EditBody`/`RejectBody` request models). This is
the narrow fact `labels_from_action` (route/labels.py) needs to turn a
reviewer's accept/edit/reject into a `Label`: which kind of action, on which
field, judging which prior claim, at what predicted confidence, by whom, when.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ReviewAction:
    kind: str
    field: str
    claim_id: str
    predicted_confidence: float
    actor: str
    at: datetime
