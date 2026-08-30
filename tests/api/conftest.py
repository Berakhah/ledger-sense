"""Fixtures for the API tests.

The database is a SQLite file built by running the real migration
(`alembic upgrade head`) through LEDGER_SENSE_DB_URL, not by create_all: if
0001_tenancy and the Table definitions the code queries ever drift apart,
these tests fail rather than passing against a schema no deployment has.

`two_tenants` hands back two live, logged-in clients — separate tenants,
separate users, separate sessions — because a cross-tenant test is worthless
if either side is faked. What it does NOT do is model documents or review
exceptions: those rows live in `stub_resources` (see api/_stub_routes.py) and
carry nothing but an id and an owner, which is all an ownership check reads.
The real tables arrive with the phases that own them.
"""

import pathlib
import uuid
from collections.abc import Iterator
from typing import cast

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from ledger_sense.api._stub_routes import STUB_METADATA, seed_resource
from ledger_sense.api.app import _login_hits, app
from ledger_sense.auth.models import create_tenant, create_user
from ledger_sense.store import session as store_session

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
        return seed_resource(self.tenant_id)


@pytest.fixture
def two_tenants() -> tuple[TenantClient, TenantClient]:
    return TenantClient(), TenantClient()
