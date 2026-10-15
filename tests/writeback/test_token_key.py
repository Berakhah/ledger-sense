"""OAuth tokens get their own key, not the document KEK.

One key protecting both documents and accounting tokens means its loss or
exposure compromises both, and a KEK rotation (which rewrites one short wrapped
key per blob) would silently have to cover a store it has no business knowing
about. `LEDGER_SENSE_TOKEN_KEY` separates them; it must differ from the KEK, or
the separation is a label rather than a fact.
"""

import base64
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken

from ledger_sense.writeback import qbo
from ledger_sense.writeback.idempotency import IdempotencyLedger
from ledger_sense.writeback.qbo import qbo_adapter_from_environment
from ledger_sense.writeback.tokens import TokenStore, token_key_from_environment


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> tuple[bytes, bytes]:
    kek, token_key = Fernet.generate_key(), Fernet.generate_key()
    monkeypatch.setenv("LEDGER_SENSE_KEK", kek.decode())
    monkeypatch.setenv("LEDGER_SENSE_TOKEN_KEY", token_key.decode())
    return kek, token_key


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[IdempotencyLedger]:
    led = IdempotencyLedger(str(tmp_path / "writes.db"))
    yield led
    led.close()


def test_the_token_key_comes_from_its_own_variable(keys: tuple[bytes, bytes]) -> None:
    assert token_key_from_environment() == keys[1]


def test_an_unset_token_key_refuses_rather_than_falling_back_to_the_kek(
    keys: tuple[bytes, bytes], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LEDGER_SENSE_TOKEN_KEY")
    with pytest.raises(RuntimeError, match="LEDGER_SENSE_TOKEN_KEY"):
        token_key_from_environment()


def test_the_env_example_placeholder_is_not_a_valid_key(
    keys: tuple[bytes, bytes], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEDGER_SENSE_TOKEN_KEY", "changeme")
    with pytest.raises(RuntimeError, match="not a valid Fernet key"):
        token_key_from_environment()


def test_a_token_key_equal_to_the_kek_is_refused(
    keys: tuple[bytes, bytes], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEDGER_SENSE_TOKEN_KEY", keys[0].decode())
    with pytest.raises(RuntimeError, match="must differ"):
        token_key_from_environment()


@pytest.mark.parametrize("variant", ["{k}\n", "{k} ", " {k}", "{plus}"])
def test_a_re_spelling_of_the_kek_is_still_the_kek(
    keys: tuple[bytes, bytes], monkeypatch: pytest.MonkeyPatch, variant: str,
) -> None:
    """Fernet's decoder ignores whitespace and accepts +/ for -_, so a raw string
    comparison passes a pasted KEK that decodes to the identical key."""
    # 0xfb 0xff encodes to "-_" in the url-safe alphabet, so the +/ spelling differs.
    kek = base64.urlsafe_b64encode(b"\xfb\xff" * 16).decode()
    assert "-" in kek and "_" in kek
    monkeypatch.setenv("LEDGER_SENSE_KEK", kek)
    spelled = variant.format(k=kek, plus=kek.replace("-", "+").replace("_", "/"))
    monkeypatch.setenv("LEDGER_SENSE_TOKEN_KEY", spelled)
    with pytest.raises(RuntimeError, match="must differ"):
        token_key_from_environment()


def test_the_adapter_factory_encrypts_tokens_under_the_token_key_not_the_kek(
    keys: tuple[bytes, bytes], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ledger: IdempotencyLedger,
) -> None:
    _, token_key = keys
    monkeypatch.setenv("LEDGER_SENSE_WRITEBACK_DIR", str(tmp_path))
    monkeypatch.setenv("QBO_REALM_ID", "realm-1")
    seen: list[bytes] = []
    real = TokenStore

    def spy(db_path: str, fernet_key: bytes) -> TokenStore:
        seen.append(fernet_key)
        return real(db_path, fernet_key=fernet_key)

    monkeypatch.setattr(qbo, "TokenStore", spy)

    qbo_adapter_from_environment(ledger)

    assert seen == [token_key]


def test_tokens_written_under_the_token_key_do_not_open_under_the_kek(
    keys: tuple[bytes, bytes], tmp_path: Path,
) -> None:
    kek, token_key = keys
    path = str(tmp_path / "tokens.db")
    TokenStore(path, fernet_key=token_key).save(
        "realm-1", "access", "refresh", datetime.now(UTC) + timedelta(hours=1))

    with pytest.raises(InvalidToken):
        TokenStore(path, fernet_key=kek).load("realm-1")


def test_the_boot_probe_is_silent_when_no_accounting_realm_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ledger_sense.__main__ import _probe_token_key

    monkeypatch.delenv("QBO_REALM_ID", raising=False)
    monkeypatch.delenv("LEDGER_SENSE_TOKEN_KEY", raising=False)
    _probe_token_key()  # the demo has no realm and no key; it must still boot


def test_the_boot_probe_fails_when_a_realm_is_configured_without_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ledger_sense.__main__ import _probe_token_key

    monkeypatch.setenv("QBO_REALM_ID", "realm-1")
    monkeypatch.delenv("LEDGER_SENSE_TOKEN_KEY", raising=False)
    with pytest.raises(RuntimeError, match="LEDGER_SENSE_TOKEN_KEY"):
        _probe_token_key()


def test_the_boot_probe_passes_with_a_realm_and_a_distinct_valid_key(
    keys: tuple[bytes, bytes], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ledger_sense.__main__ import _probe_token_key

    monkeypatch.setenv("QBO_REALM_ID", "realm-1")
    _probe_token_key()
