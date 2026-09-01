from datetime import date
from typing import Protocol, runtime_checkable


@runtime_checkable
class RuleSource(Protocol):
    """Structural type. bok_core.provenance.RuleSource satisfies this as-is."""

    authority: str
    citation: str
    confirmed_by: str | None
    confirmed_at: date | None
