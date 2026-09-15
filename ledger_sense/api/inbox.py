"""The inbox: the labelled door with an HTTP front.

POST composes three functions that already exist and are already guarded —
intake() (resource caps), accept_document() (the only Labelled constructor),
store_document() (the only documents.insert()) — and adds nothing of its own.
No extraction, no rules, no gateway call: those come later and read the row
this route writes.

Mounted through api.deps.register() in api/app.py, so require_session is
applied there; depended on here too because the handlers need the tenant and
user off the session.
"""

import unicodedata
import urllib.parse
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ledger_sense.api.deps import require_session
from ledger_sense.api.fixtures import demo_profile_enabled
from ledger_sense.auth.session import Session
from ledger_sense.boundary import Origin, SensitivityViolation
from ledger_sense.ingest.intake import accept_document, intake
from ledger_sense.ingest.limits import RejectedDocument, Rejection
from ledger_sense.store.blobs import blob_store_from_environment
from ledger_sense.store.documents import store_document
from ledger_sense.store.session import app_engine

router = APIRouter(prefix="/api/inbox")

CurrentSession = Annotated[Session, Depends(require_session)]

FALLBACK_FILENAME = "document.pdf"
MAX_FILENAME = 255

# Only TOO_LARGE is the client's payload being too big for the server (413).
# Everything else is a payload the server cannot process as sent (422).
_STATUS_OF: dict[Rejection, int] = {Rejection.TOO_LARGE: 413}


def sanitise_filename(name: str | None) -> str:
    """A display name, never a path: basename only (either slash), control
    characters dropped, bounded length, a fallback if nothing survives.

    `unquote` first: the HTML5 multipart/form-data algorithm that browsers
    (and httpx) apply to a filename containing a control character or a
    literal quote encodes it as a percent-triplet in the wire value rather
    than sending the raw byte — RFC 7578 forbids the raw byte in a
    quoted-string. A client-supplied "%00" or "%2F" is therefore exactly as
    hostile as the byte it stands for and must be decoded before the control
    filter and the path split below can see it.
    """
    decoded = urllib.parse.unquote(name or "")
    stem = decoded.replace("\\", "/").rsplit("/", 1)[-1]
    clean = "".join(ch for ch in stem if unicodedata.category(ch)[0] != "C").strip()
    return clean[:MAX_FILENAME] or FALLBACK_FILENAME


def _provenance(origin: Origin, session: Session) -> str:
    # Who put it through the door — never the client-controlled filename.
    return f"{origin.value.lower()}://{session.tenant_id}/{session.user_id}"


def _row(document_id: str, *, filename: str, sensitivity: str, origin: str,
         received_at: datetime) -> dict[str, Any]:
    return {"id": document_id, "filename": filename, "sensitivity": sensitivity,
            "origin": origin, "received_at": received_at.isoformat()}


@router.post("", status_code=201)
async def upload(
    session: CurrentSession,
    file: Annotated[UploadFile, File()],
    origin: Annotated[Origin, Form()] = Origin.UPLOAD,
) -> dict[str, Any]:
    # Route-level policy, not the door's: CORPUS means "our generator made
    # this", which only the demo profile has any business asserting.
    if origin is Origin.CORPUS and not demo_profile_enabled():
        raise HTTPException(
            status_code=422,
            detail="origin CORPUS is accepted only under the demo profile "
                   "(LEDGER_SENSE_PROFILE=demo); a client deployment labels every "
                   "upload CLIENT_CONFIDENTIAL")
    if origin is Origin.REDACTED_UPLOAD:
        raise HTTPException(
            status_code=422,
            detail="REDACTED_UPLOAD needs a verified redaction pass and no verifier "
                   "ships yet; upload the original and it will be labelled "
                   "CLIENT_CONFIDENTIAL")

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=422, detail="file is empty")

    parsed = intake(payload)
    if isinstance(parsed, RejectedDocument):
        raise HTTPException(status_code=_STATUS_OF.get(parsed.kind, 422), detail=parsed.reason)

    try:
        doc = accept_document(payload, origin=origin, provenance=_provenance(origin, session))
    except SensitivityViolation as exc:
        # Belt and braces: the two checks above should make this unreachable,
        # but the door's refusal must never surface as a 500 traceback.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    filename = sanitise_filename(file.filename)
    received_at = datetime.now(UTC)
    with app_engine().begin() as conn:
        document_id = store_document(conn, tenant_id=session.tenant_id, doc=doc,
                                     blobs=blob_store_from_environment(),
                                     received_at=received_at, filename=filename)
    return _row(document_id, filename=filename, sensitivity=doc.sensitivity.value,
                origin=doc.origin.value, received_at=received_at)
