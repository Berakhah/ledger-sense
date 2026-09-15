"""M10: the deployment's own startup probe. A misconfigured KEK or an
unwritable blob root must fail loudly at boot (`_probe_blob_store()`, called
from `main()` after `_migrate()`), not silently on the first client upload."""

import pathlib

import pytest
from cryptography.fernet import Fernet

from ledger_sense.__main__ import _probe_blob_store


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
