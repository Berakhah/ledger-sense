"""Extraction -> domain, via Claims.

Every value reaching the domain model carries a Claim recording how it was
obtained and exactly where on the page it came from.

A field we cannot extract is RECORDED as unextractable, never guessed — B11.

This is the ONE module allowed to construct `PayApplication` / `ContinuationLine`
/ `Contract` (enforced by
`tests/extract/test_assemble.py::test_no_module_outside_assemble_constructs_a_domain_model`).
"""

import hashlib
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import NamedTuple

from ledger_sense.domain.models import ContinuationLine, PayApplication
from ledger_sense.domain.money import Money, money
from ledger_sense.extract.anchors import (
    BELOW_BAND_PT,
    BELOW_X_SLACK_PT,
    SAME_BAND_PT,
    Anchored,
    extract_summary,
    find_labels,
)
from ledger_sense.extract.profiles import ProfileCache
from ledger_sense.extract.tables import (
    AmbiguousColumnError,
    MissingColumnError,
    find_headers,
    reconstruct_rows,
)
from ledger_sense.extract.textlayer import BBox, Word, extract_words
from ledger_sense.ingest.fingerprint import fingerprint
from ledger_sense.provenance import Claim, FileSource, Method

_LINE_FIELDS = ("scheduled_value", "previous", "this_period", "stored",
                "total_completed_and_stored", "retainage")
_SUMMARY_MONEY = ("change_orders_net", "retainage_total", "previous_payments",
                  "total_completed_and_stored")

# DOCUMENT HEADER VOCABULARY (Ruling 4/5). extract/ must never import
# ledger_sense.corpus (a test enforces this with an AST check, F-14's same
# lesson applied a third time) — these labels are extraction's OWN copy,
# deliberately duplicating the plain-English wording the corpus renders.
_DOCUMENT_HEADER_LABELS: dict[str, str] = {
    "Contract Number": "contract_id",
    "Application Number": "application_no",
    "Period Ending": "period_to",
}
_INTEGER_TOKEN = re.compile(r"-?\d+")
_DATE_TOKEN = re.compile(r"\d{4}-\d{2}-\d{2}")


class UnextractableField(NamedTuple):
    field: str
    reason: str


def _clean(text: str) -> str:
    text = text.strip().replace(",", "").lstrip("$")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    return text


def _claim(source_path: str, page: int, bbox: BBox, value: object, *,
           match_quality: float | None = None) -> Claim[object]:
    return Claim(
        value=value,
        method=Method.DETERMINISTIC,
        confidence=None,
        observed_at=datetime.now(UTC),
        match_quality=match_quality,
        source=FileSource(path=source_path, page=page,
                          bbox=(bbox.x0, bbox.top, bbox.x1, bbox.bottom)))


def match_quality(*, label_exact: bool, value_unique: bool,
                  column_unambiguous: bool, profile_hit: bool) -> float:
    """0.0-1.0. Four bits of evidence, equally weighted, documented in
    docs/methodology.md. NOT a probability — Phase 6 calibrates it or refuses
    to route on it."""
    return sum((label_exact, value_unique, column_unambiguous, profile_hit)) / 4


def _value_after_label(words: list[Word], label_words: list[Word],
                        predicate: Callable[[Word], bool]) -> Word | None:
    """Same anchor+geometry style as `anchors._value_near` — nearest value-
    shaped token to the label's right on the same band, else directly below —
    generalised over a value-shape PREDICATE instead of a fixed money regex
    (Ruling 5: `application_no` is an integer, `period_to` a date, `contract_id`
    a bare id string; none of those match `anchors.MONEY_TOKEN`)."""
    label_left = min(w.bbox.x0 for w in label_words)
    label_right = max(w.bbox.x1 for w in label_words)
    label_top = min(w.bbox.top for w in label_words)
    page = label_words[0].page

    same_band = [w for w in words
                 if w.page == page and w.bbox.x0 >= label_right
                 and abs(w.bbox.top - label_top) <= SAME_BAND_PT
                 and predicate(w)]
    if same_band:
        return min(same_band, key=lambda w: w.bbox.x0)

    below = [w for w in words
             if w.page == page and 0 < w.bbox.top - label_top <= BELOW_BAND_PT
             and label_left - BELOW_X_SLACK_PT <= (w.bbox.x0 + w.bbox.x1) / 2
             <= label_right + BELOW_X_SLACK_PT
             and predicate(w)]
    if below:
        return min(below, key=lambda w: (w.bbox.top, w.bbox.x0))
    return None


