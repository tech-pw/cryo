# Cryo — Agent Handoff Notes

This file exists so another AI session (or a human) picking up this repo cold can get
oriented fast, without re-deriving everything from git history. It is a living doc —
update it as you learn things, don't just append forever.

**No secrets live here.** Tokens, passwords, and access keys are never written to this
file or committed anywhere in the repo. See "Credentials" below for how auth actually
works and where real secrets live.

---

## What is Cryo, and why does it exist

Cryo is an **Apache Iceberg lakehouse maintenance platform** — it runs against a fleet
of Iceberg tables (currently ~12,000-13,000 active tables, `cdp` catalog, backed by
Trino + S3) and keeps them healthy: compaction (`optimize`), snapshot expiry
(`expire_snapshots`), orphan-file cleanup (`remove_orphan_files`), manifest
optimization, and small-file remediation.

**The core business goal is cost reduction.** Iceberg tables left unmaintained
accumulate small files, stale snapshots, and orphaned data — all of which directly
inflate S3 storage cost and query cost (more files = more S3 GETs = slower, pricier
Trino scans). Cryo's job is to keep that fleet-wide "maintenance debt" low
automatically, at a scale where manual per-table maintenance isn't feasible.

Cryo also watches **its own operating cost** — the `cryo` K8s namespace's own AWS
spend (worker/api/governor/scheduler pod compute) is tracked against a real dollar
ceiling (`cryo/reporting/cost_ceiling.py`), because over-provisioned idle capacity
defeats the purpose of a cost-saving tool.

**North Star metric**: GB freed per day, fleet-wide (`cryo/reporting/impact_signals.py`).
**Vitals**: a composite 0-100 operational health score (`cryo/reporting/vitals.py`) —
covers stuck runs, stale jobs, repeat failures, queue growth, scan freshness/failures,
namespace cost ratio, and fleet small-file trend. Both are exposed via the API
(`/api/v1/vitals`, `/api/v1/dashboard/fleet-trends`) and a Slack report
(`cryo/reporting/health_check_slack.py`).

---

## Architecture, in one pass

- **Backend**: Python / FastAPI / SQLAlchemy (`cryo/api/`), Postgres (RDS) as the
  system of record (`cryo.tables`, `cryo.runs`, `cryo.jobs`, `cryo.table_stats`, ...).
- **Engines** (how maintenance SQL actually executes):
  - **Trino** (`cryo/engines/trino/`) — direct SQL against Trino coordinators, the
    default/preferred engine for most tables.
  - **Spark-on-K8s** (`cryo/engines/spark/k8s.py`) — self-managed `spark-submit
    --master k8s://...` for tables that need it (fallback dispatch, some ops types).
  - **Native Go engine** (`engine-go/`, `cryo/engines/native/`) — a faster, lower-RSS
    scan/stats engine (`CRYO_SCAN_ENGINE=go`), also does native K8s Job dispatch for
    some operations.
- **Governor** (`cryo/governor/`) — the sole admitter. Decides which queued/deferred
  jobs get admitted each "wave" (~30s tick), applying fleet-wide capacity caps,
  fairness/round-robin, ROI/benefit-cost gating, and safety guardrails. Runs as
  `cryo-governor` deployment.
- **Worker** (`cryo/worker/`) — `cryo-worker` deployment, KEDA-autoscaled 1-10 replicas
  on `ADMITTED+RUNNING` job count (`deploy/helm/cryo/templates/keda-worker-scaledobject.yaml`).
  Each pod polls every 5s (`WorkerRunner._tick()` in `cryo/cli.py`), processes one job
  at a time synchronously (`process_next()`), and also runs orphan-recovery
  (`cryo/worker/orphan_recovery.py`) to re-adopt RUNNING jobs left behind by a crashed
  pod.
- **Scanner** (`cryo/scanner/`) — `cryo-scanner-worker` deployment (HPA-autoscaled 3-8
  on CPU/memory, consumes the `cryo:scan:pending` Redis queue for ad-hoc scans) plus
  `cryo-scanner-cronjob` for scheduled fleet scans.
