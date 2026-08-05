# Cryo

**Self-hosted Apache Iceberg maintenance and health platform.**

Cryo scans every table in your lake, grades it A–F, tells you exactly what maintenance will cost
and save, and lets you run that maintenance — on Trino or Spark, from a UI or an API — without
writing another Airflow DAG or dbt macro. One system for compaction, snapshot expiry, orphan-file
cleanup, cost tracking, and data-quality monitoring, deployable to Kubernetes.

[![CI](https://github.com/shelf-project/Cryo/actions/workflows/ci.yml/badge.svg)](https://github.com/shelf-project/Cryo/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![Backend](https://img.shields.io/badge/backend-FastAPI%20%2B%20PostgreSQL-informational)
![Frontend](https://img.shields.io/badge/frontend-React%2019%20%2B%20TypeScript-informational)
![License](https://img.shields.io/badge/license-Apache%202.0-blue)

## Table of contents

- [Why Cryo](#why-cryo)
- [How it compares](#how-it-compares)
- [Features](#features)
- [Quick start](#quick-start)
- [Architecture](#architecture)
- [Research foundations](#research-foundations)
- [Documentation](#documentation)
- [Tech stack](#tech-stack)
- [Local development](#local-development)
- [Contributing](#contributing)
- [License](#license)

## Why Cryo

Most teams running Apache Iceberg at any real scale end up with the same pile of infrastructure:
an Airflow DAG that scans tables and computes health stats, a second DAG that decides what to
optimize and shells out to per-table dbt subprocesses, and a set of dbt macros carrying the actual
maintenance logic — partition-aware chunking, load-aware throttling, cooldowns, drift detection.
None of it shares state. None of it has a UI. Running maintenance on one table right now means
hand-editing a macro's arguments and hoping you got the flags right.

Cryo replaces that pile with one deployable product:

- **One scanner** produces the stats, health grade, and cost estimate that everything else reads —
  the planner, the dashboards, the alerts, all from the same source of truth.
- **One planner**, engine-agnostic, that renders to either Trino SQL or Spark SQL instead of
  hard-wiring maintenance to a single engine.
- **One governor** that watches live engine load and admits work accordingly, so scaling workers
  doesn't silently scale concurrent `OPTIMIZE` jobs into a Trino outage.
- **One UI and API** for everything above — assigning maintenance policies, triggering ad-hoc
  runs, watching a live run console, and seeing exactly what's broken across the fleet and why.

It's designed for fleets in the thousands of tables, because that's the scale it was built
against.

## How it compares

| | Cryo | DIY (Airflow + dbt) | Apache Amoro | LakeOps | Managed cloud (Databricks / S3 Tables / Snowflake) |
|---|---|---|---|---|---|
| Deployment | Self-hosted (Kubernetes) | Self-hosted, hand-rolled | Self-hosted | SaaS | Fully managed |
| Engines | Trino **and** Spark/EMR | Whatever you wired up | Flink/Spark, Mixed-Iceberg | Own Rust engine + telemetry from Trino/Spark/Snowflake/DuckDB/Flink | Locked to the platform |
| Load-aware admission control | Yes — reads live engine load before admitting work | Only if you built it | Limited | Yes | N/A (fully managed) |
| Table health grading (A–F) + cost projection | Yes, built in | No — usually a one-off script | No | Partial | Varies by platform |
| Data leaves your infra | No | No | No | Yes — telemetry goes to LakeOps | Yes — data lives on the platform |
| UI for fleet-wide assignment | Yes | No | Limited | Yes | Yes |
| Cost | Free, self-hosted | Free, but you build and own everything | Free, self-hosted | Paid SaaS | Usage-based, platform lock-in |

Cryo sits where **LakeOps** does — a dedicated, self-hosted control plane rather than a managed
platform — but keeps your telemetry on your own infrastructure, and treats Trino as a first-class
engine rather than an afterthought. Against **Apache Amoro**, Cryo is lighter to operate (no
Mixed-Iceberg coupling) and Trino-first rather than Flink-first. Against the managed clouds
(Databricks Predictive Optimization, AWS S3 Tables, Snowflake), Cryo is the answer for teams who
aren't consolidating onto a single vendor's compute.

### Who this isn't for (yet)

- **Multi-tenant SaaS operators.** Tenant isolation is opt-in (`Settings.enforce_tenant_isolation`),
  not default. Run one instance per customer until that switches.
- **Flink-primary streaming shops.** The Flink engine ships config generation only; it does **not**
  submit jobs to a cluster (see `cryo/engines/flink/engine.py` module docstring).
- **Teams wanting a zero-config managed product.** This is a thing you operate — Kubernetes,
  Postgres, Redis, an Iceberg catalog — not a SaaS you sign up for.
- **Anyone needing the native Rust engine for production.** It's experimental, off by default,
   and not exercised by CI (see [`architecture.md` §1.5](./docs/architecture.md) and
   `cryo/engines/native/engine.py`). Use Trino or Spark.
- **Heavy "advanced" / AI / FinOps / tiering / anomaly dashboards out of the box.** These are
  partially implemented and become useful only once query-log ingestion is enabled (`Settings`
  → `telemetry_*`). Until then, expect empty charts, not broken ones.

## Features

### Maintenance
- **Sources (read)** connect Iceberg catalogs — REST, Hive Metastore, or Glue — and discover
  tables via PyIceberg, configured independently of write engines.
- **Destinations (write)** submit maintenance to **Trino** or **Spark on EMR**, each with its own
  SQL renderer. The planner itself emits no SQL — it stays engine-agnostic.
- **Planner** walks pre-computed scan data (no live `$files` reads on hot paths), GB-budgets
  chunks, caps partitions-per-chunk to avoid Trino `FilterNode` blowups, and applies cooldowns,
  off-peak windows, adaptive budgets, and deadlines.
- **Governor** is the global, load-aware admission controller — reads Trino
  `system.runtime.queries` to bound real concurrency, applies per-replica caps with round-robin,
  and tunes thresholds per destination with a contextual-bandit policy.
- **Presets** bind *operations + params + target engine + schedule + selection rules* into a
  named profile, e.g. `spark-large = "tables > 100 GB → Spark/EMR, deep maintenance weekly"`.
- **Assignment UX**: filter the fleet by schema, size, small-file ratio, grade, or drift, select
  every matching table across pages, and assign a preset in one action — or let rule-based
  auto-matching do it, with manual override.

### Health & observability
- **Scanner** runs chunked, resumable datalake scans, producing per-table and per-partition
  stats, an A–F health grade, `needs_compaction` priority, and an S3 cost estimate.
- **Dashboards** for datalake and per-table health, audit timelines, drift/cost alerts, scheduled
  email/Slack summaries, and a WebSocket-backed live run console.
- **Anomaly detection** across the fleet for null-rate, size, file-count, and small-file-ratio
  drift, with history for baseline comparison.
- **AWS cost & inventory**: opt-in S3 size/object-count from CloudWatch and CUR/inventory feeds,
  so the platform quotes real storage spend per source.
- **Owner and consumer alerting**: tables carry an owner and subscribed consumers; SLA or
  data-quality breaches email (and optionally Slack) the people who'd actually notice.

### Operations
- **Scheduler / Governor / Worker** are leader-elected, separate Kubernetes deployments — scaling
  workers does not automatically scale parallel `OPTIMIZE`s.
- **Per-table locks** prevent double maintenance. **Failure isolation** means a bad table doesn't
  fail the run — it shows up in the audit log and the failed-tables panel, with a
  `consecutive_scan_failures` streak and last error tracked per table.
- **Audit log** captures every action: table, action, status, detail, engine, duration, file
  counts, actor.
- **Auth** is JWT-based with optional RS256 OIDC, ABAC enforcement, and per-tenant isolation.
  Every API route is wired to a role decorator (`viewer` / `operator` / `admin` / `editor`).

## Status (read this before adopting)

Cryo is **pre-1.0** (`v0.x` SemVer). The **launch-tier surface** — scanner, health grading,
planner, governor, Trino/Spark engines, presets + assignment, bulk run, audit log, run
console, JWT/OIDC auth — is real and tested (379 backend + 58 frontend tests). The
**advanced** / **AI / FinOps / tiering / anomaly** tiers are partially implemented and
depend on opt-in query-log ingestion to populate; see `docs/OSS_READINESS.md` §2.4 for
which surfaces are launch-tier versus behind-flags.

For an honest accounting of what's solid, what's partial, and what isn't shipped yet, see
[`docs/current-state.md`](./docs/current-state.md). For the production-readiness reasoning
backing this `v0.x` floor, see [`docs/premortem.md`](./docs/premortem.md).

## Quick start

Requires Docker, Python 3.12+, and Node 22+.

```bash
git clone https://github.com/shelf-project/Cryo.git
cd Cryo

make up      # Postgres, Redis, MinIO, Iceberg REST/HMS catalogs, Trino, Cryo API + web
make seed    # create demo Iceberg tables with intentional maintenance issues
```

Open **http://localhost:5173**, sign in with any username (dev auth issues a JWT locally; real
deployments wire up OIDC — see [`docs/DEPLOYMENT.md`](./docs/DEPLOYMENT.md)), and you'll land on
the datalake health dashboard for the tables `make seed` just created.

From there: **Tables** to explore the fleet and filter/assign presets, **Scanner** to trigger a
scan on demand, **Runs** to watch maintenance execute live.

## Architecture

```
Sources (read)            Cryo platform                              Destinations (write)
─────────────             ───────────────────────────               ────────────────────
Iceberg REST  ─┐          Scanner ─► Postgres (stats/health)
Hive / Glue   ─┼─ read ─► Scheduler ─► Governor ─► Workers ─ exec ─► Trino (SQL)
                │          Planner (partition-aware, engine-agnostic)        Spark on EMR (Spark SQL)
                │          API (FastAPI) ── Web UI (React)
                └────────► Redis (queue / locks / live WS)  ·  S3 / MinIO (data)
```

Sources and destinations are independent: a table is *discovered* from a source, then *maintained*
on whichever destination its preset targets. Full diagrams and component responsibilities in
[`hld.md`](./hld.md).

## Research foundations

Several features are inspired by or directly implement recent papers. The claim of "parity
tests" means there is at least one test exercising the core logic of each paper's contribution.

| Paper | Feature | Where it lives |
|---|---|---|
| [arXiv:2504.04186](https://arxiv.org/abs/2504.04186) — *AutoComp* (SIGMOD 2025, LinkedIn) | Compaction candidate ranking by small-file count, skew, and benefit score | `cryo/scanner/compaction_candidates.py`, `cryo/planner/benefit_score.py` |
| [arXiv:2510.09567](https://arxiv.org/abs/2510.09567) — *Proof-Carrying AI Agents* | Plan Verifier: statically checks a plan against the live catalog before enqueue | `cryo/planner/verifier.py`, `POST /plans/verify` |
| [arXiv:2511.02674](https://arxiv.org/abs/2511.02674) — *EasyTUS* | Embedding-based unionable-table search across the fleet | `cryo/ai/union_search.py`, `GET /tables/union-search` |
| [arXiv:2512.02795](https://arxiv.org/abs/2512.02795) — *Observation Lakehouse* (SANER 2026) | Append-only archive of every actuation, for audit and replay | `cryo/observability/observation_lakehouse.py` |
| [arXiv:2604.20081](https://arxiv.org/abs/2604.20081) — *Silent Data Loss* (inspired by) | Detector for orphan data files and missing committed files | `cryo/scanner/silent_data_loss.py`, `cryo/scheduler/health.py` |
| [arXiv:2605.30308](https://arxiv.org/abs/2605.30308) — *Zero-Scan Data Quality* (FORMATS '26) | Per-column null rates, value bounds, and drift from manifest metadata — no data scans | `cryo/scanner/quality.py`, `cryo/scanner/puffin_writer.py` |
| [arXiv:2606.01185](https://arxiv.org/abs/2606.01185) — *Skill Issues* (inspired by) | Versioned AI skillsets (prompt + tool registry) with outcome-based promotion | `cryo/ai/skillset.py` |
| [arXiv:2606.03464](https://arxiv.org/abs/2606.03464) — *Variance-aware QPP* (CLOUD 2026) | Query performance prediction; the Governor refuses jobs predicted to blow p95 | `cryo/governor/qpp.py`, `cryo/governor/governor.py` |

## Documentation

| Doc | What's in it |
|---|---|
| [`plan.md`](./plan.md) | Vision, scope, architecture decisions, phased roadmap |
| [`hld.md`](./hld.md) | High-level design: components, data flow, deployment topology |
| [`lld.md`](./lld.md) | Low-level design: schema, interfaces, planner algorithm, API surface |
| [`agents.md`](./agents.md) / [`AGENTS.md`](./AGENTS.md) | Guardrails for contributors and AI agents, including the parity checklist |
| [`todo.md`](./todo.md) | Phased, checkbox task breakdown (P0–P7) |
| [`docs/`](./docs/) | Execution plan, recommendations, competitive analysis — start at [`docs/EXECUTION_PLAN.md`](./docs/EXECUTION_PLAN.md). Full index in [`docs/README.md`](./docs/README.md) |
| [`docs/local-docker-compose.md`](./docs/local-docker-compose.md) | Three-file Compose layout (Cryo / dev datalake / observability) |
| [`docs/SUPPORT_POLICY.md`](./docs/SUPPORT_POLICY.md) | Supported releases, triage severity, channels, and safe diagnostic bundle |
| [`docs/EXTERNAL_EVIDENCE_ATTESTATION.md`](./docs/EXTERNAL_EVIDENCE_ATTESTATION.md) | Signed external gate evidence and release-dossier trust boundary |
| [`docs/ECOSYSTEM_CONNECTORS.md`](./docs/ECOSYSTEM_CONNECTORS.md) | Versioned ecosystem source/engine conformance contract |
| [`docs/RATING_2026.md`](./docs/RATING_2026.md) | 2026-07-07 deep rating of the codebase across 25 attributes (UI / a11y / usability / engineering / experience) |

## Tech stack

- **Backend** — Python 3.12, FastAPI, SQLAlchemy 2.0 + Alembic (PostgreSQL + pgvector),
  PyIceberg, trino-python-client, boto3 (EMR Serverless), Redis, structlog.
- **Frontend** — React 19 + TypeScript (Vite), Radix UI primitives, Tailwind CSS, Recharts,
  TanStack Table, and TanStack Query. Types are generated from the backend's OpenAPI schema
  (`npm run gen:api`).
- **AI** — pgvector for table/column embeddings; a pluggable provider interface (Anthropic /
  OpenAI / local); versioned skillsets for prompts and tools.
- **Observability** — OpenTelemetry tracing, Prometheus metrics, Loki logs.
- **Deploy** — Docker images + a Helm chart per service (`api`, `web`, `scheduler`, `governor`,
  `worker`; `scanner` as a `CronJob`), with Postgres and Redis as dependencies.

## Local development

```bash
# Full local stack (datalake + Cryo deps + observability)
make up                 # all three compose files
make up-cryo             # Cryo platform only (prod-shaped)
make up-datalake         # MinIO + catalogs + Trino (LOCAL/CI only)
make down                # stop full local stack
make seed                # create demo Iceberg tables (scripts/)

# Backend
make install             # uv/pip install -e .[dev]
make migrate             # alembic upgrade head
make scan                # run scanner once against the local source
make api                 # run FastAPI (uvicorn) locally
make worker              # run a worker
make governor            # run the governor
make scheduler           # run the scheduler

# Quality (run before finishing any change)
make lint                # ruff check + ruff format --check
make typecheck           # mypy
make test                # pytest (unit + parity); add -k for a subset

# Frontend
cd web && npm install && npm run dev      # Vite dev server
cd web && npm run build && npm run test
```

Service endpoints once `make up` is running:

| Service | URL |
|---|---|
| Cryo Web | http://localhost:5173 |
| Cryo API | http://localhost:8000 |
| MinIO Console | http://localhost:9001 (minioadmin / minioadmin) |
| Iceberg REST catalog | http://localhost:8181 |
| Trino | http://localhost:8080 |
| Postgres | localhost:5432 (`cryo` / `cryo` / `cryo`) |
| Grafana | http://localhost:3000 (admin / admin) |

```bash
# Query the demo lake directly
docker exec -it cryo-trino trino --execute "SHOW SCHEMAS FROM iceberg"
docker exec -it cryo-trino trino --execute "SELECT * FROM iceberg.demo.customers"
```

## Contributing

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for the full setup guide, and read
[`AGENTS.md`](./AGENTS.md) before opening a PR — the non-negotiables are the parity checklist
(§5), the sources/destinations separation, separate Trino/Spark SQL renderers with golden tests,
and an Alembic migration for every schema change.

## License

[Apache License 2.0](./LICENSE). Copyright 2026 shelf-project.

By contributing, you agree your contributions are licensed under the same Apache-2.0 terms.
See [CONTRIBUTING.md](./CONTRIBUTING.md) and [docs/GOVERNANCE.md](./docs/GOVERNANCE.md).
