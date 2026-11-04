"""ON DELETE actions so a removed table can be hard-deleted (#330)

Revision ID: 20261103_1200
Revises: 20261103_1510

Purging a table tombstoned by #120 deletes its ``cryo.tables`` row. Most children already
cascade; the rest had no ON DELETE action, so the delete failed. Every single-column foreign
key in schema ``cryo`` that references ``tables``, ``jobs`` or ``maintenance_intents`` with no
action is rewritten: a NOT NULL column cascades (jobs, intents, query patterns and activity,
layout recommendations), a nullable one is set to NULL (audit_log, query_logs, AI and failure
diagnostics), so the audit trail outlives the table.

The constraints are found in ``pg_constraint`` rather than listed by name: names in long-lived
databases do not all follow the model naming convention, and some databases still carry
tables the models no longer define. Each rewritten constraint is tagged with a comment so the
downgrade reverts exactly those.

Locking: the drop and re-add are NOT VALID, which only touches the catalog, but each one takes
ACCESS EXCLUSIVE on both the child and the parent. Taken one constraint at a time inside the
shared migration transaction, which already holds ACCESS EXCLUSIVE on ``presets`` from
20261103_1510, they deadlocked against a running app session (2026-11-04: the migrate hook
waited on ``tables`` for ``fk_jobs_table_id_tables`` while a session holding ``tables`` waited
on a relation the migration had already locked). So the rewrite runs in its own transaction
after the earlier revisions commit, locks every relation it touches in one LOCK TABLE (parents
first) under a short lock_timeout, and on a timeout or deadlock rolls back its subtransaction,
releasing those locks, and tries again. VALIDATE then takes SHARE UPDATE EXCLUSIVE, so reads
and writes continue while it scans.
The four indexes make the SET NULL lookups a purge triggers index scans instead of full scans
of audit_log and the diagnostics tables.

SQLite (tests) builds its schema from the models, which carry the same actions, so this
revision does nothing there.
"""

from __future__ import annotations

from alembic import op

revision = "20261103_1200"
down_revision = "20261103_1510"
branch_labels = None
depends_on = None

MARKER = "cryo#330 on delete action for removed-table purge"
REVERTED_MARKER = "cryo#330 on delete action reverted"

_CONSTRAINTS = """
    SELECT c.conname, child.relname AS child, a.attname AS col, a.attnotnull AS notnull,
           parent.relname AS parent, pa.attname AS parent_col, c.convalidated
    FROM pg_constraint c
    JOIN pg_class child ON child.oid = c.conrelid
    JOIN pg_namespace cn ON cn.oid = child.relnamespace
    JOIN pg_class parent ON parent.oid = c.confrelid
    JOIN pg_namespace pn ON pn.oid = parent.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
    JOIN pg_attribute pa ON pa.attrelid = c.confrelid AND pa.attnum = c.confkey[1]
    WHERE c.contype = 'f' AND cn.nspname = 'cryo' AND pn.nspname = 'cryo'
      AND cardinality(c.conkey) = 1
"""

LOCK_TIMEOUT = "3s"
LOCK_ATTEMPTS = 20


def _rewrite_sql(pending: str, body: str) -> str:
    """Run ``body`` for each constraint ``pending`` selects, with every lock it needs held first.

    Waiting on one relation while holding another is what deadlocks against app sessions, so
    the locks are taken together before any ALTER. A lock timeout or deadlock rolls back the
    inner block, releasing everything it took, so the app makes progress before the retry.
    """
    return f"""
DO $$
DECLARE
  r record;
  rels text;
  attempt int := 0;
BEGIN
  PERFORM set_config('lock_timeout', '{LOCK_TIMEOUT}', true);
  LOOP
    BEGIN
      SELECT string_agg(format('cryo.%I', s.rel), ', ' ORDER BY
               CASE s.rel WHEN 'tables' THEN 0 WHEN 'jobs' THEN 1
                          WHEN 'maintenance_intents' THEN 2 ELSE 3 END, s.rel)
        INTO rels
        FROM (SELECT p.child AS rel FROM ({pending}) p
              UNION SELECT p.parent FROM ({pending}) p) s;
      IF rels IS NULL THEN
        RETURN;
      END IF;
      EXECUTE 'LOCK TABLE ' || rels || ' IN ACCESS EXCLUSIVE MODE';
      FOR r IN {pending} LOOP
        {body}
      END LOOP;
      RETURN;
    EXCEPTION WHEN lock_not_available OR deadlock_detected THEN
      attempt := attempt + 1;
      IF attempt >= {LOCK_ATTEMPTS} THEN
        RAISE;
      END IF;
      PERFORM pg_sleep(least(attempt, 5));
    END;
  END LOOP;
END $$
"""


