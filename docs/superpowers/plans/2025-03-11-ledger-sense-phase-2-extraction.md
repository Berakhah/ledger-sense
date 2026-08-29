# Ledger Sense — Phase 2: Extraction (Week 7)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a generated pay-application PDF into a `PayApplication` whose every field carries a `Claim` with a page and bounding box — deterministically, with no model call.

**Architecture:** Word-level text extraction with coordinates, a per-producer fingerprint, table reconstruction for the continuation sheet and anchor+geometry for the summary, assembled into Claims. A profile cache learns geometry from reviewer corrections. OCR handles the scanned tail by refusing rather than guessing.

**Tech Stack:** `pdfplumber` (MIT) for word extraction, `pypdfium2` (BSD-3/Apache-2.0) for rendering and metadata, `rapidocr-onnxruntime` (Apache-2.0) for the OCR tail, stdlib `sqlite3` for the profile cache.

**Spec:** `docs/superpowers/specs/2025-03-11-ledger-sense-design.md` §4.3
**Parent plan:** `2025-03-11-ledger-sense-weeks-5-12.md` — its Global Constraints and File Structure apply here in full.

---

## Phase preconditions

- Tasks 1–12 complete. Task 13 (boundary) is **not** required — Phase 2 reads only synthetic corpus bytes produced in-process by `corpus.generate`, which never crosses the intake door.
- If Task 13 has landed, `assemble` still takes raw `bytes`; the intake door wraps it upstream in Phase 4.

## Coordinate systems — read before Task 14

`pdfplumber` reports coordinates with the **origin at the top-left, `y` increasing downward**.
`pypdfium2` (used by `corpus/layouts.py` to render) uses the PDF convention: **origin bottom-left,
`y` increasing upward**. Task 14 converts once, at the boundary, and everything downstream is
top-left. Getting this wrong produces boxes that appear mirrored vertically in the review queue —
a bug that looks like a CSS problem and is not.

```
pdfplumber_top = PAGE_HEIGHT_PT - pdfium_y
```

---

### Task 13b: Baseline study screen

Spec §8 item 1. Tasks 10–11 built the engine and the printable report; this is the interactive instrument. It runs before any pipeline exists — it is what you sell in week one and the DoD 11 outreach asset.

**Files:**
- Create: `ledger_sense/api/baseline.py`
- Create: `ledger_sense/web/app/baseline/page.tsx`
- Test: `tests/api/test_baseline.py`
- Test: `tests/e2e/test_baseline_screen.spec.ts`

**Interfaces:**
- Consumes: `BaselineStudy`, `LoadedRate`, `TimedSample` (Task 10); `render_report` (Task 11).
- Produces: FastAPI router at `/api/baseline` with
  `POST /studies` → `{study_id}`,
  `POST /studies/{id}/samples` body `{document_id, doc_type, touch_seconds, exceptions}`,
  `PUT /studies/{id}/rate` body `{hourly, overhead_multiplier}` (both **strings**),
  `GET /studies/{id}/report` → `text/html`.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_baseline.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ledger_sense.api.baseline import router

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def _new_study():
    resp = client.post("/api/baseline/studies", json={
        "population_size": 500,
        "sampling_frame": "all AP documents received Aug 2026",
        "collection_period": ["2026-08-01", "2026-08-31"]})
    assert resp.status_code == 201
    return resp.json()["study_id"]


def test_samples_accumulate_and_report_renders():
    study_id = _new_study()
    for i, seconds in enumerate([300, 360, 420]):
        assert client.post(f"/api/baseline/studies/{study_id}/samples", json={
            "document_id": str(i), "doc_type": "pay_application",
            "touch_seconds": seconds, "exceptions": 0}).status_code == 201
    client.put(f"/api/baseline/studies/{study_id}/rate",
               json={"hourly": "30.00", "overhead_multiplier": "1.4"})
    report = client.get(f"/api/baseline/studies/{study_id}/report")
    assert report.status_code == 200
    assert "95% confidence interval" in report.text


def test_report_refuses_before_two_samples():
    study_id = _new_study()
    client.post(f"/api/baseline/studies/{study_id}/samples", json={
        "document_id": "1", "doc_type": "pay_application",
        "touch_seconds": 360, "exceptions": 0})
    client.put(f"/api/baseline/studies/{study_id}/rate",
               json={"hourly": "30.00", "overhead_multiplier": "1.4"})
    resp = client.get(f"/api/baseline/studies/{study_id}/report")
    assert resp.status_code == 409
    assert "at least 2" in resp.json()["detail"]


def test_rate_rejects_a_json_float_rather_than_coercing_it():
    study_id = _new_study()
    resp = client.put(f"/api/baseline/studies/{study_id}/rate",
                      json={"hourly": 30.0, "overhead_multiplier": "1.4"})
    assert resp.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_baseline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.api.baseline'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/api/baseline.py
"""Baseline study API. The billable week-one instrument.

Money arrives as strings. A JSON float in the rate body is REJECTED at the
schema, never coerced — this is the money path.
"""

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, StrictStr

from ledger_sense.baseline.report import render_report
from ledger_sense.baseline.study import BaselineStudy, LoadedRate, TimedSample
from ledger_sense.domain.money import money

router = APIRouter(prefix="/api/baseline")

# ponytail: in-process store. Swap for the Postgres session in Task 43, when the
# inbox needs studies to survive a restart.
_STUDIES: dict[str, BaselineStudy] = {}


class NewStudy(BaseModel):
    population_size: int
    sampling_frame: str
    collection_period: tuple[date, date]


class NewSample(BaseModel):
    document_id: str
    doc_type: str
    touch_seconds: int
    exceptions: int


class NewRate(BaseModel):
    hourly: StrictStr
    overhead_multiplier: StrictStr


def _get(study_id: str) -> BaselineStudy:
    if study_id not in _STUDIES:
        raise HTTPException(status_code=404, detail="study not found")
    return _STUDIES[study_id]


@router.post("/studies", status_code=201)
def create_study(body: NewStudy) -> dict[str, str]:
    study_id = str(uuid.uuid4())
    _STUDIES[study_id] = BaselineStudy(
        samples=[],
        loaded_rate=LoadedRate(hourly=money("0.00"), overhead_multiplier=Decimal("1")),
        population_size=body.population_size, sampling_frame=body.sampling_frame,
        collection_period=body.collection_period)
    return {"study_id": study_id}


