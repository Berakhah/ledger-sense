# I3 Bank-Change Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reviewer clears and undoes an I3 bank-detail hard stop from the review queue in the browser, keyboard-only, without the UI ever holding a full account number.

**Architecture:** One read-only endpoint (`GET /api/review/exceptions/{id}/bank-change`) gives the panel the vendor name, the kind of change and the previous/incoming last four digits. A standalone `BankChangePanel` (same pattern as `VendorConfirmPanel`) renders the red-lined diff and the attestation form. `ReviewQueue.tsx` makes accept/edit/reject inert on I3, hosts the panel, and waits for the server before showing "Confirmed". A new `bank` scenario in the demo-only seed route produces real I2/I3 findings for Playwright.

**Tech Stack:** FastAPI + SQLAlchemy (pytest, `uv`, `ruff`, `mypy --strict`); Next.js 15 / React 19 / Tailwind 4 (vitest + jsdom, Playwright + `@axe-core/playwright`).

**Spec:** `docs/superpowers/specs/2026-10-19-i3-bank-change-panel-design.md` — read its "Amendments" section: reading the code changed four details (no `useReviewKeys` I3 mode, no auto-focus, no green rule on confirm, confirm waits for the server) and the stale-baseline behaviour (409).

## Global Constraints

- Never-rule 6: nothing but `confirm-bank-change` clears I3. The UI must not offer accept/edit/reject on I3, and a bare `Enter` must never clear one.
- No full account/routing number and no HMAC in any response, log or UI. Last four digits only.
- No floats in the money path (nothing here is money; do not introduce any).
- `ruff` and `mypy --strict` clean on every Python change. Coverage floor 75%.
- Frontend: tabular numerals (`numeric` class) for digits; colour is never the only signal; WCAG 2.2 AA, axe zero serious/critical.
- No hardcoded secrets or real account data. Fixtures are the synthetic corpus only.
- Commit trailer on every commit: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- uv is not on PATH: `UV="$APPDATA/Python/Python313/Scripts/uv.exe"`. Run pytest with `-p no:cacheprovider` (the `.pytest_cache` dir is permission-locked on this machine).
- Frontend commands run from the repo root (`package.json` lives there): `npx vitest run <path>`.
- Before any Playwright run, make sure nothing stale holds :3000/:8000 (the config reuses existing servers and would test the wrong build).

---

## File Structure

| File | Responsibility |
|---|---|
| `ledger_sense/api/review.py` (modify) | `BankChangeView` model + `GET .../bank-change` |
| `tests/api/test_bank_change.py` (modify) | endpoint tests, appended to the existing LS-10 file so its helpers are reused |
| `ledger_sense/api/fixtures.py` (modify) | `bank` seed scenario (demo profile only) |
| `tests/api/test_fixtures.py` (modify) | seed-scenario tests |
| `ledger_sense/web/app/review/[docId]/BankChangePanel.tsx` (create) | diff + attestation form; owns its fetch, form state and inline errors |
| `ledger_sense/web/app/review/[docId]/BankChangePanel.test.tsx` (create) | jsdom tests |
| `ledger_sense/web/app/review/[docId]/ReviewQueue.tsx` (modify) | I3 inertness, panel hosting, confirm/undo wiring |
| `ledger_sense/web/e2e/seed.ts` (modify) | `seedBankDocument` |
| `ledger_sense/web/e2e/bank_change.spec.ts` (create) | keyboard-only journey, bare-Enter inertness, axe, greyscale screenshot |
| `docs/security.md`, `docs/superpowers/specs/2026-10-18-ls-10-bank-change-hard-stop-design.md` (modify) | record that the UI landed and what is still out of scope |

---

### Task 1: Read-only `bank-change` endpoint

**Files:**
- Modify: `ledger_sense/api/review.py` (add model after `VendorMatch`, route after `vendor_match`)
- Test: `tests/api/test_bank_change.py` (append)

**Interfaces:**
- Consumes (already in `review.py`): `_get_finding(exc_id, session, conn)`, `_require_open(row)`, `_vendor_for_bank_confirmation(conn, ledger, row) -> str`, `_reextract_detail(conn, row, session) -> PaymentDetail`, `assess_payment_detail(ledger, vendor_id, detail)` (returns an object with `.kind: "first_seen" | "changed" | "changed_back"`, `.expected_last4: str | None`, or `None` when the detail equals the confirmed baseline), `load_vendor_ledger(conn, tenant_id)`, `ledger.get(vendor_id).name`.
- Produces: `GET /api/review/exceptions/{exc_id}/bank-change` → `{"vendor_name": str, "kind": "first_seen"|"changed"|"changed_back", "previous_last4": str|None, "incoming_last4": str}`. 422 non-I3; 409 not open; 409 when the baseline now equals the document's detail (stale finding — re-run the document).

- [ ] **Step 1: Write the failing tests** (append to `tests/api/test_bank_change.py`)

