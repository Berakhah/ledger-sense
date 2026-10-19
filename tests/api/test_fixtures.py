import os
import threading
from typing import cast

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from ledger_sense.api._stub_routes import STUB_METADATA
from ledger_sense.api.fixtures import PRIOR_BILL, ensure_stub_tables
from ledger_sense.store import session as store_session
from ledger_sense.store.retention import claims, documents


def _client(monkeypatch: pytest.MonkeyPatch, profile: str) -> TestClient:
    monkeypatch.setenv("LEDGER_SENSE_PROFILE", profile)
    from ledger_sense.api.app import build_app
    return TestClient(build_app())


def test_the_seed_route_exists_under_the_demo_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, "demo")
    assert client.post("/api/fixtures/seed",
                       json={"doc_id": "doc-50", "exceptions": 50}).status_code == 201


def test_the_seed_route_does_not_exist_under_the_client_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A route that mints documents must not exist where real documents live."""
    client = _client(monkeypatch, "client")
    assert client.post("/api/fixtures/seed", json={"doc_id": "x"}).status_code == 404


def test_seeded_documents_are_labelled_synthetic(monkeypatch: pytest.MonkeyPatch) -> None:
    """F-49 depends on this: a synthetic session must never produce field
    labels. Read back through the real inbox route, off the real documents
    table — the seeder stores through the same door as an upload."""
    client = _client(monkeypatch, "demo")
    body = client.post("/api/fixtures/seed", json={"doc_id": "doc-3", "exceptions": 3}).json()
    row = client.get(f"/api/inbox/{body['doc_id']}").json()
    assert (row["sensitivity"], row["origin"]) == ("SYNTHETIC", "CORPUS")
    assert row["filename"] == "doc-3.pdf"


def test_the_seeded_row_records_who_seeded_it(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, "demo")
    body = client.post("/api/fixtures/seed", json={"doc_id": "doc-4", "exceptions": 0}).json()
    with store_session.app_engine().connect() as conn:
        provenance = conn.execute(sa.select(documents.c.provenance)
                                  .where(documents.c.id == body["doc_id"])).scalar_one()
    assert provenance.startswith(f"corpus://seed/{body['tenant_id']}/")


# ---- controls on the demo hatch itself ----

def test_an_unset_profile_is_the_client_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default-deny. A typo in an env file must not open the hatch."""
    monkeypatch.delenv("LEDGER_SENSE_PROFILE", raising=False)
    from ledger_sense.api.app import build_app
    assert TestClient(build_app()).post(
        "/api/fixtures/seed", json={"doc_id": "x"}).status_code == 404


def test_a_seeded_document_is_still_refused_to_an_anonymous_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seed route's cookie is the ONLY thing the relaxation buys.

    Without it, /api/inbox/{doc_id} answers 401 exactly as it does for every
    other route: the session boundary is not special-cased for demo rows.
    """
    seeder = _client(monkeypatch, "demo")
    doc_id = seeder.post("/api/fixtures/seed",
                         json={"doc_id": "doc-9", "exceptions": 1}).json()["doc_id"]
    from ledger_sense.api.app import build_app
    anonymous = TestClient(build_app())
    assert anonymous.get(f"/api/inbox/{doc_id}").status_code == 401


# ---- the optional vendor scenario (I2 in front of Playwright) ----

def _exceptions(client: TestClient, doc_id: str) -> list[dict[str, object]]:
    return cast(list[dict[str, object]],
                client.get(f"/api/review/documents/{doc_id}/exceptions").json())


def test_without_a_vendor_the_seed_mints_no_vendor_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The existing specs depend on this: omitting `vendor` changes nothing."""
    client = _client(monkeypatch, "demo")
    body = client.post("/api/fixtures/seed", json={"doc_id": "doc-nv", "exceptions": 2}).json()
    assert [e["rule_id"] for e in _exceptions(client, body["doc_id"])] == ["A2", "A2"]
    with store_session.app_engine().connect() as conn:
        assert conn.execute(sa.select(claims.c.id)
                            .where(claims.c.document_id == body["doc_id"])).all() == []


