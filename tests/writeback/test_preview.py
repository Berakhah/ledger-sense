"""writeback/preview.py: the read-only posting preview (LS-04).

The write amount today is the sum of the lines' `this_period`. The payment
chain the rules already define (A5/A6) says the payable is total completed and
stored, less retainage, less previous payments. The preview puts both side by
side and says whether they agree. It does not decide which is right."""

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from ledger_sense.auth.models import create_tenant
from ledger_sense.boundary import Origin, Sensitivity
from ledger_sense.domain.money import money
from ledger_sense.store import session as store_session
from ledger_sense.store.retention import claims, documents
from ledger_sense.writeback.assemble import MissingClaimError
from ledger_sense.writeback.preview import build_posting_preview

pytestmark = pytest.mark.usefixtures("_database")


@pytest.fixture()
def tenant_id() -> str:
    return create_tenant(f"tenant-{uuid.uuid4().hex[:8]}")


def _seed(conn: sa.Connection, tenant_id: str, rows: list[tuple[str, str, str]]) -> str:
    document_id = uuid.uuid4().hex
    conn.execute(documents.insert().values(
        id=document_id, tenant_id=tenant_id, blob_id=None,
        received_at=datetime.now(UTC), sensitivity=Sensitivity.SYNTHETIC.value,
        origin=Origin.CORPUS.value, provenance="tests/writeback/test_preview.py",
        filename="fixture.pdf", content_sha256="abc123"))
    for field, value, value_type in rows:
        _claim(conn, tenant_id, document_id, field, value, value_type)
    return document_id


def _claim(conn: sa.Connection, tenant_id: str, document_id: str,
           field: str, value: str, value_type: str = "money") -> None:
    conn.execute(claims.insert().values(
        id=uuid.uuid4().hex, tenant_id=tenant_id, document_id=document_id,
        field=field, value=value, value_type=value_type, method="deterministic",
        confidence=None, match_quality=1.0, page=0, x0=0.0, top=0.0, x1=1.0,
        bottom=1.0, caveat=None, observed_at=datetime.now(UTC)))


def _rows(*, this_period: list[str], tcs: str = "1000.00", retainage: str = "50.00",
          previous: str = "0.00") -> list[tuple[str, str, str]]:
    rows = [("total_completed_and_stored", tcs, "money"),
            ("retainage_total", retainage, "money"),
            ("previous_payments", previous, "money")]
    for index, amount in enumerate(this_period):
        rows.append((f"line[{index:03d}].description", f"Item {index}", "str"))
        rows.append((f"line[{index:03d}].this_period", amount, "money"))
    return rows


def test_the_audit_example_is_flagged_as_not_reconciling(tenant_id: str) -> None:
    """LS-04: completed 1,000, retainage 50, previous 0, this-period 1,000 is
    written as 1,000 although the payable under A5/A6 is 950."""
    with store_session.app_engine().begin() as conn:
        document_id = _seed(conn, tenant_id, _rows(this_period=["1000.00"]))
        preview = build_posting_preview(conn, tenant_id=tenant_id, document_id=document_id)
    assert preview.amount_to_write == money("1000.00")
    assert preview.expected_payable == money("950.00")
    assert preview.difference == money("50.00")
    assert preview.reconciles is False


def test_an_agreeing_document_reconciles_across_several_lines(tenant_id: str) -> None:
    with store_session.app_engine().begin() as conn:
        document_id = _seed(conn, tenant_id, _rows(this_period=["400.00", "550.00"]))
        preview = build_posting_preview(conn, tenant_id=tenant_id, document_id=document_id)
    assert preview.amount_to_write == money("950.00")
    assert preview.difference == money("0.00")
    assert preview.reconciles is True


def test_previous_payments_are_subtracted_once(tenant_id: str) -> None:
    with store_session.app_engine().begin() as conn:
        document_id = _seed(conn, tenant_id, _rows(
            this_period=["600.00"], tcs="1500.00", retainage="75.00", previous="825.00"))
        preview = build_posting_preview(conn, tenant_id=tenant_id, document_id=document_id)
    assert preview.expected_payable == money("600.00")   # 1500 - 75 - 825
    assert preview.reconciles is True


def test_a_reviewers_correction_changes_the_expected_payable(tenant_id: str) -> None:
    """The latest claim wins, same rule as the write request itself."""
    with store_session.app_engine().begin() as conn:
        document_id = _seed(conn, tenant_id, _rows(this_period=["950.00"]))
        _claim(conn, tenant_id, document_id, "retainage_total", "100.00")
        preview = build_posting_preview(conn, tenant_id=tenant_id, document_id=document_id)
    assert preview.expected_payable == money("900.00")
    assert preview.reconciles is False


def test_a_missing_summary_claim_is_refused_not_guessed(tenant_id: str) -> None:
    rows = [row for row in _rows(this_period=["950.00"]) if row[0] != "previous_payments"]
    with store_session.app_engine().begin() as conn:
        document_id = _seed(conn, tenant_id, rows)
        with pytest.raises(MissingClaimError, match="previous_payments"):
            build_posting_preview(conn, tenant_id=tenant_id, document_id=document_id)
