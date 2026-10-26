---
title: Troubleshooting
description: Symptom-first troubleshooting for Cryo installs, sign-in, scans, runs, deferred jobs and Spark lanes, plus an FAQ.
canonical: https://tech-pw.github.io/cryo/troubleshooting/
updated: 2026-10-26
---

# Troubleshooting

Start from what you see. Each entry gives the likely cause, where to check it, and the fix. A short FAQ follows.

Most answers are on the job itself. Open **Runs**, then the run, then the job. A deferred or failed job shows its reason there. The commands below assume the release name `cryo` in namespace `cryo`.

## Install and upgrade

### Helm fails with `production requires image.digest`

**Cause**

With `CRYO_ENVIRONMENT=prod`, the chart refuses to render until every enabled image is pinned by digest. The same check covers `migration`, `web`, `redis`, `native` and `spark.k8s`.

**Check**

`helm template cryo ./deploy/helm/cryo -f cryo-values.yaml` names the missing value.

**Fix**

Set that digest. See [Install](https://tech-pw.github.io/cryo/install/#install).

### The migration hook fails or times out

**Cause**

Most often a lock. Migrations use `lock_timeout=10s`, so a long-open transaction on a Cryo table makes them fail instead of wait. Wrong database credentials, or a missing `pgcrypto` privilege on a first install, also fail here.

**Check**

`kubectl -n cryo logs job/cryo-migrate-<revision>`. For locks, look for sessions left `idle in transaction`: `SELECT pid, state, xact_start, query FROM pg_stat_activity WHERE datname = current_database() AND state LIKE 'idle in transaction%';`

**Fix**

End the blocking session, then run the same `helm upgrade` again. The failed chain was rolled back as one transaction, so nothing is half-applied. See [How the migration hook behaves](https://tech-pw.github.io/cryo/upgrade/#migrations).

## Sign-in

### The login page won't load, and `/api/v1/auth/config` returns 503

**Cause**

The detail `OIDC issuer discovery failed` means the API couldn't fetch `CRYO_OIDC_ISSUER_URL/.well-known/openid-configuration`.

**Check**

`kubectl -n cryo exec deploy/cryo-api -- sh -c 'curl -sS "$CRYO_OIDC_ISSUER_URL/.well-known/openid-configuration"'` fetches it from inside the pod, with the pod's own setting.

**Fix**

Correct the issuer URL, or allow egress from the cluster to it.

### You come back from the identity provider and aren't signed in

**Cause**

`Code exchange failed` (400) means a wrong client secret, or a `CRYO_OIDC_REDIRECT_URI` that doesn't match the URI registered at the provider. A 401 with `Invalid bearer token` afterwards means Cryo rejected the provider's access token. It may not be a JWT, its `aud` or `iss` may not match, or it may carry no Cryo role.

**Check**

Decode the access token (it's a JWT) and compare `iss`, `aud` and the role claims with [OIDC setup](https://tech-pw.github.io/cryo/security/#oidc).

**Fix**

Set `CRYO_OIDC_AUDIENCE` to the token's audience, or configure the provider to issue a JWT access token. Then emit a role named exactly `viewer`, `operator` or `admin` in `roles`, `groups` or one of the other claims Cryo reads.

### Dev sign-in returns 503

**Cause**

This is intended. The dev token route is off in `prod`.

**Check**

`/api/v1/auth/config` reports `"dev_token_enabled":false`.

**Fix**

Sign in through OIDC. Don't set `CRYO_ALLOW_DEV_TOKEN_IN_PROD` to work around it. See [The dev token](https://tech-pw.github.io/cryo/security/#dev-token).

## Scans

### A scan finds no tables

**Cause**

If **Discovered** is 0, the problem is the catalog or the schema filter, not storage. Common causes are a wrong catalog name or warehouse, catalog credentials that can list nothing, or **Schemas for maintenance** set to schemas that don't exist.

**Check**

Open **Scans** (`/scans`), then the scan, and read its live log. Run the connection test on the source under **Settings → Connections**.

**Fix**

Correct the source and scan again. See [The first scan](https://tech-pw.github.io/cryo/onboarding/#scan).

### Tables are discovered but fail to scan

**Cause**

Cryo found the tables but couldn't read their files. `AccessDenied` on an S3 path is a credentials problem. On EKS, the usual cause is a ServiceAccount without an `eks.amazonaws.com/role-arn` annotation.

**Check**

The first failure in the scan's live log usually explains the rest. The storage step on the source shows which buckets are readable.

**Fix**

Grant read access, or set `serviceAccount.annotations`. See [S3 authentication](https://tech-pw.github.io/cryo/onboarding/#auth).

### A table stays unreadable after a rescan

**Cause**

Two categories are permanent. `metadata_not_found` means the catalog points at a metadata file that isn't in storage. `manifest_missing` means a manifest list the metadata names is gone. A query engine fails the same way. `permission_denied` isn't permanent: an IAM change fixes it.

**Check**

The table's last scan error, on the table's row in **Tables** and on its detail page.

**Fix**

The table's owners repoint the catalog entry or restore the table. Rescanning doesn't help. Don't drop the table because of this error alone.

## Runs and jobs

### Tables have grades, but no maintenance runs happen

**Cause**

Scanning never schedules maintenance. A table needs a preset, and no preset is assigned by default.

**Check**

**Presets** (`/presets`) lists each preset, what it applies to and its schedule.

**Fix**

Assign a preset. See [Maintenance](https://tech-pw.github.io/cryo/maintenance/).

### A job is deferred with `Destructive operation requires approval`

**Cause**

`expire_snapshots`, `expire_data` and `remove_orphan_files` need an approval for that table and those parameters. The governor creates the approval request when it defers the job.

**Check**

**Governance** (`/governance`) lists the pending approvals.

**Fix**

Approve it or reject it. Waiting, requeueing and adding workers don't clear it.

### A job is deferred with `table_locked`

**Cause**

Another job holds this table's lock, so the two can't rewrite the same table at once. It's usually a job from another run that's still running.

**Check**

Look in **Runs** for another run with a job still running on the same table. The deferred job is retried on later governor waves.

**Fix**

Usually nothing. It runs when the other job finishes. If the holder will never finish, the governor reaps stale running jobs and their locks after the lock TTL (six hours).

### Jobs stay queued and nothing runs

**Cause**

Either work isn't being admitted, or it's admitted and no worker picks it up. A worker runs one job at a time. A pause, an approval, a budget or ROI refusal, or the engine's slot cap keeps work from being admitted, and more workers don't change that.

**Check**

Each job's deferral reason names the gate that refused it. Control Room (`/control-room`) shows active pauses. Then check that the governor and workers are running: `kubectl -n cryo get pods -l app.kubernetes.io/component=governor`, then the same for `worker`, and read `kubectl -n cryo logs deploy/cryo-governor --tail=50`.

**Fix**

Clear the gate the reason names. Add workers only when jobs are admitted and waiting with no deferral reason.

### Daily runs fire twice, at odd times

**Cause**

Redis restarted. The scheduler's "last fire" markers live only in Redis, so after a restart a daily preset or the daily scan can fire again within 24 hours. This is a known issue, [#177](https://github.com/tech-pw/cryo/issues/177).

**Check**

Compare the extra run's start time with the Redis pod's start time: `kubectl -n cryo get pod -l app.kubernetes.io/component=redis -o wide`.

**Fix**

Keep Redis stable until #177 is fixed. See [Losing Redis](https://tech-pw.github.io/cryo/backup/#redis).

### Tables show `skipped` at the end of a nightly run

**Cause**

The run hit its deadline before reaching them. They weren't lost and didn't fail.

**Check**

The run's summary and the skipped jobs' reasons.

**Fix**

Nothing urgent, because the next night picks them up. If it happens every night, add lanes or narrow the preset.

## Spark lanes

### A lane is `OOMKilled` or exits with 137

**Cause**

The lane pod ran out of memory. The JVM heap is 60% of the pod limit, and the rest is for off-heap memory and the runner. A Java 21 lane can also fail with `OutOfMemoryError: Java heap space` and `Retried waiting for GCLocker too often` while the heap is mostly free.

**Check**

`kubectl -n cryo describe pod <lane-pod>` shows the termination reason. The job's error names the table that was running.

**Fix**

A table that died mid-run is marked failed and isn't retried on the same pod size. Tables the lane hadn't started go to a replacement lane. Lane pods are capped by `maintenance.laneMaxMemMib`, which defaults to 16384.

### A Spark pod fails with `exec format error`

**Cause**

The Spark image is amd64-only, and the pod landed on an arm64 node.

**Check**

The `nodeSelector` on the Spark wrapper Job, not on Cryo's own Deployments, which are pinned separately.

**Fix**

Don't set `kubernetes.io/arch` to `arm64` in the Spark destination's node selector or in `CRYO_SPARK_K8S_NODE_SELECTOR`. Leave it unset, and Cryo adds `amd64`.

### Creating a Job or Secret fails with 403

**Cause**

The worker's ServiceAccount can't create the lane Job or the driver Secret. That RBAC is only rendered when `native.enabled` or `spark.k8s.enabled` is true.

**Check**

`kubectl -n cryo get role,rolebinding` shows whether a Role exists that grants `batch/jobs` create and `secrets` create, delete and patch.

**Fix**

Turn on the lane you use, `native.enabled` or `spark.k8s.enabled`, pin its image digest in `prod`, and upgrade.

## FAQ

**How is this different from running `OPTIMIZE` on a cron?**

A cron runs the same statement against the same tables whatever their state. Cryo scans every table, grades it, and plans only the work it needs, scoped to partitions where it can. It runs operations in a fixed safe order. A governor admits work against your engines' live capacity, and every job leaves an audit row. See [How Cryo fits](https://tech-pw.github.io/cryo/concepts/).

**Is it safe to point at production?**

Scanning is read-only. Destructive operations (snapshot expiry, data expiry and orphan-file removal) need an approval for each table by default, and a pause stops new work before it's submitted. Cryo is pre-1.0. Start with a preset on a few tables you own, and read [Security](https://tech-pw.github.io/cryo/security/) first.

**Which engines are supported?**

Trino, Apache Spark (EMR Serverless, or Spark on Kubernetes run by Cryo), and a native engine for compaction, which is in beta. For Flink, Cryo generates a maintenance config but doesn't submit the job. See [Engines](https://tech-pw.github.io/cryo/engines/).

**Which catalogs?**

Iceberg REST, Hive Metastore, AWS Glue, Polaris and Nessie. Unity Catalog and JDBC catalogs are rejected. See [Catalog types](https://tech-pw.github.io/cryo/onboarding/#catalogs).

## Still stuck

Search the [issues](https://github.com/tech-pw/cryo/issues), or open one with the symptom, the job's reason and the relevant log lines. Remove hostnames, credentials and table names you can't share. For a security problem, follow [SECURITY.md](https://github.com/tech-pw/cryo/blob/main/SECURITY.md) instead.

Source: https://tech-pw.github.io/cryo/troubleshooting/
Markdown: https://tech-pw.github.io/cryo/troubleshooting/index.md
