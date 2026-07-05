# Migration review checklist

Use this before merging any Alembic revision.

## Pre-merge checks

1. **Schema consistency** — all new tables/columns use `schema="cryo"` unless intentionally public.
2. **Column exists** — `add_column` / `alter_column` targets a column that exists in the prior revision.
3. **No duplicate DDL** — same column is not added in two parallel branches without a merge revision.
4. **Downgrade** — `downgrade()` reverses `upgrade()` for additive changes where feasible.
5. **ORM parity** — SQLAlchemy models in `cryo/db/models.py` match the migration.
6. **Data backfill** — one-off `UPDATE` statements are idempotent and batched for large tables.
7. **Extension deps** — Postgres extensions (`vector`, etc.) use `CREATE EXTENSION IF NOT EXISTS`.
8. **Index naming** — indexes are named explicitly; HNSW/IVFFlat params documented in migration comment.

## Squash procedure (pre-production only)

When no long-lived production DB exists on the old chain:

1. On a clean DB: `alembic upgrade head`
2. Generate baseline: `alembic revision --autogenerate -m "baseline_squash"`
3. Move old revisions to `migrations/versions/archive/`
4. Set new baseline `down_revision = None`
5. Existing dev DBs at old head: `alembic stamp <new_baseline_rev>` (no DDL replay)

**Do not squash** once production databases have divergent migration history without a coordinated cutover.
