# LS-10 Bank-Change Hard Stop (I3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the I3 bank-detail hard stop real end to end: a synthetic pay application carries remit-to bank details, the runner extracts them and raises I3 (first-seen or changed), and only a recorded human attestation clears it.

**Architecture:** Add a deterministic remit extractor (`extract/remit.py`) and a synthetic remit block (`corpus/`). The runner calls a new `assess_payment_detail` against the vendor ledger's *confirmed* history and writes an I3 finding carrying only masked last-four digits. A new `confirm-bank-change` review action re-extracts the detail from the stored document, records it with an attestation through `VendorLedger.record_payment_detail`, and closes the finding. Generic accept/edit/reject on I3 are refused.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy + alembic (SQLite in tests), pydantic, pytest, pypdfium2 (corpus), `uv`, `ruff`, `mypy --strict`.

**Spec:** `docs/superpowers/specs/2026-10-18-ls-10-bank-change-hard-stop-design.md` (see "Spec amendments" below — this plan supersedes the spec where they differ).

## Global Constraints

- Never-rule 6: never auto-approve a bank-detail change, never auto-update the record. Only `confirm-bank-change` clears I3.
- Never-rule 1: bank details are `CLIENT_CONFIDENTIAL` data; nothing here may reach the model lane.
- Never-rule 2: no float literals under `ledger_sense/domain` or the money path (`scripts/check_no_float.py`, `tests/test_no_floats.py`).
- Never-rule 7: the corpus uses OUR layout. No AIA form artifact.
- Only last four digits plus `account_hmac` are ever stored. The full account/routing number exists only inside one function frame and is never logged, never in a `repr`, never in a claim, finding, API response or exception message.
- `extract/` must never import `ledger_sense.corpus` (AST test). Label strings are duplicated deliberately.
- `payment_details.insert()` may appear only in `store/vendors.py` (`tests/boundary/test_structure.py`); `payment_history` is mutated only inside `domain/vendor.py` (`tests/rules/integrity/test_bank_details.py`).
- Every rule ships with a fixture that trips it and a clean control that does not.
- Run commands from the worktree root. PowerShell: `$env:Path="$env:APPDATA\Python\Python313\Scripts;$env:Path"` first so `uv` resolves. Do not use bare `git stash`.
- Commit trailer on every commit: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Baseline before this plan: `1040 passed, 10 skipped` on `fix/ls-10-bank-change` at e293fd2.

## Spec amendments (discovered while planning)

1. **No claims for bank fields.** The spec said masked claims. Claims feed the Router and the review `edit` action, which would let a reviewer type a plaintext account into `claims`. Instead the I3 finding itself carries the located box and the masked `....1234` in `actual`.
2. **`reject` is also refused on I3.** `reject` closes any finding with a reason; left open it is a one-click bypass of the hard stop. `accept`/`edit` are refused for any finding whose field is `payment_account`. An `X0` on `payment_account` (bank block present but unreadable) may still be rejected with a stated reason.
3. **Baseline = last *confirmed* detail.** An unconfirmed history entry never counts as the baseline.
4. **`bank_change` is a history helper, not an `ErrorClass`.** Like `I1_duplicate`, it needs two documents. It lives in `corpus/errors.py` as `generate_bank_change_history`.
5. **The pending detail is not stored.** `confirm-bank-change` re-extracts it from the stored document, so there is no unconfirmed row to leak into the baseline.
6. **Review-queue UI for the new action is out of scope** (browser workflow is LS-13). The API and tests are in scope.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `ledger_sense/run/document.py` | modify | Task 1: `_predictor`. Task 4: `_payment_rows` I3 step |
| `ledger_sense/corpus/remit.py` | create | `Remit`, deterministic `remit_for(seed, variant)` |
| `ledger_sense/corpus/layouts.py` | modify | remit block geometry constants |
| `ledger_sense/corpus/generate.py` | modify | render the remit block when given |
| `ledger_sense/corpus/errors.py` | modify | `generate_bank_change_history` |
| `ledger_sense/extract/remit.py` | create | label-anchored remit extraction |
| `ledger_sense/domain/vendor.py` | modify | `Attestation`, attestation fields, confirmed-only baseline, find/undo helpers |
| `ledger_sense/rules/integrity/bank_details.py` | modify | `assess_payment_detail`, first-seen, `check_payment_detail` wrapper |
| `migrations/versions/0009_payment_attestation.py` | create | attestation columns |
| `ledger_sense/store/retention.py`, `store/vendors.py` | modify | columns, load/save |
| `ledger_sense/api/review.py` | modify | `confirm-bank-change`, `undo-confirm-bank-change`, refusals |
| `ledger_sense/api/vendors.py` | modify | expose attestation (never the HMAC) |
| `tests/...` | create/modify | per task |

---

### Task 1: Route deterministic claims on match_quality (LS-09 follow-up)

Separate small commit. Review labels are fitted on `match_quality` (`api/review.py:_match_quality_for`), but extraction sets `confidence=None` and `Router.decide_claim` turns `None` into `0.0`, so any fitted curve would be queried at the wrong point.

**Files:**
- Modify: `ledger_sense/run/document.py` (`_routing_rows`, new `_predictor`)
- Create: `tests/run/test_routing_predictor.py`

**Interfaces:**
- Produces: `_predictor(claim: dict[str, object]) -> object` — `confidence` if not `None`, else `match_quality`.

- [ ] **Step 1: Write the failing test**

```python
"""LS-09 follow-up: the Router is asked about the score review labels are fitted on."""

from datetime import UTC, datetime
from typing import cast

from ledger_sense.route.router import Router, RoutingDecision
from ledger_sense.run.document import _routing_rows


class _RecordingRouter:
    def __init__(self) -> None:
        self.seen: list[tuple[str, object]] = []

    def decide_claim(self, field: str, confidence: object, *,
                     method: str = "DETERMINISTIC",
                     rule_failures: tuple[str, ...] = ()) -> RoutingDecision:
        self.seen.append((field, confidence))
        return RoutingDecision(field, "review", "uncalibrated", None, None, None, "stub")


def _ask(confidence: float | None, match_quality: float | None) -> object:
    router = _RecordingRouter()
    claim: dict[str, object] = {
        "field": "total_completed_and_stored", "value": "10.00",
        "method": "deterministic", "confidence": confidence,
        "match_quality": match_quality, "page": 0,
        "x0": 0.0, "top": 0.0, "x1": 1.0, "bottom": 1.0}
    _routing_rows("t", "d", [claim], [], cast(Router, router), datetime.now(UTC))
    return router.seen[0][1]


def test_a_deterministic_claim_is_routed_on_its_match_quality() -> None:
    assert _ask(None, 0.75) == 0.75


def test_a_claim_with_its_own_confidence_keeps_it() -> None:
    assert _ask(0.4, 0.9) == 0.4
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/run/test_routing_predictor.py -v`
Expected: `test_a_deterministic_claim_is_routed_on_its_match_quality` FAILS (`None == 0.75` is false); the other passes.

- [ ] **Step 3: Implement**

In `ledger_sense/run/document.py`, add above `_routing_rows`:

```python
def _predictor(claim: dict[str, object]) -> object:
    """The score the Router is asked about must be the score review labels are
    fitted on. Labels carry `match_quality` (api/review.py `_match_quality_for`)
    because `Claim.confidence` is None for every deterministic extraction; a
    NULL used to reach the Router as 0.0, so a fitted curve would have been
    queried at the wrong point (LS-09 follow-up)."""
    confidence = claim["confidence"]
    return confidence if confidence is not None else claim["match_quality"]
```

and change the call in `_routing_rows` from `claim["confidence"]` to `_predictor(claim)`:

```python
            decision = router.decide_claim(
                field_type_of(field), _predictor(claim), method=str(claim["method"]))
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/run -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/run/document.py tests/run/test_routing_predictor.py
git commit -m "fix(route): route deterministic claims on match_quality, the score labels are fitted on (LS-09)"
```

---

### Task 2: Synthetic remit block and deterministic extraction

