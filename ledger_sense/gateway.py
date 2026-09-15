"""The model-lane seam (§3): where a label is judged, before anything else.

`bok_core.gateway` will replace the transport behind this module; `admit` and
its order stay. The order is the point:

    1. local lane → nothing to judge, the deterministic path takes everything
    2. label check → SensitivityViolation           (before any provider state)
    3. provider check → ProviderDisabled
    4. transport.send

Step 2 runs before step 3 so that no configuration change — enabling a
provider, adding a key, flipping BOK_PROVIDERS_DISABLED — can open the door.
tests/boundary/test_client_confidential_cannot_reach_a_model.py proves the
order, not just the outcome.

Deliberately not implemented: `00` R4(b), CLIENT_CONFIDENTIAL to a provider
marked trains_on_data=false on a client-paid key. `02` Never #1 forbids it in
v1, so ProviderPolicy carries no such fields. Adding them is the day that
decision is reversed, not before.

No Transport implementation ships here. The first one arrives with
models.yaml and a working free-tier account (`00` B1, `00` R1: code asks for a
lane, never a model).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final, Protocol

from ledger_sense.boundary import Labelled, Sensitivity, SensitivityViolation

__all__ = ["Lane", "ProviderDisabled", "ProviderPolicy", "Transport", "admit"]


class Lane(str, Enum):
    """`00` R1 lane names. local-fallback must exist for every lane."""

    LOCAL_FALLBACK = "local-fallback"
    EXTRACT_VISION = "extract-vision"

    @property
    def is_model(self) -> bool:
        return self is not Lane.LOCAL_FALLBACK


@dataclass(frozen=True)
class ProviderPolicy:
    """What the caller knows about the provider and the tenant.

    `enabled` is false when BOK_PROVIDERS_DISABLED is set, when no key is
    configured, or when no models.yaml names a provider for the lane — the
    caller decides; this module only orders the checks. `redacted_opt_in` is
    the client's written opt-in, a per-tenant fact the caller supplies.
    """

    enabled: bool
    redacted_opt_in: bool


class ProviderDisabled(Exception):
    """The label allowed it; the provider is not there to send it to."""


class Transport(Protocol):
    def send(self, lane: Lane, payload: bytes) -> bytes: ...


# Never #1: the model lane accepts SYNTHETIC or REDACTED only.
_MODEL_LANE_ADMITS: Final[frozenset[Sensitivity]] = frozenset(
    {Sensitivity.SYNTHETIC, Sensitivity.REDACTED})


def _check_label(lane: Lane, doc: Labelled[bytes], policy: ProviderPolicy) -> None:
    if doc.sensitivity not in _MODEL_LANE_ADMITS:
        raise SensitivityViolation(
            f"{doc.sensitivity.value} may not reach the {lane.value} lane "
            f"(02 Never #1: SYNTHETIC or REDACTED only); origin {doc.origin.value}, "
            f"provenance {doc.provenance}")
    if doc.sensitivity is Sensitivity.REDACTED and not policy.redacted_opt_in:
        raise SensitivityViolation(
            f"REDACTED may reach the {lane.value} lane only with the client's written "
            "opt-in, and this tenant has not opted in")


def admit(
    lane: Lane,
    doc: Labelled[bytes],
    *,
    policy: ProviderPolicy,
    transport: Transport,
) -> bytes:
    """Send `doc` down `lane` if — and only after — its label allows it."""
    if lane.is_model:
        _check_label(lane, doc, policy)
        if not policy.enabled:
            raise ProviderDisabled(f"no provider is enabled for the {lane.value} lane")
    return transport.send(lane, doc.payload)
