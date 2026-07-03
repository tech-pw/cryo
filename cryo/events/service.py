from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from cryo.db.models import Table, TableStats
from cryo.db.session import SessionLocal
from cryo.events.consumer import TableCommitConsumer
from cryo.events.redis_stream import RedisEventStream
from cryo.events.watcher import TableCommitted, TableCommitWatcher
from cryo.infra.wiring import RuntimeWiring, build_runtime_wiring

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
        raw = dict(stats.raw or {})
        raw["last_commit_at"] = datetime.now(UTC).isoformat()
        raw["last_commit_snapshot_id"] = event.snapshot_id
        stats.raw = raw
        session.commit()
