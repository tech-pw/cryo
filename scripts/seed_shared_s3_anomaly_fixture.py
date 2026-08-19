#!/usr/bin/env python3
"""Register overlapping Iceberg names on the local REST (and HMS) catalogs.

LOCAL DEV ONLY. Creates:

- ``demo.orders_evolving`` at the same warehouse as ``demo.orders`` (exact alias)
- ``demo.orders_nested`` under ``demo.orders/nested_alias`` (nested prefix)

Requires ``make up-datalake`` and ``make seed`` (so ``demo.orders`` exists).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, TableAlreadyExistsError


def _docker_mode() -> bool:
    return os.environ.get("START_CRYO_DOCKER", "0") == "1"


def _rest_config() -> dict[str, str]:
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


def _hms_config() -> dict[str, str]:
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

NAMESPACE = "demo"
BASE_TABLE = "orders"
ALIAS_TABLE = "orders_evolving"
NESTED_TABLE = "orders_nested"


def _catalog(config: dict[str, str]):
    properties = dict(config)
    name = properties.pop("catalog_name", "local")
    return load_catalog(str(name), **properties)


def _ensure_namespace(catalog, namespace: str) -> None:
    try:
        catalog.create_namespace(namespace)
    except (NamespaceAlreadyExistsError, Exception):
        pass


def _register_alias(catalog, identifier: tuple[str, str], metadata_location: str) -> None:
    try:
        catalog.register_table(identifier, metadata_location)
        print(f"Registered {identifier[0]}.{identifier[1]} -> {metadata_location}")
    except TableAlreadyExistsError:
        print(f"{identifier[0]}.{identifier[1]} already exists")
    except Exception as exc:
        print(f"register_table {identifier} failed: {exc}", file=sys.stderr)


def _create_nested(catalog, identifier: tuple[str, str], schema, location: str) -> None:
    try:
        catalog.create_table(identifier, schema=schema, location=location)
        print(f"Created {identifier[0]}.{identifier[1]} at {location}")
    except TableAlreadyExistsError:
        print(f"{identifier[0]}.{identifier[1]} already exists")
    except Exception as exc:
        print(f"create_table {identifier} failed: {exc}", file=sys.stderr)


def seed_catalog(label: str, config: dict[str, str]) -> None:
    print(f"\n=== {label} ===")
    catalog = _catalog(config)
    _ensure_namespace(catalog, NAMESPACE)
    try:
        base = catalog.load_table((NAMESPACE, BASE_TABLE))
    except Exception as exc:
        print(
            f"{label}: {NAMESPACE}.{BASE_TABLE} not found ({exc}). Run `make seed` first.",
            file=sys.stderr,
        )
        return
    metadata_location = str(base.metadata_location)
    _register_alias(catalog, (NAMESPACE, ALIAS_TABLE), metadata_location)
    nested_location = f"{str(base.location()).rstrip('/')}/nested_alias"
    _create_nested(catalog, (NAMESPACE, NESTED_TABLE), base.schema(), nested_location)


def main() -> int:
    docker = os.environ.get("START_CRYO_DOCKER", "0") == "1"
    if docker:
        print("Using docker-network catalog URIs (START_CRYO_DOCKER=1)")
    seed_catalog("local-rest", _rest_config())
    try:
        seed_catalog("local-hms", _hms_config())
    except Exception as exc:
        print(f"local-hms seed skipped: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
