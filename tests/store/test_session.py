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


# ---- fix round: connect_timeout + password escaping (review findings 1 & 2) ----

SPECIAL_PASSWORD = "p@ss:w/rd%25"  # "@", ":", "/", "%" — each a URL delimiter/escape char


def test_engine_url_survives_a_password_with_special_characters(monkeypatch):
    """Finding 2 regression test. The old f-string URL
    (f"...{user}:{password}@{host}...") mis-parses a password containing
    "@"/":"/"/" — SQLAlchemy's URL parser would read past the first such
    character as a delimiter, so eng.url.password would come back truncated
    or wrong. URL.create() builds the URL from already-separated
    components, so no parsing/escaping step can corrupt it — this asserts
    the password comes back byte-for-byte, using a real (unmocked) Engine.
    """
    monkeypatch.setenv("POSTGRES_HOST", "db.example")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    monkeypatch.setenv("POSTGRES_USER", "alice")
    monkeypatch.setenv("POSTGRES_PASSWORD", SPECIAL_PASSWORD)
    monkeypatch.setenv("POSTGRES_DB", "ledger")

    eng = session.engine()

    assert eng.url.password == SPECIAL_PASSWORD
    assert eng.url.host == "db.example"  # unaffected by the password's "@"/"/"


def test_connect_passes_connect_timeout_and_an_unmangled_password(monkeypatch):
    """Finding 1 + 2 for the psycopg path. Captures the real kwargs
    connect() would hand to psycopg.connect (without opening a socket) to
    assert connect_timeout is set and the special-character password
    arrives intact — passing params as keyword args sidesteps URL escaping
    entirely, so this also doubles as the connect()-side escaping check.
    """
    monkeypatch.setenv("POSTGRES_HOST", "db.example")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    monkeypatch.setenv("POSTGRES_USER", "alice")
    monkeypatch.setenv("POSTGRES_PASSWORD", SPECIAL_PASSWORD)
    monkeypatch.setenv("POSTGRES_DB", "ledger")

    captured = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return "not-a-real-connection"

    monkeypatch.setattr(session.psycopg, "connect", fake_connect)

    result = session.connect()

    assert result == "not-a-real-connection"
    assert captured["connect_timeout"] == 5
    assert captured["host"] == "db.example"
    assert captured["port"] == "6543"
    assert captured["user"] == "alice"
    assert captured["password"] == SPECIAL_PASSWORD
    assert captured["dbname"] == "ledger"


def test_engine_sets_connect_timeout_via_connect_args(monkeypatch):
    """Finding 1 for the SQLAlchemy path. create_engine() doesn't expose
    connect_args back through any public Engine attribute, so this
    captures the real call create_engine() would receive."""
    monkeypatch.setenv("POSTGRES_HOST", "db.example")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    monkeypatch.setenv("POSTGRES_USER", "alice")
    monkeypatch.setenv("POSTGRES_PASSWORD", "s3cret")
    monkeypatch.setenv("POSTGRES_DB", "ledger")

    captured = {}

    def fake_create_engine(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return "not-a-real-engine"

    monkeypatch.setattr(session, "create_engine", fake_create_engine)

    result = session.engine()

    assert result == "not-a-real-engine"
    assert captured["kwargs"]["connect_args"] == {"connect_timeout": 5}
