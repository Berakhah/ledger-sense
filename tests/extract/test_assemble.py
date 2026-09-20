import ast
import pathlib

import pytest

from ledger_sense.corpus.generate import generate
from ledger_sense.domain.models import PayApplication
from ledger_sense.extract import assemble as assemble_mod
from ledger_sense.extract.anchors import extract_summary
from ledger_sense.extract.assemble import UnextractableField, assemble_pay_application
from ledger_sense.extract.tables import Cell, find_headers, reconstruct_rows
from ledger_sense.extract.textlayer import BBox, extract_words
from ledger_sense.provenance import Claim, FileSource, Method


def claim(app: PayApplication, key: str) -> Claim[object]:
    """`PayApplication.provenance` is typed `dict[str, object]` on the domain
    model, so every site reading a value back out of it needs to assert what
    it actually is before touching `.method`/`.source`/etc -- mypy --strict's
    own `attr-defined` errors here (fix-wave finding 2) are the direct
    consequence of the tests previously ASSUMING the type without ever
    asserting it."""
    c = app.provenance[key]
    assert isinstance(c, Claim)
    return c


def file_source(c: Claim[object]) -> FileSource:
    """Every claim `assemble.py` raises is `_claim()`-built with a
    `FileSource` (never `HumanSource`) -- narrow it once here instead of
    re-asserting at every `.source.page`/`.source.bbox` call site."""
    assert isinstance(c.source, FileSource)
    return c.source


def unextractable_fields(app: PayApplication) -> list[UnextractableField]:
    fields = app.provenance["_unextractable"]
    assert isinstance(fields, list)
    return fields


def _known_retainage_bbox(pdf: bytes) -> BBox:
    """Ruling 7.3. Simulates a human reviewer reading the actual page: the
    FULL, undropped word list through the normal pipeline finds the real
    retainage_total bbox, even though `assemble_pay_application` was called
    on the same bytes with `_drop_labels={"Retainage"}` and could not."""
    words = extract_words(pdf)
    headers = find_headers(words)
    rows = reconstruct_rows(words, headers)
    grid_bottom = max((c.bbox.bottom for row in rows for c in row.values()),
                      default=None)
    summary, _ambiguous = extract_summary(words, grid_bottom=grid_bottom)
    return summary["retainage_total"].bbox


def test_assembled_application_matches_the_generated_one() -> None:
    _contract, app, pdf = generate(seed=1, error="clean")
    built = assemble_pay_application(pdf, "corpus://seed-1")
    assert built.total_completed_and_stored == app.total_completed_and_stored
    assert len(built.lines) == len(app.lines)


def test_every_populated_field_carries_a_deterministic_claim() -> None:
    _c, _a, pdf = generate(seed=1, error="clean")
    built = assemble_pay_application(pdf, "corpus://seed-1")
    fields = [k for k in built.provenance if not k.startswith("_")]
    assert fields, "no provenance recorded"
    for field in fields:
        c = claim(built, field)
        assert c.method is Method.DETERMINISTIC, field
        assert c.confidence is None, f"{field}: deterministic claims carry no confidence"


def test_every_claim_points_at_a_page_and_a_box() -> None:
    _c, _a, pdf = generate(seed=1, error="clean")
    built = assemble_pay_application(pdf, "corpus://seed-1")
    for field in built.provenance:
        if field.startswith("_"):
            continue
        source = file_source(claim(built, field))
        assert source.page >= 0, field
        x0, top, x1, bottom = source.bbox
        assert x1 > x0 and bottom > top, f"{field}: degenerate bbox"


def test_a_found_header_field_carries_a_claim_too() -> None:
    """Important, reviewer-confirmed: contract_id/application_no/period_to
    populated the domain model when found but carried no Claim at all —
    asymmetric with every grid cell and summary total, which both get one,
    and exactly the field family Ruling 4/F-15 exist to keep honest (a
    wrongly-parsed-but-still-ISO-shaped period_to would sail through with no
    bbox for a reviewer to check and no match_quality for Phase 6 to route
    on)."""
    _c, _a, pdf = generate(seed=1, error="clean")
    built = assemble_pay_application(pdf, "corpus://seed-1")
    for field in ("contract_id", "application_no", "period_to"):
        assert field in built.provenance, field
        c = claim(built, field)
        assert c.method is Method.DETERMINISTIC, field
        assert c.confidence is None, field
        source = file_source(c)
        assert source.page >= 0, field
        x0, top, x1, bottom = source.bbox
        assert x1 > x0 and bottom > top, f"{field}: degenerate bbox"
        assert c.match_quality is not None, field


