# Query cost user-level attribution — follow-up plan

**Status (updated 2026-08-19, later same day):** Steps 5–6 below (signal/dataclass +
Slack/report wiring) already have visible progress in the working tree — `UserQueryCost`,
`top_users_current` on `QueryCostSignal`, a "Top users by scan cost" Slack block, and
`top_users` in `signal_to_payload` all exist as of this update. **Before writing more code
here, read the actual current state of `query_cost_signals.py` / `query_cost_slack.py` /
`query_cost_report.py` first** — this doc was written when none of that existed, so treat the
step list below as the original design intent, not a guaranteed gap list. If steps 1–4
(the actual schema migration + ingestion rewrite) are what's still missing, that's the real
remaining work — the report-layer plumbing (5–7) may already be done or in progress.

**LLM narrative layer: DONE, do not redo.** `query_cost_report.py::_generate_bottom_line`
already calls `cryo.ai.runtime.get_active_provider` + `complete_with_budget_and_observation` —
Cryo's existing Azure OpenAI provider infra, the same path `daily_report.py`'s fleet-health
bottom line uses. It's wired end-to-end into `notify_query_cost_report` →
`build_query_cost_slack_blocks(..., bottom_line=...)`, degrades to `None` cleanly with no
configured provider, and never lets the model invent a number (only pre-computed context is
passed in). This satisfies "add the LLM, since we have Azure API in the Cryo project" — no
further LLM wiring is needed for the bottom-line narrative.

**Original status note (still accurate for the schema/ingestion parts):** Planning only for
steps 1–4. Written 2026-08-19 as a scoped follow-up spun out of the Trino Query Cost Report
work (`cryo/reporting/query_cost_report.py`, `query_cost_signals.py`, `query_cost_slack.py`)
— that report currently ranks cost by **client** (`QueryCostAttributionDaily.client`, e.g.
"Metabase", "dbt-trino"), not by the individual Trino **user** who ran the query. User asked
for user-level attribution; this doc exists so the next agent/engineer can pick it up without
re-deriving the plan.

## Why this is a separate change, not a quick addition

`QueryCostAttributionDaily`'s grain is `(source_id, day, client, schema_name)` — see
`cryo/db/models.py`. Adding a user dimension means:

1. A real Alembic migration (add `user_name` column, change the unique constraint from
   `uq_query_cost_attribution_key (source_id, day, client, schema_name)` to include
   `user_name`).
2. Rewriting the live ingestion path in `cryo/telemetry/cost_attribution.py`
   (`aggregate_query_cost_attribution` / `_upsert_rollup`) — it currently reads `source`,
   `schema`, `query_state`, `inputs_json` from the external Iceberg query-log table but does
   **not** read a user column at all.
3. Updating every reader of the table's grain: `top_cost_drivers`, `bytes_scanned_by_day`,
   `apply_scan_cost_prices` (all in `cost_attribution.py`), plus
   `cryo/reporting/query_cost_signals.py::_totals_for_day` / `_top_clients_for_day`.

This touches a **live-ingesting production table's schema**, not just a report. That's a
meaningfully higher-risk change than the report itself, which is why it was scoped out rather
than done inline.

## Evidence the column exists upstream

Trino's own query-completion event listener carries a `user` field. Confirmed two ways:

- `cryo/telemetry/iceberg_source.py`'s own docstring literally names `trino_logs.trino_queries`
  as the example external table this read-through path targets (line ~70), and that exact
  table (verified directly against a live deployment in a separate cost-investigation session,
  2026-08-17/18) has a `"user"` column alongside `source`, `query_state`, `inputs_json`, etc.
- Cryo's **own** internal query-log archive schema (`cryo/telemetry/iceberg_writer.py`,
  `_build_query_log_schema`) already has a `user_name` field
  (`NestedField(field_id=7, name="user_name", ...)`), and `cryo/telemetry/ingestion.py:93`
  already does `user AS user_name` when reading live from a Trino coordinator. So `user_name`
  is already this codebase's established name for this concept — just not read by the
  `iceberg_table`-mode read-through path in `cost_attribution.py` yet.

