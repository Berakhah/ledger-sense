from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol, TypeVar, runtime_checkable

from ledger_sense.boundary import Labelled
from ledger_sense.rules.provenance import RuleSource

__all__ = ["Claim", "ClaimProtocol", "FileSource", "HumanSource", "Labelled", "Method", "RuleSource"]


class Method(str, Enum):
    """Enumeration of the methods by which a claim's value was derived."""

    DETERMINISTIC = "deterministic"
    MODEL = "model"
    HUMAN = "human"
    DERIVED = "derived"


@dataclass(frozen=True)
class FileSource:
    """Source information for a field extracted from a document."""

    path: str
    page: int
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class HumanSource:
    """Source information for a field reviewed or validated by a human."""

    reviewer_id: str
    reviewed_at: datetime


T_co = TypeVar("T_co", covariant=True)


@runtime_checkable
class ClaimProtocol(Protocol[T_co]):
    """Structural protocol for a claim about a value.

    Allows bok_core's eventual concrete implementation to satisfy this
    structurally without inheritance.
    """

    @property
    def value(self) -> T_co: ...

    @property
    def source(self) -> FileSource | HumanSource: ...

    @property
    def method(self) -> Method: ...

    @property
    def confidence(self) -> float | None: ...

    @property
    def match_quality(self) -> float | None: ...

    @property
    def observed_at(self) -> datetime: ...

    @property
    def parents(self) -> Sequence["ClaimProtocol[object]"]: ...

    @property
    def caveat(self) -> str | None: ...


T = TypeVar("T")


@dataclass(frozen=True)
class Claim[T]:
    """A claim about a value with provenance, confidence, and method information."""

    value: T
    source: FileSource | HumanSource
    method: Method
    confidence: float | None
    observed_at: datetime
    match_quality: float | None = None
    parents: tuple["Claim[object]", ...] = field(default_factory=tuple)
    caveat: str | None = None
