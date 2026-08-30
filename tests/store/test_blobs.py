import pytest
from cryptography.fernet import Fernet

from ledger_sense.store.blobs import BlobStore, kek_from_environment


def _store(tmp_path):
    return BlobStore(root=str(tmp_path), kek=Fernet.generate_key())


def test_a_stored_document_round_trips(tmp_path):
    store = _store(tmp_path)
    assert store.get("tenant-a", store.put("tenant-a", b"%PDF-1.4 x")) == b"%PDF-1.4 x"


def test_the_payload_is_not_readable_on_disk(tmp_path):
    store = _store(tmp_path)
    store.put("tenant-a", b"%PDF-1.4 ACCOUNT 123456789")
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert b"123456789" not in path.read_bytes()


def test_another_tenant_cannot_read_the_blob(tmp_path):
    store = _store(tmp_path)
    assert store.get("tenant-b", store.put("tenant-a", b"%PDF")) is None


def test_each_blob_gets_its_own_data_key(tmp_path):
    store = _store(tmp_path)
    a = store.put("tenant-a", b"identical bytes")
    b = store.put("tenant-a", b"identical bytes")
    assert store.raw(a) != store.raw(b), "identical plaintext gave identical ciphertext"


def test_delete_removes_the_ciphertext_and_the_wrapped_key(tmp_path):
    store = _store(tmp_path)
    blob_id = store.put("tenant-a", b"%PDF")
    store.delete("tenant-a", blob_id)
    assert store.get("tenant-a", blob_id) is None
    assert store.raw(blob_id) is None


# --- added here, not given in the brief: the KEK the deployment actually uses ---


def test_a_missing_kek_refuses_rather_than_defaulting(monkeypatch):
    """No fallback key, and no writing documents in the clear: an unset
    LEDGER_SENSE_KEK has to stop the deployment, not degrade it."""
    monkeypatch.delenv("LEDGER_SENSE_KEK", raising=False)
    with pytest.raises(RuntimeError, match="LEDGER_SENSE_KEK"):
        kek_from_environment()


def test_the_kek_from_the_environment_opens_the_store(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_SENSE_KEK", Fernet.generate_key().decode())
    store = BlobStore(root=str(tmp_path), kek=kek_from_environment())
    assert store.get("t", store.put("t", b"%PDF")) == b"%PDF"


def test_a_blob_id_cannot_escape_the_store_root(tmp_path):
    """The id reaches this module from a database row, and one day from a
    URL. It is validated before it becomes a path component."""
    store = _store(tmp_path)
    assert store.raw("../../.env") is None
    assert store.get("tenant-a", "../../.env") is None
