"""Archive and restore jobs: re-class a partition's live data files in S3, in place.

Runs on a small thread pool in the API process. A job takes the same per-table lock maintenance
uses, so archiving never overlaps a compaction, expiry or orphan removal on that table. Moves
are idempotent (an object already in the target class is skipped), so an interrupted job is
safe to run again.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import ArchiveJob, AuditLog, PartitionStorageClass, Source, Table
from cryo.tiering.executor import (
    ObjectRef,
    TransitionResult,
    parse_s3_uri,
    transition_objects,
)

logger = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=10)
LOCK_TTL_SECONDS = 15 * 60
ACTIVE_STATUSES = ("queued", "running")
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cryo-archive")


def s3_client_for_source(config: Mapping[str, Any]) -> Any:
    """S3 client from the Source's storage settings, assuming ``s3.role-arn`` when set."""
    import boto3
    from botocore.config import Config

    from cryo.sources.locations import s3_client_kwargs_for_source_config

    kwargs = s3_client_kwargs_for_source_config(dict(config))
    kwargs["config"] = kwargs["config"].merge(
        Config(max_pool_connections=32, retries={"max_attempts": 5, "mode": "adaptive"})
    )
    role_arn = config.get("s3.role-arn")
    if role_arn:
        sts = boto3.client(
            "sts",
            region_name=kwargs.get("region_name"),
            aws_access_key_id=kwargs.get("aws_access_key_id"),
            aws_secret_access_key=kwargs.get("aws_secret_access_key"),
        )
        creds = sts.assume_role(RoleArn=str(role_arn), RoleSessionName="cryo-archive")[
            "Credentials"
        ]
        kwargs["aws_access_key_id"] = creds["AccessKeyId"]
        kwargs["aws_secret_access_key"] = creds["SecretAccessKey"]
        kwargs["aws_session_token"] = creds["SessionToken"]
    return boto3.client("s3", **kwargs)


def source_view(source: Source) -> dict[str, Any]:
    return {"name": source.name, "type": source.type, "config": dict(source.config or {})}


def partition_files(iceberg_table: Any, keys: Iterable[str]) -> dict[str, list[ObjectRef]]:
    """Live data files of the current snapshot, grouped by the scanner's partition key.

    Delete files, manifests and metadata stay where they are: they are small, read on every
    query, and a slow or costly read of them would affect the whole table.
    """
    from cryo.scanner.analyze import (
        _is_delete_file,
        data_file_partition_values,
        iter_data_files,
        partition_fields,
        partition_key,
    )

    wanted: dict[str, list[ObjectRef]] = {key: [] for key in keys}
    fields = partition_fields(iceberg_table)
    for data_file in iter_data_files(iceberg_table):
        if _is_delete_file(data_file):
            continue
        key = partition_key(data_file_partition_values(data_file, fields))
        if key not in wanted:
            continue
        ref = parse_s3_uri(str(getattr(data_file, "file_path", "") or ""))
        if ref is None:
            continue
        size = getattr(data_file, "file_size_in_bytes", None)
        wanted[key].append(ObjectRef(ref.bucket, ref.key, int(size) if size is not None else None))
    return wanted


def is_stale(job: ArchiveJob, now: datetime | None = None) -> bool:
    if job.status not in ACTIVE_STATUSES:
        return False
    now = now or datetime.now(UTC)
    seen = job.heartbeat_at or job.started_at or job.created_at
    if seen is None:
        return False
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=UTC)
    return now - seen > STALE_AFTER


def active_job(session: Session, table_id: uuid.UUID) -> ArchiveJob | None:
    for job in session.scalars(
        select(ArchiveJob)
        .where(ArchiveJob.table_id == table_id, ArchiveJob.status.in_(ACTIVE_STATUSES))
        .order_by(ArchiveJob.created_at.desc())
    ):
        if not is_stale(job):
            return job
    return None


def _lock_store() -> Any:
    from cryo.infra.wiring import build_runtime_wiring

    return build_runtime_wiring().lock_store


def submit(
    job_id: uuid.UUID,
    session_factory: Callable[[], Session],
    *,
    lock_store_factory: Callable[[], Any] = _lock_store,
    client_factory: Callable[[Mapping[str, Any]], Any] = s3_client_for_source,
    table_loader: Callable[[Mapping[str, Any], str, str], Any] | None = None,
) -> None:
    _pool.submit(
        run_job,
        job_id,
        session_factory,
        lock_store_factory=lock_store_factory,
        client_factory=client_factory,
        table_loader=table_loader,
    )


def _load_table(source: Mapping[str, Any], schema: str, name: str) -> Any:
    from cryo.table_config.live import load_iceberg_table

    return load_iceberg_table(source, schema, name)


