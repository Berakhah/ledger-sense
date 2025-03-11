# Ledger Sense — Phase 5: Writeback (Week 10)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Write unapproved bills into a QuickBooks Online sandbox company, idempotently, never into a closed period, with a reversal that restores the prior state — and a CSV/IIF export that works with zero API approval.

**Architecture:** A `WritebackPort` protocol with two peer adapters (`QBOAdapter`, `ExportAdapter`). An idempotency ledger records intent *before* the remote call and reconciles on retry by querying the remote system for the stamped key. Every write carries an append-only audit trail.

**Tech Stack:** FastAPI, `httpx`, `vcrpy` for recorded cassettes, QuickBooks Online sandbox, `cryptography` for token storage at rest.

**Spec:** `docs/superpowers/specs/2025-03-11-ledger-sense-design.md` §3.1, §4.2
**Parent plan:** `2025-03-11-ledger-sense-weeks-5-12.md` — its Global Constraints and File Structure apply here in full.

---

## Read this before Task 34

**This is the highest-consequence code in the product. You are writing to someone's books.**

### The platform reality the brief's wording does not survive

The brief says *"draft only — create unapproved bills / draft transactions."* **QuickBooks Online
Bills have no native draft state.** A Bill is a Bill the moment it exists. The honest mapping:

| Brief's intent | What v1 actually does |
|---|---|
| Create a draft / unapproved bill | Create the **Bill, unpaid**, stamped `AWAITING REVIEW — Ledger Sense` in `PrivateNote`, keyed in `DocNumber` |
| A human approves in the accounting system | The human **pays it** in QBO. That is the approval act |
| Never post an approved transaction | **Never create a `BillPayment`.** Ever. Asserted by test and by AST guard |

Say this out loud to the client. *"We create the bill; you approve it by paying it; we never move
money"* is a true sentence, and a better one than a claim about a draft state the platform does
not have.

### Sandbox only

Weeks 5–12 touch the **sandbox** exclusively. Sandbox needs no Intuit approval. The production
app-assessment questionnaire (spec §3.1) is not on the critical path and must not become one.

### The dual-write problem, and why the ledger writes first

A naive implementation calls QBO, then records what it did. If the process dies between those two,
a retry creates a **second bill**. So the ledger records `pending` **before** the remote call, and
a retry that finds a `pending` record **queries QBO for the stamped key** before creating anything.

```
begin(key, pending) ──▶ POST /bill (DocNumber = key) ──▶ commit(key, remote_id)
                    ▲                                 ▲
                    │                                 │
     crash here: retry finds pending,   crash here: retry finds pending,
     query finds nothing → safe to POST query FINDS the bill → adopt its id
```

---

### Task 34: The writeback port

**Files:**
- Create: `ledger_sense/writeback/interface.py`
- Test: `tests/writeback/test_interface.py`

**Interfaces:**
- Consumes: `Money`, `money`.
- Produces:
  - `build_idempotency_key(document_hash, contract_id, application_no) -> str` — ≤21 chars
  - `LineItem(description, amount, account_ref)`
  - `WriteRequest(idempotency_key, doc_id, vendor_ref, txn_date, line_items, memo)`
  - `WriteResult(remote_id, idempotency_key, adapter, created_at, was_replay)`
  - `ReversalResult(remote_id, reversed_at, method: Literal["deleted", "voided"])`
  - `ClosedPeriodError`, `ApprovedTransactionError`
  - `WritebackPort(Protocol)` — `name`, `create_unapproved_bill`, `is_period_closed`, `reverse`

- [ ] **Step 1: Write the failing test**

```python
# tests/writeback/test_interface.py
from datetime import date, datetime, timezone

import pytest

from ledger_sense.domain.money import money
from ledger_sense.writeback.interface import (
    LineItem, ReversalResult, WriteRequest, WriteResult, WritebackPort,
    build_idempotency_key,
)


def test_idempotency_key_is_deterministic():
    a = build_idempotency_key(document_hash="abc123", contract_id="C-1", application_no=2)
    b = build_idempotency_key(document_hash="abc123", contract_id="C-1", application_no=2)
    assert a == b


def test_idempotency_key_changes_with_the_application_number():
    a = build_idempotency_key(document_hash="abc123", contract_id="C-1", application_no=2)
    b = build_idempotency_key(document_hash="abc123", contract_id="C-1", application_no=3)
    assert a != b


def test_idempotency_key_fits_a_qbo_docnumber_field():
    """QBO DocNumber is capped at 21 characters. A key that will not fit cannot
    be stamped, and an unstamped key cannot be queried back after a crash."""
    key = build_idempotency_key(document_hash="a" * 64, contract_id="C-1",
                                application_no=2)
    assert len(key) <= 21


def test_line_item_rejects_a_float_amount():
    with pytest.raises(Exception):
        LineItem(description="x", amount=1000.0, account_ref="54")


def test_a_conforming_adapter_satisfies_the_protocol():
    class FakeAdapter:
        name = "fake"

        def create_unapproved_bill(self, request: WriteRequest) -> WriteResult:
            return WriteResult(remote_id="1", idempotency_key=request.idempotency_key,
                               adapter=self.name,
                               created_at=datetime.now(timezone.utc), was_replay=False)

        def is_period_closed(self, txn_date: date) -> bool:
            return False

        def reverse(self, remote_id: str) -> ReversalResult:
            return ReversalResult(remote_id=remote_id,
                                  reversed_at=datetime.now(timezone.utc),
                                  method="deleted")

    assert isinstance(FakeAdapter(), WritebackPort)


def test_a_missing_method_fails_the_protocol():
    class Incomplete:
        name = "incomplete"

        def create_unapproved_bill(self, request):
            raise NotImplementedError

    assert not isinstance(Incomplete(), WritebackPort)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/writeback/test_interface.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.writeback.interface'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/writeback/interface.py
"""The writeback port. QBO and CSV/IIF export are PEER adapters.

Export is not a fallback bolted on after the API failed — it is a first-class
adapter satisfying the same protocol, which is what makes B12 survivable: the
product is useful on day one with no API approval at all.
"""

import hashlib
from datetime import date, datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, field_serializer

from ledger_sense.domain.money import Money

# QBO DocNumber accepts up to 21 characters. The key must fit so it can be
# stamped on the remote object and queried back after a crash.
KEY_LENGTH = 21


class ClosedPeriodError(Exception):
    """The books are closed through this date. Refuse and explain."""


class ApprovedTransactionError(Exception):
    """Someone tried to post an approved transaction. v1 never does this."""


def build_idempotency_key(document_hash: str, contract_id: str,
                          application_no: int) -> str:
    payload = f"{document_hash}|{contract_id}|{application_no}".encode("utf-8")
    return "LS" + hashlib.sha256(payload).hexdigest()[:KEY_LENGTH - 2]


class LineItem(BaseModel):
    description: str
    amount: Money
    account_ref: str

    @field_serializer("amount")
    def _as_string(self, value: Money) -> str:
        return str(value)


class WriteRequest(BaseModel):
    idempotency_key: str
    doc_id: str
    vendor_ref: str
    txn_date: date
    line_items: list[LineItem]
    memo: str


class WriteResult(BaseModel):
    remote_id: str
    idempotency_key: str
    adapter: str
    created_at: datetime
    was_replay: bool


class ReversalResult(BaseModel):
    remote_id: str
    reversed_at: datetime
    method: Literal["deleted", "voided"]


@runtime_checkable
class WritebackPort(Protocol):
    name: str

    def create_unapproved_bill(self, request: WriteRequest) -> WriteResult: ...

    def is_period_closed(self, txn_date: date) -> bool: ...

    def reverse(self, remote_id: str) -> ReversalResult: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/writeback/test_interface.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/writeback/interface.py tests/writeback/test_interface.py
git commit -m "feat(writeback): port with deterministic idempotency keys sized for QBO"
```