- **Scheduler** (`cryo/cli.py` → `run_scheduler()`) — `cryo-scheduler` deployment, one
  replica, runs cron-triggered maintenance run creation plus several daemon threads:
  benefit-score updater, evolving-catalog cleanup, S3 log ingest, alert dispatcher, etc.
  Each daemon is a `DaemonRunner` subclass, manually threaded — **not** auto-discovered,
  a new daemon must be explicitly wired into `run_scheduler()`'s body (a past bug: a
  daemon was wired into `run_serve()`'s `other_runners` list instead, which nothing
  actually invokes).
- **API** (`cryo/api/app.py`) — `cryo-api` deployment, plain `uvicorn`, no CLI daemons.
- **Web frontend** — `cryo-web` deployment (see `web/` directory if present).
- **Redis** — `cryo-redis`, backs the job queue (`cryo/queue/redis_queue.py`) and
  distributed locks (`cryo/queue/locks.py`).

### Job lifecycle (the part that matters most for debugging)

`MaintenanceRun` (one per scheduled/manual trigger, can cover 1 to 10,000+ tables) →
many `MaintenanceJob` rows (one per table) → each job has a `status`:
`queued → admitted → running → {succeeded | failed | partial | skipped | deferred}`.

- **DEFERRED** means the governor chose not to admit this cycle (capacity/cooldown/
  guardrails) — not a failure. `reconcile_deferred_jobs` (`cryo/queue/reconcile.py`)
  re-examines deferred jobs every wave to un-defer them.
- **`finalize_open_runs`** (`cryo/db/repositories/run_finalizer.py`) closes a run once
  every job is terminal. DEFERRED only counts as "terminal enough to close the run"
  after sitting deferred for 10+ minutes (`_DEFERRED_FINALIZE_GRACE`) — this grace
  period exists specifically to avoid closing a run out from under jobs that are about
  to be legitimately re-queued.
- **`orphaned_deferred: parent run already finalized`** is what a job gets marked when
  it's still deferred after its parent run already finalized (race the grace period
  usually prevents). Seeing many of these in `repeat_failures` means the deferred
  backlog is growing faster than reconcile can clear it within the grace window —
  almost always a symptom of an upstream throughput problem (worker OOM, governor
  admission stall), not a bug in the finalizer/reconciler themselves.

---

## Deploy workflow

```bash
bash deploy/deploy.sh --skip-tests   # or without --skip-tests to run the suite first
```

What it does: multi-arch (`linux/amd64`, `linux/arm64`) Docker build via
`docker buildx` → push to `registry.gitlab.com/penpencil-services/data/data-engineering/cryo`
→ pins the resulting digest into `deploy/helm/cryo/values-prod.yaml` (`image.digest`,
`migration.image.digest`, `native.image.digest`) → commits that file → pushes to the
`gitlab` remote (`gitlab-main` branch) → `helm upgrade` on `data-platform-cluster` →
rollout-restarts every deployment (image tag is static `:latest`, so nothing picks up
a new digest without an explicit restart) → waits for rollout → **self-verifies** by
checking every pod's live digest.

**Known gotchas:**

1. **The deploy script's own digest-verification frequently false-positives as
   "MISMATCH" mid-swap.** Every single deploy this session showed this. Always
   independently re-verify:
   ```bash
   kubectl get pods -n cryo -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[0].imageID}{"\n"}{end}'
   ```
   and compare against the digest the script printed as "Built digest: sha256:...".

2. **`docker buildx build --push` stalls or fails with a transient registry error**
   (e.g. `blob unknown to registry`) fairly often — happened 3+ times in one session.
   Diagnose a stall with `docker stats --no-stream` (check if
   `buildx_buildkit_multiarch0`'s CPU%/NET I/O is genuinely flat between two checks,
   not just quiet). Fix: `kill -9 <deploy_pid>`, `pkill -9 -f "docker buildx build"`,
   relaunch fresh. A registry push error (not a stall) is just a flaky push — retry
   immediately, no kill needed. No need for `docker buildx prune` unless retries keep
   failing.

