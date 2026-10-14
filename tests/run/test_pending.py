"""run_pending and the concurrency guard."""

import logging
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import OperationalError

from ledger_sense.corpus.generate import generate
from ledger_sense.extract.assemble import assemble_pay_application
from ledger_sense.run.document import run_document, run_in_background, run_pending
from ledger_sense.store.retention import documents
from tests.run.conftest import RunDb, _ledger_sense_loggers_enabled


def _pdf(seed: int = 1) -> bytes:
    return generate(seed=seed, error="clean")[2]


def test_pending_runs_oldest_first_and_honours_the_limit(db: RunDb) -> None:
    t0 = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)
    ids = [db.store_pdf(_pdf(seed), received_at=t0 + timedelta(minutes=i))
           for i, seed in enumerate((1, 2, 3))]
    outcomes = run_pending(db.engine, limit=2)
    assert [o.document_id for o in outcomes] == ids[:2]
    assert db.document(ids[2]).status == "received"
    assert [o.document_id for o in run_pending(db.engine)] == [ids[2]]
    assert run_pending(db.engine) == []


def test_pending_picks_up_failed_documents_too(db: RunDb) -> None:
    document_id = db.store_pdf(_pdf())
    with db.engine.begin() as conn:
        conn.execute(documents.update().where(documents.c.id == document_id)
                     .values(status="failed", run_note="failed: RuntimeError"))
    assert [o.status for o in run_pending(db.engine)] == ["extracted"]


def test_one_failing_document_does_not_stop_the_batch(
    db: RunDb, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ledger_sense.run.document as run_module
    bad = db.store_pdf(_pdf(), received_at=datetime(2026, 1, 1, tzinfo=UTC))
    good = db.store_pdf(_pdf(2), received_at=datetime(2026, 1, 2, tzinfo=UTC))
    real = assemble_pay_application

    def _fail_bad(pdf: bytes, source_path: str) -> object:
        if source_path == bad:
            raise RuntimeError("x")
        return real(pdf, source_path)
    monkeypatch.setattr(run_module, "assemble_pay_application", _fail_bad)
    outcomes = {o.document_id: o.status for o in run_pending(db.engine)}
    assert outcomes == {bad: "failed", good: "extracted"}


def test_two_runners_on_one_document_write_exactly_one_result(
    db: RunDb, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The step-1 UPDATE takes the write lock. On SQLite the second runner
    either waits (busy timeout, 5 s by default) and then finds the row
    `extracted`, or hits the timeout and raises OperationalError — an
    infrastructure error, which propagates. Either way exactly one set of
    claims exists and the row was never `failed`."""
    import ledger_sense.run.document as run_module
    document_id = db.store_pdf(_pdf())
    real = assemble_pay_application
    entered = threading.Event()

    def _slow(pdf: bytes, source_path: str) -> object:
        if not entered.is_set():
            entered.set()
            time.sleep(1.0)  # hold the lock until the other thread is queued on its UPDATE
        return real(pdf, source_path)
    monkeypatch.setattr(run_module, "assemble_pay_application", _slow)

    results: list[object] = []

    def _run() -> None:
        try:
            results.append(run_document(db.engine, document_id))
        except OperationalError as exc:
            results.append(exc)

    threads = [threading.Thread(target=_run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    statuses = [getattr(r, "status", "locked") for r in results]
    assert "extracted" in statuses
    assert "failed" not in statuses
    assert db.document(document_id).status == "extracted"
    expected_claims = len({k for k in real(_pdf(), document_id).provenance
                           if not k.startswith("_")})
    assert len(db.claims_for(document_id)) == expected_claims


def test_run_in_background_swallows_and_logs_everything(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    import ledger_sense.run.document as run_module

    def _boom(engine: object, document_id: str, **kw: object) -> object:
        raise RuntimeError("db")
    monkeypatch.setattr(run_module, "run_document", _boom)
    monkeypatch.setattr(run_module, "app_engine", lambda: object())
    with caplog.at_level("ERROR"):
        run_in_background("doc-1", "tenant-1")  # must not raise
    assert any("doc-1" in r.message and "tenant-1" in r.message for r in caplog.records)


def test_db_fixture_leaves_the_run_document_logger_enabled(db: RunDb) -> None:
    """The minimum covering assertion: whatever the `db` fixture does about
    alembic disabling loggers, ledger_sense.run.document's logger — the one
    run_in_background logs through — must be usable while `db` is live."""
    assert logging.getLogger("ledger_sense.run.document").disabled is False


def test_ledger_sense_loggers_enabled_restores_the_pre_entry_disabled_state() -> None:
    """Proves the scoping directly, without depending on alembic or on test
    execution order: a logger disabled before the context is entered comes
    back disabled after it exits, even though the context (as the `db`
    fixture uses it) force-enables it for the duration in between."""
    probe = logging.getLogger("ledger_sense.throwaway_probe")
    probe.disabled = True
    with _ledger_sense_loggers_enabled() as loggers:
        assert probe.disabled is True  # entering alone does not force anything
        for logger in loggers.values():
            logger.disabled = False
        assert probe.disabled is False  # caller's force-enable took effect
    assert probe.disabled is True  # restored to the pre-entry snapshot
