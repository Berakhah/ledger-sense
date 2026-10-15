"""`python -m ledger_sense erase` — the operator entry point for a client erasure request."""

import json
import pathlib

import pytest
from cryptography.fernet import Fernet

from ledger_sense.__main__ import _erase
from tests.store.conftest import EXPIRED_DOCUMENT_ID, SeededDocuments


@pytest.fixture(autouse=True)
def _blob_env(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SENSE_KEK", Fernet.generate_key().decode())
    monkeypatch.setenv("LEDGER_SENSE_BLOB_ROOT", str(tmp_path))


def _argv(seeded: SeededDocuments, *extra: str) -> list[str]:
    return ["ledger_sense", "erase", "--tenant", seeded.tenant_id,
            "--actor", "operator:ada", "--reason", "client request", *extra]


def test_default_is_a_dry_run(
    seeded_documents: SeededDocuments, capsys: pytest.CaptureFixture[str],
) -> None:
    assert _erase(_argv(seeded_documents)) == 0

    out = json.loads(capsys.readouterr().out)
    assert out["executed"] is False
    assert out["documents_purged"] == 1
    assert seeded_documents.documents_for(EXPIRED_DOCUMENT_ID) != []


def test_execute_deletes_and_reports(
    seeded_documents: SeededDocuments, capsys: pytest.CaptureFixture[str],
) -> None:
    assert _erase(_argv(seeded_documents, "--execute")) == 0

    out = json.loads(capsys.readouterr().out)
    assert out["executed"] is True
    assert seeded_documents.documents_for(EXPIRED_DOCUMENT_ID) == []
    (row,) = seeded_documents.audit_for(EXPIRED_DOCUMENT_ID)
    assert row.actor == "operator:ada"


def test_document_flag_scopes_the_erasure(seeded_documents: SeededDocuments) -> None:
    seeded_documents.add_document("doc-live", age_days=1)

    assert _erase(_argv(seeded_documents, "--document", "doc-live", "--execute")) == 0

    assert seeded_documents.documents_for("doc-live") == []
    assert seeded_documents.documents_for(EXPIRED_DOCUMENT_ID) != []


@pytest.mark.parametrize("missing", ["--tenant", "--actor", "--reason"])
def test_actor_tenant_and_reason_are_required(
    seeded_documents: SeededDocuments, capsys: pytest.CaptureFixture[str], missing: str,
) -> None:
    argv = _argv(seeded_documents)
    i = argv.index(missing)
    del argv[i:i + 2]

    assert _erase(argv) == 2
    assert "usage" in capsys.readouterr().err


def test_an_unknown_tenant_exits_nonzero_with_the_reason(
    seeded_documents: SeededDocuments, capsys: pytest.CaptureFixture[str],
) -> None:
    argv = _argv(seeded_documents)
    argv[argv.index("--tenant") + 1] = "nobody"

    assert _erase(argv) == 1
    assert "no such tenant" in capsys.readouterr().err
