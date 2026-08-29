# tests/store/test_session.py
"""store/session.py has no given test in the brief — P2, which adds the
first Postgres-backed table, hasn't run yet, so there is no tenant/session
table to point a live test at. Docker's daemon is also not running on this
machine, so there is no live Postgres to connect against either.

These tests cover what's testable without one: that connection parameters
come from the environment (matching docker-compose.yml's POSTGRES_* names
and defaults) and that the factory builds the connection target
(SQLAlchemy engine URL) Alembic and psycopg expect. sqlalchemy.create_engine
is lazy — building an Engine never opens a socket, only .connect() does — so
these assert shape, not live connectivity.
"""

from ledger_sense.store import session


def test_params_fall_back_to_docker_compose_defaults(monkeypatch):
    for var in (
        "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_USER",
        "POSTGRES_PASSWORD", "POSTGRES_DB",
    ):
        monkeypatch.delenv(var, raising=False)
    params = session._params()
    assert params == {
        "host": "localhost",
        "port": "5432",
        "user": "ledger_sense",
        "password": "changeme",
        "dbname": "ledger_sense",
    }


def test_params_read_from_environment(monkeypatch):
    monkeypatch.setenv("POSTGRES_HOST", "db.example")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    monkeypatch.setenv("POSTGRES_USER", "alice")
    monkeypatch.setenv("POSTGRES_PASSWORD", "s3cret")
    monkeypatch.setenv("POSTGRES_DB", "ledger")
    assert session._params() == {
        "host": "db.example",
        "port": "6543",
        "user": "alice",
        "password": "s3cret",
        "dbname": "ledger",
    }


def test_engine_targets_the_env_configured_database_without_connecting(monkeypatch):
    monkeypatch.setenv("POSTGRES_HOST", "db.example")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    monkeypatch.setenv("POSTGRES_USER", "alice")
    monkeypatch.setenv("POSTGRES_PASSWORD", "s3cret")
    monkeypatch.setenv("POSTGRES_DB", "ledger")

    eng = session.engine()

    assert eng.url.drivername == "postgresql+psycopg"
    assert eng.url.host == "db.example"
    assert eng.url.port == 6543
    assert eng.url.username == "alice"
    assert eng.url.database == "ledger"
