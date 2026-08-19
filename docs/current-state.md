# Current implementation state

Last reconciled: 2026-08-18.

PDAE-8121 (umbrella "Iceberg maintenance via cryo") stays **Dev Complete**: the
platform is deployed (schedule, expire/orphan/optimize, table-level cost
heuristics). It is **not** "every table healthy." Wave 1 (PDAE-8274) is still
In Progress; Wave 2 (PDAE-8276) is plan-only — see
[`PDAE-8121-umbrella-reconciliation-2026-08-18.md`](./PDAE-8121-umbrella-reconciliation-2026-08-18.md)
and [`PDAE-8276-wave2-rollout-plan.md`](./PDAE-8276-wave2-rollout-plan.md).
Fleet fixes from 2026-08-18 are in `f967caa` (digest pin `f922d0c`).

This is a concise snapshot, not a release claim. The generated
[`CAPABILITIES.md`](./CAPABILITIES.md) is authoritative for catalog and
engine-operation support. The evidence-gated future program lives in
[`cryo-10-of-10-plan.md`](./cryo-10-of-10-plan.md).

## Product shape

Cryo is a self-hosted Apache Iceberg maintenance and health control plane. It:

- discovers tables through read-only Source adapters;
- stores scan-derived health, partition, cost, and workload signals;
- builds engine-agnostic maintenance plans;
- admits work against live load and policy;
- executes through separately configured Destination engines;
- records plans, jobs, external effects, outcomes, and audit evidence;
- gives operators health, cost, maintenance, governance, and connection views.

Sources and Destinations remain separate in the database, API, and runtime. The
Planner emits operations and predicates, never SQL. Trino and Spark renderers
remain separate.

## Production ground truth

- The known fleet is approximately 11,000-16,000 tables.
- A Governor failure previously rescanned an 11,000+ native queue every 30
  seconds while native dispatch did not consume replica entries. Candidate
  selection is now bounded by replica capacity.
- `NativeJobWatcher` processes a bounded, oldest-first batch; the default is 200.
- Missing metadata or manifest references reported as `NoSuchKey` can be
  classified as dead-table failures without treating repairable missing data
  files the same way.
- PDAE-8347 (2026-08-18): Spark-on-K8s `BaseMetastoreTableOperations.current`
  failures on live gold tables, once `[root cause]` is preserved, are
  `NoSuchKeyException` on the catalog's current metadata pointer -- a stale
  HMS/REST location after a concurrent snapshot expire, not a commit conflict
  and not a dropped table. Cryo retries those Spark jobs (plus
  `CommitFailedException`) with backoff. Bare `NoSuchKey` without
  `metadata.json` / manifest wording is **not** treated as
  `is_table_not_found_error`, so gold tables are not deactivated. A
  `remove_orphan_files` race deleting *current* metadata is unlikely at the
  default 7-day orphan cutoff; the Spark job fails at `loadTable` *before*
  that procedure runs, so the concurrent writer is expire/commit from another
  engine, not this job's own orphan cleanup. Temp/test tables are skipped at
  `effective_preset` (except `testpress` and names with `test` in the middle).
- Raising the native slot cap to 80 did not prove that slot capacity is the
  production throughput bottleneck.

These facts motivate the roadmap. They do not substitute for a measured soak,
published SLO attainment, or an independently reproduced benchmark.

## Shipped control-plane foundations

### Discovery and planning

- Iceberg REST, Polaris, Nessie, Hive Metastore, and AWS Glue source adapters.
- PyIceberg and Go scanner paths with stored table, partition, snapshot,
  manifest, delete-file, health, and cost signals.
- Partition-aware planning from stored scan data, including identity/day/month/
  year transforms.
- GB-budgeted chunks, max partitions per chunk, oversize-partition isolation,
  small-table bypass, small-file gating, cooldown, deadlines, and hot-partition
  checks.
- Canonical operation ordering (`CORRECT_ORDER`: convert_deletes →
  rewrite_position_deletes → compact → compute_statistics → manifests → expire
  → orphan) via `sort_operations_canonically()`, plus engine-specific SQL
  renderers with golden tests.

### Scheduling, admission, and execution