@router.post("/studies/{study_id}/samples", status_code=201)
def add_sample(study_id: str, body: NewSample) -> dict[str, int]:
    study = _get(study_id)
    study.samples.append(TimedSample(**body.model_dump()))
    return {"count": len(study.samples)}


@router.put("/studies/{study_id}/rate")
def set_rate(study_id: str, body: NewRate) -> dict[str, str]:
    study = _get(study_id)
    study.loaded_rate = LoadedRate(
        hourly=money(body.hourly),
        overhead_multiplier=Decimal(body.overhead_multiplier))
    return {"effective_hourly": str(study.loaded_rate.effective_hourly())}


@router.get("/studies/{study_id}/report")
def get_report(study_id: str) -> Response:
    study = _get(study_id)
    try:
        html = render_report(study)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(content=html, media_type="text/html")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_baseline.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 5: Build the screen**

`ledger_sense/web/app/baseline/page.tsx` — four regions, `bok-ui` tokens, `compact` density:

- **Volume counter** — documents received in the period, a plain number input.
- **Timing instrument** — large start/stop control, running elapsed time in IBM Plex Mono with
  tabular numerals. `Space` starts and stops; `Enter` files the sample.
- **Loaded-rate input** — two text fields, **never `type="number"`**, which permits float coercion
  in the browser before the value ever reaches the strict schema.
- **Exception log** — a running list, one line per exception with a free-text note.

A "Produce report" button opens the Task-11 HTML in a new tab. Below two samples it is disabled
**with the reason rendered next to it** (`data-testid="report-blocked-reason"`), not silently greyed.

- [ ] **Step 6: Write the Playwright test**

```typescript
// tests/e2e/test_baseline_screen.spec.ts
import { expect, test } from '@playwright/test'

test('times three documents and produces a report with an interval', async ({ page }) => {
  await page.goto('/baseline')
  await page.getByLabel('Documents in period').fill('500')
  await page.getByLabel('Sampling frame').fill('all AP documents received Aug 2026')

  for (let i = 0; i < 3; i++) {
    await page.keyboard.press('Space')          // start
    await page.waitForTimeout(1100)
    await page.keyboard.press('Space')          // stop
    await page.keyboard.press('Enter')          // file the sample
  }
  await expect(page.getByTestId('sample-count')).toHaveText('3')

  await page.getByLabel('Hourly rate').fill('30.00')
  await page.getByLabel('Overhead multiplier').fill('1.4')

  const [report] = await Promise.all([
    page.waitForEvent('popup'),
    page.getByRole('button', { name: 'Produce report' }).click(),
  ])
  await expect(report.locator('body')).toContainText('95% confidence interval')
})

test('report button explains itself below two samples', async ({ page }) => {
  await page.goto('/baseline')
  await expect(page.getByRole('button', { name: 'Produce report' })).toBeDisabled()
  await expect(page.getByTestId('report-blocked-reason')).toContainText('at least 2')
})
```

- [ ] **Step 7: Run E2E and commit**

Run: `npx playwright test tests/e2e/test_baseline_screen.spec.ts`
Expected: PASS, 2 tests.

```bash
git add ledger_sense/api/baseline.py ledger_sense/web/app/baseline/ tests/api/test_baseline.py tests/e2e/test_baseline_screen.spec.ts
git commit -m "feat(baseline): interactive timing instrument and study screen"
```

---

### Task 14: Text layer with coordinates

**Files:**
- Create: `ledger_sense/extract/textlayer.py`
- Test: `tests/extract/test_textlayer.py`

**Interfaces:**
- Consumes: `corpus.generate` (Task 12), `corpus.layouts` constants.
- Produces:
  - `BBox = NamedTuple("BBox", [("x0", float), ("top", float), ("x1", float), ("bottom", float)])` — **top-left origin, `top` < `bottom`**
  - `Word = NamedTuple("Word", [("text", str), ("bbox", BBox), ("page", int)])`
  - `extract_words(pdf: bytes) -> list[Word]` — sorted page-major, then top, then left
  - `has_text_layer(pdf: bytes) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# tests/extract/test_textlayer.py
import pypdfium2 as pdfium

from ledger_sense.corpus.generate import generate
from ledger_sense.corpus.layouts import CONTINUATION_COLUMNS, PAGE_HEIGHT_PT
from ledger_sense.extract.textlayer import extract_words, has_text_layer


def _pdf(seed=1):
    _contract, _app, pdf = generate(seed=seed, error="clean")
    return pdf


def test_every_header_is_found_in_the_text_layer():
    text = " ".join(w.text for w in extract_words(_pdf()))
    for column in CONTINUATION_COLUMNS:
        assert column.header.split()[0] in text


def test_header_x_positions_match_the_layout_within_one_point():
    words = extract_words(_pdf())
    for column in CONTINUATION_COLUMNS:
        first_token = column.header.split()[0]
        hits = [w for w in words if w.text == first_token]
        assert hits, f"header token {first_token!r} not found"
        assert min(abs(w.bbox.x0 - column.x_pt) for w in hits) <= 1.0


def test_coordinates_use_a_top_left_origin():
    """pdfplumber is top-left; the renderer is bottom-left. The conversion
    happens here, once. The header row is near the top, so `top` must be small."""
    header = [w for w in extract_words(_pdf()) if w.text == "Scheduled"]
    assert header
    assert header[0].bbox.top < PAGE_HEIGHT_PT / 2
    assert header[0].bbox.top < header[0].bbox.bottom


def test_words_are_returned_in_reading_order():
    words = [w for w in extract_words(_pdf()) if w.page == 0]
    tops = [round(w.bbox.top, 1) for w in words]
    assert tops == sorted(tops), "words are not ordered top to bottom"


def test_has_text_layer_is_true_for_a_generated_document():
    assert has_text_layer(_pdf()) is True


def test_has_text_layer_is_false_for_an_image_only_pdf():
    blank = pdfium.PdfDocument.new()
    blank.new_page(612, 792)
    assert has_text_layer(blank.save_to_bytes()) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/extract/test_textlayer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.extract.textlayer'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/extract/textlayer.py
"""Word-level extraction with coordinates.

COORDINATE CONTRACT: everything downstream of this module uses a TOP-LEFT
origin with `top` increasing downward. pdfplumber already reports this way, so
the conversion is a no-op today — the contract is stated so that any future
backend swap converts HERE and nowhere else.
"""

import io
from typing import NamedTuple

import pdfplumber


class BBox(NamedTuple):
    x0: float
    top: float
    x1: float
    bottom: float


class Word(NamedTuple):
    text: str
    bbox: BBox
    page: int


def extract_words(pdf: bytes) -> list[Word]:
    words: list[Word] = []
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        for page_no, page in enumerate(doc.pages):
            for raw in page.extract_words(use_text_flow=False, keep_blank_chars=False):
                words.append(Word(
                    text=raw["text"],
                    bbox=BBox(float(raw["x0"]), float(raw["top"]),
                              float(raw["x1"]), float(raw["bottom"])),
                    page=page_no))
    words.sort(key=lambda w: (w.page, round(w.bbox.top, 1), w.bbox.x0))
    return words


def has_text_layer(pdf: bytes) -> bool:
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        return any((page.extract_text() or "").strip() for page in doc.pages)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/extract/test_textlayer.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/extract/textlayer.py tests/extract/test_textlayer.py
git commit -m "feat(extract): word extraction with a stated top-left coordinate contract"
```

