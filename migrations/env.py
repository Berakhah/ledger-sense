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
from ledger_sense.store.session import app_engine as ledger_sense_engine

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

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
