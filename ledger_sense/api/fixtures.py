"""E2E seeding — mounted under the `demo` profile and nowhere else.

Phase 4's Playwright suite navigates to /review/doc-50 and clears fifty
exceptions. Something has to mint doc-50. That something must not exist in a
deployment holding real pay applications, so app.build_app() mounts this route
only when LEDGER_SENSE_PROFILE == "demo" — an absent route, not a guarded one:
a 403 tells a prober the demo hatch is compiled in and merely switched off.
The default (unset, or any other value) is the `client` profile.

Every row it writes is labelled SYNTHETIC. That label is what /api/inbox/{id}
hands back, so "the demo holds no client data" is a property the API can be
asked about rather than a claim in a README.

## The Secure-flag relaxation, stated plainly

This route's response sets a session cookie **without** Secure, which
/api/auth/login sets and must keep setting. The reason is mechanical: the
seeder is called by a test runner over http:// (TestClient's base URL, and
Playwright's dev server), and http.cookiejar refuses to store a Secure cookie
arriving over a non-secure connection — see the docstring on
tests/api/conftest.py::TenantClient, which works around the same thing by
re-attaching the token by hand on every call. A seed call whose cookie the
caller silently drops is a seeded fixture nobody can read.

Scope of the relaxation, all four fences:

1. It is on this route only. /api/auth/login is untouched and still Secure —
   tests/api/test_auth.py asserts that flag directly.
2. The route only exists under the `demo` profile, so no `client` deployment
   can emit this cookie at all.
3. The session it hands out belongs to a tenant minted seconds earlier that
   owns nothing but the synthetic rows this call just wrote. Intercepting it
   gets you the fixtures you could have asked for anyway.
4. Everything downstream of it stays normal: /api/inbox/{doc_id} is mounted
   through register() with require_session and checks owned() like every
   other route. Nothing about the session boundary is special-cased to let
   the seeder through — it holds a real session, obtained the real way.

The alternative — returning the token in the body for the caller to attach —
is what conftest already does, and it cannot work here: the given test calls
client.get() with no token argument, and rightly, because a fixture endpoint
that leaves the caller unauthenticated is a fixture endpoint that does not
work in a browser either. Playwright's page navigates; it does not hand-attach
cookies.
"""

import os
import uuid
from typing import Annotated

import sqlalchemy as sa
from fastapi import Response
from pydantic import BaseModel, Field

from ledger_sense.api._stub_routes import STUB_METADATA, stub_resources
from ledger_sense.auth.models import create_tenant, create_user
from ledger_sense.auth.session import SESSION_TTL, issue_session
from ledger_sense.store.session import app_engine

SEED_PATH = "/api/fixtures/seed"
DEMO_PROFILE = "demo"
SYNTHETIC = "SYNTHETIC"

# Ids are interpolated into row keys and into the URL Playwright navigates to,
# so they are constrained at the boundary rather than trusted: no separators,
# no LIKE wildcards, bounded length.
DocId = Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")]

# Enough for the fifty-exception keyboard run with headroom; a bound, because
# an unbounded count is an unbounded write from an unauthenticated caller.
MAX_EXCEPTIONS = 500


def demo_profile_enabled() -> bool:
    """True only for an explicit LEDGER_SENSE_PROFILE=demo.

    Unset, empty, misspelt or anything else is the `client` profile. Defaulting
    the other way would put a document-minting route in front of real client
    data on the strength of a typo in an env file.
    """
    return os.environ.get("LEDGER_SENSE_PROFILE") == DEMO_PROFILE


class SeedRequest(BaseModel):
    doc_id: DocId
    exceptions: int = Field(default=0, ge=0, le=MAX_EXCEPTIONS)


def _exception_id(doc_id: str, index: int) -> str:
    # ":exc:" is outside DocId's alphabet, so an exception row's id can never
    # collide with a document id, and the prefix delete below cannot reach
    # another document's rows ("doc-50:exc:" does not prefix "doc-500:exc:").
    return f"{doc_id}:exc:{index}"


def seed(body: SeedRequest, response: Response) -> dict[str, object]:
    """Mint one synthetic document plus `exceptions` synthetic exceptions.

    Idempotent by doc_id: re-seeding replaces the previous rows rather than
    failing on the primary key, so an E2E suite can be run twice.
    """
    # ponytail: unauthenticated and unrate-limited, and each call mints a
    # tenant, a user (one argon2 hash) and a session that are never collected.
    # The ceiling is a demo container with a tmpfs Postgres, so a flood costs
    # a restart and nothing else. Add the login limiter's brake and a sweep of
    # demo-* tenants if the demo is ever put somewhere it can be found.
    # A real `demo` container has run alembic but not the test fixtures, and
    # stub_resources is deliberately not in any migration (it dies with
    # _stub_routes.py). checkfirst makes this a no-op under pytest, where
    # conftest has already created it.
    engine = app_engine()
    STUB_METADATA.create_all(engine, checkfirst=True)

    # A fresh tenant per seed call, following tests/api/conftest.py's
    # collision-safe naming. The password is random and thrown away: this
    # identity is reachable through this route and no other, which is one less
    # way in than a fixed demo credential would be.
    suffix = uuid.uuid4().hex[:8]
    tenant_id = create_tenant(f"demo-{suffix}")
    user_id = create_user(tenant_id, f"demo-{suffix}@example.invalid", uuid.uuid4().hex)

    rows = [{"id": body.doc_id, "tenant_id": tenant_id, "sensitivity": SYNTHETIC}]
    rows += [{"id": _exception_id(body.doc_id, i), "tenant_id": tenant_id,
              "sensitivity": SYNTHETIC} for i in range(body.exceptions)]

    with engine.begin() as conn:
        conn.execute(stub_resources.delete().where(sa.or_(
            stub_resources.c.id == body.doc_id,
            # autoescape: "_" is in DocId's alphabet and is also LIKE's
            # single-character wildcard, so re-seeding "doc_50" would
            # otherwise delete "docX50"'s exception rows too.
            stub_resources.c.id.startswith(f"{body.doc_id}:exc:", autoescape=True),
        )))
        conn.execute(stub_resources.insert(), rows)

    response.set_cookie(
        "session",
        issue_session(user_id, tenant_id),
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="strict",
        # Deliberately not Secure. See this module's docstring for why, and for
        # the four fences that keep it from meaning anything outside the demo.
        secure=False,
        path="/",
    )
    return {"doc_id": body.doc_id, "exceptions": body.exceptions,
            "sensitivity": SYNTHETIC, "tenant_id": tenant_id}