---

### Task 15: Document fingerprint

**Files:**
- Create: `ledger_sense/ingest/fingerprint.py`
- Test: `tests/ingest/test_fingerprint.py`

**Interfaces:**
- Consumes: `extract_words` (Task 14).
- Produces:
  - `ProfileKey = NamedTuple("ProfileKey", [("producer", str), ("column_hash", str)])`
  - `ProfileKey.as_str() -> str` — stable cache key, `f"{producer}:{column_hash}"`
  - `fingerprint(pdf: bytes) -> ProfileKey`

- [ ] **Step 1: Write the failing test**

```python
# tests/ingest/test_fingerprint.py
from ledger_sense.corpus.generate import generate
from ledger_sense.ingest.fingerprint import fingerprint


def _pdf(seed):
    _contract, _app, pdf = generate(seed=seed, error="clean")
    return pdf


def test_same_layout_different_data_yields_the_same_key():
    """The fingerprint keys on GEOMETRY, not content. Two documents from the
    same producing software must share a profile."""
    assert fingerprint(_pdf(1)) == fingerprint(_pdf(2))


def test_a_shifted_column_layout_yields_a_different_key(monkeypatch):
    import ledger_sense.corpus.layouts as layouts
    baseline = fingerprint(_pdf(1))
    shifted = tuple(c._replace(x_pt=c.x_pt + 25.0) for c in layouts.CONTINUATION_COLUMNS)
    monkeypatch.setattr(layouts, "CONTINUATION_COLUMNS", shifted)
    assert fingerprint(_pdf(1)) != baseline


def test_key_is_stable_across_calls():
    assert fingerprint(_pdf(3)).as_str() == fingerprint(_pdf(3)).as_str()


def test_key_string_is_filesystem_and_sql_safe():
    assert all(ch.isalnum() or ch in ":-_." for ch in fingerprint(_pdf(1)).as_str())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/test_fingerprint.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.ingest.fingerprint'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/ingest/fingerprint.py
"""Identify the producing software so a learned profile can be reused.

Keys on geometry, never on content. Two pay applications from the same
accounting package share a key regardless of vendor, amounts or period.
"""

import hashlib
import io
import re
from typing import NamedTuple

import pdfplumber

from ledger_sense.extract.textlayer import extract_words

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


class ProfileKey(NamedTuple):
    producer: str
    column_hash: str

    def as_str(self) -> str:
        return f"{self.producer}:{self.column_hash}"


def _producer(pdf: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        raw = (doc.metadata or {}).get("Producer") or "unknown"
    return _UNSAFE.sub("-", str(raw))[:64]


def _column_hash(pdf: bytes) -> str:
    """Hash the distinct x-offsets on the busiest row band. Rounded to 2pt so
    sub-point rendering jitter does not fork the profile."""
    bands: dict[float, list[float]] = {}
    for word in extract_words(pdf):
        if word.page != 0:
            continue
        bands.setdefault(round(word.bbox.top / 4.0) * 4.0, []).append(word.bbox.x0)
    if not bands:
        return hashlib.sha256(b"").hexdigest()[:16]
    busiest = max(bands.values(), key=len)
    offsets = sorted({round(x / 2.0) * 2.0 for x in busiest})
    payload = ",".join(f"{x:.1f}" for x in offsets).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def fingerprint(pdf: bytes) -> ProfileKey:
    return ProfileKey(producer=_producer(pdf), column_hash=_column_hash(pdf))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingest/test_fingerprint.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/ingest/fingerprint.py tests/ingest/test_fingerprint.py
git commit -m "feat(ingest): geometry-keyed document fingerprint for profile reuse"
```

---

### Task 16: Continuation-sheet table reconstruction

**Files:**
- Create: `ledger_sense/extract/tables.py`
- Test: `tests/extract/test_tables.py`

**Interfaces:**
- Consumes: `Word`, `BBox` (Task 14); `corpus.layouts.CONTINUATION_COLUMNS`.
- Produces:
  - `HeaderMap = dict[str, BBox]` — semantic field name → header bbox
  - `find_headers(words: list[Word]) -> HeaderMap`
  - `Cell = NamedTuple("Cell", [("field", str), ("text", str), ("bbox", BBox), ("page", int)])`
  - `reconstruct_rows(words: list[Word], headers: HeaderMap) -> list[dict[str, Cell]]`
  - `MissingColumnError(Exception)`

- [ ] **Step 1: Write the failing test**