---

### Task 35: Idempotency ledger — force-fail mid-write

**Files:**
- Create: `ledger_sense/writeback/idempotency.py`
- Test: `tests/writeback/test_idempotency.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `WriteState = Literal["pending", "committed", "failed"]`
  - `WriteRecord(key, adapter, state, remote_id, reason, attempted_at, committed_at)`
  - `IdempotencyLedger(db_path)` with `begin(key, adapter)`, `commit(key, remote_id, adapter)`, `fail(key, reason, adapter)`, `lookup(key, adapter)`, `close()`
  - `begin` returns the **existing** record when one is present — the caller inspects `state`

- [ ] **Step 1: Write the failing test**

```python
# tests/writeback/test_idempotency.py
import pytest

from ledger_sense.writeback.idempotency import IdempotencyLedger

KEY = "LS0123456789abcdef012"


@pytest.fixture()
def ledger(tmp_path):
    led = IdempotencyLedger(str(tmp_path / "writes.db"))
    yield led
    led.close()


def test_a_fresh_key_begins_pending(ledger):
    assert ledger.begin(KEY, adapter="qbo").state == "pending"


def test_begin_is_idempotent_and_returns_the_existing_record(ledger):
    ledger.begin(KEY, adapter="qbo")
    ledger.commit(KEY, remote_id="145")
    replayed = ledger.begin(KEY, adapter="qbo")
    assert replayed.state == "committed"
    assert replayed.remote_id == "145"


def test_committing_records_the_remote_id(ledger):
    ledger.begin(KEY, adapter="qbo")
    ledger.commit(KEY, remote_id="145")
    assert ledger.lookup(KEY).remote_id == "145"


def test_a_crash_between_begin_and_commit_leaves_the_record_pending(ledger, tmp_path):
    """THE CASE THAT MATTERS. A pending record on retry means we do not know
    whether the remote write landed — the adapter must go and look."""
    ledger.begin(KEY, adapter="qbo")
    ledger.close()

    reopened = IdempotencyLedger(str(tmp_path / "writes.db"))
    assert reopened.lookup(KEY).state == "pending"
    reopened.close()


def test_a_failed_write_records_its_reason_and_can_be_retried(ledger):
    ledger.begin(KEY, adapter="qbo")
    ledger.fail(KEY, reason="502 from upstream")
    assert ledger.lookup(KEY).state == "failed"
    assert ledger.begin(KEY, adapter="qbo").state == "failed"


def test_committing_an_unknown_key_raises_rather_than_inventing_a_record(ledger):
    with pytest.raises(KeyError):
        ledger.commit("LSnever-seen-this-key", remote_id="1")


def test_two_adapters_do_not_share_a_key_namespace(ledger):
    ledger.begin(KEY, adapter="qbo")
    ledger.commit(KEY, remote_id="145")
    assert ledger.begin(KEY, adapter="export").state == "pending"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/writeback/test_idempotency.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.writeback.idempotency'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/writeback/idempotency.py
"""Idempotency ledger. Records intent BEFORE the remote call.

The dual-write problem: call the remote system, then record what you did, and a
crash between the two produces a duplicate bill on retry. So the order inverts —
record `pending`, make the call, record `committed`.

A `pending` record found on retry means WE DO NOT KNOW whether the write landed.
The adapter must query the remote system for the stamped key before creating
anything. That reconciliation is Task 36's job; this module's job is to make the
uncertainty visible instead of silently duplicating.
"""

import sqlite3
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel

