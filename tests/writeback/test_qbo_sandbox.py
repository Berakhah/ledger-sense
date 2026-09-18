"""Contract test against the REAL QBO sandbox, replayed from cassettes.

Record once with live sandbox credentials, then replay forever. This is how you
find out the API changed before a client does.

`filter_headers` and `filter_post_data_parameters` keep tokens out of the
committed cassettes — a secret in a fixture is still a secret in the repo.

TASK 37 RULING (this session has no real QBO sandbox credentials and must not
fabricate any): the four tests below that need a recorded cassette
(`test_a_bill_can_be_created_in_the_sandbox`,
`test_a_created_bill_can_be_found_by_its_stamped_key`,
`test_the_book_close_date_can_be_read`, `test_a_bill_can_be_deleted_for_reversal`)
are written exactly as the plan specifies, using the `sandbox_client` fixture
(tests/conftest.py), which skips cleanly via `pytest.skip(...)` whenever
QBO_CLIENT_ID/QBO_CLIENT_SECRET/QBO_REALM_ID/QBO_REFRESH_TOKEN are not set —
which they are not, in this session or in CI outside the nightly job. No
cassette files exist under tests/writeback/cassettes/ yet; recording them is
an open item for an operator with real sandbox credentials.

The remaining tests in this file (vcr configuration + OAuth token refresh) do
NOT need a live cassette or real credentials and are fully exercised here
against a fake transport.
"""

import pathlib
from datetime import UTC, date, datetime

import httpx
import pytest
import vcr

from ledger_sense.domain.money import money
from ledger_sense.writeback.idempotency import IdempotencyLedger
from ledger_sense.writeback.interface import (
    LineItem,
    WriteRequest,
    build_idempotency_key,
)
from ledger_sense.writeback.qbo import QBOAdapter


def _scrub_response(response):
    """REVISED (F-28). `filter_headers` scrubs the REQUEST header and
    `filter_post_data_parameters` the REQUEST body. NEITHER touches the response
    body — and the OAuth token endpoint returns access_token and refresh_token
    right there. `test_no_cassette_contains_a_token` would catch it, but only
    after the secret had been written to disk and quite possibly committed.

    Hard constraint 00 §3.4: no secrets in a repo, ever, including test fixtures.
    """
    import json
    body = response.get("body", {}).get("string")
    if not body:
        return response
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return response
    for secret in ("access_token", "refresh_token", "id_token"):
        if secret in payload:
            payload[secret] = "REDACTED"
    response["body"]["string"] = json.dumps(payload).encode()
    return response


qbo_vcr = vcr.VCR(
    cassette_library_dir="tests/writeback/cassettes",
    record_mode="once",
    filter_headers=["authorization"],
    filter_post_data_parameters=["access_token", "refresh_token", "client_secret"],
    before_record_response=_scrub_response,
    # "query" is REQUIRED (F-29). Every find_bill_by_docnumber call is
    # GET /v3/company/{realm}/query?query=… — same path, different query. Without
    # it, two different queries in one cassette replay each other's responses,
    # and the contract test that exists to prove our fake matches reality proves
    # nothing at all.
    match_on=["method", "scheme", "host", "path", "query"],
)


@pytest.fixture()
def ledger(tmp_path):
    led = IdempotencyLedger(str(tmp_path / "writes.db"))
    yield led
    led.close()


def _request(application_no):
    return WriteRequest(
        idempotency_key=build_idempotency_key("sandbox", "C-SANDBOX", application_no),
        doc_id="doc-sandbox", vendor_ref="1", vendor_invoice_number=None,
        txn_date=date(2026, 8, 31),
        line_items=[LineItem(description="Sitework", amount=money("100.00"),
                             account_ref="7")],
        memo="sandbox contract test")


# ---------------------------------------------------------------------------
# Cassette-dependent contract tests — skip cleanly without sandbox credentials
# (see module docstring and the Task 37 ruling). `sandbox_client` skips
# before any of these ever touch the network.
# ---------------------------------------------------------------------------

@qbo_vcr.use_cassette("create_bill.yaml")
def test_a_bill_can_be_created_in_the_sandbox(ledger, sandbox_client):
    result = QBOAdapter(sandbox_client, ledger,
                        realm_id="sandbox").create_unapproved_bill(_request(901))
    assert result.remote_id