```python
def _bank_view(tenant: TenantClient, exc_id: object) -> object:
    return tenant.get(f"/api/review/exceptions/{exc_id}/bank-change")


def test_bank_change_view_of_a_first_sighting(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=20, variant="base")
    _confirm_new_vendor(a, document_id)
    i3 = _exception(a, document_id, "I3")
    response = _bank_view(a, i3["id"])
    assert response.status_code == 200, response.text  # type: ignore[attr-defined]
    assert response.json() == {  # type: ignore[attr-defined]
        "vendor_name": "Acme Steel Company", "kind": "first_seen",
        "previous_last4": None, "incoming_last4": remit_for(20).account[-4:]}


def test_bank_change_view_of_a_changed_account(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    first = _stored_and_run(a, seed=21, variant="base")
    _confirm_new_vendor(a, first)
    first_i3 = _exception(a, first, "I3")
    assert a.post_json(f"/api/review/exceptions/{first_i3['id']}/confirm-bank-change",
                       ATTEST).status_code == 200

    second = _stored_and_run(a, seed=21, variant="changed")
    changed = _exception(a, second, "I3")
    body = _bank_view(a, changed["id"]).json()  # type: ignore[attr-defined]
    assert body["kind"] == "changed"
    assert body["previous_last4"] == remit_for(21).account[-4:]
    assert body["incoming_last4"] == remit_for(21, "changed").account[-4:]


def test_bank_change_view_is_read_only(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=22, variant="base")
    vendor_id = _confirm_new_vendor(a, document_id)
    i3 = _exception(a, document_id, "I3")
    before = a.get(f"/api/vendors/{vendor_id}/ledger").json()
    assert _bank_view(a, i3["id"]).status_code == 200  # type: ignore[attr-defined]
    assert a.get(f"/api/vendors/{vendor_id}/ledger").json() == before
    assert _exception(a, document_id, "I3")["state"] == "open"


def test_bank_change_view_refuses_a_non_i3_exception(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=23, variant="base")
    i2 = _exception(a, document_id, "I2")
    assert _bank_view(a, i2["id"]).status_code == 422  # type: ignore[attr-defined]


def test_bank_change_view_refuses_a_closed_exception(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=24, variant="base")
    _confirm_new_vendor(a, document_id)
    i3 = _exception(a, document_id, "I3")
    assert a.post_json(f"/api/review/exceptions/{i3['id']}/confirm-bank-change",
                       ATTEST).status_code == 200
    assert _bank_view(a, i3["id"]).status_code == 409  # type: ignore[attr-defined]


def test_bank_change_view_is_tenant_scoped(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, b = two_tenants
    document_id = _stored_and_run(a, seed=25, variant="base")
    _confirm_new_vendor(a, document_id)
    i3 = _exception(a, document_id, "I3")
    assert _bank_view(b, i3["id"]).status_code == 404  # type: ignore[attr-defined]


def test_bank_change_view_needs_a_confirmed_vendor_first(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=26, variant="base")
    i3 = _exception(a, document_id, "I3")
    assert _bank_view(a, i3["id"]).status_code == 409  # type: ignore[attr-defined]


def test_bank_change_view_leaks_no_full_number_and_no_hmac(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=27, variant="base")
    _confirm_new_vendor(a, document_id)
    i3 = _exception(a, document_id, "I3")
    text = _bank_view(a, i3["id"]).text  # type: ignore[attr-defined]
    remit = remit_for(27)
    assert remit.account not in text and remit.routing not in text
    assert "hmac" not in text.lower()


def test_bank_change_view_is_a_409_when_the_baseline_has_caught_up(
        two_tenants: tuple[TenantClient, TenantClient],
        monkeypatch: pytest.MonkeyPatch) -> None:
    """assess_payment_detail returns None when the detail equals the confirmed
    baseline: the finding is stale and the reviewer must re-run the document."""
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=28, variant="base")
    _confirm_new_vendor(a, document_id)
    i3 = _exception(a, document_id, "I3")
    monkeypatch.setattr("ledger_sense.api.review.assess_payment_detail",
                        lambda ledger, vendor_id, detail: None)
    response = _bank_view(a, i3["id"])
    assert response.status_code == 409  # type: ignore[attr-defined]
    assert "re-run" in response.json()["detail"]  # type: ignore[attr-defined]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `"$UV" run pytest tests/api/test_bank_change.py -k bank_change_view -q --no-cov -p no:cacheprovider`
Expected: FAIL — 404 (route does not exist) on most tests; the tenant-scoped test may pass by accident, which is fine.

- [ ] **Step 3: Implement** (in `ledger_sense/api/review.py`)

After `class VendorMatch`:

```python
class BankChangeView(BaseModel):
    """What the review panel shows for an I3 hard stop. Last four digits only:
    the full number and the HMAC never leave the server."""
    vendor_name: str
    kind: Literal["first_seen", "changed", "changed_back"]
    previous_last4: str | None
    incoming_last4: str
```

After the `vendor_match` route:

```python
@router.get("/exceptions/{exc_id}/bank-change")
def bank_change(exc_id: str, session: CurrentSession) -> BankChangeView:
    """What the I3 hard stop is about, for the review panel: the vendor, whether
    this is a first sighting or a change, and the last four digits either side.
    Re-assessed against the ledger exactly as confirm-bank-change will, so the
    panel shows what confirm will accept. Read-only: no write lock, nothing
    persisted."""
    with app_engine().connect() as conn:
        row = _get_finding(exc_id, session, conn)
        if row.rule_id != "I3":
            raise HTTPException(status_code=422,
                                detail="bank-change only applies to an I3 exception")
        _require_open(row)
        ledger = load_vendor_ledger(conn, session.tenant_id)
        vendor_id = _vendor_for_bank_confirmation(conn, ledger, row)
        detail = _reextract_detail(conn, row, session)
        assessment = assess_payment_detail(ledger, vendor_id, detail)
        if assessment is None:
            raise HTTPException(
                status_code=409,
                detail="the vendor's confirmed account now matches this document; "
                       "re-run the document to refresh this exception")
        vendor = ledger.get(vendor_id)
        assert vendor is not None  # _vendor_for_bank_confirmation returned a ledger id
    return BankChangeView(vendor_name=vendor.name, kind=assessment.kind,
                          previous_last4=assessment.expected_last4,
                          incoming_last4=detail.account_last4)
```

- [ ] **Step 4: Run the tests, lint and types**

Run: `"$UV" run pytest tests/api/test_bank_change.py -q --no-cov -p no:cacheprovider` → all PASS (new and existing).
Run: `"$UV" run ruff check ledger_sense tests && "$UV" run mypy --strict ledger_sense` → clean.

If `mypy` rejects `assessment.kind` as `str` for the `Literal`, change `BankAssessment.kind` in `ledger_sense/rules/integrity/bank_details.py` to the same `Literal[...]` rather than casting here.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/api/review.py tests/api/test_bank_change.py
git commit -m "feat(review): read-only bank-change view for the I3 panel (LS-13)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `bank` scenario in the demo seed route

**Files:**
- Modify: `ledger_sense/api/fixtures.py` (model, validator, branch in `seed`)
- Modify: `ledger_sense/web/e2e/seed.ts` (add `seedBankDocument`)
- Test: `tests/api/test_fixtures.py` (append)

**Interfaces:**
- Consumes: `generate(seed, error, remit=...)`, `remit_for(seed, variant)`, `run_document(engine, document_id)`, `VendorLedger.create_confirmed / record_payment_detail`, `PaymentDetail`, `Attestation`, `payment_hmac`.
- Produces: `POST /api/fixtures/seed` accepts `"bank": {"seed": int, "variant": "base"|"changed"}` (requires `exceptions == 0`, excludes `vendor`). The stored document is run through the real pipeline, so it carries a real I2 (new vendor) and I3. With `variant: "changed"` the same tenant first gets a baseline: a vendor with a confirmed detail from the `base` remit, so the document raises only I3 (kind `changed`). Response shape is unchanged (`doc_id` is the document to open). `seed.ts` produces `seedBankDocument(page, stem, bank): Promise<{ docId: string }>`.

- [ ] **Step 1: Write the failing tests** (append to `tests/api/test_fixtures.py`)

```python
def test_a_bank_seed_produces_a_real_i2_and_i3(monkeypatch: pytest.MonkeyPatch) -> None:
    from ledger_sense.corpus.remit import remit_for

    client = _client(monkeypatch, "demo")
    body = client.post("/api/fixtures/seed", json={
        "doc_id": "bank-1", "bank": {"seed": 30, "variant": "base"}}).json()
    rows = client.get(f"/api/review/documents/{body['doc_id']}/exceptions").json()
    by_rule = {r["rule_id"]: r for r in rows}
    assert {"I2", "I3"} <= set(by_rule)
    assert by_rule["I3"]["actual"] == f"....{remit_for(30).account[-4:]}"
    assert by_rule["I3"]["state"] == "open"


