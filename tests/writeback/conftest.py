"""Fixtures for the writeback tests that touch the real database.

`_database` gives tests/writeback/test_assemble.py (and any later test in
this directory that reads or writes `claims`/`documents`) a live SQLite file
built by the real migrations (`alembic upgrade head`) via LEDGER_SENSE_DB_URL
— the same override tests/api/conftest.py and tests/store/conftest.py use —
rather than letting `store_session.app_engine()` fall through to its
Postgres default and hang/fail against nothing listening on localhost.

Deliberately NOT autouse: this fixture unconditionally enters a MonkeyPatch
context, resets the cached app engine, and runs a full `alembic upgrade
head` against a fresh SQLite file before yielding — real I/O and migration
cost that only the tests which actually call `store_session.app_engine()`
should pay. Making it directory-wide autouse would tax every other test
file in tests/writeback/ (test_interface.py, test_qbo.py,
test_qbo_sandbox.py, test_tokens.py, test_idempotency.py, test_audit.py,
test_export.py) with that setup/teardown on every run despite none of them
touching the real database — and that cost only grows as later tasks add
more real-database tests here. Tests that need it request it explicitly by
name (test_assemble.py does this once, module-wide, via
`pytestmark = pytest.mark.usefixtures("_database")`); every other test file
in this directory never requests it and so never enters its setup at all.

Function-scoped: every test in this directory that creates a document
creates its own tenant via `create_tenant` first, so a fresh database per
test keeps that cheap without needing any shared setup.
"""

import pathlib
from collections.abc import Iterator

import pytest

from ledger_sense.store import session as store_session

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


@pytest.fixture()
def _database(tmp_path: pathlib.Path) -> Iterator[None]:
    from alembic import command
    from alembic.config import Config

    # as_posix(): a Windows path's backslashes and drive colon do not
    # survive SQLAlchemy's URL parser.
    database = (tmp_path / "writeback.db").as_posix()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("LEDGER_SENSE_DB_URL", f"sqlite:///{database}")
        store_session.reset_app_engine()
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        command.upgrade(config, "head")
        yield
    store_session.reset_app_engine()