@qbo_vcr.use_cassette("find_bill_by_docnumber.yaml")
def test_a_created_bill_can_be_found_by_its_stamped_key(ledger, sandbox_client):
    request = _request(902)
    QBOAdapter(sandbox_client, ledger, realm_id="sandbox").create_unapproved_bill(request)
    assert sandbox_client.find_bill_by_docnumber(request.idempotency_key) is not None


@qbo_vcr.use_cassette("book_close_date.yaml")
def test_the_book_close_date_can_be_read(sandbox_client):
    close_date = sandbox_client.get_book_close_date()
    assert close_date is None or isinstance(close_date, date)


@qbo_vcr.use_cassette("delete_bill.yaml")
def test_a_bill_can_be_deleted_for_reversal(ledger, sandbox_client):
    request = _request(903)
    adapter = QBOAdapter(sandbox_client, ledger, realm_id="sandbox")
    result = adapter.create_unapproved_bill(request)
    assert adapter.reverse(result.remote_id, request.idempotency_key).method == "deleted"
    assert sandbox_client.find_bill_by_docnumber(request.idempotency_key) is None


# ---------------------------------------------------------------------------
# vcr configuration — run today, with zero cassette files present (F-28/F-29)
# ---------------------------------------------------------------------------

def test_no_cassette_contains_a_token():
    """A secret in a test fixture is still a secret in the repo (00 §3.4).

    F-28: the original scrubbed request headers and request bodies only. The
    token endpoint returns access_token and refresh_token in the RESPONSE body,
    which nothing filtered — this test would have caught it after the fact, which
    for a secret is too late. `before_record_response` now scrubs at record time,
    and Phase 0 Task P1 runs gitleaks over this directory in CI as well.

    No cassette files exist yet (Task 37 ruling — no credentials to record
    with), so this glob is empty and the test passes vacuously today; it
    starts doing real work the moment cassettes land.
    """
    for cassette in pathlib.Path("tests/writeback/cassettes").glob("*.yaml"):
        text = cassette.read_text(encoding="utf-8").lower()
        for marker in ("bearer ", "client_secret"):
            assert marker not in text, f"{cassette.name} contains {marker!r}"
        for marker in ('"access_token": "', '"refresh_token": "'):
            if marker in text:
                start = text.index(marker) + len(marker)
                assert text[start:start + 8] == "redacted", \
                    f"{cassette.name} contains an unscrubbed token value"


def test_different_queries_do_not_replay_each_others_responses():
    """F-29. match_on omitted "query", so every GET /query?query=… matched every
    other one — the contract test proving our fake matches the real sandbox was
    itself replaying the wrong thing."""
    assert "query" in qbo_vcr.match_on


# ---------------------------------------------------------------------------
# Token refresh: F-41. No live cassette needed — a fake httpx transport
# stands in for the sandbox network entirely.
# ---------------------------------------------------------------------------

class _RefreshTestTransport(httpx.BaseTransport):
    """Minimal fake QBO network: the first call to the company API 401s, the
    OAuth token endpoint then serves a fresh access/refresh pair, and the
    retried company-API call succeeds (or, in `crash_after_refresh` mode,
    the connection dies right after the refresh completed).

    `refresh_persisted_before_retry` records whether the rotated refresh
    token was already durable in the `TokenStore` by the time the RETRY
    request reached the network — the exact ordering `HttpQBOClient._refresh`
    exists to guarantee, since Intuit's refresh token is single-use and
    rotates on every exchange: persist-after-retry would mean a crash
    between the two strands the integration on a refresh token QBO has
    already invalidated.
    """

    def __init__(self, token_store, realm_id, new_refresh="rt-2",
                 crash_after_refresh=False):
        self._token_store = token_store
        self._realm_id = realm_id
        self._new_refresh = new_refresh
        self._crash_after_refresh = crash_after_refresh
        self._company_calls = 0
        self.refresh_persisted_before_retry = False

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth.platform.intuit.com":
            return httpx.Response(200, json={
                "access_token": "at-fresh",
                "refresh_token": self._new_refresh,
                "expires_in": 3600,
            })

        self._company_calls += 1
        if self._company_calls == 1:
            return httpx.Response(
                401, json={"Fault": {"Error": [{"Message": "token expired"}]}})

        stored = self._token_store.load(self._realm_id)
        self.refresh_persisted_before_retry = (
            stored is not None and stored.refresh == self._new_refresh)
        if self._crash_after_refresh:
            raise ConnectionError(
                "connection dropped after refresh, before the retry completed")
        return httpx.Response(
            200, json={"AccountingInfoPrefs": {"BookCloseDate": None}})


