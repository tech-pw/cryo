# UI strategy: beat Amoro, LakeOps, and Nimtable on the operator screen

Date: 2026-09-28. This is a product plan, not a claim that Cryo's UI is already ahead.
Companion: [backend competitive strategy](COMPETITIVE_STRATEGY_AMORO_LAKEOPS_2026-09-28.md).

## What each competitor's screen actually sells

Checked against their public pages on 2026-09-28. Marketing screenshots are not production evidence.

| Screen | What the buyer sees | What we must not copy |
|---|---|---|
| LakeOps home | Last-30-days activity, health counts, a live operations list: operation, table, duration, impact, status. | Their impact column prints TB and dollars we have not measured. A Cryo screen that invents those numbers loses the comparison the moment an operator opens the audit row. |
| Nimtable | Catalog browser: schema, partitions, snapshots, file layout, SQL in the browser. Docker in one command. | We are not a REST catalog. Do not rebuild their file explorer before the maintenance evidence is honest. |
| Amoro AMS | Optimizing-process list, optimizer groups, quota, table optimizing status. | Their screen is a self-optimizer console. Ours is a run debugger until a signal can enqueue a job without a pin. Do not draw a fake autopilot toggle. |
| Cryo today | Operations is an Airflow-style workflow and run list. Run detail has status, logs, and a job table. Table detail is the OpenMetadata-shaped page. | The runs list had no evidence column. A missing byte count was formatted as zero. That is the opposite of the rule in the backend strategy. |

## The sentence the UI is allowed to win

> **You can see what changed, and you can see when we do not know.**

LakeOps wins a screenshot with a dollar. Nimtable wins a first look at a table. Amoro wins a process list for a running optimizer. Cryo wins the operator who has to defend a maintenance run: status, logs, the plan, the engine, and an evidence state that is measured, unknown, or not applicable. Never zero for unknown.

## What to ship, in order

1. **Evidence on every run.** Done in this change for the runs list and the savings label. `realized_bytes_freed == null` renders "Bytes unknown", not "Freed 0". Partial coverage stays visible ("1 of 2 actions measured").
2. **Same label on the run header.** Already calls `realizedSavingsLabel`. The bug was in the helper, so the header is fixed with it.
3. **Do not add a health donut or a savings hero until the API returns a retained daily split.** The backend now stores `bytes_freed_by_engine`. The UI must not chart it until a response field exists and a test locks the unknown-versus-zero rule.
4. **Table detail, next.** Show why this table was or was not in last night's run: pinned, grade, needs compaction, last success, engine. That is the answer Nimtable cannot give and LakeOps hides behind "autopilot".
5. **File layout, later.** Nimtable's partition and snapshot charts are the right second screen, after evidence. Do not start them in the same change as the evidence fix.

## Banned in the UI

- A dollar or TB figure computed in the browser from a null byte count.
- "100% healthy", "12×", or "76% saved" as chrome. Those are LakeOps marketing lines.
- An Autopilot switch that does not enqueue work. Production nightly selection does not use the governor staleness boost, and the event watcher is not deployed.
- A catalog SQL console. That is Nimtable's product, not this gap.

## Done when

An operator can open Operations, read the Evidence column, and tell a measured reclaim from a run whose engine did not return a size, without opening the database.

## Deeper pass, 2026-09-28

