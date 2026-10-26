# Cryo documentation

User and contributor documentation for Cryo. The published docs site is
<https://tech-pw.github.io/cryo/>; its sources are in [`../site/`](../site/).

Architecture and contracts:

- [`design/hld.md`](./design/hld.md): components, data flow, deployment
- [`design/lld.md`](./design/lld.md): schemas, interfaces, planner algorithm, rendered SQL
- [`design/smart_schedule.md`](./design/smart_schedule.md): event-driven Smart Maintenance design
- [`architecture.md`](./architecture.md): architecture and AI strategy
- [`../README.md`](../README.md): overview and quickstart

## Install and operate

- [`operator-guide.md`](./operator-guide.md): deployment and day-2 operations
- [`operator/README.md`](./operator/README.md): operator runbooks (install, run triage, queue, scan health, config)
- [`BATCH_MAINTENANCE.md`](./BATCH_MAINTENANCE.md): the nightly batch maintenance pipeline
- [`maintenance-batch-dispatch.md`](./maintenance-batch-dispatch.md): how batch jobs are dispatched
- [`SAFE_ROLLOUT.md`](./SAFE_ROLLOUT.md): rolling maintenance out safely
- [`WORKER_STEP_LEDGER.md`](./WORKER_STEP_LEDGER.md): per-statement step ledger and orphan recovery
- [`UPGRADE_ROLLBACK.md`](./UPGRADE_ROLLBACK.md): upgrade and rollback
- [`BACKUP_RESTORE.md`](./BACKUP_RESTORE.md): backup and restore
- [`SLOS.md`](./SLOS.md): service-level objectives
- [`shared-s3-anomalies.md`](./shared-s3-anomalies.md): shared S3 warehouse-path anomalies
- [`trino-catalog-bindings.md`](./trino-catalog-bindings.md): Trino catalog bindings
- [`ARCHIVE_EXECUTION_2026-10-18.md`](./ARCHIVE_EXECUTION_2026-10-18.md): how archive and restore run (preflight, IAM, guardrails)
- [`SLACK_REPORT_FORMAT_GUIDE.md`](./SLACK_REPORT_FORMAT_GUIDE.md): daily Slack report format

## API, capabilities and integrations

- [`api.md`](./api.md): REST API guide
- [`CAPABILITIES.md`](./CAPABILITIES.md): generated capability and engine-operation support contract
- [`ECOSYSTEM_CONNECTORS.md`](./ECOSYSTEM_CONNECTORS.md): connector manifest and conformance contract
- [`MAINTENANCE_DECISION_RECORD.md`](./MAINTENANCE_DECISION_RECORD.md): Maintenance Decision Record v1
- [`AI_ASSISTANT.md`](./AI_ASSISTANT.md), [`AI_MCP_OPERATOR_GUIDE.md`](./AI_MCP_OPERATOR_GUIDE.md), [`MCP_CLIENT.md`](./MCP_CLIENT.md): Ask Cryo and MCP
- [`assistant-eval-nightly.md`](./assistant-eval-nightly.md): proposed Ask Cryo eval job (not enabled)
- [`scanner-data-points.md`](./scanner-data-points.md): scanner field-level parity contract
- [`schemas/`](./schemas/): JSON schemas for connector manifests, evidence and benchmarks

## Cost and savings

- [`cost-savings.md`](./cost-savings.md): cost model and savings math
- [`s3-request-cost-tracking.md`](./s3-request-cost-tracking.md): per-table S3 request-cost attribution
- [`ARCHIVE_TIERING_RESEARCH_2026-10-01.md`](./ARCHIVE_TIERING_RESEARCH_2026-10-01.md): partition-aware archive and storage tiering
- [`PARTITION_ACCESS_VISUALIZATION_PLAN_2026-08.md`](./PARTITION_ACCESS_VISUALIZATION_PLAN_2026-08.md): partition access design
- [`QUERY_COST_USER_ATTRIBUTION_PLAN_2026-08.md`](./QUERY_COST_USER_ATTRIBUTION_PLAN_2026-08.md): user-level query cost attribution
- [`top25_trino_cost_query.sql`](./top25_trino_cost_query.sql): sample top-25 Trino cost query