WriteState = Literal["pending", "committed", "failed"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS writes (
    key          TEXT NOT NULL,
    adapter      TEXT NOT NULL,
    state        TEXT NOT NULL,
    remote_id    TEXT,
    reason       TEXT,
    attempted_at TEXT NOT NULL,
    committed_at TEXT,
    PRIMARY KEY (key, adapter)
)
"""


class WriteRecord(BaseModel):
    key: str
    adapter: str
    state: WriteState
    remote_id: str | None
    reason: str | None
    attempted_at: datetime
    committed_at: datetime | None


def _to_record(row: tuple) -> WriteRecord:
    return WriteRecord(
        key=row[0], adapter=row[1], state=row[2], remote_id=row[3], reason=row[4],
        attempted_at=datetime.fromisoformat(row[5]),
        committed_at=datetime.fromisoformat(row[6]) if row[6] else None)


class IdempotencyLedger:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def lookup(self, key: str, adapter: str = "qbo") -> WriteRecord | None:
        row = self._conn.execute(
            "SELECT key, adapter, state, remote_id, reason, attempted_at, "
            "committed_at FROM writes WHERE key = ? AND adapter = ?",
            (key, adapter)).fetchone()
        return _to_record(row) if row else None

    def begin(self, key: str, adapter: str) -> WriteRecord:
        """Return the existing record if there is one; otherwise open a pending."""
        existing = self.lookup(key, adapter)
        if existing is not None:
            return existing
        self._conn.execute(
            "INSERT INTO writes (key, adapter, state, attempted_at) "
            "VALUES (?, ?, 'pending', ?)",
            (key, adapter, datetime.now(timezone.utc).isoformat()))
        self._conn.commit()
        record = self.lookup(key, adapter)
        assert record is not None
        return record

    def commit(self, key: str, remote_id: str, adapter: str = "qbo") -> None:
        if self.lookup(key, adapter) is None:
            raise KeyError(f"no pending write for key {key!r} on adapter {adapter!r}")
        self._conn.execute(
            "UPDATE writes SET state = 'committed', remote_id = ?, committed_at = ? "
            "WHERE key = ? AND adapter = ?",
            (remote_id, datetime.now(timezone.utc).isoformat(), key, adapter))
        self._conn.commit()

    def fail(self, key: str, reason: str, adapter: str = "qbo") -> None:
        if self.lookup(key, adapter) is None:
            raise KeyError(f"no pending write for key {key!r} on adapter {adapter!r}")
        self._conn.execute(
            "UPDATE writes SET state = 'failed', reason = ? "
            "WHERE key = ? AND adapter = ?", (reason, key, adapter))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/writeback/test_idempotency.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/writeback/idempotency.py tests/writeback/test_idempotency.py
git commit -m "feat(writeback): idempotency ledger recording intent before the remote call"
```

---

### Task 36: QBO adapter, against sandbox

**Files:**
- Create: `ledger_sense/writeback/tokens.py`
- Create: `ledger_sense/writeback/qbo.py`
- Test: `tests/writeback/test_tokens.py`
- Test: `tests/writeback/test_qbo.py`

**Interfaces:**
- Consumes: Tasks 34, 35.
- Produces:
  - `Tokens(access, refresh, expires_at)`; `TokenStore(db_path, fernet_key)` with `save`/`rotate`/`load`/`close`
  - `QBOClient(Protocol)` — `post_bill`, `find_bill_by_docnumber`, `get_book_close_date`, `delete_bill`
  - `QBOAdapter(client, ledger, realm_id)` satisfying `WritebackPort`
  - `AWAITING_REVIEW: str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/writeback/test_tokens.py
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from ledger_sense.writeback.tokens import TokenStore


@pytest.fixture()
def store(tmp_path):
    s = TokenStore(str(tmp_path / "tokens.db"), fernet_key=Fernet.generate_key())
    yield s
    s.close()


def test_tokens_round_trip(store):
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    store.save("realm-1", access="at-1", refresh="rt-1", expires_at=expires)
    loaded = store.load("realm-1")
    assert loaded.access == "at-1" and loaded.refresh == "rt-1"


def test_tokens_are_encrypted_at_rest(store, tmp_path):
    """Token compromise means access to a client's financials."""
    store.save("realm-1", access="at-secret", refresh="rt-secret",
               expires_at=datetime.now(timezone.utc))
    store.close()
    raw = (tmp_path / "tokens.db").read_bytes()
    assert b"at-secret" not in raw
    assert b"rt-secret" not in raw


def test_rotation_replaces_both_tokens(store):
    now = datetime.now(timezone.utc)
    store.save("realm-1", access="at-1", refresh="rt-1", expires_at=now)
    store.rotate("realm-1", access="at-2", refresh="rt-2",
                 expires_at=now + timedelta(hours=1))
    loaded = store.load("realm-1")
    assert loaded.access == "at-2" and loaded.refresh == "rt-2"


def test_an_unknown_realm_loads_as_none(store):
    assert store.load("realm-unknown") is None
```

```python
# tests/writeback/test_qbo.py
import ast
import pathlib
from datetime import date

import pytest

from ledger_sense.domain.money import money
from ledger_sense.writeback.idempotency import IdempotencyLedger
from ledger_sense.writeback.interface import (
    ClosedPeriodError, LineItem, WriteRequest, build_idempotency_key,
)
from ledger_sense.writeback.qbo import QBOAdapter


class FakeQBOClient:
    """Stands in for the sandbox. `fail_after_post` reproduces the crash that
    happens between QBO accepting the bill and us recording it."""

    def __init__(self, book_close: date | None = None,
                 fail_after_post: bool = False) -> None:
        self.bills: dict[str, dict] = {}
        self.payments: list[dict] = []
        self.book_close = book_close
        self.fail_after_post = fail_after_post
        self._next_id = 100

    def post_bill(self, payload: dict) -> dict:
        self._next_id += 1
        remote_id = str(self._next_id)
        self.bills[remote_id] = {**payload, "Id": remote_id, "Balance": "1000.00"}
        if self.fail_after_post:
            raise ConnectionError("connection dropped after QBO accepted the bill")
        return {"Id": remote_id}

    def find_bill_by_docnumber(self, doc_number: str) -> dict | None:
        return next((b for b in self.bills.values()
                     if b.get("DocNumber") == doc_number), None)

    def get_book_close_date(self) -> date | None:
        return self.book_close

    def delete_bill(self, remote_id: str) -> None:
        self.bills.pop(remote_id, None)

    def post_bill_payment(self, payload: dict) -> dict:  # must never be called
        self.payments.append(payload)
        return {"Id": "pay-1"}


@pytest.fixture()
def ledger(tmp_path):
    led = IdempotencyLedger(str(tmp_path / "writes.db"))
    yield led
    led.close()


def _request(application_no=2, txn_date=date(2026, 8, 31)):
    return WriteRequest(
        idempotency_key=build_idempotency_key("abc123", "C-1", application_no),
        doc_id="doc-1", vendor_ref="V-9", txn_date=txn_date,
        line_items=[LineItem(description="Sitework", amount=money("1000.00"),
                             account_ref="54")],
        memo="Application 2")


def test_a_bill_is_created_unpaid_and_marked_awaiting_review(ledger):
    client = FakeQBOClient()
    result = QBOAdapter(client, ledger, realm_id="r-1").create_unapproved_bill(_request())
    assert "AWAITING REVIEW" in client.bills[result.remote_id]["PrivateNote"]


def test_no_bill_payment_is_ever_created(ledger):
    """v1 never posts an approved transaction, no matter what the client asks."""
    client = FakeQBOClient()
    QBOAdapter(client, ledger, realm_id="r-1").create_unapproved_bill(_request())
    assert client.payments == []


def test_no_code_path_calls_post_bill_payment():
    """AST guard. The absence of a call today is not a guarantee tomorrow."""
    offenders = []
    for path in pathlib.Path("ledger_sense").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(
                    node.func, "attr", "") == "post_bill_payment":
                offenders.append(f"{path}:{node.lineno}")
    assert offenders == [], f"BillPayment created at: {offenders}"


def test_the_idempotency_key_is_stamped_on_the_remote_bill(ledger):
    client = FakeQBOClient()
    request = _request()
    result = QBOAdapter(client, ledger, realm_id="r-1").create_unapproved_bill(request)
    assert client.bills[result.remote_id]["DocNumber"] == request.idempotency_key


def test_a_retry_after_success_replays_rather_than_creating_a_second_bill(ledger):
    client = FakeQBOClient()
    adapter = QBOAdapter(client, ledger, realm_id="r-1")
    first = adapter.create_unapproved_bill(_request())
    second = adapter.create_unapproved_bill(_request())
    assert second.remote_id == first.remote_id
    assert second.was_replay is True
    assert len(client.bills) == 1


def test_force_failing_mid_write_then_retrying_creates_exactly_one_bill(ledger):
    """THE TEST THIS PHASE EXISTS FOR. QBO accepted the bill; the connection
    died before we recorded it. The retry must find the stamped key, not post
    again."""
    request = _request()
    crashing = FakeQBOClient(fail_after_post=True)
    with pytest.raises(ConnectionError):
        QBOAdapter(crashing, ledger, realm_id="r-1").create_unapproved_bill(request)
    assert len(crashing.bills) == 1

    crashing.fail_after_post = False
    result = QBOAdapter(crashing, ledger, realm_id="r-1").create_unapproved_bill(request)

    assert len(crashing.bills) == 1, "the retry created a duplicate bill"
    assert result.was_replay is True


def test_a_closed_period_is_refused_with_an_explanation(ledger):
    client = FakeQBOClient(book_close=date(2026, 9, 30))
    with pytest.raises(ClosedPeriodError) as exc:
        QBOAdapter(client, ledger, realm_id="r-1").create_unapproved_bill(
            _request(txn_date=date(2026, 8, 31)))
    assert "2026-09-30" in str(exc.value)
    assert client.bills == {}, "a bill was created despite the closed period"


def test_the_close_date_is_checked_before_anything_is_recorded(ledger):
    client = FakeQBOClient(book_close=date(2026, 12, 31))
    request = _request()
    with pytest.raises(ClosedPeriodError):
        QBOAdapter(client, ledger, realm_id="r-1").create_unapproved_bill(request)
    assert ledger.lookup(request.idempotency_key) is None


def test_a_date_after_the_close_is_permitted(ledger):
    client = FakeQBOClient(book_close=date(2026, 7, 31))
    result = QBOAdapter(client, ledger, realm_id="r-1").create_unapproved_bill(
        _request(txn_date=date(2026, 8, 31)))
    assert result.remote_id in client.bills


def test_reversal_deletes_the_bill(ledger):
    client = FakeQBOClient()
    adapter = QBOAdapter(client, ledger, realm_id="r-1")
    result = adapter.create_unapproved_bill(_request())
    reversal = adapter.reverse(result.remote_id)
    assert reversal.method == "deleted"
    assert result.remote_id not in client.bills
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/writeback/test_tokens.py tests/writeback/test_qbo.py -v`
Expected: FAIL — `ModuleNotFoundError` for `ledger_sense.writeback.tokens` and `.qbo`

- [ ] **Step 3: Write the token store**

```python
# ledger_sense/writeback/tokens.py
"""OAuth 2.0 token storage, encrypted at rest, with rotation.

Token compromise means access to a client's financials. Encrypt, rotate, and
never log a token value.
"""

import sqlite3
from datetime import datetime

from cryptography.fernet import Fernet
from pydantic import BaseModel

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    realm_id   TEXT PRIMARY KEY,
    access     BLOB NOT NULL,
    refresh    BLOB NOT NULL,
    expires_at TEXT NOT NULL
)
"""


class Tokens(BaseModel):
    access: str
    refresh: str
    expires_at: datetime


class TokenStore:
    def __init__(self, db_path: str, fernet_key: bytes) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(_SCHEMA)
        self._conn.commit()
        self._fernet = Fernet(fernet_key)

    def save(self, realm_id: str, access: str, refresh: str,
             expires_at: datetime) -> None:
        self._conn.execute(
            "INSERT INTO tokens (realm_id, access, refresh, expires_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (realm_id) DO UPDATE SET "
            "access = excluded.access, refresh = excluded.refresh, "
            "expires_at = excluded.expires_at",
            (realm_id, self._fernet.encrypt(access.encode()),
             self._fernet.encrypt(refresh.encode()), expires_at.isoformat()))
        self._conn.commit()

    # Rotation is a save with new values; the alias documents the intent.
    rotate = save

    def load(self, realm_id: str) -> Tokens | None:
        row = self._conn.execute(
            "SELECT access, refresh, expires_at FROM tokens WHERE realm_id = ?",
            (realm_id,)).fetchone()
        if row is None:
            return None
        return Tokens(access=self._fernet.decrypt(row[0]).decode(),
                      refresh=self._fernet.decrypt(row[1]).decode(),
                      expires_at=datetime.fromisoformat(row[2]))

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: Write the QBO adapter**

```python
# ledger_sense/writeback/qbo.py
"""QuickBooks Online adapter. Sandbox in weeks 5-12.

