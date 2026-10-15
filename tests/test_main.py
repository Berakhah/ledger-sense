"""M10: the deployment's own startup probe. A misconfigured KEK or an
unwritable blob root must fail loudly at boot (`_probe_blob_store()`, called
from `main()` after `_migrate()`), not silently on the first client upload."""

import json
import pathlib

import pytest
from cryptography.fernet import Fernet

from ledger_sense.__main__ import _extract, _probe_blob_store
from ledger_sense.corpus.generate import generate
from tests.run.conftest import RunDb


def test_a_valid_kek_and_a_writable_root_probe_clean(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEDGER_SENSE_KEK", Fernet.generate_key().decode())
    monkeypatch.setenv("LEDGER_SENSE_BLOB_ROOT", str(tmp_path))

    _probe_blob_store()

    assert list(tmp_path.iterdir()) == []


def test_the_placeholder_kek_from_env_example_fails_the_probe(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """.env.example ships LEDGER_SENSE_KEK=changeme; a deployment that never
    replaces it must not pass this probe."""
    monkeypatch.setenv("LEDGER_SENSE_KEK", "changeme")
    monkeypatch.setenv("LEDGER_SENSE_BLOB_ROOT", str(tmp_path))

    with pytest.raises(ValueError, match="Fernet key"):
        _probe_blob_store()

    assert list(tmp_path.iterdir()) == []


def test_extract_pending_runs_every_runnable_document_and_prints_one_line_each(
    db: RunDb, capsys: pytest.CaptureFixture[str],
) -> None:
    ids = [db.store_pdf(generate(seed=s, error="clean")[2]) for s in (1, 2)]
    assert _extract(["ledger_sense", "extract", "--pending"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert {line["document_id"] for line in lines} == set(ids)
    assert all(line["status"] == "extracted" for line in lines)


def test_extract_pending_honours_limit(db: RunDb, capsys: pytest.CaptureFixture[str]) -> None:
    for s in (1, 2, 3):
        db.store_pdf(generate(seed=s, error="clean")[2])
    assert _extract(["ledger_sense", "extract", "--pending", "--limit", "2"]) == 0
    assert len(capsys.readouterr().out.strip().splitlines()) == 2


def test_extract_document_runs_exactly_one(db: RunDb, capsys: pytest.CaptureFixture[str]) -> None:
    document_id = db.store_pdf(generate(seed=1, error="clean")[2])
    other = db.store_pdf(generate(seed=2, error="clean")[2])
    assert _extract(["ledger_sense", "extract", "--document", document_id]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "extracted"
    assert db.document(other).status == "received"


def test_extract_usage_is_still_an_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert _extract(["ledger_sense", "extract"]) == 2
    assert "usage" in capsys.readouterr().err


def test_an_unset_payment_hmac_key_fails_the_boot_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """domain/vendor.py's payment_hmac raised a bare KeyError mid-request."""
    from ledger_sense.__main__ import _probe_payment_hmac_key

    monkeypatch.delenv("LEDGER_SENSE_PAYMENT_HMAC_KEY", raising=False)
    with pytest.raises(RuntimeError, match="LEDGER_SENSE_PAYMENT_HMAC_KEY"):
        _probe_payment_hmac_key()


def test_a_set_payment_hmac_key_passes_the_boot_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ledger_sense.__main__ import _probe_payment_hmac_key

    monkeypatch.setenv("LEDGER_SENSE_PAYMENT_HMAC_KEY", "k" * 32)
    _probe_payment_hmac_key()
