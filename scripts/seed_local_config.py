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

LOCAL_DESTINATION = {
    "name": "local-trino",
    "engine": "trino",
    "config": {
        "host": "localhost",
        "port": 8080,
        "user": "cryo",
        "catalog": "iceberg",
        "per_replica_cap": 5,
    },
}

LOCAL_PRESET = {
    "name": "local-standard",
    "mode": "standard",
    "operations": ["expire_snapshots", "optimize"],
    "params": {"min_small_files": 10},
    "description": "Default local Trino maintenance profile",
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
            enabled=True,
        )
        session.add(preset)
    else:
        preset.destination_id = destination_id
        preset.mode = payload["mode"]
        preset.operations = payload["operations"]
        preset.params = payload["params"]
        preset.description = payload.get("description")
        preset.enabled = True
    session.flush()
    return preset


def main() -> None:
    with SessionLocal() as session:
        source = upsert_source(session, LOCAL_SOURCE)
        destination = upsert_destination(session, LOCAL_DESTINATION)
        preset = upsert_preset(session, LOCAL_PRESET, destination_id=destination.id)
        session.commit()
        print(f"source={source.id} destination={destination.id} preset={preset.id}")


if __name__ == "__main__":
    main()
