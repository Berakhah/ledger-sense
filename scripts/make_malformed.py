"""Generates the malformed-PDF fixtures used by tests/ingest/test_limits.py.

`00` §8 / the AIA guard applies here too: no third-party document ever enters
this repo. Every fixture is built from raw bytes by this script,
deterministically — nothing downloaded, nothing hand-crafted-then-forgotten.
Re-run `uv run python scripts/make_malformed.py` any time the fixture set
needs to change; it always rewrites all twenty files from scratch.

Each generator starts from `_wellformed`, a byte-accurate (real xref offsets,
real trailer) single-revision PDF builder, and then breaks exactly one thing
about it — a different code path per fixture (page count, decompression,
wall-clock, structural corruption, filters, encryption) rather than twenty
variations on one trick.
"""

from __future__ import annotations

import pathlib
import zlib
from collections.abc import Callable

FIXTURES = pathlib.Path("tests/ingest/fixtures/malformed")

_HEADER = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"


def _obj(num: int, body: bytes) -> bytes:
    return f"{num} 0 obj\n".encode() + body + b"\nendobj\n"


def _wellformed(
    objects: list[tuple[int, bytes]], *, root: int = 1, extra_trailer: str = ""
) -> bytes:
    """A structurally correct single-revision PDF: real objects, a real xref
    table with byte-accurate offsets, a trailer, %%EOF."""
    out = bytearray(_HEADER)
    offsets: dict[int, int] = {}
    for num, body in objects:
        offsets[num] = len(out)
        out += _obj(num, body)
    xref_offset = len(out)
    size = max(n for n, _ in objects) + 1
    out += f"xref\n0 {size}\n".encode()
    out += b"0000000000 65535 f \n"
    for num in range(1, size):
        off = offsets.get(num)
        out += (f"{off:010d} 00000 n \n" if off is not None else "0000000000 00000 f \n").encode()
    out += f"trailer\n<< /Size {size} /Root {root} 0 R{extra_trailer} >>\n".encode()
    out += f"startxref\n{xref_offset}\n".encode()
    out += b"%%EOF"
    return bytes(out)


def _one_page_objects() -> list[tuple[int, bytes]]:
    """Object numbers 1-4: catalog, pages, page, content stream. Every
    fixture below that only wants to break one object starts here and
    replaces the object it cares about."""
    catalog = b"<< /Type /Catalog /Pages 2 0 R >>"
    pages = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
    page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> /Contents 4 0 R >>"
    content = b"<< /Length 0 >>\nstream\n\nendstream"
    return [(1, catalog), (2, pages), (3, page), (4, content)]


# --- page count -------------------------------------------------------------


def many_pages() -> bytes:
    """/Count 100000, Kids all pointing at the same Page object — a valid
    page tree that never needed 100000 real objects to lie about its count."""
    catalog = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join("3 0 R" for _ in range(100_000))
    pages = f"<< /Type /Pages /Kids [{kids}] /Count 100000 >>".encode()
    page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> >>"
    return _wellformed([(1, catalog), (2, pages), (3, page)])


def cyclic_page_tree() -> bytes:
    """Two Pages nodes that are each other's /Parent and only /Kid — the
    "cyclic object graph" from the brief, specifically in the page tree."""
    catalog = b"<< /Type /Catalog /Pages 2 0 R >>"
    pages_a = b"<< /Type /Pages /Parent 3 0 R /Kids [3 0 R] /Count 1 >>"
    pages_b = b"<< /Type /Pages /Parent 2 0 R /Kids [2 0 R] /Count 1 >>"
    return _wellformed([(1, catalog), (2, pages_a), (3, pages_b)])


def deeply_nested_page_tree() -> bytes:
    """5000 single-child Pages nodes stacked before the one real Page —
    stresses recursion depth in whatever walks the tree, independent of the
    /Count shortcut `many_pages` exercises."""
    depth = 5000
    page_num = depth + 2
    objects: list[tuple[int, bytes]] = [(1, b"<< /Type /Catalog /Pages 2 0 R >>")]
    for i in range(depth):
        num = 2 + i
        objects.append((num, f"<< /Type /Pages /Kids [{num + 1} 0 R] /Count 1 >>".encode()))
    objects.append((page_num, b"<< /Type /Page /MediaBox [0 0 200 200] /Resources << >> >>"))
    return _wellformed(objects)


