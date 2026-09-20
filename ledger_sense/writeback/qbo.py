"""QuickBooks Online adapter. Sandbox in weeks 5-12.

PLATFORM REALITY: QBO Bills have no draft state. v1 therefore creates the Bill
UNPAID, stamped AWAITING REVIEW, and NEVER creates a BillPayment. The human
approves by paying it inside QBO. That is a true sentence to tell a client, and
a better one than a claim about a draft state the platform does not have.

DOCNUMBER / PRIVATENOTE SPLIT (F-40, resolved 2026-08-29): `DocNumber` on a
QBO Bill is the VENDOR'S OWN invoice number — what a bookkeeper reconciles
against paper, and what QBO's own duplicate-document-number warning keys on.
Overwriting it with our idempotency key would silently disable a duplicate
control the client already has, in a product whose pitch is catching
duplicates. So:

  * When `request.vendor_invoice_number` is present: `DocNumber` keeps that
    invoice number unchanged, and the idempotency key is embedded in
    `PrivateNote` (see `_KEY_PATTERN` / `extract_key_from_private_note`).
  * When it is absent (nothing was extracted): `DocNumber` falls back to the
    idempotency key, as the original design did, and `PrivateNote` records in
    plain language that this fallback happened, so a human reading the books
    later understands why DocNumber looks like a key rather than an invoice
    number.

CRASH SAFETY: the key is always recoverable, but the query used to find it
after a crash differs by path:
  * Fallback (no invoice number): DocNumber IS the key, so a retry that finds
    a `pending` record queries QBO directly by DocNumber, as before.
  * Normal (invoice number present): the key is not in any queryable field,
    because QBO cannot query on PrivateNote. A retry instead queries
    `Bill WHERE TxnDate = ... AND VendorRef = ...` (there won't be many bills
    for one vendor on one date) and matches the key against each candidate's
    PrivateNote client-side.
"""

import os
import re
import threading
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

import httpx

from ledger_sense.store.blobs import kek_from_environment
from ledger_sense.writeback.idempotency import IdempotencyLedger
from ledger_sense.writeback.interface import (
    ClosedPeriodError,
    ReversalResult,
    WriteRequest,
    WriteResult,
)
from ledger_sense.writeback.tokens import TokenStore

if TYPE_CHECKING:
    from ledger_sense.writeback.interface import WritebackPort

# Intuit's OAuth token endpoint. Fixed, not client-configurable: it is the
# same host for every realm, sandbox or production (the realm id in the
# COMPANY API path is what selects sandbox vs. production data).
_TOKEN_ENDPOINT = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"

AWAITING_REVIEW = (
    "AWAITING REVIEW — Ledger Sense. Approve by paying this bill in QuickBooks."
)

# The delimiter embedded in PrivateNote so the idempotency key can be found
# and parsed back out reliably. Anchored on both sides (start-of-string-or-
# whitespace before, whitespace-or-end after) so it matches the `key=`
# TOKEN, not `key=` as a substring of some other word in the free-text memo
# (e.g. "turnkey=YES" must not be mistaken for a key marker). The key itself
# is alphanumeric (see interface.build_idempotency_key).
_KEY_PATTERN = re.compile(r"(?:^|\s)key=([A-Za-z0-9]+)(?=\s|$)")


def extract_key_from_private_note(private_note: str) -> str | None:
    """Recover the idempotency key embedded in a Bill's PrivateNote, or None
    if this note does not carry one (e.g. a bill Ledger Sense did not write)."""
    match = _KEY_PATTERN.search(private_note)
    return match.group(1) if match else None


class QBORejected(Exception):
    """QBO rejected the write outright (e.g. a 400 for an unknown VendorRef).
    Distinct from a transport failure: the write did NOT land, and never
    will without a correction, so it fails cleanly rather than sitting
    `pending` forever (F-44)."""


class ConcurrentWriteInProgressError(Exception):
    """Another write with this idempotency key is already being processed
    (or was interrupted) elsewhere in this running process (C-1).

    `IdempotencyLedger.begin()`'s `BEGIN IMMEDIATE` transaction closes the
    race in the LEDGER TABLE itself, but two concurrent callers can still
    both observe a `pending` record and both reach the reconcile-then-POST
    branch below — proven by a concurrency test using a realistic
    (non-instant) fake `post_bill`, matching a real HTTP round trip to QBO.
    Proceeding here would race that sibling call's own reconcile-then-POST
    and could create a second bill under the same key. Retry after a short
    delay instead of writing immediately: refusing outright and letting the
    caller retry is the correct trade for money-path code — silently
    risking a duplicate bill is worse than a write that has to be retried.
    """