PLATFORM REALITY: QBO Bills have no draft state. v1 therefore creates the Bill
UNPAID, stamped AWAITING REVIEW, and NEVER creates a BillPayment. The human
approves by paying it inside QBO. That is a true sentence to tell a client, and
a better one than a claim about a draft state the platform does not have.

CRASH SAFETY: the idempotency key is stamped into DocNumber BEFORE the post, so
a retry that finds a `pending` record can query QBO for the key and adopt the
bill that already landed rather than creating a second one.
"""

from datetime import date, datetime, timezone
from typing import Protocol

from ledger_sense.writeback.idempotency import IdempotencyLedger
from ledger_sense.writeback.interface import (
    ClosedPeriodError, ReversalResult, WriteRequest, WriteResult,
)

AWAITING_REVIEW = (
    "AWAITING REVIEW — Ledger Sense. Approve by paying this bill in QuickBooks."
)


class QBOClient(Protocol):
    def post_bill(self, payload: dict) -> dict: ...
    def find_bill_by_docnumber(self, doc_number: str) -> dict | None: ...
    def get_book_close_date(self) -> date | None: ...
    def delete_bill(self, remote_id: str) -> None: ...


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

    def create_unapproved_bill(self, request: WriteRequest) -> WriteResult:
        # The closed-period check runs BEFORE anything is recorded or posted.
        close_date = self._client.get_book_close_date()
        if close_date is not None and request.txn_date <= close_date:
            raise ClosedPeriodError(
                f"the books are closed through {close_date.isoformat()}; this "
                f"transaction is dated {request.txn_date.isoformat()}. Ask the "
                f"client to reopen the period or re-date the bill — Ledger Sense "
                f"will not write into a closed period.")

        record = self._ledger.begin(request.idempotency_key, adapter=self.name)

        if record.state == "committed":
            return WriteResult(
                remote_id=record.remote_id or "", adapter=self.name,
                idempotency_key=request.idempotency_key,
                created_at=record.committed_at or record.attempted_at,
                was_replay=True)

        # `pending` means a prior attempt may or may not have landed. Go and look.
        existing = self._client.find_bill_by_docnumber(request.idempotency_key)
        if existing is not None:
            self._ledger.commit(request.idempotency_key, remote_id=existing["Id"],
                                adapter=self.name)
            return WriteResult(remote_id=existing["Id"], adapter=self.name,
                               idempotency_key=request.idempotency_key,
                               created_at=datetime.now(timezone.utc), was_replay=True)

        payload = {
            "DocNumber": request.idempotency_key,
            "PrivateNote": f"{AWAITING_REVIEW} {request.memo}",
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
        response = self._client.post_bill(payload)
        self._ledger.commit(request.idempotency_key, remote_id=response["Id"],
                            adapter=self.name)
        return WriteResult(remote_id=response["Id"], adapter=self.name,
                           idempotency_key=request.idempotency_key,
                           created_at=datetime.now(timezone.utc), was_replay=False)

    def reverse(self, remote_id: str) -> ReversalResult:
        self._client.delete_bill(remote_id)
        return ReversalResult(remote_id=remote_id,
                              reversed_at=datetime.now(timezone.utc), method="deleted")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/writeback/test_tokens.py tests/writeback/test_qbo.py -v`
Expected: PASS, 14 tests — including
`test_force_failing_mid_write_then_retrying_creates_exactly_one_bill`.

- [ ] **Step 6: Commit**

```bash
git add ledger_sense/writeback/tokens.py ledger_sense/writeback/qbo.py tests/writeback/test_tokens.py tests/writeback/test_qbo.py
git commit -m "feat(writeback): QBO adapter, unpaid bills only, crash-safe idempotency"
```

---

### Task 37: Sandbox cassettes and the nightly live smoke test

Tasks 34–36 run against a fake. This task proves the fake matches the real sandbox.

**Files:**
- Modify: `ledger_sense/writeback/qbo.py` (add `HttpQBOClient`)
- Create: `tests/conftest.py` (add the `sandbox_client` fixture)
- Create: `tests/writeback/test_qbo_sandbox.py`
- Create: `tests/writeback/cassettes/` (recorded, committed, token-free)
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `QBOAdapter`, sandbox credentials from `.env` (never committed).
- Produces: `HttpQBOClient(base_url, realm_id, token_store)` satisfying `QBOClient`; a `vcrpy`-backed contract test; a nightly live job allowed to fail without breaking CI but required to open an issue.

- [ ] **Step 1: Write the failing test**

```python
# tests/writeback/test_qbo_sandbox.py
"""Contract test against the REAL QBO sandbox, replayed from cassettes.