**Files:**
- Create: `ledger_sense/corpus/remit.py`, `ledger_sense/extract/remit.py`, `tests/extract/test_remit.py`
- Modify: `ledger_sense/corpus/layouts.py`, `ledger_sense/corpus/generate.py`, `ledger_sense/corpus/errors.py`

**Interfaces:**
- Produces: `Remit(account: str, routing: str)` (NamedTuple) and `remit_for(seed: int, variant: Literal["base", "changed"] = "base") -> Remit` in `corpus/remit.py`.
- Produces: `generate(seed, error, layout="baseline", remit: Remit | None = None)`; with `remit=None` the PDF is byte-identical to today's.
- Produces: `generate_bank_change_history(seed: int) -> list[tuple[Contract, PayApplication, bytes]]` — `[original (variant "base"), changed (variant "changed")]`, same vendor.
- Produces: `RemitHit(account, routing, bbox, page)` frozen dataclass (`account`/`routing` have `repr=False`), `RemitResult(hit: RemitHit | None, problem: str | None)` and `extract_remit(words: list[Word]) -> RemitResult` in `extract/remit.py`. `hit is None and problem is None` means "no bank block on the document".

- [ ] **Step 1: Write the failing tests** — `tests/extract/test_remit.py`

```python
from ledger_sense.corpus.errors import generate_bank_change_history
from ledger_sense.corpus.generate import generate
from ledger_sense.corpus.remit import remit_for
from ledger_sense.extract.assemble import assemble_pay_application
from ledger_sense.extract.remit import extract_remit
from ledger_sense.extract.textlayer import extract_words


def test_remit_for_is_deterministic_and_the_variants_differ() -> None:
    assert remit_for(3) == remit_for(3)
    assert remit_for(3, "changed") != remit_for(3, "base")
    assert len(remit_for(3).routing) == 9


def test_it_extracts_the_rendered_account_and_routing() -> None:
    remit = remit_for(3)
    _c, _a, pdf = generate(seed=3, error="clean", remit=remit)
    result = extract_remit(extract_words(pdf))
    assert result.problem is None
    assert result.hit is not None
    assert (result.hit.account, result.hit.routing) == (remit.account, remit.routing)


def test_a_document_without_a_bank_block_has_nothing_to_extract() -> None:
    _c, _a, pdf = generate(seed=3, error="clean")
    result = extract_remit(extract_words(pdf))
    assert result.hit is None
    assert result.problem is None


def test_a_label_without_its_value_is_a_problem_not_a_guess() -> None:
    remit = remit_for(3)
    _c, _a, pdf = generate(seed=3, error="clean", remit=remit)
    words = [w for w in extract_words(pdf) if w.text != remit.routing]
    result = extract_remit(words)
    assert result.hit is None
    assert result.problem is not None


def test_a_repeated_label_is_refused_rather_than_resolved() -> None:
    _c, _a, pdf = generate(seed=3, error="clean", remit=remit_for(3))
    words = extract_words(pdf)
    i = next(i for i, w in enumerate(words) if w.text == "Account")
    assert words[i + 1].text == "Number"
    result = extract_remit([*words, *words[i:i + 2]])
    assert result.hit is None
    assert result.problem is not None


def test_the_hit_never_reveals_the_number_in_its_repr() -> None:
    remit = remit_for(3)
    _c, _a, pdf = generate(seed=3, error="clean", remit=remit)
    result = extract_remit(extract_words(pdf))
    assert remit.account not in repr(result)
    assert remit.routing not in repr(result)


def test_the_bank_block_does_not_disturb_pay_application_extraction() -> None:
    _c, _a, plain = generate(seed=4, error="clean")
    _c, _a, with_remit = generate(seed=4, error="clean", remit=remit_for(4))
    a = assemble_pay_application(plain, "x")
    b = assemble_pay_application(with_remit, "x")
    assert b.lines == a.lines
    assert b.provenance["_unextractable"] == a.provenance["_unextractable"]
    assert (b.vendor_name, b.contract_id, b.application_no, b.period_to) == (
        a.vendor_name, a.contract_id, a.application_no, a.period_to)


def test_the_bank_change_history_is_the_same_vendor_with_a_different_account() -> None:
    (_c1, app1, pdf1), (_c2, app2, pdf2) = generate_bank_change_history(seed=5)
    assert app1.vendor_name == app2.vendor_name
    first = extract_remit(extract_words(pdf1)).hit
    second = extract_remit(extract_words(pdf2)).hit
    assert first is not None and second is not None
    assert first.account != second.account
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/extract/test_remit.py -v`
Expected: collection error / FAIL — `ledger_sense.corpus.remit` and `ledger_sense.extract.remit` do not exist.

- [ ] **Step 3: Implement**

`ledger_sense/corpus/remit.py`:

```python
"""Synthetic remit-to bank details for the corpus (LS-10).

Numbers are derived from the seed so a document's ground truth is reproducible,
and are obviously fake. Never real account data; no secret material."""

import random
from typing import Literal, NamedTuple


class Remit(NamedTuple):
    account: str
    routing: str


def remit_for(seed: int, variant: Literal["base", "changed"] = "base") -> Remit:
    rng = random.Random(f"remit-{seed}-{variant}")
    return Remit(account=str(rng.randrange(10**9, 10**10)),
                 routing=str(rng.randrange(10**8, 10**9)))
```

`ledger_sense/corpus/layouts.py` — append after the DOC_HEADER block:

```python
# REMIT BLOCK (LS-10) — account and routing numbers for I3. Same label/value
# pattern as the document header, drawn on page one in the empty space to the
# RIGHT of that block and ABOVE the continuation grid's header row, so the grid
# reconstruction never sees it. "Account Number" / "Routing Number" share no
# word sequence with any column header, summary label or header label.
REMIT_FIELDS: tuple[tuple[str, str], ...] = (
    ("Account Number", "account"),
    ("Routing Number", "routing"),
)
REMIT_TOP_PT = 588.0
REMIT_LABEL_X_PT = 380.0
REMIT_VALUE_X_PT = 520.0
```

`ledger_sense/corpus/generate.py`:
- import `from ledger_sense.corpus.remit import Remit` and add `REMIT_FIELDS, REMIT_LABEL_X_PT, REMIT_TOP_PT, REMIT_VALUE_X_PT` to the `layouts` import list.
- Change `_render(app, contract, layout)` to `_render(app, contract, layout, remit: Remit | None = None)` and pass `remit` into `_render_into(pdf, app, contract, layout, remit)`; give `_render_into` the same `remit: Remit | None` parameter.
- In `_render_into`, directly after the document-header `for label, field in DOC_HEADER_FIELDS:` loop, add:

```python
    if remit is not None:
        remit_values = {"account": remit.account, "routing": remit.routing}
        y = REMIT_TOP_PT
        for label, field in REMIT_FIELDS:
            _insert_text(pages[0], pdf, label, REMIT_LABEL_X_PT, y, BODY_FONT_PT)
            _insert_text(pages[0], pdf, remit_values[field], REMIT_VALUE_X_PT, y, BODY_FONT_PT)
            y -= DOC_HEADER_ROW_HEIGHT_PT
```

- Change `generate`:

```python
def generate(seed: int, error: ErrorClass, layout: str = "baseline",
             remit: Remit | None = None) -> tuple[Contract, PayApplication, bytes]:
    contract, app = _clean(seed)
    contract, mutated = inject(contract, app, error)
    return contract, mutated, _render(mutated, contract, layout, remit)
```

`ledger_sense/corpus/errors.py` — append:

```python
def generate_bank_change_history(seed: int) -> list[tuple[Contract, PayApplication, bytes]]:
    """Two documents from one vendor: the original and a re-submission of the
    same invoice whose remit-to account differs. I3 must stop the second.

    A HISTORY-level error like I1_duplicate: it needs a prior document to differ
    from, so it cannot be an `ErrorClass` mutation. Imports `generate` inside the
    function for the same circular-import reason as `generate_history`."""
    from ledger_sense.corpus.generate import generate
    from ledger_sense.corpus.remit import remit_for

    original = generate(seed=seed, error="clean", remit=remit_for(seed, "base"))
    changed = generate(seed=seed, error="clean", remit=remit_for(seed, "changed"))
    return [original, changed]
```

