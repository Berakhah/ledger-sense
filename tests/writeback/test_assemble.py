"""writeback/assemble.py: claims -> WriteRequest, and the idempotency key
derived independently of vendor identity (Ruling 5, Phase 6 spec)."""

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from ledger_sense.auth.models import create_tenant
from ledger_sense.boundary import Origin, Sensitivity
from ledger_sense.store import session as store_session
from ledger_sense.store.retention import claims, documents
from ledger_sense.writeback.assemble import MissingClaimError, derive_idempotency_key
from ledger_sense.writeback.interface import build_idempotency_key


@pytest.fixture()
def tenant_id() -> str:
    return create_tenant(f"tenant-{uuid.uuid4().hex[:8]}")


def _insert_document(conn: sa.Connection, tenant_id: str, *,
                     content_sha256: str | None = "abc123") -> str:
    document_id = uuid.uuid4().hex
    conn.execute(documents.insert().values(
        id=document_id, tenant_id=tenant_id, blob_id=None,
        received_at=datetime.now(UTC), sensitivity=Sensitivity.SYNTHETIC.value,
        origin=Origin.CORPUS.value, provenance="tests/writeback/test_assemble.py",
        filename="fixture.pdf", content_sha256=content_sha256))
    return document_id


def _insert_claim(conn: sa.Connection, tenant_id: str, document_id: str, *,
                  field: str, value: str, value_type: str = "str") -> None:
    conn.execute(claims.insert().values(
        id=uuid.uuid4().hex, tenant_id=tenant_id, document_id=document_id,
        field=field, value=value, value_type=value_type, method="deterministic",
        confidence=None, match_quality=1.0, page=0, x0=0.0, top=0.0, x1=1.0,
        bottom=1.0, caveat=None, observed_at=datetime.now(UTC)))


def test_derive_idempotency_key_matches_build_idempotency_key(tenant_id: str) -> None:
    with store_session.app_engine().begin() as conn:
        document_id = _insert_document(conn, tenant_id)
        _insert_claim(conn, tenant_id, document_id, field="contract_id", value="C-1")
        _insert_claim(conn, tenant_id, document_id, field="application_no",
                      value="2", value_type="int")
        key = derive_idempotency_key(conn, document_id=document_id)
    assert key == build_idempotency_key("abc123", "C-1", 2)


def test_derive_idempotency_key_is_stable_across_two_calls(tenant_id: str) -> None:
    with store_session.app_engine().begin() as conn:
        document_id = _insert_document(conn, tenant_id)
        _insert_claim(conn, tenant_id, document_id, field="contract_id", value="C-1")
        _insert_claim(conn, tenant_id, document_id, field="application_no",
                      value="2", value_type="int")
        first = derive_idempotency_key(conn, document_id=document_id)
        second = derive_idempotency_key(conn, document_id=document_id)
    assert first == second


def test_derive_idempotency_key_refuses_a_document_with_no_content_hash(tenant_id: str) -> None:
    with store_session.app_engine().begin() as conn:
        document_id = _insert_document(conn, tenant_id, content_sha256=None)
        with pytest.raises(MissingClaimError, match="content_sha256"):
            derive_idempotency_key(conn, document_id=document_id)


def test_derive_idempotency_key_refuses_a_document_missing_contract_id(tenant_id: str) -> None:
    with store_session.app_engine().begin() as conn:
        document_id = _insert_document(conn, tenant_id)
        _insert_claim(conn, tenant_id, document_id, field="application_no",
                      value="2", value_type="int")
        with pytest.raises(MissingClaimError, match="contract_id"):
            derive_idempotency_key(conn, document_id=document_id)


def test_derive_idempotency_key_picks_the_latest_claim_for_a_field(tenant_id: str) -> None:
    """The human-edit-supersedes-extraction rule (api/review.py::_value_type_for's
    convention): a later observed_at wins, so a reviewer's correction to
    contract_id changes which key a re-post derives."""
    with store_session.app_engine().begin() as conn:
        document_id = _insert_document(conn, tenant_id)
        conn.execute(claims.insert().values(
            id=uuid.uuid4().hex, tenant_id=tenant_id, document_id=document_id,
            field="contract_id", value="C-WRONG", value_type="str", method="deterministic",
            confidence=None, match_quality=1.0, page=0, x0=0.0, top=0.0, x1=1.0,
            bottom=1.0, caveat=None, observed_at=datetime(2026, 1, 1, tzinfo=UTC)))
        conn.execute(claims.insert().values(
            id=uuid.uuid4().hex, tenant_id=tenant_id, document_id=document_id,
            field="contract_id", value="C-CORRECT", value_type="str", method="human",
            confidence=None, match_quality=None, page=0, x0=0.0, top=0.0, x1=1.0,
            bottom=1.0, caveat=None, observed_at=datetime(2026, 1, 2, tzinfo=UTC)))
        _insert_claim(conn, tenant_id, document_id, field="application_no",
                      value="2", value_type="int")
        key = derive_idempotency_key(conn, document_id=document_id)
    assert key == build_idempotency_key("abc123", "C-CORRECT", 2)
