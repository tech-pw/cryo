from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from cryo.db.models import Table, TableStats
from cryo.db.session import SessionLocal
from cryo.events.consumer import TableCommitConsumer
from cryo.events.redis_stream import RedisEventStream
from cryo.events.watcher import TableCommitted, TableCommitWatcher
from cryo.infra.wiring import RuntimeWiring, build_runtime_wiring
from cryo.scanner.incremental import CommitDelta, apply_commit_delta

logger = logging.getLogger(__name__)


class CatalogSnapshotProbe:
    """Probe current snapshot id via PyIceberg for hot tables."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def current_snapshot_id(self, table_ref: str) -> int | None:
        parts = table_ref.split(".")
        if len(parts) < 3:
            return None
        schema, table_name = parts[-2], parts[-1]
        with self._session_factory() as session:
            row = session.scalar(
                select(Table)
                .where(Table.schema_name == schema, Table.table_name == table_name)
                .options(selectinload(Table.stats))
            )
            if row is None or row.stats is None:
                return None
            raw = dict(row.stats.raw or {})
            snapshot_id = raw.get("current_snapshot_id") or raw.get("snapshot_id")
            if snapshot_id is not None:
                return int(snapshot_id)
            return int(row.stats.total_snapshots or 0)


def build_event_consumer(wiring: RuntimeWiring | None = None) -> TableCommitConsumer:
    wiring = wiring or build_runtime_wiring()
    stream = RedisEventStream(wiring.redis_client)
    return TableCommitConsumer(stream, on_commit=_on_table_committed)


def build_event_watcher(wiring: RuntimeWiring | None = None) -> TableCommitWatcher:
    wiring = wiring or build_runtime_wiring()
    stream = RedisEventStream(wiring.redis_client)
    consumer = TableCommitConsumer(stream)

    def publish(event: TableCommitted) -> None:
        consumer.publish(event)

    probe = CatalogSnapshotProbe(SessionLocal)
    watcher = TableCommitWatcher(probe, publish)
    for table_ref in _hot_table_refs():
        watcher.watch(table_ref)
    return watcher


def poll_events_once(wiring: RuntimeWiring | None = None) -> dict[str, int]:
    consumer = build_event_consumer(wiring)
    result = consumer.consume_once()
    return {"handled": result.handled, "failed": result.failed}


def _hot_table_refs() -> set[str]:
    refs: set[str] = set()
    with SessionLocal() as session:
        rows = session.execute(
            select(Table.schema_name, Table.table_name, TableStats.raw).join(
                TableStats, TableStats.table_id == Table.id
            )
        )
        for schema, table_name, raw in rows:
            payload = dict(raw or {})
            if payload.get("is_streaming"):
                refs.add(f"iceberg.{schema}.{table_name}")
    return refs


def _on_table_committed(event: TableCommitted) -> None:
    logger.info(
        "TableCommitted %s snapshot=%s at %s — scheduling opportunistic re-score",
        event.table_ref,
        event.snapshot_id,
        event.observed_at.isoformat(),
    )
    parts = event.table_ref.split(".")
    if len(parts) < 3:
        return
    schema, table_name = parts[-2], parts[-1]
    with SessionLocal() as session:
        table = session.scalar(
            select(Table).where(Table.schema_name == schema, Table.table_name == table_name)
        )
        if table is None:
            return
        stats = session.scalar(select(TableStats).where(TableStats.table_id == table.id))
        if stats is None:
            return
        applied = apply_commit_delta(
            session,
            CommitDelta(
                table_id=table.id,
                snapshot_id=event.snapshot_id,
                parent_snapshot_id=event.parent_snapshot_id,
                files_added=event.files_added,
                files_removed=event.files_removed,
                bytes_added=event.bytes_added,
                bytes_removed=event.bytes_removed,
                delete_files_added=event.delete_files_added,
                equality_delete_files_added=event.equality_delete_files_added,
                position_delete_files_added=event.position_delete_files_added,
                delete_files_removed=event.delete_files_removed,
                equality_delete_files_removed=event.equality_delete_files_removed,
                position_delete_files_removed=event.position_delete_files_removed,
                snapshots_added=event.snapshots_added,
                committed_at=event.observed_at,
            ),
        )
        if not applied:
            session.rollback()
            return
        session.commit()
        _enqueue_maintenance_evaluation(
            table_id=str(table.id),
            source_id=str(table.source_id),
            trigger="commit_event",
            snapshot_id=event.snapshot_id,
            reconcile_due=_reconciliation_due(stats),
        )


def _reconciliation_due(stats: TableStats, *, now: datetime | None = None) -> bool:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    last = stats.last_full_reconcile_at
    if last is None:
        return True
    return current - last.astimezone(UTC) >= timedelta(days=7)


def _enqueue_maintenance_evaluation(
    *,
    table_id: str,
    source_id: str,
    trigger: str,
    snapshot_id: int,
    reconcile_due: bool,
) -> None:
    try:
        wiring = build_runtime_wiring()
        wiring.redis_client.xadd(
            "cryo:maintenance-eval",
            {
                "table_id": table_id,
                "source_id": source_id,
                "trigger": trigger,
                "snapshot_id": str(snapshot_id),
                "reconcile_due": "1" if reconcile_due else "0",
                "enqueued_at": datetime.now(UTC).isoformat(),
            },
            maxlen=10_000,
            approximate=True,
        )
    except Exception as exc:  # pragma: no cover - best effort path
        logger.warning("Failed to enqueue maintenance evaluation for table %s: %s", table_id, exc)
