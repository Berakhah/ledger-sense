import concurrent.futures
import pathlib
import sqlite3

import pytest

from ledger_sense.extract.profiles import ProfileCache
from ledger_sense.extract.textlayer import BBox
from ledger_sense.ingest.fingerprint import ProfileKey

KEY = ProfileKey(producer="AcmeBooks-9", column_hash="abc123")
BOX = BBox(100.0, 200.0, 180.0, 212.0)


@pytest.fixture()
def cache(tmp_path):
    c = ProfileCache(str(tmp_path / "profiles.db"), tenant_id="a")
    yield c
    c.close()


def test_lookup_misses_before_anything_is_learned(cache):
    assert cache.lookup(KEY, "retainage_total") is None


def test_a_correction_is_reused_on_the_next_document(cache):
    cache.learn(KEY, "retainage_total", BOX)
    assert cache.lookup(KEY, "retainage_total") == BOX


def test_a_different_fingerprint_does_not_share_geometry(cache):
    cache.learn(KEY, "retainage_total", BOX)
    other = ProfileKey(producer="OtherBooks-2", column_hash="def456")
    assert cache.lookup(other, "retainage_total") is None


def test_relearning_overwrites(cache):
    cache.learn(KEY, "retainage_total", BOX)
    newer = BBox(105.0, 200.0, 185.0, 212.0)
    cache.learn(KEY, "retainage_total", newer)
    assert cache.lookup(KEY, "retainage_total") == newer


def test_the_cache_stores_geometry_only_and_never_a_value(cache, tmp_path):
    """A cached VALUE would leak one client's data into another's extraction."""
    cache.learn(KEY, "retainage_total", BOX)
    cache.close()
    conn = sqlite3.connect(str(tmp_path / "profiles.db"))
    columns = {row[1] for row in conn.execute("PRAGMA table_info(profiles)")}
    conn.close()
    assert columns == {"tenant_id", "key", "field", "x0", "top", "x1", "bottom"}


def test_one_tenants_correction_does_not_move_another_tenants_geometry(tmp_path):
    """F-18. The key was producer:column_hash with no tenant, so a wrong
    correction by client A silently reshaped extraction for client B."""
    path = str(tmp_path / "profiles.db")
    ProfileCache(path, tenant_id="a").learn(KEY, "retainage_total", BOX)
    assert ProfileCache(path, tenant_id="b").lookup(KEY, "retainage_total") is None


def test_the_cache_refuses_to_open_without_a_tenant(tmp_path):
    with pytest.raises((TypeError, ValueError)):
        ProfileCache(str(tmp_path / "p.db"))


def test_lookup_performs_no_write(tmp_path):
    """F-20. A hit counter cost one write transaction per field read."""
    path = str(tmp_path / "profiles.db")
    cache = ProfileCache(path, tenant_id="a")
    cache.learn(KEY, "retainage_total", BOX)
    before = pathlib.Path(path).stat().st_mtime_ns
    for _ in range(50):
        cache.lookup(KEY, "retainage_total")
    assert pathlib.Path(path).stat().st_mtime_ns == before


def test_the_cache_is_usable_from_a_worker_thread(tmp_path):
    """F-19. sqlite3 defaults to check_same_thread=True and FastAPI runs sync
    handlers in a threadpool."""
    cache = ProfileCache(str(tmp_path / "p.db"), tenant_id="a")
    cache.learn(KEY, "retainage_total", BOX)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: cache.lookup(KEY, "retainage_total"), range(8)))
    assert all(r == BOX for r in results)
