---
title: Contributing
description: Contributing to Cryo -- setup, the test suite, the conventions the codebase follows, and the review process.
canonical: https://tech-pw.github.io/cryo/contributing/
updated: 2026-10-26
---

# Contributing

How to propose a change to Cryo, and what happens after you open the pull request.

The full guide is [CONTRIBUTING.md](https://github.com/tech-pw/cryo/blob/main/CONTRIBUTING.md) in the repository. This page is the short version.

## Set up

You need Python 3.12+, Node 22+ (for the UI), Docker and Docker Compose. Go 1.25.5 only if you touch the native compactor. Java 17+ only if you run PySpark — the test suite does not need it.

**terminal**

```
$ git clone https://github.com/tech-pw/cryo.git
$ cd cryo
$ make install
$ make test
```

For the full local stack — a datalake with a catalog, Trino and Spark, plus Cryo's own dependencies and observability — use `./start-local.sh start`. That runs the migrations too. `make up` starts the containers only.

## Run the tests

| Command | Runs |
| --- | --- |

| `make test` | The Python suite. In-memory SQLite; it does not need the local stack. |

| `make lint` | Ruff check and format check. |

| `make typecheck` | mypy. |

| `make web-test` | The frontend suite. |

| `make test-engine-go` | The Go engine's tests. |

make test is not all the tests

CI also runs the Go engine tests — including with `-race` — and the frontend tests. A green `make test` is not a green build.

## Conventions

- Python: PEP 8, enforced by Ruff, with type hints checked by mypy. Google-style docstrings on public APIs.

- TypeScript: strict, functional components with hooks. The package has no ESLint or Prettier script — `npm run typecheck` is the check.

- SQL: keywords uppercase, identifiers lowercase with underscores.

- Commits are conventional: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `perf`.

### Migrations

Cryo's schema policy is **additive-first**. Prefer adding a column over altering a type, renaming or dropping. If a destructive change is unavoidable, write it in two revisions — add, backfill, switch, cleanup — so it can be paused between each.

- One concern per revision. A column add, an index and a constraint do not belong in one revision.

- Every `downgrade()` must restore the previous schema. If a revision is genuinely irreversible, leave the downgrade empty and say so in the docstring.

- UUID primary keys, server-side `gen_random_uuid()`.

- Everything in the `cryo` schema, and every migration operation passes `schema="cryo"`.

- No backfill data in a migration. Backfill in a separate one-off script, or in a follow-up revision gated on a column's presence.

Test the migration: upgrade head against an empty database, downgrade base, upgrade head again, and check the schema matches.

## Open the pull request

### Branch

One focused change per branch, named for the change: `feat/…`, `fix/…`, `docs/…`, `ci/…`.

### Test

Add tests for the new behaviour. A renderer change needs a golden test — an input plan and the expected SQL. Partition-predicate maths needs parity tests.

### Document

Update the docs the change makes wrong. The project brain is in `docs/`, and `todo.md` tracks what is still open.

### Open it

Describe what changed and why, and how you tested it. Link the issue it closes.

GitHub is the source of truth

Pull requests merged here are the change. There is no separate mirror to port them to.

## Review

A maintainer reviews every pull request. Address the feedback, and once it is approved it is merged. CI must be green — every check reports on every pull request, including the docs ones.

If you are unsure whether something is wanted, open an issue first. Discussion is cheaper than a pull request that gets rewritten.

## Security

Do not open a public issue for a security-sensitive finding. Use [GitHub Security Advisory](https://github.com/tech-pw/cryo/security/advisories/new), which stays private until a fix is published.

## Community

- [GitHub Issues](https://github.com/tech-pw/cryo/issues) — bugs and feature requests.

- [GitHub Discussions](https://github.com/tech-pw/cryo/discussions) — questions, design discussion and show-and-tell.

- [Code of Conduct](https://github.com/tech-pw/cryo/blob/main/CODE_OF_CONDUCT.md) — the Contributor Covenant 2.1.

## Where to go next

Source: https://tech-pw.github.io/cryo/contributing/
Markdown: https://tech-pw.github.io/cryo/contributing/index.md
