# Ledger Sense — Phase 0: Platform (Week 5, concurrent with Tasks 1–13)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the platform the other five phases assume exists — application scaffold, authenticated
multi-tenant access, hardened document intake, one shared persistence layer, encryption and retention, and the
three CI gates the hard constraints mandate.

**Why this document exists.** The audit (`2026-08-29-REVISIONS.md` §8) found eleven items that the spec or the
definition of done requires and that no task implements. Five of them — the frontend scaffold, the FastAPI app
object, Postgres and migrations, the repository files Task 48 *tests but nothing creates*, and E2E seeding — are
roughly a week of work the eight-week plan does not contain. The other six are the security posture of a product
that becomes a data processor on document one.

**Sequencing.** P1 and P2 land in week 5 alongside Task 1. P3–P5 land before Task 13 (any external document).
P6 and P7 land before Phase 4 begins. Nothing here blocks Tasks 2–12, which touch no document and no network.

**Tech Stack:** FastAPI, Postgres 16 + `alembic`, `argon2-cffi`, `cryptography`, Next.js 15 + Tailwind v4,
`pdf.js`, Playwright, Vitest, `gitleaks`, `pip-audit`, `cyclonedx-py`.

**Spec:** `2025-03-11-ledger-sense-design.md` §4.1, §4.2, §8, §12 · **Audit:** `2026-08-29-REVISIONS.md`
**Parent plan:** `2025-03-11-ledger-sense-weeks-5-12.md` — its Global Constraints apply here in full.

---

## The three rules this phase adds to the Global Constraints

1. **No route without a session and a tenant.** Every `APIRouter` in this repository is registered through
   `register()`, which applies `require_session`. A router mounted any other way fails a test that walks the
   app's route table.
2. **No document byte is parsed outside the hardened path.** `extract/` is reached through
   `ingest/intake.py` only, and `intake` applies the caps in P3.
3. **One connection factory.** No module calls `sqlite3.connect` or `psycopg.connect` directly. A test greps
   for both.

---

### Task P1: Repository skeleton and the three mandated CI gates

Closes **G-4** and **G-8**. Task 48 tests `docker-compose.yml`, `.env.example`, `LICENSE` and `README.md`;
without this task those tests fail against files that were never written.

**Files:**
- Create: `pyproject.toml` (body — Task 1 creates the file, this fills it), `LICENSE`, `README.md`,
  `SECURITY.md`, `CONTRIBUTING.md`, `.env.example`, `Dockerfile`, `docker-compose.yml`,
  `.pre-commit-config.yaml`, `ledger_sense/notice.py`
- Modify: `.github/workflows/ci.yml`
- Test: `tests/test_platform_skeleton.py`

**Interfaces:**
- Produces: `ledger_sense/notice.py::NON_AFFILIATION_NOTICE` — **the single source of the notice string**,
  imported by `scripts/aia_guard.py`, `tests/test_repo_closeout.py` and the UI footer. F-55 exists because two
  documents each hardcoded their own wording and each asserted it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_platform_skeleton.py
import pathlib

import yaml

from ledger_sense.notice import NON_AFFILIATION_NOTICE

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_the_notice_has_exactly_one_definition():
    """F-55: two documents each hardcoded their own wording; both asserted it."""
    hits = []
    for path in ROOT.rglob("*.py"):
        if ".venv" in path.parts or path.name == "notice.py":
            continue
        if "not developed, endorsed, approved" in path.read_text(encoding="utf-8"):
            hits.append(str(path))
    assert hits == [], f"notice text duplicated outside notice.py: {hits}"


def test_compose_defines_both_client_and_demo_profiles():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    profiles = {p for svc in compose["services"].values() for p in svc.get("profiles", [])}
    assert {"client", "demo"} <= profiles


def test_the_client_profile_uses_no_third_party_processor():
    """Spec §2.1 Q6: CLIENT = compose-only Postgres, no third-party processor ever."""
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    for name, svc in compose["services"].items():
        if "client" in svc.get("profiles", []):
            image = svc.get("image", "")
            assert not any(h in image for h in ("supabase", "planetscale", "neon")), name


def test_env_example_contains_no_real_secret():
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        _, _, value = line.partition("=")
        assert value.strip() in {"", "changeme"} or value.strip().startswith("<"), line


