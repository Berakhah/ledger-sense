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
    # Ruling R9: the brief's original body imports
    # `ledger_sense.corpus.generate.generate`, which belongs to Phase 1 Task
    # 12 — several tasks deep in money-arithmetic domain models
    # (PayApplication/Contract) that don't exist in this repo yet and are not
    # this task's to build or stub. Substituted with a minimal well-formed
    # PDF built directly with this task's own PDF library (pypdfium2), which
    # keeps the test's intent intact: a legitimately valid document must
    # parse, unlike every fixture in the malformed set above.
    import io

    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument.new()
    doc.new_page(200, 200)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    assert not isinstance(parse_within_limits(buf.getvalue(), Limits()), RejectedDocument)


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