def test_a_vendor_scenario_adds_one_i2_after_the_arithmetic_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, "demo")
    body = client.post("/api/fixtures/seed", json={
        "doc_id": "doc-v1", "exceptions": 2,
        "vendor": {"observed_name": "Keystone Framing LLC"}}).json()
    assert body["exceptions"] == 3
    listed = _exceptions(client, body["doc_id"])
    assert [e["rule_id"] for e in listed] == ["A2", "A2", "I2"]
    assert listed[-1]["field"] == "vendor_name"

    match = client.get(f"/api/review/exceptions/{listed[-1]['id']}/vendor-match").json()
    assert match == {"observed_name": "Keystone Framing LLC", "candidate": None}
    # Still synthetic end to end: the document the I2 hangs off is CORPUS.
    row = client.get(f"/api/inbox/{body['doc_id']}").json()
    assert row["sensitivity"] == "SYNTHETIC"


def test_a_known_vendor_is_offered_as_the_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, "demo")
    body = client.post("/api/fixtures/seed", json={
        "doc_id": "doc-v2", "exceptions": 0,
        "vendor": {"observed_name": "Acme Steel Company",
                   "known_vendor": "Acme Steel Co."}}).json()
    [i2] = _exceptions(client, body["doc_id"])
    match = client.get(f"/api/review/exceptions/{i2['id']}/vendor-match").json()
    assert match["candidate"]["name"] == "Acme Steel Co."
    assert match["candidate"]["vendor_id"] == body["known_vendor_id"]


def test_undoing_a_link_to_the_known_vendor_keeps_the_vendor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-seeded vendor's evidence is a prior bill, not this document, so
    undo removes only this document's alias. Without that separate alias undo
    would find the vendor evidence-free and delete it."""
    client = _client(monkeypatch, "demo")
    body = client.post("/api/fixtures/seed", json={
        "doc_id": "doc-v3", "exceptions": 0,
        "vendor": {"observed_name": "Acme Steel Company",
                   "known_vendor": "Acme Steel Co."}}).json()
    [i2] = _exceptions(client, body["doc_id"])
    vendor_id = body["known_vendor_id"]

    assert client.post(f"/api/review/exceptions/{i2['id']}/confirm-vendor",
                       json={"vendor_id": vendor_id}).status_code == 200
    assert client.post(f"/api/review/exceptions/{i2['id']}/undo-confirm-vendor").status_code == 200

    ledger = client.get(f"/api/vendors/{vendor_id}/ledger")
    assert ledger.status_code == 200
    assert [a["source_document"] for a in ledger.json()["aliases"]] == [PRIOR_BILL]


def test_an_observed_name_that_needs_no_confirmation_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exact match raises no I2, so there is nothing to seed — say so
    rather than hand back a document with no vendor finding on it."""
    client = _client(monkeypatch, "demo")
    response = client.post("/api/fixtures/seed", json={
        "doc_id": "doc-v4", "exceptions": 0,
        "vendor": {"observed_name": "Acme Steel Co.", "known_vendor": "Acme Steel Co."}})
    assert response.status_code == 422


