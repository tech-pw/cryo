# Wave 0 baseline, evidence, and stop-ship policy

Baseline date: 2026-08-04.  
Roadmap: [`cryo-10-of-10-plan.md`](./cryo-10-of-10-plan.md), Wave 0.

This document separates reproducible repository facts from observed production
facts and open claims. It does not graduate Wave 0 by itself.

## Reproduce the repository snapshot

```bash
python -m cryo.devtools.baseline
python -m cryo.devtools.baseline --output docs/baseline-2026-08-04.json
```

The checked-in snapshot is
[`baseline-2026-08-04.json`](./baseline-2026-08-04.json). The tool has a
determinism test and collects source size, tests, migrations, package hotspots,
frontend-stack drift, and the capability matrix. Re-running it on a later tree
is expected to produce a different snapshot; retain dated snapshots rather than
silently replacing historical evidence.

## Evidence grades

| Grade | Meaning |
|---|---|
| A | Reproducible from versioned code, tests, generated artifacts, or a query with retained raw output. |
| B | Direct production observation with date and operator/system provenance, but not independently reproduced. |
| C | Documentation, issue, or design record without direct retained measurement. |
| D | Hypothesis, estimate, or unverified claim. It cannot graduate a capability. |

## Starting baseline

| Signal | Baseline | Grade | Evidence |
|---|---:|---|---|
| Production fleet | Approximately 11,000-16,000 tables | B | Master plan §0.3; operator-observed production cohort |
| Capability coverage | 28/28 engine-operation cells declared | A | `tests/test_capabilities.py`; generated capability contract |
| Capability states | 14 direct, 2 conditional, 4 fallback, 7 config-only, 1 unsupported | A | Dated baseline JSON |
| Catalog adapters | 5 canonical products/protocol variants | A | Capability manifest and source-factory tests |
| Backend Python | 283 files / 68,341 lines | A | Dated baseline JSON |
| Frontend source | 242 files / 39,020 lines | A | Dated baseline JSON |
| Backend tests | 209 files / 1,297 `test_*` functions | A | Dated baseline JSON; collection is not a pass claim |
| Frontend test files | 29 | A | Dated baseline JSON; collection is not a pass claim |
| Alembic graph | 50 revisions, one head (`20260731_0035`) | A | AST-parsed migration graph |
| Ant Design runtime use | 0 imports; no dependency | A | Dated baseline JSON and frontend build |
| Governor unbounded native scan | Incident observed and bounded fix shipped | B | Master plan §0.1 and Jira PDAE-8272 |
| Native throughput ceiling | Unknown; slot cap was not the observed bottleneck | B | Master plan §0.3 and Jira PDAE-8275 |
| 100,000-table target | Not demonstrated | D | Roadmap target only |
| 30-day representative soak | Not completed | D | External/time-based gate remains open |
| Design-partner evidence | No completed interviews recorded here | D | External gate remains open |

The line and test counts include the active implementation tree on the baseline
date. Generated `openapi.d.ts`, dependencies, build output, and migrations are
excluded from handwritten source hotspots.

## Package and hotspot map

Largest package surfaces:

| Package | Files | Lines | Risk |
|---|---:|---:|---|
| `web/src/pages` | 70 | 16,793 | Page ownership and cross-page patterns |
| `web/src/components` | 127 | 15,103 | Shared UI contract and reuse pressure |
| `cryo/api` | 36 | 13,076 | Broad HTTP surface and repository coupling |
| `cryo/engines` | 36 | 8,651 | External effects and dialect boundaries |
| `cryo/scanner` | 25 | 7,802 | Fleet-scale I/O and memory behavior |
| `cryo/db` | 19 | 5,561 | Durable truth and migration compatibility |
| `cryo/governor` | 12 | 3,160 | Admission, fairness, and convergence |
| `cryo/planner` | 8 | 2,790 | Safety and maintenance intelligence kernel |
| `cryo/worker` | 4 | 2,333 | Effect execution and recovery |

Largest handwritten files:

| File | Lines | Required direction |
|---|---:|---|
| `cryo/api/routes.py` | 2,362 | No new unrelated routes; split by owned contract |
| `web/src/api/client.ts` | 2,278 | Move new domains behind focused client modules |
| `cryo/worker/worker.py` | 1,846 | Extract intent/effect/reconciliation contracts during Wave 1 |
| `cryo/db/models.py` | 1,704 | Split only with additive migration-safe ownership |
| `cryo/api/advanced_routes.py` | 1,651 | Prevent further feature aggregation |
| `cryo/api/schemas.py` | 1,454 | Co-locate new schemas with focused routers where safe |
| `cryo/api/extensions.py` | 1,357 | Freeze unrelated additions and decompose |
| `web/src/api/types.ts` | 1,282 | Prefer generated OpenAPI types for new contracts |
| `cryo/engines/native/engine.py` | 1,073 | Preserve routing and subprocess boundaries while extracting |
| `web/src/components/table-detail/OverviewTab.tsx` | 1,046 | Split by operator task, not visual card type |