def test_every_line_description_carries_a_claim_too() -> None:
    """Phase 6's writeback assembly (ledger_sense/writeback/assemble.py) needs
    a claim for every line's description, matching every other grid cell —
    the same asymmetry test_a_found_header_field_carries_a_claim_too already
    caught for the document-header fields. Without this, build_write_request
    can never resolve a bill line's description with provenance."""
    _c, app, pdf = generate(seed=1, error="clean")
    built = assemble_pay_application(pdf, "corpus://seed-1")
    assert built.lines, "fixture produced no lines"
    for line in built.lines:
        field = f"line[{line.item_no}].description"
        assert field in built.provenance, field
        c = claim(built, field)
        assert c.method is Method.DETERMINISTIC, field
        assert c.confidence is None, field
        assert c.value == line.description


def test_a_missing_field_is_recorded_not_guessed() -> None:
    _c, _a, pdf = generate(seed=1, error="clean")
    built = assemble_pay_application(pdf, "corpus://seed-1",
                                     _drop_labels=frozenset({"Retainage"}))
    assert "retainage_total" in {u.field for u in unextractable_fields(built)}


def test_the_period_is_never_defaulted_to_today() -> None:
    """F-15. `period_to = date.today()` was a guess wearing a domain type. C12
    compares every waiver's through-date against it, so a document processed a
    month late flags every waiver on it."""
    _c, _a, pdf = generate(seed=1, error="clean")
    built = assemble_pay_application(pdf, "corpus://seed-1",
                                     _drop_labels=frozenset({"Period"}))
    assert built.period_to is None
    assert "period_to" in {u.field for u in unextractable_fields(built)}


def test_every_claim_carries_a_match_quality_score() -> None:
    """F-46. Phase 6's Router and CalibrationCurve both require a confidence and
    NOTHING produced one — DoD 4 rested on a value the product never computed."""
    _c, _a, pdf = generate(seed=1, error="clean")
    built = assemble_pay_application(pdf, "corpus://seed-1")
    for field in built.provenance:
        if field.startswith("_"):
            continue
        c = claim(built, field)
        assert c.confidence is None, f"{field}: DETERMINISTIC carries no confidence"
        assert c.match_quality is not None, field
        assert 0.0 <= c.match_quality <= 1.0, field


def test_a_cold_anchor_scores_lower_than_a_profile_hit() -> None:
    """The score must actually vary with the evidence, or it is a constant with
    a nicer name."""
    from ledger_sense.extract.assemble import match_quality
    assert match_quality(label_exact=True, value_unique=True,
                         column_unambiguous=True, profile_hit=False) < \
           match_quality(label_exact=True, value_unique=True,
                         column_unambiguous=True, profile_hit=True)


def test_a_reviewer_correction_teaches_the_next_document() -> None:
    """F-17. Spec §4.3 calls this "the commercial one" — and ProfileCache was
    built, tested in isolation, and never called by anything."""
    from ledger_sense.extract.profiles import ProfileCache
    from ledger_sense.ingest.fingerprint import fingerprint

    _c, _a, pdf = generate(seed=1, error="clean")
    cache = ProfileCache(":memory:", tenant_id="t1")
    built = assemble_pay_application(pdf, "corpus://seed-1", cache=cache,
                                     _drop_labels=frozenset({"Retainage"}))
    assert "retainage_total" in {u.field for u in unextractable_fields(built)}

    corrected = _known_retainage_bbox(pdf)
    cache.learn(fingerprint(pdf), "retainage_total", corrected)

    _c2, _a2, pdf2 = generate(seed=2, error="clean")
    again = assemble_pay_application(pdf2, "corpus://seed-2", cache=cache,
                                     _drop_labels=frozenset({"Retainage"}))
    assert "retainage_total" not in {u.field for u in unextractable_fields(again)}


def test_a_profile_hit_does_not_score_the_label_as_found() -> None:
    """Fix-wave finding 5, VERIFIED live before the fix: the profile-cache-hit
    branch is reached ONLY when the label-anchored lookup did NOT find the
    label on this document, yet it hardcoded `label_exact=True` -- scoring
    the LEAST-verified path (geometry memory, no label confirmation) the
    HIGHEST of any field on a clean document. A profile hit must score
    exactly like a cold anchor that also lacks a confirmed label, not higher."""
    from ledger_sense.extract.assemble import match_quality
    from ledger_sense.extract.profiles import ProfileCache
    from ledger_sense.ingest.fingerprint import fingerprint

    _c, _a, pdf = generate(seed=1, error="clean")
    cache = ProfileCache(":memory:", tenant_id="t1")
    assemble_pay_application(pdf, "corpus://seed-1", cache=cache,
                             _drop_labels=frozenset({"Retainage"}))
    cache.learn(fingerprint(pdf), "retainage_total", _known_retainage_bbox(pdf))

    _c2, _a2, pdf2 = generate(seed=2, error="clean")
    again = assemble_pay_application(pdf2, "corpus://seed-2", cache=cache,
                                     _drop_labels=frozenset({"Retainage"}))
    profile_hit_claim = claim(again, "retainage_total")
    assert profile_hit_claim.match_quality == match_quality(
        label_exact=False, value_unique=True, column_unambiguous=True, profile_hit=True)


