"""Learned extraction geometry, keyed by document fingerprint.

The review queue is the training signal for the DETERMINISTIC extractor. No
model, no inference cost, entirely inside the data boundary — which is what
makes "the product improves as you use it" true without an asterisk.

GEOMETRY ONLY. Never a value. This cache is shared per deployment, so a cached
value would carry one client's data into another client's extraction.
"""

from ledger_sense.extract.textlayer import BBox
from ledger_sense.ingest.fingerprint import ProfileKey
from ledger_sense.store.sqlite import connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    tenant_id TEXT NOT NULL,
    key       TEXT NOT NULL,
    field     TEXT NOT NULL,
    x0        REAL NOT NULL,
    top       REAL NOT NULL,
    x1        REAL NOT NULL,
    bottom    REAL NOT NULL,
    PRIMARY KEY (tenant_id, key, field)
)
"""


class ProfileCache:
    """REVISED. Three changes, each a finding:

    F-18 — TENANT SCOPE. The key was `producer:column_hash`, so two clients whose
    subcontractors use the same accounting package shared a profile, and a wrong
    correction by client A's bookkeeper silently shifted extraction geometry for
    client B. Geometry-only prevents DATA leakage; it does not prevent INFLUENCE,
    and in a product whose differentiator is that a buyer can trust the number,
    one tenant steering another's extraction is the harder problem. `tenant_id`
    is a required constructor argument, not an optional one.

    F-19 — CONNECTION. Direct SQLite connections in __init__, held, and used from a
    FastAPI threadpool raise ProgrammingError on the first cross-thread call.
    Use the shared factory (Phase 0 Task P4): WAL, busy timeout, per-thread.

    F-20 — NO WRITE ON READ. `lookup` ran an UPDATE and a commit per call, so a
    50-field document performed 50 write transactions to read. The hit counter
    bought nothing; it is gone.
    """

    def __init__(self, db_path: str, *, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("ProfileCache requires a tenant_id — see F-18")
        self._path = db_path
        self._tenant = tenant_id
        connect(db_path).execute(_SCHEMA)
        connect(db_path).commit()

    def lookup(self, key: ProfileKey, field: str) -> BBox | None:
        row = connect(self._path).execute(
            "SELECT x0, top, x1, bottom FROM profiles "
            "WHERE tenant_id = ? AND key = ? AND field = ?",
            (self._tenant, key.as_str(), field)).fetchone()
        return BBox(*row) if row else None

    def learn(self, key: ProfileKey, field: str, bbox: BBox) -> None:
        conn = connect(self._path)
        conn.execute(
            "INSERT INTO profiles (tenant_id, key, field, x0, top, x1, bottom) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (tenant_id, key, field) DO UPDATE SET "
            "x0 = excluded.x0, top = excluded.top, "
            "x1 = excluded.x1, bottom = excluded.bottom",
            (self._tenant, key.as_str(), field,
             bbox.x0, bbox.top, bbox.x1, bbox.bottom))
        conn.commit()

    def close(self) -> None:
        """No-op for compatibility. The factory owns connection lifetime."""
