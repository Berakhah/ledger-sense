"""SQLite connection factory (F-19/G-3, "one connection factory").

FastAPI runs sync handlers in a threadpool. sqlite3 defaults to
check_same_thread=True, so a connection built on one thread raises
ProgrammingError the moment a different thread touches it. This module is
the only place in the codebase allowed to call sqlite3.connect (enforced by
tests/test_platform_skeleton.py::test_no_module_opens_a_database_connection_directly)
— every store takes a db_path and calls connect() here instead of opening
its own connection.
"""

import sqlite3
import threading

_local = threading.local()
# ponytail: a single process-wide lock serializing first-open per path would
# scale better than one lock for every path; fine while a handful of stores
# each open one db file. The lock only guards the one-time WAL transition
# below, not steady-state reads/writes, which proceed concurrently.
_setup_lock = threading.Lock()


def connect(path: str) -> sqlite3.Connection:
    """Return this thread's connection to the SQLite database at `path`.

    Each thread gets its own connection (kept in thread-local storage and
    reused on repeat calls with the same path), opened with
    check_same_thread=False, WAL journaling, a 5s busy timeout, and foreign
    key enforcement on.
    """
    conns: dict[str, sqlite3.Connection] | None = getattr(_local, "conns", None)
    if conns is None:
        conns = {}
        _local.conns = conns

    conn = conns.get(path)
    if conn is None:
        # A brand-new file's first switch to WAL needs a brief exclusive
        # lock; concurrent first-openers can race into "database is locked"
        # even with busy_timeout set. Serializing first-open avoids the
        # race — later opens see WAL already active, a fast no-op pragma.
        with _setup_lock:
            conn = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
        conns[path] = conn
    return conn
