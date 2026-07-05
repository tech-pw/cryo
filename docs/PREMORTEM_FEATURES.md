# Cryo Premortem, Part 2 — Every Feature, 6 Months Out

Companion to `docs/PREMORTEM.md` (which covered auth/secrets/deployment/scale/ops). This pass goes
feature-by-feature through the actual behavior of every subsystem — scanner, planner, governor,
compliance, AI/ML layer, integrations, frontend — to answer: **when this has been running against
a real fleet for 6 months, what specifically goes wrong, and why?** Every finding below cites a
real file/line. Actionables are appended to `docs/PREMORTEM_ACTIONABLES.md`.

---

## Remediation status (2026-07-05 hardening pass)

The table below maps each section's original finding to what was fixed in this session. Items marked
**Open** remain tracked in `docs/PREMORTEM_ACTIONABLES.md`.

| Section | Original risk | Status | Fix / notes |
|---|---|---|---|
| Native engine | Fake `ok=True` compaction | **Fixed** | Returns `ok=False`; gated behind `CRYO_NATIVE_ENGINE_ENABLED` (`cryo/engines/native/engine.py`, `factory.py`) |
| Auth / RBAC | Unsigned bearer tokens | **Fixed** | HS256 JWT verification; dev token endpoint; frontend fetches signed tokens (`cryo/api/security.py`, `web/src/lib/auth.tsx`) |
| Scanner manifests | Parallel vs serial split | **Fixed** | Unified `_read_manifest_entries()` with logged warnings + `manifest_read_errors` in raw |
| Streaming signals | `history=` never passed | **Fixed** | `stats_history_for_signals()` wired in scan path |
| Cost accounting | Flat `$0.023/GB` | **Fixed** | `calculate_monthly_storage_cost` from `cryo/cost/s3_pricing.py` in scanner cost |
| Locking | No renewal | **Fixed** | `renew_table_lock()` called per worker statement |
| Bandit confidence | Key mismatch → always confident | **Fixed** | Composite arm keys; Postgres persistence; `POST /api/v1/auth/governor/bandit/reset` |
| PII gate | Bypass without role; fail-open | **Fixed** | `pii_gate_bypass_authorized` + admin role; governor defers on classifier errors |
| Multi-tenancy | Unenforced | **Partial** | Scoped CRUD when `CRYO_ENFORCE_TENANT_ISOLATION=true`; documented single-tenant default (`docs/DEPLOYMENT.md`) |
| AWS integration | Inconsistent errors; plaintext secrets | **Fixed** | Fernet encryption; CloudWatch wrapped in clear `RuntimeError`; daily report checks Slack return |
| AI provider keys | Plaintext + TODO | **Fixed** | Encrypt/decrypt in `cryo/api/extensions.py` |
| Daily report / Slack | `slack_sent` always true | **Fixed** | Checks `send_blocks` return value |
| Frontend auth | Role string as token | **Fixed** | JWT exchange on role change |
| Frontend confirmations | None | **Fixed** | `ConfirmAction` on bulk run, table run, deletes, run cancel, AWS remove |
| Frontend error states | Empty = outage | **Fixed** | `QueryErrorAlert` on dashboard pages |
| Frontend pagination | 500-table silent cap | **Partial** | `TruncatedResultsBanner` + fallback; Runs paginated; dashboard top offenders still server-capped |
| ABAC | Unwired | **Open (P2)** | Marked backend-only in `docs/DEPLOYMENT.md` |
| Audit log actor | No user column | **Open (P2)** | — |
| Model drift checks | On-demand only | **Open (P2)** | — |
| OpenTelemetry | No correlation | **Open (P1)** | — |
| Backend-only UI | tiering/approvals/lineage/FinOps | **Open (P1)** | Documented in `docs/DEPLOYMENT.md` |
| E2E CI | No docker-compose job | **Open (P2)** | Canary workflow for pyiceberg added |

---

## The single scariest finding

**`cryo/engines/native/engine.py:180-214` — the native engine's `OPTIMIZE` (compaction) execution
path is an unimplemented placeholder that reports success anyway.** The code's own comment says "This
is a placeholder — actual implementation would: 1. Get file paths... 2. Call the native engine...
3. Update Iceberg metadata" — and then the method logs and unconditionally returns
`ExecResult(ok=True, ...)` with no files actually rewritten and no `actual_bytes_freed`. Any
destination configured with `EngineType.NATIVE` (a real, selectable option in `worker.py`'s
`DestinationConfig`) will have every compaction job marked `SUCCEEDED` in `AuditRecord`
(`worker.py:355-368`) while doing nothing. Six months out: dashboards show a steady stream of
"succeeded" maintenance runs, small-file counts and health grades never actually improve, realized
savings tracking (which the learned cost model depends on) trains on fabricated success, and nobody
notices because the job status itself is lying, not just missing.

