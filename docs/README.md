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
- `chat-b413e9e0-build-log.md` — implementation log for the referenced Cursor chat transcript.

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
