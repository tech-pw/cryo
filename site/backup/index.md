---
title: Backup and restore
description: Backing up and restoring Cryo -- encrypted PostgreSQL dumps, scheduled offsite backups, and why Redis is not backed up.
canonical: https://tech-pw.github.io/cryo/backup/
updated: 2026-10-26
---

# Backup and restore

PostgreSQL holds Cryo's durable state. Redis does not. This page covers taking an encrypted backup, restoring one, and what you do not need to back up.

## What holds what

| Store | Holds | Back it up? |
| --- | --- | --- |

| **PostgreSQL** | Intents, fences, attempts, effects, approvals, controls, jobs, tables, stats and audit evidence. This is Cryo's durable source of truth. | Yes. This is the backup. |

| **Redis** | The queue, the leader leases, the live counters and the sign-in throttle counters. | No. It is reconstructable. |

| **Object storage** | Your Iceberg data files. Cryo does not own them. | Your backup policy, not Cryo's. |

Losing Redis costs you queued work and the leases. The scheduler and governor re-elect, and the next scheduled run rebuilds the queue. The durable record — what ran, what it did, what it cost — is in PostgreSQL and survives.

## Create an encrypted backup

Set `CRYO_BACKUP_KEY` to the URL-safe base64 encoding of 32 random bytes, and keep that key in a secret manager **separate from the backup**. A backup encrypted with a key stored next to it is not a backup.

**terminal**

```
$ python -m cryo.operations.backup create --output /backups/cryo.dump.enc
```

The command runs `pg_dump --format=custom`, encrypts the dump as bounded 4 MiB AES-256-GCM chunks, and writes an adjacent checksum manifest. The database password is passed through `PGPASSWORD`, never as a process argument, so it does not appear in the process list.

Test the restore

An encrypted backup that has never been restored is a hypothesis. Restore it into a scratch database before you need it, and check that the records are there.

## Restore

Restore is force-gated, because it replaces the schema of the database it is pointed at. Point it at a scratch database first:

**terminal**

```
$ python -m cryo.operations.backup restore \
--database-url "$SCRATCH_DATABASE_URL" \
--backup /backups/cryo.dump.enc \
--force
```

The command verifies the ciphertext against its checksum manifest, decrypts it, verifies the plaintext against the manifest too, and only then runs `pg_restore --exit-on-error --clean --if-exists`. A corrupted or tampered backup fails before it touches the database.

### Verify the restore

Check that the durable state came back, not just that the command exited zero:

**terminal**

```
$ psql "$CRYO_DATABASE_URL" -c "SELECT count(*) FROM cryo.tables WHERE is_active"
$ psql "$CRYO_DATABASE_URL" -c "SELECT count(*) FROM cryo.audit_logs WHERE created_at > now() - interval '7 days'"
$ psql "$CRYO_DATABASE_URL" -c "SELECT count(*) FROM cryo.maintenance_jobs WHERE status = 'succeeded'"
```

Then start the API against the restored database and check `/healthz` and the tables list.

## Scheduled offsite backups

The chart ships a backup CronJob. Enable it only after assigning it a service account that can write to an offsite bucket, and an offsite prefix of its own:

**values.yaml**

```
backup:
enabled: true
schedule: "0 2 * * *"
s3Uri: s3://company-recovery/cryo/production/
retentionDays: 30
serviceAccount: cryo-backup
```

The CronJob writes the same encrypted format, with the key supplied from a Secret, and uses the `create-offsite` command to upload it. Retention is enforced by the bucket's lifecycle policy — the chart does not delete objects.

Keep the key somewhere the backup job cannot reach

The service account that writes the backup should not be able to read the encryption key. If one compromised credential can both take and read a backup, the backup only raises the cost of the breach.

## Where to go next

Source: https://tech-pw.github.io/cryo/backup/
Markdown: https://tech-pw.github.io/cryo/backup/index.md
