"""LS-09: the runner routes every extracted field through the Router, and a
field that routes to review becomes an open R1 finding, so the review queue
and the writeback gate enforce 'until calibrated, everything routes to
review' with no separate mechanism."""
from typing import cast

from ledger_sense.corpus.generate import generate
from ledger_sense.route.calibration import CalibrationStore
from ledger_sense.route.router import Router
from ledger_sense.run.document import run_document
from tests.run.conftest import RunDb


class _AlwaysCalibrated:
    """Stands in for a store whose every field has a curve that licenses auto."""

    def load_curve(self, field: str) -> object:
        class _Curve:
            state = "calibrated:field"
            basis = "field"

            def threshold_for(self, target: float) -> float:
                return 0.0

            def calibrated(self, confidence: float) -> float:
                return 1.0
        return _Curve()


def _run_clean(db: RunDb, router: Router | None) -> str:
    document_id = db.store_pdf(generate(seed=1, error="clean")[2])
    run_document(db.engine, document_id, router=router)
    return document_id


def _fields_needing_review(db: RunDb, document_id: str) -> set[str]:
    return {str(f.field) for f in db.findings_for(document_id) if f.state == "open"}


def test_an_uncalibrated_clean_upload_requires_review_of_every_extracted_field(
        db: RunDb) -> None:
    router = Router(cast(CalibrationStore, type("S", (), {"load_curve": lambda s, f: None})()))
    document_id = _run_clean(db, router)
    extracted = {str(c.field) for c in db.claims_for(document_id)}
    assert extracted, "fixture must extract something"
    assert extracted <= _fields_needing_review(db, document_id)
    assert {f.rule_id for f in db.findings_for(document_id)} >= {"R1"}


def test_with_no_router_configured_every_field_still_requires_review(db: RunDb) -> None:
    document_id = _run_clean(db, None)
    extracted = {str(c.field) for c in db.claims_for(document_id)}
    assert extracted <= _fields_needing_review(db, document_id)


def test_a_calibrated_field_takes_a_different_path(db: RunDb) -> None:
    router = Router(cast(CalibrationStore, _AlwaysCalibrated()), audit_fraction=0.0)
    document_id = _run_clean(db, router)
    r1 = [f for f in db.findings_for(document_id) if f.rule_id == "R1"]
    assert r1 == []
