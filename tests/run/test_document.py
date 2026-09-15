"""run_document: the guard. Extraction behaviour arrives in later tests."""

from datetime import UTC, datetime

import pytest  # noqa: F401 -- unused here; Task 6's tests in this module use monkeypatch
import sqlalchemy as sa

from ledger_sense.corpus.generate import generate
from ledger_sense.run.document import RunOutcome, run_document
from ledger_sense.store.retention import documents, findings
from tests.run.conftest import RunDb


def _clean_pdf(seed: int = 1) -> bytes:
    return generate(seed=seed, error="clean")[2]


def test_a_missing_document_is_reported_not_raised(db: RunDb) -> None:
    outcome = run_document(db.engine, "nope")
    assert outcome == RunOutcome("nope", "missing", None, 0, 0)


def test_a_document_that_is_not_runnable_is_left_alone(db: RunDb) -> None:
    document_id = db.store_pdf(_clean_pdf())
    with db.engine.begin() as conn:
        conn.execute(documents.update().where(documents.c.id == document_id)
                     .values(status="extracted", run_attempts=3))
    outcome = run_document(db.engine, document_id)
    assert outcome.status == "extracted"
    assert outcome.note == "already extracted"
    assert db.document(document_id).run_attempts == 3


def test_every_run_increments_the_attempt_counter(db: RunDb) -> None:
    document_id = db.store_pdf(_clean_pdf())
    run_document(db.engine, document_id)
    assert db.document(document_id).run_attempts == 1


def test_a_document_with_a_human_decision_is_never_overwritten(db: RunDb) -> None:
    document_id = db.store_pdf(_clean_pdf())
    with db.engine.begin() as conn:
        conn.execute(documents.update().where(documents.c.id == document_id)
                     .values(status="failed"))
        conn.execute(findings.insert().values(
            id="f1", tenant_id=db.tenant_id, document_id=document_id, rule_id="A1",
            field="total_completed_and_stored", message="m", state="accepted",
            created_at=datetime.now(UTC)))
    outcome = run_document(db.engine, document_id)
    assert outcome.status == "failed"
    assert outcome.note == "human decisions present; refusing to overwrite"
    with db.engine.connect() as conn:
        assert conn.execute(sa.select(findings.c.state)
                            .where(findings.c.document_id == document_id)).scalar() == "accepted"