**Second-scariest:** the RBAC/auth bypass already identified in `docs/PREMORTEM.md` is confirmed
worse than first described — `cryo/mcp`'s write-gated `run_preset` tool and the frontend's own
`AuthProvider` (`web/src/lib/auth.tsx:8-33`) route through the *identical* unsigned-token check.
The frontend literally sends the bare string `"admin"`/`"operator"` as the auth token — this isn't
a theoretical bypass an attacker has to discover, it's the shipped, working client behavior today.

---

## 1. Scanner & analysis correctness

`analyze.py`'s parallel manifest-reading path (`_fetch_manifest_entries_safe`, ~line 385) swallows
*any* exception from a corrupt/unreadable manifest and silently returns `[]` — undercounting
`total_files`/`total_size_bytes` with nothing logged. The serial path only catches `TypeError`, so
the same corrupt manifest instead crashes the whole scan. **Which failure mode you get depends on
whether `parallel_manifests` is on** — a nondeterministic, config-dependent split with no user-facing
signal either way. Separately, `enrich_stats_signals` (`signals.py:37-64`) computes streaming/
commit-velocity detection from `history`, but `analyze.py` never passes `history=` into it in the
live scan path — **the streaming-detection feature (cited in this session's own docs as a real
capability) is dead code**, so `health.py`'s streaming-table penalty exemption never fires.

## 2. Health scoring

`health.py`'s `HEALTH_THRESHOLDS` (file counts, partition counts) are flat module-level constants —
not configurable per table size or per deployment. A well-managed 10PB table with 150k files (avg
~70MB each, genuinely fine) gets penalized identically to a poorly-organized 150k-file table that's
actually broken; the score can't distinguish "big" from "unhealthy." Combined with the dead
streaming-detection above, high-commit-frequency streaming tables will chronically sit at
"critical" for snapshot count with no way to raise the threshold per-table — a direct path to
alert fatigue, where operators start ignoring "critical" because it's usually just a known-fine
streaming table.

## 3. Maintenance execution & presets

Destructive operations are expressed as declarative `ALTER TABLE ... EXECUTE` procedure calls
(Trino/Spark), which is genuinely idempotent since the Iceberg connector — not Cryo — decides
which files to touch on re-run. But **there is no real dry-run mode**: "preview" (`api/preview_cost.py`)
is a cost-estimate built from stale, already-persisted `TableStats`, not a live re-evaluation, so
the gap between preview time and execution time can drift arbitrarily if writes happened in
between — an operator approving a preview number could get a real result that doesn't match what
they signed off on. The native-engine execution path (`engine.py:339-390`) does manual
`delete_data_file`/`add_data_file` transactions with no visible retry-on-`CommitFailedException`
handling if a concurrent writer commits first.

## 4. Cost accounting

The core per-scan cost number hardcodes `S3_COST_PER_GB_MONTH = 0.023` (`cost.py:14`) applied to
100% of bytes with **no storage-class awareness**, even though a real Glacier/IA-aware pricing
module already exists (`cryo/cost/s3_pricing.py`, plus the new AWS integration) — it just isn't
wired into the core scan's cost number. Any table with lifecycle rules moving cold data to
Glacier/IA will show an inflated "current monthly cost" regardless of whether AWS integration is
configured. Separately, tables whose snapshot/manifest counts exceed configured scan limits
silently fall back to a heuristic with **zeroed expire-snapshots savings estimates**, tagged only
by a `CostPrecision.HEURISTIC` marker buried in a `raw` JSON blob — easy to miss in the UI, meaning
the largest tables (where savings matter most) get the least trustworthy numbers with the weakest
visual warning.

## 5. Locking & concurrency

Locks have a flat 6-hour TTL (`worker.py:258`, `governor.py:73`) with **no heartbeat/renewal
mechanism**. A job on a genuinely huge table (millions of files) that runs longer than 6 hours will
have its lock silently expire mid-execution, and the governor can admit a second job against the
same table while the first is still running — two concurrent destructive operations on one table.
If the lock-holder process crashes outright, the table stays locked for up to the full TTL window
before auto-release, a long stuck period for anyone trying to intervene interactively.

## 6. Governor & contextual-bandit tuning

