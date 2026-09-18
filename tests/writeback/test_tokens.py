"""TokenStore: OAuth tokens encrypted at rest, with rotation.

Token compromise means access to a client's financials, so the tests assert
both the round trip and the fact that raw ciphertext, not plaintext, sits on
disk.
"""

from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet

from ledger_sense.writeback.tokens import TokenStore


@pytest.fixture()
def store(tmp_path):
    s = TokenStore(str(tmp_path / "tokens.db"), fernet_key=Fernet.generate_key())
    yield s
    s.close()


def test_tokens_round_trip(store):
    expires = datetime.now(UTC) + timedelta(hours=1)
    store.save("realm-1", access="at-1", refresh="rt-1", expires_at=expires)
    loaded = store.load("realm-1")
    assert loaded.access == "at-1" and loaded.refresh == "rt-1"


def test_tokens_are_encrypted_at_rest(store, tmp_path):
    """Token compromise means access to a client's financials."""
    store.save("realm-1", access="at-secret", refresh="rt-secret",
               expires_at=datetime.now(UTC))
    store.close()
    raw = (tmp_path / "tokens.db").read_bytes()
    assert b"at-secret" not in raw
    assert b"rt-secret" not in raw


def test_rotation_replaces_both_tokens(store):
    now = datetime.now(UTC)
    store.save("realm-1", access="at-1", refresh="rt-1", expires_at=now)
    store.rotate("realm-1", access="at-2", refresh="rt-2",
                 expires_at=now + timedelta(hours=1))
    loaded = store.load("realm-1")
    assert loaded.access == "at-2" and loaded.refresh == "rt-2"


def test_an_unknown_realm_loads_as_none(store):
    assert store.load("realm-unknown") is None


def test_different_realms_do_not_collide(store):
    now = datetime.now(UTC)
    store.save("realm-1", access="at-1", refresh="rt-1", expires_at=now)
    store.save("realm-2", access="at-2", refresh="rt-2", expires_at=now)
    assert store.load("realm-1").access == "at-1"
    assert store.load("realm-2").access == "at-2"
