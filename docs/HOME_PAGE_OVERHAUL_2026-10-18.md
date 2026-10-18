# Home page overhaul (2026-10-18)

Status: **shipped** on `feat/home-page-overhaul`. Scope: `web/src/pages/DatalakeHealth.tsx` (route `/`, title "Home") and `web/src/pages/home/*`.

The page answers one operator question: **what happened last night, and does anything need me?** Every change below either answers that faster or removes something that didn't.

This supersedes `docs/HOME_PAGE_PRESENTATION_PLAN_2026-09-29.md` and `docs/HOME_PAGE_REDESIGN_2026-09-29.md`.

---

## Layout, top to bottom

| Row | Left | Right |
|-----|------|-------|
| Banner | Global maintenance pause (only when paused) | |
| Now strip | Maintenance · Engine slots · Workers · Off-peak window · Scanner | |
| 1 | Latest nightly maintenance + 14-night history | Your next steps |
| 2 | Lake health (grades, D/F trend) | Storage impact |
| 3 | Worst tables people query | Fixed by Cryo |
| 4 | Table freshness | Cryo platform |
| Bottom | Recently discovered (full width, card style) | |

Why this order:

- **Action and outcome come first.** Last night's run and the to-do list answer the page's question. The lake picture and the platform gauge are context, not the answer.
- **Pairs read together.** Lake condition sits next to what cleaning it is worth. The hot-table list (the problem) sits next to "Fixed by Cryo" (the proof).
- **Platform health is a footnote when it's fine.** When it's critical, it shows up as the second "next step" anyway, so the gauge can sit low on the page.

## What each section does

**Now strip.** Live state at a glance, refreshed every minute. Maintenance shows `Paused globally`, `Running · N jobs`, or `Idle`, plus scoped holds. Engine slots shows `used/limit` for the first two engines. Workers comes from the Redis Streams consumer group (`EngineLoadResponse.workers_online`). It shows `Unavailable`, never `0`, when Redis can't be read. The off-peak window is in IST and says `open` while it's open. Scanner shows its state, last scan age, and queue depth. Every clause links to the page where you'd act on it.

**Latest nightly maintenance.** Last batch run's outcome counts plus a 14-slot history strip. Each slot is a link to that run, coloured succeeded / partial / failed / running. Empty state shows the off-peak window so "no run yet" isn't alarming at 3 pm.

**Your next steps.** Ranked by urgency, and rows with a zero count are hidden. The ranking is: global pause, platform critical, failed tables, scoped holds, approvals, scanner off/failed/stale (>36h), hot D/F tables, compaction need, then fast-path flags. Shows **All clear** when nothing is pending. A non-empty scan queue is *not* a next step; waiting work is normal and already visible in the Now strip.

**Lake health.** Grade donut and legend, plus:
- D/F change "since yesterday". It compares against the latest complete day before today in `/dashboard/fleet-trends`, and skips a prior day that scanned under half or over double today's graded tables. That way a coverage change isn't shown as the lake getting worse.
- A 14-day D/F sparkline, hidden when the line is flat or there's under two points.
- Small files as a share of all files.

**Storage impact.** Reclaimed yesterday, `wow_pct` (yesterday vs the same weekday last week, only when the day's measurement is complete), reclaimable now, and `≈ $X/month` from `totals.projected_savings_usd_month`.

**Worst tables people query.** D/F tables ranked by `query_count_30d`, then score. Uses `TableStats.query_count_30d`, which comes from engine-agnostic `table_query_activity`, so sources with `query_log.mode == "iceberg_table"` still rank. The tables API has no query-count sort, so "All D and F tables" links to `/tables?grade=D,F`.

**Fixed by Cryo.** Tables whose grade rose after a succeeded maintenance job finished in the last 24 hours. A table only counts once a scan *after* the job shows the higher grade. The baseline is the last history row at or before the job started (7-day lookback).

**Cryo platform.** Smaller gauge. A reason line always explains the status word: the top deduction, else the vitals `headline` when not healthy, else nothing. Platform links moved to the Now strip.

**Recently discovered.** Same data, now styled like the other cards.

## Backend additions

| Endpoint | Change |
|----------|--------|
| `GET /dashboards/datalake` | `?hot_limit=1..20` (default 5); adds `hot_at_risk`, `hot_at_risk_count`, `recently_improved`, `recently_improved_count`, `totals.running_jobs` |
| `GET /runs` | `?pipeline=` filters on `summary.pipeline`; Home asks for `pipeline=batch&limit=14` instead of `limit=200` |
| `GET /engine-load` | adds `workers_online` (`null` when Redis is unreachable) |

Boundedness: "recently improved" starts from runs created in the last 36 hours (a small table). It reaches jobs through the `(run_id, status)` index and caps at 2000 tables. It never scans the fleet or the job list on a timer. `running_jobs` is a single `COUNT` on job status.

## Refresh

Live data (runs, engine load, scanner, vitals, approvals, controls) refetches every 60s. Dashboard, fleet trends, findings, and the off-peak window refetch every 5 min. The header shows the scan data's age. The refresh button's tooltip says it reloads stored results and does not start a scan.

## Reversals from the 2026-09-29 plan

The 2026-09-29 plan called for a linear 0–100 score bar ("not a gauge") and a stacked grade bar ("do not replace this with a donut"). The page already shipped a donut and a radial gauge, and this overhaul keeps both: the gauge smaller and lower on the page. The donut plus its legend is the fastest read of grade mix on a half-width card. The gauge works once it always carries its reason line, which was the real problem the old plan identified (a score with no explanation).

## Docs site

`site/assets/img/screens/health-{light,dark}.png` were regenerated with `web/e2e/docs-capture/capture.ts` (Home shot is now 1440×1700 so it reaches the hot/fixed row). The quickstart alt text and caption, the Health page, and the onboarding summary describe the new page.

## Not done

- Visual regression baselines (`web/e2e/visual/health.spec.ts-snapshots/`) need regenerating against a live API (`npm run test:visual:update -- --grep health`). The spec skips without one.