def _transport_returning_401_then_200(token_store, realm_id, new_refresh="rt-2"):
    return _RefreshTestTransport(token_store, realm_id, new_refresh=new_refresh)


def _transport_crashing_after_refresh(token_store, realm_id, new_refresh="rt-2"):
    return _RefreshTestTransport(token_store, realm_id, new_refresh=new_refresh,
                                 crash_after_refresh=True)


def test_a_401_triggers_a_refresh_and_retries_once(tmp_path):
    """Nothing tested refresh. The plan said "refreshed on 401" in prose."""
    from cryptography.fernet import Fernet

    from ledger_sense.writeback.qbo import HttpQBOClient
    from ledger_sense.writeback.tokens import TokenStore

    store = TokenStore(str(tmp_path / "t.db"), fernet_key=Fernet.generate_key())
    store.save("r-1", access="expired", refresh="rt-1",
               expires_at=datetime.now(UTC))
    client = HttpQBOClient(base_url="https://sandbox.example.test",
                           realm_id="r-1", token_store=store,
                           transport=_transport_returning_401_then_200(store, "r-1"))
    assert client.get_book_close_date() is None
    assert store.load("r-1").access != "expired"


def test_the_rotated_refresh_token_is_persisted_before_the_retry(tmp_path):
    """Intuit ROTATES the refresh token on every refresh and expires it after
    ~100 days. Failing to persist the new one locks the integration out
    permanently, and it surfaces as a nightly smoke failure with no obvious
    cause — days later, with no way to recover without re-consent."""
    from cryptography.fernet import Fernet

    from ledger_sense.writeback.qbo import HttpQBOClient
    from ledger_sense.writeback.tokens import TokenStore

    store = TokenStore(str(tmp_path / "t.db"), fernet_key=Fernet.generate_key())
    store.save("r-1", access="expired", refresh="rt-1",
               expires_at=datetime.now(UTC))
    transport = _transport_returning_401_then_200(store, "r-1", new_refresh="rt-2")
    client = HttpQBOClient(base_url="https://sandbox.example.test",
                           realm_id="r-1", token_store=store, transport=transport)
    client.get_book_close_date()
    assert store.load("r-1").refresh == "rt-2"
    assert transport.refresh_persisted_before_retry is True


def test_a_crash_between_refresh_and_retry_leaves_a_usable_token(tmp_path):
    """The one that bites at 2am: the refresh succeeded, the retry died, and the
    old refresh token is already dead upstream."""
    from cryptography.fernet import Fernet

    from ledger_sense.writeback.qbo import HttpQBOClient
    from ledger_sense.writeback.tokens import TokenStore

    store = TokenStore(str(tmp_path / "t.db"), fernet_key=Fernet.generate_key())
    store.save("r-1", access="expired", refresh="rt-1",
               expires_at=datetime.now(UTC))
    client = HttpQBOClient(
        base_url="https://sandbox.example.test", realm_id="r-1", token_store=store,
        transport=_transport_crashing_after_refresh(store, "r-1", new_refresh="rt-2"))
    with pytest.raises(ConnectionError):
        client.get_book_close_date()
    assert store.load("r-1").refresh == "rt-2", "the rotated token was lost"


def test_no_token_value_is_ever_logged(caplog, tmp_path):
    from cryptography.fernet import Fernet

    from ledger_sense.writeback.tokens import TokenStore

    store = TokenStore(str(tmp_path / "t.db"), fernet_key=Fernet.generate_key())
    store.save("r-1", access="at-secret", refresh="rt-secret",
               expires_at=datetime.now(UTC))
    store.load("r-1")
    assert "at-secret" not in caplog.text and "rt-secret" not in caplog.text