# --- memory / decompression --------------------------------------------------


def zip_bomb() -> bytes:
    """A real decompression bomb: 300MB of zeros, which zlib compresses to a
    few hundred bytes on disk. Decompressing it naively would build a
    300MB buffer in memory."""
    huge = zlib.compress(b"\x00" * 300_000_000, level=9)
    stream = f"<< /Length {len(huge)} /Filter /FlateDecode >>\nstream\n".encode() + huge + b"\nendstream"
    objects = _one_page_objects()
    objects[3] = (4, stream)
    return _wellformed(objects)


def nested_filter_chain() -> bytes:
    """/Filter declares a 50-entry alternating chain. The brief's "nested
    /Filter chain" example — stresses filter-chain handling, not the payload
    itself (the bytes are a small, ordinary flate stream)."""
    chain = " ".join(["/ASCII85Decode", "/FlateDecode"] * 25)
    raw = zlib.compress(b"hello world" * 10)
    stream = f"<< /Length {len(raw)} /Filter [{chain}] >>\nstream\n".encode() + raw + b"\nendstream"
    objects = _one_page_objects()
    objects[3] = (4, stream)
    return _wellformed(objects)


def corrupt_flate_stream() -> bytes:
    """/Filter /FlateDecode over bytes that are not valid zlib data at all —
    the decoder must error cleanly, not hang trying to make sense of it."""
    raw = b"\xff" * 5000
    stream = f"<< /Length {len(raw)} /Filter /FlateDecode >>\nstream\n".encode() + raw + b"\nendstream"
    objects = _one_page_objects()
    objects[3] = (4, stream)
    return _wellformed(objects)


def malformed_ascii85() -> bytes:
    """/ASCII85Decode over bytes outside the ASCII85 alphabet (raw NUL..0x05)
    — a different filter's error path than the flate one above."""
    raw = b"<~" + bytes([0, 1, 2, 3, 4, 5]) + b"~>"
    stream = f"<< /Length {len(raw)} /Filter /ASCII85Decode >>\nstream\n".encode() + raw + b"\nendstream"
    objects = _one_page_objects()
    objects[3] = (4, stream)
    return _wellformed(objects)


def huge_content_stream() -> bytes:
    """~5MB of real, uncompressed, syntactically valid content-stream
    operators — large but under the byte cap, so this exercises the content
    tokenizer's cost rather than the size check."""
    raw = b"q Q\n" * 1_250_000
    stream = f"<< /Length {len(raw)} >>\nstream\n".encode() + raw + b"\nendstream"
    objects = _one_page_objects()
    objects[3] = (4, stream)
    return _wellformed(objects)


# --- structural corruption ---------------------------------------------------


def truncated_trailer() -> bytes:
    """Cut off mid-trailer-dictionary, before /Root or the closing >>."""
    full = _wellformed(_one_page_objects())
    cut = full.index(b"trailer")
    return full[: cut + len(b"trailer\n<< /Size 5")]


def invalid_xref_offsets() -> bytes:
    """The xref table is present and well-shaped, but every offset in it
    points at the wrong byte — distinct from `missing_xref` (no table) and
    `truncated_trailer` (no trailer)."""
    full = bytearray(_wellformed(_one_page_objects()))
    free_line = b"0000000000 65535 f \n"
    start = full.index(free_line) + len(free_line)
    garbage_line = b"9999999999 00000 n \n"
    for i in range(4):
        full[start + i * len(garbage_line): start + (i + 1) * len(garbage_line)] = garbage_line
    return bytes(full)


def missing_xref() -> bytes:
    """No xref table at all: trailer and startxref exist, but startxref
    points at a trailer that has nothing preceding it to cross-reference."""
    full = _wellformed(_one_page_objects())
    start = full.index(b"xref\n")
    end = full.index(b"trailer\n")
    return full[:start] + full[end:]


def corrupted_header() -> bytes:
    """Correct %PDF magic, then nothing but binary garbage — no objects, no
    xref, no trailer. Distinct from the magic-bytes test: the header is
    right, everything after it is wrong."""
    return b"%PDF-1.4\n" + bytes(range(256)) * 4 + b"\n%%EOF"


def truncated_mid_object() -> bytes:
    """Cut off partway through an object body — a dangling `obj` with no
    `endobj`, no xref, no trailer at all."""
    full = _wellformed(_one_page_objects())
    cut = full.index(b"/Type /Page")
    return full[: cut + 5]