def extract_document_header(words: list[Word]) -> dict[str, object]:
    """Contract id / application number / period-ending, by label anchor plus
    geometry — same principle as `anchors.extract_summary`, written from
    scratch (Ruling 5) because the VALUE shapes here are not money.

    A label that is absent, or appears more than once, or whose nearest value
    does not parse into the expected type, means the field is OMITTED — never
    guessed (B11), same discipline as `extract_summary`.
    """
    predicates: dict[str, Callable[[Word], bool]] = {
        "contract_id": lambda w: bool(w.text.strip()),
        "application_no": lambda w: bool(_INTEGER_TOKEN.fullmatch(w.text)),
        "period_to": lambda w: bool(_DATE_TOKEN.fullmatch(w.text)),
    }
    parsers: dict[str, Callable[[str], object]] = {
        "contract_id": str,
        "application_no": int,
        "period_to": lambda text: date.fromisoformat(text),
    }

    header: dict[str, object] = {}
    for label, field in _DOCUMENT_HEADER_LABELS.items():
        matches = find_labels(words, label)
        if len(matches) != 1:
            continue  # absent, or ambiguous — refuse rather than guess which one
        value_word = _value_after_label(words, matches[0], predicates[field])
        if value_word is None:
            continue
        try:
            header[field] = parsers[field](value_word.text)
        except ValueError:
            continue
    return header


def _value_in_box(words: list[Word], box: BBox) -> Word | None:
    """Ruling 7.2: find whatever word(s) on THIS document occupy a box
    remembered from a reviewer's correction on a DIFFERENT document sharing
    the same layout fingerprint. Geometry only — centre-inside-box, the same
    style of proximity test as `anchors._value_near` and `tables._column_for`.
    Multiple words whose centres fall in the box are merged into one, same
    convention as `tables._merge`."""
    hits = sorted(
        (w for w in words
         if box.x0 <= (w.bbox.x0 + w.bbox.x1) / 2 <= box.x1
         and box.top <= (w.bbox.top + w.bbox.bottom) / 2 <= box.bottom),
        key=lambda w: w.bbox.x0)
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    merged_bbox = BBox(hits[0].bbox.x0, min(w.bbox.top for w in hits),
                       hits[-1].bbox.x1, max(w.bbox.bottom for w in hits))
    return Word(text=" ".join(w.text for w in hits), bbox=merged_bbox,
                page=hits[0].page)