**Caveat:** the external Iceberg query-log table's exact user-column name is
deployment-specific (whatever the operator's event listener writes — likely `"user"` per
Trino's own convention, but not guaranteed). Do not hardcode it; make it configurable the same
way `inputs_json_column` already is.

## Proposed shape (mirrors the existing `inputs_json_column` pattern exactly)

### 1. `cryo/telemetry/iceberg_source.py`

Add to `IcebergQueryLogConfig`:

```python
user_column: str | None = "user"  # optional, defaults shown; null/false disables
```

Parse it in `parse_iceberg_query_log_config` the same way `inputs_json_column` is parsed
(explicit `false`/`null` disables user-level attribution for sources whose event-listener
table doesn't have one, without disabling the whole read-through path).

### 2. `cryo/telemetry/cost_attribution.py`

- Add `config.user_column` to the `fields` tuple in `aggregate_query_cost_attribution` (only
  when configured — mirrors the existing `if config.inputs_json_column:` conditional for
  `inputs_json`).
- Extend the rollup key from `(day, client, schema_name)` to
  `(day, client, schema_name, user_name)`, defaulting to `"unknown"` when the column is
  unset/null for that row — **same fallback string `_normalize_client` already uses**, so a
  report never has to special-case a missing value differently for client vs. user.
- Extend `_upsert_rollup`'s `insert(...).values(...)` and `on_conflict_do_update` to include
  `user_name`.

### 3. Migration

New Alembic revision:

- `ALTER TABLE cryo.query_cost_attribution_daily ADD COLUMN user_name TEXT NOT NULL DEFAULT 'unknown'`
- Drop `uq_query_cost_attribution_key (source_id, day, client, schema_name)`, add
  `uq_query_cost_attribution_key (source_id, day, client, schema_name, user_name)`.
- **Backfill consideration:** existing rows have no way to know their true user split
  retroactively (the rollup already collapsed it) — they'll all land under `user_name='unknown'`
  post-migration. This is expected and should be called out in the migration's own docstring,
  not silently accepted. A prior day's `(client, schema)` totals stay correct; only the new
  `user_name` dimension starts from the migration date forward.

### 4. `cryo/db/models.py`

Add `user_name: Mapped[str]` column + update `UniqueConstraint`/`Index` as needed (a
`ix_query_cost_attribution_user` index if per-user lookups become a common query path — not
obviously needed yet, add only if a real query pattern calls for it).

### 5. `cryo/reporting/query_cost_signals.py`

- `_top_clients_for_day` → add a parallel `_top_users_for_day` (same composite-key ranking:
  priced first by $, then by bytes) grouped by `user_name` instead of `client`.
- `ClientQueryCost` → add a `UserQueryCost` dataclass (or generalize the existing one with a
  `dimension: Literal["client", "user"]` field — pick whichever reads cleaner once written, not
  worth deciding in a plan doc).
- `QueryCostSignal` → add `top_users_current: list[UserQueryCost]` alongside
  `top_clients_current`.

### 6. `cryo/reporting/query_cost_slack.py` + `query_cost_report.py`

- New Slack table block: "Top users by scan cost (yesterday)" — same shape as the existing
  "Top clients" table, placed directly after it.
- `signal_to_payload` → add a `top_users` list alongside `top_clients`.

### 7. Tests

Mirror `tests/test_query_cost_signals.py`'s existing `_top_clients_for_day` test class
one-for-one for `_top_users_for_day` (ranking-by-cost, unpriced-fallback, multi-row merge per
user). Add a migration test if this repo has a convention for that (check
`tests/` for existing Alembic-migration test patterns before assuming there isn't one).

## Related, separately-scoped gap: failure error-type breakdown

Same shape of problem, found while building the report's failure-rate signal
(2026-08-19): `QueryCostAttributionDaily` already tracks `failed_query_count`, so DoD/WoW
failure-*rate* comparison shipped in the report without a schema change. But the table has no
`error_code`/`error_type` column at all, so "top failure reasons" (which error is actually
firing) is NOT in this report yet, unlike the `internal-dbt-repo` Airflow version of this
report which has it (queries `error_code`/`error_type`/`failure_message` directly off
`trino_logs.trino_queries` — no rollup table involved there, so no schema constraint).

Adding it here would follow the exact same shape as the `user_name` plan above: extend
`IcebergQueryLogConfig` with an `error_code_column`/`error_type_column` pair (default
`"error_code"`/`"error_type"`, matching Trino's own event-listener column names), read them in
`cost_attribution.py`'s scan, and either (a) extend `QueryCostAttributionDaily`'s grain further
(getting unwieldy — day/client/schema/user/error_code is a lot of dimensions for one rollup
table) or (b) add a **separate** `QueryFailureDaily` table keyed on
`(day, error_code, error_type)` with a `sample_message` and `failure_count`, analogous to how
`cost_attribution.py`'s docstring already justifies `QueryCostAttributionDaily` being a
separate watermark/table from `iceberg_source.py`'s pattern aggregation despite reading the
same source table. Option (b) is very likely the better choice — it avoids blowing up the
existing table's cardinality for a mostly-orthogonal question ("what broke" vs. "who/what cost
how much").

Not detailed further here since there's enough overlap that the user_name migration above
should probably land first and inform the two-table-vs-one-table decision.

## Explicitly out of scope for this follow-up

- Backfilling historical user attribution for days before the migration ships (not possible —
  see backfill note above).
- Any change to `cryo/telemetry/ingestion.py`'s live-polling path (`TrinoQueryIngester`) — that
  already captures `user_name`; this plan is entirely about the separate
  `iceberg_source.py` / `cost_attribution.py` read-through path used when a Source is
  configured with `query_log.mode == "iceberg_table"`.
- Any UI/dashboard change — this plan covers the Slack report + underlying data model only.

## Suggested order of work

1. Migration + model change (steps 3–4) — land and deploy first, inert until step 2 populates
   `user_name` for real. Every existing row defaults to `'unknown'`, which is honest (no data
   loss, no silent guess).
2. Ingestion change (steps 1–2) — starts populating `user_name` going forward.
3. Wait at least 1–2 days post-(2) so there's real per-user data before building the report
   layer on top of it — otherwise step 4 ships against an all-`'unknown'` table and nobody can
   tell if it's broken or just early.
4. Signal + Slack + report changes (steps 5–7).

This ordering means each step ships independently and is individually verifiable, rather than
one large PR that's hard to bisect if something in the ingestion rewrite is wrong.

**⚠️ If steps 5–7 have landed ahead of steps 1–4** (check the current code — see the status
note at the top of this doc): the report-layer code exists but `QueryCostAttributionDaily` has
no `user_name` column yet, so `_top_users_for_day`/`top_users_current` either don't compile
against the real schema yet, or are reading/grouping by something that isn't true per-user data
(e.g. silently equal to client, or erroring). **Whoever picks this back up needs to reconcile
that gap first** — either fast-track steps 1–4 (the actual migration + ingestion rewrite) to
catch up, or confirm the report layer is correctly guarding against the column not existing yet
(returning empty `top_users_current` rather than crashing) until it does. Do not assume "the
report shows a Top Users table" means user-level attribution is real and working end-to-end —
verify against the live schema and a real ingested row before trusting it.