# In-process guard for C-1: closes the window BEGIN IMMEDIATE cannot, because
# that window is AFTER begin() returns (see IdempotencyLedger's module
# docstring). Keyed on (ledger path, adapter name, idempotency key) so it
# identifies the actual resource being written, not a particular
# IdempotencyLedger/QBOAdapter Python object. Deliberately an in-memory,
# per-process set — NOT persisted, NOT a heartbeat/staleness system: a
# caller retrying after ITS OWN prior crash starts a new process (or at
# least a marker-free one) and is never blocked by it, while a genuinely
# concurrent sibling call, still holding the marker in this same process,
# is refused outright rather than allowed to race.
_IN_FLIGHT_LOCK = threading.Lock()
_IN_FLIGHT: set[tuple[str, str, str]] = set()


class QBOClient(Protocol):
    def post_bill(self, payload: dict[str, object]) -> dict[str, object]: ...
    def find_bill_by_docnumber(
        self, doc_number: str) -> dict[str, object] | None: ...
    def find_bills_by_date_and_vendor(
        self, txn_date: date, vendor_ref: str) -> list[dict[str, object]]: ...
    def get_book_close_date(self) -> date | None: ...
    def delete_bill(self, remote_id: str) -> None: ...


def _build_payload(request: WriteRequest) -> dict[str, object]:
    """Build the QBO Bill payload, applying the DocNumber/PrivateNote split."""
    if request.vendor_invoice_number:
        doc_number = request.vendor_invoice_number
        private_note = (
            f"{AWAITING_REVIEW} key={request.idempotency_key} {request.memo}")
    else:
        doc_number = request.idempotency_key
        private_note = (
            f"{AWAITING_REVIEW} key={request.idempotency_key} "
            f"(no vendor invoice number extracted; DocNumber holds the "
            f"Ledger Sense idempotency key instead) {request.memo}")

    return {
        "DocNumber": doc_number,
        "PrivateNote": private_note,
        "TxnDate": request.txn_date.isoformat(),
        "VendorRef": {"value": request.vendor_ref},
        "Line": [
            {
                "DetailType": "AccountBasedExpenseLineDetail",
                "Amount": str(item.amount),
                "Description": item.description,
                "AccountBasedExpenseLineDetail": {
                    "AccountRef": {"value": item.account_ref}},
            }
            for item in request.line_items
        ],
    }


