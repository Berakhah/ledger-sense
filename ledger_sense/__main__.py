"""`python -m ledger_sense` — the container's CMD (see Dockerfile) and the
documented dev entrypoint (pyproject.toml). With no args, migrates then serves
api.app:app over ASGI. With `extract <path>`, runs the deterministic pipeline
over one PDF and prints a JSON summary — a dev entrypoint, not a product
surface (the buyer entrypoint is the web UI); no CLI framework, no
`[project.scripts]`.

    python -m ledger_sense               # migrate, then serve (container CMD)
    python -m ledger_sense extract PATH  # read a PDF, assemble, print JSON

Nothing else in the stack runs `alembic upgrade head` against the real
Postgres (tests/api/conftest.py does it for the SQLite fixture only) — this
is the one place `docker compose up` on a clean machine gets a schema, so the
`extract` subcommand is dispatched INSIDE `main()` rather than replacing it.
"""

import json
import pathlib
import sys

import uvicorn
from alembic import command
from alembic.config import Config

from ledger_sense.extract.assemble import assemble_pay_application
from ledger_sense.ingest.classify import classify

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _migrate() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "head")


def _extract(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: python -m ledger_sense extract <path>", file=sys.stderr)
        return 2
    path = pathlib.Path(argv[2])
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


def main() -> None:
    argv = sys.argv
    if len(argv) > 1 and argv[1] == "extract":
        raise SystemExit(_extract(argv))
    _migrate()
    # 0.0.0.0: this process IS the container's edge for port 8000
    # (docker-compose.yml maps "8000:8000") — binding to localhost only would
    # make it unreachable from the host or from `web`.
    uvicorn.run("ledger_sense.api.app:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
