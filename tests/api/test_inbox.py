"""POST /api/inbox is the labelled door with an HTTP front. It composes
intake → accept_document → store_document and adds nothing of its own."""

import ast
import io
import pathlib
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast

import pypdfium2 as pdfium
import pytest
import sqlalchemy as sa
from httpx import Response

from ledger_sense.api.app import MULTIPART_OVERHEAD_BYTES
from ledger_sense.boundary import Origin
from ledger_sense.corpus.generate import generate
from ledger_sense.domain.vendor import VendorLedger
from ledger_sense.ingest.intake import accept_document
from ledger_sense.ingest.limits import DEFAULT_LIMITS
from ledger_sense.store import session as store_session
from ledger_sense.store.blobs import blob_store_from_environment
from ledger_sense.store.documents import store_document
from ledger_sense.store.retention import documents
from ledger_sense.store.vendors import save_vendor_ledger
from tests.api.conftest import TenantClient, client


def _pdf(pages: int = 1) -> bytes:
    doc = pdfium.PdfDocument.new()
    for _ in range(pages):
        doc.new_page(612, 792)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


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
    assert set(body) == {"id", "filename", "sensitivity", "origin", "received_at",
                          "status", "doc_type", "run_note", "finding_count", "claim_count"}
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


def test_a_declared_oversized_upload_is_refused_before_any_read(
    two_tenants: tuple[TenantClient, TenantClient], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This proves the ROUTE's own `file.size > _LIMITS.max_bytes` guard, not
    the ASGI body-cap middleware (I3) sitting in front of it — a payload
    large enough to trip the middleware's own (wider) cap would 413 there
    first and never reach the route at all, making a patch on the route's
    read path vacuous. The payload is sized to land strictly between the two
    thresholds: one byte over DEFAULT_LIMITS.max_bytes, so the route's own
    check fires, but multipart-framed it stays well under
    DEFAULT_LIMITS.max_bytes + MULTIPART_OVERHEAD_BYTES (64 KiB of slack for
    the boundary and headers httpx's multipart encoder adds), so the
    middleware lets the request through. file.size is checked, and the
    spooled file backing the upload is never read, so the oversized body is
    never pulled into this process as one buffer. The route reads via the
    sync `file.file.read` (I2), not `UploadFile.read`, so the counting patch
    targets the underlying SpooledTemporaryFile Starlette's multipart parser
    spools uploads into."""
    a, _ = two_tenants
    read_calls = 0
    original_read = tempfile.SpooledTemporaryFile.read

    def _counting_read(self: "tempfile.SpooledTemporaryFile[bytes]", n: int = -1) -> bytes:
        nonlocal read_calls
        read_calls += 1
        return original_read(self, n)

    monkeypatch.setattr(tempfile.SpooledTemporaryFile, "read", _counting_read)
    oversized = b"\x00" * (DEFAULT_LIMITS.max_bytes + 1)
    response = _upload(a, oversized)
    assert response.status_code == 413
    # "payload size", not "declared request size": proves the ROUTE's
    # message, not the middleware's, is what answered.
    assert "payload size" in response.json()["detail"]
    assert read_calls == 0


def test_a_declared_content_length_over_the_cap_is_413_before_the_body_is_read(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    """I3: the upstream body cap. A forged Content-Length above the route's
    own cap (plus the fixed multipart-framing allowance) is refused by the
    ASGI middleware before Starlette's multipart parser spools anything —
    proven here by sending a tiny actual body under a huge declared length
    and confirming nothing lands in the table."""
    a, _ = two_tenants
    before = _count(a.tenant_id)
    declared = DEFAULT_LIMITS.max_bytes + MULTIPART_OVERHEAD_BYTES + 1
    response = cast(Response, client.post(
        "/api/inbox", content=b"tiny",
        headers={"content-length": str(declared),
                 "content-type": "multipart/form-data; boundary=x"},
        cookies={"session": a.token}))
    assert response.status_code == 413
    assert _count(a.tenant_id) == before


def test_the_body_cap_middlewares_413_still_carries_cors_headers(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    """The web app is a separate origin calling this API with
    fetch()+credentials, so CORSMiddleware must stay outermost — wrapping
    InboxBodyCapMiddleware's own short-circuited responses too, not just
    responses that reach the route. Without that ordering, a real cross-
    origin oversized upload gets no Access-Control-Allow-Origin header and
    the browser reports an opaque network error instead of surfacing the
    413. WEB_ORIGIN defaults to http://localhost:3000 (see api/app.py) and
    nothing in this test suite overrides it."""
    a, _ = two_tenants
    before = _count(a.tenant_id)
    declared = DEFAULT_LIMITS.max_bytes + MULTIPART_OVERHEAD_BYTES + 1
    response = cast(Response, client.post(
        "/api/inbox", content=b"tiny",
        headers={"content-length": str(declared),
                 "content-type": "multipart/form-data; boundary=x",
                 "origin": "http://localhost:3000"},
        cookies={"session": a.token}))
    assert response.status_code == 413
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert _count(a.tenant_id) == before


def test_a_chunked_request_with_no_content_length_is_411(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    """I3: a body with no declared length (chunked transfer-encoding) cannot
    be refused on size before it is read, so it is refused outright."""
    a, _ = two_tenants
    before = _count(a.tenant_id)

    def _chunks() -> Iterator[bytes]:
        yield b"tiny"

    response = cast(Response, client.post(
        "/api/inbox", content=_chunks(),
        headers={"content-type": "multipart/form-data; boundary=x"},
        cookies={"session": a.token}))
    assert response.status_code == 411
    assert _count(a.tenant_id) == before


def test_the_body_cap_middleware_leaves_other_paths_alone() -> None:
    """The middleware is scoped to POST /api/inbox exactly: health and login
    must behave exactly as they did before it existed."""
    assert client.get("/api/health").status_code == 200
    response = cast(Response, client.post(
        "/api/auth/login", json={"email": "nope@example.test", "password": "wrong"}))
    assert response.status_code == 401


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


def test_a_literal_percent_in_a_filename_is_not_url_decoded(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    """Only the WHATWG multipart/form-data escape set (`"`, C0 controls other
    than ESC) is ever undone. `%25`/`%20`/`%2F`/a non-ASCII percent-triplet
    are not escapes that algorithm produces, so a file genuinely named with
    one of these round-trips untouched rather than being mistaken for a
    percent-encoded byte."""
    a, _ = two_tenants
    assert _upload(a, _pdf(), filename="50%25off.pdf").json()["filename"] == "50%25off.pdf"
    assert _upload(a, _pdf(), filename="100%20done.pdf").json()["filename"] == "100%20done.pdf"
    assert _upload(a, _pdf(), filename="Q3%2Freport.pdf").json()["filename"] == "Q3%2Freport.pdf"
    assert _upload(a, _pdf(), filename="caf%E9.pdf").json()["filename"] == "caf%E9.pdf"


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


def test_the_list_is_the_tenants_own_documents_newest_first(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, b = two_tenants
    first = _upload(a, _pdf(), filename="first.pdf").json()["id"]
    second = _upload(a, _pdf(), filename="second.pdf").json()["id"]
    _upload(b, _pdf(), filename="theirs.pdf")
    items = a.get("/api/inbox").json()["items"]
    assert [i["id"] for i in items][:2] == [second, first]
    assert all(set(i) == {"id", "filename", "sensitivity", "origin", "received_at",
                          "status", "doc_type", "run_note", "finding_count", "claim_count"}
               for i in items)
    assert "theirs.pdf" not in {i["filename"] for i in items}


def test_the_list_pages_by_keyset_and_caps_the_page(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    ids = [_upload(a, _pdf()).json()["id"] for _ in range(3)]
    page = a.get("/api/inbox?limit=2").json()["items"]
    assert [i["id"] for i in page] == [ids[2], ids[1]]
    last = page[-1]
    rest = a.get(f"/api/inbox?limit=2&before_received_at={last['received_at']}"
                 f"&before_id={last['id']}").json()["items"]
    assert [i["id"] for i in rest] == [ids[0]]
    assert a.get("/api/inbox?limit=201").status_code == 422
    assert a.get("/api/inbox?limit=0").status_code == 422
    assert a.get(f"/api/inbox?before_id={last['id']}").status_code == 422


def test_the_keyset_tie_breaks_on_id_when_received_at_is_identical(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    """M6: two documents sharing one received_at must not collide at the page
    boundary — a keyset on received_at alone would drop or repeat one of
    them. Stored directly via store_document (not the route, which always
    stamps datetime.now(UTC) and so cannot produce a tie) with a fixed
    timestamp for both rows."""
    a, _ = two_tenants
    tied_at = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)
    blobs = blob_store_from_environment()
    with store_session.app_engine().begin() as conn:
        ids = [
            store_document(
                conn, tenant_id=a.tenant_id,
                doc=accept_document(_pdf(), origin=Origin.UPLOAD,
                                    provenance=f"tests/api/test_inbox.py#{i}"),
                blobs=blobs, received_at=tied_at, filename="tie.pdf")
            for i in range(2)
        ]
    newer_id, older_id = sorted(ids, reverse=True)

    first_page = a.get("/api/inbox?limit=1").json()["items"]
    assert [i["id"] for i in first_page] == [newer_id]
    cursor = first_page[0]
    second_page = a.get(f"/api/inbox?limit=1&before_received_at={cursor['received_at']}"
                        f"&before_id={cursor['id']}").json()["items"]
    assert [i["id"] for i in second_page] == [older_id]


def test_a_document_reads_back_for_its_owner_and_404s_for_anyone_else(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    # Not a full-body equality: the background run completes (synchronously,
    # under TestClient) between the 201 and this GET, so status/run_note can
    # legitimately differ — the identity and static fields must not.
    a, b = two_tenants
    body = _upload(a, _pdf(), filename="mine.pdf").json()
    read_back = a.get(f"/api/inbox/{body['id']}").json()
    assert (read_back["id"], read_back["filename"], read_back["sensitivity"],
            read_back["origin"], read_back["received_at"]) == (
        body["id"], body["filename"], body["sensitivity"],
        body["origin"], body["received_at"])
    assert b.get(f"/api/inbox/{body['id']}").status_code == 404
    assert a.get("/api/inbox/nope").status_code == 404


def _corpus_pdf(error: str = "clean") -> bytes:
    return generate(seed=1, error=error)[2]  # type: ignore[arg-type]


def _register_vendor(tenant: TenantClient, vendor_name: str) -> None:
    # A fresh tenant's vendor ledger is empty, so the corpus document's
    # vendor must be pre-registered before the run — otherwise I2 legitimately
    # fires ("first time we've seen this name") and a "no findings" or exact
    # finding-count expectation would be false. Mirrors
    # tests/run/test_document.py::test_a_clean_document_produces_no_findings.
    ledger = VendorLedger()
    ledger.create(vendor_name)
    with store_session.app_engine().begin() as conn:
        save_vendor_ledger(conn, tenant.tenant_id, ledger)


def test_an_upload_answers_received_and_is_extracted_by_the_time_it_is_read_back(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    _, app, pdf = generate(seed=1, error="clean")
    assert app.vendor_name is not None
    _register_vendor(a, app.vendor_name)
    response = _upload(a, pdf)
    assert response.status_code == 201
    assert response.json()["status"] == "received"
    # TestClient runs BackgroundTasks before returning, so the run is done.
    body = a.get(f"/api/inbox/{response.json()['id']}").json()
    assert body["status"] == "extracted"
    assert body["doc_type"] == "pay_application"
    assert body["run_note"] is None
    assert body["claim_count"] > 0
    # LS-09: nothing is calibrated, so every extracted field of a clean
    # upload is an open R1 review requirement -- none are silently trusted.
    assert body["finding_count"] == body["claim_count"]


def test_a_footing_error_shows_up_as_an_open_finding_count(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    document_id = _upload(a, _corpus_pdf("A1_footing")).json()["id"]
    assert a.get(f"/api/inbox/{document_id}").json()["finding_count"] >= 1


def test_a_blank_pdf_is_stored_and_honestly_cannot_extract(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    document_id = _upload(a, _pdf()).json()["id"]
    body = a.get(f"/api/inbox/{document_id}").json()
    assert body["status"] == "cannot_extract"
    assert body["run_note"].startswith("no text layer")


def test_a_crashing_background_run_leaves_the_row_received(
    two_tenants: tuple[TenantClient, TenantClient], monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ledger_sense.api.inbox as inbox_module

    def _boom(document_id: str, tenant_id: str) -> None:
        raise RuntimeError("x")
    monkeypatch.setattr(inbox_module, "run_in_background", _boom)
    a, _ = two_tenants
    # Starlette's TestClient re-raises a background task's exception (a real
    # server only logs it); the 201 was already built and the row committed.
    with pytest.raises(RuntimeError):
        _upload(a, _corpus_pdf())
    with store_session.app_engine().connect() as conn:
        latest = conn.execute(sa.select(documents.c.status)
                              .where(documents.c.tenant_id == a.tenant_id)
                              .order_by(documents.c.received_at.desc(), documents.c.id.desc())
                              ).scalar()
    assert latest == "received"


def test_the_list_filters_by_status_and_rejects_unknown_statuses(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    extracted = _upload(a, _corpus_pdf()).json()["id"]
    blank = _upload(a, _pdf()).json()["id"]
    ids = {d["id"] for d in a.get("/api/inbox?status=cannot_extract").json()["items"]}
    assert blank in ids and extracted not in ids
    assert a.get("/api/inbox?status=bogus").status_code == 422


def test_counts_are_per_tenant(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, b = two_tenants
    mine = _upload(a, _corpus_pdf("A1_footing")).json()["id"]
    assert a.get(f"/api/inbox/{mine}").json()["finding_count"] >= 1
    assert b.get(f"/api/inbox/{mine}").status_code == 404
    assert all(d["finding_count"] == 0 for d in b.get("/api/inbox").json()["items"])


def test_the_pdf_bytes_read_back_for_the_owner_and_404_for_anyone_else(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    # DocumentPane's pdfUrl: the same bytes that went through the door, back
    # out of the encrypted blob, served uncached — a pay application must not
    # sit in a shared cache after the reviewer's session ends.
    a, b = two_tenants
    payload = _pdf(pages=2)
    document_id = _upload(a, payload).json()["id"]

    response = a.get(f"/api/inbox/{document_id}/pdf")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    assert response.content == payload

    assert b.get(f"/api/inbox/{document_id}/pdf").status_code == 404
    assert a.get("/api/inbox/nope/pdf").status_code == 404


def test_the_pdf_route_is_a_404_not_a_500_when_the_blob_is_gone(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    # A retention purge nulls blob_id and leaves the row: the owner still
    # gets "not found", not a traceback.
    a, _ = two_tenants
    document_id = _upload(a, _pdf()).json()["id"]
    with store_session.app_engine().begin() as conn:
        conn.execute(documents.update().where(documents.c.id == document_id)
                     .values(blob_id=None))
    assert a.get(f"/api/inbox/{document_id}/pdf").status_code == 404