Sources read, not screenshots guessed: Amoro 0.8.1 [Using Tables](https://amoro.apache.org/docs/0.8.1/using-tables/) and [Managing Optimizers](https://amoro.apache.org/docs/0.8.1/managing-optimizers/); LakeOps [observability](https://lakeops.dev/solutions/observability) and home page; Nimtable README and hacking guide. Cryo routes are from `web/src/App.tsx` and `TableDetailShell.tsx`.

### Amoro's console is an optimizer console, not a catalog browser

The documented screens, in the order an operator uses them:

- **Optimizer Groups** and **Optimizers**. Create a group, pick a container (local, Kubernetes, Flink, Spark, external), set `scheduling-policy` to `quota` or `balanced`, then Create Optimizer and watch it go `RUNNING`. Release is a button. This is the product. Cryo has no equivalent, and should not draw one until a table can be assigned a resource pool that the nightly path actually honors. Today the nightly path does not.
- **Optimizing**, lake-wide. One row per table. Status is a state machine: `idle`, `pending`, `planning`, `minor`, `major`, `full`, `committing`. Columns include duration, file count split by base / insert / equality-delete / position-delete, file size, quota, and quota occupation over the last hour. That is the screen that makes a self-optimizer feel real. A green "succeeded" on a nightly run is a weaker sentence.
- **Table detail** has five tabs: Details (schema, keys, partitions, properties, file counts, average file size, last commit), Files (partitions and files), Snapshots (filter by branch and tag), Optimizing (before/after file count and average size, duration), Operations (DDL history). Watermark is shown for mixed formats only, not native Iceberg.
- **Terminal** runs Spark SQL in the dashboard: create, alter, drop, set `self-optimizing.enabled`, set quota, set expire. Hive upgrade is a button on the table page.

What Amoro does not document: a run log, a Trino refusal, an evidence state of unknown, or a cost number. Their UI wins by showing the optimizer's current state, not by a savings hero.

### LakeOps publishes eight screens. The numbers on them are not a fleet.

The observability page names the information architecture a buyer will ask for:

| Their screen | What it claims to show | Cryo today |
|---|---|---|
| Dashboard | Critical / Warning / Healthy counts, table inventory, size, last modified | `/health` and catalog grades A–F. Different scale (six grades, not three). Do not collapse A–F to copy their three words. |
| Events | Every compact, expire, orphan, manifest rewrite, with duration, status, and before/after file counts. Filter by catalog, type, success. | Operations runs list now has an Evidence column. It is run-scoped, not an action-scoped live feed. Audit rows exist per table, not as a lake-wide event stream in the UI. |
| Insights | Findings ranked CRITICAL, HIGH, WARNING, LOW, each tied to a table and a next step. | Health grade and `needs_compaction` exist. There is no ranked insight with a recommended action that the UI can execute. |
| Table metrics | Records over snapshots, active / stale / delete files, file-size histogram. | Table detail has Health & Quality. No file-size histogram and no snapshot-growth chart were found in that panel. |
| Partitions | File count, bytes, and delete-file concentration per partition key. | Not a first-class tab. This is the screen that explains a Trino planning timeout. It is the highest-value Nimtable/LakeOps gap that is not a lie. |
| Field access | SELECT, FILTER, and JOIN frequency per column, per engine. | Query log coverage is too thin to draw this without labeling it as a sample. Do not ship the chart until the sample is on the screen. |
| Policies | Compaction, expire, orphan, manifest rules with next run, last run, enabled. Scope catalog or table. | Presets and governance policies exist. Preset creation is disabled in the Operations header ("isn't available in this UI yet"). That empty button is a worse first impression than Amoro's Create Optimizer. |
| Routing / engines | A separate product. | Out of scope. Cryo does not route queries. |

Their published totals (786 tables, $1,374,672 saved, 46.8 PB, 12.4×) are marketing fixtures on the page, not a method. Do not put a counter next to them.

### Nimtable is a catalog console with maintenance bolted on

README and hacking guide, not a live login:

- Next.js UI, generated client from OpenAPI, Docker Compose, default `admin` / `admin`.
- Explore schemas, partitions, snapshots, manifests. Visualize file and snapshot distribution. Run SQL in the browser.
- Compaction is "integrate with Spark or RisingWave". There is no optimizing state machine and no run debugger.
- It can be the Iceberg REST catalog. Cryo reads a catalog and must not pretend to replace one.

Nimtable wins the first ten minutes: connect, see files, see snapshots. Cryo wins after the first maintenance run, if the run page shows evidence. The Evidence column is that wedge. It is not a file explorer.

### What Cryo already has that the first note understated

Routes in `web/src/App.tsx`: health, catalogs, cost, attribution, table detail, operations (scanner, preset, run), job detail, governance, anomalies, query performance, SLA, dark tables, scan cost, smart maintenance, control room, connections, AI.

Table detail tabs: Overview, Structure, Health & Quality, Cost, Lineage, Activity. That is closer to OpenMetadata than to Amoro, and it is already more than Nimtable's maintenance story. Activity is the audit list. It is not Amoro's Optimizing tab (before/after file count, duration, process status).

### Revised order, after this pass

1. Evidence label. Shipped. Unknown is not zero.
2. **Action feed, not another dashboard.** One lake-wide list, last N audit rows: operation, table, engine, duration, files in → out when present, bytes or "unknown", status. This is LakeOps Events and Amoro's Optimizing history, built from rows we already store. No new metric.
3. **Partition file counts on table detail.** The screen that explains a Trino planning failure. Needs a scan field or `$partitions` summary already stored. If the API does not have per-partition file counts, do not fake a histogram.
4. **Remove or finish the disabled New preset button.** An operator comparing to Amoro's Create Optimizer will stop there.
5. File-size histogram and snapshot chart, only after (3) has real data.
6. Field-access chart, only with a coverage count on the chart ("3,406 query rows"). Otherwise it is LakeOps' telemetry claim without the telemetry.
7. Optimizer-group UI, Terminal SQL, query routing, and an Autopilot switch: not this track.

The UI does not beat them by looking more like their marketing site. It beats Amoro when a table shows its last process the way their Optimizing tab does, and beats LakeOps when every impact cell can be traced to an audit row.

## Bugs and unwired surfaces, 2026-09-28

Read from the current tree. Not a production probe.

### The evidence the UI cannot show, even after the runs-list fix

`cryo.audit_log` stores `input_files_count`, `output_files_count`, and `actual_bytes_freed` (`cryo/db/models.py`). `AuditLogRead` in `cryo/api/schemas.py` returns only `actual_bytes_freed`. The file counts never leave the API. The frontend `AuditEntry` type drops `actual_bytes_freed` as well. `ActivityTab` prints action, time, engine, and duration. It does not print files or bytes. That is why the table page cannot match Amoro's Optimizing tab or LakeOps' Events row. The data is in the database. Two type layers throw it away.

`getTableEffectiveness` is implemented in the client and never imported by a page. The dashboard route already computes an effectiveness ratio from those file counts (`dashboard_routes.py` around the audit read). The table page does not call it.

### Messages that lie when an endpoint exists

- `DatalakeHealth.tsx` calls `getDatalakeDashboard`. On any error the banner says "Dashboard endpoint is not available yet." The route exists. A 500, a timeout, or an auth failure all render as "not built."
- `CostOverviewTab.tsx` does the same for `getCostDashboard`. `usingFallback` is `cost.isError`. The copy says the endpoint is not available yet.
- `CostAttribution.tsx` is honest: the page says the endpoint always returns an empty list. `dashboard_routes.py` `get_cost_attribution` is a TODO that returns `attributions=[]`. The button "View tables" is disabled so it cannot be a fake link. Leave this page labeled experimental. Do not wire a chart to it.

### Controls that do nothing

- Operations "New preset" is a disabled primary button. The tooltip says creation is not in the UI. Amoro's equivalent (Create Optimizer) is the first action on their optimizer page. This is the sharpest dead control in the product.
- Cost attribution "View tables" is disabled on purpose. Keep it disabled until tags exist.

### Client functions no page calls

Of 226 exports in `web/src/api/client.ts`, these are never referenced outside that file:

| Function | What it is | Why it matters |
|---|---|---|
| `getAwsCostInsightConfig` and the create/patch/delete/ingest pair | CUR and inventory ingest | S3 cost drill-down cannot be configured from the UI that already has an AWS tab. |
| `getCostByBucket`, `getCostDailySeries`, `getSizeByPrefix` | Bucket and prefix cost | The hottest temp-bucket prefixes have no screen. |
| `previewRouting` | Routing preview | Query routing is a LakeOps product. Do not surface this until it is a supported Cryo feature. |
| `getTableEffectiveness` | Before/after file ratio | This is the missing Optimizing-tab number. |
| `getActiveSkillset` | Active skillset | Skillsets page does not read the active one through this call. |
| `predictQPP`, `recordQPPObservation` | Query-performance predictor | Governor QPP exists. The query page does not use it. |

`getTablePartitions` and `getTablePartitionAccess` are wired. `StructureTabPanel` renders a partition histogram and an access chart. The earlier note that said partition counts were absent was wrong. The gap is maintenance evidence on the activity timeline, not the absence of a partition view.

### What not to "fix" while hunting unwired code

- Do not connect `previewRouting` or QPP record just to empty the unused list. Those are unfinished products.
- Do not fill cost attribution with invented departments.
- Do not change the health-page fallback math. Change the banner so a failed call is a failure, not "not built yet."
- The file-count omission on `AuditLogRead` is the one backend wire that unblocks the UI comparison. Adding the two integers to the schema, then to `AuditEntry`, then to `ActivityTab`, is the smallest change that makes a table's last optimize show files in and files out. Bytes stay "unknown" when `actual_bytes_freed` is null.