Record once with live sandbox credentials, then replay forever. This is how you
find out the API changed before a client does.

`filter_headers` and `filter_post_data_parameters` keep tokens out of the
committed cassettes — a secret in a fixture is still a secret in the repo.
"""

import pathlib
from datetime import date

import pytest
import vcr

from ledger_sense.domain.money import money
from ledger_sense.writeback.idempotency import IdempotencyLedger
from ledger_sense.writeback.interface import (
    LineItem, WriteRequest, build_idempotency_key,
)
from ledger_sense.writeback.qbo import QBOAdapter

qbo_vcr = vcr.VCR(
    cassette_library_dir="tests/writeback/cassettes",
    record_mode="once",
    filter_headers=["authorization"],
    filter_post_data_parameters=["access_token", "refresh_token", "client_secret"],
    match_on=["method", "scheme", "host", "path"],
)


@pytest.fixture()
def ledger(tmp_path):
    led = IdempotencyLedger(str(tmp_path / "writes.db"))
    yield led
    led.close()


def _request(application_no):
    return WriteRequest(
        idempotency_key=build_idempotency_key("sandbox", "C-SANDBOX", application_no),
        doc_id="doc-sandbox", vendor_ref="1", txn_date=date(2026, 8, 31),
        line_items=[LineItem(description="Sitework", amount=money("100.00"),
                             account_ref="7")],
        memo="sandbox contract test")


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
    assert adapter.reverse(result.remote_id).method == "deleted"
    assert sandbox_client.find_bill_by_docnumber(request.idempotency_key) is None


def test_no_cassette_contains_a_token():
    """A secret in a test fixture is still a secret in the repo."""
    for cassette in pathlib.Path("tests/writeback/cassettes").glob("*.yaml"):
        text = cassette.read_text(encoding="utf-8").lower()
        for marker in ("bearer ", "refresh_token", "client_secret"):
            assert marker not in text, f"{cassette.name} contains {marker!r}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/writeback/test_qbo_sandbox.py -v`
Expected: FAIL — `fixture 'sandbox_client' not found`, and `HttpQBOClient` does not exist.

- [ ] **Step 3: Write the real client**

Add `HttpQBOClient` to `ledger_sense/writeback/qbo.py` — an `httpx`-backed implementation of the
`QBOClient` protocol against `https://sandbox-quickbooks.api.intuit.com/v3/company/{realm}`:

| Method | Call |
|---|---|
| `post_bill` | `POST /bill` |
| `find_bill_by_docnumber` | `GET /query?query=SELECT * FROM Bill WHERE DocNumber = '{key}'` |
| `get_book_close_date` | `GET /preferences`, read `AccountingInfoPrefs.BookCloseDate` |
| `delete_bill` | `POST /bill?operation=delete` with `{Id, SyncToken}` |

Access tokens come from `TokenStore` (Task 36) and are refreshed on `401`. **Never log a token.**

- [ ] **Step 4: Add the fixture**

```python
# tests/conftest.py  (append)
import os

import pytest


@pytest.fixture()
def sandbox_client():
    """Real sandbox client, or skip. A fresh clone replays cassettes without
    credentials; only re-recording needs them."""
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
```

- [ ] **Step 5: Record the cassettes once, then prove replay**

```bash
# With sandbox credentials in .env, record:
uv run pytest tests/writeback/test_qbo_sandbox.py -v

# Then prove a fresh clone replays them with no credentials at all:
env -u QBO_REFRESH_TOKEN -u QBO_CLIENT_SECRET uv run pytest tests/writeback/test_qbo_sandbox.py -v
```

Expected: PASS, 5 tests, both times.

- [ ] **Step 6: Add the nightly live smoke job and commit**

```yaml
  qbo-live-smoke:
    if: github.event_name == 'schedule'
    continue-on-error: true          # allowed to fail; must not break CI
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --all-extras
      - run: uv run pytest tests/writeback/test_qbo_sandbox.py --vcr-record=none -v
        env:
          QBO_CLIENT_ID: ${{ secrets.QBO_CLIENT_ID }}
          QBO_CLIENT_SECRET: ${{ secrets.QBO_CLIENT_SECRET }}
          QBO_REALM_ID: ${{ secrets.QBO_REALM_ID }}
          QBO_REFRESH_TOKEN: ${{ secrets.QBO_REFRESH_TOKEN }}
      - if: failure()
        uses: actions/github-script@v7
        with:
          script: |
            github.rest.issues.create({
              owner: context.repo.owner, repo: context.repo.repo,
              title: 'QBO sandbox smoke test failed',
              body: 'Recorded cassettes may no longer match the live API. Re-record.',
              labels: ['writeback', 'upstream-drift'],
            })
```

```bash
git add ledger_sense/writeback/qbo.py tests/conftest.py tests/writeback/test_qbo_sandbox.py tests/writeback/cassettes/ .github/workflows/ci.yml
git commit -m "test(writeback): sandbox cassettes plus a nightly live smoke that opens an issue"
```

---

### Task 38: Reversal and the audit trail — **DoD 6**

**Files:**
- Create: `ledger_sense/writeback/audit.py`
- Test: `tests/writeback/test_audit.py`

**Interfaces:**
- Consumes: `WriteResult`, `ReversalResult`, `WritebackPort`, `QBOAdapter`.
- Produces:
  - `AuditEntry(id, doc_id, adapter, action, remote_id, idempotency_key, approved_by, occurred_at, detail)`
  - `AuditLog(db_path)` with `record_write`, `record_reversal`, `for_document`, `close` — **append-only, no delete or update method by design**
  - `AuditedAdapter(inner, audit, approved_by)` satisfying `WritebackPort`

- [ ] **Step 1: Write the failing test**

```python
# tests/writeback/test_audit.py
from datetime import date

import pytest

from ledger_sense.domain.money import money
from ledger_sense.writeback.audit import AuditedAdapter, AuditLog
from ledger_sense.writeback.idempotency import IdempotencyLedger
from ledger_sense.writeback.interface import (
    LineItem, WriteRequest, build_idempotency_key,
)
from ledger_sense.writeback.qbo import QBOAdapter
from tests.writeback.test_qbo import FakeQBOClient


def _request():
    return WriteRequest(
        idempotency_key=build_idempotency_key("abc123", "C-1", 2),
        doc_id="doc-1", vendor_ref="V-9", txn_date=date(2026, 8, 31),
        line_items=[LineItem(description="Sitework", amount=money("1000.00"),
                             account_ref="54")],
        memo="Application 2")


@pytest.fixture()
def audited(tmp_path):
    ledger = IdempotencyLedger(str(tmp_path / "writes.db"))
    audit = AuditLog(str(tmp_path / "audit.db"))
    client = FakeQBOClient()
    adapter = AuditedAdapter(QBOAdapter(client, ledger, realm_id="r-1"),
                             audit=audit, approved_by="A. Bookkeeper")
    yield adapter, audit, client
    ledger.close()
    audit.close()


def test_a_write_records_document_approver_and_remote_id(audited):
    adapter, audit, _client = audited
    result = adapter.create_unapproved_bill(_request())
    entry = audit.for_document("doc-1")[0]
    assert entry.action == "write"
    assert entry.approved_by == "A. Bookkeeper"
    assert entry.remote_id == result.remote_id


def test_a_reversal_is_recorded_against_the_same_document(audited):
    adapter, audit, _client = audited
    result = adapter.create_unapproved_bill(_request())
    adapter.reverse(result.remote_id)
    assert [e.action for e in audit.for_document("doc-1")] == ["write", "reversal"]


def test_reversal_removes_the_bill_from_the_accounting_system(audited):
    adapter, _audit, client = audited
    result = adapter.create_unapproved_bill(_request())
    adapter.reverse(result.remote_id)
    assert result.remote_id not in client.bills


def test_reversal_restores_the_prior_state_exactly(audited):
    adapter, _audit, client = audited
    before = dict(client.bills)
    result = adapter.create_unapproved_bill(_request())
    adapter.reverse(result.remote_id)
    assert client.bills == before


def test_a_write_without_a_named_approver_is_refused(tmp_path):
    ledger = IdempotencyLedger(str(tmp_path / "w.db"))
    audit = AuditLog(str(tmp_path / "a.db"))
    adapter = AuditedAdapter(QBOAdapter(FakeQBOClient(), ledger, realm_id="r-1"),
                             audit=audit, approved_by="")
    with pytest.raises(ValueError, match="approved_by"):
        adapter.create_unapproved_bill(_request())
    ledger.close()
    audit.close()


def test_the_audit_trail_has_no_delete_or_update_method(audited):
    """An audit log you can edit is not an audit log."""
    _adapter, audit, _client = audited
    for forbidden in ("delete_entry", "update_entry", "purge", "clear"):
        assert not hasattr(audit, forbidden)


def test_a_replayed_write_is_audited_as_a_replay_not_a_second_write(audited):
    adapter, audit, _client = audited
    adapter.create_unapproved_bill(_request())
    adapter.create_unapproved_bill(_request())
    details = [e.detail for e in audit.for_document("doc-1")]
    assert len(details) == 2
    assert "replay" in details[1].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/writeback/test_audit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.writeback.audit'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/writeback/audit.py
"""Audit trail for everything written to a client's books.