def test_a_changed_bank_seed_raises_only_a_change_against_a_confirmed_baseline(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from ledger_sense.corpus.remit import remit_for

    client = _client(monkeypatch, "demo")
    body = client.post("/api/fixtures/seed", json={
        "doc_id": "bank-2", "bank": {"seed": 31, "variant": "changed"}}).json()
    rows = client.get(f"/api/review/documents/{body['doc_id']}/exceptions").json()
    assert "I2" not in {r["rule_id"] for r in rows}
    (i3,) = [r for r in rows if r["rule_id"] == "I3"]
    assert i3["expected"] == f"....{remit_for(31).account[-4:]}"
    assert i3["actual"] == f"....{remit_for(31, 'changed').account[-4:]}"
    view = client.get(f"/api/review/exceptions/{i3['id']}/bank-change").json()
    assert view["kind"] == "changed"


def test_a_bank_seed_cannot_be_combined_with_synthetic_exceptions_or_a_vendor(
        monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, "demo")
    bank = {"seed": 32, "variant": "base"}
    assert client.post("/api/fixtures/seed", json={
        "doc_id": "bank-3", "exceptions": 2, "bank": bank}).status_code == 422
    assert client.post("/api/fixtures/seed", json={
        "doc_id": "bank-4", "bank": bank,
        "vendor": {"observed_name": "Keystone Framing LLC"}}).status_code == 422


def test_the_bank_scenario_does_not_exist_under_the_client_profile(
        monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, "client")
    assert client.post("/api/fixtures/seed", json={
        "doc_id": "bank-5", "bank": {"seed": 33, "variant": "base"}}).status_code == 404
```

- [ ] **Step 2: Run to verify they fail**

Run: `"$UV" run pytest tests/api/test_fixtures.py -k bank -q --no-cov -p no:cacheprovider`
Expected: FAIL (the unknown field `bank` is ignored, so no findings are produced and the combination test returns 201).

- [ ] **Step 3: Implement** (in `ledger_sense/api/fixtures.py`)

Add imports: `from typing import Literal` (extend the existing `typing` import); `from pydantic import model_validator`; `from ledger_sense.corpus.remit import remit_for`; `from ledger_sense.domain.vendor import Attestation, PaymentDetail, payment_hmac` (extend the existing `VendorLedger` import from the same module); `from ledger_sense.run.document import run_document`.

Models (after `VendorScenario`; replace `SeedRequest`, keeping its comments):

```python
class BankScenario(BaseModel):
    """A real corpus PDF with a remit-to bank block, run through the real
    pipeline, so the review queue holds a genuine I3 (and, for a vendor the
    ledger has not seen, a genuine I2). `changed` first gives the tenant a
    vendor whose confirmed account is the `base` one."""
    seed: int = Field(ge=0, le=9999)
    variant: Literal["base", "changed"] = "base"


class SeedRequest(BaseModel):
    doc_id: DocId
    exceptions: int = Field(default=0, ge=0, le=MAX_EXCEPTIONS)
    vendor: VendorScenario | None = None
    bank: BankScenario | None = None

    @model_validator(mode="after")
    def _bank_is_standalone(self) -> "SeedRequest":
        if self.bank is not None and (self.exceptions != 0 or self.vendor is not None):
            raise ValueError("bank cannot be combined with exceptions or vendor")
        return self
```

Helpers (above `seed`):

```python
def _store_bank_document(engine: Engine, tenant_id: str, user_id: str, stem: str,
                         seed_no: int, variant: Literal["base", "changed"]) -> str:
    _, _, payload = generate(seed=seed_no, error="clean", remit=remit_for(seed_no, variant))
    doc = accept_document(payload, origin=Origin.CORPUS,
                          provenance=f"corpus://seed/{tenant_id}/{user_id}")
    with engine.begin() as conn:
        document_id = store_document(conn, tenant_id=tenant_id, doc=doc,
                                     blobs=blob_store_from_environment(),
                                     received_at=datetime.now(UTC), filename=f"{stem}.pdf")
    run_document(engine, document_id)
    return document_id


def _seed_confirmed_baseline(engine: Engine, tenant_id: str, user_id: str,
                             seed_no: int) -> None:
    """A vendor whose confirmed bank account is the `base` remit for this seed,
    so the `changed` document stops as a change, not a first sighting."""
    base_id = _store_bank_document(engine, tenant_id, user_id, "baseline", seed_no, "base")
    remit = remit_for(seed_no, "base")
    with engine.begin() as conn:
        observed = conn.execute(
            sa.select(claims.c.value).where(claims.c.document_id == base_id,
                                            claims.c.field == "vendor_name")).scalar_one()
        ledger = load_vendor_ledger_for_update(conn, tenant_id)
        vendor_id = ledger.create_confirmed(observed, observed, source_document=base_id,
                                            confirmed_by=user_id)
        ledger.record_payment_detail(
            vendor_id,
            PaymentDetail(account_last4=remit.account[-4:], routing_last4=remit.routing[-4:],
                          account_hmac=payment_hmac(remit.account, remit.routing),
                          observed_at=datetime.now(UTC), source_document=base_id),
            confirmed_by=user_id,
            attestation=Attestation(channel="phone", contact="Seeded baseline",
                                    note="synthetic baseline for the demo"))
        save_vendor_ledger(conn, tenant_id, ledger)
```

In `seed()`: after the tenant/user are created and before `_, _, payload = generate(seed=0, ...)`, add the bank branch with an early return. Extract the existing `response.set_cookie(...)` block (with its Secure-relaxation comment) into `_set_session_cookie(response, user_id, tenant_id)` and call it from both paths:

```python
    if body.bank is not None:
        if body.bank.variant == "changed":
            _seed_confirmed_baseline(engine, tenant_id, user_id, body.bank.seed)
        document_id = _store_bank_document(engine, tenant_id, user_id, body.doc_id,
                                           body.bank.seed, body.bank.variant)
        _set_session_cookie(response, user_id, tenant_id)
        return {"doc_id": document_id, "exceptions": 0, "sensitivity": SYNTHETIC,
                "tenant_id": tenant_id, "known_vendor_id": None}
```

`seed.ts` (after `seedVendorDocument`):

```ts
export interface BankScenario {
  seed: number
  variant?: 'base' | 'changed'
}

/**
 * A real corpus PDF with a remit-to bank block, run through the real pipeline,
 * so the queue holds a genuine I3 — and, for 'base', a genuine I2 first
 * (the vendor is new to the tenant). 'changed' pre-seeds a vendor whose
 * confirmed account is the base one, so only the change fires. Demo profile
 * only, like every seed route.
 */
export async function seedBankDocument(
  page: Page,
  docId: string,
  bank: BankScenario,
): Promise<{ docId: string }> {
  const response = await page.request.post(`${API_URL}/api/fixtures/seed`, {
    data: { doc_id: docId, bank: { variant: 'base', ...bank } },
  })
  if (response.status() !== 201) {
    throw new Error(`bank seed failed (${response.status()}): ${await response.text()}`)
  }
  const body = await response.json()
  return { docId: body.doc_id as string }
}
```

- [ ] **Step 4: Run tests, lint, types**

Run: `"$UV" run pytest tests/api/test_fixtures.py -q --no-cov -p no:cacheprovider` → PASS (new and existing).
Run: `"$UV" run ruff check ledger_sense tests && "$UV" run mypy --strict ledger_sense` → clean.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/api/fixtures.py tests/api/test_fixtures.py ledger_sense/web/e2e/seed.ts
git commit -m "test(e2e): bank seed scenario producing a real I2/I3 for Playwright

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `BankChangePanel` component

**Files:**
- Create: `ledger_sense/web/app/review/[docId]/BankChangePanel.tsx`
- Test: `ledger_sense/web/app/review/[docId]/BankChangePanel.test.tsx`

**Interfaces:**
- Consumes: `GET {apiUrl}/api/review/exceptions/{excId}/bank-change` (Task 1 shape).
- Produces: `export function BankChangePanel(props: { excId: string; apiUrl: string; disabled: boolean; onConfirm: (a: BankAttestation) => Promise<void>; onLeaveOpen: () => void; onEscape: () => void })`, `export interface BankAttestation { channel: 'phone'|'in_person'|'video_call'|'other'; contact: string; note: string | null }`. `onConfirm` rejecting with an `Error` shows its message inline. Test ids: `bank-change-panel`, `bank-diff`, `bank-contact`, `bank-note`, `bank-confirm`, `bank-leave-open`, `bank-error`, `bank-change-error`, `bank-change-loading`.
- Keys inside the panel: `Ctrl+Enter` (or `Cmd+Enter`) confirms when valid; bare `Enter` does nothing in any field; `Escape` calls `onEscape`. The panel never auto-focuses (a focused input swallows `J`/`K`; `Tab` from the queue enters it).

- [ ] **Step 1: Write the failing tests**

```tsx
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BankChangePanel } from './BankChangePanel'

