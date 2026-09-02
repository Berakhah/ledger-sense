from ledger_sense.corpus.generate import generate
from ledger_sense.extract.document import BBox, ParsedPDF, Word
from ledger_sense.ingest.fingerprint import document_hash, fingerprint


def _pdf(seed: int) -> bytes:
    _contract, _app, pdf = generate(seed=seed, error="clean")
    return pdf


def test_same_layout_different_data_yields_the_same_key():
    """The fingerprint keys on GEOMETRY, not content. Two documents from the
    same producing software must share a profile."""
    assert fingerprint(_pdf(1)) == fingerprint(_pdf(2))


def test_a_shifted_column_layout_yields_a_different_key():
    """The corpus's five adversarial layouts (Task 16) vary header wording,
    value alignment and pagination, but none of them shift a column's x0 —
    real geometry variation across producing software, which is what the
    fingerprint exists to key on. `_column_hash` is exercised directly
    against two hand-built word sets that differ only in x0."""
    from ledger_sense.ingest.fingerprint import _column_hash

    def _doc(x0s: list[float]) -> ParsedPDF:
        words = tuple(
            Word(text=str(i), bbox=BBox(x0, 100.0, x0 + 20.0, 110.0), page=0)
            for i, x0 in enumerate(x0s))
        return ParsedPDF(words=words, metadata={}, page_count=1, has_text_layer=True)

    baseline = _column_hash(_doc([36.0, 80.0, 288.0, 372.0]))
    shifted = _column_hash(_doc([61.0, 105.0, 313.0, 397.0]))
    assert baseline != shifted


def test_document_hash_is_stable_and_content_addressed():
    """F-16. `build_idempotency_key(document_hash, ...)` in Phase 5 had NO
    producer anywhere in the six plan documents."""
    pdf = _pdf(1)
    assert document_hash(pdf) == document_hash(pdf)
    assert document_hash(pdf) != document_hash(_pdf(2))
    assert len(document_hash(pdf)) == 32


def test_key_is_stable_across_calls():
    assert fingerprint(_pdf(3)).as_str() == fingerprint(_pdf(3)).as_str()


def test_key_string_is_filesystem_and_sql_safe():
    assert all(ch.isalnum() or ch in ":-_." for ch in fingerprint(_pdf(1)).as_str())
