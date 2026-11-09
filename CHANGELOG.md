# Changelog

All notable changes to Cryo will be documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- The docs homepage opens with the product demo loop (`docs/img/demo-loop.mp4`), the real UI
  on demo data, above the hero. Click to play, pause, seek or mute. The homepage film at the
  bottom is unchanged.
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
- Pausing a preset cancels its jobs that are not yet admitted (queued or deferred become
  skipped with `cancelled: preset paused`). Admitted and running jobs finish, and a run left
  with nothing active is marked cancelled. Before, queued jobs drained after a pause (#330).

### Security
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