type View = {
  vendor_name: string
  kind: 'first_seen' | 'changed' | 'changed_back'
  previous_last4: string | null
  incoming_last4: string
}

function stubView(view: View, ok = true, detail = 'nope'): void {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok,
      status: ok ? 200 : 409,
      statusText: ok ? 'OK' : 'Conflict',
      json: async () => (ok ? view : { detail }),
    }),
  )
}

const CHANGED: View = {
  vendor_name: 'Acme Steel Company', kind: 'changed',
  previous_last4: '1234', incoming_last4: '5678',
}

function renderPanel(overrides: Partial<Parameters<typeof BankChangePanel>[0]> = {}) {
  const props = {
    excId: 'exc-1', apiUrl: 'http://api.test', disabled: false,
    onConfirm: vi.fn().mockResolvedValue(undefined),
    onLeaveOpen: vi.fn(), onEscape: vi.fn(), ...overrides,
  }
  render(<BankChangePanel {...props} />)
  return props
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('BankChangePanel', () => {
  it('shows the previous account struck through and the incoming one underlined', async () => {
    stubView(CHANGED)
    renderPanel()
    const diff = await screen.findByTestId('bank-diff')
    expect(diff.querySelector('del')).toHaveTextContent('1234')
    expect(diff.querySelector('ins')).toHaveTextContent('5678')
    expect(screen.getByText(/Acme Steel Company/)).toBeInTheDocument()
  })

  it('shows only the incoming account for a first sighting', async () => {
    stubView({ vendor_name: 'Acme', kind: 'first_seen', previous_last4: null, incoming_last4: '5678' })
    renderPanel()
    const diff = await screen.findByTestId('bank-diff')
    expect(diff.querySelector('del')).toBeNull()
    expect(diff.querySelector('ins')).toHaveTextContent('5678')
    expect(screen.getByText(/first bank details/i)).toBeInTheDocument()
  })

  it('says so when the account has changed back to an earlier one', async () => {
    stubView({ ...CHANGED, kind: 'changed_back' })
    renderPanel()
    expect(await screen.findByText(/changed back/i)).toBeInTheDocument()
  })

  it('cannot confirm until a contact is named', async () => {
    stubView(CHANGED)
    const props = renderPanel()
    const confirm = await screen.findByTestId('bank-confirm')
    expect(confirm).toBeDisabled()
    fireEvent.change(screen.getByTestId('bank-contact'), { target: { value: '   ' } })
    expect(confirm).toBeDisabled()
    fireEvent.change(screen.getByTestId('bank-contact'), { target: { value: 'J. Vendor' } })
    expect(confirm).toBeEnabled()
    expect(props.onConfirm).not.toHaveBeenCalled()
  })

  it('confirms with the chosen channel, the trimmed contact and a null empty note', async () => {
    stubView(CHANGED)
    const props = renderPanel()
    await screen.findByTestId('bank-confirm')
    fireEvent.click(screen.getByLabelText('In person'))
    fireEvent.change(screen.getByTestId('bank-contact'), { target: { value: '  J. Vendor ' } })
    fireEvent.click(screen.getByTestId('bank-confirm'))
    await waitFor(() => expect(props.onConfirm).toHaveBeenCalledWith(
      { channel: 'in_person', contact: 'J. Vendor', note: null }))
  })

  it('sends the note when one is typed', async () => {
    stubView(CHANGED)
    const props = renderPanel()
    await screen.findByTestId('bank-confirm')
    fireEvent.change(screen.getByTestId('bank-contact'), { target: { value: 'J. Vendor' } })
    fireEvent.change(screen.getByTestId('bank-note'), { target: { value: 'called the number on file' } })
    fireEvent.click(screen.getByTestId('bank-confirm'))
    await waitFor(() => expect(props.onConfirm).toHaveBeenCalledWith(
      { channel: 'phone', contact: 'J. Vendor', note: 'called the number on file' }))
  })

  it('Ctrl+Enter confirms from inside the form when valid', async () => {
    stubView(CHANGED)
    const props = renderPanel()
    const contact = await screen.findByTestId('bank-contact')
    fireEvent.change(contact, { target: { value: 'J. Vendor' } })
    fireEvent.keyDown(contact, { key: 'Enter', ctrlKey: true })
    await waitFor(() => expect(props.onConfirm).toHaveBeenCalledOnce())
  })

  it('Ctrl+Enter does nothing while the form is invalid', async () => {
    stubView(CHANGED)
    const props = renderPanel()
    const contact = await screen.findByTestId('bank-contact')
    fireEvent.keyDown(contact, { key: 'Enter', ctrlKey: true })
    expect(props.onConfirm).not.toHaveBeenCalled()
  })

  it('a bare Enter in the form never confirms', async () => {
    stubView(CHANGED)
    const props = renderPanel()
    const contact = await screen.findByTestId('bank-contact')
    fireEvent.change(contact, { target: { value: 'J. Vendor' } })
    fireEvent.keyDown(contact, { key: 'Enter' })
    expect(props.onConfirm).not.toHaveBeenCalled()
  })

  it('Escape hands the keys back to the queue', async () => {
    stubView(CHANGED)
    const props = renderPanel()
    const contact = await screen.findByTestId('bank-contact')
    fireEvent.keyDown(contact, { key: 'Escape' })
    expect(props.onEscape).toHaveBeenCalledOnce()
  })

  it('shows a server refusal inline and stays usable', async () => {
    stubView(CHANGED)
    const onConfirm = vi.fn().mockRejectedValue(new Error('bank details differ from the vendor'))
    renderPanel({ onConfirm })
    await screen.findByTestId('bank-confirm')
    fireEvent.change(screen.getByTestId('bank-contact'), { target: { value: 'J. Vendor' } })
    fireEvent.click(screen.getByTestId('bank-confirm'))
    expect(await screen.findByTestId('bank-error')).toHaveTextContent('bank details differ from the vendor')
    expect(screen.getByTestId('bank-confirm')).toBeEnabled()
  })

  it('leaving it open records nothing', async () => {
    stubView(CHANGED)
    const props = renderPanel()
    fireEvent.click(await screen.findByTestId('bank-leave-open'))
    expect(props.onLeaveOpen).toHaveBeenCalledOnce()
    expect(props.onConfirm).not.toHaveBeenCalled()
    expect(screen.getByTestId('bank-change-panel')).toHaveTextContent(/stays open/i)
  })

  it('reports a failed lookup instead of offering to confirm', async () => {
    stubView(CHANGED, false, 'the stored document is no longer available')
    renderPanel()
    expect(await screen.findByRole('alert')).toHaveTextContent('the stored document is no longer available')
    expect(screen.queryByTestId('bank-confirm')).not.toBeInTheDocument()
  })

  it('disables everything while the exception is not open', async () => {
    stubView(CHANGED)
    renderPanel({ disabled: true })
    fireEvent.change(await screen.findByTestId('bank-contact'), { target: { value: 'J. Vendor' } })
    expect(screen.getByTestId('bank-confirm')).toBeDisabled()
  })

  it('bounds the contact and note to the server limits', async () => {
    stubView(CHANGED)
    renderPanel()
    expect(await screen.findByTestId('bank-contact')).toHaveAttribute('maxlength', '200')
    expect(screen.getByTestId('bank-note')).toHaveAttribute('maxlength', '2000')
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run "ledger_sense/web/app/review/[docId]/BankChangePanel.test.tsx"`
Expected: FAIL — cannot resolve `./BankChangePanel`.

- [ ] **Step 3: Implement** `BankChangePanel.tsx`

```tsx
'use client'

import { useEffect, useState } from 'react'

export type BankChannel = 'phone' | 'in_person' | 'video_call' | 'other'

/** Mirrors ledger_sense.api.review.ConfirmBankChangeBody. */
export interface BankAttestation {
  channel: BankChannel
  contact: string
  note: string | null
}

/** Mirrors ledger_sense.api.review.BankChangeView. Last four digits only —
 *  the server never sends a full number, so the panel cannot show one. */
interface BankChangeView {
  vendor_name: string
  kind: 'first_seen' | 'changed' | 'changed_back'
  previous_last4: string | null
  incoming_last4: string
}

const CONTACT_MAX = 200
const NOTE_MAX = 2000

const CHANNELS: ReadonlyArray<readonly [BankChannel, string]> = [
  ['phone', 'Phone'],
  ['in_person', 'In person'],
  ['video_call', 'Video call'],
  ['other', 'Other'],
]

const KIND_LEAD: Record<BankChangeView['kind'], string> = {
  first_seen: 'First bank details seen for this vendor — nothing on file confirms them.',
  changed: 'These bank details differ from the account on file.',
  changed_back: 'These bank details have changed back to an account seen earlier.',
}

async function fetchBankChange(apiUrl: string, excId: string): Promise<BankChangeView> {
  const response = await fetch(`${apiUrl}/api/review/exceptions/${excId}/bank-change`, {
    credentials: 'include',
  })
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // A non-JSON error body: the status line above is the best we have.
    }
    throw new Error(detail)
  }
  return (await response.json()) as BankChangeView
}

/** The I3 hard stop's confirm step. Standalone, like VendorConfirmPanel, so it
 *  is testable in jsdom without pdf.js.
 *
 *  Keys. A bare Enter must never clear a hard stop, so no field submits on
 *  Enter; Ctrl/Cmd+Enter does, and only when the form is valid. Escape hands
 *  the keyboard back to the queue (the hook ignores everything typed into an
 *  input, so J/K work again only once focus has left the form). The panel does
 *  not take focus when it appears for the same reason: Tab from the queue
 *  enters it.
 *
 *  Confirming waits for the server (onConfirm resolves) instead of closing
 *  optimistically — a hard stop must not show "Confirmed" and then reopen on a
 *  409. A rejection is shown inline and the form stays usable. */
export function BankChangePanel({
  excId,
  apiUrl,
  disabled,
  onConfirm,
  onLeaveOpen,
  onEscape,
}: {
  excId: string
  apiUrl: string
  disabled: boolean
  onConfirm: (attestation: BankAttestation) => Promise<void>
  onLeaveOpen: () => void
  onEscape: () => void
}) {
  const [view, setView] = useState<BankChangeView | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [channel, setChannel] = useState<BankChannel>('phone')
  const [contact, setContact] = useState('')
  const [note, setNote] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setView(null)
    setLoadError(null)
    fetchBankChange(apiUrl, excId)
      .then((result) => {
        if (!cancelled) setView(result)
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [apiUrl, excId])

  const valid =
    contact.trim() !== '' && contact.length <= CONTACT_MAX && note.length <= NOTE_MAX

  async function submit(): Promise<void> {
    if (!valid || submitting || disabled) return
    setSubmitting(true)
    setError(null)
    try {
      await onConfirm({
        channel,
        contact: contact.trim(),
        note: note.trim() === '' ? null : note,
      })
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Could not confirm the bank details')
    } finally {
      setSubmitting(false)
    }
  }

  if (loadError !== null) {
    return (
      <p role="alert" data-testid="bank-change-error" className="mt-3 text-sm text-flag">
        Could not check these bank details: {loadError}
      </p>
    )
  }
  if (view === null) {
    return (
      <p data-testid="bank-change-loading" className="mt-3 text-sm text-ink-muted">
        Checking bank details…
      </p>
    )
  }

  return (
    <div
      data-testid="bank-change-panel"
      className="mt-3 border-l-2 border-flag pl-3 text-sm"
      onKeyDown={(e) => {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
          e.preventDefault()
          void submit()
        } else if (e.key === 'Escape') {
          e.preventDefault()
          onEscape()
        }
      }}
    >
      <h3 className="font-semibold text-flag">Hard stop: bank details</h3>
      <p className="mt-1">
        <span className="text-ink-muted">Vendor</span> {view.vendor_name}
      </p>
      <p className="mt-1">{KIND_LEAD[view.kind]}</p>

      {/* Strike-through and underline carry the meaning, so the change reads in
          greyscale; colour only reinforces it. */}
      <p data-testid="bank-diff" className="numeric mt-2">
        {view.previous_last4 !== null && (
          <>
            <del className="text-flag line-through">
              <span className="sr-only">Previous account ending </span>
              ....{view.previous_last4}
            </del>{' '}
          </>
        )}
        <ins className="text-ink underline decoration-accent decoration-2">
          <span className="sr-only">Incoming account ending </span>
          ....{view.incoming_last4}
        </ins>
      </p>

      <p className="mt-2 text-ink-muted">
        Verify with the vendor using a number you already trust — not one printed on the document.
        The stop stays open until you do; nothing is recorded until you confirm.
      </p>

      <fieldset className="mt-3" disabled={disabled || submitting}>
        <legend className="text-ink-muted">How did you verify?</legend>
        <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
          {CHANNELS.map(([value, label]) => (
            <label key={value} className="flex items-center gap-1">
              <input
                type="radio"
                name={`bank-channel-${excId}`}
                value={value}
                checked={channel === value}
                onChange={() => setChannel(value)}
              />
              {label}
            </label>
          ))}
        </div>

        <label htmlFor={`bank-contact-${excId}`} className="mt-2 block text-ink-muted">
          Who did you speak to?
        </label>
        <input
          id={`bank-contact-${excId}`}
          data-testid="bank-contact"
          className="mt-1 w-full border border-rule bg-paper px-2 py-1"
          maxLength={CONTACT_MAX}
          value={contact}
          onChange={(e) => setContact(e.target.value)}
        />

        <label htmlFor={`bank-note-${excId}`} className="mt-2 block text-ink-muted">
          Note (optional)
        </label>
        <textarea
          id={`bank-note-${excId}`}
          data-testid="bank-note"
          className="mt-1 w-full border border-rule bg-paper px-2 py-1"
          maxLength={NOTE_MAX}
          rows={2}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </fieldset>

      <div aria-live="polite">
        {error !== null && (
          <p role="alert" data-testid="bank-error" className="mt-2 text-flag">
            {error}
          </p>
        )}
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          data-testid="bank-confirm"
          className="border border-rule px-3 py-1"
          disabled={disabled || submitting || !valid}
          aria-keyshortcuts="Control+Enter"
          onClick={() => void submit()}
        >
          Confirm bank details <kbd aria-hidden="true" className="text-ink-muted">Ctrl+Enter</kbd>
        </button>
        <button
          type="button"
          data-testid="bank-leave-open"
          className="border border-rule px-3 py-1"
          onClick={onLeaveOpen}
        >
          Leave open, next
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run "ledger_sense/web/app/review/[docId]/BankChangePanel.test.tsx"` → PASS (15 tests).
Run: `npx tsc --noEmit -p ledger_sense/web` → no errors.

- [ ] **Step 5: Commit**

```bash
git add "ledger_sense/web/app/review/[docId]/BankChangePanel.tsx" "ledger_sense/web/app/review/[docId]/BankChangePanel.test.tsx"
git commit -m "feat(web): BankChangePanel - I3 diff and attestation form (LS-13)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Wire the panel into `ReviewQueue.tsx`

**Files:**
- Modify: `ledger_sense/web/app/review/[docId]/ReviewQueue.tsx`

**Interfaces:**
- Consumes: `BankChangePanel`, `BankAttestation` (Task 3); existing `postJson`, `resync`, `commit`, `model`, `nextOpen`, `focusQueue`, `refetchProgress`, `setActionError`, `setItemState`, `STATE_LABEL`.
- Produces: I3 behaviour — Enter/E/R show a notice and change nothing; panel shown for an open I3; "Bank details confirmed. Undo" for a confirmed I3; `data-testid="current-rule"` on the rule id span; `data-testid="bank-confirmed"` and `data-testid="undo-bank-change"` on the confirmed state.

ReviewQueue has no unit tests (it pulls in pdf.js); its behaviour is proved by Task 5's Playwright run. Make each edit exactly, then type-check.

- [ ] **Step 1: Import and constant**

Add below the `VendorConfirmPanel` import:

```tsx
import { BankChangePanel, type BankAttestation } from './BankChangePanel'
```

Add below `STATE_LABEL`:

```tsx
/** The server refuses accept/edit/reject on I3 with a 422 (review.py
 *  _refuse_bank_clearing). Decided here without a round trip so an I3 item is
 *  never optimistically closed for a write that cannot succeed. */
const BANK_CLEARING_NOTICE =
  'Bank-detail changes are cleared only by verifying with the vendor and confirming below — accept, edit and reject are disabled.'
```

- [ ] **Step 2: Make accept/edit/reject inert on I3**

In `refusal`, add as the first statement of the callback body (before the `state === 'rejected' ...` early return):

```tsx
    if (item.rule_id === 'I3') return BANK_CLEARING_NOTICE
```

Rename `closedNotice` to `blockedNotice` (definition and its two uses in `onEdit` and `onReject`, including their dependency arrays), update its doc comment to "E and R on a closed or bank-detail item say so, like Enter does, rather than focusing a disabled input", and replace its body with:

```tsx
  const blockedNotice = useCallback((): boolean => {
    const item = model.current.items[model.current.cursor]
    if (!item) return false
    if (item.rule_id === 'I3' && item.state === 'open') {
      setActionError(BANK_CLEARING_NOTICE)
      return true
    }
    if (item.state === 'open') return false
    setActionError(`This exception is already ${STATE_LABEL[item.state].toLowerCase()}.`)
    return true
  }, [])
```

Disable the two inputs for I3: on `#edit-input` and `#reject-reason` change `disabled={current.state !== 'open'}` to `disabled={current.state !== 'open' || current.rule_id === 'I3'}`.

- [ ] **Step 3: Confirm, undo and leave-open handlers** (add after `undoConfirmation`)

```tsx
  // Not routed through actOnCurrent: a hard stop is confirmed pessimistically.
  // Closing it on the keystroke and reopening on a 409 would flash "Confirmed"
  // over an exception the server has refused. The panel awaits this and shows
  // a rejection inline. resync() brings the server's list and count in; the
  // cursor moves on only if it is still on the item just confirmed.
  const confirmBankChange = useCallback(async (excId: string, attestation: BankAttestation) => {
    await postJson(`/api/review/exceptions/${excId}/confirm-bank-change`, attestation)
    setActionError(null)
    try {
      await resync()
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : 'Could not reload the queue')
    }
    const { items, cursor } = model.current
    if (items[cursor]?.id === excId) {
      model.current = { items, cursor: nextOpen(items, cursor) ?? cursor }
      commit()
    }
    focusQueue()
  }, [resync, commit, focusQueue])

  const undoBankChange = useCallback((excId: string) => {
    void postJson(`/api/review/exceptions/${excId}/undo-confirm-bank-change`, {})
      .then(() => {
        setItemState(excId, 'open')
        commit()
        refetchProgress()
        focusQueue()
      })
      .catch((err: unknown) => {
        setActionError(err instanceof Error ? err.message : 'Could not undo the bank-detail confirmation')
      })
  }, [commit, refetchProgress, setItemState, focusQueue])

  /** "Leave open, next": records nothing. Moves to the next open exception, or
   *  says nothing else is open. */
  const leaveBankChangeOpen = useCallback(() => {
    const { items, cursor } = model.current
    const next = nextOpen(items, cursor)
    if (next === null || next === cursor) {
      setActionError('Nothing else is open. This bank-detail stop stays open until it is verified.')
    } else {
      model.current = { items, cursor: next }
      commit()
    }
    focusQueue()
  }, [commit, focusQueue])
```

- [ ] **Step 4: Render**

Add `data-testid="current-rule"` to the rule-id span (`<span className="numeric text-ink-muted">{current.rule_id}</span>`).

After the `current.rule_id === 'I2' && (...)` block and before the Provenance heading, add:

```tsx
              {current.rule_id === 'I3' && (
                current.state === 'confirmed' ? (
                  <p data-testid="bank-confirmed" className="mt-3 border-l-2 border-accent pl-3 text-sm">
                    Bank details confirmed.{' '}
                    <button
                      type="button"
                      data-testid="undo-bank-change"
                      className="underline"
                      onClick={() => undoBankChange(current.id)}
                    >
                      Undo
                    </button>
                  </p>
                ) : current.state === 'open' ? (
                  <BankChangePanel
                    key={current.id}
                    excId={current.id}
                    apiUrl={API_URL}
                    disabled={current.state !== 'open'}
                    onConfirm={(attestation) => confirmBankChange(current.id, attestation)}
                    onLeaveOpen={leaveBankChangeOpen}
                    onEscape={focusQueue}
                  />
                ) : null
              )}
```

- [ ] **Step 5: Type-check and run the existing frontend tests**

Run: `npx tsc --noEmit -p ledger_sense/web` → clean.
Run: `npx vitest run` → all PASS (existing suites unchanged; `useReviewKeys.test.ts` must still pass untouched).

- [ ] **Step 6: Commit**

```bash
git add "ledger_sense/web/app/review/[docId]/ReviewQueue.tsx"
git commit -m "feat(web): host the I3 panel in the review queue; accept/edit/reject inert on I3 (LS-13)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Playwright journey, axe, greyscale, docs

**Files:**
- Create: `ledger_sense/web/e2e/bank_change.spec.ts`
- Modify: `docs/security.md`, `docs/superpowers/specs/2026-10-18-ls-10-bank-change-hard-stop-design.md`

**Interfaces:**
- Consumes: `seedBankDocument` (Task 2), `forbidMouse / expectNoMouse / tabTo` from `./keyboard`, test ids from Tasks 3–4.

- [ ] **Step 1: Write the spec**

```ts
import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { expectNoMouse, forbidMouse, tabTo } from './keyboard'
import { seedBankDocument } from './seed'

// The I3 bank-detail hard stop, keyboard-only: a bare Enter changes nothing,
// the attestation form confirms with Ctrl+Enter, and Undo reopens it. Each test
// seeds a real corpus PDF through the real pipeline (api/fixtures.py's bank
// scenario), so the findings are the product's own, not hand-made rows.

function docKey(stem: string): string {
  return `${stem}-w${test.info().workerIndex}`
}

const queue = (page: Page) => page.getByRole('region', { name: 'Exception under review' })

/** Walk to the exception raised by `rule` with J, as a reviewer would. */
async function goToRule(page: Page, rule: string) {
  for (let i = 0; i < 6; i++) {
    if ((await page.getByTestId('current-rule').textContent()) === rule) return
    await page.keyboard.press('j')
  }
  throw new Error(`never reached ${rule}`)
}

async function seriousViolations(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze()
  return results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
}

test('a changed account is confirmed with Ctrl+Enter and undone, keyboard-only', async ({ page }) => {
  await forbidMouse(page)
  const { docId } = await seedBankDocument(page, docKey('bank-changed'), { seed: 40, variant: 'changed' })
  await page.goto(`/review/${docId}`)
  await expect(page.getByTestId('current-rule')).toHaveText('I3')

  const panel = page.getByTestId('bank-change-panel')
  await expect(panel).toBeVisible()
  await expect(panel.locator('del')).toHaveCount(1)
  await expect(panel.locator('ins')).toHaveCount(1)

  // Bare Enter is the queue's Accept key — on a hard stop it must do nothing.
  await queue(page).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('action-error')).toContainText('Bank-detail changes are cleared only')
  await expect(page.getByTestId('progress')).toHaveText('0 / 1')
  await expect(panel).toBeVisible()
  // E and R are inert too.
  await page.keyboard.press('e')
  await page.keyboard.press('r')
  await expect(page.getByTestId('progress')).toHaveText('0 / 1')

  await tabTo(page, page.getByTestId('bank-contact'))
  await page.keyboard.type('J. Vendor')
  // A bare Enter inside the form does not confirm either.
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('progress')).toHaveText('0 / 1')

  await page.keyboard.press('Control+Enter')
  await expect(page.getByTestId('bank-confirmed')).toBeVisible()
  await expect(page.getByTestId('progress')).toHaveText('1 / 1')
  await expect(queue(page)).toBeFocused()

  await tabTo(page, page.getByTestId('undo-bank-change'))
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('bank-change-panel')).toBeVisible()
  await expect(page.getByTestId('progress')).toHaveText('0 / 1')
  await expectNoMouse(page)
})

test('a first sighting is reached after confirming the vendor, then confirmed', async ({ page }) => {
  await forbidMouse(page)
  const { docId } = await seedBankDocument(page, docKey('bank-first'), { seed: 41 })
  await page.goto(`/review/${docId}`)

  // I2 first: confirming bank details against an unconfirmed vendor is refused.
  await goToRule(page, 'I2')
  await tabTo(page, page.getByRole('button', { name: 'Confirm new vendor' }))
  await page.keyboard.press('Enter')
  await goToRule(page, 'I3')
  await expect(page.getByTestId('bank-change-panel').locator('del')).toHaveCount(0)

  await tabTo(page, page.getByTestId('bank-contact'))
  await page.keyboard.type('J. Vendor')
  await page.keyboard.press('Control+Enter')
  await expect(page.getByTestId('bank-confirmed')).toBeVisible()
  await expectNoMouse(page)
})

test('"Leave open, next" records nothing and the stop stays open', async ({ page }) => {
  const { docId } = await seedBankDocument(page, docKey('bank-leave'), { seed: 42, variant: 'changed' })
  await page.goto(`/review/${docId}`)
  await tabTo(page, page.getByTestId('bank-leave-open'))
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('action-error')).toContainText('stays open')
  await expect(page.getByTestId('progress')).toHaveText('0 / 1')
})

test('the open panel passes axe, and the diff reads in greyscale', async ({ page }, testInfo) => {
  const { docId } = await seedBankDocument(page, docKey('bank-a11y'), { seed: 43, variant: 'changed' })
  await page.goto(`/review/${docId}`)
  await expect(page.getByTestId('bank-change-panel')).toBeVisible()
  expect(await seriousViolations(page)).toEqual([])

  await page.addStyleTag({ content: 'html { filter: grayscale(1); }' })
  await testInfo.attach('bank-diff-greyscale', {
    body: await page.getByTestId('bank-change-panel').screenshot(),
    contentType: 'image/png',
  })
})

test('the confirmed state passes axe', async ({ page }) => {
  const { docId } = await seedBankDocument(page, docKey('bank-a11y2'), { seed: 44, variant: 'changed' })
  await page.goto(`/review/${docId}`)
  await tabTo(page, page.getByTestId('bank-contact'))
  await page.keyboard.type('J. Vendor')
  await page.keyboard.press('Control+Enter')
  await expect(page.getByTestId('bank-confirmed')).toBeVisible()
  expect(await seriousViolations(page)).toEqual([])
})
```

- [ ] **Step 2: Run it**

First confirm nothing stale holds the ports: `netstat -ano | findstr ":3000 :8000"` — stop any listener that is not from this run.
Run: `npx playwright test ledger_sense/web/e2e/bank_change.spec.ts` (the config starts the demo stack; if it uses the compose stack, build serially: app-demo, then web, then `up -d`).
Expected: 5 PASS. If a test fails on a selector, fix the selector or the component; do not loosen the assertion that Enter/E/R are inert or that the form needs Ctrl+Enter.

- [ ] **Step 3: Run the whole e2e, unit and backend suites for regressions**

Run: `npx vitest run` and `npx playwright test` → all PASS (the fifty-exception keyboard run and the I2 vendor specs must be unaffected).
Run: `"$UV" run pytest -q -p no:cacheprovider` → all PASS.

- [ ] **Step 4: Look at the greyscale screenshot**

Open the attached `bank-diff-greyscale` image from the Playwright report (`npx playwright show-report`). Confirm the previous digits are visibly struck through and the incoming digits visibly underlined without colour. If either is not distinguishable, fix the markup, not the screenshot.

- [ ] **Step 5: Docs**

In `docs/security.md`, in the paragraph on the I3 hard stop, add: "The review queue clears and undoes it from a panel that shows last four digits only; accept, edit and reject are inert on I3 in the UI as well as refused by the API, and confirming needs an explicit Ctrl+Enter or button press after naming who was contacted." In the LS-10 spec's "Known limitations and follow-ups", add: "The review-queue panel landed in `docs/superpowers/specs/2026-10-19-i3-bank-change-panel-design.md`; a 'couldn't verify' follow-up action, the rejected-X0 dead end and undo's evidence trail remain open."

- [ ] **Step 6: Commit**

```bash
git add ledger_sense/web/e2e/bank_change.spec.ts docs/security.md docs/superpowers/specs/2026-10-18-ls-10-bank-change-hard-stop-design.md
git commit -m "test(e2e): keyboard-only I3 journey, axe and greyscale; docs (LS-13)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-review against the spec

- Endpoint with kinds, 422/409, last-four-only, read-only, stale-baseline handling → Task 1 (stale baseline is a 409 per the amendment).
- Panel: banner, diff with strike/underline, fieldset, guidance, confirm/undo, "Leave open, next", inline errors → Tasks 3–4.
- Keys: Enter/E/R inert, J/K move, Ctrl+Enter gated on validity → Task 3 (form) and Task 4 (`refusal`, `blockedNotice`); the hook is unchanged by design (it already ignores Ctrl-modified keys and typing targets).
- Accessibility: labelled fieldset, `aria-live` errors, focus returns to the queue after confirm/undo; no auto-focus (amendment) → Tasks 3–5.
- Tests: backend, component, keys (covered by the panel and e2e tests, hook untouched), Playwright, axe, greyscale → Tasks 1, 3, 5.
- Seed fixture for Playwright → Task 2.
- Types: `BankAttestation`, `BankChangeView` field names match across Tasks 1, 3, 4, 5.