```python
# tests/extract/test_tables.py
import pytest

from ledger_sense.corpus.generate import generate, ground_truth
from ledger_sense.extract.tables import MissingColumnError, find_headers, reconstruct_rows
from ledger_sense.extract.textlayer import extract_words


def _rows(seed):
    _contract, app, pdf = generate(seed=seed, error="clean")
    words = extract_words(pdf)
    return app, reconstruct_rows(words, find_headers(words))


def test_headers_map_to_semantic_field_names():
    _c, _a, pdf = generate(seed=1, error="clean")
    headers = find_headers(extract_words(pdf))
    assert {"item_no", "scheduled_value", "previous", "this_period",
            "stored", "total_completed_and_stored", "retainage"} <= set(headers)


def test_row_count_matches_the_generated_line_count():
    app, rows = _rows(1)
    assert len(rows) == len(app.lines)


@pytest.mark.parametrize("seed", list(range(1, 21)))
def test_every_cell_matches_ground_truth(seed):
    """20 seeds. If reconstruction drifts on one layout it drifts on all."""
    _app, rows = _rows(seed)
    truth = ground_truth(seed=seed, error="clean")
    for row in rows:
        item_no = row["item_no"].text
        for field in ("scheduled_value", "previous", "this_period", "stored",
                      "total_completed_and_stored"):
            assert row[field].text == truth[f"line[{item_no}].{field}"]


def test_every_cell_carries_a_bbox_inside_its_column():
    _c, _a, pdf = generate(seed=1, error="clean")
    words = extract_words(pdf)
    headers = find_headers(words)
    for row in reconstruct_rows(words, headers):
        for field, cell in row.items():
            assert cell.bbox.x0 >= headers[field].x0 - 4.0


def test_a_missing_column_raises_rather_than_silently_dropping():
    """A dropped retainage column is a wrong bill, not a missing field."""
    _c, _a, pdf = generate(seed=1, error="clean")
    words = [w for w in extract_words(pdf) if w.text != "Retainage"]
    with pytest.raises(MissingColumnError, match="retainage"):
        reconstruct_rows(words, find_headers(words))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/extract/test_tables.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.extract.tables'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/extract/tables.py
"""Continuation-sheet grid reconstruction.

The continuation sheet IS a table, so recover it as one: find the header row,
band the remaining words into rows by vertical proximity, assign each word to
the column whose header it sits under.

A missing column RAISES. Silently dropping the retainage column produces a
plausible bill that is wrong, which is worse than a loud failure.
"""

from typing import NamedTuple

from ledger_sense.corpus.layouts import CONTINUATION_COLUMNS
from ledger_sense.extract.textlayer import BBox, Word

HeaderMap = dict[str, BBox]
ROW_BAND_PT = 6.0
COLUMN_SLACK_PT = 4.0

REQUIRED_FIELDS = frozenset(
    c.field for c in CONTINUATION_COLUMNS if c.field != "description")


class MissingColumnError(Exception):
    pass


class Cell(NamedTuple):
    field: str
    text: str
    bbox: BBox
    page: int


def _merge(words: list[Word]) -> tuple[str, BBox]:
    words = sorted(words, key=lambda w: w.bbox.x0)
    return (
        " ".join(w.text for w in words),
        BBox(words[0].bbox.x0, min(w.bbox.top for w in words),
             words[-1].bbox.x1, max(w.bbox.bottom for w in words)),
    )


def _band(words: list[Word]) -> dict[float, list[Word]]:
    bands: dict[float, list[Word]] = {}
    for word in words:
        bands.setdefault(round(word.bbox.top / ROW_BAND_PT) * ROW_BAND_PT, []).append(word)
    return bands


def find_headers(words: list[Word]) -> HeaderMap:
    """Match each column's header phrase against consecutive words on one band."""
    headers: HeaderMap = {}
    bands = _band(words)
    for column in CONTINUATION_COLUMNS:
        tokens = column.header.split()
        for band in bands.values():
            ordered = sorted(band, key=lambda w: w.bbox.x0)
            for i in range(len(ordered) - len(tokens) + 1):
                window = ordered[i:i + len(tokens)]
                if [w.text for w in window] == tokens:
                    _text, bbox = _merge(window)
                    headers[column.field] = bbox
                    break
            if column.field in headers:
                break
    return headers


def _column_for(word: Word, headers: HeaderMap) -> str | None:
    candidates = [(field, bbox) for field, bbox in headers.items()
                  if word.bbox.x0 >= bbox.x0 - COLUMN_SLACK_PT]
    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[1].x0)[0]


def reconstruct_rows(words: list[Word], headers: HeaderMap) -> list[dict[str, Cell]]:
    missing = REQUIRED_FIELDS - set(headers)
    if missing:
        raise MissingColumnError(
            f"continuation sheet is missing columns: {sorted(missing)}")

    header_bottom = max(bbox.bottom for bbox in headers.values())
    body = [w for w in words if w.bbox.top > header_bottom]

    rows: list[dict[str, Cell]] = []
    for band_top in sorted(_band(body)):
        grouped: dict[str, list[Word]] = {}
        for word in _band(body)[band_top]:
            field = _column_for(word, headers)
            if field is not None:
                grouped.setdefault(field, []).append(word)
        if not REQUIRED_FIELDS <= set(grouped):
            continue  # not a data row: footer, page number, continuation note
        row: dict[str, Cell] = {}
        for field, group in grouped.items():
            text, bbox = _merge(group)
            row[field] = Cell(field=field, text=text, bbox=bbox, page=group[0].page)
        rows.append(row)
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/extract/test_tables.py -v`
Expected: PASS, 24 tests (4 plus 20 parametrised).

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/extract/tables.py tests/extract/test_tables.py
git commit -m "feat(extract): continuation-sheet grid reconstruction, missing column raises"
```

---

### Task 17: Summary anchor + geometry extraction

The summary certificate is a **form**, not a table — labels with values beside or below them. Different algorithm, same coordinate contract.

**Files:**
- Create: `ledger_sense/extract/anchors.py`
- Test: `tests/extract/test_anchors.py`

**Interfaces:**
- Consumes: `Word`, `BBox` (Task 14); `corpus.layouts.SUMMARY_FIELDS`.
- Produces:
  - `MONEY_TOKEN: re.Pattern` — what counts as a value-shaped token
  - `Anchored = NamedTuple("Anchored", [("field", str), ("text", str), ("bbox", BBox), ("page", int)])`
  - `extract_summary(words: list[Word]) -> dict[str, Anchored]`

- [ ] **Step 1: Write the failing test**

```python
# tests/extract/test_anchors.py
from ledger_sense.corpus.generate import generate
from ledger_sense.extract.anchors import MONEY_TOKEN, extract_summary
from ledger_sense.extract.textlayer import extract_words


def _summary(seed=1):
    _contract, app, pdf = generate(seed=seed, error="clean")
    return app, extract_summary(extract_words(pdf))


def test_money_token_accepts_plain_and_formatted_amounts():
    for good in ["1234.56", "1,234.56", "$1,234.56", "0.00", "(1,234.56)"]:
        assert MONEY_TOKEN.fullmatch(good), good


def test_money_token_rejects_prose_and_bare_integers():
    for bad in ["Sitework", "Item", "003", "N/A"]:
        assert not MONEY_TOKEN.fullmatch(bad), bad


