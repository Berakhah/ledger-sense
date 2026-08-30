import pytest
from fastapi.testclient import TestClient


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
    """F-49 depends on this: a synthetic session must never produce field labels."""
    client = _client(monkeypatch, "demo")
    doc_id = client.post("/api/fixtures/seed",
                         json={"doc_id": "doc-3", "exceptions": 3}).json()["doc_id"]
    assert client.get(f"/api/inbox/{doc_id}").json()["sensitivity"] == "SYNTHETIC"


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
