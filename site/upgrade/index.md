---
title: Upgrade and rollback
description: Upgrading Cryo on Kubernetes -- the migration hook, checking the migration path, and the limits of a rollback.
canonical: https://tech-pw.github.io/cryo/upgrade/
updated: 2026-10-26
---

# Upgrade and rollback

How to move a Cryo release forward, what a downgrade can and cannot undo, and how to check the migration path before you upgrade.

## Upgrade

`helm upgrade` runs `alembic upgrade head` as a `pre-install,pre-upgrade` hook and blocks the release until that Job finishes. It does not delete the external database or the warehouse.

**terminal**

```
$ helm upgrade --install cryo oci://ghcr.io/tech-pw/charts/cryo --version 0.1.6 \
--namespace cryo \
--set global.envFromSecret=cryo-secrets
$ kubectl -n cryo rollout status deployment/cryo-api --timeout=180s
```

Pin the digests. The image tag is static, so a rollout without a digest change can leave pods running the old image while the chart reports success.

## Check the migration path first

Cryo renders the complete Alembic graph in both directions for every signed release. Run it before you upgrade, not after something fails:

**terminal**

```
$ python scripts/build_upgrade_rollback_evidence.py \
--revision "$(git rev-parse HEAD)" \
--output build/upgrade-rollback-evidence.json
```

The record fingerprints transaction-wrapped PostgreSQL SQL for `base → head` and `head → base`. It catches disconnected revisions, missing downgrade paths, and migrations that cannot render before an image is released.

What offline rendering proves, and what it does not

It is repository evidence, not a deployed rollback claim. It does not prove data preservation, application compatibility, availability, or duration. A production drill is still required: upgrade a restored database, verify representative Cryo records and API health, roll back, verify them again, and record the service impact.

## Before you upgrade

- [Backup and restore](https://tech-pw.github.io/cryo/backup/): Take a backup

### Check the changelog

Cryo is pre-1.0. A minor release may change the HTTP API, the Helm values, the database schema or the engine SQL, and there is no still-working deprecation window. The changelog says so — read it.

### Check the off-peak window

A migration that rewrites a large table runs on your database, not on your engine. The governor's off-peak window does not apply to it.

### Verify the chart renders

`helm template` needs no cluster and catches a broken template before the hook runs.

## Rollback

Roll back the release:

**terminal**

```
$ helm rollback cryo --namespace cryo
```

The pre-upgrade hook runs again on the way down, so the database is migrated back with the previous release's Alembic graph.

### What a rollback cannot undo

Three things survive a rollback, and two of them are not reversible:

| Survives the rollback | Why, and what to do |
| --- | --- |

| **Data written by the new release** | Maintenance that ran under the new version has already rewritten data files, expired snapshots and removed orphans. The old release does not undo it. If the new version did something you did not want, restore from a backup. |

| **Irreversible migrations** | A migration that drops or renames a column cannot be reversed by Alembic's `downgrade()`. Cryo's policy is additive-first precisely so this stays rare, and every revision documents when it is not. Check the evidence file above before you rely on a downgrade. |

| **Queue and lease state** | Redis is reconstructable. The scheduler and governor re-elect and the next scheduled run rebuilds the queue. |

A rollback is not a restore

Rolling back moves the application and the schema back. It does not bring back expired snapshots or rewritten data files. If the rollback is because the new version wrote something wrong, restore the database from a backup and assess the warehouse separately.

## Where to go next

Backup and restoreTaking a backup, and what to do with one.Install on KubernetesA first install, or a fresh environment.

Source: https://tech-pw.github.io/cryo/upgrade/
Markdown: https://tech-pw.github.io/cryo/upgrade/index.md