def test_summary_total_matches_the_generated_application():
    app, summary = _summary()
    cleaned = summary["total_completed_and_stored"].text.replace(",", "").lstrip("$")
    assert cleaned == str(app.total_completed_and_stored)


def test_each_anchored_value_carries_a_non_degenerate_bbox():
    _app, summary = _summary()
    for anchored in summary.values():
        assert anchored.bbox.x1 > anchored.bbox.x0
        assert anchored.bbox.bottom > anchored.bbox.top


def test_an_absent_label_is_omitted_rather_than_guessed():
    """No label, no value. Never take the nearest number and hope."""
    _c, _a, pdf = generate(seed=1, error="clean")
    words = [w for w in extract_words(pdf) if w.text != "Retainage"]
    assert "retainage_total" not in extract_summary(words)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/extract/test_anchors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.extract.anchors'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/extract/anchors.py
"""Summary-certificate extraction by label anchor plus geometry.

Find the label, take the nearest value-shaped token to its right on the same
band, or directly below if the row is empty to the right.

If the label is absent, the field is ABSENT. Never take the nearest number and
hope — a guessed contract sum is a wrong bill with a confident number on it.
"""

import re
from typing import NamedTuple

from ledger_sense.corpus.layouts import SUMMARY_FIELDS
from ledger_sense.extract.textlayer import BBox, Word

MONEY_TOKEN = re.compile(r"\(?\$?-?\d{1,3}(,\d{3})*\.\d{2}\)?|\(?\$?-?\d+\.\d{2}\)?")
SAME_BAND_PT = 6.0
BELOW_BAND_PT = 20.0


class Anchored(NamedTuple):
    field: str
    text: str
    bbox: BBox
    page: int


def _find_label(words: list[Word], label: str) -> list[Word] | None:
    tokens = label.split()
    for i in range(len(words) - len(tokens) + 1):
        window = words[i:i + len(tokens)]
        if [w.text for w in window] != tokens:
            continue
        spread = max(w.bbox.top for w in window) - min(w.bbox.top for w in window)
        if spread > SAME_BAND_PT:
            continue
        return window
    return None


def _value_near(words: list[Word], label_words: list[Word]) -> Word | None:
    label_right = max(w.bbox.x1 for w in label_words)
    label_top = min(w.bbox.top for w in label_words)
    page = label_words[0].page

    same_band = [w for w in words
                 if w.page == page and w.bbox.x0 >= label_right
                 and abs(w.bbox.top - label_top) <= SAME_BAND_PT
                 and MONEY_TOKEN.fullmatch(w.text)]
    if same_band:
        return min(same_band, key=lambda w: w.bbox.x0)

    below = [w for w in words
             if w.page == page and 0 < w.bbox.top - label_top <= BELOW_BAND_PT
             and MONEY_TOKEN.fullmatch(w.text)]
    if below:
        return min(below, key=lambda w: (w.bbox.top, w.bbox.x0))
    return None


def extract_summary(words: list[Word]) -> dict[str, Anchored]:
    found: dict[str, Anchored] = {}
    for label, field in SUMMARY_FIELDS:
        label_words = _find_label(words, label)
        if label_words is None:
            continue
        value = _value_near(words, label_words)
        if value is None:
            continue
        found[field] = Anchored(field=field, text=value.text,
                                bbox=value.bbox, page=value.page)
    return found
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/extract/test_anchors.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/extract/anchors.py tests/extract/test_anchors.py
git commit -m "feat(extract): summary anchor+geometry, absent label means absent field"
```

---

### Task 18: Claim assembly

Where extraction meets the domain. **Every field gets a `Claim` with `method=DETERMINISTIC`, `confidence=None`, and a `FileSource(path, page, bbox)`.**

**Files:**
- Create: `ledger_sense/extract/assemble.py`
- Test: `tests/extract/test_assemble.py`

**Interfaces:**
- Consumes: Tasks 14, 16, 17; `PayApplication`, `ContinuationLine`, `money`; `bok_core.provenance.{Claim, Method, FileSource}`.
- Produces:
  - `UnextractableField = NamedTuple("UnextractableField", [("field", str), ("reason", str)])`
  - `assemble_pay_application(pdf: bytes, source_path: str, _drop_labels: frozenset[str] = frozenset()) -> PayApplication`
  - `provenance["_unextractable"]` holds `list[UnextractableField]`; all other keys hold `Claim`

- [ ] **Step 1: Write the failing test**

```python
# tests/extract/test_assemble.py
import ast
import pathlib

from bok_core.provenance import Method

from ledger_sense.corpus.generate import generate
from ledger_sense.extract.assemble import assemble_pay_application


def test_assembled_application_matches_the_generated_one():
    _contract, app, pdf = generate(seed=1, error="clean")
    built = assemble_pay_application(pdf, "corpus://seed-1")
    assert built.total_completed_and_stored == app.total_completed_and_stored
    assert len(built.lines) == len(app.lines)


def test_every_populated_field_carries_a_deterministic_claim():
    _c, _a, pdf = generate(seed=1, error="clean")
    built = assemble_pay_application(pdf, "corpus://seed-1")
    claims = {k: v for k, v in built.provenance.items() if not k.startswith("_")}
    assert claims, "no provenance recorded"
    for field, claim in claims.items():
        assert claim.method is Method.DETERMINISTIC, field
        assert claim.confidence is None, f"{field}: deterministic claims carry no confidence"


def test_every_claim_points_at_a_page_and_a_box():
    _c, _a, pdf = generate(seed=1, error="clean")
    built = assemble_pay_application(pdf, "corpus://seed-1")
    for field, claim in built.provenance.items():
        if field.startswith("_"):
            continue
        assert claim.source.page >= 0, field
        x0, top, x1, bottom = claim.source.bbox
        assert x1 > x0 and bottom > top, f"{field}: degenerate bbox"


def test_a_missing_field_is_recorded_not_guessed():
    _c, _a, pdf = generate(seed=1, error="clean")
    built = assemble_pay_application(pdf, "corpus://seed-1",
                                     _drop_labels=frozenset({"Retainage"}))
    assert "retainage_total" in {u.field for u in built.provenance["_unextractable"]}


