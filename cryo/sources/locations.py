"""Helpers for catalog-native table warehouse locations (no Iceberg metadata load)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

HMS_TABLE_BATCH_SIZE = 100


@dataclass(frozen=True)
class CatalogTableLocation:
    """Warehouse location as stored in the catalog, plus a per-table error if unreadable."""

    schema_name: str
    table_name: str
    location: str | None
    error: str | None = None


def iceberg_table_location(table_obj: object) -> str | None:
    """Resolve ``location`` on a loaded PyIceberg table without importing the scanner."""
    location = getattr(table_obj, "location", None)
    if callable(location):
        try:
            location = location()
        except TypeError:
            location = None
    if location:
        return str(location)
    metadata = getattr(table_obj, "metadata", None)
    if metadata is not None:
        meta_location = getattr(metadata, "location", None)
        if meta_location:
            return str(meta_location)
    return None


def hive_storage_location(hive_table: object) -> str | None:
    sd = getattr(hive_table, "sd", None)
    if sd is None and isinstance(hive_table, dict):
        sd = hive_table.get("sd") or hive_table.get("StorageDescriptor")
    if sd is None:
        return None
    location = getattr(sd, "location", None)
    if location is None and isinstance(sd, dict):
        location = sd.get("location") or sd.get("Location")
    return str(location) if location else None


def hive_table_name(hive_table: object) -> str | None:
    for attr in ("tableName", "table_name"):
        value = getattr(hive_table, attr, None)
        if value:
            return str(value)
    if isinstance(hive_table, dict):
        value = (
            hive_table.get("tableName") or hive_table.get("table_name") or hive_table.get("Name")
        )
        return str(value) if value else None
    return None


def hive_db_name(hive_table: object, default: str) -> str:
    for attr in ("dbName", "db_name"):
        value = getattr(hive_table, attr, None)
        if value:
            return str(value)
    if isinstance(hive_table, dict):
        value = (
            hive_table.get("dbName") or hive_table.get("db_name") or hive_table.get("DatabaseName")
        )
        if value:
            return str(value)
    return default


def call_hms(client: object, method: str, *args: Any) -> Any:
    fn = getattr(client, method, None)
    if fn is None:
        raise AttributeError(method)
    return fn(*args)


def list_hive_table_locations(
    client: object,
    *,
    databases: list[str] | None = None,
) -> list[CatalogTableLocation]:
    """Enumerate HMS ``sd.location`` without loading Iceberg metadata.json.

    ``databases`` restricts the walk to a subset, letting a caller page through a large
    metastore a slice at a time instead of blocking on all of them (14k tables takes well
    over a minute on a real catalog). Default is every database, as before.
    """
    if databases is None:
        databases = [str(name) for name in call_hms(client, "get_all_databases")]
    results: list[CatalogTableLocation] = []
    for database in databases:
        db_name = str(database)
        try:
            names = [str(name) for name in call_hms(client, "get_all_tables", db_name)]
        except Exception as exc:
            results.append(
                CatalogTableLocation(
                    schema_name=db_name,
                    table_name="*",
                    location=None,
                    error=f"list tables failed: {exc}"[:500],
                )
            )
            continue
        for hive_table in _hive_tables_for_db(client, db_name, names):
            table_name = hive_table_name(hive_table)
            if not table_name:
                continue
            schema_name = hive_db_name(hive_table, db_name)
            try:
                location = hive_storage_location(hive_table)
                results.append(
                    CatalogTableLocation(
                        schema_name=schema_name,
                        table_name=table_name,
                        location=location,
                    )
                )
            except Exception as exc:
                results.append(
                    CatalogTableLocation(
                        schema_name=schema_name,
                        table_name=table_name,
                        location=None,
                        error=str(exc)[:500],
                    )
                )
    return results


def _hive_tables_for_db(client: object, db_name: str, names: list[str]) -> list[object]:
    if not names:
        return []
    if hasattr(client, "get_table_objects_by_name"):
        tables: list[object] = []
        for start in range(0, len(names), HMS_TABLE_BATCH_SIZE):
            chunk = names[start : start + HMS_TABLE_BATCH_SIZE]
            try:
                tables.extend(list(call_hms(client, "get_table_objects_by_name", db_name, chunk)))
            except Exception:
                tables.extend(_hive_tables_one_by_one(client, db_name, chunk))
        return tables
    return _hive_tables_one_by_one(client, db_name, names)


def _hive_tables_one_by_one(client: object, db_name: str, names: list[str]) -> list[object]:
    tables: list[object] = []
    for name in names:
        try:
            tables.append(call_hms(client, "get_table", db_name, name))
        except Exception:
            continue
    return tables


def glue_storage_location(glue_table: dict[str, Any]) -> str | None:
    descriptor = glue_table.get("StorageDescriptor") or {}
    location = descriptor.get("Location")
    return str(location) if location else None


def list_glue_table_locations(glue_client: object) -> list[CatalogTableLocation]:
    """Enumerate Glue ``StorageDescriptor.Location`` without loading Iceberg metadata."""
    results: list[CatalogTableLocation] = []
    for database in _glue_database_names(glue_client):
        try:
            for glue_table in _glue_tables_in_database(glue_client, database):
                table_name = str(glue_table.get("Name") or "")
                if not table_name:
                    continue
                schema_name = str(glue_table.get("DatabaseName") or database)
                results.append(
                    CatalogTableLocation(
                        schema_name=schema_name,
                        table_name=table_name,
                        location=glue_storage_location(glue_table),
                    )
                )
        except Exception as exc:
            results.append(
                CatalogTableLocation(
                    schema_name=database,
                    table_name="*",
                    location=None,
                    error=f"list tables failed: {exc}"[:500],
                )
            )
    return results


def _glue_database_names(glue_client: object) -> list[str]:
    names: list[str] = []
    paginator = getattr(glue_client, "get_paginator", None)
    if callable(paginator):
        for page in paginator("get_databases").paginate():
            for database in page.get("DatabaseList", []):
                name = database.get("Name")
                if name:
                    names.append(str(name))
        return names
    response = glue_client.get_databases()  # type: ignore[attr-defined]
    for database in response.get("DatabaseList", []):
        name = database.get("Name")
        if name:
            names.append(str(name))
    return names


def _glue_tables_in_database(glue_client: object, database: str) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    paginator = getattr(glue_client, "get_paginator", None)
    if callable(paginator):
        for page in paginator("get_tables").paginate(DatabaseName=database):
            tables.extend(page.get("TableList", []))
        return tables
    response = glue_client.get_tables(DatabaseName=database)  # type: ignore[attr-defined]
    tables.extend(response.get("TableList", []))
    return tables


def bucket_from_location(location: str | None) -> str | None:
    """Extract the bucket from an object-store warehouse URI, or None if there isn't one.

    Accepts the schemes Iceberg warehouses actually appear under -- ``s3://``, plus the
    Hadoop-era ``s3a://``/``s3n://`` spellings that HMS rows commonly still carry, and
    ``gs://``/``abfss://`` so a non-AWS catalog degrades to "no bucket" rather than
    silently reporting an AWS-looking one. Local paths (``file:///``, bare paths) yield
    None by design: there is no bucket to offer.
    """
    if not location:
        return None
    text = str(location).strip()
    for scheme in ("s3://", "s3a://", "s3n://", "gs://", "abfss://"):
        if text.lower().startswith(scheme):
            remainder = text[len(scheme) :]
            # abfss://container@account.dfs.core.windows.net/path -- the container is the
            # closest analogue to a bucket, so take the part before "@" when present.
            host = remainder.split("/", 1)[0]
            if "@" in host:
                host = host.split("@", 1)[0]
            return host or None
    return None


def probe_bucket_access(
    s3_client: object, buckets: list[str], *, max_workers: int = 8
) -> dict[str, tuple[str, str | None]]:
    """Check whether these buckets can actually be read, returning ``{bucket: (status, detail)}``.

    A bucket appearing in catalog metadata says nothing about whether Cryo's credentials can
    read it -- the two are configured by different people, often at different times. Without
    this, the first scan is where that mismatch surfaces, long after onboarding said
    "Connected". One ``list_objects_v2(MaxKeys=1)`` per bucket is the cheapest call that
    proves read access; measured at 0.34s for 12 buckets in parallel.

    Never raises: a probe that fails for an unexpected reason reports ``unknown`` rather than
    failing discovery, because an unprobeable bucket is still worth showing.
    """
    from concurrent.futures import ThreadPoolExecutor

    def _probe(bucket: str) -> tuple[str, tuple[str, str | None]]:
        try:
            s3_client.list_objects_v2(Bucket=bucket, MaxKeys=1)  # type: ignore[attr-defined]
            return bucket, ("readable", None)
        except Exception as exc:  # noqa: BLE001 -- every failure mode maps to a status below
            code = ""
            response = getattr(exc, "response", None)
            if isinstance(response, dict):
                code = str(response.get("Error", {}).get("Code", ""))
            if code in {"AccessDenied", "AllAccessDisabled", "403"}:
                return bucket, ("denied", "Credentials can't read this bucket")
            if code in {"NoSuchBucket", "404"}:
                return bucket, ("missing", "Bucket does not exist")
            return bucket, ("unknown", str(exc)[:200])

    if not buckets:
        return {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return dict(pool.map(_probe, buckets))


def s3_client_kwargs_for_source_config(source_config: dict[str, Any]) -> dict[str, Any]:
    """Translate a source's S3 settings into ``boto3.client("s3", **kwargs)`` arguments.

    Returns kwargs rather than a client so each caller passes its own ``boto3`` reference.
    Callers that build the client here instead would move the patch point into this module
    and silently break anything stubbing ``boto3`` where the client is actually used.
    Sharing the translation is the part that matters: the orphan scanner and the onboarding
    bucket probe must interpret endpoint/keys/region/path-style identically, or the probe
    would greenlight settings the scanner then fails on.
    """
    from botocore.config import Config

    path_style = str(source_config.get("s3.path-style-access", "false")).lower() in {
        "true",
        "1",
        "yes",
    }
    return {
        "endpoint_url": source_config.get("s3.endpoint"),
        "aws_access_key_id": source_config.get("s3.access-key-id"),
        "aws_secret_access_key": source_config.get("s3.secret-access-key"),
        "region_name": source_config.get("s3.region", "us-east-1"),
        "config": Config(s3={"addressing_style": "path" if path_style else "auto"}),
    }
