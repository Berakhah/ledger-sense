"""Fixtures for the writeback tests that touch the real database.

`_database` gives tests/writeback/test_assemble.py (and any later test in
this directory that reads or writes `claims`/`documents`) a live SQLite file
built by the real migrations (`alembic upgrade head`) via LEDGER_SENSE_DB_URL
— the same override tests/api/conftest.py and tests/store/conftest.py use —
rather than letting `store_session.app_engine()` fall through to its
Postgres default and hang/fail against nothing listening on localhost.

Autouse and function-scoped: every test in this directory that creates a
document creates its own tenant via `create_tenant` first, so a fresh
database per test keeps that cheap without needing any shared setup. Tests
in this directory that never touch the database (test_qbo.py, test_tokens.py,
...) are unaffected — they never call `store_session.app_engine()`, so the
override sits unused.
"""

import pathlib
from collections.abc import Iterator

import pytest

from ledger_sense.store import session as store_session

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


@pytest.fixture(autouse=True)
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