def test_gitleaks_is_wired_into_pre_commit_and_ci():
    """Hard constraint 00 §3.4. Named in the Global Constraints, implemented nowhere."""
    assert "gitleaks" in (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "gitleaks" in (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")


def test_dependency_audit_and_sbom_jobs_exist():
    """00 §8. pip-audit, npm audit, cyclonedx — none appeared in any task."""
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    for tool in ("pip-audit", "npm audit", "cyclonedx"):
        assert tool in ci, f"{tool} missing from CI"


def test_gitleaks_scans_the_recorded_cassettes():
    """F-28: VCR does not scrub response bodies, and that is where the token lands."""
    assert "tests/writeback/cassettes" in (
        ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")


def test_the_container_runs_as_a_non_root_user():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER " in dockerfile
    assert "USER root" not in "\n".join(dockerfile.splitlines()[-5:])


def test_no_module_opens_a_database_connection_directly():
    """Rule 3. One connection factory; F-19 is what happens without it."""
    offenders = []
    for path in (ROOT / "ledger_sense").rglob("*.py"):
        if path.parts[-2:] == ("store", "sqlite.py") or path.name == "session.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "sqlite3.connect" in text or "psycopg.connect" in text:
            offenders.append(str(path))
    assert offenders == [], f"direct connection outside the factory: {offenders}"


def test_the_licence_is_apache_2_0():
    assert "Apache License" in (ROOT / "LICENSE").read_text(encoding="utf-8")


def test_the_readme_carries_the_notice_from_the_single_source():
    assert NON_AFFILIATION_NOTICE in (ROOT / "README.md").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_platform_skeleton.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.notice'`, then missing files.

- [ ] **Step 3: Write the notice module**

```python
# ledger_sense/notice.py
"""The non-affiliation notice, defined once.

Spec §11 requires it in the README, the docs and the product UI. The audit found
two plan documents each hardcoding their own wording and each asserting it,
which is two tests that cannot both pass (F-55). One string, imported.
"""

NON_AFFILIATION_NOTICE = (
    "Not developed, endorsed, approved, sponsored by, or affiliated with the "
    "American Institute of Architects. AIA®, G702® and G703® are registered "
    "trademarks of the American Institute of Architects. References describe "
    "format compatibility only."
)
```

- [ ] **Step 4: Write the compose file, both profiles**

`docker-compose.yml` — one `app`, one `postgres`, one `web`. Two profiles:

| Profile | Data class | Postgres | Notes |
|---|---|---|---|
| `client` | `CLIENT_CONFIDENTIAL` | compose-only, named volume, never a hosted service | Spec §2.1 Q6: *"your documents never leave your infrastructure"* must stay literally true |
| `demo` | `SYNTHETIC` only | ephemeral, `tmpfs` | Hostable anywhere; mounts no client volume |

The `app` service runs non-root with `read_only: true` plus a `tmpfs` scratch, `mem_limit`, `cpus` and
`security_opt: [no-new-privileges:true]` — `00` §8 applied to the service that parses hostile PDFs.

- [ ] **Step 5: Write the CI jobs**

```yaml
  secrets:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: gitleaks/gitleaks-action@v2
      # F-28: VCR scrubs request headers and bodies, never responses.
      - run: gitleaks detect --no-git --source tests/writeback/cassettes --redact

  supply-chain:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --all-extras
      - run: uv run pip-audit --strict
      - run: npm audit --audit-level=high
      - run: uv run cyclonedx-py environment -o sbom.json
      - uses: actions/upload-artifact@v4
        with: { name: sbom, path: sbom.json }
```

- [ ] **Step 6: Run test to verify it passes, then commit**

Run: `uv run pytest tests/test_platform_skeleton.py -v` → PASS, 11 tests.

```bash
git add pyproject.toml LICENSE README.md SECURITY.md CONTRIBUTING.md .env.example \
        Dockerfile docker-compose.yml .pre-commit-config.yaml \
        ledger_sense/notice.py .github/workflows/ci.yml tests/test_platform_skeleton.py
git commit -m "chore(platform): repo skeleton, compose profiles, gitleaks/pip-audit/SBOM gates"
```

---

### Task P2: Sessions, tenancy and the ownership check — **closes F-25, F-18, G-2**

**The audit's most serious finding.** Every route defined in Phases 2, 4 and 6 is unauthenticated, and
`GET /api/reconciliation/{doc_id}` takes an identifier straight from the caller with no ownership check. This is
a product holding pay applications, vendor bank details and OAuth tokens for a client's QuickBooks company.

**Files:**
- Create: `ledger_sense/api/app.py`, `ledger_sense/api/deps.py`, `ledger_sense/auth/session.py`,
  `ledger_sense/auth/models.py`, `migrations/versions/0001_tenancy.py`
- Test: `tests/api/test_auth.py`

**Interfaces:**
- Produces:
  - `build_app() -> FastAPI` and `app` — the application object Phase 6 Task 43 imports and no task creates
  - `require_session(request) -> Session`; `Session(user_id, tenant_id, expires_at)`
  - `owned(session, resource_tenant_id) -> None` — raises `HTTPException(404)`, **never 403**, so a probe
    cannot distinguish "exists elsewhere" from "does not exist"
  - `register(app, router)` — the only sanctioned way to mount a router

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_auth.py
import pytest
from fastapi.testclient import TestClient

from ledger_sense.api.app import app

client = TestClient(app)

PROTECTED_PREFIXES = ("/api/baseline", "/api/review", "/api/reconciliation",
                      "/api/inbox", "/api/vendors", "/api/metrics")


def test_every_api_route_requires_a_session():
    """The test that would have caught F-25 the day the first router landed."""
    unprotected = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/") or path.startswith(("/api/health", "/api/auth")):
            continue
        names = {d.dependency.__name__ for d in getattr(route, "dependencies", [])
                 if getattr(d, "dependency", None)}
        if "require_session" not in names:
            unprotected.append(path)
    assert unprotected == [], f"routes without require_session: {unprotected}"


@pytest.mark.parametrize("prefix", PROTECTED_PREFIXES)
def test_an_anonymous_request_is_refused(prefix):
    assert client.get(f"{prefix}/").status_code == 401


def test_a_document_from_another_tenant_is_not_found_rather_than_forbidden(two_tenants):
    """404, never 403. A 403 confirms the resource exists, which is the leak."""
    a, b = two_tenants
    doc_id = a.create_document()
    assert b.get(f"/api/reconciliation/{doc_id}").status_code == 404


def test_the_owner_can_read_their_own_document(two_tenants):
    a, _b = two_tenants
    assert a.get(f"/api/reconciliation/{a.create_document()}").status_code == 200


def test_a_review_action_cannot_be_taken_across_tenants(two_tenants):
    a, b = two_tenants
    exc_id = a.seed_exception()
    assert b.post(f"/api/review/exceptions/{exc_id}/accept").status_code == 404


def test_the_profile_cache_cannot_be_opened_without_a_tenant():
    """F-18: geometry-only prevents data leakage, not influence."""
    from ledger_sense.extract.profiles import ProfileCache
    with pytest.raises(TypeError):
        ProfileCache(db_path=":memory:")


def test_a_session_expires():
    token = client.post("/api/auth/login",
                        json={"email": "a@example.test",
                              "password": "changeme-in-test"}).json()["token"]
    from ledger_sense.auth.session import expire_now
    expire_now(token)
    assert client.get("/api/inbox/", cookies={"session": token}).status_code == 401


def test_passwords_are_stored_as_argon2_hashes_not_plaintext():
    from ledger_sense.auth.models import hash_password, verify_password
    digest = hash_password("correct horse battery staple")
    assert digest.startswith("$argon2")
    assert verify_password("correct horse battery staple", digest)
    assert not verify_password("wrong", digest)


def test_the_login_endpoint_is_rate_limited():
    codes = [client.post("/api/auth/login",
                         json={"email": "a@example.test", "password": "no"}).status_code
             for _ in range(25)]
    assert 429 in codes


def test_the_session_cookie_is_httponly_samesite_strict_and_secure():
    resp = client.post("/api/auth/login", json={"email": "a@example.test",
                                                "password": "changeme-in-test"})
    header = resp.headers["set-cookie"].lower()
    for flag in ("httponly", "samesite=strict", "secure"):
        assert flag in header, flag


def test_health_is_the_only_unauthenticated_non_auth_route():
    assert client.get("/api/health").status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.api.app'`

- [ ] **Step 3: Implement**

```python
# ledger_sense/api/deps.py
"""Session and ownership dependencies.

`register` is the only sanctioned way to mount a router, and it applies
`require_session` unconditionally. A router mounted any other way fails
test_every_api_route_requires_a_session — which is how F-25 gets caught the day
it recurs rather than at week 12.

`owned` raises 404, never 403. A 403 tells a prober the resource exists.
"""

from fastapi import Depends, FastAPI, HTTPException, Request

from ledger_sense.auth.session import Session, load_session


def require_session(request: Request) -> Session:
    token = request.cookies.get("session")
    session = load_session(token) if token else None
    if session is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return session


def owned(session: Session, resource_tenant_id: str | None) -> None:
    if resource_tenant_id is None or resource_tenant_id != session.tenant_id:
        raise HTTPException(status_code=404, detail="not found")


def register(app: FastAPI, router) -> None:
    app.include_router(router, dependencies=[Depends(require_session)])
```

`migrations/versions/0001_tenancy.py` adds `tenant_id TEXT NOT NULL` to **every** table holding a document, a
claim, an exception, a vendor, a write record, an audit entry, a label, a curve or a profile, each with an index
on `(tenant_id, id)`. There is no table without one.

Sessions: opaque 256-bit token, `argon2` password hashing, HttpOnly + SameSite=Strict + Secure cookie, sliding
8-hour expiry, 10-per-minute limiter on `/api/auth/login`.

- [ ] **Step 4: Run test to verify it passes, then commit**

Run: `uv run pytest tests/api/test_auth.py -v` → PASS, 16 tests.

```bash
git add ledger_sense/api/app.py ledger_sense/api/deps.py ledger_sense/auth/ migrations/ tests/api/test_auth.py
git commit -m "feat(platform): sessions, tenant scoping and a 404-not-403 ownership check on every route"
```

---

### Task P3: Hardened document intake — **closes F-26, G-6**

`00` §8 requires resource limits on anything executing untrusted input. Extraction is the one place the product
touches hostile bytes, and it is in-process with no caps.

**Files:**
- Modify: `ledger_sense/ingest/intake.py`
- Create: `ledger_sense/ingest/limits.py`, `scripts/make_malformed.py`
- Create: `tests/ingest/fixtures/malformed/` (20 files, all generated — no third-party document enters the repo)
- Test: `tests/ingest/test_limits.py`

**Interfaces:**
- Produces: `Limits(max_bytes=25_000_000, max_pages=200, timeout_seconds=60, max_memory_mb=512)`,
  `RejectedDocument(reason)`, `parse_within_limits(payload, limits) -> ParsedDocument | RejectedDocument`

- [ ] **Step 1: Write the failing test**

```python
# tests/ingest/test_limits.py
import pathlib

import pytest

from ledger_sense.ingest.limits import Limits, RejectedDocument, parse_within_limits

MALFORMED = pathlib.Path("tests/ingest/fixtures/malformed")


def test_a_non_pdf_payload_is_rejected_on_its_magic_bytes_not_its_extension():
    assert isinstance(parse_within_limits(b"MZ\x90\x00", Limits()), RejectedDocument)


def test_an_oversized_payload_is_rejected_before_parsing():
    """Rejected on length, so a 2GB upload never reaches pdfminer."""
    result = parse_within_limits(b"%PDF-1.4" + b"\x00" * 26_000_000, Limits())
    assert isinstance(result, RejectedDocument) and "size" in result.reason


def test_a_page_bomb_is_rejected_rather_than_parsed():
    result = parse_within_limits((MALFORMED / "many_pages.pdf").read_bytes(), Limits())
    assert isinstance(result, RejectedDocument) and "page" in result.reason


def test_a_decompression_bomb_does_not_exhaust_memory():
    result = parse_within_limits((MALFORMED / "zip_bomb.pdf").read_bytes(),
                                 Limits(max_memory_mb=128))
    assert isinstance(result, RejectedDocument)


@pytest.mark.parametrize("name", sorted(p.name for p in MALFORMED.glob("*.pdf")))
def test_no_malformed_fixture_hangs_or_raises_an_unhandled_exception(name):
    """Twenty malformed documents. Every one returns; none hangs; none escapes."""
    assert parse_within_limits((MALFORMED / name).read_bytes(),
                               Limits(timeout_seconds=10)) is not None


def test_a_wellformed_document_still_parses():
    from ledger_sense.corpus.generate import generate
    _c, _a, pdf = generate(seed=1, error="clean")
    assert not isinstance(parse_within_limits(pdf, Limits()), RejectedDocument)


def test_a_rejection_is_surfaced_to_the_reviewer_not_swallowed():
    """B11: route to review with an honest 'cannot extract' rather than guess."""
    result = parse_within_limits(b"%PDF-1.4 truncated", Limits())
    assert isinstance(result, RejectedDocument) and result.reason


def test_intake_is_the_only_caller_of_the_parser():
    """Rule 2. A parse outside intake is a parse without the caps."""
    import ast
    import pathlib as pl
    offenders = []
    for path in pl.Path("ledger_sense").rglob("*.py"):
        if path.name in {"intake.py", "limits.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(
                    node.func, "id", "") == "parse_within_limits":
                offenders.append(f"{path}:{node.lineno}")
    assert offenders == [], offenders
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/test_limits.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.ingest.limits'`

- [ ] **Step 3: Implement**

Parsing runs in a `multiprocessing` child with `resource.setrlimit(RLIMIT_AS, …)` and a hard join timeout; the
parent kills the child on expiry and returns `RejectedDocument`. Byte length and the `%PDF` magic are checked in
the parent, before a child is spawned. Page count is read from the trailer before any content stream is decoded.

`scripts/make_malformed.py` generates the twenty fixtures deterministically — truncated trailer, cyclic object
graph, 100k-page catalogue, nested `/Filter` chain, invalid xref, 2GB declared stream length, and so on — so no
third-party document enters the repo, the same discipline the AIA guard enforces.

- [ ] **Step 4: Run and commit**

Run: `uv run pytest tests/ingest/test_limits.py -v` → PASS, 27 tests (7 plus 20 parametrised).

```bash
git add ledger_sense/ingest/limits.py ledger_sense/ingest/intake.py scripts/make_malformed.py tests/ingest/
git commit -m "feat(ingest): resource-capped PDF parsing in a killable child process"
```

---

### Task P4: One connection factory — **closes F-19, G-3**

Five classes each open a bare `sqlite3` connection in `__init__` and hold it. FastAPI runs sync handlers in a
threadpool and `sqlite3` defaults to `check_same_thread=True`, so the first cross-thread use raises
`ProgrammingError`. None sets WAL or a busy timeout, so concurrent writers get `database is locked` rather than
waiting.

**Files:**
- Create: `ledger_sense/store/sqlite.py`, `ledger_sense/store/session.py`, `alembic.ini`, `migrations/env.py`
- Modify: `extract/profiles.py`, `writeback/idempotency.py`, `writeback/tokens.py`, `writeback/audit.py`,
  `route/calibration.py`
- Test: `tests/store/test_connection.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/store/test_connection.py
import concurrent.futures

from ledger_sense.store.sqlite import connect


def test_a_connection_is_usable_from_a_worker_thread(tmp_path):
    """The failure every one of the five stores would hit under FastAPI."""
    path = str(tmp_path / "t.db")
    connect(path).execute("CREATE TABLE t (x INTEGER)")

    def write(i):
        conn = connect(path)
        conn.execute("INSERT INTO t VALUES (?)", (i,))
        conn.commit()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(64)))
    assert connect(path).execute("SELECT COUNT(*) FROM t").fetchone()[0] == 64


def test_wal_is_enabled(tmp_path):
    mode = connect(str(tmp_path / "t.db")).execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_a_busy_timeout_is_set(tmp_path):
    assert connect(str(tmp_path / "t.db")).execute(
        "PRAGMA busy_timeout").fetchone()[0] >= 5000


def test_foreign_keys_are_enforced(tmp_path):
    assert connect(str(tmp_path / "t.db")).execute(
        "PRAGMA foreign_keys").fetchone()[0] == 1


def test_each_thread_gets_its_own_connection(tmp_path):
    path = str(tmp_path / "t.db")
    ids: set[int] = set()

    def grab(_):
        ids.add(id(connect(path)))

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(grab, range(4)))
    assert len(ids) > 1
```

- [ ] **Step 2: Run, implement, re-run**

`connect(path)` returns a `threading.local` connection opened with `check_same_thread=False`, then executes
`PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`, `PRAGMA foreign_keys=ON`. All five stores take a
`db_path` and call it; none constructs a connection itself. `store/session.py` is the Postgres equivalent for
the tables migrated in P2, with `alembic` wired.

Expected: PASS, 5 tests, and the five existing store suites still green.

- [ ] **Step 3: Commit**

```bash
git add ledger_sense/store/ alembic.ini migrations/env.py tests/store/ \
        ledger_sense/extract/profiles.py ledger_sense/writeback/ ledger_sense/route/calibration.py
git commit -m "feat(store): one thread-safe WAL connection factory for every store"
```

---

### Task P5: Encryption at rest, retention and deletion — **closes F-30, G-7**

`02` B9 and `00` B7 both require this *before the first document arrives*. Spec §12.1 does not schedule it and
no task implements it. It is contractual as much as technical: a design partner's data agreement will name a
retention period and there is currently no code that honours one.

**Files:**
- Create: `ledger_sense/store/blobs.py`, `ledger_sense/store/retention.py`, `docs/security.md`
- Test: `tests/store/test_blobs.py`, `tests/store/test_retention.py`, `tests/docs/test_security_doc.py`

**Interfaces:**
- Produces: `BlobStore(root, kek)` with `put(tenant_id, payload) -> blob_id`, `get(tenant_id, blob_id)`,
  `delete(tenant_id, blob_id)`, `raw(blob_id)` (test seam) — envelope encryption, a per-blob data key wrapped by
  the KEK from `LEDGER_SENSE_KEK`; `retention_days(tenant_id) -> int`; `purge_expired(now) -> PurgeReport`

- [ ] **Step 1: Write the failing tests**

```python
# tests/store/test_blobs.py
from cryptography.fernet import Fernet

from ledger_sense.store.blobs import BlobStore


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
```

```python
# tests/store/test_retention.py
from datetime import datetime, timedelta, timezone

from ledger_sense.store.retention import purge_expired, retention_days


def test_a_document_past_its_retention_period_is_purged(seeded_documents):
    assert purge_expired(now=datetime.now(timezone.utc)).documents_purged == 1


def test_a_document_inside_its_retention_period_survives(seeded_documents):
    earlier = datetime.now(timezone.utc) - timedelta(days=400)
    assert purge_expired(now=earlier).documents_purged == 0


def test_purging_removes_the_derived_claims_too(seeded_documents):
    """A deletion that leaves the extracted bank details behind is not a deletion."""
    purge_expired(now=datetime.now(timezone.utc))
    assert seeded_documents.claims_for("doc-expired") == []


def test_purging_keeps_the_audit_entry_that_records_the_purge(seeded_documents):
    """The audit trail records the deletion; it is not itself deleted."""
    purge_expired(now=datetime.now(timezone.utc))
    assert any(e.action == "purge" for e in seeded_documents.audit_for("doc-expired"))


def test_the_retention_period_is_per_tenant_not_a_global_constant():
    """It is a term in a signed data agreement, not a number we choose."""
    import inspect
    assert "tenant_id" in inspect.signature(retention_days).parameters
```

```python
# tests/docs/test_security_doc.py
import pathlib

DOC = pathlib.Path("docs/security.md").read_text(encoding="utf-8")


def test_the_document_has_the_sections_00_section_6_requires():
    for heading in ("## Threat model", "## Coordinated disclosure", "## Data we hold",
                    "## Retention and deletion", "## Encryption"):
        assert heading in DOC


def test_the_deletion_procedure_is_written_down_not_implied():
    assert len(DOC.split("## Retention and deletion")[1].strip()) > 400


def test_the_document_contains_no_unresolved_placeholder():
    for token in ("TODO", "TBD", "REPLACE:", "XXX", "<insert"):
        assert token not in DOC
```

- [ ] **Step 2: Implement, run, commit**

Envelope encryption: a fresh Fernet data key per blob, wrapped by the KEK from `LEDGER_SENSE_KEK`, stored
alongside the ciphertext. `retention_days(tenant_id)` reads the tenant row — a term in a signed agreement, never
a constant. `purge_expired` runs from a compose-scheduled job and writes a `purge` audit entry per document.

Expected: PASS, 13 tests.

```bash
git add ledger_sense/store/blobs.py ledger_sense/store/retention.py docs/security.md tests/store/ tests/docs/
git commit -m "feat(store): envelope-encrypted blobs, per-tenant retention, audited purge"
```

---

### Task P6: Frontend scaffold and E2E seeding — **closes G-1, G-9**

Phase 4 opens with three screens and a `pdf.js` renderer and assumes a Next.js application, Tailwind v4 tokens,
`bok-ui`, three density modes, a Playwright config and a Vitest config all exist. None is created by any task.
Every Playwright test navigates to `/review/doc-50` with no task creating the fixture.

**Files:**
- Create: `ledger_sense/web/` (Next.js 15 app router, Tailwind v4 `@theme`, `app/globals.css`,
  `public/pdf.worker.min.mjs`), `playwright.config.ts`, `vitest.config.ts`, `package.json`,
  `ledger_sense/web/lib/tokens.ts`
- Create: `ledger_sense/api/fixtures.py` — the E2E seed router, **`demo` profile only**
- Test: `ledger_sense/web/lib/tokens.test.ts`, `tests/api/test_fixtures.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_fixtures.py
from fastapi.testclient import TestClient


def _client(monkeypatch, profile):
    monkeypatch.setenv("LEDGER_SENSE_PROFILE", profile)
    from ledger_sense.api.app import build_app
    return TestClient(build_app())


def test_the_seed_route_exists_under_the_demo_profile(monkeypatch):
    client = _client(monkeypatch, "demo")
    assert client.post("/api/fixtures/seed",
                       json={"doc_id": "doc-50", "exceptions": 50}).status_code == 201


def test_the_seed_route_does_not_exist_under_the_client_profile(monkeypatch):
    """A route that mints documents must not exist where real documents live."""
    client = _client(monkeypatch, "client")
    assert client.post("/api/fixtures/seed", json={"doc_id": "x"}).status_code == 404


def test_seeded_documents_are_labelled_synthetic(monkeypatch):
    """F-49 depends on this: a synthetic session must never produce field labels."""
    client = _client(monkeypatch, "demo")
    doc_id = client.post("/api/fixtures/seed",
                         json={"doc_id": "doc-3", "exceptions": 3}).json()["doc_id"]
    assert client.get(f"/api/inbox/{doc_id}").json()["sensitivity"] == "SYNTHETIC"
```

```typescript
// ledger_sense/web/lib/tokens.test.ts
import { describe, expect, it } from 'vitest'
import { DENSITIES, tokens } from './tokens'

describe('design tokens', () => {
  it('defines the three densities Task 33 asserts', () => {
    expect(DENSITIES).toEqual(['comfortable', 'compact', 'dense'])
  })
  it('defaults to compact', () => {
    expect(tokens.defaultDensity).toBe('compact')
  })
  it('uses the ledger green accent from 00 §5.2', () => {
    expect(tokens.accent).toBe('oklch(0.58 0.11 158)')
  })
  it('sets tabular numerals on the numeric class', () => {
    expect(tokens.numeric.fontVariantNumeric).toContain('tabular-nums')
  })
  it('defines light as the primary mode', () => {
    expect(tokens.defaultScheme).toBe('light')
  })
})
```

- [ ] **Step 2: Scaffold, run, commit**

`npx create-next-app@15` into `ledger_sense/web`; Tailwind v4 `@theme` consuming the OKLCH tokens from `00` §5.2;
`pdf.js` worker copied into `public/` at build — no CDN, for the same offline-demo and privacy reason `00` §5.2
gives for self-hosting fonts; Playwright `webServer` starting the compose `demo` profile; Vitest with
`@testing-library/react`.

Expected: PASS, 3 Python tests and 5 TypeScript tests.

```bash
git add ledger_sense/web/ playwright.config.ts vitest.config.ts package.json \
        ledger_sense/api/fixtures.py tests/api/test_fixtures.py
git commit -m "feat(web): Next.js scaffold, design tokens, demo-only E2E seed route"
```

---

### Task P7: Persist vendors and duplicate history — **closes F-38, G-10**

`VendorLedger` is a dict, so I1's *"duplicates across full history"*, I2's alias accumulation and I3's
payment-detail baseline all vanish on restart while DoD 3 reports met. Duplicate detection is also a linear
Python scan with no index.

**Files:**
- Create: `migrations/versions/0002_domain.py`
- Modify: `ledger_sense/domain/vendor.py`, `ledger_sense/rules/integrity/duplicates.py`
- Test: `tests/rules/integrity/test_persistence.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/rules/integrity/test_persistence.py
from datetime import datetime, timezone


def test_a_vendor_survives_a_restart(fresh_db):
    from ledger_sense.domain.vendor import VendorLedger
    vendor_id = VendorLedger(fresh_db, tenant_id="t1").create("Acme Steel Company")
    assert VendorLedger(fresh_db, tenant_id="t1").get(vendor_id) is not None


def test_payment_history_survives_a_restart(fresh_db):
    """I3's baseline is the whole fraud control. In memory it is not a control."""
    from ledger_sense.domain.vendor import PaymentDetail, VendorLedger
    led = VendorLedger(fresh_db, tenant_id="t1")
    vendor_id = led.create("Acme Steel Company")
    led.record_payment_detail(vendor_id, PaymentDetail(
        account_last4="4321", routing_last4="8765",
        account_hmac="0" * 64,
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source_document="doc://1", confirmed_by="A. Bookkeeper",
        confirmed_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        confirmed_by="A. Bookkeeper")
    assert len(VendorLedger(fresh_db, tenant_id="t1").get(vendor_id).payment_history) == 1


def test_a_vendor_is_invisible_to_another_tenant(fresh_db):
    from ledger_sense.domain.vendor import VendorLedger
    vendor_id = VendorLedger(fresh_db, tenant_id="t1").create("Acme Steel Company")
    assert VendorLedger(fresh_db, tenant_id="t2").get(vendor_id) is None


def test_duplicate_detection_uses_an_index_rather_than_a_scan(fresh_db, seeded_history):
    """F-37: 'across full history' cannot be a Python loop over a list."""
    plan = fresh_db.explain(
        "SELECT 1 FROM document_fingerprints "
        "WHERE tenant_id = %s AND vendor_id = %s AND amount = %s")
    assert "Seq Scan" not in plan


def test_duplicate_detection_spans_more_than_the_current_period(fresh_db, seeded_history):
    from ledger_sense.rules.integrity.duplicates import find_duplicates
    assert find_duplicates(fresh_db, tenant_id="t1",
                           candidate=seeded_history.candidate_from_last_year())
```

- [ ] **Step 2: Implement, run, commit**

`VendorLedger(db, tenant_id)` — same public API, Postgres-backed, every query filtered on `tenant_id`.
`document_fingerprints` gains a composite index on `(tenant_id, vendor_id, amount, doc_date)` and a partial index
on `(tenant_id, invoice_number)`. `find_duplicates` takes the connection and queries.

Expected: PASS, 5 tests, and the Phase 3 integrity suite still green.

```bash
git add migrations/versions/0002_domain.py ledger_sense/domain/vendor.py \
        ledger_sense/rules/integrity/duplicates.py tests/rules/integrity/test_persistence.py
git commit -m "feat(domain): persist vendors and duplicate history, indexed and tenant-scoped"
```

---

## Phase 0 exit criteria

- [ ] `uv run pytest tests/test_platform_skeleton.py tests/api/test_auth.py tests/api/test_fixtures.py tests/ingest/test_limits.py tests/store tests/docs/test_security_doc.py -v` green
- [ ] `test_every_api_route_requires_a_session` green — **the test that closes F-25**, and it stays green as
      Phases 2, 4 and 6 add routers
- [ ] `test_a_document_from_another_tenant_is_not_found_rather_than_forbidden` green
- [ ] `test_no_malformed_fixture_hangs_or_raises_an_unhandled_exception` green across all 20 fixtures
- [ ] `test_the_payload_is_not_readable_on_disk` green
- [ ] `gitleaks`, `pip-audit`, `npm audit` and the SBOM job present and green; `gitleaks` also scans the cassettes
- [ ] `docker compose --profile demo up` and `--profile client up` both start on a clean machine
- [ ] `uv run mypy --strict ledger_sense` green
- [ ] `alembic upgrade head` runs from empty

**Then the rest of week 5 — Tasks 1–12.** Nothing in those tasks touches a document or the network, so they can
run concurrently with P3–P7 if the hours allow. P1 and P2 come first regardless: a router written before
`register()` exists is a router that will be mounted the wrong way.
