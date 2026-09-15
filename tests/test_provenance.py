from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from ledger_sense.provenance import Claim, FileSource, HumanSource, Method, RuleSource


def test_method_has_exactly_the_four_spec_members() -> None:
    assert {m.value for m in Method} == {"deterministic", "model", "human", "derived"}


def test_file_source_carries_path_page_and_a_four_tuple_bbox() -> None:
    src = FileSource(path="corpus://seed-1", page=0, bbox=(10.0, 20.0, 30.0, 40.0))
    assert src.path == "corpus://seed-1"
    assert src.page == 0
    assert src.bbox == (10.0, 20.0, 30.0, 40.0)


def test_file_source_is_frozen() -> None:
    src = FileSource(path="x", page=0, bbox=(0.0, 0.0, 1.0, 1.0))
    with pytest.raises(FrozenInstanceError):
        src.page = 1  # type: ignore[misc]


def test_claim_carries_value_source_method_confidence_and_match_quality() -> None:
    claim = Claim(
        value="1000.00",
        source=FileSource(path="corpus://seed-1", page=0, bbox=(0.0, 0.0, 10.0, 10.0)),
        method=Method.DETERMINISTIC,
        confidence=None,
        observed_at=datetime.now(UTC),
        match_quality=0.75,
    )
    assert claim.value == "1000.00"
    assert claim.method is Method.DETERMINISTIC
    assert claim.confidence is None
    assert claim.match_quality == 0.75
    assert claim.parents == ()
    assert claim.caveat is None


def test_claim_is_frozen() -> None:
    claim = Claim(
        value="x", source=FileSource(path="x", page=0, bbox=(0.0, 0.0, 1.0, 1.0)),
        method=Method.DETERMINISTIC, confidence=None,
        observed_at=datetime.now(UTC))
    with pytest.raises(FrozenInstanceError):
        claim.value = "y"  # type: ignore[misc]


def test_human_source_carries_reviewer_and_timestamp() -> None:
    src = HumanSource(reviewer_id="u-1", reviewed_at=datetime.now(UTC))
    assert src.reviewer_id == "u-1"


def test_rule_source_is_reexported_from_rules_provenance() -> None:
    from ledger_sense.rules.provenance import RuleSource as _Original
    assert RuleSource is _Original


def test_labelled_wraps_a_payload_with_sensitivity_and_provenance() -> None:
    from ledger_sense.boundary import Origin, Sensitivity
    from ledger_sense.provenance import Labelled
    wrapped = Labelled(payload=b"%PDF-1.4", sensitivity=Sensitivity.SYNTHETIC,
                       origin=Origin.CORPUS, provenance="corpus")
    assert wrapped.payload == b"%PDF-1.4"
    assert wrapped.sensitivity is Sensitivity.SYNTHETIC
    assert wrapped.provenance == "corpus"


def test_a_dataclass_claim_satisfies_the_claim_protocol_structurally() -> None:
    """The whole point of the Protocol pattern: bok_core's eventual concrete class
    needs no inheritance from ours, only the same shape."""
    from ledger_sense.provenance import (
        ClaimProtocol,  # the Protocol, distinct name from the dataclass
    )

    claim = Claim(
        value=1,
        source=FileSource(path="x", page=0, bbox=(0.0, 0.0, 1.0, 1.0)),
        method=Method.DETERMINISTIC,
        confidence=None,
        observed_at=datetime.now(UTC),
    )
    assert isinstance(claim, ClaimProtocol)
