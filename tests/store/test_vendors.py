import os
import pathlib
import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ledger_sense.auth.models import tenants
from ledger_sense.boundary import Origin, Sensitivity
from ledger_sense.domain.vendor import PaymentDetail, VendorLedger, payment_hmac
from ledger_sense.store import session as store_session
from ledger_sense.store.retention import documents, vendors
from ledger_sense.store.vendors import load_vendor_ledger, save_vendor_ledger
from tests.store.conftest import SeededDocuments

OBSERVED_AT = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def test_an_empty_tenant_round_trips_to_an_empty_ledger(
    seeded_documents: SeededDocuments,
) -> None:
    with store_session.app_engine().connect() as conn:
        ledger = load_vendor_ledger(conn, seeded_documents.tenant_id)
    assert ledger.get("nope") is None


def test_a_vendor_with_an_alias_and_a_payment_detail_round_trips(
    seeded_documents: SeededDocuments, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEDGER_SENSE_PAYMENT_HMAC_KEY", "test-key")
    ledger = VendorLedger()
    vendor_id = ledger.create("Acme Steel Company")
    ledger.confirm_alias(vendor_id, "Acme Steel Co.", source_document="doc-1",
                         confirmed_by="reviewer-1")
    ledger.record_payment_detail(
        vendor_id,
        PaymentDetail(account_last4="1234", routing_last4="5678",
                      account_hmac=payment_hmac("00001234", "00005678"),
                      observed_at=OBSERVED_AT, source_document="doc-1"),
        confirmed_by="")

    with store_session.app_engine().begin() as conn:
        save_vendor_ledger(conn, seeded_documents.tenant_id, ledger)
    with store_session.app_engine().connect() as conn:
        reloaded = load_vendor_ledger(conn, seeded_documents.tenant_id)

    vendor = reloaded.get(vendor_id)
    assert vendor is not None
    assert vendor.name == "Acme Steel Company"
    assert [a.name for a in vendor.aliases] == ["Acme Steel Co."]
    assert vendor.aliases[0].confirmed_by == "reviewer-1"
    assert len(vendor.payment_history) == 1
    assert vendor.payment_history[0].account_last4 == "1234"


def test_another_tenants_vendors_are_invisible(
    seeded_documents: SeededDocuments,
) -> None:
    ledger = VendorLedger()
    ledger.create("Acme Steel Company")
    with store_session.app_engine().begin() as conn:
        save_vendor_ledger(conn, seeded_documents.tenant_id, ledger)

    with store_session.app_engine().connect() as conn:
        other_tenants_ledger = load_vendor_ledger(conn, "someone-else")
        # sanity: the row does exist, just not for "someone-else"
        assert conn.execute(sa.select(vendors)).all() != []
    assert other_tenants_ledger.get("nope") is None
    assert other_tenants_ledger.all_vendors() == []


def test_resaving_replaces_rather_than_accumulates(
    seeded_documents: SeededDocuments,
) -> None:
    ledger = VendorLedger()
    vendor_id = ledger.create("Acme Steel Company")
    with store_session.app_engine().begin() as conn:
        save_vendor_ledger(conn, seeded_documents.tenant_id, ledger)
        save_vendor_ledger(conn, seeded_documents.tenant_id, ledger)
    with store_session.app_engine().connect() as conn:
        reloaded = load_vendor_ledger(conn, seeded_documents.tenant_id)
    assert len(reloaded.all_vendors()) == 1
    assert reloaded.get(vendor_id) is not None


def _alembic_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config


@pytest.mark.skipif(
    not os.environ.get("LEDGER_SENSE_LIVE_POSTGRES_URL"),
    reason="set LEDGER_SENSE_LIVE_POSTGRES_URL to validate against a live Postgres",
)
def test_resaving_does_not_violate_the_documents_vendor_id_fk_on_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """documents.vendor_id's FK is non-deferrable by default, and
    save_vendor_ledger deletes every one of a tenant's vendors rows and
    reinserts them (same ids) inside one transaction. Postgres checks a
    non-deferrable FK the instant DELETE FROM vendors runs, which would
    raise even though the same vendor id exists again by commit — SQLite
    never enforces this FK at all, so only a live Postgres can prove the
    deferred-constraint fix in migration 0007/retention.py actually works.
    """
    monkeypatch.setenv("LEDGER_SENSE_DB_URL", os.environ["LEDGER_SENSE_LIVE_POSTGRES_URL"])
    store_session.reset_app_engine()
    try:
        command.upgrade(_alembic_config(), "head")

        tenant_id = uuid.uuid4().hex
        document_id = uuid.uuid4().hex
        with store_session.app_engine().begin() as conn:
            conn.execute(tenants.insert().values(
                id=tenant_id, name="pg-fk-tenant",
                created_at=datetime.now(UTC), retention_days=365))
            conn.execute(documents.insert().values(
                id=document_id, tenant_id=tenant_id, blob_id=None,
                received_at=datetime.now(UTC),
                sensitivity=Sensitivity.SYNTHETIC.value, origin=Origin.CORPUS.value,
                provenance="tests/store/test_vendors.py", filename="fk-test",
            ))

        ledger = VendorLedger()
        vendor_id = ledger.create("Acme Steel Company")
        with store_session.app_engine().begin() as conn:
            save_vendor_ledger(conn, tenant_id, ledger)
            conn.execute(documents.update().where(documents.c.id == document_id)
                         .values(vendor_id=vendor_id))

        # Resaving the same ledger deletes and reinserts the same vendor id
        # within one transaction; a non-deferrable FK would raise here.
        with store_session.app_engine().begin() as conn:
            save_vendor_ledger(conn, tenant_id, ledger)

        with store_session.app_engine().connect() as conn:
            row = conn.execute(
                sa.select(documents.c.vendor_id).where(documents.c.id == document_id)
            ).one()
        assert row.vendor_id == vendor_id
    finally:
        command.downgrade(_alembic_config(), "base")
        store_session.reset_app_engine()
