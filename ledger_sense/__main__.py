"""`python -m ledger_sense` — the container's CMD (see Dockerfile) and the
documented dev entrypoint (pyproject.toml). With no args, migrates then serves
api.app:app over ASGI. With `extract <path>`, runs the deterministic pipeline
over one PDF and prints a JSON summary — a dev entrypoint, not a product
surface (the buyer entrypoint is the web UI); no CLI framework, no
`[project.scripts]`.

    python -m ledger_sense               # migrate, then serve (container CMD)
    python -m ledger_sense extract PATH  # read a PDF, assemble, print JSON
    python -m ledger_sense purge         # delete documents past their retention term
    python -m ledger_sense erase ...     # a client's erasure request (dry run by default)

Nothing else in the stack runs `alembic upgrade head` against the real
Postgres (tests/api/conftest.py does it for the SQLite fixture only) — this
is the one place `docker compose up` on a clean machine gets a schema, so the
`extract` subcommand is dispatched INSIDE `main()` rather than replacing it.
"""

import json
import os
import pathlib
import sys
from dataclasses import asdict
from datetime import UTC, datetime

import uvicorn
from alembic import command
from alembic.config import Config

from ledger_sense.extract.assemble import assemble_pay_application
from ledger_sense.ingest.classify import classify
from ledger_sense.run.document import run_document, run_pending
from ledger_sense.store.blobs import blob_store_from_environment
from ledger_sense.store.retention import erase_documents, purge_expired
from ledger_sense.store.session import app_engine
from ledger_sense.writeback.tokens import token_key_from_environment

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The tenant id a startup probe writes and deletes under. Never a real
# tenant's id (create_tenant() mints a uuid4 hex, which this is not shaped
# like), and BlobStore's tenant binding means a probe blob written under this
# id can never be read back as any other tenant's document.
_STARTUP_PROBE_TENANT_ID = "_startup_probe"


def _migrate() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "head")


def _probe_blob_store() -> None:
    """Fail loudly at boot, not on the first client upload (M10).

    `.env.example` ships `LEDGER_SENSE_KEK=changeme`, which is not a valid
    Fernet key — `blob_store_from_environment()` raises when it constructs
    the `Fernet` object, but only once something actually calls it. Without
    this probe, a deployment that never replaced the placeholder KEK, or
    whose `LEDGER_SENSE_BLOB_ROOT` is unwritable (see C1), serves
    `/api/health` as healthy and accepts logins right up until the first
    client document upload fails with PermissionError or InvalidToken deep
    inside `store_document`. A write-then-delete round trip on a probe
    tenant id proves both the KEK and the root are usable before this
    process ever calls itself ready.
    """
    store = blob_store_from_environment()
    blob_id = store.put(_STARTUP_PROBE_TENANT_ID, b"startup probe")
    store.delete(_STARTUP_PROBE_TENANT_ID, blob_id)


def _probe_payment_hmac_key() -> None:
    """Fail at boot, not mid-request, when the vendor payment-detail HMAC key
    is missing. `domain.vendor.payment_hmac` indexes the environment directly,
    so an unset key surfaced as a bare KeyError on the first document that
    carried bank details."""
    if not os.environ.get("LEDGER_SENSE_PAYMENT_HMAC_KEY"):
        raise RuntimeError(
            "LEDGER_SENSE_PAYMENT_HMAC_KEY is unset: refusing to start without "
            "the key that fingerprints vendor bank details (see docs/security.md)."
        )


def _probe_token_key() -> None:
    """Fail at boot, not on the first writeback, when an accounting realm is
    configured but the OAuth token key is missing, malformed, or the KEK.
    Silent without a realm: the demo has no accounting connection and must
    still start."""
    if os.environ.get("QBO_REALM_ID"):
        token_key_from_environment()


def _purge(argv: list[str]) -> int:
    """`purge` — delete every document past its tenant's retention term, its
    encrypted original and derived rows included, and print the report as one
    JSON line. The compose `retention` service runs this nightly."""
    if len(argv) != 2:
        print("usage: python -m ledger_sense purge", file=sys.stderr)
        return 2
    report = purge_expired(datetime.now(UTC), blobs=blob_store_from_environment())
    print(json.dumps(asdict(report)))
    return 0


_ERASE_USAGE = ("usage: python -m ledger_sense erase --tenant ID --actor NAME --reason TEXT "
                "[--document ID ...] [--execute]")


