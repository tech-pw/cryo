#!/usr/bin/env python3
"""Upsert local Cryo source/destination/preset rows for docker-compose dev."""

from __future__ import annotations

from sqlalchemy import select

from cryo.db.models import Destination, Preset, Source
from cryo.db.session import SessionLocal

LOCAL_SOURCE = {
    "name": "local-rest",
    "type": "rest",
    "config": {
        "catalog_name": "rest",
        "type": "rest",
        "uri": "http://localhost:8181",
        "warehouse": "s3://warehouse/",
        "s3.endpoint": "http://localhost:9000",
        "s3.access-key-id": "minioadmin",
        "s3.secret-access-key": "minioadmin",
        "s3.path-style-access": "true",
    },
}

LOCAL_TRINO_DESTINATION = {
    "name": "local-trino",
    "engine": "trino",
    "config": {
        "host": "localhost",
        "port": 8090,
        "user": "cryo",
        "catalog": "iceberg",
        "per_replica_cap": 5,
    },
}

LOCAL_SPARK_DESTINATION = {
    "name": "spark-large",
    "engine": "spark_emr",
    "config": {
        "local": True,
        "slot_cap": 2,
        "application_id": "local-spark",
    },
}

LOCAL_STANDARD_PRESET = {
    "name": "local-standard",
    "mode": "standard",
    "operations": ["expire_snapshots", "optimize"],
    "params": {"min_small_files": 10},
    "description": "Default local Trino maintenance profile for small tables",
    "guardrails": {},
}

LOCAL_LARGE_PRESET = {
    "name": "local-large",
    "mode": "standard",
    "operations": ["expire_snapshots", "remove_orphan_files", "optimize", "optimize_manifests"],
    "params": {"min_small_files": 10, "delete_ratio_trigger": 20},
    "description": "Large-table preset routed to Spark when size_gb > 100",
    "guardrails": {
        "routing_rules": [
            {
                "predicate": {"field": "total_size_gb", "op": ">", "value": 100},
                "destination": "spark-large",
            }
        ],
        "cooldown_hours": 24,
        "adaptive_routing": True,
        "engine_pool": ["local-trino", "spark-large"],
        "engine_preferences": {
            "spark_min_gb": 100,
            "native_min_gb": 50,
            "latency_p99_ms": 500,
        },
    },
    "schedule_cron": "0 2 * * *",
}


def upsert_source(session, payload: dict) -> Source:
    source = session.scalar(select(Source).where(Source.name == payload["name"]))
    if source is None:
        source = Source(
            name=payload["name"],
            type=payload["type"],
            config=payload["config"],
            enabled=True,
        )
        session.add(source)
    else:
        source.type = payload["type"]
        source.config = payload["config"]
        source.enabled = True
    session.flush()
    return source


def upsert_destination(session, payload: dict) -> Destination:
    destination = session.scalar(select(Destination).where(Destination.name == payload["name"]))
    if destination is None:
        destination = Destination(
            name=payload["name"],
            engine=payload["engine"],
            config=payload["config"],
            enabled=True,
        )
        session.add(destination)
    else:
        destination.engine = payload["engine"]
        destination.config = payload["config"]
        destination.enabled = True
    session.flush()
    return destination


def upsert_preset(session, payload: dict, *, destination_id) -> Preset:
    preset = session.scalar(select(Preset).where(Preset.name == payload["name"]))
    if preset is None:
        preset = Preset(
            name=payload["name"],
            destination_id=destination_id,
            mode=payload["mode"],
            operations=payload["operations"],
            params=payload["params"],
            description=payload.get("description"),
            guardrails=payload.get("guardrails") or {},
            schedule_cron=payload.get("schedule_cron"),
            enabled=True,
        )
        session.add(preset)
    else:
        preset.destination_id = destination_id
        preset.mode = payload["mode"]
        preset.operations = payload["operations"]
        preset.params = payload["params"]
        preset.description = payload.get("description")
        preset.guardrails = payload.get("guardrails") or {}
        preset.schedule_cron = payload.get("schedule_cron")
        preset.enabled = True
    session.flush()
    return preset


def main() -> None:
    with SessionLocal() as session:
        source = upsert_source(session, LOCAL_SOURCE)
        trino = upsert_destination(session, LOCAL_TRINO_DESTINATION)
        spark = upsert_destination(session, LOCAL_SPARK_DESTINATION)
        standard = upsert_preset(session, LOCAL_STANDARD_PRESET, destination_id=trino.id)
        large = upsert_preset(session, LOCAL_LARGE_PRESET, destination_id=trino.id)
        session.commit()
        print(
            f"source={source.id} trino={trino.id} spark={spark.id} "
            f"preset_standard={standard.id} preset_large={large.id}"
        )


if __name__ == "__main__":
    main()
