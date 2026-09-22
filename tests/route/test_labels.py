"""tests/route/test_labels.py

Reviewer actions (accept/edit/reject) become calibration labels: accept says
the extraction was right, edit says it was wrong, reject says nothing about
any specific value (F-46's follow-up; see the docstring on
test_reject_produces_no_label_at_all below).
"""
from datetime import UTC, datetime

import pytest

from ledger_sense.review.actions import ReviewAction
from ledger_sense.route.labels import labels_from_action


def _action(kind: str, field: str = "retainage_rate", confidence: float = 0.8) -> ReviewAction:
    return ReviewAction(kind=kind, field=field, claim_id="c1",
                        predicted_confidence=confidence,
                        actor="reviewer@example.test",
                        at=datetime(2026, 8, 1, tzinfo=UTC))


def test_accept_produces_a_correct_label() -> None:
    labels = labels_from_action(_action("accept"), basis="field")
    assert len(labels) == 1
    assert labels[0].was_correct is True
    assert labels[0].basis == "field"


def test_edit_produces_an_incorrect_label() -> None:
    labels = labels_from_action(_action("edit"), basis="field")
    assert len(labels) == 1
    assert labels[0].was_correct is False


def test_reject_produces_no_label_at_all() -> None:
    """A rejection says the extraction was unusable, not that a specific value was wrong.

    Scoring it as incorrect would poison the curve with document-level failures that
    have nothing to do with this field's confidence.
    """
    assert labels_from_action(_action("reject"), basis="field") == []


def test_the_label_carries_the_predicted_confidence_not_the_corrected_value() -> None:
    labels = labels_from_action(_action("edit", confidence=0.42), basis="field")
    assert labels[0].predicted_confidence == 0.42


def test_the_label_carries_the_source_claim_id() -> None:
    labels = labels_from_action(_action("accept"), basis="field")
    assert labels[0].source_claim_id == "c1"


def test_a_synthetic_review_session_produces_synthetic_labels() -> None:
    labels = labels_from_action(_action("accept"), basis="synthetic")
    assert labels[0].basis == "synthetic"


def test_an_unknown_action_kind_raises_rather_than_being_ignored() -> None:
    with pytest.raises(ValueError):
        labels_from_action(_action("shrug"), basis="field")