UPGRADE_SQL = _rewrite_sql(
    f"""{_CONSTRAINTS}
      AND parent.relname IN ('tables', 'jobs', 'maintenance_intents')
      AND c.confdeltype = 'a'
""",
    f"""EXECUTE format('ALTER TABLE cryo.%I DROP CONSTRAINT %I', r.child, r.conname);
        EXECUTE format(
          'ALTER TABLE cryo.%I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES cryo.%I (%I) '
          'ON DELETE %s NOT VALID',
          r.child, r.conname, r.col, r.parent, r.parent_col,
          CASE WHEN r.notnull THEN 'CASCADE' ELSE 'SET NULL' END
        );
        EXECUTE format(
          'COMMENT ON CONSTRAINT %I ON cryo.%I IS %L', r.conname, r.child, '{MARKER}'
        );""",
)

DOWNGRADE_SQL = _rewrite_sql(
    f"""{_CONSTRAINTS}
      AND obj_description(c.oid, 'pg_constraint') = '{MARKER}'
""",
    f"""EXECUTE format('ALTER TABLE cryo.%I DROP CONSTRAINT %I', r.child, r.conname);
        EXECUTE format(
          'ALTER TABLE cryo.%I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES cryo.%I (%I) '
          'NOT VALID',
          r.child, r.conname, r.col, r.parent, r.parent_col
        );
        EXECUTE format(
          'COMMENT ON CONSTRAINT %I ON cryo.%I IS %L', r.conname, r.child, '{REVERTED_MARKER}'
        );""",
)


def validate_sql(marker: str, *, clear_comment: bool) -> str:
    """VALIDATE only the constraints this revision tagged with ``marker``."""
    clear = (
        "EXECUTE format('COMMENT ON CONSTRAINT %I ON cryo.%I IS NULL', r.conname, r.child);"
        if clear_comment
        else ""
    )
    return f"""
DO $$
DECLARE r record;
BEGIN
  FOR r IN {_CONSTRAINTS}
      AND obj_description(c.oid, 'pg_constraint') = '{marker}'
  LOOP
    IF NOT r.convalidated THEN
      EXECUTE format('ALTER TABLE cryo.%I VALIDATE CONSTRAINT %I', r.child, r.conname);
    END IF;
    {clear}
  END LOOP;
END $$
"""


INDEXES = (
    ("ix_cryo_audit_log_job_id", "audit_log", "job_id"),
    ("ix_cryo_skillset_outcomes_table_id", "skillset_outcomes", "table_id"),
    ("ix_cryo_llm_observations_table_id", "llm_observations", "table_id"),
    ("ix_cryo_run_failure_diagnoses_table_id", "run_failure_diagnoses", "table_id"),
)


def _is_postgres() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _drop_indexes() -> None:
    for name, table, _column in INDEXES:
        op.drop_index(
            name, table_name=table, schema="cryo", postgresql_concurrently=True, if_exists=True
        )


def upgrade() -> None:
    if not _is_postgres():
        return
    with op.get_context().autocommit_block():
        op.execute(UPGRADE_SQL)
        op.execute(validate_sql(MARKER, clear_comment=False))
        # A build that failed earlier leaves an INVALID index that IF NOT EXISTS would keep.
        _drop_indexes()
        for name, table, column in INDEXES:
            op.create_index(
                name,
                table,
                [column],
                schema="cryo",
                postgresql_concurrently=True,
                if_not_exists=True,
            )


def downgrade() -> None:
    if not _is_postgres():
        return
    with op.get_context().autocommit_block():
        _drop_indexes()
        op.execute(DOWNGRADE_SQL)
        op.execute(validate_sql(REVERTED_MARKER, clear_comment=True))
