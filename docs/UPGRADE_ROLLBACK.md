# Upgrade and rollback evidence

Cryo renders the complete Alembic graph in both directions for every signed
release:

```bash
python scripts/build_upgrade_rollback_evidence.py \
  --revision "$(git rev-parse HEAD)" \
  --output build/upgrade-rollback-evidence.json
```

The record fingerprints transaction-wrapped PostgreSQL SQL for `base -> head`
and `head -> base`. This catches disconnected revisions, missing downgrade
paths, and migrations that cannot render before an image is released.

Offline rendering is repository evidence, not a deployed rollback claim. It
does not prove data preservation, application compatibility, availability, or
duration. GA still requires an independently retained production-shaped drill
that upgrades a restored database, verifies representative Cryo records and
API health, rolls back, verifies them again, and records service impact.
