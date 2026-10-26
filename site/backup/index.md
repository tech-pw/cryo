---
title: Backup and restore
description: What Cryo stores where, encrypted PostgreSQL backups with the built-in tool or the backup CronJob, a restore procedure, and what losing Redis costs.
canonical: https://tech-pw.github.io/cryo/backup/
updated: 2026-10-26
---

# Backup and restore

PostgreSQL holds everything Cryo knows, so back it up and practise restoring it. Redis holds the queue and can be rebuilt, with one known exception.

## What to back up

**PostgreSQL: back it up**

The `cryo` schema is Cryo's source of truth. It holds sources, destinations, presets, table stats and grades, plans, runs and jobs, approvals, pause controls, fences and the audit trail. Connection secrets in it are encrypted with `CRYO_SECRET_KEY`.

**The Secret: back it up separately**

A database backup is useless without the `CRYO_SECRET_KEY` it was written with, because the stored connection secrets can't be decrypted without it. Keep the Secret in your secret manager, not next to the backups.

**Redis: no backup needed**

Redis holds the job queue, leases and short-lived markers. The governor's reconcile pass rebuilds the queue from PostgreSQL after a restart. The bundled Redis has no volume on purpose. See [Losing Redis](#redis) for the one thing that isn't rebuilt.

If your PostgreSQL is a managed service with snapshots or point-in-time recovery, that's a valid backup. The rest of this page covers Cryo's own tool, which adds encryption and an offsite CronJob.

## The backup key

Cryo's backups are encrypted with AES-256-GCM using `CRYO_BACKUP_KEY`: 32 random bytes, encoded as URL-safe base64. Generate one, store it in your secret manager, and add it to the `cryo-secrets` Secret. Without this key, a backup can't be restored.

**terminal**

```
$ openssl rand 32 | base64 | tr '+/' '-_'
```

## Scheduled offsite backups

The chart has a backup CronJob, which is off by default. It runs `python -m cryo.operations.backup create-offsite` with the Cryo image and the same Secret. It takes a `pg_dump --format=custom`, encrypts it, and uploads the encrypted file first and its checksum manifest last. The manifest marks a complete backup. Retention deletes only Cryo's own timestamped backups under the prefix.

**cryo-values.yaml**

```
backup:
  enabled: true
  schedule: "0 2 * * *"
  s3Uri: s3://company-recovery/cryo/production/
  retentionDays: 30
  serviceAccount: cryo-backup
```

`serviceAccount` must already exist and be able to write, list and delete under `s3Uri`, for example through IRSA. The chart doesn't create it. Use a bucket in a separate account or region, with versioning and object lock turned on. The CronJob's schedule is in UTC.

**terminal**

```
$ kubectl -n cryo get cronjob cryo-backup
$ kubectl -n cryo logs -l app.kubernetes.io/component=backup --tail=20
```

A successful run prints a JSON result with the backup and manifest S3 URIs.

## A one-off backup

Take one before every upgrade. The API pod already has the database URL and the backup key from the Secret.

**terminal**

```
$ POD=$(kubectl -n cryo get pod -l app.kubernetes.io/component=api -o jsonpath='{.items[0].metadata.name}')
$ kubectl -n cryo exec "$POD" -- python -m cryo.operations.backup create --output /tmp/cryo.dump.enc
$ kubectl -n cryo cp "$POD:/tmp/cryo.dump.enc" ./cryo.dump.enc
$ kubectl -n cryo cp "$POD:/tmp/cryo.dump.enc.manifest.json" ./cryo.dump.enc.manifest.json
$ kubectl -n cryo exec "$POD" -- rm /tmp/cryo.dump.enc /tmp/cryo.dump.enc.manifest.json
```

The tool passes the database password through `PGPASSWORD`, never on the command line. The `pg_dump` in the image comes from the Debian `postgresql-client` package. If your server's major version is newer than that client, take the dump with your own tooling instead.

## Restore

Restoring is destructive. It runs `pg_restore --clean --if-exists` against `CRYO_DATABASE_URL`. Practise it first in an isolated environment with an empty database. Keep everything that writes to the database stopped until you've checked the result.

### Stop the writers

Scale the scheduler, governor, workers and scanner workers to zero, and suspend the CronJobs. The API stays up so you can run commands in it.

**terminal**

```
$ kubectl -n cryo scale deploy/cryo-scheduler deploy/cryo-governor deploy/cryo-worker deploy/cryo-scanner-worker --replicas=0
$ kubectl -n cryo patch cronjob cryo-scanner -p '{"spec":{"suspend":true}}'
```

Suspend `cryo-maintenance` and `cryo-backup` the same way if you turned them on.

### Copy the backup in and restore it

The manifest must sit next to the backup. The tool checks the ciphertext against the manifest and then checks the decrypted dump before it restores anything. `--force` is required.

**terminal**

```
$ POD=$(kubectl -n cryo get pod -l app.kubernetes.io/component=api -o jsonpath='{.items[0].metadata.name}')
$ kubectl -n cryo cp ./cryo.dump.enc "$POD:/tmp/cryo.dump.enc"
$ kubectl -n cryo cp ./cryo.dump.enc.manifest.json "$POD:/tmp/cryo.dump.enc.manifest.json"
$ kubectl -n cryo exec "$POD" -- python -m cryo.operations.backup restore --backup /tmp/cryo.dump.enc --force
$ kubectl -n cryo exec "$POD" -- alembic upgrade head
```

### Check before you start anything

Sign in and check that sources, destinations, presets, tables and recent runs are all there. Open one source and run its connection test, which proves the stored secrets decrypt with this `CRYO_SECRET_KEY`. Open **Control Room** (`/control-room`) and check that the pauses are what you expect before maintenance can start.

### Start the writers again

Run your normal `helm upgrade` command. It puts back the replica counts from your values, and the CronJobs' `suspend` settings.

Done when every pod is ready and the next scan and run behave normally.

Write down the start and end times, the backup's manifest hash, and anything that went differently from this procedure. That record is your measured restore time.

## Losing Redis

When Redis restarts empty, the governor rebuilds the queue from PostgreSQL: queued jobs are put back, and stale running jobs and table locks are reaped. One known gap remains. The scheduler and the scan scheduler keep their "last cron fire" markers only in Redis. After a restart, every daily preset and the daily scan can fire again within 24 hours of their last run, at whatever time Redis came back. That repeats maintenance and uses engine time. This is tracked in [#177](https://github.com/tech-pw/cryo/issues/177). Until it's fixed, give Redis stable resources and avoid restarting it during the day.

## Where to go next

Upgrade and rollbackWhen a backup is the only way back.CRYO_SECRET_KEYWhy the key has to be backed up with the database.

Source: https://tech-pw.github.io/cryo/backup/
Markdown: https://tech-pw.github.io/cryo/backup/index.md
