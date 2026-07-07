"""Shared helpers for REST catalog + MinIO native integration tests."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.request import urlopen

import boto3
import pyarrow as pa
from botocore.config import Config
from pyiceberg.catalog import load_catalog
from pyiceberg.schema import Schema
from pyiceberg.types import LongType, NestedField, StringType

FIXTURE_NAMESPACE = "native_e2e"
FIXTURE_TABLE = "expire_orphan_fixture"
ORPHAN_OBJECT_NAME = "__cryo_orphan_fixture__.parquet"
ORPHAN_OBJECT_BYTES = 4096
SNAPSHOT_APPEND_BATCHES = 3


@dataclass(frozen=True)
class NativeRestFixture:
    table_ref: str
    location: str
    orphan_uri: str
    orphan_bytes: int
    snapshot_count: int


def rest_uri() -> str | None:
    value = os.getenv("CRYO_REST_URI")
    return value if value else None


def source_config() -> dict[str, str]:
    uri = rest_uri()
    if uri is None:
        raise RuntimeError("CRYO_REST_URI is not set")
    return {
        "uri": uri,
        "warehouse": os.getenv("ICEBERG_WAREHOUSE", "s3://warehouse/"),
        "s3.endpoint": os.getenv("S3_ENDPOINT", "http://localhost:9000"),
        "s3.access-key-id": os.getenv("AWS_ACCESS_KEY_ID", "minioadmin"),
        "s3.secret-access-key": os.getenv("AWS_SECRET_ACCESS_KEY", "minioadmin"),
        "s3.path-style-access": "true",
        "s3.region": os.getenv("AWS_REGION", "us-east-1"),
    }


def catalog_properties() -> dict[str, str]:
    config = source_config()
    return {
        "type": "rest",
        "uri": config["uri"],
        "warehouse": config["warehouse"],
        "s3.endpoint": config["s3.endpoint"],
        "s3.access-key-id": config["s3.access-key-id"],
        "s3.secret-access-key": config["s3.secret-access-key"],
        "s3.path-style-access": config["s3.path-style-access"],
        "s3.region": config["s3.region"],
    }


def wait_for_rest_catalog(*, timeout_seconds: float = 60.0) -> None:
    uri = rest_uri()
    if uri is None:
        raise RuntimeError("CRYO_REST_URI is not set")
    deadline = time.monotonic() + timeout_seconds
    config_url = f"{uri.rstrip('/')}/v1/config"
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(config_url, timeout=3) as response:
                if response.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001 - retry until timeout
            last_error = exc
        time.sleep(1.0)
    raise TimeoutError(f"REST catalog not ready at {config_url}: {last_error}")


def normalize_uri(uri: str) -> str:
    return uri.replace("s3a://", "s3://").replace("s3n://", "s3://")


def parse_s3_uri(uri: str) -> tuple[str, str]:
    normalized = normalize_uri(uri)
    parsed = urlparse(normalized)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"unsupported s3 uri: {uri}")
    prefix = parsed.path.lstrip("/")
    if prefix and not prefix.endswith("/"):
        prefix = f"{prefix}/"
    return parsed.netloc, prefix


def s3_client() -> object:
    config = source_config()
    return boto3.client(
        "s3",
        endpoint_url=config["s3.endpoint"],
        aws_access_key_id=config["s3.access-key-id"],
        aws_secret_access_key=config["s3.secret-access-key"],
        region_name=config["s3.region"],
        config=Config(s3={"addressing_style": "path"}),
    )


def list_object_sizes(location: str) -> dict[str, int]:
    bucket, prefix = parse_s3_uri(location)
    client = s3_client()
    sizes: dict[str, int] = {}
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = str(obj["Key"])
            uri = normalize_uri(f"s3://{bucket}/{key}")
            sizes[uri] = int(obj.get("Size", 0) or 0)
    return sizes


def _arrow_schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("id", pa.int64(), nullable=False),
            pa.field("label", pa.string(), nullable=False),
        ]
    )


def ensure_rest_fixture() -> NativeRestFixture:
    wait_for_rest_catalog()
    catalog = load_catalog("rest", **catalog_properties())
    namespace = (FIXTURE_NAMESPACE,)
    if namespace not in catalog.list_namespaces():
        catalog.create_namespace(FIXTURE_NAMESPACE)

    identifier = (FIXTURE_NAMESPACE, FIXTURE_TABLE)
    try:
        catalog.drop_table(identifier)
    except Exception:
        pass

    schema = Schema(
        NestedField(1, "id", LongType(), required=True),
        NestedField(2, "label", StringType(), required=True),
    )
    catalog.create_table(identifier, schema=schema)
    table = catalog.load_table(identifier)
    arrow_schema = _arrow_schema()

    for batch in range(SNAPSHOT_APPEND_BATCHES):
        arrow = pa.Table.from_arrays(
            [
                pa.array([batch], type=pa.int64()),
                pa.array([f"batch-{batch}"], type=pa.string()),
            ],
            schema=arrow_schema,
        )
        table.append(arrow)
        table = catalog.load_table(identifier)

    location = normalize_uri(table.location())
    bucket, prefix = parse_s3_uri(location)
    orphan_key = f"{prefix}data/{ORPHAN_OBJECT_NAME}"
    orphan_uri = normalize_uri(f"s3://{bucket}/{orphan_key}")
    client = s3_client()
    client.put_object(
        Bucket=bucket,
        Key=orphan_key,
        Body=b"x" * ORPHAN_OBJECT_BYTES,
    )

    snapshot_count = len(table.metadata.snapshots)
    return NativeRestFixture(
        table_ref=f"{FIXTURE_NAMESPACE}.{FIXTURE_TABLE}",
        location=location,
        orphan_uri=orphan_uri,
        orphan_bytes=ORPHAN_OBJECT_BYTES,
        snapshot_count=snapshot_count,
    )
