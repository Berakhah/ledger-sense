"""Fixtures for the API tests.

The database is a SQLite file built by running the real migration
(`alembic upgrade head`) through LEDGER_SENSE_DB_URL, not by create_all: if
0001_tenancy and the Table definitions the code queries ever drift apart,
these tests fail rather than passing against a schema no deployment has.

`two_tenants` hands back two live, logged-in clients — separate tenants,
separate users, separate sessions — because a cross-tenant test is worthless
if either side is faked. `create_document()` models a document for the
still-stub prefixes (reconciliation, vendors, metrics): those rows live in
`stub_resources` (see api/_stub_routes.py) and carry nothing but an id and an
owner, which is all an ownership check reads. `seed_exception()` is for the
real /api/review router, so it writes real `documents` and `findings` rows
directly by SQL — findings.document_id is a foreign key, so it needs a real
document to point at, not a stub row.
"""

import pathlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from httpx import Response

from ledger_sense.api._stub_routes import STUB_METADATA, seed_resource
from ledger_sense.api.app import _login_hits, app
from ledger_sense.auth.models import create_tenant, create_user
from ledger_sense.boundary import Origin, Sensitivity
from ledger_sense.store import session as store_session
from ledger_sense.store.retention import claims, documents, findings

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

# The seeded account the given tests log in as by name.
SEED_EMAIL = "a@example.test"
SEED_PASSWORD = "changeme-in-test"  # a fixture credential, not a secret

client = TestClient(app)


@pytest.fixture(scope="session", autouse=True)
def _database(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    from alembic import command
    from alembic.config import Config

    db = tmp_path_factory.mktemp("db") / "ledger.db"
    with pytest.MonkeyPatch.context() as patch:
        # as_posix(): a Windows path's backslashes and drive colon do not
        # survive SQLAlchemy's URL parser.
        patch.setenv("LEDGER_SENSE_DB_URL", f"sqlite:///{db.as_posix()}")
        patch.setenv("LEDGER_SENSE_KEK", Fernet.generate_key().decode())
        patch.setenv("LEDGER_SENSE_BLOB_ROOT", str(tmp_path_factory.mktemp("blobs")))
        store_session.reset_app_engine()

        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        command.upgrade(config, "head")

        STUB_METADATA.create_all(store_session.app_engine())
        create_user(create_tenant("seed"), SEED_EMAIL, SEED_PASSWORD)
        yield
    store_session.reset_app_engine()


@pytest.fixture(autouse=True)
def _reset_login_limiter() -> Iterator[None]:
    """The 10/minute login limiter is process-wide state; without this, the
    test that deliberately trips it would 429 every login after it."""
    yield
    _login_hits.clear()


class TenantClient:
    """One tenant, one user, one logged-in session.

    Sends the token explicitly on every request rather than relying on the
    client's cookie jar: the session cookie is Secure, and httpx will not
    store a Secure cookie received over the TestClient's http:// base URL.
    """

    def __init__(self) -> None:
        suffix = uuid.uuid4().hex[:8]
        self.tenant_id = create_tenant(f"tenant-{suffix}")
        self.email = f"user-{suffix}@example.test"
        create_user(self.tenant_id, self.email, SEED_PASSWORD)
        response = client.post("/api/auth/login",
                               json={"email": self.email, "password": SEED_PASSWORD})
        assert response.status_code == 200, response.text
        self.token = response.json()["token"]

    def get(self, path: str) -> Response:
        return cast(Response, client.get(path, cookies={"session": self.token}))

    def post(self, path: str) -> Response:
        return cast(Response, client.post(path, cookies={"session": self.token}))

    def create_document(self) -> str:
        return seed_resource(self.tenant_id)

    def seed_exception(self) -> str:
        document_id = uuid.uuid4().hex
        exc_id = uuid.uuid4().hex
        with store_session.app_engine().begin() as conn:
            conn.execute(documents.insert().values(
                id=document_id, tenant_id=self.tenant_id, blob_id=None,
                received_at=datetime.now(UTC), sensitivity=Sensitivity.SYNTHETIC.value,
                origin=Origin.CORPUS.value, provenance="tests/api/conftest.py",
                filename="fixture.pdf"))
            conn.execute(findings.insert().values(
                id=exc_id, tenant_id=self.tenant_id, document_id=document_id,
                rule_id="A2", field="line[000].total_completed_and_stored",
                message="previous + this period + stored does not equal the total",
                expected="1000.00", actual="1005.00", page=0,
                x0=0.0, top=0.0, x1=10.0, bottom=10.0,
                state="open", created_at=datetime.now(UTC)))
        return exc_id

    def seed_postable_document(self) -> str:
        """A document with a complete claim set (ready to post) and no open
        exceptions — findings all 'accepted', matching what the review queue
        looks like once a reviewer clears it."""
        import ledger_sense.writeback.assemble as _assemble_mod  # local import to avoid a conftest-wide dependency
        document_id = uuid.uuid4().hex
        with store_session.app_engine().begin() as conn:
            conn.execute(documents.insert().values(
                id=document_id, tenant_id=self.tenant_id, blob_id=None,
                received_at=datetime.now(UTC), sensitivity=Sensitivity.SYNTHETIC.value,
                origin=Origin.CORPUS.value, provenance="tests/api/conftest.py",
                filename="fixture.pdf", content_sha256="abc123"))
            conn.execute(findings.insert().values(
                id=uuid.uuid4().hex, tenant_id=self.tenant_id, document_id=document_id,
                rule_id="A2", field="line[000].total_completed_and_stored",
                message="cleared", expected=None, actual=None, page=None,
                x0=None, top=None, x1=None, bottom=None,
                state="accepted", created_at=datetime.now(UTC)))
            rows = [
                ("contract_id", "C-1", "str"), ("application_no", "2", "int"),
                ("period_to", "2026-08-31", "date"),
                ("total_completed_and_stored", "1000.00", "money"),
                ("retainage_total", "50.00", "money"),
                ("previous_payments", "0.00", "money"),
                ("change_orders_net", "0.00", "money"),
                ("line[000].description", "Sitework", "str"),
                ("line[000].this_period", "500.00", "money"),
            ]
            conn.execute(claims.insert(), [{
                "id": uuid.uuid4().hex, "tenant_id": self.tenant_id,
                "document_id": document_id, "field": field, "value": value,
                "value_type": value_type, "method": "deterministic",
                "confidence": None, "match_quality": 1.0, "page": 0,
                "x0": 0.0, "top": 0.0, "x1": 1.0, "bottom": 1.0,
                "caveat": None, "observed_at": datetime.now(UTC),
            } for field, value, value_type in rows])
        return document_id

    def seed_unrun_document(self) -> str:
        """A real documents row with no findings and no claims — the
        'never run' case _require_cleared (Task 9) must refuse with 422,
        distinct from create_document()'s stub-table row, which owned()
        can't even see."""
        document_id = uuid.uuid4().hex
        with store_session.app_engine().begin() as conn:
            conn.execute(documents.insert().values(
                id=document_id, tenant_id=self.tenant_id, blob_id=None,
                received_at=datetime.now(UTC), sensitivity=Sensitivity.SYNTHETIC.value,
                origin=Origin.CORPUS.value, provenance="tests/api/conftest.py",
                filename="fixture.pdf", content_sha256=None))
        return document_id


@pytest.fixture
def two_tenants() -> tuple[TenantClient, TenantClient]:
    return TenantClient(), TenantClient()
