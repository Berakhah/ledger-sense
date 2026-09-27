"""Deterministic synthetic pay applications at known ground truth.

This is a first-class deliverable, not a fixture. The demo runs on it.

pypdfium2 5.13.0 (the version pinned in this repo) has no `PdfPage.insert_text`,
`PdfDocument.add_font_helv` or `PdfDocument.save_to_bytes` — those are a
different major's API. Text is inserted through the low-level ctypes bindings
in `pypdfium2.raw` instead: create a text object, set its content, transform it
into place, insert it into the page, then `page.gen_content()` once all objects
are placed and `pdf.save(buf)` to get bytes back.
"""

import ctypes
import io
import random
from datetime import date
from decimal import Decimal
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from ledger_sense.corpus.errors import ErrorClass, inject
from ledger_sense.corpus.layouts import (
    BODY_FONT_PT,
    DOC_HEADER_FIELDS,
    DOC_HEADER_LABEL_X_PT,
    DOC_HEADER_ROW_HEIGHT_PT,
    DOC_HEADER_TOP_PT,
    DOC_HEADER_VALUE_X_PT,
    HEADER_FONT_PT,
    HEADER_Y_PT,
    PAGE_HEIGHT_PT,
    PAGE_WIDTH_PT,
    ROW_HEIGHT_PT,
    SUMMARY_FIELDS,
    SUMMARY_LABEL_X_PT,
    SUMMARY_ROW_HEIGHT_PT,
    SUMMARY_TOP_PT,
    SUMMARY_VALUE_X_PT,
    columns_for,
)
from ledger_sense.domain.models import (
    ContinuationLine,
    Contract,
    PayApplication,
    RetainageTerms,
)
from ledger_sense.domain.money import Money, RoundingPolicy, money
from ledger_sense.pdfium_lock import PDFIUM_LOCK

# "Structural Steel" first and deliberately multi-word: item 010 always gets
# it (index 0, see `_clean`), which is what the `wrapped_descriptions` layout
# needs a real word boundary to split — a single-word trade there wouldn't
# leave anything to wrap.
_TRADES = [
    "Structural Steel", "Sitework", "Concrete", "Masonry", "Rough Carpentry",
    "Roofing", "Glazing", "Drywall", "Painting", "Electrical", "Plumbing", "HVAC",
]

# A closed pool, same discipline as _TRADES: deterministic per seed via
# rng.choice, never a fabricated one-off that could collide with a
# hand-typed test fixture name.
_VENDOR_NAMES = [
    "Acme Steel Company",
    "Zenith Glazing",
    "Harborview Concrete",
    "Meridian Electrical",
    "Cascade Roofing",
    "Ironclad Mechanical",
]

_FONT = b"Helvetica"


def _new_text_object(doc: pdfium.PdfDocument, text: str, size: float) -> Any:
    text_obj = pdfium_c.FPDFPageObj_NewTextObj(doc.raw, _FONT, ctypes.c_float(size))
    buf = (text + chr(0)).encode("utf-16-le")
    # ctypes.cast's stub wants a ctypes instance, not raw bytes; this is the
    # verified-working call the pdfium raw API needs (a Python bytes object
    # backs a compatible buffer at runtime), so the mismatch is stub-only.
    pointer = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ushort))  # type: ignore[arg-type]
    pdfium_c.FPDFText_SetText(text_obj, pointer)
    return text_obj


def _insert_text(page: pdfium.PdfPage, doc: pdfium.PdfDocument, text: str,
                  x: float, y: float, size: float) -> None:
    text_obj = _new_text_object(doc, text, size)
    pdfium_c.FPDFPageObj_Transform(text_obj, 1, 0, 0, 1, x, y)
    pdfium_c.FPDFPage_InsertObject(page.raw, text_obj)


