# Backend Architecture

Cryo separates metadata reads from maintenance execution.

```text
Sources          Scanner             Postgres
Iceberg REST ->  PyIceberg scan  ->   tables / stats / partitions / history

Presets + rules + assignments  ->  Scheduler / API effective-preset resolution

Governor  ->  Queue + locks + live load probes  ->  Worker  ->  Engine
                                                     Planner -> Trino/Spark renderer
```

## Source Side

Sources answer where table metadata is read from. The current implemented source adapter is REST:

- `cryo/sources/base.py`
- `cryo/sources/rest.py`
- `cryo/sources/factory.py`

Scanner output is persisted into Postgres and becomes the input for table explorer, health views,
preset rules, and planner decisions.

## Destination Side

Destinations answer where maintenance executes. Current destination work includes:

- Trino client and live load probe.
- Trino statement execution wrapper.
- Spark SQL renderer, but not yet a Spark execution adapter.

Sources and destinations are intentionally separate. A table can be discovered from one source and
maintained through a destination selected by its effective preset.

## Planner

The planner is engine-agnostic:

- It consumes latest table stats and partition rows.
- It produces `MaintenancePlan` operations with structured predicates.
- It does not generate SQL.

Renderers are engine-specific:

- Trino SQL lives in `cryo/engines/trino/sql.py`.
- Spark SQL lives in `cryo/engines/spark/sql.py`.

## Predicate Handling

API filters, assignment filters, and preset rules share one predicate grammar in
`cryo/api/predicates.py`.

The same JSON predicate can be:

- compiled to SQLAlchemy for database filtering
- evaluated in memory for scheduler rule matching

This prevents UI/API selection from diverging from scheduled rule resolution.

## API Layer

`cryo/api/routes.py` is currently a single router with the P4 surface. As the project grows, it can
be split into `cryo/api/routers/` by area:

- connectivity
- tables
- presets/rules
- assignments
- maintenance
- runs/jobs
- dashboards

Route handlers should stay thin and delegate DB/domain behavior to repository helpers.

## Orchestration

The orchestration core is implemented as libraries and tested with in-memory stores:

- `cryo/scheduler` resolves preset rules and builds scheduled run drafts.
- `cryo/governor` admits jobs based on load, locks, cooldown, off-peak windows, and deadlines.
- `cryo/worker` builds plans, renders statements, executes an engine, and records audits.
- `cryo/queue` provides Redis and in-memory queue/lock implementations.

Production long-running service loops and complete DB-backed service wiring are still in progress.

## Data Flow For A Scan

1. Load enabled sources from Postgres.
2. Use the source adapter to list tables.
3. Assign tables to a scanner chunk deterministically.
4. Load each Iceberg table through PyIceberg.
5. Analyze file, partition, snapshot, manifest, and property metadata.
6. Upsert latest table stats and append history.

## Data Flow For A Maintenance Preview

1. API receives table stats and partition rows.
2. Planner builds an engine-agnostic `MaintenancePlan`.
3. Trino or Spark renderer turns operations into SQL.
4. API returns rendered statements and planner notes.
5. No execution happens during preview.

## Data Flow For A Scheduled Run

This flow is partially implemented:

1. Scheduler resolves effective presets.
2. Scheduler creates run/job drafts.
3. Queue receives pending jobs.
4. Governor admits jobs within capacity.
5. Worker claims admitted jobs and executes.
6. Audit and job status are persisted.

The core components are present, but the production service loop and full DB-backed end-to-end flow
still need completion.