def test_no_module_outside_assemble_constructs_a_domain_model():
    """extract/ emits Claims; only assemble.py builds domain objects from text."""
    offenders = []
    for path in pathlib.Path("ledger_sense/extract").rglob("*.py"):
        if path.name == "assemble.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") in {
                    "PayApplication", "ContinuationLine", "Contract"}:
                offenders.append(f"{path}:{node.lineno}")
    assert offenders == [], f"domain model built outside assemble.py: {offenders}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/extract/test_assemble.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.extract.assemble'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/extract/assemble.py
"""Extraction -> domain, via Claims.

Every value reaching the domain model carries a Claim recording how it was
obtained and exactly where on the page it came from.

A field we cannot extract is RECORDED as unextractable, never guessed — B11.
"""

from datetime import date, datetime, timezone
from typing import NamedTuple

from bok_core.provenance import Claim, FileSource, Method

from ledger_sense.domain.models import ContinuationLine, PayApplication
from ledger_sense.domain.money import money
from ledger_sense.extract.anchors import extract_summary
from ledger_sense.extract.tables import find_headers, reconstruct_rows
from ledger_sense.extract.textlayer import BBox, Word, extract_words

_LINE_FIELDS = ("scheduled_value", "previous", "this_period", "stored",
                "total_completed_and_stored", "retainage")
_SUMMARY_MONEY = ("change_orders_net", "retainage_total", "previous_payments",
                  "total_completed_and_stored")


class UnextractableField(NamedTuple):
    field: str
    reason: str


def _clean(text: str) -> str:
    text = text.strip().replace(",", "").lstrip("$")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    return text


def _claim(source_path: str, page: int, bbox: BBox) -> Claim:
    return Claim(
        method=Method.DETERMINISTIC,
        confidence=None,
        observed_at=datetime.now(timezone.utc),
        source=FileSource(path=source_path, page=page,
                          bbox=(bbox.x0, bbox.top, bbox.x1, bbox.bottom)))


def assemble_pay_application(
    pdf: bytes, source_path: str, _drop_labels: frozenset[str] = frozenset()
) -> PayApplication:
    words: list[Word] = [w for w in extract_words(pdf) if w.text not in _drop_labels]
    provenance: dict[str, object] = {}
    unextractable: list[UnextractableField] = []

    headers = find_headers(words)
    lines: list[ContinuationLine] = []
    for row in reconstruct_rows(words, headers):
        item_no = row["item_no"].text
        values = {}
        for field in _LINE_FIELDS:
            cell = row[field]
            values[field] = money(_clean(cell.text))
            provenance[f"line[{item_no}].{field}"] = _claim(
                source_path, cell.page, cell.bbox)
        lines.append(ContinuationLine(
            item_no=item_no,
            description=row["description"].text if "description" in row else "",
            **values))

    summary = extract_summary(words)
    totals = {}
    for field in _SUMMARY_MONEY:
        if field not in summary:
            unextractable.append(UnextractableField(field, "label not found on page"))
            totals[field] = money("0.00")
            continue
        anchored = summary[field]
        totals[field] = money(_clean(anchored.text))
        provenance[field] = _claim(source_path, anchored.page, anchored.bbox)

    provenance["_unextractable"] = unextractable
    return PayApplication(
        contract_id="", application_no=0, period_to=date.today(),
        lines=lines, provenance=provenance, **totals)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/extract/test_assemble.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/extract/assemble.py tests/extract/test_assemble.py
git commit -m "feat(extract): Claim assembly with page+bbox provenance on every field"
```

---

### Task 19: Learned profile cache

**The commercial idea in code.** A reviewer correction teaches the deterministic extractor. **Geometry only, never a value** — a cached value would carry one client's data into another client's extraction.

**Files:**
- Create: `ledger_sense/extract/profiles.py`
- Test: `tests/extract/test_profiles.py`

**Interfaces:**
- Consumes: `ProfileKey` (Task 15), `BBox` (Task 14).
- Produces: `ProfileCache(db_path: str)` with `lookup(key, field) -> BBox | None`, `learn(key, field, bbox) -> None`, `close() -> None`.
- Schema: `profiles(key TEXT, field TEXT, x0 REAL, top REAL, x1 REAL, bottom REAL, hits INTEGER, PRIMARY KEY (key, field))` — **no value column, by design and by test**.

- [ ] **Step 1: Write the failing test**

```python
# tests/extract/test_profiles.py
import sqlite3

import pytest

from ledger_sense.extract.profiles import ProfileCache
from ledger_sense.extract.textlayer import BBox
from ledger_sense.ingest.fingerprint import ProfileKey

KEY = ProfileKey(producer="AcmeBooks-9", column_hash="abc123")
BOX = BBox(100.0, 200.0, 180.0, 212.0)


@pytest.fixture()
def cache(tmp_path):
    c = ProfileCache(str(tmp_path / "profiles.db"))
    yield c
    c.close()


def test_lookup_misses_before_anything_is_learned(cache):
    assert cache.lookup(KEY, "retainage_total") is None


def test_a_correction_is_reused_on_the_next_document(cache):
    cache.learn(KEY, "retainage_total", BOX)
    assert cache.lookup(KEY, "retainage_total") == BOX


def test_a_different_fingerprint_does_not_share_geometry(cache):
    cache.learn(KEY, "retainage_total", BOX)
    other = ProfileKey(producer="OtherBooks-2", column_hash="def456")
    assert cache.lookup(other, "retainage_total") is None


def test_relearning_overwrites(cache):
    cache.learn(KEY, "retainage_total", BOX)
    newer = BBox(105.0, 200.0, 185.0, 212.0)
    cache.learn(KEY, "retainage_total", newer)
    assert cache.lookup(KEY, "retainage_total") == newer


