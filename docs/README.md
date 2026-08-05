# Cryo Documentation

This directory is the persistent project brain for Cryo — it is updated
alongside code changes so future agents and maintainers can pick up context
without re-deriving it from source.

The authoritative architecture / contract docs live at the repo root:

- [`../plan.md`](../plan.md) — vision, scope, decisions, phases, assumptions
- [`../hld.md`](../hld.md) — components, data flow, deployment
- [`../lld.md`](../lld.md) — schemas, interfaces, planner algorithm, SQL
- [`../agents.md`](../agents.md) / [`../AGENTS.md`](../AGENTS.md) — conventions
  & guardrails for AI agents
- [`../README.md`](../README.md) — quickstart & top-level overview

## Index

### Onboarding & orientation

- [`EXECUTION_PLAN.md`](./EXECUTION_PLAN.md) — ordered actionable plan.
- [`architecture.md`](./architecture.md) — architecture & AI strategy.
- [`research-and-roadmap.md`](./research-and-roadmap.md) — recommendations,
  performance & openness, competitive analysis, research notes, roadmap.
- [`implementation/workload-aware-optimization.md`](./implementation/workload-aware-optimization.md)
  — AutoComp finish, PTO, WAIR, Okolnychyi delete policy: algorithms, APIs, UI
  wireframes (docs-only spec; build order inside).
- [`RATING_2026.md`](./RATING_2026.md) — 2026-07-07 deep rating (25
  attributes) and remediation roadmap.
- [`OSS_READINESS.md`](./OSS_READINESS.md) — OSS launch readiness
  checklist.
- [`OSS_P0_REVERIFY_2026_07_07.md`](./OSS_P0_REVERIFY_2026_07_07.md) —
  P0 reverification log.
- [`current-state.md`](./current-state.md) — snapshot of what works /
  what doesn't today.

### Operations

- [`operator-guide.md`](./operator-guide.md) — deployment + day-2 ops
  procedures (production deployment, runbooks).
- [`GOVERNANCE.md`](./GOVERNANCE.md) — governance policy and process.
- [`LOCAL_DEV_ONLY_INVENTORY.md`](./LOCAL_DEV_ONLY_INVENTORY.md) —
  ad-hoc local datalake components that must not ship to prod. Update
  this in the same PR when adding new dev-only scripts or compose
  services.

### API & backend

- [`api.md`](./api.md) — REST API reference.

### UI / UX

- [`UI_UX_AUDIT_2026-08-04.md`](./UI_UX_AUDIT_2026-08-04.md) — current
  page-by-page UI/UX scorecard, research-backed path to 10/10, and repeatable
  behavioral/field evaluation program.
- [`research-and-roadmap.md`](./research-and-roadmap.md) — cost-savings UI
  patterns.
- [`implementation/workload-aware-optimization.md`](./implementation/workload-aware-optimization.md)
  — Appendix E: Smart Maintenance / Table Detail / Connections UI for
  AutoComp, PTO, WAIR.
- [`ux-improvement-loop.md`](./ux-improvement-loop.md) — design for a self-paced,
  autonomous `/loop` that fixes one UX issue per iteration on a dedicated branch,
  grounded in `PRODUCT.md`/`DESIGN.md`. Designed and proven with one iteration; not
  currently running.

### Cost / FinOps

- [`cost-savings.md`](./cost-savings.md) — cost model and savings math.
- [`s3-request-cost-tracking.md`](./s3-request-cost-tracking.md) — design for per-table S3
  GET/PUT request-cost attribution via server access logs + Trino (not yet implemented).

### Local development

- [`developer-guide.md`](./developer-guide.md) — dev workflow.
- [`native-go-compaction-throughput.md`](./native-go-compaction-throughput.md) —
  local-MinIO compaction speedup loop (≤20 min / RAM-neutral).
- [`LOCAL_DEV_ONLY_INVENTORY.md`](./LOCAL_DEV_ONLY_INVENTORY.md) — also listed under
  Operations; keep in sync when adding seed/perf Makefile targets.

## Conventions for docs in this directory

- One topic per file. Cross-link liberally.
- Update docs **in the same PR** as the code change that affects them.
- Keep filenames kebab-case and dated (e.g. `RATING_2026.md`) where a
  document is point-in-time rather than living.
- When a doc is superseded, either delete it or replace its body with a
  single redirecting line to the successor. Don't leave stale files.