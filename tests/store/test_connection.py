# tests/store/test_connection.py
import concurrent.futures
import pathlib
import threading

from ledger_sense.store.sqlite import connect


def test_a_connection_is_usable_from_a_worker_thread(tmp_path: pathlib.Path) -> None:
    """The failure every one of the five stores would hit under FastAPI."""
    path = str(tmp_path / "t.db")
    connect(path).execute("CREATE TABLE t (x INTEGER)")

    def write(i: int) -> None:
        conn = connect(path)
        conn.execute("INSERT INTO t VALUES (?)", (i,))
        conn.commit()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(64)))
    assert connect(path).execute("SELECT COUNT(*) FROM t").fetchone()[0] == 64


def test_wal_is_enabled(tmp_path: pathlib.Path) -> None:
    mode = connect(str(tmp_path / "t.db")).execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_a_busy_timeout_is_set(tmp_path: pathlib.Path) -> None:
    assert connect(str(tmp_path / "t.db")).execute(
        "PRAGMA busy_timeout").fetchone()[0] >= 5000


def test_foreign_keys_are_enforced(tmp_path: pathlib.Path) -> None:
    assert connect(str(tmp_path / "t.db")).execute(
        "PRAGMA foreign_keys").fetchone()[0] == 1


def test_each_thread_gets_its_own_connection(tmp_path: pathlib.Path) -> None:
    """A plain ThreadPoolExecutor.map over 4 fast tasks does not guarantee 4
    distinct worker threads — a warm worker can service more than one task
    before the pool spins up another, so this used to pass or fail on
    scheduling luck rather than on connect()'s actual per-thread behaviour.
    The barrier forces all 4 calls to be in flight at once, which is the one
    situation that can only happen with 4 distinct threads."""
    path = str(tmp_path / "t.db")
    ids: set[int] = set()
    ids_lock = threading.Lock()
    barrier = threading.Barrier(4)

    def grab(_: int) -> None:
        barrier.wait()
        with ids_lock:
            ids.add(id(connect(path)))

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(grab, range(4)))
    assert len(ids) > 1
