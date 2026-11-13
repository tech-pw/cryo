---
title: Upgrade and rollback
description: How to upgrade a Cryo Helm release, how the migration hook behaves, and what a rollback can and can't undo.
canonical: https://tech-pw.github.io/cryo/upgrade/
updated: 2026-11-13
---

# Upgrade and rollback

Upgrade one released version at a time, with a database backup taken first. Migrations run before any pod changes. Rolling back the chart doesn't roll back the schema.

## Before you upgrade

- **Pick a released tag.** Upgrade to a `v*` tag, never to `main` or `latest`. The chart pins that release's images by digest. See [tags](https://github.com/tech-pw/cryo/tags) and [CHANGELOG.md](https://github.com/tech-pw/cryo/blob/main/CHANGELOG.md).
- **Back up the database.** This is the only complete way back. See [Backup and restore](https://tech-pw.github.io/cryo/backup/).
- **Record the schema revision** you're on now, so you know where a schema downgrade would have to go:

**terminal**

```
$ kubectl -n cryo exec deploy/cryo-api -- alembic current
```

## Upgrade

### Pick the new chart version

These are the same variables as [Install](https://tech-pw.github.io/cryo/install/#install), with the new release. The chart pins that release's images by digest, so there are no digests to look up.

**terminal**

```
$ export CRYO_CHART=oci://ghcr.io/tech-pw/charts/cryo
$ export CRYO_CHART_VERSION=&lt;new-version&gt;
$ helm show values "$CRYO_CHART" --version "$CRYO_CHART_VERSION" > cryo-defaults.yaml
```

### Update the values file

Compare your `cryo-values.yaml` with the new `cryo-defaults.yaml` for renamed or added settings. If you pinned image tags or digests yourself, update them to the new release.

### Render, then upgrade

**terminal**

```
$ helm template cryo "$CRYO_CHART" --version "$CRYO_CHART_VERSION" --namespace cryo -f cryo-values.yaml > /dev/null
$ helm upgrade cryo "$CRYO_CHART" --version "$CRYO_CHART_VERSION" --namespace cryo -f cryo-values.yaml --wait --timeout 15m
```

Done when Helm prints `STATUS: deployed` and `alembic current` shows a revision ending in `(head)`.

## How the migration hook behaves

A `pre-upgrade` hook Job, `cryo-migrate-<revision>`, runs `alembic upgrade head` with the new image before any Deployment changes. While it runs, the old version keeps serving.

**One transaction**

The whole chain of pending migrations runs in a single PostgreSQL transaction. If any step fails, none of them is applied, and the schema stays on the old revision.

**Fails fast on locks**

Migrations connect with `lock_timeout=10s`. If a long-running transaction holds a table lock, the migration fails instead of queueing behind it and blocking every reader. The Job retries up to `migration.backoffLimit` (2) times.

**A failed hook fails the release**

Helm marks the release failed, and the Deployments stay on the old version. The Job and its log are kept: `kubectl -n cryo logs job/cryo-migrate-<revision>`.

A clean log isn't proof

A log that prints `Running upgrade …` and exits 0 doesn't prove the schema changed. Check `alembic current`, or read `cryo.alembic_version` directly.

## Rollback

`helm rollback` puts the old Deployments back, but it doesn't run the migration hook, which only runs on install and upgrade. The schema stays at the newer revision, and the old code runs against it. That's usually fine when the new migrations only added tables or columns. It isn't fine when a migration renamed, dropped or retyped something the old code reads.

In order of preference:

- **Roll forward.** Fix the problem with a newer release or a values change, and leave the schema alone.
- **Restore the backup** you took before the upgrade, then `helm rollback`. This is the only path that brings back data exactly as it was. Anything Cryo recorded after the backup is lost, including runs and audit rows. See [Restore](https://tech-pw.github.io/cryo/backup/#restore).
- **Downgrade the schema, then roll back the chart.** Every migration has a downgrade path, but a downgrade that drops a column or a table drops its data too. Scale down the writers first (see [Restore](https://tech-pw.github.io/cryo/backup/#restore)), run the downgrade with the *new* image, which has the migration files, and then roll back:

**terminal**

```
$ kubectl -n cryo exec deploy/cryo-api -- alembic downgrade REVISION_YOU_RECORDED
$ helm rollback cryo --namespace cryo
```

Schema rollback is checked offline: the release workflow renders the whole migration graph from base to head and back again. That proves the downgrade SQL exists and renders. It doesn't prove your data survives it, so restore from a backup when in doubt.

## Where to go next

Backup and restoreTake the backup this page tells you to take.Install on KubernetesThe install these commands assume.TroubleshootingA failed migration and other symptoms.

Source: https://tech-pw.github.io/cryo/upgrade/
Markdown: https://tech-pw.github.io/cryo/upgrade/index.md