@pytest.mark.skipif(
    not os.environ.get("LEDGER_SENSE_LIVE_POSTGRES_URL"),
    reason="set LEDGER_SENSE_LIVE_POSTGRES_URL to validate against a live Postgres",
)
def test_concurrent_first_seeds_on_a_fresh_database_do_not_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Playwright's parallel workers all seed at once against a fresh demo
    stack. Each create_all(checkfirst=True) sees no stub_resources table and
    issues CREATE TABLE; every loser died on pg_type's unique index with a
    500. Only a real Postgres shows it — SQLite serialises writers anyway."""
    monkeypatch.setenv("LEDGER_SENSE_DB_URL", os.environ["LEDGER_SENSE_LIVE_POSTGRES_URL"])
    store_session.reset_app_engine()
    try:
        engine = store_session.app_engine()
        STUB_METADATA.drop_all(engine, checkfirst=True)
        start = threading.Barrier(4)
        errors: list[BaseException] = []

        def ensure() -> None:
            try:
                start.wait(timeout=5)
                ensure_stub_tables(engine)
            except BaseException as exc:  # noqa: BLE001 - surfaced via `errors`
                errors.append(exc)

        threads = [threading.Thread(target=ensure) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        assert errors == []
        assert sa.inspect(engine).has_table("stub_resources")
    finally:
        store_session.reset_app_engine()


@pytest.mark.parametrize("name", ["", " leading-space", "<script>", "x" * 81])
def test_vendor_names_are_pattern_bounded(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    client = _client(monkeypatch, "demo")
    for vendor in ({"observed_name": name}, {"observed_name": "Acme", "known_vendor": name}):
        response = client.post("/api/fixtures/seed",
                               json={"doc_id": "doc-v5", "vendor": vendor})
        assert response.status_code == 422, vendor


def test_a_bank_seed_produces_a_real_i2_and_i3(monkeypatch: pytest.MonkeyPatch) -> None:
    from ledger_sense.corpus.remit import remit_for

    client = _client(monkeypatch, "demo")
    body = client.post("/api/fixtures/seed", json={
        "doc_id": "bank-1", "bank": {"seed": 30, "variant": "base"}}).json()
    rows = client.get(f"/api/review/documents/{body['doc_id']}/exceptions").json()
    by_rule = {r["rule_id"]: r for r in rows}
    assert {"I2", "I3"} <= set(by_rule)
    assert by_rule["I3"]["actual"] == f"....{remit_for(30).account[-4:]}"
    assert by_rule["I3"]["state"] == "open"


def test_a_changed_bank_seed_raises_only_a_change_against_a_confirmed_baseline(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from ledger_sense.corpus.remit import remit_for

    client = _client(monkeypatch, "demo")
    body = client.post("/api/fixtures/seed", json={
        "doc_id": "bank-2", "bank": {"seed": 31, "variant": "changed"}}).json()
    rows = client.get(f"/api/review/documents/{body['doc_id']}/exceptions").json()
    assert "I2" not in {r["rule_id"] for r in rows}
    (i3,) = [r for r in rows if r["rule_id"] == "I3"]
    assert i3["expected"] == f"....{remit_for(31).account[-4:]}"
    assert i3["actual"] == f"....{remit_for(31, 'changed').account[-4:]}"
    view = client.get(f"/api/review/exceptions/{i3['id']}/bank-change").json()
    assert view["kind"] == "changed"


def test_a_bank_seed_cannot_be_combined_with_synthetic_exceptions_or_a_vendor(
        monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, "demo")
    bank = {"seed": 32, "variant": "base"}
    assert client.post("/api/fixtures/seed", json={
        "doc_id": "bank-3", "exceptions": 2, "bank": bank}).status_code == 422
    assert client.post("/api/fixtures/seed", json={
        "doc_id": "bank-4", "bank": bank,
        "vendor": {"observed_name": "Keystone Framing LLC"}}).status_code == 422


def test_concurrent_seeds_hash_one_password_at_a_time(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The measured cause of the Playwright seed timeouts: `create_user` hashes
    the (thrown-away) password with Argon2's defaults - 64 MiB and several
    threads each. Eleven concurrent plain seeds took ~30 s each in the demo
    container (1 CPU, 1 GiB) against 0.3 s idle, with no pipeline involved.
    Hashing one at a time keeps each seed fast and memory bounded."""
    import time

    from fastapi import Response

    from ledger_sense.api import fixtures

    active = 0
    peak = 0
    guard = threading.Lock()
    real = fixtures.create_user

    def spy(*args: object, **kwargs: object) -> object:
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.05)  # widen the window an unserialised overlap needs
            return real(*args, **kwargs)  # type: ignore[arg-type]
        finally:
            with guard:
                active -= 1

    monkeypatch.setattr(fixtures, "create_user", spy)
    ensure_stub_tables(store_session.app_engine())
    errors: list[BaseException] = []

    def seed_one(n: int) -> None:
        try:
            fixtures.seed(fixtures.SeedRequest(doc_id=f"hash-{n}", exceptions=1), Response())
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assert below
            errors.append(exc)

    threads = [threading.Thread(target=seed_one, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert peak == 1


def test_the_bank_scenario_does_not_exist_under_the_client_profile(
        monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, "client")
    assert client.post("/api/fixtures/seed", json={
        "doc_id": "bank-5", "bank": {"seed": 33, "variant": "base"}}).status_code == 404