def test_a_profile_hit_landing_on_non_money_text_is_treated_as_a_miss() -> None:
    """Fix-wave finding 4, VERIFIED live before the fix: `_value_in_box` (the
    profile-cache branch) applies no `MONEY_TOKEN` filtering of its own,
    unlike the normal anchor lookup (`anchors._value_near`). A remembered box
    that, on THIS document, now overlaps non-money text (geometry drift, or a
    merged multi-word span) must degrade to "not found" -- same as a genuine
    cache miss -- rather than crashing `money()` on garbage text."""
    from ledger_sense.extract.profiles import ProfileCache
    from ledger_sense.ingest.fingerprint import fingerprint

    _c, _a, pdf = generate(seed=1, error="clean")
    words = extract_words(pdf)
    item_header = next(w for w in words if w.text == "Item")
    cache = ProfileCache(":memory:", tenant_id="t1")
    cache.learn(fingerprint(pdf), "change_orders_net", item_header.bbox)

    # "Orders" only ever appears inside "Summary — Net Change by Change
    # Orders" (SUMMARY_LABELS' one entry for change_orders_net) -- dropping it
    # forces the normal label-anchored lookup to miss, without touching the
    # grid's own required columns, so the profile-cache branch is reached.
    built = assemble_pay_application(  # must not raise decimal.InvalidOperation
        pdf, "corpus://seed-1", cache=cache, _drop_labels=frozenset({"Orders"}))

    assert "change_orders_net" in {u.field for u in unextractable_fields(built)}
    assert built.change_orders_net == 0


def test_a_total_grid_loss_is_recorded_as_unextractable(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix-wave finding 3, VERIFIED live before the fix: `reconstruct_rows`
    returning `[]` (headers resolved, every band still failing the
    REQUIRED_FIELDS completeness check) sailed through as an ordinary,
    complete result -- no "lines" entry in `_unextractable` at all."""
    _c, _a, pdf = generate(seed=1, error="clean")
    monkeypatch.setattr(assemble_mod, "reconstruct_rows", lambda words, headers: [])
    built = assemble_pay_application(pdf, "corpus://seed-1")
    assert built.lines == []
    assert "lines" in {u.field for u in unextractable_fields(built)}


def test_line_hash_of_no_lines_does_not_collide_with_a_real_document() -> None:
    """Fix-wave finding 3: `sha256(b"")` was the SAME hash for every zero-row
    document, which would make F-37's I1 duplicate rule flag every such
    document as a duplicate of every other one."""
    from ledger_sense.extract.assemble import line_hash
    _c, app, _pdf = generate(seed=1, error="clean")
    assert app.lines, "fixture must have at least one line for this to prove anything"
    assert line_hash([]) == ""
    assert line_hash([]) != line_hash(app.lines)


def test_a_cell_that_does_not_parse_as_money_is_recorded_not_crashed(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix-wave finding 4, VERIFIED live before the fix: a wrapped-description
    word spilling into a money column's span crashed `money()` with
    `decimal.InvalidOperation` instead of being recorded (B11: record, never
    guess -- an uncaught exception is neither). Mirrors the reviewer's own
    repro: inject a non-money cell directly into a real row's
    `scheduled_value`; the column-assignment geometry in tables.py that would
    normally produce such a cell is not what's under test here."""
    _c, _a, pdf = generate(seed=1, error="clean")
    words = extract_words(pdf)
    headers = find_headers(words)
    real_rows = reconstruct_rows(words, headers)
    item_no = real_rows[0]["item_no"].text
    bad_row = dict(real_rows[0])
    bad_row["scheduled_value"] = Cell(field="scheduled_value", text="Sitework",
                                      bbox=BBox(0.0, 0.0, 10.0, 10.0), page=0)
    monkeypatch.setattr(assemble_mod, "reconstruct_rows",
                        lambda words, headers: [bad_row, *real_rows[1:]])

    built = assemble_pay_application(pdf, "corpus://seed-1")  # must not raise

    assert len(built.lines) == len(real_rows) - 1
    assert f"line[{item_no}].scheduled_value" in {
        u.field for u in unextractable_fields(built)}


def test_the_line_hash_is_order_independent() -> None:
    """F-37. I1 compares on a line-item fingerprint that nothing produced."""
    from ledger_sense.extract.assemble import line_hash
    _c, app, _pdf = generate(seed=1, error="clean")
    assert line_hash(app.lines) == line_hash(list(reversed(app.lines)))


def test_no_module_outside_assemble_constructs_a_domain_model() -> None:
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