- PostgreSQL durable state plus Redis queues, leases, per-table locks, and live
  run events.
- Leader-elected Scheduler and Governor services.
- Live Trino load probes, replica caps, round-robin admission, off-peak windows,
  adaptive budgets, and failure isolation.
- Worker execution, audit records, actual-effect fields, and native Kubernetes
  Job dispatch.

### Safety and recovery

- Write-ahead `cryo.job_steps` external-effect ledger.
- Immutable, versioned maintenance intents binding canonical plan/hash, actor,
  tenant, snapshot, policy/approval, expiry, capabilities, budgets, and containment.
- Monotonic per-table fencing, attempt lineage, and destination-specific effect receipts.
- Plan hashing/invariant verification and single-use approval consumption.
- Quiet-window checks for dangerous operations.
- Orphan and intent recovery with engine probes, job-owner adoption leases, and
  no blind replay of ambiguous effects.
- Generation-stamped global, tenant, and destination emergency pause checked at
  admission and again before effect submission.
- Bounded tenant round-robin admission and a tenant-scoped Control Room for
  holds, immutable intents, effects, and audited operator reconciliation.

The repository implementation and local stop-ship suites for the Wave 1 safety
kernel pass as of 2026-08-04. Durable intents remain default-off while the
production-shaped 10,000-attempt fault campaign, deployed pause-latency
measurement, and rollout/rollback evidence are still open. Local test evidence
must not be presented as those production graduation results.

### Production-proof foundations

- Bounded correlation IDs propagate from API requests into durable run summaries, Governor and
  Worker execution, intent attempts/effects, engine references, scan runs, and reconciliation. The
  web client seeds requests and includes the returned ID in operator-facing API failures.
- API and daemon `/metrics` endpoints, Prometheus rules, and the Cryo Safety & SLOs dashboard expose
  pause propagation, exact due reconciliation backlog, oldest uncertain-effect age, API availability,
  and API latency. `docs/SLOS.md` defines the production evidence windows.
- Production Python dependencies are exported from `uv.lock`; base images use immutable manifest
  digests; Spark downloads are checksum-verified.
- CI scans the built image and retains an SPDX SBOM. Tagged releases publish provenance and use
  keyless Cosign signatures. Helm supports digest references and rejects production renders without
  required image digests.
- Dependabot covers pip, npm, GitHub Actions, Go modules, and Cargo.
- The leader scheduler prunes maintenance audit rows using a configurable, indexed, bounded
  retention policy (365 days by default).
- CryoBench v1 generates a deterministic, digest-addressed 12,938-table heavy-tailed fleet and a
  machine-readable fault matrix. Local models now prove rollback/retry after an interrupted durable
  transition, queue reconstruction from SQL state, and database-authoritative expiry. Real
  PostgreSQL failover, Redis process/data loss, and deployed clock-skew evidence remain open.
- Encrypted PostgreSQL backup/restore tooling uses 4 MiB AES-256-GCM chunks, checksum manifests,
  secret-safe commands, and a mandatory restore force gate. An optional Helm CronJob publishes
  manifest-committed backups to S3 and applies prefix-scoped retention. Production enablement and
  two measured restore drills remain open.
- Destination rollout policies evaluate durable terminal attempts and effects after a minimum
  sample. Budget breaches write the existing audited destination pause and fire a critical alert;
  only an operator can resume. A deployed forced-regression/rollback exercise remains open.
- The checked-in production override disables the insecure development-token bridge. Production
  requires configured OIDC rather than self-declared roles.

These are repository controls, not external review or production proof. Representative soak, DR
drills, deployment SLO history, pilot evidence, and an independent security review remain open.

## Capability truth

The canonical typed manifest is `cryo/capabilities/manifest.py`. It is consumed
by native routing, SQL-fallback compatibility checks, `GET /api/v1/capabilities`,
the Connections > Capabilities ledger, generated OpenAPI types, and
[`CAPABILITIES.md`](./CAPABILITIES.md). CI rejects generated-document drift.

At a glance:

