# Migrations — Squash Decision

Decision: **Do not squash** the 28-revision migration history before public launch. Rationale,
audit trail, and the alternative path are recorded here per `docs/OSS_READINESS.md` §3.

## Current state

```
$ ls migrations/versions/ | wc -l
29
# 28 revisions + 1 empty merge head (20260706_0016_merge_heads.py)
```

The history traces the build-out of a working backend and API in roughly five days. Most
revisions are small, additive (new table, new column) and reference an explicit prior revision
— they form a clean linear chain with one branchpoint (the multi-tenant + scan-config fork) and
one merge head.

## Why not squash

1. **Squashing 28 migrations is not free.** Anyone running the existing migration chain (every
   install since the first commit, every developer with a `git pull` history) would need to
   either drop their database and re-create it, or hand-merge a squashed replacement that
   produces the same schema. For a codebase that has no production installs yet this is
   survivable — but only if done **before** the first external contributor clones. Once we
   are public, doing it later is far more disruptive than doing it now.
2. **Squashing hides bug archaeology.** Three of the revisions resolve real bugs (the
   abac_policies FK constraint, the band-state column rename, the initial schema column
   default alignment). Those commit messages are searchable in `git log` after the fact —
   useful for an external reviewer tracing a column through history.
3. **Migration history is not a behavior signal for an evaluator in the same way §3 of
   `OSS_READINESS.md` suggested.** A reviewer skimming `git log` will see the commit rhythm
   immediately regardless of whether migrations are squashed — squashing does not disguise
   "this was AI-velocity-built." The honest answer is to own the velocity in the README and
   PR template, not to launder the history.
4. **The "28 migrations in 5 days" framing counts only additive changes** (most are one
   `CREATE TABLE` or one column). The schema surface area is small: one schema (`cryo`),
   one base, ~35 tables. New external contributors will not be slowed by the migration count.

## What we ship instead

- A short **"Migrations"** section in `README.md` explaining the additive-first policy,
  the schema layout (one `cryo` schema, every table uses UUID PKs), and the
  `alembic upgrade head` quick command. Removes the "is this normal?" question for a
  first-time reader.
- The **squash is preserved as an option** for a future v1.0.0 cutoff, at which point a
  single `20260101_0001_initial_baseline.py` can be generated from a freshly-built dev
  database using `pg_dump --schema-only` filtered to the `cryo` schema. The historical
  revisions remain in `git log` / GitHub history; the new install path starts from one
  file. This is a **one-line decision** documented here, not a code change.

## Cross-references

- Squashing recommendation as filed: see the PREMORTEM_ACTIONABLES.md P2 entry referenced
  from `docs/OSS_READINESS.md` §3.
- Migrations as part of the platform layering rule: `agents.md` §4 — *"Every schema change
  = an Alembic migration (additive-first)."*