def assemble_pay_application(
    pdf: bytes, source_path: str, _drop_labels: frozenset[str] = frozenset(),
    cache: ProfileCache | None = None,
) -> PayApplication:
    words: list[Word] = [w for w in extract_words(pdf) if w.text not in _drop_labels]
    provenance: dict[str, object] = {}
    unextractable: list[UnextractableField] = []
    # Computed once, lazily, only for callers that opted into the profile loop
    # (F-17) — `fingerprint()` re-parses the PDF independently of
    # `extract_words` above, so a caller with no cache never pays for it.
    key = fingerprint(pdf) if cache is not None else None

    # Ruling 6, VERIFIED: a dropped label that is ALSO a required grid column
    # header ("Retainage"; "Period" inside "This Period") makes the grid
    # unreconstructable — `find_headers`/`reconstruct_rows` raise. Summary and
    # document-header extraction still work off the SAME word list,
    # independently of the grid, and B11 says a lost field is RECORDED, never
    # allowed to take the rest of assembly down with it.
    lines: list[ContinuationLine] = []
    try:
        headers = find_headers(words)
        rows = reconstruct_rows(words, headers)
    except (MissingColumnError, AmbiguousColumnError) as exc:
        unextractable.append(UnextractableField(
            "lines", f"continuation grid could not be reconstructed: {exc}"))
        rows = []

    grid_bottom = max((c.bbox.bottom for row in rows for c in row.values()),
                      default=None)
    for row in rows:
        item_no = row["item_no"].text
        values: dict[str, Money] = {}
        for field in _LINE_FIELDS:
            cell = row[field]
            values[field] = money(_clean(cell.text))
            provenance[f"line[{item_no}].{field}"] = _claim(
                source_path, cell.page, cell.bbox, values[field],
                match_quality=match_quality(label_exact=True, value_unique=True,
                                            column_unambiguous=True, profile_hit=False))
        lines.append(ContinuationLine(
            item_no=item_no,
            description=row["description"].text if "description" in row else "",
            **values))

    summary, ambiguous = extract_summary(words, grid_bottom=grid_bottom)
    for field in ambiguous:
        unextractable.append(UnextractableField(
            field, "label appears more than once on the page; refusing to guess"))

    totals: dict[str, Money] = {}
    for field in _SUMMARY_MONEY:
        anchored: Anchored | None = summary.get(field)
        profile_hit = False

        # F-17: consult the learned profile before giving up. A remembered
        # box only ever narrows WHERE to look on THIS document — the value
        # still comes from THIS document's own words, never the cache.
        if anchored is None and cache is not None and key is not None:
            cached_box = cache.lookup(key, field)
            if cached_box is not None:
                hit = _value_in_box(words, cached_box)
                if hit is not None:
                    anchored = Anchored(field=field, text=hit.text,
                                        bbox=hit.bbox, page=hit.page)
                    profile_hit = True

        if anchored is None:
            unextractable.append(UnextractableField(field, "label not found on page"))
            totals[field] = money("0.00")
            continue
        totals[field] = money(_clean(anchored.text))
        provenance[field] = _claim(
            source_path, anchored.page, anchored.bbox, totals[field],
            match_quality=match_quality(label_exact=True, value_unique=True,
                                        column_unambiguous=field not in ambiguous,
                                        profile_hit=profile_hit))

    # NOTHING IS INVENTED — REVISED (F-15). The original wrote contract_id="",
    # application_no=0 and period_to=date.today(). Task 17's own docstring says
    # "a guessed contract sum is a wrong bill with a confident number on it";
    # a guessed period_to is worse, because C12 compares every waiver's
    # through-date against it, so a document processed a month late flags them all.
    header = extract_document_header(words)
    contract_id = header.get("contract_id")
    application_no = header.get("application_no")
    period_to = header.get("period_to")
    for field_name, field_value in (("contract_id", contract_id),
                                    ("application_no", application_no),
                                    ("period_to", period_to)):
        if field_value is None:
            unextractable.append(UnextractableField(field_name, "not found on page"))
    assert contract_id is None or isinstance(contract_id, str)
    assert application_no is None or isinstance(application_no, int)
    assert period_to is None or isinstance(period_to, date)

    provenance["_unextractable"] = unextractable
    # F-37: I1 matches on a line-item fingerprint that nothing produced.
    provenance["_line_hash"] = line_hash(lines)
    return PayApplication(
        contract_id=contract_id,
        application_no=application_no,
        period_to=period_to,
        lines=lines, provenance=provenance, **totals)


def line_hash(lines: list[ContinuationLine]) -> str:
    """The line-item fingerprint I1 compares on (F-37). Order-independent, so a
    resubmission with the rows shuffled still matches."""
    payload = "|".join(sorted(
        f"{ln.item_no}\x1f{ln.description}\x1f{ln.total_completed_and_stored}"
        for ln in lines))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
