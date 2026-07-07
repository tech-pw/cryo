# Cryo Documentation

This folder documents the backend and local operations that have been built so far. The design
source of truth remains the root-level `plan.md`, `hld.md`, `lld.md`, and `todo.md`; these docs are
the practical guide for running and extending the current implementation.

## Contents

- `current-state.md` — what is implemented today, what is partial, and what remains.
- `local-development.md` — local stack setup, fixtures, scanner runs, and quality commands.
- `api.md` — implemented FastAPI endpoints, auth roles, request shapes, and known gaps.
- `operator-runbook.md` — common operational workflows for scanning, checking health, and smoke
  testing compaction.
- `developer-guide.md` — code layout, layering rules, tests, and implementation gotchas.
- `cost-savings.md` — implementation brief for the cost & savings-projection feature (tiered scan,
  byte accounting, per-operation savings math); pairs with `lld.md` §16.
- `ui-cost-savings.md` — frontend build spec for visualizing storage gains + cost savings in the
  health UI (current→projected waterfall, savings-by-operation, current-vs-projected by schema);
  consumes the `cost-savings.md` backend.

## Quick Commands

```bash
make up
.venv/bin/alembic upgrade head
make seed-fragmented
.venv/bin/python -m cryo.scanner.run --num-chunks 1 --full-scan
.venv/bin/python -m pytest
.venv/bin/ruff check cryo tests
.venv/bin/mypy cryo
```

Use `Authorization: Bearer viewer`, `Authorization: Bearer operator`, or
`Authorization: Bearer admin` for local API calls that require roles.
