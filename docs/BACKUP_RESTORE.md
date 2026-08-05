# Backup and restore

PostgreSQL is Cryo's durable source of truth for intents, fences, attempts, effects, approvals,
controls, jobs, and audit evidence. Redis is reconstructable queue/lease state and is not backed up.

## Create an encrypted backup

Set `CRYO_BACKUP_KEY` to URL-safe base64 encoding of 32 random bytes and keep it in a secret manager
separate from the backup:

```bash
python -m cryo.operations.backup create --output /backups/cryo.dump.enc
```

The command runs `pg_dump --format=custom`, encrypts the dump as bounded 4 MiB AES-256-GCM chunks,
and writes an adjacent checksum manifest. The database password is passed through
`PGPASSWORD`, never a process argument.

## Scheduled offsite backups

Enable the chart's backup CronJob only after assigning an IAM/IRSA-capable service account and an
offsite S3 prefix:

```yaml
backup:
  enabled: true
  schedule: "0 2 * * *"
  s3Uri: s3://company-recovery/cryo/production/
  retentionDays: 30
  serviceAccount: cryo-backup
```

The CronJob uses the same immutable Cryo image and `CRYO_DATABASE_URL`/`CRYO_BACKUP_KEY` secret
contract. It uploads the encrypted envelope first and its manifest last, so the manifest is the
commit marker. Retention deletes only timestamped Cryo backup envelopes and manifests below the
configured prefix; unrelated objects are never selected. Local artifacts are removed after each
attempt. Use a bucket in a separate recovery account or region with versioning, object lock, and
least-privilege write/list/delete permissions.

## Restore drill

Restore only into an isolated empty environment first:

```bash
python -m cryo.operations.backup restore \
  --backup /backups/cryo.dump.enc \
  --force
alembic upgrade head
```

Restore verifies the ciphertext manifest and authenticated encryption before invoking
`pg_restore --clean --if-exists --exit-on-error`. `--force` is mandatory because restoration is
destructive.

After restore:

1. Keep Scheduler, Governor, Worker, and scanner scaled to zero.
2. Run migrations and readiness checks.
3. Compare counts for intents, attempts, effects, jobs, audit rows, sources, and destinations.
4. Start the reconciler path first; verify unknown-effect backlog and engine probes.
5. Start Scheduler/Governor/Worker only after pause controls and fencing state are confirmed.
6. Record start/end time, backup manifest hash, counts, SLO screenshots, and all deviations.

Repository tests prove envelope integrity, command safety, offsite publication ordering, and scoped
retention only. Wave 2 still requires deployment of the CronJob and two retained restore drills
meeting RTO <=30 minutes and RPO 0 for acknowledged intents.
