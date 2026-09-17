"""STUB — replaced by the real router when Phase 2/4/6 lands; exists only to
prove every route enforces require_session + tenant ownership.

Nothing here is business logic and nothing here should grow any. The security
property this task ships — "no route without a session and a tenant" — is only
testable against routes that exist: with nothing mounted under /api/inbox,
FastAPI answers an anonymous GET with 404 (no such route) long before
require_session runs, and the test proving the boundary would pass for the
wrong reason. Same for owned(): 404-not-403 needs a real handler that looks a
resource up and hands its tenant_id to owned().

So each of the remaining prefixes gets a trivial placeholder, mounted through
register() like everything else, and `stub_resources` holds the bare
(id, tenant_id, sensitivity) rows the ownership-checked handler reads.
Documents and their real tables belong to the extraction and review-queue
phases.

**/api/review is real** — ledger_sense/api/review.py, registered in
api/app.py — and is no longer among these prefixes. **/api/reconciliation is
real too** (Task 32, ledger_sense/api/reconciliation.py) and was removed the
same way.

**Replace, do not add alongside**: when the next real router lands, delete
this file's entry for it — and delete the file outright once the last prefix
is real. `stub_resources` goes with it; it is created by the test fixtures,
never by a migration, so nothing in a client's database depends on it.
"""

import uuid

import sqlalchemy as sa
from fastapi import APIRouter

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

PREFIXES = ("/api/baseline", "/api/vendors", "/api/metrics")


def seed_resource(tenant_id: str) -> str:
    """Insert one owned row and return its id (test fixtures only)."""
    resource_id = uuid.uuid4().hex
    with app_engine().begin() as conn:
        conn.execute(stub_resources.insert().values(id=resource_id, tenant_id=tenant_id))
    return resource_id


def _index() -> dict[str, bool]:
    return {"stub": True}


def stub_routers() -> list[APIRouter]:
    routers = []
    for prefix in PREFIXES:
        router = APIRouter(prefix=prefix, tags=["stub"])
        router.add_api_route("/", _index, methods=["GET"])
        routers.append(router)
    return routers
