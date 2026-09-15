"""Resource-capped PDF parsing (F-26, G-6).

`00` §8: anything that executes untrusted input gets resource limits. Document
extraction is the one place this product touches hostile bytes — a PDF upload
can carry a decompression bomb, a page-count bomb, or a byte stream large
enough to stall the parser on its own. Parsing therefore never runs in this
process: it runs in a killable child, capped on bytes, page count, wall-clock
time and (platform-dependent — see below) address space.

Platform note (Ruling R10): `resource.RLIMIT_AS` is POSIX-only. On Windows
there is no drop-in equivalent, so the memory cap is enforced two ways
instead of one:

  1. (both platforms) `_decompression_bomb` decompresses every raw stream in
     the payload incrementally, capped at `max_memory_mb`, and bails the
     moment that cap would be exceeded — the oversized buffer is never
     actually materialised. This alone is enough to satisfy
     `test_a_decompression_bomb_does_not_exhaust_memory` on any platform,
     because it never allocates past the cap in the first place.
  2. (POSIX only) `resource.setrlimit(RLIMIT_AS, ...)` in the child, as a
     blanket backstop against every *other* memory-exhaustion vector (a
     pathological object graph, an allocation inside pdfium itself, ...)
     that check 1 does not anticipate.

On Windows only (1) applies; (2) is skipped outright (guarded by
`sys.platform`, not attempted-then-caught) so the module still imports and
the byte/page/timeout caps still work. The wall-clock timeout below bounds a
hung or spinning child on either platform, which also catches most bombs
that (1) doesn't recognise (they tend to burn CPU as well as memory). The
residual gap — a non-decompression memory-exhaustion vector that also
finishes within the timeout — is real and disclosed, not hidden.
ponytail: a Windows Job Object memory limit (pywin32,
JOB_OBJECT_LIMIT_PROCESS_MEMORY) would close that gap without a new heavy
dependency; not adding it until the corpus actually needs it.
"""

from __future__ import annotations

import multiprocessing
import re
import sys
import zlib
from dataclasses import dataclass
from enum import Enum
from queue import Empty

import pypdfium2 as pdfium

_STREAM_RE = re.compile(rb"stream\r?\n(.*?)endstream", re.DOTALL)


@dataclass(frozen=True)
class Limits:
    """Caps applied to one parse attempt. All four are independent triggers."""

    max_bytes: int = 25_000_000
    max_pages: int = 200
    timeout_seconds: float = 60
    max_memory_mb: int = 512


class Rejection(str, Enum):
    """Why a payload was refused. The route maps HTTP status on this, never
    on the wording of `reason`."""

    NOT_PDF = "NOT_PDF"
    TOO_LARGE = "TOO_LARGE"
    TOO_MANY_PAGES = "TOO_MANY_PAGES"
    BOMB = "BOMB"
    TIMEOUT = "TIMEOUT"
    UNPARSEABLE = "UNPARSEABLE"


@dataclass(frozen=True)
class RejectedDocument:
    """A document that did not parse — malformed, or over a cap. Never raised,
    always returned: B11 (route to review with an honest "cannot extract"
    rather than guess) applies to resource limits too, not just field-level
    confidence.
    """

    reason: str
    kind: Rejection


@dataclass(frozen=True)
class ParsedDocument:
    """The output of a successful parse. Deliberately minimal — the field
    extraction pipeline that consumes this is a separate, later task."""

    page_count: int
    text: str


ParseResult = ParsedDocument | RejectedDocument


def _decompression_bomb(payload: bytes, max_bytes: int) -> bool:
    """True if any raw stream in `payload` would decompress past `max_bytes`.

    Scans for `stream ... endstream` spans by regex — independent of whether
    the surrounding PDF structure is even valid — and feeds each through
    `zlib.decompressobj` with a hard output cap, so the oversized buffer a
    real bomb produces is never built. Streams that are not valid zlib data
    (a different filter, or genuinely corrupt) are not this check's concern
    and are skipped; the parser downstream will accept or reject them on
    their own terms.
    """
    for match in _STREAM_RE.finditer(payload):
        chunk = match.group(1)
        decompressor = zlib.decompressobj()
        total = 0
        try:
            while chunk:
                out = decompressor.decompress(chunk, max_bytes + 1 - total)
                total += len(out)
                if total > max_bytes:
                    return True
                if not decompressor.unconsumed_tail:
                    break
                chunk = decompressor.unconsumed_tail
        except zlib.error:
            continue
    return False


def _parse_child(payload: bytes, limits: Limits, out: multiprocessing.Queue[ParseResult]) -> None:
    """Runs inside the child process. Never lets an exception escape — always
    puts exactly one result on the queue, per
    `test_no_malformed_fixture_hangs_or_raises_an_unhandled_exception`.
    """
    if sys.platform != "win32":
        import resource

        cap = limits.max_memory_mb * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
        except (ValueError, OSError):
            pass  # best-effort backstop; the timeout still bounds a runaway child

    try:
        if _decompression_bomb(payload, limits.max_memory_mb * 1024 * 1024):
            out.put(RejectedDocument("rejected: a stream would decompress past the memory cap", Rejection.BOMB))
            return

        doc = pdfium.PdfDocument(payload)
        try:
            page_count = len(doc)
            if page_count > limits.max_pages:
                out.put(RejectedDocument(f"rejected: page count {page_count} exceeds the page cap", Rejection.TOO_MANY_PAGES))
                return

            text_parts: list[str] = []
            for i in range(page_count):
                page = doc.get_page(i)
                try:
                    text_parts.append(page.get_textpage().get_text_range())
                finally:
                    page.close()
            out.put(ParsedDocument(page_count=page_count, text="".join(text_parts)))
        finally:
            doc.close()
    except Exception as exc:  # noqa: BLE001 - a parse failure must return, never raise
        out.put(RejectedDocument(f"rejected: could not parse ({exc})", Rejection.UNPARSEABLE))


def parse_within_limits(payload: bytes, limits: Limits) -> ParseResult:
    """Parse `payload` as a PDF, or reject it, within `limits`.

    The only place PDF bytes are parsed anywhere in this codebase — enforced
    by `test_intake_is_the_only_caller_of_the_parser`. Byte length and the
    `%PDF` magic are checked right here, in this process, before a child is
    ever spawned — a 2GB upload never reaches pdfium, let alone a decoder.
    Everything that actually touches document structure or content — page
    count, decompression, text — runs in a child that the parent kills on
    timeout, so a hang never blocks the caller and a memory blowout never
    reaches this process.
    """
    if not payload.startswith(b"%PDF"):
        return RejectedDocument("rejected: not a PDF (bad magic bytes)", Rejection.NOT_PDF)
    if len(payload) > limits.max_bytes:
        return RejectedDocument(f"rejected: payload size {len(payload)} exceeds the size cap", Rejection.TOO_LARGE)

    ctx = multiprocessing.get_context("spawn")
    queue: multiprocessing.Queue[ParseResult] = ctx.Queue()
    process = ctx.Process(target=_parse_child, args=(payload, limits, queue))
    process.start()
    process.join(limits.timeout_seconds)

    if process.is_alive():
        process.kill()
        process.join()
        return RejectedDocument("rejected: parsing exceeded the wall-clock timeout", Rejection.TIMEOUT)

    try:
        return queue.get_nowait()
    except Empty:
        return RejectedDocument(f"rejected: parser exited without a result (code {process.exitcode})", Rejection.UNPARSEABLE)
