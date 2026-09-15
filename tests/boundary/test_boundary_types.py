"""§1 of the sensitivity-gateway design: the vocabulary, as types."""

import ast
import dataclasses
import pathlib

import pytest

from ledger_sense.boundary import Labelled, Origin, Sensitivity, SensitivityViolation


def test_sensitivity_carries_the_00_r4_vocabulary_verbatim() -> None:
    assert [s.value for s in Sensitivity] == [
        "PUBLIC", "SYNTHETIC", "REDACTED", "CLIENT_CONFIDENTIAL"]


def test_origin_names_the_three_ways_bytes_enter_this_product() -> None:
    assert [o.value for o in Origin] == ["UPLOAD", "CORPUS", "REDACTED_UPLOAD"]


def test_sensitivity_violation_is_a_plain_exception() -> None:
    with pytest.raises(SensitivityViolation):
        raise SensitivityViolation("refused")


def test_labelled_is_frozen() -> None:
    doc = Labelled(payload=b"%PDF-1.4", sensitivity=Sensitivity.SYNTHETIC,
                   origin=Origin.CORPUS, provenance="corpus://seed-1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        doc.sensitivity = Sensitivity.PUBLIC  # type: ignore[misc]


def test_labelled_is_reexported_from_provenance_unchanged() -> None:
    """F-61: `from ledger_sense.provenance import Labelled` is the spelling the
    Phase 2/4 tasks use. It must be the same class, not a second one."""
    from ledger_sense.provenance import Labelled as FromProvenance
    assert FromProvenance is Labelled


def test_boundary_is_a_leaf_module() -> None:
    """Anything may import boundary.py; boundary.py imports nothing of ours."""
    source = pathlib.Path("ledger_sense/boundary.py").read_text(encoding="utf-8")
    ours = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("ledger_sense"):
            ours.append(node.lineno)
        if isinstance(node, ast.Import) and any(
                alias.name.startswith("ledger_sense") for alias in node.names):
            ours.append(node.lineno)
    assert ours == []
