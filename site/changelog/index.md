---
title: Changelog
description: Cryo's release history -- what was added, changed and fixed in each version.
canonical: https://tech-pw.github.io/cryo/changelog/
updated: 2026-10-26
---

# Changelog

What changed in each Cryo release. The full changelog is in the repository.

Cryo is pre-1.0

Package, chart and web versions are `0.1.x`. A minor release may change the HTTP API, the Helm values, the database schema or the engine SQL, and there is no still-working deprecation window. The changelog says when it does. See [docs/VERSIONING.md](https://github.com/tech-pw/cryo/blob/main/docs/VERSIONING.md).

The authoritative changelog is [CHANGELOG.md](https://github.com/tech-pw/cryo/blob/main/CHANGELOG.md) in the repository. It follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Added

- The public Helm chart README, NOTES and a permissive values schema, plus issue forms, CodeQL, the labeler and zizmor workflows.

- `SECURITY.md` with GitHub Security Advisory routing and a supported-versions table.

- `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1).

- The bug and feature issue templates, and a pull-request template.

### Security

- Failed password and Google sign-ins are throttled per account (5 in 15 minutes) and per client IP (20), counted in Redis so the limit holds across replicas. A locked attempt gets `429` with `Retry-After`. Every failure is audited as `auth.login_failed`.

- `/docs`, `/redoc` and `/openapi.json` are off by default in `prod`, and `CRYO_METRICS_BEARER_TOKEN` protects the scraper's `/metrics`.

### Changed

- The docs site homepage tells the story of one 1 TB table over 30 days of decay, then through each operation in order, with a cost shown at every step.

- `LICENSE` and the chart's licence are now the verbatim Apache 2.0 text, so GitHub can identify the licence.

- GitHub is now the only source of truth. The GitLab-to-GitHub mirror sync is gone, so a pull request merged here is no longer overwritten.

- CI installs dependencies from `uv.lock` — the same lock the image ships — instead of resolving the newest releases on every run, and builds the Go engine for the scanner parity tests.

- New brand mark: ice cubes fusing into one solid cube. Updated in the docs site, the web app, the favicon and the README images.

- The README is rebuilt around images: a hero banner, the maintenance flow, product screenshots and the architecture, each with a light and a dark version.

- Spark maintenance lanes run `local[N,3]` task retries, cap the driver's status store, bulk-delete S3 keys in batches, retry throttled calls, and may schedule on spot capacity with on-demand fallback.

### Fixed

- The web UI's nginx now sends a Content-Security-Policy, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` and a Referrer-Policy on every response, including error pages. Hashed assets are cached for a year as immutable.

- `stats_compute_ndv: false` was ignored, so batch runs paid for a full-table statistics scan on every optimized table — 31% of lane time.

- The Spark renderer dropped every `rewrite_position_deletes` parameter, so the operation did nothing.

- Spark `expire_snapshots` on a table with protected snapshots called a procedure Iceberg does not have.

- A batch lane whose pod was deleted — eviction, drain, spot reclaim — was never noticed, and stranded its tables until the run deadline.

- Maintenance runs stayed `queued` forever after all their jobs finished, because the finalizer only looked at `running` runs.

## Releases

The published releases are on [GitHub](https://github.com/tech-pw/cryo/releases), and the tags are `v0.1.0` through `v0.1.5`. Each release publishes signed images with an SBOM, and the Helm chart for that release is pinned to its image digests.

Pre-release history predates the changelog. See the [commit history](https://github.com/tech-pw/cryo/commits/main) for the full record.

## Where to go next

Source: https://tech-pw.github.io/cryo/changelog/
Markdown: https://tech-pw.github.io/cryo/changelog/index.md