def test_the_cache_stores_geometry_only_and_never_a_value(cache, tmp_path):
    """A cached VALUE would leak one client's data into another's extraction."""
    cache.learn(KEY, "retainage_total", BOX)
    cache.close()
    conn = sqlite3.connect(str(tmp_path / "profiles.db"))
    columns = {row[1] for row in conn.execute("PRAGMA table_info(profiles)")}
    conn.close()
    assert columns == {"key", "field", "x0", "top", "x1", "bottom", "hits"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/extract/test_profiles.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.extract.profiles'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/extract/profiles.py
"""Learned extraction geometry, keyed by document fingerprint.

The review queue is the training signal for the DETERMINISTIC extractor. No
model, no inference cost, entirely inside the data boundary — which is what
makes "the product improves as you use it" true without an asterisk.

GEOMETRY ONLY. Never a value. This cache is shared per deployment, so a cached
value would carry one client's data into another client's extraction.
"""

import sqlite3

from ledger_sense.extract.textlayer import BBox
from ledger_sense.ingest.fingerprint import ProfileKey

_SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    key    TEXT NOT NULL,
    field  TEXT NOT NULL,
    x0     REAL NOT NULL,
    top    REAL NOT NULL,
    x1     REAL NOT NULL,
    bottom REAL NOT NULL,
    hits   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (key, field)
)
"""


class ProfileCache:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def lookup(self, key: ProfileKey, field: str) -> BBox | None:
        row = self._conn.execute(
            "SELECT x0, top, x1, bottom FROM profiles WHERE key = ? AND field = ?",
            (key.as_str(), field)).fetchone()
        if row is None:
            return None
        self._conn.execute(
            "UPDATE profiles SET hits = hits + 1 WHERE key = ? AND field = ?",
            (key.as_str(), field))
        self._conn.commit()
        return BBox(*row)

    def learn(self, key: ProfileKey, field: str, bbox: BBox) -> None:
        self._conn.execute(
            "INSERT INTO profiles (key, field, x0, top, x1, bottom, hits) "
            "VALUES (?, ?, ?, ?, ?, ?, 0) "
            "ON CONFLICT (key, field) DO UPDATE SET "
            "x0 = excluded.x0, top = excluded.top, "
            "x1 = excluded.x1, bottom = excluded.bottom",
            (key.as_str(), field, bbox.x0, bbox.top, bbox.x1, bbox.bottom))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/extract/test_profiles.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/extract/profiles.py tests/extract/test_profiles.py
git commit -m "feat(extract): profile cache learning geometry from reviewer corrections"
```

---

### Task 20: Classification and the OCR tail

**Files:**
- Create: `ledger_sense/ingest/classify.py`
- Create: `ledger_sense/extract/ocr.py`
- Test: `tests/ingest/test_classify.py`
- Test: `tests/extract/test_ocr.py`

**Interfaces:**
- Consumes: `extract_words`, `Word`, `BBox` (Task 14).
- Produces:
  - `DocType = Literal["pay_application", "continuation_sheet", "invoice", "lien_waiver", "unknown"]`
  - `classify(pdf: bytes) -> DocType`
  - `CannotExtract = NamedTuple("CannotExtract", [("reason", str), ("page", int)])`
  - `ocr_words(pdf: bytes) -> list[Word] | CannotExtract`

- [ ] **Step 1: Write the failing tests**

```python
# tests/ingest/test_classify.py
import pypdfium2 as pdfium

from ledger_sense.corpus.generate import generate
from ledger_sense.ingest.classify import classify


def test_a_generated_document_classifies_as_a_continuation_sheet():
    _c, _a, pdf = generate(seed=1, error="clean")
    assert classify(pdf) == "continuation_sheet"


def test_an_empty_pdf_classifies_as_unknown_not_as_a_guess():
    """Unknown routes to review. A confident wrong type is worse than a shrug."""
    blank = pdfium.PdfDocument.new()
    blank.new_page(612, 792)
    assert classify(blank.save_to_bytes()) == "unknown"
```

```python
# tests/extract/test_ocr.py
import pypdfium2 as pdfium

from ledger_sense.corpus.generate import generate
from ledger_sense.extract.ocr import CannotExtract, ocr_words


def _rasterise(pdf: bytes) -> bytes:
    """Render to an image and back — destroys the text layer, like a scan."""
    doc = pdfium.PdfDocument(pdf)
    image = doc[0].render(scale=2).to_pil()
    out = pdfium.PdfDocument.new()
    page = out.new_page(image.width / 2, image.height / 2)
    pdf_image = pdfium.PdfImage.new(out)
    pdf_image.set_bitmap(pdfium.PdfBitmap.from_pil(image))
    pdf_image.set_matrix(pdfium.PdfMatrix().scale(image.width / 2, image.height / 2))
    page.insert_obj(pdf_image)
    page.gen_content()
    return out.save_to_bytes()


def test_ocr_returns_words_for_a_rasterised_document():
    _c, _a, pdf = generate(seed=1, error="clean")
    result = ocr_words(_rasterise(pdf))
    assert not isinstance(result, CannotExtract)
    assert any("Schedul" in w.text for w in result)


def test_ocr_refuses_a_blank_page_rather_than_returning_noise():
    """B11: route to review with an honest 'cannot extract' rather than guess."""
    blank = pdfium.PdfDocument.new()
    blank.new_page(612, 792)
    result = ocr_words(blank.save_to_bytes())
    assert isinstance(result, CannotExtract)
    assert result.page == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/test_classify.py tests/extract/test_ocr.py -v`
Expected: FAIL — `ModuleNotFoundError` for both modules.

- [ ] **Step 3: Write the classifier**

```python
# ledger_sense/ingest/classify.py
"""Rules-based document classification. No model.

"unknown" is a valid answer and routes to review. A confident wrong type is
worse than an honest shrug.
"""

from typing import Literal

from ledger_sense.extract.textlayer import extract_words

DocType = Literal["pay_application", "continuation_sheet", "invoice",
                  "lien_waiver", "unknown"]

_MARKERS: tuple[tuple[DocType, tuple[str, ...]], ...] = (
    ("continuation_sheet", ("scheduled value", "total completed and stored")),
    ("pay_application", ("contract sum to date", "current payment due")),
    ("lien_waiver", ("waiver", "lien")),
    ("invoice", ("invoice", "remit to")),
)


def classify(pdf: bytes) -> DocType:
    text = " ".join(w.text for w in extract_words(pdf)).lower()
    for doc_type, markers in _MARKERS:
        if all(marker in text for marker in markers):
            return doc_type
    return "unknown"
```

- [ ] **Step 4: Write the OCR tail**

```python
# ledger_sense/extract/ocr.py
"""OCR for the scanned minority. RapidOCR (Apache-2.0) via ONNX.

