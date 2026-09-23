# Migrations logged success and committed nothing (2026-09-23)

## What happened

For part of 2026-09-23, `alembic upgrade head` printed the full chain of
`INFO [alembic.runtime.migration] Running upgrade X -> Y` lines, exited 0, and left the
database completely untouched. No tables, no `alembic_version` row, no error.

Caught while verifying an unrelated migration (`20260923_1500_report_send_state`) against a
throwaway `postgres:16`: the migration "ran", then `\d cryo.report_send_state` reported
`Did not find any relation`.

## Root cause

Earlier the same day, `migrations/env.py` gained a `lock_timeout` guard (see
`docs/` notes on the hung `last_scan_error_kind` migration). It was written as a statement
issued on the connection before Alembic was configured:

```python
with connectable.connect() as connection:
    connection.exec_driver_sql("SET lock_timeout = '10s'")   # <-- opens a transaction
    context.configure(connection=connection, ...)
    with context.begin_transaction():
        context.run_migrations()
```

Under SQLAlchemy 2.0 a `Connection` begins an implicit transaction on its first statement.
By the time `context.begin_transaction()` runs, one is already open, so Alembic's context
manager is effectively a no-op and never issues the `COMMIT`. Leaving the `with
connectable.connect()` block then rolls the whole thing back — including the
`alembic_version` update, which is why re-running it reported the same migrations as
pending each time and never looked "stuck".

The failure is silent by construction: every statement succeeded, so Alembic had nothing to
report.

## Proof

Same fresh database, same chain, only the one line differing:

| `env.py` | tables in `cryo` schema after `alembic upgrade head` |
|---|---|
| with `connection.exec_driver_sql("SET lock_timeout = '10s'")` | **0** |
| without that line | **65** |

## Fix

Pass it as a libpq connect option so the setting is applied at connection time and no
statement is issued before Alembic takes over:

```python
connectable = engine_from_config(
    configuration,
    prefix="sqlalchemy.",
    poolclass=pool.NullPool,
    connect_args={"options": "-c lock_timeout=10s"},
)
```

Verified afterwards, all three properties on a real `postgres:16`:

- `alembic upgrade head` creates 65 tables and writes `alembic_version = 20260923_1500`.
- `SHOW lock_timeout` is `10s` on a migration-style connection and `0` on a plain one, so
  the guard is genuinely active rather than merely not breaking anything.
- With a concurrent transaction holding the table, `ALTER TABLE` aborts after **10.0s** with
  `psycopg.errors.LockNotAvailable`, instead of queueing behind the blocker and taking
  readers down with it.

## Why it did not cause an outage

The `last_scan_error_kind` migration it was introduced for had already been applied by hand
during the incident that prompted the guard, and the next deploy found the schema at head,
so the no-op path was never exercised against a migration that actually needed to run. The
next real migration would have deployed application code expecting a column that did not
exist.

## Lesson

Do not issue any statement on the Alembic connection before `context.begin_transaction()`.
Session-level settings for migrations belong in `connect_args`. When adding a guard to the
migration path, verify it against a real database by asserting the *schema changed*, not by
reading Alembic's log output — the log is not evidence of a commit.