def _insert_text_right_aligned(page: pdfium.PdfPage, doc: pdfium.PdfDocument, text: str,
                                right_edge: float, y: float, size: float) -> None:
    """F-12. Column assignment downstream must use the span, not the left edge:
    place the text so its RIGHT edge lands on `right_edge`, not its left edge on
    the column's x. Measuring the object's natural (untransformed) bounds first
    is what makes the right edge land exactly on `right_edge` regardless of the
    string's width — a fixed per-character offset guess would not."""
    text_obj = _new_text_object(doc, text, size)
    left, bottom, right, top = ctypes.c_float(), ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
    pdfium_c.FPDFPageObj_GetBounds(text_obj, ctypes.byref(left), ctypes.byref(bottom),
                                   ctypes.byref(right), ctypes.byref(top))
    x_shift = right_edge - right.value
    pdfium_c.FPDFPageObj_Transform(text_obj, 1, 0, 0, 1, x_shift, y)
    pdfium_c.FPDFPage_InsertObject(page.raw, text_obj)


def _insert_wrapped_text(page: pdfium.PdfPage, doc: pdfium.PdfDocument, text: str,
                          x: float, y: float, size: float) -> None:
    """Two lines instead of one, split at a word boundary partway through. Row
    banding has to survive a wrapped cell, so this layout has to produce one."""
    words = text.split(" ")
    if len(words) < 2:
        _insert_text(page, doc, text, x, y, size)
        return
    mid = len(words) // 2 or 1
    _insert_text(page, doc, " ".join(words[:mid]), x, y, size)
    _insert_text(page, doc, " ".join(words[mid:]), x, y - size, size)


