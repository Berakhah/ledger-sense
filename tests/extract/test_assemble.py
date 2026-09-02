import ast
import pathlib

from ledger_sense.corpus.generate import generate
from ledger_sense.extract.anchors import extract_summary
from ledger_sense.extract.assemble import assemble_pay_application
from ledger_sense.extract.tables import find_headers, reconstruct_rows
from ledger_sense.extract.textlayer import BBox, extract_words
from ledger_sense.provenance import Method


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


def test_the_period_is_never_defaulted_to_today():
    """F-15. `period_to = date.today()` was a guess wearing a domain type. C12
    compares every waiver's through-date against it, so a document processed a
    month late flags every waiver on it."""
    _c, _a, pdf = generate(seed=1, error="clean")
    built = assemble_pay_application(pdf, "corpus://seed-1",
                                     _drop_labels=frozenset({"Period"}))
    assert built.period_to is None
    assert "period_to" in {u.field for u in built.provenance["_unextractable"]}


def test_every_claim_carries_a_match_quality_score():
    """F-46. Phase 6's Router and CalibrationCurve both require a confidence and
    NOTHING produced one — DoD 4 rested on a value the product never computed."""
    _c, _a, pdf = generate(seed=1, error="clean")
    built = assemble_pay_application(pdf, "corpus://seed-1")
    for field, claim in built.provenance.items():
        if field.startswith("_"):
            continue
        assert claim.confidence is None, f"{field}: DETERMINISTIC carries no confidence"
        assert claim.match_quality is not None, field
        assert 0.0 <= claim.match_quality <= 1.0, field


def test_a_cold_anchor_scores_lower_than_a_profile_hit():
    """The score must actually vary with the evidence, or it is a constant with
    a nicer name."""
    from ledger_sense.extract.assemble import match_quality
    assert match_quality(label_exact=True, value_unique=True,
                         column_unambiguous=True, profile_hit=False) < \
           match_quality(label_exact=True, value_unique=True,
                         column_unambiguous=True, profile_hit=True)


def test_a_reviewer_correction_teaches_the_next_document():
    """F-17. Spec §4.3 calls this "the commercial one" — and ProfileCache was
    built, tested in isolation, and never called by anything."""
    from ledger_sense.extract.profiles import ProfileCache
    from ledger_sense.ingest.fingerprint import fingerprint

    _c, _a, pdf = generate(seed=1, error="clean")
    cache = ProfileCache(":memory:", tenant_id="t1")
    built = assemble_pay_application(pdf, "corpus://seed-1", cache=cache,
                                     _drop_labels=frozenset({"Retainage"}))
    assert "retainage_total" in {u.field for u in built.provenance["_unextractable"]}

    corrected = _known_retainage_bbox(pdf)
    cache.learn(fingerprint(pdf), "retainage_total", corrected)

    _c2, _a2, pdf2 = generate(seed=2, error="clean")
    again = assemble_pay_application(pdf2, "corpus://seed-2", cache=cache,
                                     _drop_labels=frozenset({"Retainage"}))
    assert "retainage_total" not in {u.field for u in again.provenance["_unextractable"]}


def test_the_line_hash_is_order_independent():
    """F-37. I1 compares on a line-item fingerprint that nothing produced."""
    from ledger_sense.extract.assemble import line_hash
    _c, app, _pdf = generate(seed=1, error="clean")
    assert line_hash(app.lines) == line_hash(list(reversed(app.lines)))


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
