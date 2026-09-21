#!/usr/bin/env python3
"""Seed (idempotent upsert) the daily top-500-by-Trino-frequency Spark maintenance preset.

Purpose: reduce real Trino query cost by concentrating Cryo's daily maintenance effort on
exactly the tables Trino actually scans the most -- fragmented/bloated hot tables are what
drive Trino's scan-bytes cost up (more small files / more manifests to open per query), so
keeping the top-N hottest tables well-optimized has the most leverage per maintenance dollar
spent, versus spreading effort evenly (or by size) across the whole fleet.

This script only creates the Preset + its rule. It does NOT compute or pin the top-500 tables
themselves -- that's cryo.scheduler.trino_hot_preset.repin_trino_hot_tables, run daily by
TrinoHotPresetRunner (see cryo/cli.py) once this preset exists. Run this script once (or
whenever you want to change its config); the daily repin runs automatically after that.

Why this preset only runs tables that actually need it: the rule below requires
needs_compaction=true (TableStats' own "does this table currently need maintenance" signal --
computed from small-file/fragmentation/manifest-bloat thresholds elsewhere in the platform).
Being in the daily top-500 by Trino query volume makes a table ELIGIBLE for this preset; the
rule is what stops a healthy, already-optimized-but-frequently-queried table from getting a
needless job. Within the run, cryo.scheduler.scheduler.apply_priority_order (already
query_count_30d-weighted -- see that module) ranks HIGH/MEDIUM/LOW compaction_priority tables
first and, within a tier, puts the hottest/most-reclaimable tables first -- so if capacity is
constrained mid-run, the tables that matter most for both "needs it" and "is hot on Trino"
go first.

Run via: kubectl exec -n cryo deploy/cryo-governor -- python3 scripts/seed_trino_hot_preset.py
         [--spark-destination-name NAME] [--preset-name NAME] [--schedule-cron CRON]
"""

from __future__ import annotations

import argparse

from sqlalchemy import select

from cryo.db.models import Destination, Preset, PresetRule
from cryo.db.session import SessionLocal

_DEFAULT_PRESET_NAME = "daily-trino-hot-tables"
_DEFAULT_SPARK_DESTINATION_NAME = "spark-k8s-fallback"
# 02:00 UTC daily -- off-hours for most workloads, matching LOCAL_LARGE_PRESET's own
# schedule_cron convention (scripts/seed_local_config.py) and giving the daily repin (run
# separately, hourly-aligned via TrinoHotPresetRunner) time to have already run that day.
_DEFAULT_SCHEDULE_CRON = "0 2 * * *"


def upsert_preset_rule(session, preset_id, predicate: dict) -> PresetRule:
    """Idempotent by (preset_id, predicate) -- avoids creating a duplicate identical rule on
    every re-run of this script."""
    existing = session.scalars(
        select(PresetRule).where(PresetRule.preset_id == preset_id)
    ).all()
    for rule in existing:
        if rule.predicate == predicate:
            rule.enabled = True
            session.flush()
            return rule
    rule = PresetRule(preset_id=preset_id, predicate=predicate, enabled=True)
    session.add(rule)
    session.flush()
    return rule


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset-name", default=_DEFAULT_PRESET_NAME)
    parser.add_argument("--spark-destination-name", default=_DEFAULT_SPARK_DESTINATION_NAME)
    parser.add_argument("--schedule-cron", default=_DEFAULT_SCHEDULE_CRON)
    args = parser.parse_args()

    with SessionLocal() as session:
        destination = session.scalar(
            select(Destination).where(Destination.name == args.spark_destination_name)
        )
        if destination is None:
            raise SystemExit(
                f"No Destination named {args.spark_destination_name!r} exists -- create the "
                "Spark destination first (see scripts/seed_local_config.py's "
                "LOCAL_SPARK_DESTINATION for the shape), or pass --spark-destination-name "
                "pointing at an existing one."
            )
        if destination.engine != "spark_emr":
            raise SystemExit(
                f"Destination {args.spark_destination_name!r} has engine={destination.engine!r}, "
                "not a Spark engine -- this preset is meant to run on Spark specifically."
            )

        preset = session.scalar(select(Preset).where(Preset.name == args.preset_name))
        preset_fields = dict(
            destination_id=destination.id,
            mode="standard",
            operations=["optimize", "optimize_manifests", "expire_snapshots", "remove_orphan_files"],
            params={"min_small_files": 10},
            description=(
                "Daily maintenance for the top-N tables by real Trino query volume (see "
                "cryo.planner.trino_hot_tables), gated to only tables that currently "
                "need_compaction. Repinned daily by TrinoHotPresetRunner -- membership is "
                "recomputed from scratch each day, not accumulated."
            ),
            guardrails={"cooldown_hours": 24},
            schedule_cron=args.schedule_cron,
            is_default=False,
        )
        if preset is None:
            preset = Preset(name=args.preset_name, **preset_fields)
            session.add(preset)
        else:
            for field, value in preset_fields.items():
                setattr(preset, field, value)
            preset.enabled = True
        session.flush()

        # Only eligible (top-N-by-Trino-frequency, via daily TableAssignment pin) AND
        # currently needing maintenance -- being hot on Trino alone does not create a job for
        # an already-healthy table.
        rule = upsert_preset_rule(
            session, preset.id, {"field": "needs_compaction", "op": "=", "value": True}
        )

        session.commit()
        print(
            f"Preset {preset.name!r} ({preset.id}) -> destination "
            f"{destination.name!r} ({destination.engine}), rule {rule.id} "
            f"(needs_compaction=true), schedule_cron={preset.schedule_cron!r}."
        )
        print(
            "Next: TrinoHotPresetRunner (cryo scheduler / cryo serve) will pin the current "
            "top-N Trino-hot tables to this preset on its own daily cadence -- or run "
            "cryo.scheduler.trino_hot_preset.repin_trino_hot_tables directly for an "
            "immediate first pin."
        )


if __name__ == "__main__":
    main()
