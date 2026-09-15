"""Never #1, DoD 8, re-tested against the local seam (F-60: the feature is
cuttable, this file is not).

The spy doubles the TRANSPORT only. `admit` is always the real one — a mocked
boundary stops nothing at 2am in week 11.
"""

import pytest

from ledger_sense.boundary import Labelled, Origin, Sensitivity, SensitivityViolation
from ledger_sense.gateway import Lane, ProviderDisabled, ProviderPolicy, admit

PAYLOAD = b"%PDF-1.4 invoice"

# The allow-matrix for the model lane, written out. A fifth Sensitivity value
# fails test_the_matrix_names_every_sensitivity until someone decides its row.
#   PUBLIC: 00 R4 admits it; 02 Never #1 says "SYNTHETIC or REDACTED only".
#   The brief wins for this product.
ADMITTED_TO_THE_MODEL_LANE = {
    Sensitivity.PUBLIC: False,
    Sensitivity.SYNTHETIC: True,
    Sensitivity.REDACTED: True,  # with opt-in; the opt-in tests cover the flip
    Sensitivity.CLIENT_CONFIDENTIAL: False,
}

ENABLED = ProviderPolicy(enabled=True, redacted_opt_in=True)
DISABLED = ProviderPolicy(enabled=False, redacted_opt_in=True)


class SpyTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[Lane, bytes]] = []

    def send(self, lane: Lane, payload: bytes) -> bytes:
        self.sent.append((lane, payload))
        return b"response"


def _doc(sensitivity: Sensitivity) -> Labelled[bytes]:
    # Tests may construct Labelled directly; the structural guard scans
    # ledger_sense/ only. The gateway keys on sensitivity, not origin.
    return Labelled(payload=PAYLOAD, sensitivity=sensitivity, origin=Origin.UPLOAD,
                    provenance="test")


def test_the_matrix_names_every_sensitivity() -> None:
    assert set(ADMITTED_TO_THE_MODEL_LANE) == set(Sensitivity)


@pytest.mark.parametrize("sensitivity", list(Sensitivity))
def test_the_model_lane_follows_the_matrix(sensitivity: Sensitivity) -> None:
    spy = SpyTransport()
    if ADMITTED_TO_THE_MODEL_LANE[sensitivity]:
        assert admit(Lane.EXTRACT_VISION, _doc(sensitivity), policy=ENABLED,
                     transport=spy) == b"response"
        assert spy.sent == [(Lane.EXTRACT_VISION, PAYLOAD)]
    else:
        with pytest.raises(SensitivityViolation):
            admit(Lane.EXTRACT_VISION, _doc(sensitivity), policy=ENABLED, transport=spy)
        assert spy.sent == []


@pytest.mark.parametrize("policy", [ENABLED, DISABLED], ids=["enabled", "disabled"])
def test_client_confidential_is_refused_before_the_provider_is_consulted(
    policy: ProviderPolicy,
) -> None:
    """The ordering test. With the provider disabled the refusal must still be
    SensitivityViolation, never ProviderDisabled: if the provider were checked
    first, enabling it would be the only thing between a client's invoice and
    a free tier."""
    spy = SpyTransport()
    with pytest.raises(SensitivityViolation):
        admit(Lane.EXTRACT_VISION, _doc(Sensitivity.CLIENT_CONFIDENTIAL), policy=policy,
              transport=spy)
    assert spy.sent == []


def test_synthetic_with_the_provider_disabled_raises_provider_disabled() -> None:
    """The other half of the ordering: the provider check exists and is second."""
    spy = SpyTransport()
    with pytest.raises(ProviderDisabled):
        admit(Lane.EXTRACT_VISION, _doc(Sensitivity.SYNTHETIC), policy=DISABLED,
              transport=spy)
    assert spy.sent == []


def test_redacted_without_opt_in_is_refused() -> None:
    spy = SpyTransport()
    no_opt_in = ProviderPolicy(enabled=True, redacted_opt_in=False)
    with pytest.raises(SensitivityViolation, match="opt-in"):
        admit(Lane.EXTRACT_VISION, _doc(Sensitivity.REDACTED), policy=no_opt_in,
              transport=spy)
    assert spy.sent == []


def test_redacted_with_opt_in_is_sent() -> None:
    spy = SpyTransport()
    admit(Lane.EXTRACT_VISION, _doc(Sensitivity.REDACTED), policy=ENABLED, transport=spy)
    assert spy.sent == [(Lane.EXTRACT_VISION, PAYLOAD)]


@pytest.mark.parametrize("sensitivity", list(Sensitivity))
def test_the_local_lane_admits_everything_with_every_provider_disabled(
    sensitivity: Sensitivity,
) -> None:
    """00 R3 / degraded mode: the deterministic path is not a provider."""
    spy = SpyTransport()
    admit(Lane.LOCAL_FALLBACK, _doc(sensitivity), policy=DISABLED, transport=spy)
    assert spy.sent == [(Lane.LOCAL_FALLBACK, PAYLOAD)]


def test_only_the_local_fallback_lane_is_not_a_model_lane() -> None:
    assert [lane for lane in Lane if not lane.is_model] == [Lane.LOCAL_FALLBACK]