What was written, from which document, on whose approval, with a reversal path.

Append-only: there is deliberately no delete or update method. An audit log you
can edit is not an audit log.
"""

import sqlite3
import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from ledger_sense.writeback.interface import (
    ReversalResult, WriteRequest, WriteResult, WritebackPort,
)

Action = Literal["write", "reversal"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id              TEXT PRIMARY KEY,
    doc_id          TEXT NOT NULL,
    adapter         TEXT NOT NULL,
    action          TEXT NOT NULL,
    remote_id       TEXT NOT NULL,
    idempotency_key TEXT,
    approved_by     TEXT NOT NULL,
    occurred_at     TEXT NOT NULL,
    detail          TEXT NOT NULL
)
"""


class AuditEntry(BaseModel):
    id: str
    doc_id: str
    adapter: str
    action: Action
    remote_id: str
    idempotency_key: str | None
    approved_by: str
    occurred_at: datetime
    detail: str


class AuditLog:
    """Append-only. No delete, no update — deliberately."""

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def _append(self, entry: AuditEntry) -> None:
        self._conn.execute(
            "INSERT INTO audit VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry.id, entry.doc_id, entry.adapter, entry.action, entry.remote_id,
             entry.idempotency_key, entry.approved_by,
             entry.occurred_at.isoformat(), entry.detail))
        self._conn.commit()

    def record_write(self, doc_id: str, adapter: str, result: WriteResult,
                     approved_by: str) -> None:
        self._append(AuditEntry(
            id=str(uuid.uuid4()), doc_id=doc_id, adapter=adapter, action="write",
            remote_id=result.remote_id, idempotency_key=result.idempotency_key,
            approved_by=approved_by, occurred_at=result.created_at,
            detail="replay of an earlier write" if result.was_replay
                   else "unapproved bill created, awaiting review"))

    def record_reversal(self, doc_id: str, adapter: str, result: ReversalResult,
                        approved_by: str) -> None:
        self._append(AuditEntry(
            id=str(uuid.uuid4()), doc_id=doc_id, adapter=adapter, action="reversal",
            remote_id=result.remote_id, idempotency_key=None,
            approved_by=approved_by, occurred_at=result.reversed_at,
            detail=f"reversed by {result.method}"))

    def for_document(self, doc_id: str) -> list[AuditEntry]:
        rows = self._conn.execute(
            "SELECT id, doc_id, adapter, action, remote_id, idempotency_key, "
            "approved_by, occurred_at, detail FROM audit WHERE doc_id = ? "
            "ORDER BY occurred_at, rowid", (doc_id,)).fetchall()
        return [AuditEntry(
            id=r[0], doc_id=r[1], adapter=r[2], action=r[3], remote_id=r[4],
            idempotency_key=r[5], approved_by=r[6],
            occurred_at=datetime.fromisoformat(r[7]), detail=r[8]) for r in rows]

    def close(self) -> None:
        self._conn.close()


