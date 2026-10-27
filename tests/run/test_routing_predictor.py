"""LS-09 follow-up: the Router is asked about the score review labels are fitted on."""

from datetime import UTC, datetime
from typing import cast

from ledger_sense.route.router import Router, RoutingDecision
from ledger_sense.run.document import _routing_rows


class _RecordingRouter:
    def __init__(self) -> None:
        self.seen: list[tuple[str, object]] = []

    def decide_claim(self, field: str, confidence: object, *,
                     method: str = "DETERMINISTIC",
                     rule_failures: tuple[str, ...] = ()) -> RoutingDecision:
        self.seen.append((field, confidence))
        return RoutingDecision(field, "review", "uncalibrated", None, None, None, "stub")


def _ask(confidence: float | None, match_quality: float | None) -> object:
    router = _RecordingRouter()
    claim: dict[str, object] = {
        "field": "total_completed_and_stored", "value": "10.00",
        "method": "deterministic", "confidence": confidence,
        "match_quality": match_quality, "page": 0,
        "x0": 0.0, "top": 0.0, "x1": 1.0, "bottom": 1.0}
    _routing_rows("t", "d", [claim], [], cast(Router, router), datetime.now(UTC))
    return router.seen[0][1]


def test_a_deterministic_claim_is_routed_on_its_match_quality() -> None:
    assert _ask(None, 0.75) == 0.75


def test_a_claim_with_its_own_confidence_keeps_it() -> None:
    assert _ask(0.4, 0.9) == 0.4
