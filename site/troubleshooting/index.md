---
title: Troubleshooting
description: Common Cryo problems -- scans that find no tables, runs stuck in queued, deferred jobs, OOMKilled pods, and login failures -- with the command that checks each one.
canonical: https://tech-pw.github.io/cryo/troubleshooting/
updated: 2026-10-26
---

# Troubleshooting

Symptoms first. Each entry says what you see, how to check it, and what to change.

Cryo has runbooks in the repository for the detailed cases. This page is the short version: find the symptom, check it, fix it. The full operator guide is in [docs/operator-guide.md](https://github.com/tech-pw/cryo/blob/main/docs/operator-guide.md).

## Scanning

### The scan finds no tables

**What you see:** the scan completes with zero tables, or the registry stays empty.

**Check:** the source must be enabled and must have credentials that resolve. A scan with no source, or with a source whose secrets fail to resolve, reports success and finds nothing.

**terminal**

```
$ curl -s http://<api>/api/v1/sources -H "Authorization: Bearer $TOKEN" | jq '.[] | {id, enabled, type}'
$ curl -s http://<api>/api/v1/scans -H "Authorization: Bearer $TOKEN" | jq '.items[0] | {status, tables_scanned, error}'
```

**Fix:** enable the source, then re-scan. If credentials are the problem, the scan's audit rows carry the auth error. In the UI, open the source and run *Test connection*.

### Table stats are stale

**What you see:** `scan_at` on a table is old, and the grade does not move after a scan.

**Check:** the scanner CronJob runs on a schedule, and the scanner consumes a queue. Both can stall independently of a manual scan.

**terminal**

```
$ kubectl -n cryo get cronjob
$ curl -s http://<api>/api/v1/metrics -H "Authorization: Bearer $TOKEN" | grep -E '^cryo_scanner_(errors|tables_scanned)'
```

**Fix:** check the CronJob's last schedule and the scanner worker's logs. A scan triggered manually from the UI bypasses the scheduler, so it is the quickest way to separate "the scan is broken" from "the schedule is broken".

### A scan fails on one table

**What you see:** a scan reports failed, or one table's stats never update while the rest are fine.

**Check:** Cryo isolates per-table failures on purpose — one bad table does not fail the batch. The reason is on that table's detail page and in the audit log.

**Fix:** read the audit row. The usual causes are a partition the catalog cannot describe, a file whose metadata will not parse, or a table the engine cannot read. Skipping the table is the correct behaviour; the batch should still succeed.

## Runs and jobs

### A run stays queued

**What you see:** a run sits in `queued` and never starts. `cryo_governor_queue_depth` climbs.

**Check:** the governor admits work against live engine load, so a queued run may be a deliberate deferral rather than a fault. Look at the admission decision, then at the engine.

**terminal**

```
$ curl -s http://<api>/api/v1/metrics -H "Authorization: Bearer $TOKEN" | grep '^cryo_governor_admission_decisions_total'
$ curl -s http://<api>/api/v1/metrics -H "Authorization: Bearer $TOKEN" | grep '^cryo_governor_queue_depth'
```

**Fix:** if the outcome is `deferred`, the reason is on the job: engine at capacity, off-peak window closed, a cooldown from a recent run, or the budget for that wave is spent. Waiting is the fix. If the outcome is `admitted` and nothing runs, the problem is the worker — check that the worker pods are ready and that Redis is reachable from them.

### A job is deferred with `table_locked`

**What you see:** a job is deferred and names a table lock.

**Check:** another run holds the lock on that table. That is the guardrail doing its job — two runs on one table would race.

**Fix:** wait for the other run to finish. If the other run is itself stuck, find it in the runs list and cancel it. The lock expires on its own if the holding process dies, so this resolves without intervention in most cases.

### Heavy work never runs

**What you see:** standard and deep runs are deferred, always, and never run overnight.

**Check:** the off-peak window. It is read in the install's display time zone, and equal bounds disable the gate entirely.

**terminal**

```
$ curl -s http://<api>/api/v1/governor/offpeak-window -H "Authorization: Bearer $TOKEN" | jq
```

**Fix:** set the window to hours when your engine is quiet. If the window is set but the run is still deferred, check `CRYO_DISPLAY_TIMEZONE` — the window is not in the browser's time zone, and it is not in UTC unless you set it there.

### A table has no effective preset

**What you see:** a table needs compaction but nothing is scheduled for it. It gets zero recurring maintenance, silently.

**Check:** a table resolves to a preset by pin, then by rule, then by the default. If none of the three matches, it is unmaintained.

**terminal**

```
$ curl -s "http://<api>/api/v1/tables?filter=needs_compaction=true" -H "Authorization: Bearer $TOKEN" | jq '.items[].id'
$ curl -s http://<api>/api/v1/presets -H "Authorization: Bearer $TOKEN" | jq '.[] | {id, name, enabled, is_default}'
```

**Fix:** enable the fleet-wide catchall preset and give it a `schedule_cron`, or pin the table to a preset. A *simulate* on the preset shows how many tables it would cover before you commit.

## Pods and resources

### A pod is OOMKilled

**What you see:** `cryo-api`, `cryo-worker` or `cryo-governor` restarts with `OOMKilled`.

**Check:** which pod, and what it was doing. The API and the governor have historically been the memory-hungry ones: the governor's per-tick admission work scales with queue depth, and the API's dashboard queries fan out across the fleet.

**terminal**

```
$ kubectl -n cryo get pods -o json | jq -r '.items[] | select(.status.containerStatuses[0].lastState.terminated.reason=="OOMKilled") | .metadata.name'
$ kubectl -n cryo top pods --sort-by=memory | head -10
```

**Fix:** raise that service's memory limit in the chart's values, and check the cause rather than only the symptom. A governor that is OOMing on a deep queue needs a bounded admission scan, not a bigger box forever. The scanner worker OOMs on a catalog with too many partitions per chunk — reduce the chunk count for that source.

### A pod is not ready

**What you see:** a deployment has fewer ready replicas than it should, and the rollout never completes.

**Check:** the probe. The chart's probes are on by default and they check the real dependency, so a pod that cannot reach PostgreSQL will never become ready.

**terminal**

```
$ kubectl -n cryo describe pod -l app=cryo-api | sed -n '/Events/,$p'
```

**Fix:** the events name the failing dependency. A migration job that has not finished will hold the whole release back — check the migration job first when a fresh install will not come up.

### Two governors are active, or none

**What you see:** duplicate admissions, or a governor that is running but admitting nothing.

**Check:** the governor and scheduler are leader-elected through a Redis lease. Only the leader acts.

**terminal**

```
$ redis-cli GET cryo:leader:governor
$ redis-cli GET cryo:leader:scheduler
```

**Fix:** if the lease is held by a pod that is gone, it expires on its own. Restarting the governor pod clears it immediately.

## Access

### Sign-in fails

**What you see:** the right password is refused, or sign-in returns 429.

**Check:** failed sign-ins are throttled and counted in Redis. Five failures for one account, or twenty from one IP, inside the window lock it out — and the correct password is refused until the window ends.

**terminal**

```
$ curl -s http://<api>/api/v1/auth/audit -H "Authorization: Bearer $ADMIN_TOKEN" | jq '.items[0:5]'
```

**Fix:** wait out the window, or clear the counter in Redis. The audit rows name the account, the method, the reason and the client IP; the password is never stored. A successful sign-in clears the count for that account.

### The dev token is off and you have no other way in

**What you see:** the UI has no sign-in form, and the API rejects every token.

**Check:** in `staging` and `prod` the dev token is off unless `CRYO_ALLOW_DEV_TOKEN_IN_PROD=true`. If OIDC is not configured either, there is no way in.

**Fix:** configure OIDC, local sign-in or Google sign-in. If you must get in first, set `CRYO_ALLOW_DEV_TOKEN_IN_PROD=true` temporarily, use it once, then turn it off — it lets anyone who can reach the API grant themselves any role.

## FAQ

### How is this different from running OPTIMIZE on a cron?

A cron runs one statement over a table you picked. Cryo grades every table, plans the work in the order that reclaims the most space first, budgets it against live engine load, runs it on the engine you already run, and records why each decision was made. It also does the cleanup operations a single OPTIMIZE leaves behind — snapshot expiry, orphan-file removal, manifest rewrite — which is where most of the reclaimed storage actually comes from.

### Is it safe on production?

Cryo only writes when a run says it will, and every operation is audited. Snapshot expiry is the operation to look at first: it is reversible only until the underlying data is gone. Start with a preset scoped to one schema, keep `CRYO_MAINTENANCE_WRITES_ENABLED=false` while you watch the plans, and turn it on per destination once the plans look right.

### Which engines are supported?

Trino and Spark on Kubernetes (EMR or the operator), plus the native Go compactor that ships in the main image. The planner emits no SQL — it produces a plan, and each engine renders it in its own dialect. See [Engines](https://tech-pw.github.io/cryo/engines/).

### Does the scanner read my data?

No. The scanner reads catalog metadata and object listings. It does not open data files. It does need permission to list the table's objects in the object store, so scope its credentials to the namespaces it should see.

### What happens if Redis is lost?

The queue, the leases and the live counters are in Redis. Losing it loses queued work and the leader leases; the durable state — tables, stats, jobs, audit — is in PostgreSQL. The scheduler and governor re-elect, and the next scheduled run rebuilds the queue. A run that was mid-flight when Redis went away is reconciled from PostgreSQL rather than resumed.

## Where to go next

Security and authenticationRoles, the dev token and what to turn on before exposing an install.Configuration referenceEvery environment variable and Helm value.DeploymentInstall the chart and the resources it needs.

Source: https://tech-pw.github.io/cryo/troubleshooting/
Markdown: https://tech-pw.github.io/cryo/troubleshooting/index.md
