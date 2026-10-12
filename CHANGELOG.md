# Changelog

All notable changes to Cryo will be documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Public chart README, NOTES, and a permissive values schema. Issue forms, CodeQL, labeler, and zizmor workflows. Contributor install no longer pulls the notebook `requirements.txt`.
- Open-source license metadata: `LICENSE` (Apache-2.0) at repo root, README license badge
  corrected from the `TBD` to Apache-2.0.
- `SECURITY.md` with GitHub Security Advisory routing and supported-versions table.
- `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1).
- `.github/ISSUE_TEMPLATE/` (bug, feature) and `.github/PULL_REQUEST_TEMPLATE.md`.
- Initial `CHANGELOG.md`.

### Changed
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
- Docs-vs-code drift: `docs/GOVERNANCE.md` previously asserted an Apache-2.0 `LICENSE` link
  whose target file did not exist.
- `stats_compute_ndv: false` was ignored, so batch runs paid for a full-table
  `compute_table_stats` scan on every optimized table (31% of lane time).
- The Spark renderer dropped every `rewrite_position_deletes` parameter (partial progress).
- Spark `expire_snapshots` for tables with protected snapshots called a procedure Iceberg does
  not have (`set_snapshot_ref`); it now tags the snapshot with `CREATE OR REPLACE TAG`.
- A batch lane whose pod was deleted (eviction, drain, spot reclaim) was never noticed and
  stranded its tables until the run deadline.

## [0.0.0] — Initial public release

Pre-release history predates this changelog. See `git log` for the full commit history.

[Unreleased]: https://github.com/tech-pw/cryo/blob/main/CHANGELOG.md
[0.0.0]: https://github.com/tech-pw/cryo/blob/main/CHANGELOG.md