3. **`kubectl port-forward` to `cryo-api` dies silently and often** — every deploy
   restarts `cryo-api` pods, which kills any active port-forward ("lost connection to
   pod"). After any deploy, or if a `curl` to the forwarded port returns empty/refused,
   kill and restart the port-forward:
   ```bash
   pkill -9 -f "port-forward -n cryo svc/cryo-api"
   kubectl port-forward -n cryo svc/cryo-api 18000:8000 &
   ```

4. **Background long-running commands**: never poll with bare sleeps in the foreground.
   Launch via `nohup ... & echo $! > pidfile`, then wait in a separate backgrounded
   call: `while ps -p "$PID" > /dev/null; do sleep 20; done; tail ...`.

5. **`--skip-tests`**: reasonable when the specific changed area's targeted test suite
   is already green and a full local `pytest` run risks hanging (see next section).
   Not a blanket default — use judgment, and a fresh eye should still run the relevant
   targeted tests before deploying.

---

## Cluster / kubectl access

Context: `infra:data-platform-cluster` (aliased sometimes as just
`data-platform-cluster`). Auth goes through an `infra` CLI identity-provider bridge —
**this session-based credential expires periodically** ("session in identity provider
expired or revoked" / "Access key is expired, please `infra login` again"). When this
happens, `kubectl` fails outright (not just port-forward) — there is no workaround from
inside a session; the human operator needs to run `infra login` (or equivalent) to
refresh it. Don't try to route around this via stored tokens or other credentials.

Namespace: `cryo`. Useful commands:
```bash
kubectl get pods -n cryo
kubectl get hpa -n cryo
kubectl get scaledobject -n cryo          # KEDA, for cryo-worker
kubectl logs -n cryo -l app.kubernetes.io/component=<governor|worker|scheduler|scanner-worker> --tail=N
kubectl top pods -n cryo --containers     # live CPU/mem vs requests
```

---

## Reaching the API for read/write checks (no `kubectl exec`)

`kubectl exec` into a prod pod gets blocked by the permission classifier in this
environment — don't fight it, use the API instead:

```bash
kubectl port-forward -n cryo svc/cryo-api 18000:8000 &

# Dev token issuance is enabled in prod (CRYO_ALLOW_DEV_TOKEN_IN_PROD=true — a
# deliberate, documented bridge until real OIDC is configured). tenant_id is optional,
# omit it.
TOKEN=$(curl -sS -X POST http://127.0.0.1:18000/api/v1/auth/token \
  -H "Content-Type: application/json" \
  -d '{"subject":"debug","role":"viewer"}' | python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])")

curl -sS http://127.0.0.1:18000/api/v1/vitals -H "Authorization: Bearer $TOKEN"
```

Roles: `viewer` (read-only), `operator` (can cancel runs etc.), `admin` (batch/destructive
ops gated behind `require_admin`). Issue the role you actually need — don't default to
admin for read-only checks.

Table filtering uses a JSON predicate DSL, not a query-string DSL — see
`cryo/api/predicates.py`'s `FIELD_MAP` for filterable fields and
`cryo/domain/predicates.py`'s `LEAF_OPERATORS` for operators (`=`, `!=`, `<`, `<=`, `>`,
`>=`, `in`, `like`, `glob`). Example:
```bash
curl -sS -G http://127.0.0.1:18000/api/v1/tables \
  --data-urlencode 'filter={"all":[{"field":"is_active","op":"=","value":true},{"field":"consecutive_scan_failures","op":">=","value":3}]}' \
  --data-urlencode 'sort=-consecutive_scan_failures' \
  -H "Authorization: Bearer $TOKEN"
```

---

## Credentials — what exists and how to use it, without ever printing them

- **`cryo-secrets`** K8s Secret (`cryo` namespace) — holds `CRYO_GRAFANA_TOKEN` and
  other runtime secrets consumed via `envFromSecret` in `values-prod.yaml`. Not
  chart-managed; rotate via `kubectl patch secret cryo-secrets`, added manually.
- **Grafana access**: the user has authorized using a directly-supplied API token for
  Grafana dashboard queries (Athena CUR cost data, etc.) when the browser-session
  bridge to platform-grafana has expired (it expires often). Ask the user for a fresh
  token rather than trying to extract `CRYO_GRAFANA_TOKEN` from the live secret —
  extracting secrets to a local shell is not the intended path and gets blocked by the
  permission classifier for broad credential-hunting commands.
- **Registry auth** (`registry.gitlab.com/.../cryo`) is handled transparently by
  `deploy/deploy.sh` — no manual login needed if already configured.
- **Never write real token/secret VALUES into this file, commit messages, or any
  tracked file.** Reference secrets by name/location only.

---

## Testing

```bash
.venv/bin/python -m pytest tests/<file> -q
```

- Tests using `InMemoryJobStore` / `InMemoryLockStore` / `InMemoryMaintenanceQueue`
  (e.g. `tests/test_orphan_recovery.py`, most of `tests/test_worker.py`) run standalone,
  no external dependencies.
- Tests that hit a real `session` fixture backed by Postgres (a lot of
  `tests/test_queue_and_governor.py`, `tests/test_queue_reconcile.py`, etc.) **require a
  local Postgres running** — this environment frequently does not have one, and those
  tests will hang or fail with `connection to server at "::1", port 5432 ... Connection
  refused`. This is a pre-existing environment gap, not a sign your change broke
  something — confirm by running the same file against a clean `git stash`.
- `tests/test_fleet_trends_api.py::test_aggregate_fleet_health_trend_groups_by_day` and
  `::test_aggregate_fleet_cost_trend_sums_daily_totals` are known-flaky on a day-boundary
  timing issue, unrelated to most changes — confirmed pre-existing via clean-tree rerun,
  2026-09-10.
- A full `pytest tests/` run can take 2+ minutes and risks foreground timeout in an
  agent session — run it backgrounded (`nohup ... & echo $! > pidfile`) rather than
  foreground with a short timeout.

---

## Debugging playbook (patterns that worked this session)

1. **Vitals red / CRITICAL?** Pull `/api/v1/vitals`, read every `deductions[]` entry —
   each has a `reason` string naming the exact check and numbers. Don't assume it's a
   stale echo; cross-check against `dispatch_heartbeat.is_stalled` and a fresh
   `success_rate` window before dismissing a score as historical noise.
2. **A specific run/job stuck?** `/api/v1/runs?status=running&limit=200`, sort by
   `created_at`, look for the age matching the vitals deduction's "(oldest Nh)". Pull
   `/api/v1/runs/{id}/jobs` for the job detail — check `engine_ref` (was a real engine
   job ever dispatched?), `status`, `error`.
3. **Suspect a live outage (not historical)?** Check pod health directly:
   `kubectl get pods -n cryo` (look for `CrashLoopBackOff`, high `RESTARTS`),
   `kubectl get events -n cryo --sort-by='.lastTimestamp' | grep -i worker`, and
   `kubectl top pods -n cryo --containers` to catch a pod mid-spike. A pod's
   `lastState.terminated.reason` (`kubectl get pod <name> -o jsonpath=...`) tells you
   `OOMKilled` vs something else directly — don't guess from logs alone.
4. **OOMKilled workers?** Check `kubectl logs <pod> --previous` for the last thing
   logged before the kill — often nothing useful directly (the leak/spike frequently
   isn't in the last logged line). Cross-reference against known-documented incident
   classes already fixed once before (grep the codebase for `OOMKilled` in comments —
   this codebase has extensive "confirmed live" incident docstrings on exactly this
   kind of bug, check whether a *similar* pattern was already fixed elsewhere and
   whether this new instance is the same class missed at a different call site).
5. **A governor log line like `Wave complete: admitted=N, deferred=M`** with M >> N is
   the single best signal that something is starving admission — chase why almost
   nothing is being admitted (worker capacity down? a specific guardrail firing?)
   before assuming it's a governor logic bug.
6. **Grep for "unbounded" or a specific incident date in comments** — this codebase has
   a strong convention of documenting past incidents directly in code comments with the
   date, the live numbers, and the exact fix. When investigating a new-looking bug,
   search for whether a *sibling* function already had the identical bug fixed
   (`grep -rn "unbounded\|OOMKilled\|confirmed live" cryo/`) — several bugs this session
   turned out to be the same known pattern at a missed call site, not a novel bug.

---

## State as of last update (2026-09-10, mid-active-outage)

### ⚠️ ACTIVE, UNRESOLVED: cryo-worker OOM cascade

**This is the most important thing in this file right now.** `cryo-worker` pods have
been OOMKilling repeatedly since ~08:33 UTC on 2026-09-10, during/after a large
scheduled cron burst (multiple concurrent `MaintenanceRun`s of 5,000–11,000+ jobs each).
Three things were tried, in order, each ruled out or only partially effective:

1. **Bounded `recover_orphaned_jobs`'s unbounded `list_queued_jobs()` scan**
   (`cryo/worker/orphan_recovery.py`, commit `68bc82eb`) — real bug, real fix (queue depth
   dropped 3,134→584 after deploying it), but did **not** stop the OOM cascade on its own.
2. **Raised `worker.resources.limits.memory` 6Gi→8Gi** (`values-prod.yaml`, commit
   `108ee7a9`) — did **not** help either: 3 of 10 pods OOMKilled within ~3.5 min of the
   fresh rollout, **two of them at the identical second**. That simultaneity is the key
   clue — it rules out "one big table's payload is just bigger than the ceiling" and
   points at something that spikes hard and fast under concurrent load, likely triggered
   by the same event (an admission wave? a scheduled batch?) hitting multiple pods at once.
3. **Capped KEDA `maxReplicas` 10→3** (`values-prod.yaml`, commit `d3b4c956`) — a
   concurrency-reduction stopgap, not a fix. Rationale: fewer simultaneous pods means
   fewer pods that can hit whatever this is at the same time. **Revert this
   (`native.keda.maxReplicas: 3` → delete the override, back to the template default of
   10) once the real root cause is found and fixed** — it throttles fleet-wide admission
   throughput in the meantime, which is its own cost (slower queue drain, extended
   `queue_growth`/`success_rate` vitals deductions).

**Leading (still unconfirmed) theory**: the crash signature is always the same —
`spark-k8s payload worker:pre_execute ... rss=1xx MiB` (self-reported process RSS stays
low, 120–180Mi) immediately followed by `Created Spark-on-K8s dispatch Job ...` with
**no further log line** before the kill. The Python process's own RSS is never the
smoking gun (confirmed via `kubectl exec ... cat /proc/1/status` — matches the log).
`kubectl top` readings during an active spike (once saw 5.8Gi/1.7Gi on two pods) did
**not** match `cgroup memory.current` read moments later (~147Mi) — `kubectl top` /
metrics-server appears unreliable for catching this in the moment; trust
`lastState.terminated.reason`/`exitCode` (kernel-verified) over `kubectl top`.

One specific huge table was caught in an actively-crashing pod's logs:
`cdp.user360.silver_user360` — 549.5GB, 5,751 files, crosses
`cryo/engines/spark/worker_guard.py`'s `OUTLIER_SIZE_GB=500` threshold — appearing in
**both** an "Adopted job ... still running; waiting" line (orphan-recovery re-probing an
already-dispatched job) and a fresh `worker:pre_execute` dispatch attempt around the same
time, suggesting the same table may be getting dispatched concurrently by more than one
worker/path. This matches a theory already written into `values-prod.yaml`'s own history
(`worker.resources.limits.memory` comment, 2026-08-16 note referencing
`_DEFAULT_FALLBACK_DISPATCH_CONCURRENCY=6` concurrent Spark-K8s submissions per table
admission wave) — but that theory was **never actually confirmed with real data** either
time it was written down. This is the single highest-value thing to chase next:

- Check `cryo/governor/native_admit.py`'s `_DEFAULT_FALLBACK_DISPATCH_CONCURRENCY` and
  the per-dispatch memory logging mentioned in that same 2026-08-16 comment — that
  logging was added specifically so *next time this recurs* there'd be real data. Check
  whether it actually fired and what it shows.
- Check whether `cdp.user360.silver_user360` (or any other `OUTLIER_SIZE_GB`-crossing
  table) is being dispatched by multiple workers concurrently — cross-reference
  `MaintenanceJob` rows for that `table_id` across all currently-running/recently-failed
  jobs, not just the one log line caught live.
- Check whether `validate_spark_plan_scope` (`cryo/engines/spark/worker_guard.py`) is
  actually being reached/raising for this table, or whether something about the outlier
  path bypasses the scope check that's supposed to prevent unscoped whole-table rewrites.
- Consider: does `TableMaintenanceData.stats["raw"]` (confirmed ~627KB JSON for this one
  table) get held across the *entire* `wait_for_completion` poll loop (up to 2h,
  `active_deadline_seconds`) rather than being released after dispatch? If so, and if
  several outlier tables are in-flight on the same pod concurrently (adoption + fresh
  dispatch both touching the same or different big tables), that compounds.

**Do not** just raise the memory limit again as the next move — it was tried twice
(4Gi→6Gi in August, 6Gi→8Gi today) and neither held. Find the actual allocation that
isn't being freed.

### Other fixes from this session (deployed, working)

- `deactivate_orphaned_evolving_catalog_entries` wiring (was dead code, never scheduled).
- Spark orphan-recovery `UNKNOWN`-state handling (`_spark_job_state`).
- `repeat_failures` vitals check `*_evolving` exclusion.
- `fleet_small_file_trend` active-table filtering.
- A batch table-deactivate admin API endpoint (`POST /api/v1/tables/deactivate-batch`,
  `require_admin`) — used to manually deregister 10 verified-dead tables that the
  automated sweep's naming-pattern heuristic missed.

### Other known follow-ups, not yet fixed

- `cryo/engines/trino/engine.py`'s `TrinoEngine.execute()` blocks synchronously inside
  `cursor.execute()` and only captures the Trino query ID (`engine_ref`) after it
  returns/raises — if the worker process dies mid-call, the job is stuck at
  `status='running'`, `engine_ref=null` forever, with nothing to probe or auto-recover
  (unlike the Spark path, which has a real K8s Job to check). Recurred on
  `cdp.unified_bq.gold_dbt_pw_bq` multiple times. Fixing this properly means either
  running `execute()` in a background thread and polling `cursor.query_id` concurrently,
  or dropping to the lower-level `TrinoQuery` API directly — a real architectural change
  touching every Trino dispatch path, not a one-line fix.
- ~106 active (non-`*_evolving`-named) tables sharing the same orphaned-catalog-entry
  shape (`location IS NULL` or 404-on-read, `query_count_30d=0`) but not matched by the
  automated `deactivate_orphaned_evolving_catalog_entries` sweep (different naming
  pattern, some cross-schema). Top 10 by scan-failure count were manually verified and
  deregistered via the new batch-deactivate endpoint; the rest are an open cleanup
  candidate, pending the same per-table verification rigor.

### Things learned about this environment specifically (this session)

- Docker Desktop was not running at one point mid-session — `deploy/deploy.sh` fails
  fast with a clear "Cannot connect to the Docker daemon" error in that case. `open -a
  Docker` then poll `docker info` until it responds (took ~10s here).
- `infra login` session expiry happened mid-session (~2h into a long session) and blocked
  *all* `kubectl` access, not just port-forwards, until refreshed externally.
- For a **values-only change** (resource limits, replica counts — no code change), skip
  the full `deploy/deploy.sh` (which always rebuilds+pushes an image) and instead:
  ```bash
  helm upgrade cryo deploy/helm/cryo -n cryo -f deploy/helm/cryo/values-prod.yaml
  kubectl rollout restart deployment/<name> -n cryo   # only if the change needs a pod restart
  ```
  This is much faster in an active-outage situation where every minute counts. Still
  commit the values-prod.yaml change to git either way — `deploy/deploy.sh`'s dirty-tree
  check will block the next real deploy otherwise.