class QBOAdapter:
    name = "qbo"

    def __init__(self, client: QBOClient, ledger: IdempotencyLedger,
                 realm_id: str) -> None:
        self._client = client
        self._ledger = ledger
        self._realm_id = realm_id

    def is_period_closed(self, txn_date: date) -> bool:
        close_date = self._client.get_book_close_date()
        return close_date is not None and txn_date <= close_date

    def _find_existing_bill(self, request: WriteRequest) -> dict[str, object] | None:
        """Crash reconciliation. Which query runs depends on where the key
        was stamped (see module docstring)."""
        if request.vendor_invoice_number:
            candidates = self._client.find_bills_by_date_and_vendor(
                request.txn_date, request.vendor_ref)
            for candidate in candidates:
                note = str(candidate.get("PrivateNote", ""))
                if extract_key_from_private_note(note) == request.idempotency_key:
                    return candidate
            return None
        return self._client.find_bill_by_docnumber(request.idempotency_key)

    def create_unapproved_bill(self, request: WriteRequest) -> WriteResult:
        # The closed-period check runs BEFORE anything is recorded or posted.
        close_date = self._client.get_book_close_date()
        if close_date is not None and request.txn_date <= close_date:
            raise ClosedPeriodError(
                f"the books are closed through {close_date.isoformat()}; this "
                f"transaction is dated {request.txn_date.isoformat()}. Ask the "
                f"client to reopen the period or re-date the bill — Ledger Sense "
                f"will not write into a closed period.")

        # C-1: claim the in-process marker for this (ledger, adapter, key)
        # BEFORE touching the ledger at all, and hold it for the whole call.
        # A genuinely concurrent sibling call in this same process sees the
        # marker already held and is refused outright; a sequential retry
        # (including the crash-recovery case) never sees it held, because it
        # is released in `finally` as soon as the prior call returns or
        # raises. See `ConcurrentWriteInProgressError` and the module-level
        # `_IN_FLIGHT` comment for why this closes the window
        # `IdempotencyLedger.begin()`'s `BEGIN IMMEDIATE` transaction cannot.
        marker = (self._ledger.path, self.name, request.idempotency_key)
        with _IN_FLIGHT_LOCK:
            if marker in _IN_FLIGHT:
                raise ConcurrentWriteInProgressError(
                    f"a write with idempotency key {request.idempotency_key!r} "
                    f"on adapter {self.name!r} is already in progress (or was "
                    f"interrupted) elsewhere in this process; retry after a "
                    f"short delay rather than writing immediately.")
            _IN_FLIGHT.add(marker)
        try:
            record = self._ledger.begin(request.idempotency_key, adapter=self.name).record

            if record.state == "committed":
                return WriteResult(
                    remote_id=record.remote_id or "", adapter=self.name,
                    idempotency_key=request.idempotency_key,
                    created_at=record.committed_at or record.attempted_at,
                    was_replay=True)

            # `pending` means a prior attempt may or may not have landed. Go and look.
            existing = self._find_existing_bill(request)
            if existing is not None:
                existing_id = str(existing["Id"])
                self._ledger.commit(request.idempotency_key, remote_id=existing_id,
                                    adapter=self.name)
                return WriteResult(remote_id=existing_id, adapter=self.name,
                                   idempotency_key=request.idempotency_key,
                                   created_at=datetime.now(UTC), was_replay=True)

            payload = _build_payload(request)
            try:
                response = self._client.post_bill(payload)
            except QBORejected as exc:
                # F-44: `fail()` was dead code with a passing test. A QBO 400 was
                # indistinguishable from a crash, so a permanently-rejected write sat
                # `pending` forever with no reason recorded.
                self._ledger.fail(request.idempotency_key, reason=str(exc),
                                  adapter=self.name)
                raise
            response_id = str(response["Id"])
            self._ledger.commit(request.idempotency_key, remote_id=response_id,
                                adapter=self.name)
            return WriteResult(remote_id=response_id, adapter=self.name,
                               idempotency_key=request.idempotency_key,
                               created_at=datetime.now(UTC), was_replay=False)
        finally:
            with _IN_FLIGHT_LOCK:
                _IN_FLIGHT.discard(marker)

    def reverse(self, remote_id: str, idempotency_key: str) -> ReversalResult:
        """REVISED (F-39). Takes the key, not just the remote id, and transitions
        the ledger record — otherwise the row stays `committed` pointing at a
        deleted bill and the next write with that key returns a confident
        `was_replay=True` with a dead id."""
        self._client.delete_bill(remote_id)
        self._ledger.mark_reversed(idempotency_key, adapter=self.name)
        return ReversalResult(remote_id=remote_id,
                              reversed_at=datetime.now(UTC), method="deleted")


