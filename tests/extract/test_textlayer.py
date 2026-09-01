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
    import io
    blank = pdfium.PdfDocument.new()
    blank.new_page(612, 792)
    # pypdfium2.PdfDocument.save_to_bytes() does not exist; use save(buffer) + buffer.getvalue() instead
    buffer = io.BytesIO()
    blank.save(buffer)
    assert has_text_layer(buffer.getvalue()) is False