`ledger_sense/extract/remit.py`:

```python
"""Remit-to bank details by label anchor plus geometry (LS-10 / I3).

Same discipline as `anchors.py`: an absent label means an absent field, a
repeated label is refused rather than resolved, and a label with no value
beside it is a recorded problem, never a guess.

The full account and routing numbers are returned ONLY to the caller that
reduces them to a last-four plus HMAC. They are `repr=False` so a stray log of a
result cannot leak them. extract/ must not import ledger_sense.corpus, so the
label strings are this module's own copy."""

import re
from dataclasses import dataclass, field

from ledger_sense.extract.anchors import find_labels, value_after_label
from ledger_sense.extract.textlayer import BBox, Word

_ACCOUNT_LABEL = "Account Number"
_ROUTING_LABEL = "Routing Number"
_ACCOUNT = re.compile(r"\d{4,17}")
_ROUTING = re.compile(r"\d{9}")


@dataclass(frozen=True)
class RemitHit:
    account: str = field(repr=False)
    routing: str = field(repr=False)
    bbox: BBox
    """Where the ACCOUNT value sits — the located box the I3 finding points at."""
    page: int


@dataclass(frozen=True)
class RemitResult:
    hit: RemitHit | None
    problem: str | None


def extract_remit(words: list[Word]) -> RemitResult:
    account_labels = find_labels(words, _ACCOUNT_LABEL)
    routing_labels = find_labels(words, _ROUTING_LABEL)
    if not account_labels and not routing_labels:
        return RemitResult(None, None)  # no bank block: nothing to compare
    if len(account_labels) != 1 or len(routing_labels) != 1:
        return RemitResult(None, "bank-detail label missing or repeated; refusing to guess")
    account = value_after_label(
        words, account_labels[0], lambda w: bool(_ACCOUNT.fullmatch(w.text)))
    routing = value_after_label(
        words, routing_labels[0], lambda w: bool(_ROUTING.fullmatch(w.text)))
    if account is None or routing is None:
        return RemitResult(None, "bank-detail value not found beside its label")
    return RemitResult(RemitHit(account.text, routing.text, account.bbox, account.page), None)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/extract tests/corpus -q`
Expected: all pass. If `test_the_bank_block_does_not_disturb_pay_application_extraction` fails, the remit block collides with the grid or header: move `REMIT_TOP_PT` / x constants, do not weaken the test.

- [ ] **Step 5: Lint and types**

Run: `uv run ruff check ledger_sense tests ; uv run mypy --strict ledger_sense tests`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add ledger_sense/corpus ledger_sense/extract/remit.py tests/extract/test_remit.py
git commit -m "feat(extract): synthetic remit block and label-anchored bank-detail extraction (LS-10)"
```

---

### Task 3: Attestation, confirmed-only baseline, first-seen rule, storage

**Files:**
- Modify: `ledger_sense/domain/vendor.py`, `ledger_sense/rules/integrity/bank_details.py`, `ledger_sense/store/retention.py`, `ledger_sense/store/vendors.py`, `tests/rules/integrity/test_bank_details.py`
- Create: `migrations/versions/0009_payment_attestation.py`, `tests/domain/test_payment_attestation.py`, `tests/store/test_payment_attestation_store.py`

**Interfaces:**
- Produces (`domain/vendor.py`): `Attestation(channel: Literal["phone","in_person","video_call","other"], contact: str, note: str | None = None)`; `PaymentDetail.confirmation_channel/confirmation_contact/confirmation_note: str | None = None`; `record_payment_detail(vendor_id, detail, confirmed_by, attestation: Attestation | None = None)`; `VendorLedger.find_payment_detail(source_document: str) -> tuple[str, PaymentDetail] | None` (confirmed entries only); `VendorLedger.undo_payment_detail(vendor_id: str, detail_id: str) -> None`.
- Produces (`bank_details.py`): `BankAssessment(kind: Literal["first_seen","changed","changed_back"], message: str, expected_last4: str | None, actual_last4: str)` and `assess_payment_detail(ledger: VendorLedger, vendor_id: str | None, incoming: PaymentDetail) -> BankAssessment | None`. `check_payment_detail` keeps its signature and now wraps it.

- [ ] **Step 1: Write the failing tests**

`tests/domain/test_payment_attestation.py`:

```python
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ledger_sense.domain.vendor import Attestation, PaymentDetail, VendorLedger, payment_hmac


@pytest.fixture(autouse=True)
def _hmac_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SENSE_PAYMENT_HMAC_KEY", "test-key-not-a-secret")


def _detail(account: str = "4321", doc: str = "doc://a") -> PaymentDetail:
    return PaymentDetail(account_last4=account[-4:], routing_last4="8765",
                         account_hmac=payment_hmac(account, "8765"),
                         observed_at=datetime.now(UTC), source_document=doc)


def test_an_attestation_must_name_who_was_spoken_to() -> None:
    with pytest.raises(ValidationError):
        Attestation(channel="phone", contact="   ")


def test_an_unknown_channel_is_refused() -> None:
    with pytest.raises(ValidationError):
        Attestation(channel="carrier-pigeon", contact="J. Vendor")  # type: ignore[arg-type]


def test_the_attestation_is_stored_on_the_recorded_detail() -> None:
    led = VendorLedger()
    vendor_id = led.create("Acme Steel Company")
    led.record_payment_detail(
        vendor_id, _detail(), confirmed_by="reviewer-1",
        attestation=Attestation(channel="phone", contact="J. Vendor", note="known-good number"))
    vendor = led.get(vendor_id)
    assert vendor is not None
    stored = vendor.payment_history[-1]
    assert (stored.confirmation_channel, stored.confirmation_contact,
            stored.confirmation_note) == ("phone", "J. Vendor", "known-good number")


def test_find_and_undo_work_by_source_document_and_identity() -> None:
    led = VendorLedger()
    vendor_id = led.create("Acme Steel Company")
    led.record_payment_detail(vendor_id, _detail("4321", "doc://one"), confirmed_by="r")
    led.record_payment_detail(vendor_id, _detail("9999", "doc://two"), confirmed_by="r")
    found = led.find_payment_detail("doc://two")
    assert found is not None
    assert found[0] == vendor_id
    led.undo_payment_detail(found[0], found[1].id)
    vendor = led.get(vendor_id)
    assert vendor is not None
    assert [d.source_document for d in vendor.payment_history] == ["doc://one"]
    assert led.find_payment_detail("doc://two") is None


def test_an_unconfirmed_entry_is_never_found_or_used_as_a_baseline() -> None:
    led = VendorLedger()
    vendor_id = led.create("Acme Steel Company")
    led.record_payment_detail(vendor_id, _detail("4321", "doc://one"), confirmed_by="")
    assert led.find_payment_detail("doc://one") is None
```

Append to `tests/rules/integrity/test_bank_details.py`:

```python
from ledger_sense.rules.integrity.bank_details import assess_payment_detail


def test_first_seen_details_are_a_hard_stop_too() -> None:
    led = VendorLedger()
    vendor_id = led.create("Acme Steel Company")
    assessment = assess_payment_detail(led, vendor_id, _incoming())
    assert assessment is not None
    assert assessment.kind == "first_seen"
    assert assessment.expected_last4 is None
    assert "hard stop" in assessment.message.lower()


def test_a_vendor_that_does_not_exist_yet_is_first_seen() -> None:
    assessment = assess_payment_detail(VendorLedger(), None, _incoming())
    assert assessment is not None
    assert assessment.kind == "first_seen"


def test_an_unconfirmed_history_entry_is_not_a_baseline() -> None:
    led = VendorLedger()
    vendor_id = led.create("Acme Steel Company")
    led.record_payment_detail(vendor_id, _incoming(), confirmed_by="")
    assessment = assess_payment_detail(led, vendor_id, _incoming())
    assert assessment is not None
    assert assessment.kind == "first_seen"