class AuditedAdapter:
    """Wraps any WritebackPort so nothing reaches a client's books unaudited."""

    def __init__(self, inner: WritebackPort, audit: AuditLog,
                 approved_by: str) -> None:
        self._inner = inner
        self._audit = audit
        self._approved_by = approved_by
        self.name = inner.name
        self._doc_by_remote: dict[str, str] = {}

    def is_period_closed(self, txn_date: date) -> bool:
        return self._inner.is_period_closed(txn_date)

    def create_unapproved_bill(self, request: WriteRequest) -> WriteResult:
        if not self._approved_by:
            raise ValueError(
                "a write requires approved_by — nothing reaches a client's books "
                "without a named human on the record")
        result = self._inner.create_unapproved_bill(request)
        self._doc_by_remote[result.remote_id] = request.doc_id
        self._audit.record_write(request.doc_id, self.name, result, self._approved_by)
        return result

    def reverse(self, remote_id: str) -> ReversalResult:
        result = self._inner.reverse(remote_id)
        self._audit.record_reversal(self._doc_by_remote.get(remote_id, "unknown"),
                                    self.name, result, self._approved_by)
        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/writeback/ -v`
Expected: PASS, all writeback tests. **DoD 6.**

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/writeback/audit.py tests/writeback/test_audit.py
git commit -m "feat(writeback): append-only audit trail and one-click clean reversal"
```

---

### Task 39: CSV / IIF export — a peer adapter

**Not a fallback.** A first-class adapter satisfying the same protocol, which is what makes B12 survivable: the product is useful on day one with no API approval at all.

**Files:**
- Create: `ledger_sense/writeback/export.py`
- Create: `tests/writeback/goldens/bills.csv`, `tests/writeback/goldens/bills.iif`
- Test: `tests/writeback/test_export.py`

**Interfaces:**
- Consumes: `WriteRequest`, `WriteResult`, `ReversalResult`.
- Produces: `ExportAdapter(out_dir, fmt: Literal["csv", "iif"])` satisfying `WritebackPort`. `is_period_closed` always returns `False` — there is no remote book to close. `reverse` removes every row carrying the key, preserving the header.

- [ ] **Step 1: Write the failing test**

