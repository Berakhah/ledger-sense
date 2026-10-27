"""Synthetic ground truth for I3: every injected bank change is caught, every
clean control is not (Testing bar, Never-rule 6)."""

import pytest

from ledger_sense.corpus.errors import generate_bank_change_history
from ledger_sense.corpus.remit import remit_for
from ledger_sense.run.document import run_document
from tests.run.conftest import RunDb
from tests.run.test_payment_details import _confirm_baseline, _i3


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6, 7, 8])
def test_the_injected_bank_change_is_caught_and_the_clean_control_is_not(
        db: RunDb, seed: int) -> None:
    (_c1, app, original_pdf), (_c2, _app2, changed_pdf) = generate_bank_change_history(seed)
    _confirm_baseline(db, app, remit_for(seed, "base"))

    control = db.store_pdf(original_pdf)
    run_document(db.engine, control)
    assert _i3(db, control) == [], "clean control tripped I3"

    injected = db.store_pdf(changed_pdf)
    run_document(db.engine, injected)
    (finding,) = _i3(db, injected)
    assert finding.state == "open"
