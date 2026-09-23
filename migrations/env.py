from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from cryo.config import get_settings
from cryo.db.base import Base
from cryo.db import models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Fail fast instead of queueing behind a long-lived transaction. DDL needs ACCESS
        # EXCLUSIVE, and a blocked request for it also blocks every reader that arrives after
        # it -- so a migration that merely *waits* takes the application down with it, which
        # is strictly worse than not running. 2026-09-23: `ADD COLUMN last_scan_error_kind`
        # sat 13 minutes behind two transactions the scheduler had left idle-in-transaction
        # for ~60 minutes (its benefit-score sweep holds one transaction across a 12k-table
        # Python loop), 17 sessions piled up behind the ALTER, and the helm pre-upgrade hook
        # timed out and failed the release.
        #
        # With a lock_timeout the ALTER gives up in seconds and the deploy fails loudly and
        # harmlessly, leaving the release to be retried once the blocker clears. Set on the
        # connection rather than per-migration so every migration inherits it.
        connection.exec_driver_sql("SET lock_timeout = '10s'")
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
