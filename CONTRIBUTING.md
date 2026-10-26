# Contributing to Cryo

Thank you for your interest in contributing to Cryo! This document provides guidelines
and information for the contributor.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [How to Contribute](#how-to-contribute)
- [Pull Request Process](#pull-request-process)
- [Coding Standards](#coding-standards)
- [Community](#community)

## Code of Conduct

This project follows [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Enforcement contact is not
published yet. See [SECURITY.md](SECURITY.md). Process notes are in
[docs/GOVERNANCE.md](docs/GOVERNANCE.md).

## Getting Started

### Prerequisites

- Python 3.12+
- Node 22+ (UI only; CI pins 22)
- Docker and Docker Compose
- Go 1.25.5 (`engine-go/go.mod`) if you touch the native compactor
- Java 17+ only if you run PySpark. `make test` does not need it
- Git

### Quick Start

```bash
# Clone the repository
git clone https://github.com/tech-pw/cryo.git
cd cryo

# Install dev dependencies, then run tests
make install
make test
```

## Development Setup

### 1. Create a Virtual Environment

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
```

### 2. Start Infrastructure

```bash
./start-local.sh start
```

`make up` starts containers only. It does not migrate or seed. `./start-local.sh start` runs
Alembic. Default pytest uses in-memory SQLite and does not need that stack.

UI and API without the datalake: `START_DATALAKE=0 SEED_CONFIG=0 START_SERVE=0 ./start-local.sh start`,
or `make api` plus `make web-dev` against local Postgres and Redis. Login, empty dashboards,
tables, and runs still render. Scan and maintenance need a catalog or engine.

This starts three compose stacks — see [`docs/local-docker-compose.md`](docs/local-docker-compose.md):

- **Dev datalake** (`docker-compose.dev-datalake.yml`): MinIO, Iceberg REST, HMS, Trino, Spark
- **Cryo platform** (`docker-compose.yml`): PostgreSQL, Redis (+ optional Cryo containers)
- **Observability** (`docker-compose.observability.yml`): Prometheus, Grafana, Loki, Promtail

When adding new local-only scripts or compose services, update
[`docs/LOCAL_DEV_ONLY_INVENTORY.md`](docs/LOCAL_DEV_ONLY_INVENTORY.md) in the same PR.

### 3. Run Tests

```bash
# Python unit tests. Not Go, Rust, or the UI.
make test

# One test
pytest tests/test_scanner.py -k test_name -v

# Coverage
pytest --cov=cryo

# Go engine (matches CI, except CI also runs -race)
make test-engine-go

# One Go test
cd engine-go && CGO_ENABLED=0 go test -v ./internal/ops -run TestName
```

`make test` is not "all tests". CI also runs `cargo test` and `make web-test`.
Opt-in stacks: `make test-hms-integration`, `make test-native-e2e-local-go`.

### 4. Code Quality

```bash
# Linting
make lint

# Type checking
make typecheck

# Format code
make format
```

## How to Contribute

### Types of Contributions

We welcome many types of contributions:

- **Bug reports** — Found a bug? Open an issue with details
- **Questions** — Ask in [GitHub Discussions](https://github.com/tech-pw/cryo/discussions) (Q&A)
- **Feature requests** — Have an idea? Start a thread in [Discussions → Ideas](https://github.com/tech-pw/cryo/discussions/categories/ideas), or open a feature issue once it's concrete
- **Documentation** — Improve docs, fix typos, add examples
- **Code** — Fix bugs, implement features, improve performance
- **Tests** — Add test coverage, improve test quality
- **Reviews** — Review pull requests from other contributors

### Finding Issues to Work On

- Look for issues labeled [`good first issue`](https://github.com/tech-pw/cryo/labels/good%20first%20issue)
- Check [`help wanted`](https://github.com/tech-pw/cryo/labels/help%20wanted) for more complex tasks
- Comment on an issue to let others know you're working on it

### Before You Start

1. **Check existing issues** — Your idea might already be discussed
2. **Open an issue first** — For significant changes, discuss before coding
3. **One thing at a time** — Keep PRs focused on a single change

## Pull Request Process

### 1. Fork and Branch

```bash
# Fork on GitHub, then:
git clone https://github.com/YOUR_USERNAME/cryo.git
cd cryo
git remote add upstream https://github.com/tech-pw/cryo.git

# Create a feature branch
git checkout -b feature/your-feature-name
```

### 2. Make Your Changes

- Write clear, readable code
- Add tests for new functionality
- Update documentation as needed
- Follow the coding standards below

### 3. Commit Your Changes

We use conventional commits:

```bash
# Format: <type>(<scope>): <description>

git commit -m "feat(scanner): add delete file tracking"
git commit -m "fix(planner): handle empty partitions"
git commit -m "docs: update installation guide"
git commit -m "test(api): add preset assignment tests"
```

**Types:**
- `feat` — New feature
- `fix` — Bug fix
- `docs` — Documentation only
- `test` — Adding or updating tests
- `refactor` — Code change that neither fixes a bug nor adds a feature
- `chore` — Maintenance tasks

### 4. Push and Create PR

```bash
git push origin feature/your-feature-name
```

Then create a Pull Request on GitHub with:
- Clear title describing the change
- Description of what and why
- Link to related issue(s)
- Screenshots/examples if applicable

### 5. Review Process

- Maintainers will review your PR
- Address any feedback
- Once approved, a maintainer will merge

## Coding Standards

### Python

- **Style:** Follow PEP 8, enforced by Ruff
- **Types:** Use type hints, checked by mypy
- **Docstrings:** Google style for public APIs
- **Tests:** pytest, aim for >80% coverage on new code

```python
def analyze_table(
    table_obj: Table,
    schema_name: str,
    *,
    scan_at: datetime | None = None,
) -> TableAnalysis:
    """Analyze an Iceberg table's health metrics.

    Args:
        table_obj: PyIceberg table instance
        schema_name: Schema/namespace name
        scan_at: Optional timestamp for the scan (defaults to now)

    Returns:
        TableAnalysis with health grade, file stats, and recommendations

    Raises:
        ValueError: If table_obj is not a valid Iceberg table
    """
```

### TypeScript (Frontend)

- **Style:** 2-space indent, double quotes, semicolons. This package has no ESLint or Prettier script. `npm run typecheck` is the check.
- **Components:** Functional components with hooks
- **State:** TanStack Query for server state

### SQL

- **Keywords:** UPPERCASE
- **Identifiers:** lowercase with underscores
- **Formatting:** One clause per line for complex queries

### Git

- Keep commits atomic and focused
- Write meaningful commit messages

### Migrations (Alembic)

Before merging any Alembic revision:

- **Schema additions only** — Cryo's policy is additive-first. Prefer adding columns (nullable
  or with sensible defaults) over altering types, renaming, or dropping. If a destructive
  change is unavoidable, write the migration in two revisions (additive → backfill → switch
  → cleanup) so it can be paused between each.
- **One concern per migration** — don't bundle a column add + an index + a constraint into a
  single revision. Each revision should be deployable in isolation.
- **Reversible** — every revision's `downgrade()` must restore the previous schema. If a
  migration is genuinely irreversible, leave `downgrade()` empty and call it out in the
  docstring.
- **UUID primary keys** — every table uses `UUID(as_uuid=True)` PKs, server-side
  `gen_random_uuid()` default.
- **Schema-qualified** — every table lives in the `cryo` schema; every operation in the
  migration file passes `schema="cryo"`.
- **Naming** — `<YYYYMMDD>_<HHMM>_<slug>.py`, slug describes the change (`query_log_config`,
  `table_alert_ownership`). Revision ID is the filename without the `.py`.
- **Test the migration** — write a test that runs `upgrade head` against an empty DB, then
  `downgrade base`, then `upgrade head` again, asserting no errors and that the schema
  matches expectations.
- **No backfill data in migrations** — if existing rows need values, do it in a separate
  one-off script run from CI before the migration lands, or in a follow-up revision gated
  by a column-presence check.

The full migration history is preserved (not squashed) per the decision recorded in
[`architecture.md` Part 5](./docs/architecture.md#part-5--migration-policy).
- Rebase before merging (squash if needed)

## Project Structure

```
cryo/
├── cryo/                 # Python backend
│   ├── api/              # FastAPI routers
│   ├── db/               # SQLAlchemy models, repositories
│   ├── domain/           # Domain models, enums
│   ├── engines/          # Trino/Spark SQL renderers
│   ├── governor/         # Admission control
│   ├── planner/          # Maintenance planning
│   ├── scanner/          # Table health scanning
│   └── scheduler/        # Job scheduling
├── engine-go/            # Default native compactor (Go)
├── web/                  # React frontend. See web/README.md
├── tests/                # Test suite
├── docs/                 # Documentation
├── scripts/              # Utility scripts
└── deploy/               # Deployment configs
```

## Community

- **GitHub Issues** — Bug reports and feature requests. Use the forms.
- **Questions** — See [docs/SUPPORT_POLICY.md](docs/SUPPORT_POLICY.md).
- **Pull Requests** — Code contributions
- **Security** — [SECURITY.md](SECURITY.md). Do not open a public issue.

## Recognition

Contributors are recognized in:
- Release notes for significant contributions
- The project README for major contributors

## License

By contributing, you agree that your contributions will be licensed under the
Apache License 2.0.

---

Thank you for contributing to Cryo!
