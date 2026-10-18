# Runs page redesign, holds and exclusions (2026-10-16)

PR: [tech-pw/cryo#69](https://github.com/tech-pw/cryo/pull/69). Branch `feat/runs-page-redesign`.

## Problem

- The Runs page opened on a large hero, then fleet-health cards and engine load, and only then the run
  history. You had to scroll past context to answer the page's question: what ran, what failed, and
  what is held.
- Failures and slow nights did not stand out.
- Two backend gaps:
  - A maintenance hold was close to a no-op on a default install. With `durable_intents_enabled=False`,
    nothing on the scheduler, batch or worker path checked `maintenance_control_state`.
  - Operators had no way to keep maintenance away from a schema or a table.

## Page layout (`web/src/pages/operations/`)

The layout takes its cues from the Airflow 2.x grid view:

1. **Pulse tiles** (`.ops-pulse` in `operationsHome.css`) show Running now, Failed runs · 24h,
   Succeeded · 24h, Success rate · 7d, and Scanner.
   - The first three filter the history when clicked; the selected tile is underlined.
   - The success rate counts finished runs only (`runState.successRate`).
2. **Run timeline** (`RunBars.tsx`, `RunTimeline`) draws one bar per run, coloured by
   `runState.runTone`.
   - Bar height is the square root of duration relative to the longest run, so short runs stay visible.
   - Running runs are striped. A legend counts each state.
3. **History** has filter chips (All / Active / Failed · 24h, each with a count).
   - Each row shows state, run, start time (relative, with the exact time under it), a duration bar
     scaled to the longest run on the page, bytes freed, and the trigger.
4. **Engine load** and the **last nightly funnel** sit at the bottom (`.ops-context-grid`).

The tabs are unchanged: Workflows, History, Actions, Holds.

## Holds (Runs → Holds, `ControlRoom.tsx`)

- **Hold destination** (operator) and **Hold all maintenance** (admin) both need a reason.
  Resume needs a verification note.
- Fixed in this PR:
  - The destination picker was built only from durable intents and effects. On a default install
    that list is empty, so the button was always disabled. It now lists every configured engine by
    name, with intent and effect destinations merged in.
  - The Active holds table shows the engine name instead of only the UUID.

Enforcement now applies regardless of `durable_intents_enabled`:

| Path | Code | Global hold | Destination hold |
|------|------|-------------|------------------|
| Scheduler | `SchedulerStore.list_due_presets` | No presets fire | Presets fire; their jobs are deferred at the worker |
| Nightly batch | `MaintenanceCoordinator.run` → `_is_paused` | `skipped` run, `summary.paused=true` | Same, when it targets the held Spark destination |
| Worker | `_process_claimed_job` | Non-intent job → `MaintenancePausedError` → `deferred` | Same, for jobs on that destination |

Intent-bound jobs keep the existing pre-submit guard. Known limits:

- Statements already submitted to an engine finish.
- An in-flight batch finishes the lanes it started.
- A preset whose cron slot passed during a hold fires once after resume.

## Exclusions (`/api/v1/maintenance/exclusions`)

- Table `cryo.maintenance_exclusions`, migration `20261016_1200`. Each rule is one lakehouse +
  schema, optionally one table, plus a required reason. Rules are unique per target.
- Enforced in:
  - `selection.load_candidates`, including explicit `table_ids`
  - the scheduler's table query
  - `MaintenanceRunService`: 409 when every selected table is excluded; otherwise
    `summary.excluded_tables`
  - smart-maintenance layout apply: 409
- Table maintenance status reports `operator_excluded`.
- Scans and preset assignment ignore exclusions, so health stays visible.
- Create and delete are audited (`maintenance_exclude` / `maintenance_include`).
- There is no UI yet; the client functions `listMaintenanceExclusions`, `createMaintenanceExclusion`
  and `deleteMaintenanceExclusion` exist for the planned Exclusions view.

## Docs site

- `site/_src/pages/maintenance.html`:
  - the admission section describes the new page layout;
  - new sections "Pausing maintenance" and "Excluding a schema or table".
- Screenshots: `runs-{light,dark}.png` were regenerated, and `runs-holds-{light,dark}.png` are new.
  - The demo data (`web/e2e/docs-capture/demo-data.ts`) adds a run history (one running, six
    succeeded, one partial, one failed) and a destination hold on `demo-trino`.

## Still to do

- An Exclusions view on the Runs page, and scanner pause / Scan now controls next to the pulse tiles.
- Preset-level pause from the Workflows tab.