| Engine | Current truth |
|---|---|
| Trino | Production execution for core maintenance and statistics. Position-delete minor rewrite is unsupported. Delete conversion maps to OPTIMIZE and lacks Spark thresholds. |
| Spark / EMR | Broadest direct operation surface, including position-delete rewrite. A working Spark execution destination is required. |
| Native Go | Direct snapshot expiry and orphan cleanup. OPTIMIZE is beta and allowed only for Go, zero-delete-file, non-z-order tables without unsupported partitioned v3 deletion vectors. Other operations use compatible SQL fallbacks. |
| Native Rust | Snapshot/orphan path remains; data-file compaction is blocked upstream and should not be presented as available. |
| Flink | Experimental configuration generation only. Cryo does not submit or observe a Flink maintenance job, so this is never reported as executed work. |

Native manifest rewrite is not silently dropped: it routes to a compatible SQL
fallback when configured, otherwise it is recorded as skipped. Position-delete
rewrite permits Spark fallback, not Trino.

## API and frontend

- FastAPI with JWT roles (`viewer`, `operator`, `admin`) and generated OpenAPI.
- React 19, TypeScript, Vite, Radix UI primitives, Tailwind CSS, TanStack Query,
  TanStack Table, Recharts, and IBM Plex fonts bundled through `@fontsource`.
- The Ant Design migration is complete; new UI must use shared
  `web/src/components/ui/` primitives.
- Connections includes Lakehouses, Engines, Capabilities, AI Providers, Slack,
  and admin operations.
- Bulk actions transmit a selection/filter specification, not IDs from one page.
- CI covers TypeScript, Vitest, production build, style budget, accessibility,
  visual regression, and Impeccable checks. Individual jobs remain the source of
  truth for the latest result; do not copy old test counts into this document.

## Intelligence surfaces

Cryo has implementations or scaffolding for health scoring, cost projection,
query telemetry, AutoComp traits and admission signals, delete lifecycle policy,
layout recommendations, anomaly detection, skillsets, lineage, and governance.

These surfaces have different maturity. Presence in code is not evidence that a
model is calibrated, causally validated, safe to auto-apply, or proven at fleet
scale. Wave 3 and Wave 4 of the master plan define the missing observation-to-
outcome lineage, confidence intervals, abstention, canaries, and differential
oracles.

The first Wave 3 evidence slice is implemented. Maintenance outcomes use frozen
pre-action reclaim estimates and audited realized bytes, require intent/plan and
post-scan lineage, and abstain when lineage is incomplete. The previous cost
training path leaked realized bytes into its prediction feature; it has been
removed, and legacy caches are rejected. Chronological holdout evaluation now
reports domain abstentions separately instead of scoring heuristic fallback as a
learned prediction.

AutoComp comparison now includes size-only, file-count, threshold, FIFO, and
seeded-random baselines with one utility objective, regret, and evidence
confidence. These are local comparison controls. They do not establish
production VMY lift or pass the Wave 3 calibration gate.

Runtime and measured rewrite-output-byte intervals now use split-conformal ratio
models over the lineage dataset. Point fitting, interval calibration, and
chronologically later evaluation use disjoint observations. Models prefer
per-engine/action evidence, label pooled fallback explicitly, and abstain for
missing features, insufficient samples, unseen actions, values outside the
observed feature envelope, or excessively wide bands. Rewrite outcomes come
from durable effect receipts' `actual_bytes_written`, not reclaimed bytes.
The authenticated evaluation endpoint reports median/p90 MAPE, interval
coverage, abstention rate, lineage completeness, and per-group scope. Local
coverage is not a production calibration claim.

AutoComp's realized-value loop now joins its frozen reclaim prediction to the
audited realized reclaim and effect receipt's measured output bytes. It reports
actual-to-predicted reclaim, reclaim per rewritten output byte, temporal ratio
drift, evidence completeness, and confidence. A bounded 0.5-1.5 calibration
multiplier affects future partition scores only after at least 20 complete,
stable optimize outcomes. Sparse, malformed, unknown-schema, or drifting
feedback resolves to the behavior-safe multiplier 1.0. This local adaptation
does not establish fleet-level VMY lift.

