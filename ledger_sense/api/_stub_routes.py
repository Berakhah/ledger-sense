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
(id, tenant_id) rows the two ownership-checked handlers read. Documents,
exceptions and their real tables belong to the extraction and review-queue
phases.

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


def _owner_of(resource_id: str) -> str | None:
    with app_engine().connect() as conn:
        return conn.execute(
            sa.select(stub_resources.c.tenant_id)
            .where(stub_resources.c.id == resource_id)
        ).scalar_one_or_none()


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


def stub_routers() -> list[APIRouter]:
    routers = []
    for prefix in PREFIXES:
        router = APIRouter(prefix=prefix, tags=["stub"])
        router.add_api_route("/", _index, methods=["GET"])
        if prefix == "/api/reconciliation":
            router.add_api_route("/{doc_id}", _read_document, methods=["GET"])
        if prefix == "/api/review":
            router.add_api_route("/exceptions/{exc_id}/accept", _accept_exception,
                                 methods=["POST"])
        routers.append(router)
    return routers