def _erase(argv: list[str]) -> int:
    """`erase` — a client's erasure request: delete a tenant's documents now,
    without waiting for the retention term, and print the report as one JSON line.

    A dry run unless `--execute` is given, because the deletion cannot be
    undone. `--actor` and `--reason` are required so the audit trail names the
    operator and the request behind every document it records.
    """
    args = argv[2:]
    options: dict[str, str] = {}
    document_ids: list[str] = []
    execute = False
    i = 0
    while i < len(args):
        flag = args[i]
        if flag == "--execute":
            execute = True
            i += 1
        elif flag in ("--tenant", "--actor", "--reason", "--document") and i + 1 < len(args):
            if flag == "--document":
                document_ids.append(args[i + 1])
            else:
                options[flag] = args[i + 1]
            i += 2
        else:
            print(_ERASE_USAGE, file=sys.stderr)
            return 2
    if not all(options.get(required) for required in ("--tenant", "--actor", "--reason")):
        print(_ERASE_USAGE, file=sys.stderr)
        return 2
    try:
        report = erase_documents(
            options["--tenant"], actor=options["--actor"], reason=options["--reason"],
            document_ids=document_ids or None, blobs=blob_store_from_environment(),
            execute=execute)
    except (LookupError, RuntimeError) as error:
        print(f"erase refused: {error}", file=sys.stderr)
        return 1
    print(json.dumps({**asdict(report), "executed": execute}))
    return 0


def _extract(argv: list[str]) -> int:
    """`extract <path>`              — stateless probe: parse a file, print a summary.
    `extract --pending [--limit N]`   — run every received/failed document, oldest first.
    `extract --document <id>`        — run one stored document."""
    usage = ("usage: python -m ledger_sense extract <path> | --pending [--limit N] "
             "| --document <id>")
    args = argv[2:]
    if not args:
        print(usage, file=sys.stderr)
        return 2
    if args[0] == "--pending":
        limit = 100
        if len(args) == 3 and args[1] == "--limit" and args[2].isdigit():
            limit = int(args[2])
        elif len(args) != 1:
            print(usage, file=sys.stderr)
            return 2
        for outcome in run_pending(app_engine(), limit=limit):
            print(json.dumps(outcome._asdict()))
        return 0
    if args[0] == "--document":
        if len(args) != 2:
            print(usage, file=sys.stderr)
            return 2
        print(json.dumps(run_document(app_engine(), args[1])._asdict()))
        return 0
    if len(args) != 1:
        print(usage, file=sys.stderr)
        return 2
    path = pathlib.Path(args[0])
    pdf = path.read_bytes()
    app = assemble_pay_application(pdf, str(path))
    unextractable = app.provenance["_unextractable"]
    assert isinstance(unextractable, list)
    print(json.dumps({
        "doc_type": classify(pdf),
        "lines": len(app.lines),
        "total_completed_and_stored": str(app.total_completed_and_stored),
        "unextractable": [u.field for u in unextractable],
    }, indent=2))
    return 0


# Phase 6 Ruling 3: writeback/qbo.py's ConcurrentWriteInProgressError guard is
# an in-memory, per-process set — it closes the concurrent-write race
# (writeback/idempotency.py's module docstring) only within one process.
# Raising this requires a cross-process lock in the idempotency ledger first
# (see docs/methodology.md); until then this is asserted, not just commented.
WORKERS = 1
assert WORKERS == 1, (
    "raising WORKERS requires a cross-process lock in the idempotency "
    "ledger first — see docs/methodology.md's single-process constraint "
    "section before changing this.")


def main() -> None:
    argv = sys.argv
    if len(argv) > 1 and argv[1] == "extract":
        raise SystemExit(_extract(argv))
    if len(argv) > 1 and argv[1] == "purge":
        raise SystemExit(_purge(argv))
    if len(argv) > 1 and argv[1] == "erase":
        raise SystemExit(_erase(argv))
    _migrate()
    _probe_blob_store()
    _probe_payment_hmac_key()
    _probe_token_key()
    # 0.0.0.0: this process IS the container's edge for port 8000
    # (docker-compose.yml maps "8000:8000") — binding to localhost only would
    # make it unreachable from the host or from `web`.
    uvicorn.run("ledger_sense.api.app:app", host="0.0.0.0", port=8000, workers=WORKERS)


if __name__ == "__main__":
    main()