def missing_eof_marker() -> bytes:
    """Otherwise complete and correct, just missing the trailing %%EOF."""
    full = _wellformed(_one_page_objects())
    assert full.endswith(b"%%EOF")
    return full[: -len(b"%%EOF")]


def invalid_root_reference() -> bytes:
    """/Root points at object 99, which does not exist. The xref table
    itself is fully correct for the objects that do exist."""
    return _wellformed(_one_page_objects(), root=99)


def circular_object_reference() -> bytes:
    """Two ordinary (non-page-tree) objects that reference each other —
    the brief's "cyclic object graph" outside the page tree specifically,
    complementing `cyclic_page_tree` above."""
    objects = _one_page_objects()
    objects[0] = (1, b"<< /Type /Catalog /Pages 2 0 R /Extra 5 0 R >>")
    objects.append((5, b"<< /Other 6 0 R >>"))
    objects.append((6, b"<< /Other 5 0 R >>"))
    return _wellformed(objects)


def deeply_nested_dictionary() -> bytes:
    """A 3000-deep nested array as a catalog value — stresses recursive
    descent / stack depth in the object parser, unrelated to the page tree."""
    depth = 3000
    nested = "[" * depth + "]" * depth
    objects = _one_page_objects()
    objects[0] = (1, f"<< /Type /Catalog /Pages 2 0 R /Extra {nested} >>".encode())
    return _wellformed(objects)


# --- declared-length mismatch -------------------------------------------------


def oversized_declared_length() -> bytes:
    """/Length claims 50MB; the actual stream body is 4 bytes. Tests that a
    declared length is not trusted enough to pre-allocate against."""
    stream = b"<< /Length 50000000 >>\nstream\ntiny\nendstream"
    objects = _one_page_objects()
    objects[3] = (4, stream)
    return _wellformed(objects)


def declared_2gb_stream_length() -> bytes:
    """The brief's own example: /Length declares ~2GB while the actual bytes
    on disk are a handful — so a 2GB upload never has to actually exist for
    this to be tested."""
    stream = b"<< /Length 2000000000 /Filter /FlateDecode >>\nstream\nshort\nendstream"
    objects = _one_page_objects()
    objects[3] = (4, stream)
    return _wellformed(objects)


# --- encryption ----------------------------------------------------------------


def encrypted_with_unknown_filter() -> bytes:
    """/Encrypt references a security handler this codebase has never heard
    of. Must be refused cleanly, not guessed at."""
    objects = _one_page_objects()
    objects.append((5, b"<< /Filter /WeirdCrypt /V 5 /R 6 >>"))
    return _wellformed(objects, extra_trailer=" /Encrypt 5 0 R")


GENERATORS: dict[str, Callable[[], bytes]] = {
    "many_pages.pdf": many_pages,
    "cyclic_page_tree.pdf": cyclic_page_tree,
    "deeply_nested_page_tree.pdf": deeply_nested_page_tree,
    "zip_bomb.pdf": zip_bomb,
    "nested_filter_chain.pdf": nested_filter_chain,
    "corrupt_flate_stream.pdf": corrupt_flate_stream,
    "malformed_ascii85.pdf": malformed_ascii85,
    "huge_content_stream.pdf": huge_content_stream,
    "truncated_trailer.pdf": truncated_trailer,
    "invalid_xref_offsets.pdf": invalid_xref_offsets,
    "missing_xref.pdf": missing_xref,
    "corrupted_header.pdf": corrupted_header,
    "truncated_mid_object.pdf": truncated_mid_object,
    "missing_eof_marker.pdf": missing_eof_marker,
    "invalid_root_reference.pdf": invalid_root_reference,
    "circular_object_reference.pdf": circular_object_reference,
    "deeply_nested_dictionary.pdf": deeply_nested_dictionary,
    "oversized_declared_length.pdf": oversized_declared_length,
    "declared_2gb_stream_length.pdf": declared_2gb_stream_length,
    "encrypted_with_unknown_filter.pdf": encrypted_with_unknown_filter,
}


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, generator in GENERATORS.items():
        (FIXTURES / name).write_bytes(generator())
    print(f"wrote {len(GENERATORS)} fixtures to {FIXTURES}")


if __name__ == "__main__":
    main()
