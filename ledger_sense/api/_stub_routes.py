"""STUB — replaced by the real router when Phase 2/4/6 lands; exists only to
prove every route enforces require_session + tenant ownership.

Nothing here is business logic and nothing here should grow any. The security
property this task ships — "no route without a session and a tenant" — is only
testable against routes that exist: with nothing mounted under /api/inbox,
FastAPI answers an anonymous GET with 404 (no such route) long before
require_session runs, and the test proving the boundary would pass for the
wrong reason. Same for owned(): 404-not-403 needs a real handler that looks a
resource up and hands its tenant_id to owned().

So each of the six prefixes gets a trivial placeholder, mounted through
register() like everything else, and `stub_resources` holds the bare
(id, tenant_id, sensitivity) rows the three ownership-checked handlers read.
Documents, exceptions and their real tables belong to the extraction and
review-queue phases.

**Replace, do not add alongside**: when the real /api/review router lands,
delete this file's review entries — and delete the file outright once the last
prefix is real. `stub_resources` goes with it; it is created by the test
fixtures, never by a migration, so nothing in a client's database depends on
it.
"""

import uuid
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends

from ledger_sense.api.deps import owned, require_session
from ledger_sense.auth.session import Session
from ledger_sense.store.session import app_engine

STUB_METADATA = sa.MetaData()

stub_resources = sa.Table(
    "stub_resources",
    STUB_METADATA,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("tenant_id", sa.Text, nullable=False),
    # Nullable, and null for a row seeded by an ownership fixture that says
    # nothing about provenance. api/fixtures.py writes "SYNTHETIC" here so the
    # demo profile can prove, through the API rather than by assertion, that
    # nothing it serves is a client document. The real column belongs to the
    # documents table the extraction phase brings; this one dies with the file.
    sa.Column("sensitivity", sa.Text, nullable=True),
    sa.Index("ix_stub_resources_tenant_id_id", "tenant_id", "id"),
)

PREFIXES = ("/api/baseline", "/api/review", "/api/reconciliation",
            "/api/inbox", "/api/vendors", "/api/metrics")


def seed_resource(tenant_id: str) -> str:
    """Insert one owned row and return its id (test fixtures only)."""
    resource_id = uuid.uuid4().hex
    with app_engine().begin() as conn:
        conn.execute(stub_resources.insert().values(id=resource_id, tenant_id=tenant_id))
    return resource_id


def _row_of(resource_id: str) -> sa.Row[tuple[str, str | None]] | None:
    with app_engine().connect() as conn:
        return conn.execute(
            sa.select(stub_resources.c.tenant_id, stub_resources.c.sensitivity)
            .where(stub_resources.c.id == resource_id)
        ).one_or_none()


def _owner_of(resource_id: str) -> str | None:
    row = _row_of(resource_id)
    return None if row is None else str(row.tenant_id)


def _index() -> dict[str, bool]:
    return {"stub": True}


# register() already applies require_session to every route it mounts; asking
# for it again here is free (FastAPI caches a dependency per request) and is
# how the handler gets the tenant it must check ownership against.
CurrentSession = Annotated[Session, Depends(require_session)]


def _read_document(doc_id: str, session: CurrentSession) -> dict[str, str]:
    owned(session, _owner_of(doc_id))
    return {"id": doc_id}


def _accept_exception(exc_id: str, session: CurrentSession) -> dict[str, str]:
    owned(session, _owner_of(exc_id))
    return {"id": exc_id, "status": "accepted"}


def _read_inbox_document(doc_id: str, session: CurrentSession) -> dict[str, str | None]:
    """Same ownership contract as _read_document, plus the provenance label.

    Guarded exactly like every other stub route — register() applies
    require_session, owned() answers 404 for someone else's row. The demo
    seeder gets in here on the session its own response set, not on an
    exemption; see api/fixtures.py.
    """
    row = _row_of(doc_id)
    # owned() has already raised 404 by the time row is None; the re-test is
    # for the type checker, not a second code path.
    owned(session, None if row is None else str(row.tenant_id))
    label = None if row is None else row.sensitivity
    return {"id": doc_id, "sensitivity": None if label is None else str(label)}


def stub_routers() -> list[APIRouter]:
    routers = []
    for prefix in PREFIXES:
        router = APIRouter(prefix=prefix, tags=["stub"])
        router.add_api_route("/", _index, methods=["GET"])
        if prefix == "/api/reconciliation":
            router.add_api_route("/{doc_id}", _read_document, methods=["GET"])
        if prefix == "/api/inbox":
            router.add_api_route("/{doc_id}", _read_inbox_document, methods=["GET"])
        if prefix == "/api/review":
            router.add_api_route("/exceptions/{exc_id}/accept", _accept_exception,
                                 methods=["POST"])
        routers.append(router)
    return routers
