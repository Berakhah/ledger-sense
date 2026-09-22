"""tests/route/test_deps.py

Mirrors tests/writeback/test_idempotency.py's
test_ledger_from_environment_raises_when_unset: a store built at a guessed
path is a store the calibration job run against a different path will never
see, so an unset directory is a loud refusal, not a default.
"""
import pytest


def test_calibration_store_from_environment_builds_a_store_under_the_configured_dir(
        tmp_path, monkeypatch) -> None:
    from ledger_sense.route.deps import calibration_store_from_environment
    monkeypatch.setenv("LEDGER_SENSE_CALIBRATION_DIR", str(tmp_path))
    store = calibration_store_from_environment()
    assert store.count("some_field") == {}
    assert (tmp_path / "calibration.db").exists()


def test_calibration_store_from_environment_raises_when_unset(monkeypatch) -> None:
    from ledger_sense.route.deps import calibration_store_from_environment
    monkeypatch.delenv("LEDGER_SENSE_CALIBRATION_DIR", raising=False)
    with pytest.raises(RuntimeError, match="LEDGER_SENSE_CALIBRATION_DIR"):
        calibration_store_from_environment()
