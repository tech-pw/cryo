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

- [`cryo-10-of-10-plan.md`](./cryo-10-of-10-plan.md) — evidence-gated master
  plan for product, architecture, maintenance intelligence, UX, engineering,
  production maturity, and category leadership.
- [`EXECUTION_PLAN.md`](./EXECUTION_PLAN.md) — ordered actionable plan.
- [`architecture.md`](./architecture.md) — architecture & AI strategy.
- [`research-and-roadmap.md`](./research-and-roadmap.md) — recommendations,
  performance & openness, competitive analysis, research notes, roadmap.
- [`PAPER_IMPLEMENTATION_AUDIT_2026-09-24.md`](./PAPER_IMPLEMENTATION_AUDIT_2026-09-24.md)
  — each cited paper (AutoComp, Okolnychyi, Zero-Scan DQ, WAIR, PTO) read against the running
  code: what is implemented, what deviates, and what is safe to claim.
- [`implementation/workload-aware-optimization.md`](./implementation/workload-aware-optimization.md)
  — AutoComp finish, PTO, WAIR, Okolnychyi delete policy: algorithms, APIs, UI
  wireframes (docs-only spec; build order inside).
- [`RATING_2026.md`](./RATING_2026.md) — 2026-07-07 deep rating (25
  attributes) and remediation roadmap.
- [`oss-launch-plan-2026-09-25.md`](./oss-launch-plan-2026-09-25.md) — current
  OSS portability, Helm distribution, independent adoption, and release-gate plan;
  replaces the July OSS readiness checklist below.
- [`OSS_0_BASELINE_2026-09-25.md`](./OSS_0_BASELINE_2026-09-25.md) — OSS-0
  release-boundary and evidence checklist: candidate commit, publication
  hygiene findings, secrets scan, CI state, open blockers.
- [`OSS_READINESS.md`](./OSS_READINESS.md) — OSS launch readiness
  checklist (July 2026, superseded by the plan above).
- [`OSS_P0_REVERIFY_2026_07_07.md`](./OSS_P0_REVERIFY_2026_07_07.md) —
  P0 reverification log.
- [`current-state.md`](./current-state.md) — public pointer to capabilities and the pre-1.0 floor.
- [`WAVE_0_BASELINE.md`](./WAVE_0_BASELINE.md) — reproducible baseline,
  hotspot map, migration support, evidence registry, interview plan, and
  stop-ship policy.

### Operations

- [`operator-guide.md`](./operator-guide.md) — deployment + day-2 ops
  procedures (production deployment, runbooks).
- [`BATCH_MAINTENANCE.md`](./BATCH_MAINTENANCE.md) — the nightly batch maintenance
  pipeline (selection, Spark lanes, recording, failure modes).
- [`SPARK_ENGINE_OPTIMIZATION_2026-09-25.md`](./SPARK_ENGINE_OPTIMIZATION_2026-09-25.md) —
  where the first nightly run's Spark time went, what Iceberg/competitors do, and the
  rendering/lane changes that followed (NDV scans, min-file-size, dangling deletes, spot lanes).
- [`GOVERNANCE.md`](./GOVERNANCE.md) — governance policy and process.
- [`SUPPORT_POLICY.md`](./SUPPORT_POLICY.md) — release support, triage,
  safe diagnostics, and capability boundaries.
- [`UPGRADE_ROLLBACK.md`](./UPGRADE_ROLLBACK.md) — migration-render evidence
  and the remaining deployed-drill boundary.
- [`EXTERNAL_EVIDENCE_ATTESTATION.md`](./EXTERNAL_EVIDENCE_ATTESTATION.md) —
  independently signed external-gate manifests.
- [`LOCAL_DEV_ONLY_INVENTORY.md`](./LOCAL_DEV_ONLY_INVENTORY.md) —
  ad-hoc local datalake components that must not ship to prod. Update
  this in the same PR when adding new dev-only scripts or compose
  services.

### API & backend

- [`api.md`](./api.md) — REST API reference.
- [`CAPABILITIES.md`](./CAPABILITIES.md) — generated catalog and engine-operation
  support contract, including maturity, runtime conditions, fallbacks, and evidence.
- [`ECOSYSTEM_CONNECTORS.md`](./ECOSYSTEM_CONNECTORS.md) — versioned connector
  manifest and conformance contract.
- [`PRIVACY_PRESERVING_BENCHMARKS.md`](./PRIVACY_PRESERVING_BENCHMARKS.md) —
  opt-in k-anonymous aggregate outcome export.
- [`BENCHMARK_RESEARCH_AND_DESIGN_2026-10-06.md`](./BENCHMARK_RESEARCH_AND_DESIGN_2026-10-06.md) —
  claim rules and CryoBench-Maint v1 design; no harness yet.

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

### Brand & marketing

- [`brand/CRYO_BRAND_GUIDE.md`](./brand/CRYO_BRAND_GUIDE.md) — brand and image guide for the
  website, LinkedIn and blog diagrams: the ice-core motif, palette, type, Mermaid theme,
  LinkedIn sizes and copy-paste prompts for image AI tools.
- [`DOCS_SITE_REDESIGN_HANDOFF_2026-10-05.md`](./DOCS_SITE_REDESIGN_HANDOFF_2026-10-05.md) —
  current state of the public `site/` redesign. Read this before continuing that work.
  Measurements are in [`DOCS_SITE_DESIGN_RESEARCH_2026-10-05.md`](./DOCS_SITE_DESIGN_RESEARCH_2026-10-05.md).

### Cost / FinOps

- [`cost-savings.md`](./cost-savings.md) — cost model and savings math.
- [`s3-request-cost-tracking.md`](./s3-request-cost-tracking.md) — design for per-table S3
  GET/PUT request-cost attribution via server access logs + Trino (not yet implemented).
- [`ARCHIVE_TIERING_RESEARCH_2026-10-01.md`](./ARCHIVE_TIERING_RESEARCH_2026-10-01.md) — research
  and recommendation for a partition-aware Archive section / storage tiering (S3 mechanics,
  competitors, live fleet sizing, existing-code defects, phased plan).
- [`ARCHIVE_EXECUTION_2026-10-18.md`](./ARCHIVE_EXECUTION_2026-10-18.md) — how archive and restore
  actually run: preflight, permissions and IAM policy, bucket guardrails, jobs, planner skip.
- [`TABLE_DETAIL_POLISH_2026-10-18.md`](./TABLE_DETAIL_POLISH_2026-10-18.md) — what moved where on
  the table page (checks to Overview, Archive to Cost, Properties to Config) and why.
- [`TABLE_DETAIL_INSIGHTS_2026-10-18.md`](./TABLE_DETAIL_INSIGHTS_2026-10-18.md) — passed-over
  nights, measured maintenance impact, commit summaries, delete debt, manifest strip, revived
  recommendations, column heatmap, maintenance holds, and the metadata-cost accounting fix.

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