class HttpQBOClient:
    """`QBOClient` against the real (sandbox) QuickBooks Online API, via httpx.

    PLATFORM: base URL is `https://sandbox-quickbooks.api.intuit.com` in
    weeks 5-12 (this project ships sandbox-only, per the brief). Every
    company-scoped call goes to `/v3/company/{realm_id}/...`.

    TOKENS: the access token comes from `TokenStore` (Task 36) and is
    refreshed exactly once per call on a 401. Intuit's refresh token
    ROTATES on every use and expires after ~100 days of disuse — the
    refreshed pair is persisted to `TokenStore` BEFORE the retry is ever
    attempted (`_refresh`), so a crash between "refreshed" and "retried"
    leaves a usable, already-rotated token behind rather than stranding the
    integration on a refresh token QBO has already invalidated.

    NEVER LOG A TOKEN. No access or refresh token value is ever passed to a
    logging call, an exception message, or `repr()` anywhere in this class.

    `transport` is an injection seam for tests (an `httpx.BaseTransport`); it
    is `None` in real use, which makes `httpx.Client` open real sockets.
    """

    def __init__(self, base_url: str, realm_id: str, token_store: "TokenStore",
                 transport: httpx.BaseTransport | None = None,
                 client_id: str | None = None, client_secret: str | None = None,
                 timeout: int = 30) -> None:
        # int seconds, not float: ledger_sense/writeback is a no-float
        # guarded package (scripts/check_no_float.py MAIN_ROOTS) — the
        # scanner flags any float literal in-tree, not only ones on the
        # money path, and an HTTP timeout has no need of sub-second
        # precision here anyway.
        self._realm_id = realm_id
        self._token_store = token_store
        # Real refreshes need Intuit app credentials; unit tests exercising
        # the retry-on-401 path inject a fake transport that never checks
        # them, so an empty string is a harmless default rather than a
        # required constructor argument every test would otherwise supply.
        self._client_id = client_id or os.environ.get("QBO_CLIENT_ID", "")
        self._client_secret = client_secret or os.environ.get("QBO_CLIENT_SECRET", "")
        self._client = httpx.Client(base_url=base_url, transport=transport,
                                    timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def _company_path(self, suffix: str) -> str:
        return f"/v3/company/{self._realm_id}{suffix}"

    def _access_token(self) -> str:
        tokens = self._token_store.load(self._realm_id)
        if tokens is None:
            raise RuntimeError(
                f"no OAuth tokens stored for realm {self._realm_id!r}; connect "
                f"the QBO sandbox account before writing.")
        return tokens.access

    def _refresh(self) -> None:
        """Exchange the current refresh token for a new access/refresh pair
        and PERSIST it before returning — see the class docstring on why the
        ordering matters."""
        tokens = self._token_store.load(self._realm_id)
        if tokens is None:
            raise RuntimeError(
                f"no OAuth tokens stored for realm {self._realm_id!r}; cannot "
                f"refresh a token that was never connected.")
        response = self._client.post(
            _TOKEN_ENDPOINT,
            data={"grant_type": "refresh_token", "refresh_token": tokens.refresh},
            auth=(self._client_id, self._client_secret),
        )
        response.raise_for_status()
        payload = response.json()
        expires_at = datetime.now(UTC) + timedelta(
            seconds=int(payload.get("expires_in", 3600)))
        self._token_store.rotate(
            self._realm_id,
            access=payload["access_token"],
            refresh=payload.get("refresh_token", tokens.refresh),
            expires_at=expires_at,
        )

    def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        """One call, refreshed and retried exactly once on a 401. The retry
        only ever happens after `_refresh` has already persisted the new
        token, so if the retry itself crashes the rotated token is not lost
        (see `_refresh` and the class docstring)."""
        response = self._client.request(
            method, path, headers={"Authorization": f"Bearer {self._access_token()}"},
            **kwargs)  # type: ignore[arg-type]
        if response.status_code == 401:
            self._refresh()
            response = self._client.request(
                method, path,
                headers={"Authorization": f"Bearer {self._access_token()}"},
                **kwargs)  # type: ignore[arg-type]
        return response

    def post_bill(self, payload: dict[str, object]) -> dict[str, object]:
        response = self._request("POST", self._company_path("/bill"), json=payload)
        if 400 <= response.status_code < 500:
            # A 4xx here is QBO refusing the write outright (e.g. an unknown
            # VendorRef) — it did not land and never will without a
            # correction (F-44), so it fails cleanly rather than retrying.
            raise QBORejected(
                f"QBO rejected the bill: {response.status_code} {response.text}")
        response.raise_for_status()
        body = response.json()
        result = body.get("Bill", body)
        return dict(result) if isinstance(result, dict) else {}

    def _query(self, query: str) -> list[dict[str, object]]:
        response = self._request(
            "GET", self._company_path("/query"), params={"query": query})
        response.raise_for_status()
        bills = response.json().get("QueryResponse", {}).get("Bill", [])
        return list(bills) if isinstance(bills, list) else []

    def find_bill_by_docnumber(self, doc_number: str) -> dict[str, object] | None:
        escaped = doc_number.replace("'", "\\'")
        results = self._query(f"SELECT * FROM Bill WHERE DocNumber = '{escaped}'")
        return results[0] if results else None

    def find_bills_by_date_and_vendor(
            self, txn_date: date, vendor_ref: str) -> list[dict[str, object]]:
        escaped_vendor = vendor_ref.replace("'", "\\'")
        return self._query(
            f"SELECT * FROM Bill WHERE TxnDate = '{txn_date.isoformat()}' "
            f"AND VendorRef = '{escaped_vendor}'")

    def get_book_close_date(self) -> date | None:
        response = self._request("GET", self._company_path("/preferences"))
        response.raise_for_status()
        body = response.json()
        # REVISED (I-2, Task 37 review). QBO wraps this response in a
        # "Preferences" envelope — {"Preferences": {"AccountingInfoPrefs":
        # {...}}} — that every sibling method in this class unwraps its own
        # equivalent of (post_bill: "Bill", delete_bill: "Bill", _query:
        # "QueryResponse") but this one alone did not. The failure was
        # silent: a real book-close date would read back as None here, and
        # Never-rule #4's closed-period refusal becomes a no-op that never
        # fires. `.get("Preferences", body)` stays tolerant of an unwrapped
        # shape too (as every sibling unwrap does), rather than assuming one
        # envelope shape is the only one ever seen.
        prefs = body.get("Preferences", body)
        info_prefs = prefs.get("AccountingInfoPrefs", {}) if isinstance(prefs, dict) else {}
        raw = info_prefs.get("BookCloseDate") if isinstance(info_prefs, dict) else None
        return date.fromisoformat(raw) if raw else None

    def delete_bill(self, remote_id: str) -> None:
        # QBO's delete operation requires the current SyncToken (optimistic
        # concurrency), which the QBOClient protocol's delete_bill(remote_id)
        # signature does not carry — fetch the bill first to get it.
        current = self._request("GET", self._company_path(f"/bill/{remote_id}"))
        current.raise_for_status()
        current_body = current.json()
        bill = current_body.get("Bill", current_body)
        sync_token = bill.get("SyncToken", "0") if isinstance(bill, dict) else "0"
        response = self._request(
            "POST", self._company_path("/bill"), params={"operation": "delete"},
            json={"Id": remote_id, "SyncToken": sync_token})
        response.raise_for_status()


_WRITEBACK_DIR_ENV = "LEDGER_SENSE_WRITEBACK_DIR"
_REALM_ID_ENV = "QBO_REALM_ID"
_QBO_SANDBOX_BASE_URL = "https://sandbox-quickbooks.api.intuit.com"


def qbo_adapter_from_environment(ledger: IdempotencyLedger) -> QBOAdapter:
    """The deployment's QBO adapter: HttpQBOClient against the sandbox,
    backed by an encrypted-at-rest TokenStore, wrapping `ledger`.

    Mirrors store/blobs.py's factory pattern. Reuses the deployment KEK
    (store.blobs.kek_from_environment) for the token store rather than
    inventing a second secret to manage — both protect at-rest client data
    the same way.
    """
    root = os.environ.get(_WRITEBACK_DIR_ENV)
    if not root:
        raise RuntimeError(
            f"{_WRITEBACK_DIR_ENV} is unset: refusing to guess where the "
            f"QBO token store lives.")
    realm_id = os.environ.get(_REALM_ID_ENV)
    if not realm_id:
        raise RuntimeError(
            f"{_REALM_ID_ENV} is unset: connect the QBO sandbox account "
            f"before writing.")
    os.makedirs(root, exist_ok=True)
    token_store = TokenStore(os.path.join(root, "tokens.db"),
                             fernet_key=kek_from_environment())
    client = HttpQBOClient(_QBO_SANDBOX_BASE_URL, realm_id, token_store)
    return QBOAdapter(client, ledger, realm_id=realm_id)


if TYPE_CHECKING:
    # Static conformance check (I-2): the brief requires QBOAdapter to satisfy
    # WritebackPort. This assignment costs nothing at runtime (it never
    # executes; TYPE_CHECKING is False) but mypy --strict will fail this
    # module the moment QBOAdapter's shape drifts from the protocol. The
    # dynamic counterpart — isinstance(adapter, WritebackPort) — lives in
    # tests/writeback/test_qbo.py.
    def _qbo_adapter_satisfies_writebackport(adapter: QBOAdapter) -> None:
        _conforms: WritebackPort = adapter

    # Same check for HttpQBOClient against QBOClient. QBOClient is a plain
    # (not @runtime_checkable) Protocol, so there is no dynamic isinstance
    # counterpart here — this static assignment is the only enforcement.
    def _http_qbo_client_satisfies_qboclient(client: HttpQBOClient) -> None:
        _conforms_qbo: QBOClient = client
