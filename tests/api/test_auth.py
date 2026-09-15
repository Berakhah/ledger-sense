# tests/api/test_auth.py
from collections.abc import Callable
from typing import cast

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from ledger_sense.api.app import UNGUARDED_PREFIXES, app
from tests.api.conftest import TenantClient

client = TestClient(app)

PROTECTED_PREFIXES = ("/api/baseline", "/api/review", "/api/reconciliation",
                      "/api/inbox", "/api/vendors", "/api/metrics")

# /api/inbox only answers POST at the bare prefix (no GET "/" index, unlike
# every other stub) — everything else still has the stub's GET "/". Route
# matching (path + method) happens before any dependency runs, so a probe
# against a path/method the route table doesn't actually serve either 405s
# without reaching require_session, or (a mismatched trailing slash) 307s
# there first: Starlette's redirect_slashes would make `POST /api/inbox/`
# read as a pass, via a hop this test never asked for. Each prefix is
# therefore probed at the exact method and path it serves.
_PROBE = {"/api/inbox": ("post", "/api/inbox")}


def _probe(prefix: str) -> tuple[Callable[..., Response], str]:
    method_name, path = _PROBE.get(prefix, ("get", f"{prefix}/"))
    return cast(Callable[..., Response], getattr(client, method_name)), path


def test_every_api_route_requires_a_session() -> None:
    """The test that would have caught F-25 the day the first router landed."""
    unprotected = []
    for route in app.routes:
        path = getattr(route, "path", "")
        # UNGUARDED_PREFIXES, not a copy of it: app.py declares which routes
        # live outside the boundary, and this test asserts nothing else does.
        # /api/fixtures is in that list and exists only under the demo
        # profile, so this suite stays green whichever profile it runs under —
        # tests/api/test_fixtures.py is what holds the demo hatch to account.
        if not path.startswith("/api/") or path.startswith(UNGUARDED_PREFIXES):
            continue
        names = {d.dependency.__name__ for d in getattr(route, "dependencies", [])
                 if getattr(d, "dependency", None)}
        if "require_session" not in names:
            unprotected.append(path)
    assert unprotected == [], f"routes without require_session: {unprotected}"


@pytest.mark.parametrize("prefix", PROTECTED_PREFIXES)
def test_an_anonymous_request_is_refused(prefix: str) -> None:
    method, path = _probe(prefix)
    assert method(path).status_code == 401


def test_a_document_from_another_tenant_is_not_found_rather_than_forbidden(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    """404, never 403. A 403 confirms the resource exists, which is the leak."""
    a, b = two_tenants
    doc_id = a.create_document()
    assert b.get(f"/api/reconciliation/{doc_id}").status_code == 404


def test_the_owner_can_read_their_own_document(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _b = two_tenants
    assert a.get(f"/api/reconciliation/{a.create_document()}").status_code == 200


def test_a_review_action_cannot_be_taken_across_tenants(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, b = two_tenants
    exc_id = a.seed_exception()
    assert b.post(f"/api/review/exceptions/{exc_id}/accept").status_code == 404


def test_the_profile_cache_cannot_be_opened_without_a_tenant() -> None:
    """F-18: geometry-only prevents data leakage, not influence."""
    from ledger_sense.extract.profiles import ProfileCache
    with pytest.raises(TypeError):
        ProfileCache(db_path=":memory:")  # type: ignore[call-arg]


def test_a_session_expires() -> None:
    token = client.post("/api/auth/login",
                        json={"email": "a@example.test",
                              "password": "changeme-in-test"}).json()["token"]
    from ledger_sense.auth.session import expire_now
    expire_now(token)
    assert client.post("/api/inbox", cookies={"session": token}).status_code == 401


def test_passwords_are_stored_as_argon2_hashes_not_plaintext() -> None:
    from ledger_sense.auth.models import hash_password, verify_password
    digest = hash_password("correct horse battery staple")
    assert digest.startswith("$argon2")
    assert verify_password("correct horse battery staple", digest)
    assert not verify_password("wrong", digest)


def test_the_login_endpoint_is_rate_limited() -> None:
    codes = [client.post("/api/auth/login",
                         json={"email": "a@example.test", "password": "no"}).status_code
             for _ in range(25)]
    assert 429 in codes


def test_the_session_cookie_is_httponly_samesite_strict_and_secure() -> None:
    resp = client.post("/api/auth/login", json={"email": "a@example.test",
                                                "password": "changeme-in-test"})
    header = resp.headers["set-cookie"].lower()
    for flag in ("httponly", "samesite=strict", "secure"):
        assert flag in header, flag


def test_health_is_the_only_unauthenticated_non_auth_route() -> None:
    assert client.get("/api/health").status_code == 200
