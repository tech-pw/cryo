---
name: Pull request
about: Submit a change to Cryo
---

<!-- Thanks for sending a PR. The sections below match the parity-checklist in
     AGENTS.md §5 — answering them explicitly catches most review back-and-forth. -->

## Summary

<!-- One paragraph: what this PR does and why. -->

## Linked issue

<!-- "Fixes #123" or "Refs #123" — link the work to the problem statement. -->

## Type of change

<!-- One or more of: bug fix · new feature · refactor · docs · tests · chore -->

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to change)
- [ ] Docs / comments only
- [ ] Tests only

## Parity checklist (per `AGENTS.md` §5)

<!-- If you answered yes to any of these, the corresponding behavior must NOT change. -->

- [ ] I did **not** couple a source (read-side catalog) to a destination/engine (write-side).
- [ ] I did **not** put SQL strings in the planner (`cryo/planner/`) — Trino and Spark
      SQL generation stays in `cryo/engines/trino/sql.py` and `cryo/engines/spark/sql.py`
      respectively, each with a golden test.
- [ ] I did **not** remove or weaken the load-aware / partition-aware maintenance behavior
      that protects Trino under contention.
- [ ] I did **not** log or return any `secret_ref`, `secret_access_key`, or other credential.
- [ ] I did **not** introduce a hard dependency on the experimental native Rust engine or
      the Flink config-only engine without an explicit opt-in flag.

## Quality gates

- [ ] `make lint` (ruff check + ruff format --check)
- [ ] `make typecheck` (mypy)
- [ ] `make test` (pytest — for planner/renderer changes, golden + parity tests updated)
- [ ] `web/` side: `npm run typecheck`, `npm test`, `npm run build`

## Schema / API changes

<!-- If your PR changes a DB schema, a new Alembic migration is included
     (`migrations/versions/YYYYMMDD_NNNN_*.py`, additive-first). If it changes the API
     surface, OpenAPI is regenerated so the frontend type bundle picks it up. -->

- [ ] Migration included (or N/A — explain)
- [ ] OpenAPI regenerated (or N/A)
- [ ] No new SQL string in planner (or N/A)

## Docs updates

<!-- Per docs/OSS_READINESS.md, every behavior change must update at least one of:
     docs/, README.md, docs/design/hld.md, docs/design/lld.md. Which did you touch, and why? -->

- `docs/` updated in:
- `README.md` updated in:
- Other docs updated in:
- N/A — explain why (e.g., internal-only refactor):

## Test plan

<!-- How did you verify this works end to end? Local commands, screenshots, query output —
     anything a reviewer can reproduce from a clean checkout. -->

## Risk and rollback

<!-- What's the worst that can happen if this ships? How do we back it out? -->