def test_a_changed_account_reports_both_last_fours(
        ledger: tuple[VendorLedger, str]) -> None:
    led, vendor_id = ledger
    assessment = assess_payment_detail(led, vendor_id, _incoming(account="9999"))
    assert assessment is not None
    assert (assessment.kind, assessment.expected_last4, assessment.actual_last4) == (
        "changed", "4321", "9999")


def test_change_then_change_back_is_labelled_as_such(
        ledger: tuple[VendorLedger, str]) -> None:
    led, vendor_id = ledger
    led.record_payment_detail(vendor_id, _incoming(account="9999"), confirmed_by="A. Bookkeeper")
    assessment = assess_payment_detail(led, vendor_id, _incoming(account="4321"))
    assert assessment is not None
    assert assessment.kind == "changed_back"


def test_a_matching_confirmed_detail_is_clean(ledger: tuple[VendorLedger, str]) -> None:
    led, vendor_id = ledger
    assert assess_payment_detail(led, vendor_id, _incoming()) is None
```

Also edit `test_no_full_account_number_is_ever_stored` in the same file: add `"confirmation_channel", "confirmation_contact", "confirmation_note"` to the expected field set.

`tests/store/test_payment_attestation_store.py`:

```python
from datetime import UTC, datetime

import pytest

from ledger_sense.domain.vendor import Attestation, PaymentDetail, VendorLedger, payment_hmac
from ledger_sense.store import session as store_session
from ledger_sense.store.vendors import load_vendor_ledger, save_vendor_ledger
from tests.store.conftest import SeededDocuments


