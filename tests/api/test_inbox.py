"""POST /api/inbox is the labelled door with an HTTP front. It composes
intake → accept_document → store_document and adds nothing of its own."""

import ast
import pathlib
from typing import cast

import pypdfium2 as pdfium
import pytest
import sqlalchemy as sa
from httpx import Response

from ledger_sense.store import session as store_session
from ledger_sense.store.retention import documents
from tests.api.conftest import TenantClient, client


def _pdf(pages: int = 1) -> bytes:
    doc = pdfium.PdfDocument.new()
    for _ in range(pages):
        doc.new_page(612, 792)
    out = pathlib.Path(__file__).parent / f"_inbox_{pages}.pdf"
    doc.save(out)
    payload = out.read_bytes()
    out.unlink()
    return payload


def _upload(tenant: TenantClient, payload: bytes, filename: str = "payapp.pdf",
            origin: str | None = None) -> Response:
    data = {} if origin is None else {"origin": origin}
    return cast(Response, client.post(
        "/api/inbox", files={"file": (filename, payload, "application/pdf")},
        data=data, cookies={"session": tenant.token}))


def _row(document_id: str) -> sa.Row[tuple[object, ...]]:
    with store_session.app_engine().connect() as conn:
        return conn.execute(sa.select(documents).where(documents.c.id == document_id)).one()


def test_an_upload_is_stored_confidential_from_an_upload(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    response = _upload(a, _pdf())
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"id", "filename", "sensitivity", "origin", "received_at"}
    assert (body["sensitivity"], body["origin"], body["filename"]) == (
        "CLIENT_CONFIDENTIAL", "UPLOAD", "payapp.pdf")
    row = _row(body["id"])
    assert row.tenant_id == a.tenant_id
    assert row.provenance.startswith(f"upload://{a.tenant_id}/")
    assert row.provenance.count("/") == 3 and "payapp" not in row.provenance


def test_a_forgotten_origin_is_the_most_restrictive_label(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    assert _upload(a, _pdf(), origin=None).json()["sensitivity"] == "CLIENT_CONFIDENTIAL"


def test_corpus_origin_is_refused_outside_the_demo_profile(
    two_tenants: tuple[TenantClient, TenantClient], monkeypatch: pytest.MonkeyPatch,
) -> None:
    a, _ = two_tenants
    monkeypatch.delenv("LEDGER_SENSE_PROFILE", raising=False)
    response = _upload(a, _pdf(), origin="CORPUS")
    assert response.status_code == 422
    assert "demo" in response.json()["detail"]


def test_corpus_origin_is_accepted_under_the_demo_profile(
    two_tenants: tuple[TenantClient, TenantClient], monkeypatch: pytest.MonkeyPatch,
) -> None:
    a, _ = two_tenants
    monkeypatch.setenv("LEDGER_SENSE_PROFILE", "demo")
    body = _upload(a, _pdf(), origin="CORPUS").json()
    assert (body["sensitivity"], body["origin"]) == ("SYNTHETIC", "CORPUS")
    assert _row(body["id"]).provenance.startswith(f"corpus://{a.tenant_id}/")


def test_redacted_upload_is_refused_in_plain_words(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    response = _upload(a, _pdf(), origin="REDACTED_UPLOAD")
    assert response.status_code == 422
    assert "redaction" in response.json()["detail"].lower()


def test_an_unknown_origin_is_a_schema_error(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    assert _upload(a, _pdf(), origin="EMAIL").status_code == 422


def test_a_missing_or_empty_file_is_refused(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    assert client.post("/api/inbox", cookies={"session": a.token}).status_code == 422
    assert _upload(a, b"").status_code == 422


def test_an_oversized_payload_is_413_and_nothing_is_stored(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    before = _count(a.tenant_id)
    response = _upload(a, b"%PDF-1.4" + b"\x00" * 26_000_000)
    assert response.status_code == 413
    assert "size" in response.json()["detail"]
    assert _count(a.tenant_id) == before


def test_a_non_pdf_is_422_and_nothing_is_stored(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    before = _count(a.tenant_id)
    response = _upload(a, b"MZ\x90\x00", filename="payapp.pdf")
    assert response.status_code == 422
    assert "not a PDF" in response.json()["detail"]
    assert _count(a.tenant_id) == before


def test_a_page_bomb_is_422(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    bomb = pathlib.Path("tests/ingest/fixtures/malformed/many_pages.pdf").read_bytes()
    response = _upload(a, bomb)
    assert response.status_code == 422 and "page" in response.json()["detail"]


def _upload_raw_multipart(tenant: TenantClient, filename: str, payload: bytes) -> Response:
    """A hand-built multipart body carrying a `filename=""` attribute.

    httpx's `files=` shorthand (`FileField.render_headers`, `if self.filename:`)
    drops the `filename` parameter from Content-Disposition entirely when it is
    falsy, rather than sending it empty — so `_upload(..., filename="")` never
    reaches the route as a file part at all (Starlette parses it as a plain
    text field and FastAPI's own `UploadFile` validation 422s first). A real
    browser submitting an empty file input does send the attribute, just
    empty (WHATWG's multipart/form-data algorithm), so this builds the wire
    format by hand to exercise that real case instead of httpx's shortcut.
    """
    boundary = "ledgersenseboundary"
    body = (
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f'Content-Type: application/pdf\r\n\r\n'
    ).encode() + payload + f'\r\n--{boundary}--\r\n'.encode()
    return cast(Response, client.post(
        "/api/inbox", content=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        cookies={"session": tenant.token}))


def test_the_filename_is_a_basename_with_no_path_or_control_characters(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    assert _upload(a, _pdf(), filename="../../etc/passwd").json()["filename"] == "passwd"
    assert _upload(a, _pdf(), filename="C:\\Users\\x\\pay\x00app.pdf").json()["filename"] == "payapp.pdf"
    assert _upload_raw_multipart(a, "", _pdf()).json()["filename"] == "document.pdf"
    assert len(_upload(a, _pdf(), filename="a" * 400 + ".pdf").json()["filename"]) == 255


def test_an_anonymous_upload_is_refused() -> None:
    assert client.post("/api/inbox", files={"file": ("x.pdf", _pdf())}).status_code == 401


def test_every_upload_passes_through_the_intake_door() -> None:
    """The Task 43 guard, kept: the handler calls accept_document. The
    structural tests already forbid building a Labelled any other way."""
    src = pathlib.Path("ledger_sense/api/inbox.py").read_text(encoding="utf-8")
    called = {n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
              for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)}
    assert {"accept_document", "intake", "store_document"} <= called


def _count(tenant_id: str) -> int:
    with store_session.app_engine().connect() as conn:
        return conn.execute(sa.select(sa.func.count()).select_from(documents)
                            .where(documents.c.tenant_id == tenant_id)).scalar_one()
