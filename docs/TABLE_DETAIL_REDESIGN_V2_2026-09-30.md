# Table detail v2: diagnosis → action → evidence

**Date:** 30 September 2026. Builds on `TABLE_DETAIL_REDESIGN_RESEARCH_2026-09-30.md` (metric
semantics still apply). Verified against production data for
`ai_chat_spam.silver_chat_text_output_log` (grade D) before and after.

## What changed and why

The page now answers, top to bottom: **how healthy is it → what's pulling it down → fix it →
what the files look like → what happened lately → how it's trending → who owns/reads/pays.**

| Area | Before | After |
|---|---|---|
| Overview hero | Gauge + same-colour deduction bars; no link from a problem to a fix | Gauge panel tinted by grade; deductions ranked, critical vs warning by colour **and** label, each row carries its fix (operation button, settings link, or a note) |
| Running maintenance | Only "Preview" / full-workflow "Run now" / manifest rewrite in a header menu; ops limited to the workflow's list | "Run maintenance" cards for compact, rewrite manifests, expire snapshots, remove orphans: evidence number, points it recovers, last run (nightly vs manual), one click → confirmation |
| Confirmation | Generic "Run now?" | Operation-specific dialog: evidence, effective retention/orphan age, engine workflow, consequence; destructive ops require an explicit acknowledgement |
| File profile | Candidate % bar + four numbers | Size histogram from `raw.file_size_bins`, bands relative to the write target, target bucket marked, table alternative |
| Last action | Full-width card, mostly "Not reported" | Activity timeline (6 latest audit rows) with before → after files and freed bytes only when reported |
| Maintenance tab | Last-actions table that the API never fills, "History not recorded yet" filler, 485-row partition table (8,818 px tall) | Same action cards on top; one "Tonight's selection" card (internals behind a disclosure); partition scores capped at 10 with "Show all" (3,432 px) |
| Usage tab | Scatter with double-encoded labels (`late%253D…`), ~30,000 px tall | Concentration headline, requests-per-day strip, ranked top-12 partitions, decoded labels, full table in a disclosure (1,566 px) |
| Snapshots tab | Actions column clipped by long ids | Short ids with full id in title/`data-snapshot-id`, icon copy/select, "Expire snapshots" action on the summary card |
| Schema & files | Vertical section rail; NDV/p50/p95 "Not measured" on every row | Pill section nav; sketch columns shown only when any column has sketches |

Competitive anchors: AWS Glue's per-table optimizer cards (compaction / snapshot retention / orphan
deletion, each with status and last run); Databricks Catalog Explorer history split into automatic
vs manual runs; Amoro's optimizing view with before/after file counts. Cryo's difference is
linking each health deduction to the operation that fixes it.

## Backend contract added

`POST /api/v1/tables/{table_id}/maintenance/operations` — see `docs/api.md`. Two supporting
changes:

- `start_maintenance_run(..., allow_operations_outside_preset=True)` only from this single-table
  route. Bulk `/maintenance/run` keeps its subset check.
- `skip_operations_from_preset` now also skips `convert_deletes`, `rewrite_position_deletes` and
  `compute_statistics` when `optimize` isn't requested. Before, an orphan-only deep run on a table
  with delete files would also plan a data rewrite. Nightly scheduled runs (no preset operations)
  and any workflow that includes `optimize` are unaffected.

## Data honesty rules applied

- `raw.snapshot_overhead_gb` is a heuristic (`cryo/scanner/cost.py`: size × 30% × min(snapshots/100, 1)),
  so it is **not** shown as bytes held by history; the page shows history span and commits/day.
- Histogram bands are named by size range ("Tiny · under 32 MiB") so they never read as the
  scanner's compaction-candidate count, which uses a different definition.
- Unknown values render "—", never 0. Zero-filled legacy bins are treated as "not recorded".

## Known issue found while doing this

On the reference table the scorer's −20 critical "Metadata ratio 134.9%" deduction divides
metadata bytes by `live_bytes` = 1.15 MB for a 33.8 GB table (`/tables/{id}/cost`), so the
ratio — and the "+20 pts" shown on Rewrite manifests — is very likely spurious. Scanner fix
(`cryo/scanner/signals.py::enrich_stats_signals` input) not done here.

## Files

- `web/src/components/table-detail/operations/` — catalog (deduction → op mapping, evidence,
  last run), `MaintenanceActions`, `OperationLauncher` (dialog + context used by header,
  overview, maintenance and snapshots tabs).
- `web/src/components/table-detail/overview/` — `OverviewPanel`, `FileLayoutCard` +
  `fileSizeBuckets`, `ScoreTrend` (SVG, grade bands).
- Charts here are HTML/SVG, not Recharts; `PartitionAccessChart` left the Recharts allowlist.

## Run on demand and the runs grid (second pass, same day)

Feedback: operators couldn't find where to run compaction/orphan removal, and wanted a cart plus an
Airflow-style run view.

- **Cart.** `OperationLauncher` holds a per-table cart (canonical order). Any "Add to run" card, the
  header menu, or a deduction's fix button adds to it; a sticky cart bar follows across tabs until
  "Review & run". Review lists numbered steps with effective settings; destructive steps need the
  acknowledgement. One submit = one run with `operations=[…]` on the single-table endpoint.
- **Discoverability.** Header primary action is **Run maintenance** → Maintenance tab, `#run-on-demand`.
  "Preview workflow plan" moved into the menu.
- **Runs grid** (`runs/TableRunGrid.tsx`, `runs/runModel.ts`): one column per job on this table
  (last 25, oldest→newest), duration bar on top (ring = manual), a row per operation, cells
  green/red/blue(pulsing)/dashed-waiting/grey. Per-op state comes from `TableRunRead.steps`; runs
  without steps fall back to audit receipts with the same `job_id`. `admitted`/`waiting` render as
  *waiting*, never *running*. Polls every 5 s while anything is active.
- **Run detail.** Status, trigger, mode/engine, error in red, before → next-scan-after with deltas
  coloured by improvement (labelled as table state, not the run's isolated effect — the API's
  `effectiveness.headline` sign is inconsistent, so it isn't used), per-op Gantt + recorded
  statement (the durable log), and a live log tab (WebSocket replay, 24 h / 500 lines in Redis).
- Removed: `MaintenanceHistory` (superseded by the grid) and the separate "Running now" list.
