"""Top-level pytest config.

`pytest_plugins` registers `tests/run/conftest.py`'s fixtures (the `db`
fixture / `RunDb`) for the whole suite, not just `tests/run/`. This is the
cleaner alternative to importing the `db` fixture function by name into each
test module that needs it (see tests/test_main.py) — it avoids importing a
fixture just for pytest's collection to see it, which ruff flags as unused.
"""

pytest_plugins = ["tests.run.conftest"]

import os

import pytest


@pytest.fixture()
def sandbox_client():
    """Real sandbox client, or skip. A fresh clone replays cassettes without
    credentials; only re-recording needs them.

    Task 37 ruling: this session has no real QBO sandbox credentials and must
    not fabricate any — a secret-shaped value in the repo is exactly what
    this project's Never-rules forbid, valid or not. So the four contract
    tests that need a live cassette (tests/writeback/test_qbo_sandbox.py)
    skip cleanly here, every time, until an operator with real credentials
    records the cassettes."""
    required = ("QBO_CLIENT_ID", "QBO_CLIENT_SECRET", "QBO_REALM_ID",
                "QBO_REFRESH_TOKEN")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        pytest.skip(f"sandbox credentials not set: {', '.join(missing)}")

    from cryptography.fernet import Fernet

    from ledger_sense.writeback.qbo import HttpQBOClient
    from ledger_sense.writeback.tokens import TokenStore

    store = TokenStore(":memory:", fernet_key=Fernet.generate_key())
    return HttpQBOClient(
        base_url="https://sandbox-quickbooks.api.intuit.com",
        realm_id=os.environ["QBO_REALM_ID"], token_store=store)
