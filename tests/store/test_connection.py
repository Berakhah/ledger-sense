# tests/store/test_connection.py
import concurrent.futures

from ledger_sense.store.sqlite import connect


def test_a_connection_is_usable_from_a_worker_thread(tmp_path):
    """The failure every one of the five stores would hit under FastAPI."""
    path = str(tmp_path / "t.db")
    connect(path).execute("CREATE TABLE t (x INTEGER)")

    def write(i):
        conn = connect(path)
        conn.execute("INSERT INTO t VALUES (?)", (i,))
        conn.commit()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(64)))
    assert connect(path).execute("SELECT COUNT(*) FROM t").fetchone()[0] == 64


def test_wal_is_enabled(tmp_path):
    mode = connect(str(tmp_path / "t.db")).execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_a_busy_timeout_is_set(tmp_path):
    assert connect(str(tmp_path / "t.db")).execute(
        "PRAGMA busy_timeout").fetchone()[0] >= 5000


def test_foreign_keys_are_enforced(tmp_path):
    assert connect(str(tmp_path / "t.db")).execute(
        "PRAGMA foreign_keys").fetchone()[0] == 1


def test_each_thread_gets_its_own_connection(tmp_path):
    path = str(tmp_path / "t.db")
    ids: set[int] = set()

    def grab(_):
        ids.add(id(connect(path)))

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(grab, range(4)))
    assert len(ids) > 1