These are prioritization signals, not instructions for a mechanical line-count
refactor. Correct safety boundaries take precedence.

## Migration support statement

Current supported database contract:

1. A fresh database must upgrade through the complete Alembic graph to the
   single current head.
2. Existing pre-1.0 installations must apply every ordered migration; skipping
   revisions or editing an applied revision is unsupported.
3. Migrations remain additive-first. Destructive contract steps require an
   explicit compatibility window and restore plan.
4. Downgrade functions are not a claimed rollback mechanism. Rollback means
   restoring a tested database backup plus the compatible application version.
5. N-1 to N upgrade, failed-upgrade restore, and production-shaped data volume
   are not yet proven gates. Until they are automated, GA upgrade safety remains
   open.
6. A pre-1.0 squash may be designed later, but must include a bridge for every
   supported deployed revision and cannot rewrite customer history in place.

## Feature freeze

Until the Safety Alpha exit gate passes:

- active product implementation is restricted to Wave 0 truth work and Wave 1
  safety-kernel dependencies;
- production incident fixes, security fixes, dependency support fixes, and
  evidence instrumentation may interrupt the freeze;
- PTO, WAIR, and other layout intelligence remain shadow or human-approved;
- no new autonomous destructive operation may be enabled;
- Flink remains configuration-only;
- native support may expand only after a manifest cell gains exact conformance
  evidence; no hand-rolled native manifest rewrite is in scope;
- no new feature may be added to an existing hotspot above 800 lines without an
  explicit decomposition or recorded exception.

External discovery may continue within the roadmap's 10% budget. It does not
authorize implementation outside the dependency order.

## Stop-ship policy

Any condition below blocks release and pauses the affected admission path:

- known or suspected data loss, cross-table deletion, or unbounded orphan scope;
- an acknowledged intent disappears, executes outside its approved scope, or
  cannot be joined to its audit trail;
- a dangerous effect is duplicated or a stale worker can commit;
- an unknown external effect is blindly retried rather than probed and
  reconciled;
- tenant or role isolation can be bypassed;
- queue depth, per-tick work, watcher work, or memory grows without a configured
  bound under representative load;
- the API, UI, runtime, or docs disagree with the capability manifest;
- a fallback destination is incompatible with the operation it receives;
- a critical/high exploitable security finding lacks containment;
- backup restoration cannot meet the currently declared recovery boundary.

Required response:

1. halt the affected cohort or operation;
2. preserve intent, engine, snapshot, audit, trace, and configuration evidence;
3. classify the external effect as not-started, applied, not-applied, or unknown;
4. reconcile safely before any retry;
5. add a regression/fault test and update the evidence registry;
6. require an explicit owner to lift the stop.

## Evidence registry

| Claim | Grade | Verified | Owner | Durable source | Status |
|---|---|---|---|---|---|
| Engine-operation support is exhaustive | A | 2026-08-04 | `engines/*` | Manifest, conformance tests, generated docs | Active |
| Runtime rejects incompatible native fallbacks | A | 2026-08-04 | `engines/native` | Routing + worker/Governor tests | Active |
| UI and API consume the same capability schema | A | 2026-08-04 | `api`, `web` | OpenAPI, TypeScript build, UI tests | Active |
| Frontend is Radix/Tailwind, not Ant Design | A | 2026-08-04 | `web` | Baseline detector, package manifest, build | Active |
| PTO DOI `10.1145/3786681` belongs to PTO | B | 2026-08-04 | intelligence | Crossref verification recorded in implementation spec | Active |
| Native manifest rewrite has no direct Go path | A | 2026-08-04 | `engines/native` | Capability cell and routing test | Active |
| Fleet is approximately 11,000-16,000 tables | B | 2026-08-04 | operations | Master plan §0.3 production observation | Needs retained query export |
| Governor candidate bounding fixes the OOM class | B | 2026-08-04 | governor | Incident record and bounded code | Needs fault/scale proof |
| Native is faster or cheaper than SQL engines | D | 2026-08-04 | native | No controlled benchmark | Quarantined |
| 100,000-table scale, GA SLOs, DR, and 30-day soak | D | 2026-08-04 | reliability | No qualifying evidence | Open |

## Design-partner interview plan

Target 12 interviews across four roles:

| Interviews | Role | Questions to prove |
|---:|---|---|
| 1-3 | Data platform on-call | Failure recognition, pause/reconcile needs, evidence required during incidents |
| 4-6 | Iceberg platform owner | Engine/catalog constraints, maintenance policy, migration and blast-radius expectations |
| 7-9 | FinOps or engineering leader | Cost proof, budget ownership, acceptable confidence and payback windows |
| 10-12 | Data producer/consumer owner | Foreground impact, freshness trade-offs, approval and explanation needs |

Each record must retain date, role, environment scale, current workflow, top
failure, decision criteria, verbatim evidence where permitted, and whether the
finding confirms or rejects a roadmap hypothesis. No interview is marked
complete in this repository without a redacted durable record approved for
storage.

The first-six-interview Wave 0 gate and Wave 1 staffing confirmation remain
external blockers. Repository implementation must not claim they passed.
