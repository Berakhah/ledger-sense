"""Document intake — the only caller of `parse_within_limits` (Rule 2).

A later, gated task (the CLIENT_CONFIDENTIAL sensitivity boundary — not built
here, see task brief) adds a pre-parse check in front of this call. Until
then this is a thin pass-through so that every parse anywhere in the codebase
goes through the resource caps in `limits.py`, never around them.
"""

from __future__ import annotations

from ledger_sense.ingest.limits import Limits, ParseResult, parse_within_limits


def intake(payload: bytes, limits: Limits | None = None) -> ParseResult:
    """Parse an uploaded document within resource limits."""
    return parse_within_limits(payload, limits if limits is not None else Limits())
