"""`python -m ledger_sense purge` — what the compose `retention` service runs."""

import json
import pathlib

import pytest
from cryptography.fernet import Fernet

from ledger_sense.__main__ import _purge
from tests.store.conftest import SeededDocuments


def test_purge_command_purges_expired_documents_and_reports(
    seeded_documents: SeededDocuments, tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEDGER_SENSE_KEK", Fernet.generate_key().decode())
    monkeypatch.setenv("LEDGER_SENSE_BLOB_ROOT", str(tmp_path))

    assert _purge(["ledger_sense", "purge"]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["documents_purged"] == 1
    assert seeded_documents.documents_for("doc-expired") == []


def test_purge_usage_is_an_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert _purge(["ledger_sense", "purge", "--bogus"]) == 2
    assert "usage" in capsys.readouterr().err