NEVER guess. A phone photo of a crumpled invoice defeats geometry-based
extraction; the correct behaviour is CannotExtract and a route to review.
"""

from typing import NamedTuple

import numpy as np
import pypdfium2 as pdfium
from rapidocr_onnxruntime import RapidOCR

from ledger_sense.extract.textlayer import BBox, Word

MIN_CONFIDENCE = 0.5
RENDER_SCALE = 2
_ENGINE = RapidOCR()


class CannotExtract(NamedTuple):
    reason: str
    page: int


def ocr_words(pdf: bytes) -> list[Word] | CannotExtract:
    doc = pdfium.PdfDocument(pdf)
    words: list[Word] = []
    for page_no, page in enumerate(doc):
        image = np.asarray(page.render(scale=RENDER_SCALE).to_pil().convert("RGB"))
        result, _elapsed = _ENGINE(image)
        if not result:
            return CannotExtract("no text recovered from the page image", page_no)
        for box, text, confidence in result:
            if confidence < MIN_CONFIDENCE:
                continue
            xs = [point[0] / RENDER_SCALE for point in box]
            ys = [point[1] / RENDER_SCALE for point in box]
            words.append(Word(text=text,
                              bbox=BBox(min(xs), min(ys), max(xs), max(ys)),
                              page=page_no))
    if not words:
        return CannotExtract("every recovered token was below the confidence floor", 0)
    words.sort(key=lambda w: (w.page, round(w.bbox.top, 1), w.bbox.x0))
    return words
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/ingest/test_classify.py tests/extract/test_ocr.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 6: Commit**

```bash
git add ledger_sense/ingest/classify.py ledger_sense/extract/ocr.py tests/ingest/test_classify.py tests/extract/test_ocr.py
git commit -m "feat(extract): rules classifier and an OCR tail that refuses rather than guesses"
```

---

### Task 21: Degraded-mode proof — **DoD 2**

**Files:**
- Create: `ledger_sense/__main__.py`
- Create: `tests/test_degraded_mode.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: everything in Phases 1 and 2.
- Produces: `python -m ledger_sense extract <path>` printing a JSON summary — the dev entrypoint, no CLI framework, no `[project.scripts]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_degraded_mode.py
"""DoD 2 and 00 §R3: with every provider disabled, the deterministic path
extracts and validates digital-PDF pay applications end to end."""

import pytest

from ledger_sense.corpus.generate import generate
from ledger_sense.extract.assemble import assemble_pay_application
from ledger_sense.rules.arithmetic.foot import rule_a1, rule_a9
from ledger_sense.rules.arithmetic.lines import rule_a2, rule_a3, rule_a4
from ledger_sense.rules.arithmetic.payment import rule_a5, rule_a6, rule_a8

ALL_RULES = [rule_a1, rule_a2, rule_a3, rule_a4, rule_a5, rule_a6, rule_a8, rule_a9]


@pytest.fixture(autouse=True)
def every_provider_disabled(monkeypatch):
    monkeypatch.setenv("BOK_PROVIDERS_DISABLED", "1")

    def _explode(*args, **kwargs):
        raise AssertionError("a model call was attempted in degraded mode")

    import bok_core.gateway
    monkeypatch.setattr(bok_core.gateway.Gateway, "call", _explode)


@pytest.mark.parametrize("seed", list(range(1, 11)))
def test_clean_documents_extract_and_validate_with_no_model(seed):
    contract, _app, pdf = generate(seed=seed, error="clean")
    built = assemble_pay_application(pdf, f"corpus://seed-{seed}")
    assert built.provenance["_unextractable"] == []
    for rule in ALL_RULES:
        assert rule(built, contract) == [], f"{rule.__name__} fired on a clean document"


@pytest.mark.parametrize("error,expected_rule", [
    ("A1_footing", "A1"), ("A2_line_sum", "A2"), ("A8_retainage", "A8")])
def test_injected_errors_are_caught_with_no_model(error, expected_rule):
    contract, _app, pdf = generate(seed=1, error=error)
    built = assemble_pay_application(pdf, "corpus://seed-1")
    fired = {f.rule_id for rule in ALL_RULES for f in rule(built, contract)}
    assert expected_rule in fired
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_degraded_mode.py -v`
Expected: FAIL. The likely first failure is that `assemble_pay_application` returns a
`PayApplication` whose summary fields were not rendered onto the page by `corpus.layouts`
(which renders the continuation grid only). **Fix by extending `_render` in `corpus/generate.py`
to also emit the `SUMMARY_FIELDS` label/value pairs — not by weakening these assertions.**

- [ ] **Step 3: Write the dev entrypoint**

```python
# ledger_sense/__main__.py
"""Dev entrypoint. Not a product surface — the buyer entrypoint is the web UI.

    python -m ledger_sense extract path/to/document.pdf
"""

import json
import pathlib
import sys

from ledger_sense.extract.assemble import assemble_pay_application
from ledger_sense.ingest.classify import classify


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] != "extract":
        print("usage: python -m ledger_sense extract <path>", file=sys.stderr)
        return 2
    path = pathlib.Path(argv[2])
    pdf = path.read_bytes()
    app = assemble_pay_application(pdf, str(path))
    print(json.dumps({
        "doc_type": classify(pdf),
        "lines": len(app.lines),
        "total_completed_and_stored": str(app.total_completed_and_stored),
        "unextractable": [u.field for u in app.provenance["_unextractable"]],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_degraded_mode.py -v`
Expected: PASS, 13 tests (10 parametrised seeds plus 3 error classes). **DoD 2.**

- [ ] **Step 5: Add the CI job and commit**

```yaml
  degraded-mode:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --all-extras
      - run: uv run pytest tests/test_degraded_mode.py -v
```

```bash
git add ledger_sense/__main__.py tests/test_degraded_mode.py .github/workflows/ci.yml
git commit -m "test: degraded-mode proof, full pipeline with every provider disabled"
```

---

## Phase 2 exit criteria

- [ ] `uv run pytest tests/extract tests/ingest tests/api tests/test_degraded_mode.py` green
- [ ] `uv run python -m scripts.check_no_float`, `scripts.licence_audit`, `scripts.aia_guard` green
- [ ] `uv run mypy --strict ledger_sense` green
- [ ] `npx playwright test tests/e2e/test_baseline_screen.spec.ts` green
- [ ] **DoD 2 satisfied** — deterministic extraction handles digital-PDF pay applications end to end with no model
- [ ] Coverage ≥75% on `ledger_sense/extract` and `ledger_sense/ingest`

**Then Phase 3 — but only if the week-5 controller session has closed.** `rules/registry.py` refuses every convention rule until it has a `RuleSource` with a named `confirmed_by`. That is not a formality you can route around; it is the whole Q7 mitigation.