**The bandit's confidence gate is a no-op due to a key-format bug.** `record_outcome()` stores
per-variant stats under composite keys like `"high_load_ratio:0.75"`, but `confidence_width()` and
the `confident` check in `suggest_config()` look up the *bare* knob name (`"high_load_ratio"`) —
which never exists as a key. `knob in self._state.arms` is therefore always `False` for every knob,
so `all(...)` over an empty generator returns `True`, and `confident` becomes unconditionally true
the moment 20 total observations accumulate (aggregated across *all* knobs combined, not per-arm).
A single noisy batch — e.g., a quiet Sunday where every admitted job happened to finish fast — is
enough for the Governor to "confidently" apply an untested threshold combination, with **no minimum
per-arm sample size, no rollback trigger, no alerting, and no manual "revert to static" endpoint
anywhere in the API** (the only reset is deleting the state file by hand). This is the second
correctness bug (after the NativeEngine placeholder) where the *safety mechanism itself* is silently
broken, not the underlying feature.

## 7. PII gate & compliance

Any caller can bypass the gate entirely via `job_params["force"]` or `["ignore_pii_gate"]`
(`pii_gate.py:33-34`) with **no role check on who can set it**, and the only record is a log line,
not a durable, queryable override — meaning "who overrode PII protection and why" isn't
reconstructable from the database, only from log retention (if any). The gate also **fails open on
error** (`governor.py:208-209`, bare `except Exception: logger.warning(...)`, no re-raise) — a bug
or bad data in `classify_table_columns` doesn't block the operation, it silently lets it through.
False negatives are structural too: the classifier is a small fixed keyword list plus a
cosine-similarity check against 12 hardcoded archetypes at a fixed 0.72 threshold — any
non-English-named or unconventionally-named sensitive column (`aadhar_num`, `patient_mrn`) scores
below threshold and is never flagged.

## 8. ABAC

`cryo/security/abac.py` defines a real policy model (`ABACPolicy`, `intercept_scan_request`), but
**nothing outside its own module and tests references it** — no route creates or lists policies, no
worker or query path ever calls it. It's fully-formed, unwired logic; the module's own doc-comment
admits full enforcement "requires implementing the full Iceberg REST Catalog spec." Its default
behavior when it *would* run is fail-open ("no policy → allow full access") — so wiring it up
naively later, without addressing that default, would recreate the same class of gap the PII gate
already has.

## 9. Lineage tracking

This one is real, not a stub — `record_lineage_from_query` is genuinely called during query-log
ingestion, wrapped so one bad query doesn't kill a batch. But table matching is by **exact
schema/table string match**, with no alias/rename resolution, and SQL parsing falls back to a naive
regex when `sqlglot` fails or isn't installed for the query's dialect — CTEs, subqueries, and
non-standard SQL silently produce zero lineage edges with no warning. Any future compliance or
impact-analysis feature built on the assumption that "lineage is comprehensive" will have silent
blind spots around renamed tables, views, and complex queries.

## 10. Multi-tenancy

