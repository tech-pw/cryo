# Changelog

All notable changes to Cryo will be documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Spark drivers reserve a quarter of their CPU limit (at least 250m) instead of all of it;
  the limit is unchanged, so planning and commit bursts still get the full core. The
  `spark-submit` wrapper Job requests 50m / 256Mi instead of 250m / 512Mi, with the same
  limits. Set `spark.k8s.driverCpuRequestFraction: 1.0` (or `driver_cpu_request_fraction`
  on the destination) to go back to request == limit (#448).
- `scannerWorker.autoscaling.keda.enabled` defaults to `auto`: the scale-to-zero
  ScaledObject renders when the cluster serves KEDA's `ScaledObject` API, otherwise the
  CPU HPA does. The HPA floor drops from 3 to 1. Scanner workers only serve ad-hoc scans;
  the scheduled sweep runs in the scanner CronJob's own pods. Set `true`/`false` to force
  either (#449).
- Opt-in Spark dynamic allocation (`spark.k8s.dynamicAllocation`, or `dynamic_allocation` on
  the destination). Jobs with more than one executor start and peak at their sized count,
  release executors idle for 60s, and keep any executor that still holds shuffle output
  (shuffle tracking, 600s timeout). Off by default (#453).
- Release images (`cryo`, `cryo-web`, `cryo-spark`) are published as amd64 + arm64 manifest
  lists, so Spark and app pods can run on Graviton nodes. The Go engine and web assets build
  on the runner's own platform; the Python base is pinned to its multi-arch index digest.
  The chart still pins `kubernetes.io/arch=amd64` until arm64 is validated on your cluster;
  set `kubernetes.io/arch=arm64` in `spark.k8s.nodeSelector` to try it (#451).
- CI runs the Python tests in 4 parallel shards behind one aggregate `Test` check, and the
  e2e, image-scan and browser jobs no longer wait for the unit tests.
- Opt-in async Spark dispatch (`CRYO_WORKER_ASYNC_SPARK_DISPATCH`, default off). Workers
  submit a Spark-on-Kubernetes statement and move on; the governor hands the job back when
  the Spark Job ends, so the worker fleet no longer caps Spark throughput (#435). See
  `docs/ASYNC_SPARK_DISPATCH.md`.
- Expiry-only batch pass: `python -m cryo.maintenance.run --operations expire_snapshots`
  (Helm `maintenance.expiry.enabled`, off by default). It runs up to `batchSize` tables'
  `expire_snapshots` (default 25) in one Spark application per lane instead of one
  application per table. Per-table SQL, job status, audit rows with `snapshots_expired`,
  locks and failure isolation are unchanged (#443).
- The docs homepage opens with the product demo loop (`docs/img/demo-loop.mp4`), the real UI
  on demo data, above the hero. Click to play, pause, seek or mute. The homepage film at the
  bottom is unchanged.
- Spark `expire_snapshots`, `remove_orphan_files`, `optimize_manifests` and
  `compute_statistics` run their tasks inside the driver pod (`local[N,3]`) instead of on a
  separate executor pod. The driver takes the executor's CPU and heap, so each job is one pod
  with the same resources. Set `local_mode: false` on the destination's `spark_k8s` config
  (or `CRYO_SPARK_K8S_LOCAL_MODE=false`) to go back to executor pods (#450).
- Opt-in spot executors: `spark.k8s.executorCapacityTypes` (or `executor_capacity_types` on
  the destination), e.g. `spot,on-demand`. Executors require one of the listed Karpenter
  capacity types and prefer the first; the driver keeps the node selector's capacity type,
  so the statement survives an executor loss. With spot, Spark decommissions executors and
  migrates their shuffle and cached blocks to peers. Off by default (#452).
- Optional dedicated Karpenter NodePool for Spark (`spark.k8s.karpenter.installNodePool`).
  Spark pods are `do-not-disrupt`, so on a shared pool one small driver kept a mostly-empty
  node alive. The new pool is tainted for Spark, only reclaims empty nodes, and caps node
  removal with a disruption budget. Its `nodeClassName` must name an existing EC2NodeClass
  whose role can read the warehouse. New `spark.k8s.tolerations` and `spark.k8s.packPods`
  values (on with the pool) reach the wrapper, driver and executor pods through pod
  templates. Packing prefers a job's executors on its driver's node, then nodes already
  running Spark. Off by default; maintenance lanes are unchanged (#447).
- `make demo` takes the local stack from an empty UI to graded tables and one finished
  maintenance run: it seeds the fragmented fixtures, scans `local-rest`, assigns
  `local-standard` and runs `optimize` + `optimize_manifests` through the API
  (`scripts/demo.py`). The Quickstart now ends there, with a check after each step (#217).

- `README.md` opens with a 25-second loop of the real UI on demo data (Tables, a health grade,
  the run review, the run receipt), with an MP4 copy. `make web-demo-loop` re-records it from
  `web/e2e/docs-capture/demo-loop.ts` (#279). The homepage film already ends on the ice-cube mark
  (re-rendered in October), so the 0.2.0 note that it still showed the old mark is out of date;
  `site/README.md` now describes how the published 720p copy is made.
- A **Removed** quick view on the Tables page lists tables the scanner found gone from their
  catalog, with when and why, read-only. `GET /tables` takes `removed=exclude|include|only`
  (`include_removed=true` stays as an alias) (#330).

- Opt-in hard purge of tables removed from the catalog. Set
  `CRYO_REMOVED_TABLE_PURGE_AFTER_DAYS` (default off, minimum 7) and the scheduler deletes
  older tombstones daily, writing a `table_purged` audit row each. `cryo
  purge-removed-tables` previews or runs it by hand (#330).
- Opt-in continuous maintenance (#77). A preset's `continuous` block stays off unless enabled.
  While it is on, Cryo probes the live catalog, records per-table writer drift, and the scheduler
  enqueues that preset's maintenance through the normal governor, so cooldown, off-peak windows,
  deadlines and budget still apply. A commit lost to a concurrent writer is re-run in place, then
  the job is deferred and re-planned from current stats. After
  `CRYO_WORKER_COMMIT_CONFLICT_MAX_DEFERRALS` (default 3) it fails instead of retrying forever.
- Migrate to current partition spec: an on-request, approval-gated `migrate_partition_spec`
  operation that rewrites partitions still written under an older partition spec, in
  optimize's budgeted chunks, on Spark (Trino cannot filter old-spec partitions). The Maintain
  tab shows what is left and the progress. Both scanners now read each file's partition values
  with its own spec and record the old-spec totals, and Path to grade A no longer implies a
  layout change migrates existing data on its own (#289).
- Tables registry: an **Effective preset** column (preset, where it comes from, paused) and a
  **Coverage** filter with *On default* and *Unmaintained*. `GET /tables` rows carry
  `effective_preset`, and `GET /tables`, `/tables/group-by` and `/tables/export.csv` take
  `effective_provenance` (#330).

### Upgrade notes
- Migration `20261103_1200` gives every foreign key to `tables`, `jobs` and
  `maintenance_intents` an `ON DELETE` action (NOT NULL columns cascade, nullable ones are
  set to NULL) and adds four indexes concurrently. Constraints are re-added `NOT VALID` and
  validated outside the transaction. Nothing is deleted unless you turn the purge on (#330).

### Fixed
- Home's "Latest nightly maintenance" card follows the worker-path nightly run, not only batch-coordinator runs, and says when the newest nightly run is more than 36h old. `GET /runs` accepts `pipeline=nightly`, using the same rule as the Tables funnel (#492).
- The Runs page counts partial runs as runs with failures (**Runs with failures · 24h**, "2 failed, 9 partial"), so "No table failures" appears only when no table failed, and "Running now" says "runs waiting" instead of "Nothing queued" (#496).
- On phones the top bar has one search control (the command palette) and no overlapping Batch/⌘K controls, the Settings tab strip fades at its edge and scrolls the current tab into view, the Runs timeline fits the screen, and the truncated Home engine-slots line shows every engine on hover (#497).
- A scanner-worker running a long scan chunk is no longer restarted as hung. The
  `/healthz` stall check now measures time without progress, and each table the
  chunk starts or finishes counts as progress, so a healthy chunk that runs past
  `tickStallSeconds` keeps its pod (#432).
- The governor now says when workers, not the engine, are the bottleneck. If admitted jobs
  wait unclaimed while a destination has free engine slots, it logs `N admitted job(s)
  waiting for a worker; X/Y engine slots in use` (at most every 5 minutes per destination)
  and sets the `cryo_admitted_waiting_for_worker` gauge. The Helm chart documents sizing
  `worker.replicas` / `native.keda.maxReplicas` to the engine `slot_cap`: a worker blocks on
  one engine job at a time, so low worker CPU does not mean over-provisioned (#435).
- A Spark driver still `Running` after its dispatch Job is gone is removed once it is
  older than the driver TTL, and its credentials Secret with it. Its executors go with
  the driver. The Secret is no longer deleted while an executor of that driver has not
  finished. The governor Role gains `get` on Secrets, which the #423 check needs (#433).
- `expire_snapshots` is left out of a plan, with a note, when the latest scan shows it
  cannot remove anything: no more snapshots than `retain_last` keeps (counting commits
  since the scan), or the oldest snapshot is still inside the retention window. Missing
  stats still plan it. A successful Spark expiry now records the files it deleted on the
  audit row (`objects_deleted`, the per-kind counts in `result_metrics`); bytes freed stay
  NULL unless the procedure reported none deleted. Adds migration `20261109_1200` (#434).
- Spark-on-Kubernetes executors no longer reuse the driver's fixed 384 MiB
  `memoryOverhead`. Executor overhead is now 25% of the executor heap with a 768 MiB
  floor, so a 1 GiB executor gets room for Parquet/ZSTD write buffers. A quarter of
  the doubled 2 GiB retry heap is still under that floor, so each OOM step also
  doubles the floor, up to 3072 MiB. The driver overhead is unchanged (#431).
- Spark `expire_snapshots` and `remove_orphan_files` delete files four at a time
  (`max_concurrent_deletes`) instead of one at a time on the driver. Set the
  operation param to another count, or to 0 to leave the deletes serial (#444).
- The overview "Last nightly run" funnel covers the preset's cron and backlog runs from the
  last 24 hours, not just the cycle since the latest cron fire. Right after a capped cron
  run, it showed a few hundred tables and the rest of the fleet as not in the run. A table
  evaluated in more than one of those runs was also counted once per run; each table now
  counts once, by its latest run. The caption names how many runs are included, and the
  "Not in this run" node is now "Not reached" (#445).
- A Spark `optimize` failure whose root is `SparkPlan.session()` being null keeps
  the executor-loss and `Rewrite-Service` lines from the driver log. Those lines
  were outside the stored tail, so the saved error was only the
  NullPointerException and an executor OOM above it was not classified (#425).
- A Spark driver left `Pending` after its credentials Secret is deleted is removed
  once it is older than the driver TTL, including when its container never started.
  The Secret is no longer deleted while that driver pod still exists and has not
  finished (#423).
- An admitted job whose destination is gone releases its table lock when it fails.
  Lock reconciliation logs the table, the job, and the status (or that the job is
  missing) for every lock it releases. Ad-hoc runs get the same deadline as
  scheduled runs, so a deferred job cannot hold one open with no deadline (#424).
- Large unpartitioned tables are compacted instead of skipped once they pass the
  small-file threshold. The rewrite covers only small files and, on Spark and the
  native engine, groups of at most the chunk budget, with partial progress so a
  run that stops keeps the groups it finished. Trino still rewrites every small
  file in one statement. An operation that was explicitly requested and then
  skipped is shown on the run, and that job is no longer reported as a plain
  success (#422).
- The scanner no longer drops manifests written before a table was upgraded to
  format v3. A v3 manifest list may point at v2 files; those files are read as
  v2. A scan that still cannot read a manifest fails and keeps the previous
  stats, instead of storing file counts that are short by whole manifests (#421).
- `GET /tables` cursors resume after the last row (its sort values and table id) instead of
  at a stored offset, so a table discovered or removed between pages is not repeated or
  skipped. Rows that share a sort value stay ordered by id. A cursor issued before this
  change is rejected; request the first page again. `offset` still selects a page by
  position when no cursor is sent (#418).
- Migration `20261103_1200` no longer deadlocks against running Cryo pods. It rewrote one
  foreign key at a time inside the same transaction as `20261103_1510`, which still held
  `cryo.presets` exclusively, so an app session reading `tables` and then `presets` closed a
  cycle and the migrate hook failed with `DeadlockDetected` on `fk_jobs_table_id_tables`. The
  rewrite now runs after the earlier revisions commit, locks every relation it touches up
  front with a 3s lock timeout, and on a timeout or deadlock releases them and retries (up to
  20 times). A failed attempt with the old image rolled back, so re-syncing is safe (#330).

- The Go scan subprocess now gets `GOMEMLIMIT` at 75% of the container memory limit (or
  `CRYO_SCAN_ENGINE_MEMLIMIT` when set), so one large table is collected inside the scanner
  pod instead of OOM-killing it. The table name is logged before the subprocess starts, and a
  kill (signal 9) is recorded as that table's scan failure, which the existing retry ladder
  backs off (#378).

- The governor's in-flight rebuild no longer fails on Postgres. `in_flight_counts` grouped by
  a second copy of `coalesce(replica, 'default')`, which Postgres rejects, so the correction
  never ran and capacity could drift toward zero. The query now groups by the same expression
  (#375).
- `make seed-fragmented`, `make seed-docker` and `make smoke-file-counts` pointed the Spark
  container at `localhost:8181`, which inside the container is not the REST catalog. They now
  use `iceberg-rest:8181` (#217).

- Turning off "Active only" on the Tables page now lists tables removed from the catalog. It
  used to drop only the `is_active` filter while the server kept hiding them. CSV export and
  group-by now hide removed tables by default too, so their totals match the list (#330).
- Native `expire_snapshots` now deletes the files only the expired snapshots referenced, as Spark
  and Trino do. Before, it committed metadata only, so standard/light runs, expire-only requests,
  and shared-location tables (none of which plan `remove_orphan_files`) leaked them (#374).
- The Spark fallback sweep no longer finalizes a job whose native dispatch is still running.
  It used to mark such a job terminal mid-run, so the governor's lock reconcile released its
  table lock as orphaned. When the sweep does end a job, it now releases the job's table lock
  and in-flight slot, and a second completion of the same job no longer drives the in-flight
  counter negative (#382).
- A fleet scan whose catalog discovery failed no longer finishes `succeeded` with zero tables.
  The chunk is `failed` when nothing was discovered and `partial_failed` when other sources
  listed, and the discovery error becomes the run's `last_error`. A run with a `last_error` is
  never reported `succeeded`. Discovery has its own timeout,
  `CRYO_SCAN_DISCOVERY_TIMEOUT_SECONDS` (default 1800), instead of the per-table scan timeout,
  and heartbeats the chunk while it lists (#380).
- The missing-table reconcile no longer removes nothing once more than
  `CRYO_SCAN_MISSING_TABLE_MAX_COUNT` (default 500) tables are missing. That count is now a
  per-scan removal limit, applied to the tables last seen longest ago first, so a backlog drains
  over several scans. `CRYO_SCAN_MISSING_TABLE_MAX_FRACTION` stays the catalog-outage guard
  that removes nothing (#381).

### Changed
- `GET /sources/{id}/aws-integration` returns `200` with `null` instead of `404` when a source has no AWS integration, so Settings no longer logs a failed request per source. A non-admin on /settings/admin sees "Admin role required" instead of a silent redirect, and each Settings page has its own heading (#498).
- Pausing a preset cancels its jobs that are not yet admitted (queued or deferred become
  skipped with `cancelled: preset paused`). Admitted and running jobs finish, and a run left
  with nothing active is marked cancelled. Before, queued jobs drained after a pause (#330).

### Security
- CodeQL `init` and `analyze` move to v4.39.2 together; the two must share a version.
- CI runs trufflehog alongside gitleaks: on every PR and push to `main` over the new commits,
  and weekly over the full history. It fails on verified credentials, and on credentials whose
  verification errored outside the connection-string detectors (#203).

## [0.2.0] — 2026-11-03

First tagged release since v0.1.5. GitHub release notes group the 180+ merged pull requests;
the highlights are below.

### Upgrade notes
- A default preset that never had a schedule gets `0 2 * * *` from the migration, so after the
  upgrade it fires nightly at 02:00 in the scheduler time zone (`CRYO_DISPLAY_TIMEZONE`, UTC by
  default). Pause it first if you don't want that (#119, #330).

### Removed
- The unused Rust engines (`cryo-engine/`, `cryo-native/`) and their CI jobs. The Go engine
  (`engine-go/`, `cryo-engine-go`) is the native engine (#266).

### Added
- Public chart README, NOTES, and a permissive values schema. Issue forms, CodeQL, labeler, and zizmor workflows. Contributor install no longer pulls the notebook `requirements.txt`.
- Open-source license metadata: `LICENSE` (Apache-2.0) at repo root, README license badge
  corrected from the `TBD` to Apache-2.0.
- `SECURITY.md` with GitHub Security Advisory routing and supported-versions table.
- `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1).
- `.github/ISSUE_TEMPLATE/` (bug, feature) and `.github/PULL_REQUEST_TEMPLATE.md`.
- Initial `CHANGELOG.md`.
- Docs site: the header shows the latest release tag (`version` in `site/_src/site.json`), and
  new Changelog (rendered from this file) and Contributing pages.

### Security
- `cryo-engine-go` uses go-jose 4.1.6, which fixes a JWK-parsing DoS (CVE-2026-116587) and two
  parser panics flagged by the release image scan (#354).
- Failed password and Google sign-ins are throttled per account (5 in 15 minutes) and per client
  IP (20), using Redis so the limit holds across replicas. Locked attempts get `429` with
  `Retry-After`, and the sign-in page says how long to wait. Each failure is audited as
  `auth.login_failed` (admin-only `GET /api/v1/auth/audit`) and counted in
  `cryo_auth_login_failures_total`. `/docs`, `/redoc` and `/openapi.json` are off by default in
  `prod` (`CRYO_API_DOCS_ENABLED`). `CRYO_METRICS_BEARER_TOKEN` protects the scraper `/metrics`.
  See the operator guide (#262).

### Changed
- Docs site homepage: "Compact first, then clean up" is replaced by a scroll-driven story of one
  1 TB table. It decays for 30 days, then goes through each operation in order while a pinned
  S3 bill shows what each step costs or saves ($110.72 to $23.53 a month in the example). Prices
  are S3 Standard us-east-1, and the expiry and orphan windows match the planner's defaults.
  The table is drawn as the brand's ice core sample and changes with each step: grains packing
  into blocks, old layers lifted into a tray, orphans swept into a pile. Cryo's mark narrates and
  fuses its cubes on `optimize`. Each chapter adds a plain-language analogy and a sourced cost
  that isn't on the bill (S3 503 throttling, request latency, Trino join order, GDPR deletes).
  It ends on a printed receipt and a "tables like this in your lake" estimator. A mood layer
  makes cost felt: the bill, its face, Cryo's mascot, the meters and the timeline rail go from
  green and smiling to red and crying as the bill climbs, and each step opens with a
  before-and-after scorecard.
- `LICENSE` and `deploy/helm/cryo/LICENSE` are now the verbatim Apache License 2.0 text. The
  previous copies had reworded clauses, so GitHub could not identify the license.
- GitHub (`tech-pw/cryo`) is now the only source of truth. The GitLab-to-GitHub mirror sync
  (`scripts/sync_oss_mirror.sh`, its exclude list, the `.gitlab-ci.yml` sync job and
  `docs/OSS_MIRROR.md`) is removed, so pull requests merged here are no longer overwritten.
- GitHub CI installs Python dependencies from `uv.lock` (the same lock the image ships) instead
  of resolving the newest releases on every run, builds `cryo-engine-go` for the scanner parity
  tests, and skips CodeQL and the Pages deploy while the repository is private. Lint, mypy,
  clippy, Rust and frontend tests that had drifted are fixed.
- New Cryo mark: small ice cubes fusing into one solid cube, replacing the faceted-iceberg mark,
  which was too close to the Apache Iceberg logo. Updated in `docs/brand/logo/` (with dark and
  one-colour variants), the docs site header, homepage animation, video poster and favicon, the
  web app's `CryoMark` and favicon, and the docs-site screenshots. See
  `docs/brand/CRYO_BRAND_GUIDE.md` §6. The homepage film still shows the old mark and needs a
  re-render from its source.
- `README.md` rewritten around images: hero banner, maintenance flow, product screenshots and
  architecture, each with light and dark versions. The long comparison, features and research
  sections are now collapsible. Images live in `docs/img/` and are rebuilt by
  `scripts/readme_images.mjs`. Removed a dead link to `docs/premortem.md`.
- Docs site homepage: the maintenance animation now follows one partition through five steps
  (scan, plan, admit, rewrite, receipt) with a live readout for each, before/after file stats,
  and a clickable step strip with pause and replay. Rebuilt as responsive HTML
  (`site/_src/includes/maintenance-flow.html`) instead of separate wide and compact SVGs.
- `site/scripts/preview.mjs` no longer crashes on request paths starting with `//` or on
  malformed percent-escapes.
- `README.md`: four explainer diagrams. What decays in an Iceberg table and which operation fixes
  each; how a table earns its A–F grade; how the planner chunks partitions under a GB budget,
  with the Trino and Spark SQL it renders; and how the governor admits a job into a free engine
  slot. "How it works" now walks scan → plan → admit → rewrite and record.
- `README.md`: license section now links to `LICENSE` instead of "TBD".
- Spark maintenance lanes run `local[N,3]` (task retries), cap the long-lived driver's status
  store, bulk-delete 1,000 S3 keys per request, retry throttled S3 calls 32 times, and may
  schedule on spot capacity with on-demand fallback (`maintenance.laneCapacityTypes`). Batch
  optimize rewrites only files under the scanner's small-file threshold and no longer splits
  files up to 10× target, orders rewrite groups `files-desc`, and `convert_deletes` removes
  the delete files it leaves dangling. See `docs/SPARK_ENGINE_OPTIMIZATION_2026-09-25.md`.

### Fixed
- The web UI's nginx now sends `Content-Security-Policy` (with `frame-ancestors 'none'`),
  `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` and `Referrer-Policy` on every
  response, error pages included. `index.html` and SPA routes are `no-cache` so a deploy shows
  up at once. Hashed `/assets/*` files are cached for a year as `immutable`. HSTS is a chart
  value (`web.hsts`), off by default for installs without TLS (#260).
- Docs-vs-code drift: `docs/GOVERNANCE.md` previously asserted an Apache-2.0 `LICENSE` link
  whose target file did not exist.
- `stats_compute_ndv: false` was ignored, so batch runs paid for a full-table
  `compute_table_stats` scan on every optimized table (31% of lane time).
- The Spark renderer dropped every `rewrite_position_deletes` parameter (partial progress).
- Spark `expire_snapshots` for tables with protected snapshots called a procedure Iceberg does
  not have (`set_snapshot_ref`); it now tags the snapshot with `CREATE OR REPLACE TAG`.
- A batch lane whose pod was deleted (eviction, drain, spot reclaim) was never noticed and
  stranded its tables until the run deadline.
- Maintenance runs stayed `queued` forever after all their jobs finished: the run finalizer
  only looked at `running` runs, and maintenance runs are created `queued`. Finalizing also
  no longer drops the run's `actor`, requested `operations` and `correlation_id` from its
  summary.

## [0.0.0] — Initial public release

Pre-release history predates this changelog. See `git log` for the full commit history.

[Unreleased]: https://github.com/tech-pw/cryo/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/tech-pw/cryo/compare/v0.1.5...v0.2.0
[0.0.0]: https://github.com/tech-pw/cryo/blob/main/CHANGELOG.md