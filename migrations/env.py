"""Alembic environment.

Wired to ledger_sense.store.session so migrations run through the one
Postgres connection factory (F-19/G-3) instead of alembic.ini carrying its
own hardcoded sqlalchemy.url (and, with it, a password checked into the
repo).

Uses app_engine() rather than engine() so LEDGER_SENSE_DB_URL repoints
migrations exactly as it repoints the application — which is what lets the
suite apply 0001_tenancy to a SQLite file and assert the schema the running
code queries is the schema the migration builds.

target_metadata is the auth schema; later phases extend it with their own
tables (each carrying tenant_id) so `alembic revision --autogenerate` has
something to diff against.
"""

from logging.config import fileConfig

from alembic import context

from ledger_sense.auth.models import metadata as auth_metadata

# Registers documents/claims/retention_audit_log on that same MetaData. An
# import purely for its side effect, and load-bearing: without it autogenerate
# would diff against a schema missing three tables and propose dropping them.
from ledger_sense.store import retention  # noqa: F401
from ledger_sense.store.session import app_engine as ledger_sense_engine

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False: fileConfig's default (True) silently
    # disables every logger already created elsewhere in the process (any
    # ledger_sense.* module logger, if this runs programmatically rather
    # than via the `alembic` CLI in its own process) the moment this module
    # is imported. That is a real hazard wherever migrations run
    # in-process before or alongside application logging — not just in the
    # test suite, where it broke log.exception() calls made after the first
    # test that triggered `alembic upgrade head`.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = auth_metadata


def run_migrations_offline() -> None:
    """Emit SQL without a live connection (`alembic upgrade --sql`)."""
    url = str(ledger_sense_engine().url)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live Postgres, through the one factory."""
    connectable = ledger_sense_engine()
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
