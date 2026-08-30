"""The application object.

Three routes live outside the session boundary and only three, all of them
mounted by hand in build_app() below so the whole exception list is one
function you can read top to bottom:

  GET  /api/health        — a container orchestrator has to reach it before
                            anyone can log in.
  POST /api/auth/login    — how a session comes to exist.
  POST /api/fixtures/seed — mounted ONLY when LEDGER_SENSE_PROFILE == "demo";
                            absent, not merely guarded, under every other
                            value including unset. See api/fixtures.py.

Everything else is mounted through deps.register(), which applies
require_session — see tests/api/test_auth.py::test_every_api_route_requires_a_session,
which walks the route table and fails on anything mounted another way. The
three above are mounted with add_api_route rather than include_router so they
stay visible to that walk instead of hiding from the audit that exempts them.
"""

import threading
import time
from collections import defaultdict, deque

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from ledger_sense.api._stub_routes import stub_routers
from ledger_sense.api.deps import register
from ledger_sense.api.fixtures import SEED_PATH, demo_profile_enabled, seed
from ledger_sense.auth.models import authenticate
from ledger_sense.auth.session import SESSION_TTL, issue_session

LOGIN_LIMIT = 10
LOGIN_WINDOW_SECONDS = 60.0

# The three exceptions named in the module docstring, as data, because two
# separate audit suites assert against them (tests/api/test_auth.py and
# tests/api/test_route_registration.py) and a third hand-written copy is how
# F-55 happened. It lives here rather than in a test because it is this
# module's claim about itself: adding a fourth entry is a change to the
# session boundary and should read like one in the diff.
UNGUARDED_PREFIXES = ("/api/health", "/api/auth", "/api/fixtures")

# ponytail: in-process, per-worker. Two uvicorn workers means two counters and
# an effective 20/minute; a shared counter (Redis, or a sessions-table row)
# is the upgrade when the deployment stops being one container. It is a
# brute-force brake, not an access control — the ownership check is.
_login_hits: dict[str, deque[float]] = defaultdict(deque)
_login_lock = threading.Lock()


class LoginRequest(BaseModel):
    # Plain str, not EmailStr: pydantic's email validation needs a dependency
    # (email-validator) to reject strings the lookup rejects anyway.
    email: str
    password: str


def _rate_limited(client: str) -> bool:
    now = time.monotonic()
    with _login_lock:
        hits = _login_hits[client]
        while hits and now - hits[0] > LOGIN_WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= LOGIN_LIMIT:
            return True
        hits.append(now)
        return False


def build_app() -> FastAPI:
    app = FastAPI(title="Ledger Sense")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/auth/login")
    def login(body: LoginRequest, request: Request, response: Response) -> dict[str, str]:
        if _rate_limited(request.client.host if request.client else "unknown"):
            raise HTTPException(status_code=429, detail="too many attempts")
        identity = authenticate(body.email, body.password)
        if identity is None:
            # One message for both "no such user" and "wrong password":
            # anything narrower enumerates accounts.
            raise HTTPException(status_code=401, detail="invalid credentials")
        user_id, tenant_id = identity
        token = issue_session(user_id, tenant_id)
        response.set_cookie(
            "session",
            token,
            max_age=int(SESSION_TTL.total_seconds()),
            httponly=True,      # not readable from JavaScript
            samesite="strict",  # not sent on a cross-site request: CSRF brake
            secure=True,        # HTTPS only
            path="/",
        )
        # The token also comes back in the body for non-browser clients, which
        # is a real weakening of HttpOnly — anything that can read this
        # response has the session. Browser callers should ignore it and let
        # the cookie do the work.
        return {"token": token}

    # Read at build time, not per request: whether this process is a demo
    # cannot change under it, and a per-request read would let an env change
    # open the hatch on a running client deployment.
    if demo_profile_enabled():
        app.add_api_route(SEED_PATH, seed, methods=["POST"], status_code=201,
                          tags=["fixtures"])

    for router in stub_routers():
        register(app, router)

    return app


app = build_app()