Delete-lifecycle evidence now joins frozen pre/post observations to measured
rewrite output, runtime, delete-density reduction, and subsequent p95 query
latency. The authenticated evaluator compares minor
`rewrite_position_deletes` outcomes with a threshold-only
`convert_deletes` cohort only when both have complete lineage and a matched
engine. Historical cohorts are explicitly advisory and can never satisfy the
policy gate because they are not randomized counterfactuals; sparse or
unmatched evidence abstains. A controlled representative holdout remains
required before claiming lower combined read-plus-maintenance cost.

The first Wave 4 PTO slice is also implemented as an operator-only shadow
recommendation. It reduces partition/file-size/row-group/strategy candidates,
rejects high-cardinality identity partitioning, ranks the remaining tuples with
the documented `heuristic_v1` joint objective, and persists low-confidence
evidence in `LayoutRecommendation.detail`. It uses the distinct 30-day table
query count for its evidence threshold, abstains rather than overwriting another
analyzer's recommendation or a non-pending PTO decision, and has no execution or
auto-apply path. A read-only simulation compares its estimated scan fraction
with bounded historical `files_read`/`files_pruned` observations and reports a
wide heuristic interval; it abstains without enough observed file-touch data
and does not mutate recommendation state. Workload-distribution drift is measured
with total-variation distance against the recommendation's frozen pattern
distribution.

WAIR now has a pure offline boundary detector and deterministic GB/file-budget
selector. An operator-only report reads pre-stored file-bound samples and query
ranges from the `TableStats.raw` evidence contract, identifies only files crossing query-range edges,
and abstains when either evidence set is absent. Both Python and Go scanners now
retain a deterministic bounded sample of decoded current-snapshot file bounds;
telemetry ingestion extracts concrete two-sided SQL ranges and the pattern
aggregator persists frequency-ranked range evidence. The cross-engine
differential oracle renders each PTO intent through Trino and Spark, fingerprints
the resulting SQL, and rejects parameters either renderer drops. The oracle now
validates versioned partition scopes against a 10 GiB/500-file ceiling and proves
that both dialects render the same scope predicate. Spark's renderer can now
separate the scoped rewrite from its default persistent table-property update,
allowing scoped binpack rewrite equivalence without changing existing execution
defaults. Trino clustering, partition-evolution, row-group, and unbounded rewrite
intents remain non-equivalent.
Operators can explicitly record a v2 differential dossier on the
recommendation; each recording also appends an audit event with the actor and
exact PTO-parameter and rewrite-scope fingerprints. Read-only inspection does
not mutate evidence. Even approved equivalent scopes remain canary-ineligible
until causal holdout evidence and production safety gates exist. An operator-only
temporal replay now compares earlier and later observed file-touch baselines and
records stability plus implied improvement, but deliberately marks itself
non-qualifying because the historical data contains no candidate-layout
counterfactual.

Operators can now record an externally run `pto-controlled-shadow/v1` experiment.
The contract requires isolated clones, query-signature randomization, at least 30
queries per arm, identical per-arm query-signature-set fingerprints, exact
binding to the recorded PTO parameters and rewrite scope,
at least 5% scan-byte improvement, no more than 10% p95 latency regression, and
no material error-rate regression. Passing this contract satisfies only the
holdout-evidence blocker. Synthetic output conformance and production safety
remain independent blockers; Cryo still cannot launch the experiment or execute
the recommendation.

Production safety can now be recorded only from Cryo-owned records. The
`production-safety/v1` evaluator requires an enabled canary rollout policy, a
healthy adequately sampled attempt cohort, enough resolved effect receipts, an
unpaused destination, and a recent automatic halt followed by operator resume.
The evidence expires after 15 minutes and becomes stale immediately if the
destination rollout policy changes. PTO/scope, controlled-shadow, and output
conformance evidence are also hash-bound to the current recommendation. This can
satisfy the safety blocker. Real output evidence now has a separate ingestion
boundary: only the `evidence_collector` role can submit it; that role has no
viewer/operator permissions, and operators/admins cannot impersonate it. The
server binds fresh production observations to the collector identity, exact
PTO/scope hashes, source/output snapshot IDs, metadata-location hashes, and the
complete conformance payload. Synthetic operator fixtures still cannot satisfy
production provenance. The collector contract and runbook exist, but no real
deployed run is claimed, so no canary is enabled.
Holdout lift, bounded rewrite execution, approved canaries, and auto-apply remain
open.