## Engines

- [`SPARK_ENGINE_OPTIMIZATION_2026-09-25.md`](./SPARK_ENGINE_OPTIMIZATION_2026-09-25.md): Spark efficiency and reliability
- [`native-engine-scoping-research.md`](./native-engine-scoping-research.md): native engine scaling and readiness
- [`native-iceberg-go-compaction-plan.md`](./native-iceberg-go-compaction-plan.md), [`native-datafusion-compaction-plan.md`](./native-datafusion-compaction-plan.md): native compaction designs
- [`native-pod-overhaul.md`](./native-pod-overhaul.md), [`native-dispatch-hardening-plan.md`](./native-dispatch-hardening-plan.md): native Kubernetes dispatch
- [`native-go-compaction-throughput.md`](./native-go-compaction-throughput.md): local compaction throughput loop
- [`native-iceberg-rust-engine-errors.md`](./native-iceberg-rust-engine-errors.md): native Rust engine review
- [`amoro-evaluation.md`](./amoro-evaluation.md): Apache Amoro evaluation

## Research and design

- [`research-and-roadmap.md`](./research-and-roadmap.md): research notes and roadmap
- [`current-state.md`](./current-state.md): pointer to what ships and the pre-1.0 floor
- [`PAPER_IMPLEMENTATION_AUDIT_2026-09-24.md`](./PAPER_IMPLEMENTATION_AUDIT_2026-09-24.md): cited papers read against the code
- [`implementation/workload-aware-optimization.md`](./implementation/workload-aware-optimization.md): AutoComp, PTO, WAIR and delete policy
- [`ICEBERG_MAINTENANCE_INTELLIGENCE_RFC.md`](./ICEBERG_MAINTENANCE_INTELLIGENCE_RFC.md): maintenance intelligence RFC
- [`TABLE_DEDUPLICATION_DESIGN.md`](./TABLE_DEDUPLICATION_DESIGN.md): table deduplication design
- [`CRYOBENCH.md`](./CRYOBENCH.md), [`PRIVACY_PRESERVING_BENCHMARKS.md`](./PRIVACY_PRESERVING_BENCHMARKS.md): benchmarks and evidence
- [`DEPLOYED_CONFORMANCE.md`](./DEPLOYED_CONFORMANCE.md), [`EXTERNAL_EVIDENCE_ATTESTATION.md`](./EXTERNAL_EVIDENCE_ATTESTATION.md): conformance and evidence

## Develop and contribute

- [`developer-guide.md`](./developer-guide.md): development workflow
- [`local-docker-compose.md`](./local-docker-compose.md): local Docker Compose layout
- [`LOCAL_DEV_ONLY_INVENTORY.md`](./LOCAL_DEV_ONLY_INVENTORY.md): local-only components that must not ship to production
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md): contributing

## Project

- [`GOVERNANCE.md`](./GOVERNANCE.md): governance
- [`SUPPORT_POLICY.md`](./SUPPORT_POLICY.md): support policy
- [`VERSIONING.md`](./VERSIONING.md): versioning
- [`OSS_READINESS.md`](./OSS_READINESS.md), [`oss-launch-plan-2026-09-25.md`](./oss-launch-plan-2026-09-25.md): open-source launch plan
- [`OSS_MIRROR.md`](./OSS_MIRROR.md): how the public GitHub mirror is built
- [`brand/CRYO_BRAND_GUIDE.md`](./brand/CRYO_BRAND_GUIDE.md): brand guide
- Docs site design: [`DOCS_SITE_REDESIGN_HANDOFF_2026-10-05.md`](./DOCS_SITE_REDESIGN_HANDOFF_2026-10-05.md), [`DOCS_SITE_DESIGN_RESEARCH_2026-10-05.md`](./DOCS_SITE_DESIGN_RESEARCH_2026-10-05.md)

## Conventions

- One topic per file. Update docs in the same PR as the code change that affects them.
- Internal working notes (dated plans, post-mortems, reviews) don't belong here. Keep them in
  `docs/_private/`, which is gitignored and never published.
