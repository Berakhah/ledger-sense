import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from ledger_sense.store import session as store_session
from ledger_sense.store.retention import documents


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