def test_the_attestation_round_trips(
        seeded_documents: SeededDocuments, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SENSE_PAYMENT_HMAC_KEY", "test-key")
    ledger = VendorLedger()
    vendor_id = ledger.create("Acme Steel Company")
    ledger.record_payment_detail(
        vendor_id,
        PaymentDetail(account_last4="1234", routing_last4="5678",
                      account_hmac=payment_hmac("00001234", "00005678"),
                      observed_at=datetime(2026, 9, 15, tzinfo=UTC), source_document="doc-1"),
        confirmed_by="reviewer-1",
        attestation=Attestation(channel="video_call", contact="J. Vendor", note="n"))
    with store_session.app_engine().begin() as conn:
        save_vendor_ledger(conn, seeded_documents.tenant_id, ledger)
    with store_session.app_engine().connect() as conn:
        reloaded = load_vendor_ledger(conn, seeded_documents.tenant_id)
    vendor = reloaded.get(vendor_id)
    assert vendor is not None
    stored = vendor.payment_history[0]
    assert (stored.confirmation_channel, stored.confirmation_contact,
            stored.confirmation_note) == ("video_call", "J. Vendor", "n")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/domain/test_payment_attestation.py tests/rules/integrity/test_bank_details.py tests/store/test_payment_attestation_store.py -q`
Expected: FAIL — `Attestation`, `assess_payment_detail`, columns do not exist.

- [ ] **Step 3: Implement the domain**

`ledger_sense/domain/vendor.py`:
- imports: `from typing import Literal` and `from pydantic import Field, field_validator`.
- add above `PaymentDetail`:

```python
class Attestation(_Base):
    """How a human satisfied the I3 hard stop: an out-of-band verification, with
    enough evidence on the record to be audited later (B5, Never-rule 6)."""
    channel: Literal["phone", "in_person", "video_call", "other"]
    contact: str
    note: str | None = None

    @field_validator("contact")
    @classmethod
    def _contact_named(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("an attestation must name who was spoken to")
        return value.strip()
```

- add to `PaymentDetail` after `confirmed_at`:

```python
    confirmation_channel: str | None = None
    confirmation_contact: str | None = None
    confirmation_note: str | None = None
```

- replace `record_payment_detail` with:

```python
    def record_payment_detail(self, vendor_id: str, detail: PaymentDetail,
                              confirmed_by: str,
                              attestation: Attestation | None = None) -> None:
        """Append-only. A CHANGED detail requires a named human confirmer, and
        the confirmation is STORED (F-35) — spec §8 item 5 renders it. When an
        `attestation` is given its evidence is stored with it (LS-10).

        Comparison is on the HMAC, not on four digits, and against the last
        CONFIRMED entry: an unconfirmed entry is never a baseline."""
        vendor = self._vendors[vendor_id]
        confirmed = [d for d in vendor.payment_history if d.confirmed_by]
        current = confirmed[-1] if confirmed else None
        changed = current is not None and current.account_hmac != detail.account_hmac
        if changed and not confirmed_by:
            raise BankDetailChangeError(
                "a bank-detail change requires human confirmation; this system "
                "never auto-approves and never auto-updates a payment record")
        update: dict[str, object] = {
            "confirmed_by": confirmed_by or None,
            "confirmed_at": datetime.now(UTC) if confirmed_by else None}
        if attestation is not None:
            update.update(confirmation_channel=attestation.channel,
                          confirmation_contact=attestation.contact,
                          confirmation_note=attestation.note)
        vendor.payment_history.append(detail.model_copy(update=update))

    def find_payment_detail(self, source_document: str) -> tuple[str, PaymentDetail] | None:
        """The (vendor_id, confirmed detail) that one document's bank-change
        confirmation recorded. Undo's lookup, by identity not by value."""
        for vendor in self._vendors.values():
            for detail in vendor.payment_history:
                if detail.source_document == source_document and detail.confirmed_by:
                    return vendor.id, detail
        return None

    def undo_payment_detail(self, vendor_id: str, detail_id: str) -> None:
        """Reverse one confirmation: remove the entry by identity (F-36's lesson),
        restoring the previous baseline. A no-op if the vendor is gone."""
        vendor = self._vendors.get(vendor_id)
        if vendor is None:
            return
        vendor.payment_history = [d for d in vendor.payment_history if d.id != detail_id]
```

`ledger_sense/rules/integrity/bank_details.py` — keep the module docstring, replace everything below it with:

```python
from typing import Literal, NamedTuple

from ledger_sense.domain.money import money
from ledger_sense.domain.vendor import BankDetailChangeError, PaymentDetail, VendorLedger
from ledger_sense.rules.engine import Finding

__all__ = ["BankAssessment", "BankDetailChangeError", "assess_payment_detail",
           "check_payment_detail"]


class BankAssessment(NamedTuple):
    kind: Literal["first_seen", "changed", "changed_back"]
    message: str
    expected_last4: str | None
    actual_last4: str


def assess_payment_detail(ledger: VendorLedger, vendor_id: str | None,
                          incoming: PaymentDetail) -> BankAssessment | None:
    """None only when the incoming detail equals the vendor's last CONFIRMED
    one. Everything else is a hard stop: a first-seen detail has nothing on file
    to be compared with (no trust-on-first-use), a changed one may be a
    redirection, and a change BACK to an earlier account is a documented
    redirection pattern (F-35).

    Compares `account_hmac`, not last4 (F-35: four digits alone miss 1 in
    10,000). An unconfirmed history entry is never the baseline."""
    vendor = ledger.get(vendor_id) if vendor_id is not None else None
    name = vendor.name if vendor is not None else "this vendor"
    confirmed = [d for d in vendor.payment_history if d.confirmed_by] if vendor else []
    if not confirmed:
        return BankAssessment(
            "first_seen",
            f"HARD STOP: first bank details seen for {name} "
            f"(account ....{incoming.account_last4}); nothing on file confirms them. "
            f"Verify with the vendor using a known-good phone number before paying. "
            f"This system will not record them without your attestation.",
            None, incoming.account_last4)
    current = confirmed[-1]
    if current.account_hmac == incoming.account_hmac:
        return None
    came_back = any(d.account_hmac == incoming.account_hmac for d in confirmed[:-1])
    lead = (f"HARD STOP: payment details for {name} changed BACK to an account seen earlier"
            if came_back else
            f"HARD STOP: payment details for {name} differ from the record on file")
    return BankAssessment(
        "changed_back" if came_back else "changed",
        lead + f" (account ....{current.account_last4} -> ....{incoming.account_last4}). "
        f"Verify with the vendor using a known-good phone number before paying. "
        f"This system will not update the record without your confirmation.",
        current.account_last4, incoming.account_last4)


def check_payment_detail(ledger: VendorLedger, vendor_id: str,
                         incoming: PaymentDetail) -> list[Finding]:
    assessment = assess_payment_detail(ledger, vendor_id, incoming)
    if assessment is None:
        return []
    return [Finding("I3", "vendor.payment_detail", assessment.message,
                    money("0.00"), money("0.00"))]
```

- [ ] **Step 4: Implement storage**

`ledger_sense/store/retention.py` — add three nullable columns to the `payment_details` table after `confirmed_at`:

```python
    sa.Column("confirmation_channel", sa.Text, nullable=True),
    sa.Column("confirmation_contact", sa.Text, nullable=True),
    sa.Column("confirmation_note", sa.Text, nullable=True),
```

`ledger_sense/store/vendors.py`:
- in `load_vendor_ledger`, extend the `PaymentDetail(...)` constructor with `confirmation_channel=row.confirmation_channel, confirmation_contact=row.confirmation_contact, confirmation_note=row.confirmation_note`.
- in `save_vendor_ledger`, extend each `detail_rows` dict with `"confirmation_channel": d.confirmation_channel, "confirmation_contact": d.confirmation_contact, "confirmation_note": d.confirmation_note`.

`migrations/versions/0009_payment_attestation.py`:

```python
"""payment_details: attestation evidence for the I3 bank-change confirmation

Revision ID: 0009_payment_attestation
Revises: 0008_findings_confirmed_state
Create Date: 2026-10-18

A bank-detail confirmation is a human out-of-band verification (LS-10, B5).
The channel, the person spoken to and a note go on the record beside
`confirmed_by` / `confirmed_at` so the vendor ledger can show them and an audit
can read them. All nullable: rows written before this carry no evidence, and
the code treats an unconfirmed or evidence-less row as exactly that.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_payment_attestation"
down_revision: str | None = "0008_findings_confirmed_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ("confirmation_channel", "confirmation_contact", "confirmation_note")


def upgrade() -> None:
    with op.batch_alter_table("payment_details") as batch:
        for name in _COLUMNS:
            batch.add_column(sa.Column(name, sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("payment_details") as batch:
        for name in _COLUMNS:
            batch.drop_column(name)
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/domain tests/rules tests/store tests/boundary tests/test_no_floats.py -q`
Expected: all pass. If an older test asserted that an empty-history vendor yields no I3 finding (grep `tests/rules/integrity/test_vendors.py` near line 211), update that test to the new first-seen behaviour and say so in the commit message; do not restore the short-circuit.

- [ ] **Step 6: Lint, types, commit**

Run: `uv run ruff check ledger_sense tests ; uv run mypy --strict ledger_sense tests`

```bash
git add ledger_sense migrations tests
git commit -m "feat(rules): attested bank-detail confirmations and a first-seen hard stop (LS-10)"
```

---

### Task 4: Runner raises I3 from the document

**Files:**
- Modify: `ledger_sense/run/document.py`, `tests/run/conftest.py`
- Create: `tests/run/test_payment_details.py`

**Interfaces:**
- Consumes: `extract_remit`, `PaymentDetail`/`payment_hmac`, `assess_payment_detail`, `load_vendor_ledger`, `generate`/`remit_for`.
- Produces: `_payment_rows(conn, tenant_id, document_id, pdf, app, now) -> list[dict[str, object]]`. I3 finding rows have `field="payment_account"`, `expected` = `"....NNNN"` or `None`, `actual` = `"....NNNN"`, and the account value's located box. An unreadable bank block yields an `X0` row on `payment_account`. No document without a bank block gets any row.

- [ ] **Step 1: Set the HMAC key for runner tests**

In `tests/run/conftest.py`, inside the `db` fixture's `with pytest.MonkeyPatch.context() as patch:` block add:

```python
        patch.setenv("LEDGER_SENSE_PAYMENT_HMAC_KEY", "test-key-not-a-secret")
```

- [ ] **Step 2: Write the failing tests** — `tests/run/test_payment_details.py`

```python
"""The runner turns a document's bank details into an I3 hard stop (LS-10)."""

import io
import logging
from datetime import UTC, datetime

import pypdfium2 as pdfium
import pytest
import sqlalchemy as sa

from ledger_sense.corpus.generate import _insert_text, generate
from ledger_sense.corpus.layouts import REMIT_LABEL_X_PT, REMIT_TOP_PT
from ledger_sense.corpus.remit import Remit, remit_for
from ledger_sense.domain.models import PayApplication
from ledger_sense.domain.vendor import PaymentDetail, VendorLedger, payment_hmac
from ledger_sense.run.document import run_document
from ledger_sense.store.retention import documents, metadata
from ledger_sense.store.vendors import save_vendor_ledger
from tests.run.conftest import RunDb


def _doc(db: RunDb, seed: int, variant: str = "base") -> tuple[PayApplication, Remit, str]:
    remit = remit_for(seed, variant)  # type: ignore[arg-type]
    _c, app, pdf = generate(seed=seed, error="clean", remit=remit)
    return app, remit, db.store_pdf(pdf)


def _confirm_baseline(db: RunDb, app: PayApplication, remit: Remit) -> None:
    assert app.vendor_name
    ledger = VendorLedger()
    vendor_id = ledger.create_confirmed(app.vendor_name, app.vendor_name,
                                        source_document="seed", confirmed_by="A. Bookkeeper")
    ledger.record_payment_detail(
        vendor_id,
        PaymentDetail(account_last4=remit.account[-4:], routing_last4=remit.routing[-4:],
                      account_hmac=payment_hmac(remit.account, remit.routing),
                      observed_at=datetime.now(UTC), source_document="seed"),
        confirmed_by="A. Bookkeeper")
    with db.engine.begin() as conn:
        save_vendor_ledger(conn, db.tenant_id, ledger)


def _i3(db: RunDb, document_id: str) -> list[sa.Row[tuple[object, ...]]]:
    return [f for f in db.findings_for(document_id) if f.rule_id == "I3"]


def test_a_document_without_a_bank_block_raises_nothing(db: RunDb) -> None:
    _c, _a, pdf = generate(seed=1, error="clean")
    document_id = db.store_pdf(pdf)
    run_document(db.engine, document_id)
    assert _i3(db, document_id) == []
    assert not [f for f in db.findings_for(document_id) if f.field == "payment_account"]


def test_first_seen_bank_details_are_a_hard_stop(db: RunDb) -> None:
    _app, remit, document_id = _doc(db, seed=2)
    run_document(db.engine, document_id)
    (finding,) = _i3(db, document_id)
    assert finding.field == "payment_account"
    assert finding.state == "open"
    assert finding.expected is None
    assert finding.actual == f"....{remit.account[-4:]}"
    assert finding.x0 is not None  # located: the reviewer can see the box


def test_the_same_account_as_the_confirmed_baseline_is_clean(db: RunDb) -> None:
    app, remit, document_id = _doc(db, seed=3)
    _confirm_baseline(db, app, remit)
    run_document(db.engine, document_id)
    assert _i3(db, document_id) == []


def test_a_changed_account_is_a_hard_stop(db: RunDb) -> None:
    app, remit, _ = _doc(db, seed=4)
    _confirm_baseline(db, app, remit)
    _a, changed, document_id = _doc(db, seed=4, variant="changed")
    run_document(db.engine, document_id)
    (finding,) = _i3(db, document_id)
    assert finding.expected == f"....{remit.account[-4:]}"
    assert finding.actual == f"....{changed.account[-4:]}"


def test_a_rerun_replaces_the_finding_instead_of_duplicating_it(db: RunDb) -> None:
    _app, _remit, document_id = _doc(db, seed=5)
    run_document(db.engine, document_id)
    with db.engine.begin() as conn:
        conn.execute(documents.update().where(documents.c.id == document_id)
                     .values(status="received"))
    run_document(db.engine, document_id)
    assert len(_i3(db, document_id)) == 1


def test_a_bank_block_that_cannot_be_read_is_recorded_not_guessed(db: RunDb) -> None:
    # A label with no value beside it: append a bare "Account Number" to a plain
    # corpus PDF, which has no bank block of its own.
    _c, _a, plain = generate(seed=6, error="clean")
    doc = pdfium.PdfDocument(plain)
    page = doc[0]
    _insert_text(page, doc, "Account Number", REMIT_LABEL_X_PT, REMIT_TOP_PT, 9.0)
    page.gen_content()
    buf = io.BytesIO()
    doc.save(buf)
    document_id = db.store_pdf(buf.getvalue())
    run_document(db.engine, document_id)
    assert _i3(db, document_id) == []
    (x0,) = [f for f in db.findings_for(document_id)
             if f.rule_id == "X0" and f.field == "payment_account"]
    assert "could not extract" in x0.message


def test_no_plaintext_account_or_routing_is_stored_or_logged(
        db: RunDb, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    _app, remit, document_id = _doc(db, seed=7)
    run_document(db.engine, document_id)
    dump: list[str] = []
    with db.engine.connect() as conn:
        for table in metadata.tables.values():
            dump.extend(str(row) for row in conn.execute(sa.select(table)).all())
    haystack = "\n".join(dump) + caplog.text
    assert remit.account not in haystack
    assert remit.routing not in haystack
```

If `pdfium.PdfDocument(plain)` cannot be saved after editing in this pypdfium2 version, build the unreadable-block PDF instead with the `generate` path: call `generate(seed=6, error="clean", remit=remit_for(6))` and filter nothing — then use `_render_into`'s sibling in the test by rendering a PDF whose routing value is non-numeric. Whatever route you pick, the assertion is the same: a label with no readable value yields exactly one X0 on `payment_account` and no I3.

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/run/test_payment_details.py -v`
Expected: the I3 tests FAIL (no `payment_account` finding is ever written); the clean/no-block and plaintext tests may already pass.

- [ ] **Step 4: Implement**

In `ledger_sense/run/document.py` add imports:

```python
from ledger_sense.domain.vendor import PaymentDetail, payment_hmac
from ledger_sense.extract.remit import extract_remit
from ledger_sense.rules.integrity.bank_details import assess_payment_detail
```

and extend the existing `from ledger_sense.extract.textlayer import has_text_layer` line to `from ledger_sense.extract.textlayer import extract_words, has_text_layer`. Add after `_vendor_rows`:

```python
def _payment_rows(conn: Connection, tenant_id: str, document_id: str, pdf: bytes,
                  app: PayApplication, now: datetime) -> list[dict[str, object]]:
    """I3, read-only like I2: the document's remit-to details against the
    vendor ledger's last CONFIRMED detail. The full account and routing numbers
    live only inside this frame — they are reduced to last-four plus HMAC at once
    and never reach a claim, a finding, a log line or an exception message.

    No bank block on the document means nothing to compare (no finding, no X0).
    A block that is present but unreadable is an X0 on `payment_account`, never a
    guess. A document with details is NEVER clean unless they equal the confirmed
    baseline: first-seen and changed are both hard stops."""
    result = extract_remit(extract_words(pdf))
    base: dict[str, object] = {
        "tenant_id": tenant_id, "document_id": document_id, "field": "payment_account",
        "state": "open", "created_at": now}
    if result.hit is None:
        if result.problem is None:
            return []
        return [{**base, "id": uuid.uuid4().hex, "rule_id": "X0",
                 "message": f"could not extract: {result.problem}",
                 "expected": None, "actual": None,
                 "page": None, "x0": None, "top": None, "x1": None, "bottom": None}]
    hit = result.hit
    incoming = PaymentDetail(
        account_last4=hit.account[-4:], routing_last4=hit.routing[-4:],
        account_hmac=payment_hmac(hit.account, hit.routing),
        observed_at=now, source_document=document_id)
    ledger = load_vendor_ledger(conn, tenant_id)
    vendor_id = ledger.resolve(app.vendor_name).vendor_id if app.vendor_name else None
    assessment = assess_payment_detail(ledger, vendor_id, incoming)
    if assessment is None:
        return []
    return [{**base, "id": uuid.uuid4().hex, "rule_id": "I3", "message": assessment.message,
             "expected": (None if assessment.expected_last4 is None
                          else f"....{assessment.expected_last4}"),
             "actual": f"....{assessment.actual_last4}",
             "page": hit.page, "x0": hit.bbox.x0, "top": hit.bbox.top,
             "x1": hit.bbox.x1, "bottom": hit.bbox.bottom}]
```

In `_execute`, add directly after the `_vendor_rows` line:

```python
        finding_rows += _payment_rows(conn, tenant_id, document_id, pdf, app, now)
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/run tests/api tests/test_degraded_mode.py -q`
Expected: all pass. `tests/api` documents carry no remit block, so no new findings appear there; if any existing test counted findings on a document and now fails, investigate before editing it.

- [ ] **Step 6: Lint, types, commit**

Run: `uv run ruff check ledger_sense tests ; uv run mypy --strict ledger_sense tests`

```bash
git add ledger_sense/run/document.py tests/run
git commit -m "feat(run): raise the I3 bank-detail hard stop from the document itself (LS-10)"
```

---

### Task 5: Review API — confirm, undo, and refusing every other way to clear I3

**Files:**
- Modify: `ledger_sense/api/review.py`, `ledger_sense/api/vendors.py`, `tests/api/conftest.py`
- Create: `tests/api/test_bank_change.py`

**Interfaces:**
- Consumes: `VendorLedger.record_payment_detail(..., attestation=...)`, `find_payment_detail`, `undo_payment_detail`, `extract_remit`, `load_document`, `blob_store_from_environment`.
- Produces: `POST /api/review/exceptions/{id}/confirm-bank-change` (body `{channel, contact, note?}`) → `{"status": "confirmed", "vendor_id": ...}`; `POST /api/review/exceptions/{id}/undo-confirm-bank-change` → `{"status": "open"}`. `accept`/`edit` return 422 for any finding on field `payment_account`; `reject` returns 422 for an I3 finding. `PaymentDetailOut` gains `confirmation_channel`, `confirmation_contact`, `confirmation_note`.

- [ ] **Step 1: Give the API test session the HMAC key**

In `tests/api/conftest.py`, in `_database`'s `with pytest.MonkeyPatch.context() as patch:` block add:

```python
        patch.setenv("LEDGER_SENSE_PAYMENT_HMAC_KEY", "test-key-not-a-secret")
```

- [ ] **Step 2: Write the failing tests** — `tests/api/test_bank_change.py`

```python
"""confirm-bank-change is the only way to clear an I3 hard stop (LS-10)."""

from datetime import UTC, datetime

from ledger_sense.boundary import Origin
from ledger_sense.corpus.generate import generate
from ledger_sense.corpus.remit import remit_for
from ledger_sense.ingest.intake import accept_document
from ledger_sense.run.document import run_document
from ledger_sense.store.blobs import blob_store_from_environment
from ledger_sense.store.documents import store_document
from ledger_sense.store.session import app_engine
from tests.api.conftest import TenantClient

ATTEST = {"channel": "phone", "contact": "J. Vendor", "note": "called the number on file"}


def _stored_and_run(tenant: TenantClient, seed: int, variant: str) -> str:
    remit = remit_for(seed, variant)  # type: ignore[arg-type]
    _c, _a, pdf = generate(seed=seed, error="clean", remit=remit)
    doc = accept_document(pdf, origin=Origin.CORPUS, provenance="tests/api/test_bank_change.py")
    with app_engine().begin() as conn:
        document_id = store_document(
            conn, tenant_id=tenant.tenant_id, doc=doc, blobs=blob_store_from_environment(),
            received_at=datetime.now(UTC), filename="payapp.pdf")
    run_document(app_engine(), document_id)
    return document_id


def _exception(tenant: TenantClient, document_id: str, rule_id: str) -> dict[str, object]:
    rows = tenant.get(f"/api/review/documents/{document_id}/exceptions").json()
    (row,) = [r for r in rows if r["rule_id"] == rule_id]
    assert isinstance(row, dict)
    return row


def _confirm_new_vendor(tenant: TenantClient, document_id: str) -> str:
    i2 = _exception(tenant, document_id, "I2")
    response = tenant.post_json(
        f"/api/review/exceptions/{i2['id']}/confirm-vendor",
        {"new_vendor_name": "Acme Steel Company"})
    assert response.status_code == 200, response.text
    return str(response.json()["vendor_id"])


def test_generic_accept_edit_and_reject_cannot_clear_the_hard_stop(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=2, variant="base")
    i3 = _exception(a, document_id, "I3")
    base = f"/api/review/exceptions/{i3['id']}"
    assert a.post(f"{base}/accept").status_code == 422
    assert a.post_json(f"{base}/edit", {"value": "123456789"}).status_code == 422
    assert a.post_json(f"{base}/reject", {"reason": "looks fine"}).status_code == 422
    assert _exception(a, document_id, "I3")["state"] == "open"


def test_confirm_needs_a_confirmed_vendor_first(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=2, variant="base")
    i3 = _exception(a, document_id, "I3")
    response = a.post_json(f"/api/review/exceptions/{i3['id']}/confirm-bank-change", ATTEST)
    assert response.status_code == 409
    assert _exception(a, document_id, "I3")["state"] == "open"


def test_an_attestation_without_a_named_contact_is_refused(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=2, variant="base")
    _confirm_new_vendor(a, document_id)
    i3 = _exception(a, document_id, "I3")
    response = a.post_json(f"/api/review/exceptions/{i3['id']}/confirm-bank-change",
                           {"channel": "phone", "contact": "  "})
    assert response.status_code == 422


def test_confirming_records_the_detail_with_its_evidence_and_never_the_hmac(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=3, variant="base")
    vendor_id = _confirm_new_vendor(a, document_id)
    i3 = _exception(a, document_id, "I3")
    response = a.post_json(f"/api/review/exceptions/{i3['id']}/confirm-bank-change", ATTEST)
    assert response.status_code == 200, response.text
    assert _exception(a, document_id, "I3")["state"] == "confirmed"
    ledger = a.get(f"/api/vendors/{vendor_id}/ledger").json()
    (detail,) = ledger["payment_history"]
    assert detail["account_last4"] == remit_for(3).account[-4:]
    assert detail["confirmed_by"]
    assert (detail["confirmation_channel"], detail["confirmation_contact"]) == (
        "phone", "J. Vendor")
    assert "account_hmac" not in detail


def test_a_changed_account_on_the_next_invoice_stops_again_and_undo_reopens_it(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    first = _stored_and_run(a, seed=4, variant="base")
    vendor_id = _confirm_new_vendor(a, first)
    first_i3 = _exception(a, first, "I3")
    assert a.post_json(f"/api/review/exceptions/{first_i3['id']}/confirm-bank-change",
                       ATTEST).status_code == 200

    second = _stored_and_run(a, seed=4, variant="changed")
    changed = _exception(a, second, "I3")
    assert changed["expected"] == f"....{remit_for(4).account[-4:]}"
    assert changed["actual"] == f"....{remit_for(4, 'changed').account[-4:]}"
    response = a.post_json(f"/api/review/exceptions/{changed['id']}/confirm-bank-change", ATTEST)
    assert response.status_code == 200, response.text
    assert len(a.get(f"/api/vendors/{vendor_id}/ledger").json()["payment_history"]) == 2

    undo = a.post(f"/api/review/exceptions/{changed['id']}/undo-confirm-bank-change")
    assert undo.status_code == 200
    assert _exception(a, second, "I3")["state"] == "open"
    assert len(a.get(f"/api/vendors/{vendor_id}/ledger").json()["payment_history"]) == 1


def test_another_tenant_cannot_confirm_or_undo(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, b = two_tenants
    document_id = _stored_and_run(a, seed=5, variant="base")
    _confirm_new_vendor(a, document_id)
    i3 = _exception(a, document_id, "I3")
    base = f"/api/review/exceptions/{i3['id']}"
    assert b.post_json(f"{base}/confirm-bank-change", ATTEST).status_code == 404
    assert b.post(f"{base}/undo-confirm-bank-change").status_code == 404


def test_confirm_only_applies_to_an_i3_exception(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    exc_id = a.seed_exception()  # an A2 finding
    assert a.post_json(f"/api/review/exceptions/{exc_id}/confirm-bank-change",
                       ATTEST).status_code == 422
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/api/test_bank_change.py -v`
Expected: FAIL — routes do not exist (404/405), accept/edit/reject do not refuse.

- [ ] **Step 4: Implement**

`ledger_sense/api/review.py`:
- imports: add `from ledger_sense.domain.vendor import Attestation, PaymentDetail, VendorLedger, payment_hmac`, `from ledger_sense.extract.remit import extract_remit`, `from ledger_sense.extract.textlayer import extract_words`, `from ledger_sense.store.blobs import blob_store_from_environment`, `from ledger_sense.store.documents import load_document`. `Literal`, `StrictStr`, `field_validator` are already imported.
- add near the other module constants and helpers:

```python
_BANK_FIELD = "payment_account"


def _refuse_bank_clearing(row: sa.Row[Any], *, action: str) -> None:
    """Never-rule 6. The I3 hard stop is cleared by ONE action, confirm-bank-change,
    which records who verified the details and how. accept/edit would write a
    claim carrying an account number; reject would dismiss the stop with a
    sentence. An X0 on the bank field (details present but unreadable) may still
    be rejected with a stated reason — there is no detail to attest."""
    if row.rule_id == "I3" or (row.field == _BANK_FIELD and action != "reject"):
        raise HTTPException(
            status_code=422,
            detail="bank-detail exceptions are cleared only by confirm-bank-change")


class ConfirmBankChangeBody(BaseModel):
    channel: Literal["phone", "in_person", "video_call", "other"]
    contact: StrictStr
    note: StrictStr | None = None

    @field_validator("contact")
    @classmethod
    def _named(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("an attestation must name who was spoken to")
        return value


def _vendor_for_bank_confirmation(conn: sa.Connection, ledger: VendorLedger,
                                  row: sa.Row[Any]) -> str:
    """The vendor whose bank details this document asserts: the one a reviewer
    confirmed (documents.vendor_id), else an EXACT, unambiguous ledger match.
    Never a fuzzy guess — confirming bank details against the wrong vendor is the
    attack this control exists to stop."""
    confirmed = conn.execute(
        sa.select(documents.c.vendor_id).where(documents.c.id == row.document_id)
    ).scalar_one()
    if confirmed is not None:
        return str(confirmed)
    observed = _observed_vendor_name(conn, row.document_id)
    if observed is not None:
        match = ledger.resolve(observed)
        if match.vendor_id is not None and not match.needs_confirmation:
            return match.vendor_id
    raise HTTPException(status_code=409,
                        detail="confirm the vendor (I2) before confirming its bank details")


def _reextract_detail(conn: sa.Connection, row: sa.Row[Any], session: Session) -> PaymentDetail:
    """Re-read the detail from the stored document instead of keeping a pending
    one anywhere: there is then no unconfirmed row to leak into the baseline, and
    what is confirmed is provably what the document says (its last four must
    match the finding the reviewer is looking at)."""
    pdf = load_document(conn, tenant_id=session.tenant_id, document_id=row.document_id,
                        blobs=blob_store_from_environment()).payload
    hit = extract_remit(extract_words(pdf)).hit
    if hit is None or f"....{hit.account[-4:]}" != row.actual:
        raise HTTPException(status_code=409,
                            detail="the document no longer matches this exception; re-run it")
    return PaymentDetail(
        account_last4=hit.account[-4:], routing_last4=hit.routing[-4:],
        account_hmac=payment_hmac(hit.account, hit.routing),
        observed_at=datetime.now(UTC), source_document=row.document_id)
```

- call the guard first thing after `_require_open(row)` in `accept` and `edit`: `_refuse_bank_clearing(row, action="accept")` / `action="edit"`; in `reject`: `_refuse_bank_clearing(row, action="reject")`.
- add the two endpoints after `undo_confirm_vendor`:

```python
@router.post("/exceptions/{exc_id}/confirm-bank-change")
def confirm_bank_change(exc_id: str, body: ConfirmBankChangeBody,
                        session: CurrentSession) -> dict[str, str | None]:
    """The ONLY way to clear I3 (Never-rule 6). Records the detail the document
    carries, with the reviewer as confirmer and their attestation as evidence."""
    with app_engine().begin() as conn:
        row = _get_finding(exc_id, session, conn)
        _require_open(row)
        if row.rule_id != "I3":
            raise HTTPException(status_code=422,
                                detail="confirm-bank-change only applies to an I3 exception")
        ledger = load_vendor_ledger_for_update(conn, session.tenant_id)
        vendor_id = _vendor_for_bank_confirmation(conn, ledger, row)
        detail = _reextract_detail(conn, row, session)
        ledger.record_payment_detail(
            vendor_id, detail, confirmed_by=session.user_id,
            attestation=Attestation(channel=body.channel, contact=body.contact,
                                    note=body.note))
        save_vendor_ledger(conn, session.tenant_id, ledger)
        _close(conn, exc_id, "confirmed")
    return {"status": "confirmed", "vendor_id": vendor_id}


@router.post("/exceptions/{exc_id}/undo-confirm-bank-change")
def undo_confirm_bank_change(exc_id: str, session: CurrentSession) -> dict[str, str | None]:
    """Reverses confirm_bank_change: removes the recorded detail by identity,
    restoring the previous baseline, and reopens the hard stop. No time limit — a
    wrong confirmation may only surface on a later audit."""
    with app_engine().begin() as conn:
        row = _get_finding(exc_id, session, conn)
        _require_confirmed(row)
        if row.rule_id != "I3":
            raise HTTPException(status_code=422,
                                detail="undo-confirm-bank-change only applies to an I3 exception")
        ledger = load_vendor_ledger_for_update(conn, session.tenant_id)
        found = ledger.find_payment_detail(row.document_id)
        if found is None:
            raise HTTPException(status_code=409, detail="exception already actioned")
        ledger.undo_payment_detail(found[0], found[1].id)
        save_vendor_ledger(conn, session.tenant_id, ledger)
        _transition(conn, exc_id, "confirmed", "open")
    return {"status": "open"}
```

`ledger_sense/api/vendors.py`: update the module docstring's first paragraph to say payment history is populated by the review queue's confirm-bank-change action (not "always []"); add to `PaymentDetailOut`:

```python
    confirmation_channel: str | None = None
    confirmation_contact: str | None = None
    confirmation_note: str | None = None
```

and pass `confirmation_channel=p.confirmation_channel, confirmation_contact=p.confirmation_contact, confirmation_note=p.confirmation_note` in the constructor. `account_hmac` stays off the model.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/api tests/run tests/boundary -q`
Expected: all pass. If an existing vendor-ledger API test asserts the exact JSON keys of a detail, add the three new keys there.

- [ ] **Step 6: Lint, types, commit**

Run: `uv run ruff check ledger_sense tests ; uv run mypy --strict ledger_sense tests`

```bash
git add ledger_sense/api tests/api
git commit -m "feat(review): confirm-bank-change is the only way to clear an I3 hard stop (LS-10)"
```

---

### Task 6: Ground truth across seeds, security review, docs

**Files:**
- Create: `tests/run/test_bank_change_ground_truth.py`
- Modify: `docs/superpowers/specs/2026-10-18-ls-10-bank-change-hard-stop-design.md` (apply the amendments above), `docs/security.md` (one paragraph)

- [ ] **Step 1: Write the ground-truth test**

```python
"""Synthetic ground truth for I3: every injected bank change is caught, every
clean control is not (Testing bar, Never-rule 6)."""

import pytest

from ledger_sense.corpus.errors import generate_bank_change_history
from ledger_sense.corpus.remit import remit_for
from ledger_sense.run.document import run_document
from tests.run.conftest import RunDb
from tests.run.test_payment_details import _confirm_baseline, _i3


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6, 7, 8])
def test_the_injected_bank_change_is_caught_and_the_clean_control_is_not(
        db: RunDb, seed: int) -> None:
    (_c1, app, original_pdf), (_c2, _app2, changed_pdf) = generate_bank_change_history(seed)
    _confirm_baseline(db, app, remit_for(seed, "base"))

    control = db.store_pdf(original_pdf)
    run_document(db.engine, control)
    assert _i3(db, control) == [], "clean control tripped I3"

    injected = db.store_pdf(changed_pdf)
    run_document(db.engine, injected)
    (finding,) = _i3(db, injected)
    assert finding.state == "open"
```

- [ ] **Step 2: Run it and the whole suite**

Run: `uv run pytest -q`
Expected: `1040 + new` passed, same skips, no failures.

- [ ] **Step 3: Quality gates**

Run: `uv run ruff check . ; uv run mypy --strict ledger_sense tests ; uv run python scripts/check_no_float.py`
Expected: clean.

- [ ] **Step 4: Security review**

Invoke the `security-review` skill (CLAUDE.md skill map: `PaymentDetail`) on the diff from e293fd2 to HEAD. Required focus: (a) no path writes a plaintext account/routing to DB, logs, API responses or exception text; (b) `confirm-bank-change` cannot be replayed to record a detail for a different vendor or tenant; (c) no route other than confirm-bank-change closes an I3. Fix any finding with its own test before continuing.

- [ ] **Step 5: Update the spec and security doc**

Apply "Spec amendments" 1–6 to the spec so spec and code agree (masked claims → no claims; reject refused; confirmed-only baseline; history helper; re-extraction at confirm; UI out of scope). Append to `docs/security.md` under the payment-key section: "Bank account and routing numbers are read from a document in memory only, reduced to last four plus HMAC, and never written to claims, findings, logs or API responses. The I3 hard stop is cleared only by `confirm-bank-change`, which stores the verifier's channel and contact."

- [ ] **Step 6: Commit**

```bash
git add tests/run/test_bank_change_ground_truth.py docs
git commit -m "test(run): I3 ground truth across seeds; docs record the bank-detail handling (LS-10)"
```

- [ ] **Step 7: Finish**

Invoke `superpowers:verification-before-completion`, then `superpowers:requesting-code-review`, then `superpowers:finishing-a-development-branch`.

---

## Not in this plan (follow-ups)

- Review-queue UI for confirm/undo bank change (LS-13: the browser workflow).
- Demo fixtures (`api/fixtures.py`) do not yet render a remit block, so the seeded demo never shows I3.
- Semantic duplicate detection, contract storage, and the scope-statement docs (LS-10 items 4–6).
- Construction convention rules still await domain authority.
- The bank-detail path has no Playwright keyboard test until the UI exists.

## Self-Review

1. **Spec coverage:** corpus + extraction (Task 2); runner I3 incl. first-seen and change-back (Tasks 3–4); confirm action, undo, refusals, writeback gate via existing open-findings count (Task 5); plaintext scan, ground truth, security review (Tasks 4, 6); match_quality fix (Task 1). Gap noted: no explicit test that writeback is blocked while I3 is open — covered because the LS-05 gate counts any `open` finding and I3 is written `open`; add a `progress` assertion if a reviewer wants it explicit.
2. **Placeholders:** none; every code step has code. Task 5 Step 4 and Task 3 Step 4 edit existing files by named anchors, with the new code shown in full.
3. **Type consistency:** `assess_payment_detail`/`BankAssessment`, `Attestation`, `find_payment_detail`/`undo_payment_detail`, `RemitHit`/`RemitResult`/`extract_remit`, `remit_for`/`Remit`, `generate_bank_change_history`, `_payment_rows`, `_predictor` are named identically wherever used. Finding `field` is `payment_account` in the runner, the API guard (`_BANK_FIELD`) and the tests.