Wave 5 repository evidence has started. Maintenance Decision Record v1 provides
a privacy-safe, integrity-bound export of intent, policy, immutable plan,
attempts, effects, outcomes, and exact capability limitations. A deterministic
release dossier fingerprints capability, benchmark/fault, safety, SLO/recovery,
supply-chain, MDR, and upgrade/rollback artifacts; the signed release workflow
retains it. The same workflow now renders and fingerprints transaction-wrapped
PostgreSQL SQL for the full Alembic graph in both directions. This proves the
repository graph renders, not that deployed data and service availability
survive a migration. The dossier deliberately reports every production,
external-review, consumer, and case-study gate as missing.

A public support policy defines supported release intent, community channels,
incident severity, safe diagnostic evidence, and the capability boundary. It
does not claim a commercial support SLA.

External gates can now enter the release dossier only through revision-bound,
gate-context-checked Ed25519 attestations from explicitly trusted verifier keys.
Counted gates deduplicate evidence IDs and retain their required counts. This
provides a durable route for real evidence; it does not make any currently
missing drill, review, pilot, consumer, or contributor evidence exist.
The registry covers every external Wave 0-6 graduation gate rather than only
the final GA checklist.

Wave 6 foundations are opt-in and non-executing. The aggregate fleet benchmark
publishes only coarse, rounded cohorts with at least 10 distinct tables and 20
lineage-complete outcomes, suppresses smaller cohorts, and contains no direct
identifiers. It is k-anonymous aggregation, not differential privacy, and
nothing is transmitted automatically. The `cryo.connector/v1` manifest and
conformance checker give ecosystem source/engine packages a stable protocol
boundary without dynamic plugin discovery or implied core ownership.

## Spark-on-K8s driver cleanup (2026-08-05, extended 2026-08-12)

Spark keeps completed **driver** pods by design; they are not owned by Cryo's wrapper Job, so
Job `ttlSecondsAfterFinished` never reclaims them (live: ~1000 orphaned `cryo-spark-*-driver`
pods on data-platform). Fix:

- `SparkK8sClient.cleanup_after_run` deletes `{job}-driver` + the per-job AWS Secret after a
  terminal/cancelled run; `SparkK8sEngine.execute` always calls it in `finally`.
- Credentials Secrets are also `ownerReference`d to the wrapper Job so TTL cascades Secret
  deletion for fire-and-forget admin dispatches.
- Worker/Governor Roles gain `pods/delete` so in-cluster cleanup is allowed.
- `submit()` (PDAE-8352's async fire-and-forget fallback path, 2026-08-11) never calls
  `execute()`, so its `finally: cleanup_after_run(...)` never runs for a submit()-dispatched
  job -- `cryo.governor.fallback_sweep.sweep_pending_fallback_dispatches` now calls
  `cleanup_after_run` itself once a SUBMITTED row resolves to a terminal state.
- `cleanup_stale_driver_pods` (a separate periodic sweep, not tied to any specific job's
  completion) is kept as defense-in-depth alongside the above -- confirmed live, 2026-08-06:
  3,276 completed driver pods had accumulated with zero owner references, catching cases where
  neither `execute()`'s `finally` nor the fallback sweep ever ran for a given job (crash before
  reaching either, a dispatch path that predates this fix, etc).

## Known gaps and non-claims

- No external design-partner interviews, third-party security audit, published
  case study, or time-based production soak can be completed by repository code.
- Native throughput and cost superiority are not established.
- Flink execution is not implemented.
- Rust native data-file compaction remains upstream-blocked.
- Production SLOs, disaster recovery, and multi-version migration survival need
  repeated deployed exercises. Upgrade/rollback rendering is captured, but a
  production-shaped data-preservation and availability drill remains open.
- PTO/WAIR recommendations must remain shadow or approval-gated until their
  roadmap evidence gates pass.

## Verification

Canonical local checks:

```bash
make capabilities-docs-check
make lint
make typecheck
make test
make web-quality
make web-build
```

Engine integration, native differential, accessibility, visual, and deployment
checks are separate targets because they need their respective runtime
dependencies. A green unit suite must not be described as production proof.