```python
# tests/writeback/test_export.py
import pathlib
from datetime import date

from ledger_sense.domain.money import money
from ledger_sense.writeback.export import ExportAdapter
from ledger_sense.writeback.interface import (
    LineItem, WriteRequest, WritebackPort, build_idempotency_key,
)

GOLDENS = pathlib.Path("tests/writeback/goldens")


def _request(application_no=2):
    return WriteRequest(
        idempotency_key=build_idempotency_key("abc123", "C-1", application_no),
        doc_id="doc-1", vendor_ref="Acme Steel Company", txn_date=date(2026, 8, 31),
        line_items=[
            LineItem(description="Sitework", amount=money("1000.00"), account_ref="54"),
            LineItem(description="Concrete", amount=money("2500.50"), account_ref="55"),
        ],
        memo="Application 2")


def test_the_export_adapter_satisfies_the_same_protocol(tmp_path):
    assert isinstance(ExportAdapter(out_dir=str(tmp_path), fmt="csv"), WritebackPort)


def test_csv_output_matches_the_golden(tmp_path):
    ExportAdapter(out_dir=str(tmp_path), fmt="csv").create_unapproved_bill(_request())
    assert (tmp_path / "bills.csv").read_text(encoding="utf-8") == \
        (GOLDENS / "bills.csv").read_text(encoding="utf-8")


def test_iif_output_matches_the_golden(tmp_path):
    ExportAdapter(out_dir=str(tmp_path), fmt="iif").create_unapproved_bill(_request())
    assert (tmp_path / "bills.iif").read_text(encoding="utf-8") == \
        (GOLDENS / "bills.iif").read_text(encoding="utf-8")


def test_amounts_are_written_as_decimal_strings_never_floats(tmp_path):
    ExportAdapter(out_dir=str(tmp_path), fmt="csv").create_unapproved_bill(_request())
    text = (tmp_path / "bills.csv").read_text(encoding="utf-8")
    assert "2500.50" in text
    assert "2500.5," not in text and "2500.5\n" not in text


def test_writing_the_same_key_twice_does_not_duplicate_a_row(tmp_path):
    adapter = ExportAdapter(out_dir=str(tmp_path), fmt="csv")
    adapter.create_unapproved_bill(_request())
    result = adapter.create_unapproved_bill(_request())
    rows = (tmp_path / "bills.csv").read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 3          # header + two line items, written once
    assert result.was_replay is True


def test_export_never_reports_a_closed_period(tmp_path):
    """There is no remote book to close. Say so rather than guessing."""
    adapter = ExportAdapter(out_dir=str(tmp_path), fmt="csv")
    assert adapter.is_period_closed(date(2020, 1, 1)) is False


def test_reversal_removes_the_exported_rows(tmp_path):
    adapter = ExportAdapter(out_dir=str(tmp_path), fmt="csv")
    result = adapter.create_unapproved_bill(_request())
    adapter.reverse(result.remote_id)
    rows = (tmp_path / "bills.csv").read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 1          # header only
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/writeback/test_export.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.writeback.export'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/writeback/export.py
"""CSV / IIF export. A PEER adapter, not a fallback.

B12: accounting-platform API access is a gate we do not control. This adapter
means the product is useful on day one with no API approval at all, and it
satisfies exactly the same protocol as the QBO adapter, so nothing above the
port knows the difference.
"""

import csv
import io
import pathlib
from datetime import date, datetime, timezone
from typing import Literal

from ledger_sense.writeback.interface import (
    ReversalResult, WriteRequest, WriteResult,
)

CSV_HEADER = ["idempotency_key", "doc_id", "vendor", "txn_date", "account_ref",
              "description", "amount", "memo", "status"]
IIF_HEADER = "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\tMEMO\n"
STATUS = "AWAITING REVIEW"


class ExportAdapter:
    name = "export"

    def __init__(self, out_dir: str, fmt: Literal["csv", "iif"] = "csv") -> None:
        self._dir = pathlib.Path(out_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._fmt = fmt
        self._path = self._dir / f"bills.{fmt}"

    def is_period_closed(self, txn_date: date) -> bool:
        """There is no remote book to close."""
        return False

    def _existing_keys(self) -> set[str]:
        if not self._path.exists():
            return set()
        text = self._path.read_text(encoding="utf-8")
        if self._fmt == "csv":
            return {row["idempotency_key"] for row in csv.DictReader(io.StringIO(text))}
        return {line.split("\t")[6] for line in text.splitlines()
                if line.startswith("TRNS\t")}

    def create_unapproved_bill(self, request: WriteRequest) -> WriteResult:
        if request.idempotency_key in self._existing_keys():
            return WriteResult(remote_id=request.idempotency_key, adapter=self.name,
                               idempotency_key=request.idempotency_key,
                               created_at=datetime.now(timezone.utc), was_replay=True)

        is_new = not self._path.exists()
        if self._fmt == "csv":
            with self._path.open("a", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                if is_new:
                    writer.writerow(CSV_HEADER)
                for item in request.line_items:
                    writer.writerow([
                        request.idempotency_key, request.doc_id, request.vendor_ref,
                        request.txn_date.isoformat(), item.account_ref,
                        item.description, str(item.amount), request.memo, STATUS])
        else:
            with self._path.open("a", encoding="utf-8") as handle:
                if is_new:
                    handle.write(IIF_HEADER)
                for item in request.line_items:
                    handle.write("\t".join([
                        "TRNS", "BILL", request.txn_date.isoformat(),
                        item.account_ref, request.vendor_ref, str(item.amount),
                        request.idempotency_key,
                        f"{STATUS} — {item.description}"]) + "\n")

        return WriteResult(remote_id=request.idempotency_key, adapter=self.name,
                           idempotency_key=request.idempotency_key,
                           created_at=datetime.now(timezone.utc), was_replay=False)

    def reverse(self, remote_id: str) -> ReversalResult:
        """Remove every row carrying this key, preserving the header."""
        lines = self._path.read_text(encoding="utf-8").splitlines(keepends=True)
        kept = lines[:1] + [ln for ln in lines[1:] if remote_id not in ln]
        self._path.write_text("".join(kept), encoding="utf-8")
        return ReversalResult(remote_id=remote_id,
                              reversed_at=datetime.now(timezone.utc), method="deleted")
```

- [ ] **Step 4: Generate and READ the goldens**

Run the adapter once into a scratch directory, **read both files by eye** — confirm the amounts,
dates and status text are right — then copy them into `tests/writeback/goldens/`.

A golden committed without being read is a bug frozen in amber.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/writeback/test_export.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 6: Commit**

```bash
git add ledger_sense/writeback/export.py tests/writeback/test_export.py tests/writeback/goldens/
git commit -m "feat(writeback): CSV/IIF export as a peer adapter with golden-file tests"
```

---

## Phase 5 exit criteria

- [ ] `uv run pytest tests/writeback -v` green
- [ ] `test_force_failing_mid_write_then_retrying_creates_exactly_one_bill` passes — **the test this phase exists for**
- [ ] `test_no_code_path_calls_post_bill_payment` passes — v1 never posts an approved transaction
- [ ] Closed-period refusal proven, and proven to happen **before** anything is recorded or posted
- [ ] Reversal restores prior state exactly; the audit trail records document, approver and timestamp
- [ ] Tokens encrypted at rest, verified by reading the raw database file
- [ ] No cassette contains a bearer token, refresh token or client secret
- [ ] Cassettes replay with credentials removed from the environment
- [ ] `uv run mypy --strict ledger_sense` green
- [ ] **DoD 6 satisfied** — QBO draft writeback against sandbox with idempotency, closed-period refusal and reversal proven by test

**Then Phase 6** — routing, calibration and close-out. That phase decides what the product is allowed to claim.