def run_job(
    job_id: uuid.UUID,
    session_factory: Callable[[], Session],
    *,
    lock_store_factory: Callable[[], Any] = _lock_store,
    client_factory: Callable[[Mapping[str, Any]], Any] = s3_client_for_source,
    table_loader: Callable[[Mapping[str, Any], str, str], Any] | None = None,
) -> None:
    loader = table_loader or _load_table
    session = session_factory()
    lock_store = None
    lock_token = f"archive:{job_id}"
    table_key: str | None = None
    try:
        job = session.get(ArchiveJob, job_id)
        if job is None:
            return
        table = session.get(Table, job.table_id)
        source = session.get(Source, table.source_id) if table is not None else None
        if table is None or source is None:
            _finish(session, job, "failed", errors=["Table or its source no longer exists."])
            return
        table_key = str(table.id)
        try:
            lock_store = lock_store_factory()
        except Exception:
            logger.warning("archive job %s: no lock store, running unlocked", job_id, exc_info=True)
        if lock_store is not None and not lock_store.acquire_table_lock(
            table_key, lock_token, LOCK_TTL_SECONDS
        ):
            _finish(
                session,
                job,
                "failed",
                errors=["Maintenance is running on this table. Try again when it finishes."],
            )
            return
        now = datetime.now(UTC)
        job.status = "running"
        job.started_at = now
        job.heartbeat_at = now
        session.commit()

        view = source_view(source)
        iceberg_table = loader(view, table.schema_name, table.table_name)
        files = partition_files(iceberg_table, list(job.partition_keys))
        objects = [ref for refs in files.values() for ref in refs]
        job.objects_total = len(objects)
        session.commit()
        client = client_factory(view["config"])
        running = job

        def progress(result: TransitionResult) -> None:
            running.objects_moved = result.moved
            running.objects_skipped = result.skipped
            running.objects_failed = result.failed
            running.bytes_moved = result.bytes_moved
            running.heartbeat_at = datetime.now(UTC)
            session.commit()
            if lock_store is not None and table_key is not None:
                lock_store.renew_table_lock(table_key, lock_token, LOCK_TTL_SECONDS)

        result = transition_objects(client, objects, job.target_class, on_progress=progress)
        _record_classes(session, job, files, result)
        status = "succeeded" if result.failed == 0 else "partial" if result.moved else "failed"
        missing = [key for key, refs in files.items() if not refs]
        errors = list(result.errors)
        if missing:
            errors.append(f"{len(missing)} partition(s) had no live data files in the snapshot.")
        _finish(session, job, status, result=result, errors=errors)
    except Exception as exc:
        logger.exception("archive job %s failed", job_id)
        session.rollback()
        job = session.get(ArchiveJob, job_id)
        if job is not None:
            _finish(session, job, "failed", errors=[f"{type(exc).__name__}: {exc}"[:500]])
    finally:
        if lock_store is not None and table_key is not None:
            try:
                lock_store.release_table_lock(table_key, lock_token)
            except Exception:
                logger.warning("archive job %s: lock release failed", job_id, exc_info=True)
        session.close()


def _record_classes(
    session: Session,
    job: ArchiveJob,
    files: Mapping[str, list[ObjectRef]],
    result: TransitionResult,
) -> None:
    if result.failed and not result.moved:
        return
    now = datetime.now(UTC)
    for key, refs in files.items():
        if not refs:
            continue
        row = session.get(PartitionStorageClass, (job.table_id, key))
        if job.target_class == "STANDARD":
            if row is not None:
                session.delete(row)
            continue
        if row is None:
            row = PartitionStorageClass(table_id=job.table_id, partition_key=key)
            session.add(row)
        row.storage_class = job.target_class
        row.objects = len(refs)
        row.bytes = sum(ref.size or 0 for ref in refs)
        row.job_id = job.id
        row.updated_at = now


def _finish(
    session: Session,
    job: ArchiveJob,
    status: str,
    *,
    result: TransitionResult | None = None,
    errors: list[str] | None = None,
) -> None:
    now = datetime.now(UTC)
    job.status = status
    job.finished_at = now
    job.heartbeat_at = now
    if result is not None:
        job.objects_moved = result.moved
        job.objects_skipped = result.skipped
        job.objects_failed = result.failed
        job.bytes_moved = result.bytes_moved
    if errors is not None:
        job.errors = errors[:25]
    started = job.started_at or job.created_at
    if started is not None and started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    session.add(
        AuditLog(
            table_id=job.table_id,
            action=f"archive.{job.action}",
            status=status,
            engine="s3",
            detail=(
                f"{job.action} {len(job.partition_keys)} partition(s) to {job.target_class}: "
                f"{job.objects_moved} moved, {job.objects_skipped} skipped, "
                f"{job.objects_failed} failed; job {job.id}; reason: {job.reason}"
            )[:2000],
            duration_ms=int((now - started).total_seconds() * 1000) if started else None,
            actor=job.actor,
        )
    )
    session.commit()


__all__ = [
    "ACTIVE_STATUSES",
    "active_job",
    "is_stale",
    "partition_files",
    "run_job",
    "s3_client_for_source",
    "submit",
]
