#!/usr/bin/env python3
"""Upsert local Cryo source/destination/preset rows for docker-compose dev."""

from __future__ import annotations

import os

from sqlalchemy import select

from cryo.db.models import Destination, Preset, Source
from cryo.db.session import SessionLocal


def _docker_mode() -> bool:
    return os.environ.get("START_CRYO_DOCKER", "0") == "1"


def _local_rest_source_config() -> dict[str, str]:
    docker = _docker_mode()
    return {
        "catalog_name": "rest",
        "type": "rest",
        "uri": "http://iceberg-rest:8181" if docker else "http://localhost:8181",
        "warehouse": "s3://warehouse/",
        "s3.endpoint": "http://minio:9000" if docker else "http://localhost:9000",
        "s3.access-key-id": "minioadmin",
        "s3.secret-access-key": "minioadmin",
        "s3.path-style-access": "true",
        "s3.region": "us-east-1",
    }


def _local_hms_source_config() -> dict[str, str]:
    docker = _docker_mode()
    return {
        "catalog_name": "hms",
        "type": "hive",
        "uri": "thrift://hive-metastore:9083" if docker else "thrift://localhost:9083",
        "warehouse": "s3://warehouse/",
        "s3.endpoint": "http://minio:9000" if docker else "http://localhost:9000",
        "s3.access-key-id": "minioadmin",
        "s3.secret-access-key": "minioadmin",
        "s3.path-style-access": "true",
        "s3.region": "us-east-1",
    }


LOCAL_SOURCE = {
    "name": "local-rest",
    "type": "rest",
    "config": _local_rest_source_config(),
}

LOCAL_TRINO_DESTINATION = {
    "name": "local-trino",
    "engine": "trino",
    "config": {
        "host": "localhost",
        "port": 8080,
        "user": "cryo",
        "catalog": "iceberg",
        "per_replica_cap": 5,
        "offpeak_start_ist": 0,
        "offpeak_end_ist": 0,
        "adaptive_budget": False,
    },
}

LOCAL_HMS_SOURCE = {
    "name": "local-hms",
    "type": "hive",
    "config": _local_hms_source_config(),
}

LOCAL_TRINO_HMS_DESTINATION = {
    "name": "local-trino-hms",
    "engine": "trino",
    "config": {
        "host": "localhost",
        "port": 8080,
        "user": "cryo",
        "catalog": "iceberg_hms",
        "per_replica_cap": 5,
        "offpeak_start_ist": 0,
        "offpeak_end_ist": 0,
        "adaptive_budget": False,
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

LOCAL_NATIVE_DESTINATION = {
    "name": "native-datafusion",
    "engine": "native",
    "config": {
        "slot_cap": 5,
        "operation_mode": "execute",
        "timeout_seconds": 3600,
    },
}

LOCAL_STANDARD_PRESET = {
    "name": "local-standard",
    "mode": "deep",
    "operations": ["expire_snapshots", "remove_orphan_files", "optimize", "optimize_manifests"],
    "params": {"min_small_files": 10},
    "description": "Default local Trino maintenance profile (deep mode, all steps)",
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

LOCAL_DEFAULT_NATIVE_PRESET = {
    "name": "default-native-deep",
    "mode": "deep",
    # Phase 2: expire_snapshots is paired with remove_orphan_files to avoid file leaks (#20).
    "operations": ["expire_snapshots", "remove_orphan_files"],
    "params": {
        "min_small_files": 10,
        "retention_days": 7,
        "retain_last": 1,
        "orphan_retention_days": 7,
    },
    "description": (
        "Default native maintenance (expire_snapshots + remove_orphan_files; optimize deferred)"
    ),
    "guardrails": {"cooldown_hours": 24},
    "is_default": True,
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
            is_default=bool(payload.get("is_default", False)),
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
        preset.is_default = bool(payload.get("is_default", False))
        preset.enabled = True
    session.flush()
    return preset


def main() -> None:
    with SessionLocal() as session:
        source = upsert_source(session, LOCAL_SOURCE)
        hms_source = upsert_source(session, LOCAL_HMS_SOURCE)
        trino = upsert_destination(session, LOCAL_TRINO_DESTINATION)
        trino_hms = upsert_destination(session, LOCAL_TRINO_HMS_DESTINATION)
        spark = upsert_destination(session, LOCAL_SPARK_DESTINATION)
        native = upsert_destination(session, LOCAL_NATIVE_DESTINATION)
        standard = upsert_preset(session, LOCAL_STANDARD_PRESET, destination_id=trino.id)
        large = upsert_preset(session, LOCAL_LARGE_PRESET, destination_id=trino.id)
        default_native = upsert_preset(
            session, LOCAL_DEFAULT_NATIVE_PRESET, destination_id=native.id
        )
        session.commit()
        print(
            f"source={source.id} hms_source={hms_source.id} trino={trino.id} "
            f"trino_hms={trino_hms.id} spark={spark.id} native={native.id} "
            f"preset_standard={standard.id} preset_large={large.id} "
            f"preset_default_native={default_native.id}"
        )


if __name__ == "__main__":
    main()