Confirmed **100% unenforced**, not partial (partial would at least be a known gap — this is worse
because the schema and a whole unused helper module both exist, inviting the assumption it works).
`cryo/multi_tenant.py`'s `add_tenant_filter`/`verify_tenant_access` are called nowhere outside their
own file, and `verify_tenant_access` explicitly fails open when no tenant context is set ("backward
compatibility"). `tenant_id` columns exist on only 4 models with zero route-level filtering anywhere.

## 11. Audit log integrity

`AuditLog` has no `user_id`/`actor` column at all — only `table_id`, `job_id`, `action`, `status`.
Reconstructing "who ran or approved this" requires joining to a separate approvals table, and only
for jobs that went through that specific path. The table itself is an ordinary mutable SQLAlchemy
table with no DB-level append-only guarantee (no triggers, no `REVOKE UPDATE/DELETE` anywhere in
any migration) — combined with the known auth bypass, anyone who reaches a DB session (which every
authenticated route has) could alter or delete audit evidence with no detection mechanism.

## 12. Cryo Copilot & MCP

No rate limiting or aggregate cost cap exists on Copilot/LLM queries anywhere in the routes — only
a per-call `max_tokens` default, not a spend ceiling, so any authenticated caller can drive
unbounded LLM API cost. On the positive side, the "no match" path is honest (`"source": "none"`
with a clear message), not a hallucinated answer. Provider API keys are stored **unencrypted**, with
a literal `# TODO: encrypt in production` comment left in the route handlers.

## 13. Learned cost model & TabPFN health classifier

Both degrade gracefully on cold start (clean fallback to the heuristic below a minimum training-row
count, no crash on day 1). But **there is no drift detection or scheduled retraining anywhere** —
the model cache is only ever refreshed on-demand via a GET endpoint a human has to open. A fleet's
workload characteristics can drift for months with the cached model silently serving increasingly
wrong predictions and nothing flagging degraded accuracy.

## 14. AWS integration

Error handling is inconsistent in a way that produces misleading partial failures: bucket
storage-metrics fetching has **no exception handling at all** (a missing CloudWatch permission
raises a raw boto3 error straight into a 500), while pricing-lookup swallows all exceptions and
returns `None` silently — so a partially-misconfigured IAM policy fails loudly in one place and
silently in the other, an inconsistent debugging experience. There's no credential-expiry detection
after initial setup and no validation that the configured bucket name is actually the right one — a
typo'd-but-accessible bucket returns fully plausible, silently wrong cost numbers with no
cross-check against the table's actual storage location.

## 15. Sources/catalog integrations

Every catalog-factory test mocks `load_catalog` — **zero automated tests exercise a real REST/Hive/
Glue catalog**. Per-table scan failures are handled well (one bad table doesn't kill a scan), but a
whole-source discovery failure at scan start is only logged, never surfaced into any dashboard
metric — an entire catalog being unreachable is invisible to anyone not reading raw logs. There's no
OAuth token-refresh/retry logic anywhere in the catalog layer, so a long-running scan over a large
fleet whose REST catalog token expires mid-run will start failing table-by-table with no re-auth
attempt.

## 16. Frontend

Ant Design mutations get proper error toasts, but plain data-fetching (`useQuery`) calls have no
`onError` handling — a 401 (from the auth issue) or a 500 on endpoints like the Copilot questions
list or bandit status renders an empty-state ("no data yet") that's **visually indistinguishable
from an actual outage**, masking real failures as normal empty states. Several endpoints backing
fleet-wide views cap results (e.g., 500, 50 rows) with no "truncated" indicator in the UI — large
fleets silently see partial results presented as complete.

## 17. Daily reporting / Slack

`SlackClient.send_message` returns `False` on any delivery failure, but the report generator never
checks that return value — it marks `slack_sent: True` unconditionally whenever a client was
configured, so a dead webhook or a Slack outage reports success while nothing was actually
delivered. Separately, reports embed real table names and dollar cost figures with no redaction —
a Slack channel with broader membership than intended leaks internal data-lake structure and spend
figures.

---

## So — how will it actually work, 6 months out?

The honest picture: **most of Cryo works as designed for the well-behaved path** — a single-tenant
deployment, a moderate-sized fleet, Trino/Spark as the execution engine (not the native Rust one),
an operator who trusts the UI's "succeeded" status, and nobody actively trying to bypass auth. Under
those conditions the scanner, planner, cost dashboard, PII gate, and AI layer behave close to how
the docs describe them.

Where it breaks is at the edges that 6 months of real usage reliably finds:

- **The moment someone selects the native engine for real workloads**, compaction silently no-ops
  while reporting success — this is the one finding in this whole audit that produces *wrong
  data*, not just a missing safeguard, and it will corrupt whatever "realized savings" history the
  learned cost model trains on.
- **The moment anyone treats an RBAC/PII/tenancy boundary as real**, it either doesn't exist
  (multi-tenancy, ABAC), fails open silently (PII gate on error), or was never actually locked in
  the first place (JWT verification) — three different mechanisms, the same underlying pattern of
  a control that *looks* present in the schema/decorators but isn't enforced underneath.
- **The moment fleet size or job duration exceeds what was tested in dev** — locks without
  heartbeat renewal, a bandit whose confidence check is structurally broken, catalogs with no
  token refresh, embedding search with no caching — things that work fine at demo scale start
  producing wrong or stuck states at real scale, usually silently.
- **The moment something goes wrong operationally**, the audit trail and error signals meant to
  help diagnose it are themselves incomplete (no actor column on audit log, inconsistent
  fail-loud-vs-fail-silent across AWS/PII/Slack paths, empty-state UI masking real outages) — so
  incidents take longer to diagnose than they should, right when speed matters most.

None of this is a case for a rewrite — the architecture is sound and most individual features are
well-scoped and honestly built (the graceful ML fallbacks and the honest "no match" Copilot path are
good examples of doing this right). The pattern across all 17 areas above is narrower and more
fixable than it looks: **several specific safety mechanisms were built to look complete
(a decorator, a schema column, a `try/except`, a status field) without the enforcement logic behind
them being finished or tested against the failure case it exists to catch.** That's a closeable gap,
not a structural flaw — see `docs/PREMORTEM_ACTIONABLES.md` for the prioritized fix list.
