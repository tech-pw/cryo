---
title: Contributing
description: How to set up a Cryo development environment, run the tests and the docs build, and get a pull request merged.
canonical: https://tech-pw.github.io/cryo/contributing/
updated: 2026-10-26
---

# Contributing

Issues and pull requests live on GitHub. Set up a dev environment, run the checks that match what you touched, and open a focused pull request against main.

This page is a summary. The full guide, with coding standards and the migration rules, is [CONTRIBUTING.md](https://github.com/tech-pw/cryo/blob/main/CONTRIBUTING.md). Everyone taking part follows the [Code of Conduct](https://github.com/tech-pw/cryo/blob/main/CODE_OF_CONDUCT.md). Report security problems privately as described in [SECURITY.md](https://github.com/tech-pw/cryo/blob/main/SECURITY.md), never in a public issue.

## How a pull request gets merged

[tech-pw/cryo](https://github.com/tech-pw/cryo) on GitHub is the source of truth. Pull requests are reviewed and merged there.

### Open an issue or pick one

For anything bigger than a small fix, open an issue first so the change can be discussed before you write it. Starter work is labelled [good first issue](https://github.com/tech-pw/cryo/labels/good%20first%20issue).

### Open a pull request

Fork, branch from `main`, and open the pull request against `tech-pw/cryo`. GitHub Actions runs lint, type checks, tests and the docs check on it.

### Review and merge

A maintainer reviews it. Keep the pull request focused on one change and link the issue it fixes with `Fixes #N`. Once checks are green and it's approved, a maintainer squash-merges it into `main`.

The "Edit this page" link at the bottom of every docs page opens GitHub's editor, and your edit becomes a pull request the same way.

## Development setup

You need Python 3.12+, Node 22 for the web UI and the docs site, Docker with Compose for the local stack, and Git. Go 1.25 is needed only for the native compactor in `engine-go/`, Rust only for `cryo-engine/`, and Java 17+ only to run PySpark.

**terminal**

```
$ git clone https://github.com/YOUR_USERNAME/cryo.git && cd cryo
$ python3.12 -m venv .venv && source .venv/bin/activate
$ make install
$ ./start-local.sh start
```

`make install` installs the package with its dev extras. `./start-local.sh start` brings up the dev datalake (MinIO, Iceberg REST, Hive Metastore, Trino, Spark), Postgres and Redis, and the observability stack, then runs the Alembic migrations. `make up` starts the containers only, without migrating or seeding. For the UI and API without the datalake, run `make api` and `make web-dev` against a local Postgres and Redis.

## Tests and checks

| Command | What it checks |
| --- | --- |
| `make test` | Python unit tests (pytest, in-memory SQLite; no Docker stack needed) |
| `make lint`, `make typecheck` | ruff lint and format check, mypy. `make format` fixes what it can. |
| `make web-quality` | Web UI type check, CSS size budget and unit tests |
| `make web-test` | Web UI unit tests only (vitest) |
| `make test-engine-go` | Go native compactor tests |
| `cargo test` in `cryo-engine/` | Rust engine tests |

`make test` isn't everything CI runs. Run the rows that match the parts you touched.

## Docs site

These pages are built from `site/_src/pages/*.html` with navigation in `site/_src/site.json`. The generator has no dependencies beyond Node 22. Commit the generated output along with your source edit; CI fails when they don't match.

**terminal**

```
$ make docs-preview
$ make docs
$ node site/scripts/build.mjs --check
```

`make docs-preview` serves the site on `http://localhost:4173` and rebuilds on save. `make docs` renders once. The changelog page comes from `CHANGELOG.md`, so rebuild the site when you add a changelog entry. More detail is in [site/README.md](https://github.com/tech-pw/cryo/blob/main/site/README.md).

## Commits and pull requests

- Use conventional commit messages: `feat(scanner): …`, `fix(planner): …`, `docs: …`, `test: …`, `refactor: …`, `chore: …`.
- Add tests for new behaviour and update the docs in the same pull request.
- Add a line under `[Unreleased]` in `CHANGELOG.md` for anything a user or operator would notice.
- Alembic migrations are additive first, one concern per revision. Read the migration section of CONTRIBUTING.md before adding one.
- Fill in the pull request template, and add screenshots for UI changes.

ChangelogWhat changed, release by release.IssuesReport a bug or propose a feature with the issue forms.

Source: https://tech-pw.github.io/cryo/contributing/
Markdown: https://tech-pw.github.io/cryo/contributing/index.md
