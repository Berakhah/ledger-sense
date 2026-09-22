"""Where the process-wide `CalibrationStore` lives.

No caller before this task ever constructed a `CalibrationStore` outside a
test (`grep CalibrationStore(` across `ledger_sense/` before this change
turns up only `tests/route/test_router.py` and `tests/route/test_calibration.py`).
This module is the first production wiring, and it follows the same shape as
`ledger_sense.writeback.idempotency.ledger_from_environment`: a directory
named by an environment variable, built where it is used (the review-queue
API, `ledger_sense/api/review.py`) rather than as a hidden module-level
singleton, and a loud refusal rather than a guessed path when the variable is
unset.
"""
from __future__ import annotations

import os
from pathlib import Path

from ledger_sense.route.calibration import CalibrationStore

_CALIBRATION_DIR_ENV = "LEDGER_SENSE_CALIBRATION_DIR"


def calibration_store_from_environment() -> CalibrationStore:
    """The deployment's calibration label/curve store, at
    `<LEDGER_SENSE_CALIBRATION_DIR>/calibration.db`.

    Raises if the directory is unset: labels written to a guessed path are
    labels the calibration job run against a different path will never see.
    """
    root = os.environ.get(_CALIBRATION_DIR_ENV)
    if not root:
        raise RuntimeError(
            f"{_CALIBRATION_DIR_ENV} is unset: refusing to guess where the "
            f"calibration store lives.")
    os.makedirs(root, exist_ok=True)
    return CalibrationStore(Path(root) / "calibration.db")
