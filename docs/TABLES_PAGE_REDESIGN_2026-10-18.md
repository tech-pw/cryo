# Tables page redesign (2026-10-18)

The `/tables` page (`web/src/pages/Catalog.tsx`) used to answer "how big and how healthy is each
table". It now answers the operator's real question first: **what did maintenance do last night,
and what needs me?**

## Layout

1. **Header**: title "Tables", one-line purpose, and a pill showing the last nightly run status
   that links to `/runs/{id}`.
2. **Triage cards** (`CatalogDashboard.tsx`): Failed last run, Queued or running, Skipped with
   work, Health dropped (score fell 10+ points in 7 days), Scan issues (failing, never scanned, or
   stale for 48h+). Clicking a card toggles the matching view in the explorer below.
3. **Maintenance funnel**: a Sankey of the latest nightly batch (`FlowSankey` in
   `components/charts/`, mapped by `pages/catalog/MaintenanceFunnelSankey.tsx`). Clickable stages
   filter the table.
4. **Catalog card**: compact list (integer score, tables · size · cost/mo · saves). Clicking a row
   sets the `catalog` URL param.
5. **Scanner lag banner** when stale scans make up at least 10% of active tables.
6. **Explorer**: quick-view chips with counts, then the filter bar, which has a visible name search,
   facet filters (Catalog, Workflow, Grade, Priority), a "More filters" popover, "Active only",
   "Ask in plain English", and right-aligned actions (group by, columns, saved views, export,
   refresh). Below that sit the bulk bar and the table.

## Funnel semantics

- It covers the **latest nightly batch run** (`latest_batch_run`), not all time. The lookup
  filters `summary->>'pipeline' = 'batch'` in SQL. The first version scanned only the newest 20
  runs, and on prod, ad-hoc scans and manual runs pushed the nightly run out of that window, so
  the card showed "No nightly batch has run yet" (fixed 2026-10-21). The Decisions funnel
  (`/decisions/funnel`) uses the same helper.
- `last_scan_error_kind` must stay in `EXPLORER_FIELDS`. The Scan issues view filters on it,
  and without it the count request returns 400 and the card shows "—".
- It counts **per table, not per operation**. A table with six ops counts once.
- Stages: active → scanned / scan failing / not scanned → needs work / no work needed →
  skip reasons (cooldown, over budget, not eligible, held, excluded…) or selected →
  succeeded / failed / in progress / not run.
- Clicking a stage opens the explorer view for the tables in that state **now**. Current state
  can differ from what happened last night (for example, a table that has been rescanned since).
- Flow is conserved at every node; `tests/test_tables_maintenance_view.py` asserts this.

## Backend

- `cryo/api/table_maintenance.py`:
  - `derive_state` has a fixed priority: inactive → running → queued → excluded → held →
    scan_failing → not_scanned → failed/partial → skipped → needs_work → healthy.
  - `maintenance_summaries` fills the per-row `maintenance` block with bulk window-function
    lookups (no N+1).
  - `tables_funnel` is a single GROUP BY.
- Migration `20261018_1200_jobs_table_finished_index` adds `ix_jobs_table_finished (table_id,
  finished_at)` for the last-job lookups.
- Computed predicate fields (`COMPUTED_FIELDS`, `EXPLORER_FIELDS` in `predicates.py`) are
  **explorer-only**. They are deliberately kept out of `SUPPORTED_FIELDS` because preset rules
  are evaluated in Python against stats rows.

## Frontend notes

- Triage views (`TRIAGE_VIEW_IDS` in `lib/tableFilters.ts`) are predicates, so counts come from
  `limit: 1` `listTables` calls (`useTriageCounts`). The badges and the table always agree.
- View, catalog, sort and page round-trip through the URL.
- Default columns: Table, Health (grade, score, 7-day delta), Maintenance (state and reason),
  Last run, Small files (ratio bar), Size, Reads 30d, Savings/mo. Workflow, Owner, Last scan and
  others are opt-in from the grouped Columns menu. Storage keys: `columns v4`,
  `cryo.catalog.columnSizing.v2`.
- The Savings view forces savings-first column order. The columns menu explains why it is locked.
- Rows show hover quick actions: Scan now, and open the maintenance tab.
- Bulk bar layout, left to right:
  - Count and "Select all N matching" (sends a filter spec).
  - Assign workflow.
  - Preview plan and Scan.
  - "Run maintenance…" dialog. It holds the workflow picker, the single operation picker (full
    workflow or manifests only) and the engine picker. It warns when selected tables will likely
    be skipped.
  - Destructive "Remove workflow pins" behind the ⋯ menu with an AlertDialog.
- Empty savings render as `-`, and sub-dollar savings as `<$1`.

## Mock data

`web/e2e/docs-capture/demo-data.ts` carries maintenance blocks, reads, savings, owners and
`demoTablesFunnel()`, so the page can be screenshotted without prod. The demo Trino destination
is bound to the demo catalog; without a binding, `filterPresetsForSources` hides every Trino
workflow from the bulk bar.

## Docs site

`site/_src/pages/tables.html` (Operate → Tables) walks through the page section by section. Its
`tables-*` shots come from `SHOTS` in `web/e2e/docs-capture/capture.ts`; regenerate with
`npx playwright test -c e2e/docs-capture/playwright.config.ts -g tables`, then `make docs`.
