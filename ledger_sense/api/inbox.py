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

import re
import unicodedata
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ledger_sense.api.deps import require_session
from ledger_sense.api.fixtures import demo_profile_enabled
from ledger_sense.auth.session import Session
from ledger_sense.boundary import Origin, SensitivityViolation
from ledger_sense.ingest.intake import accept_document, intake
from ledger_sense.ingest.limits import Limits, RejectedDocument, Rejection
from ledger_sense.store.blobs import blob_store_from_environment
from ledger_sense.store.documents import store_document
from ledger_sense.store.session import app_engine

router = APIRouter(prefix="/api/inbox")

CurrentSession = Annotated[Session, Depends(require_session)]

FALLBACK_FILENAME = "document.pdf"
MAX_FILENAME = 255

# The one Limits instance the route consults — never a second copy of
# max_bytes. ingest.limits.parse_within_limits enforces the same cap again,
# independently, on the bytes this route hands it; this one exists only so
# an oversized upload can be refused before those bytes are read at all.
_LIMITS = Limits()

# Only TOO_LARGE is the client's payload being too big for the server (413).
# Everything else is a payload the server cannot process as sent (422).
_STATUS_OF: dict[Rejection, int] = {Rejection.TOO_LARGE: 413}

# The exact percent-triplets the WHATWG "multipart/form-data" algorithm
# produces for a filename: `"` and every C0 control byte except ESC (0x1B),
# which the algorithm leaves alone (see the `_HTML5_FORM_ENCODING_REPLACEMENTS`
# table httpx's own multipart encoder uses to build a request, and which
# browsers implement independently for the same reason — RFC 7578's
# `filename=` is a quoted-string and cannot carry these bytes raw). Nothing
# else is in this set: `%25`, `%20`, `%2F`, `%E9`, ... are not escapes this
# algorithm produces and must survive as the literal text a client sent,
# including a file genuinely named "50%25off.pdf".
_ESCAPED_CODEPOINTS = frozenset({0x22} | {c for c in range(0x20) if c != 0x1B})
_PERCENT_TRIPLET = re.compile(r"%([0-9A-Fa-f]{2})")


def _unescape_form_filename(value: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        code = int(match.group(1), 16)
        return chr(code) if code in _ESCAPED_CODEPOINTS else match.group(0)
    return _PERCENT_TRIPLET.sub(_replace, value)


def sanitise_filename(name: str | None) -> str:
    """A display name, never a path: basename only (either slash), control
    characters dropped, bounded length, a fallback if nothing survives.

    `_unescape_form_filename` first, undoing only the specific percent-triplet
    escaping the WHATWG multipart/form-data algorithm applies to a control
    character or a literal quote in a filename — a client-supplied "%00" is
    exactly as hostile as the NUL byte it stands for and must be decoded
    before the control filter and the path split below can see it. Anything
    outside that exact escape set (`%25`, `%2F`, `%E9`, ...) is left alone:
    it is not an escape this algorithm produces, so it is the client's
    literal, harmless text, not a byte to recover.
    """
    decoded = _unescape_form_filename(name or "")
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
    # this", which only the demo profile has any business asserting. Read
    # per request, deliberately — unlike app.py's build-time read (which
    # decides once whether the demo-seed route is mounted at all), a
    # profile flip must take effect on this route's very next call, not on
    # the next process restart.
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

    # Refuse on the declared size before reading a single byte: Starlette's
    # multipart parser has already spooled the upload (to memory, then to
    # disk past its own threshold) by the time this handler runs, but that
    # spooling is not this route's to fix. What is this route's to fix is
    # not *also* pulling the whole spooled file into one Python `bytes`
    # object before refusing it — file.size is tracked incrementally by the
    # parser and is available for free.
    if file.size is not None and file.size > _LIMITS.max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"rejected: payload size {file.size} exceeds the size cap")

    # Capped even so: file.size can be absent (some ASGI servers/clients
    # never set Content-Length and ours is only as good as the parser's
    # count), and intake() re-checks the same cap independently on whatever
    # this reads. Bounding the read itself is what actually keeps a
    # multi-gigabyte body from landing in this process as one buffer.
    payload = await file.read(_LIMITS.max_bytes + 1)
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
