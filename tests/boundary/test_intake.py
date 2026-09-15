"""§2: the label attaches where the file is opened, derived from where the
bytes came from. There is no sensitivity parameter to get wrong."""

import inspect
from typing import cast

import pytest

from ledger_sense.boundary import Origin, Sensitivity, SensitivityViolation
from ledger_sense.ingest.intake import RedactionVerdict, accept_document, intake

PAYLOAD = b"%PDF-1.4 invoice"

EXPECTED_LABEL = {
    Origin.UPLOAD: Sensitivity.CLIENT_CONFIDENTIAL,
    Origin.CORPUS: Sensitivity.SYNTHETIC,
    Origin.REDACTED_UPLOAD: Sensitivity.REDACTED,
}


def test_every_origin_has_a_decided_label() -> None:
    assert set(EXPECTED_LABEL) == set(Origin)


@pytest.mark.parametrize("origin", [Origin.UPLOAD, Origin.CORPUS])
def test_the_label_is_derived_from_the_origin(origin: Origin) -> None:
    doc = accept_document(PAYLOAD, origin=origin, provenance="test")
    assert doc.sensitivity is EXPECTED_LABEL[origin]
    assert doc.origin is origin
    assert doc.payload == PAYLOAD
    assert doc.provenance == "test"


def test_an_upload_is_client_confidential_with_no_way_to_say_otherwise() -> None:
    """The 2am case: there is no parameter through which a real upload can be
    called SYNTHETIC."""
    assert "sensitivity" not in inspect.signature(accept_document).parameters
    doc = accept_document(PAYLOAD, origin=Origin.UPLOAD, provenance="upload:test")
    assert doc.sensitivity is Sensitivity.CLIENT_CONFIDENTIAL


def test_nothing_produces_public() -> None:
    assert Sensitivity.PUBLIC not in EXPECTED_LABEL.values()


def test_redacted_without_a_verdict_is_refused() -> None:
    with pytest.raises(SensitivityViolation, match="verif"):
        accept_document(PAYLOAD, origin=Origin.REDACTED_UPLOAD, provenance="test")


def test_redacted_with_a_failed_verdict_is_refused() -> None:
    verdict = RedactionVerdict(verified=False, verifier="test", detail="3 identifiers remain")
    with pytest.raises(SensitivityViolation, match="verif"):
        accept_document(PAYLOAD, origin=Origin.REDACTED_UPLOAD, provenance="test",
                        redaction=verdict)


def test_redacted_with_a_passing_verdict_is_labelled_redacted() -> None:
    verdict = RedactionVerdict(verified=True, verifier="test")
    doc = accept_document(PAYLOAD, origin=Origin.REDACTED_UPLOAD, provenance="test",
                          redaction=verdict)
    assert doc.sensitivity is Sensitivity.REDACTED
    assert doc.origin is Origin.REDACTED_UPLOAD


@pytest.mark.parametrize("origin", [Origin.UPLOAD, Origin.CORPUS])
def test_a_verdict_does_not_lift_a_non_redacted_origin(origin: Origin) -> None:
    """A passing verdict on an UPLOAD does not make it REDACTED; the origin
    would have to be REDACTED_UPLOAD. Refuse, so the mistake is loud."""
    verdict = RedactionVerdict(verified=True, verifier="test")
    with pytest.raises(SensitivityViolation, match="origin"):
        accept_document(PAYLOAD, origin=origin, provenance="test", redaction=verdict)


def test_the_parse_pass_through_is_untouched() -> None:
    """Rule 2: intake() is still the only caller of parse_within_limits."""
    assert callable(intake)


def test_a_raw_string_origin_is_coerced_at_the_door() -> None:
    """Symmetry with gateway._check_label's Sensitivity coercion: a raw
    "UPLOAD" string must not hash into _LABEL_OF un-coerced."""
    doc = accept_document(PAYLOAD, origin=cast(Origin, "UPLOAD"), provenance="test")
    assert doc.origin is Origin.UPLOAD
    assert doc.sensitivity is Sensitivity.CLIENT_CONFIDENTIAL


def test_an_unknown_raw_string_origin_fails_closed() -> None:
    with pytest.raises(ValueError):
        accept_document(PAYLOAD, origin=cast(Origin, "EMAIL"), provenance="test")