def _clean(seed: int) -> tuple[Contract, PayApplication]:
    rng = random.Random(seed)
    lines: list[ContinuationLine] = []
    for i in range(rng.randint(4, 10)):
        scheduled = money(str(rng.randrange(5_000, 250_000)) + ".00")
        previous = money(str(rng.randrange(0, int(scheduled) // 2)) + ".00")
        this_period = money(str(rng.randrange(0, int(scheduled - previous))) + ".00")
        total = money(previous + this_period)
        lines.append(ContinuationLine(
            item_no=f"{(i + 1) * 10:03d}", description=_TRADES[i % len(_TRADES)],
            scheduled_value=scheduled, previous=previous, this_period=this_period,
            stored=money("0.00"), total_completed_and_stored=total,
            retainage=money("0.00")))

    contract_sum = money(sum((ln.scheduled_value for ln in lines), money("0")))
    grand_total = money(sum((ln.total_completed_and_stored for ln in lines), money("0")))
    contract = Contract(id=f"C-{seed}", original_sum=contract_sum,
                        rounding=RoundingPolicy(2, 4),
                        retainage=RetainageTerms(base_rate=Decimal("0.10")))
    app = PayApplication(
        contract_id=contract.id, application_no=rng.randint(1, 12),
        period_to=date(2026, 8, 31), lines=lines, change_orders_net=money("0.00"),
        retainage_total=money("0.00"), previous_payments=money("0.00"),
        total_completed_and_stored=grand_total,
        vendor_name=rng.choice(_VENDOR_NAMES))
    return contract, app


def _summary_values(app: PayApplication, contract: Contract) -> dict[str, Money]:
    earned = money(app.total_completed_and_stored - app.retainage_total)
    return {
        "original_sum": contract.original_sum,
        "change_orders_net": app.change_orders_net,
        "adjusted_contract_sum": app.adjusted_contract_sum(contract),
        "total_completed_and_stored": app.total_completed_and_stored,
        "retainage_total": app.retainage_total,
        "total_earned_less_retainage": earned,
        "previous_payments": app.previous_payments,
        "current_payment_due": money(earned - app.previous_payments),
    }


def _render(app: PayApplication, contract: Contract, layout: str) -> bytes:
    """Render OUR layout with a real text layer. Extraction depends on the text
    layer existing — a scanned-only corpus would be useless.

    Only `alternate_headers` changes the column set (`columns_for` handles
    that); `right_aligned`, `wrapped_descriptions` and `two_page` are rendering
    decisions made here.

    Serialised on PDFIUM_LOCK (ledger_sense/pdfium_lock.py): both demo seed
    routes call this from FastAPI's thread pool.
    """
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument.new()
        try:
            return _render_into(pdf, app, contract, layout)
        finally:
            pdf.close()  # inside the lock, not left to a finaliser on another thread


def _render_into(pdf: pdfium.PdfDocument, app: PayApplication, contract: Contract,
                 layout: str) -> bytes:
    columns = columns_for(layout)

    if layout == "two_page":
        midpoint = max(1, len(app.lines) // 2)
        line_groups = [app.lines[:midpoint], app.lines[midpoint:]]
    else:
        line_groups = [app.lines]

    pages = [pdf.new_page(PAGE_WIDTH_PT, PAGE_HEIGHT_PT) for _ in line_groups]

    for page, lines in zip(pages, line_groups, strict=True):
        for column in columns:
            _insert_text(page, pdf, column.header, column.x_pt, HEADER_Y_PT, HEADER_FONT_PT)
        y = HEADER_Y_PT - ROW_HEIGHT_PT
        for line in lines:
            for column in columns:
                value = str(getattr(line, column.field))
                if layout == "right_aligned":
                    _insert_text_right_aligned(
                        page, pdf, value, column.x_pt + column.width_pt, y, BODY_FONT_PT)
                elif layout == "wrapped_descriptions" and column.field == "description":
                    _insert_wrapped_text(page, pdf, value, column.x_pt, y, BODY_FONT_PT)
                else:
                    _insert_text(page, pdf, value, column.x_pt, y, BODY_FONT_PT)
            y -= ROW_HEIGHT_PT

    # THE DOCUMENT HEADER BLOCK (Task 18). Contract/application/period
    # identifiers, drawn above the grid's own header row, page one only —
    # mirrors the SUMMARY_FIELDS pattern below.
    header_values: dict[str, str] = {
        "contract_id": contract.id,
        "application_no": str(app.application_no),
        "period_to": str(app.period_to),
        "vendor_name": app.vendor_name or "",
    }
    y = DOC_HEADER_TOP_PT
    for label, field in DOC_HEADER_FIELDS:
        _insert_text(pages[0], pdf, label, DOC_HEADER_LABEL_X_PT, y, BODY_FONT_PT)
        _insert_text(pages[0], pdf, header_values[field], DOC_HEADER_VALUE_X_PT, y, BODY_FONT_PT)
        y -= DOC_HEADER_ROW_HEIGHT_PT

    # THE SUMMARY BLOCK (F-10). Defined in layouts.py, drawn here, on page one
    # only — the block belongs to the document, not to a particular grid page.
    summary_values = _summary_values(app, contract)
    y = SUMMARY_TOP_PT
    for label, field in SUMMARY_FIELDS:
        _insert_text(pages[0], pdf, label, SUMMARY_LABEL_X_PT, y, BODY_FONT_PT)
        _insert_text(pages[0], pdf, str(summary_values[field]),
                     SUMMARY_VALUE_X_PT, y, BODY_FONT_PT)
        y -= SUMMARY_ROW_HEIGHT_PT

    for page in pages:
        page.gen_content()

    buf = io.BytesIO()
    pdf.save(buf)
    for page in pages:
        page.close()
    return buf.getvalue()


def generate(seed: int, error: ErrorClass,
             layout: str = "baseline") -> tuple[Contract, PayApplication, bytes]:
    contract, app = _clean(seed)
    contract, mutated = inject(contract, app, error)
    return contract, mutated, _render(mutated, contract, layout)


def ground_truth(seed: int, error: ErrorClass) -> dict[str, str]:
    """Pre-mutation values. Extraction is scored against these.

    `error` is part of the public interface (ground truth is always looked up
    per error class, symmetrically with `generate`) but unused here on
    purpose: ground truth is what the document SHOULD say, which is the clean
    figures regardless of which error class the rendered PDF carries.
    """
    del error
    _contract, app = _clean(seed)
    return {
        "total_completed_and_stored": str(app.total_completed_and_stored),
        "retainage_total": str(app.retainage_total),
        "previous_payments": str(app.previous_payments),
        **{f"line[{ln.item_no}].{field}": str(getattr(ln, field))
           for ln in app.lines
           for field in ("scheduled_value", "previous", "this_period", "stored",
                         "total_completed_and_stored")},
    }
