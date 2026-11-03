from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from cryo.db.models import Source, Table, TableStats
from cryo.db.session import SessionLocal
from cryo.events.consumer import TableCommitConsumer
from cryo.events.drift import accumulate_drift, small_file_bytes_from_settings
from cryo.events.redis_stream import RedisEventStream
from cryo.events.snapshots import (
    DEFAULT_MAX_COMMITS_PER_OBSERVATION,
    SnapshotObservation,
    observe_metadata,
)
from cryo.events.watcher import TableCommitted, TableCommitWatcher
from cryo.infra.wiring import RuntimeWiring, build_runtime_wiring
from cryo.scanner.incremental import CommitDelta, apply_commit_delta

logger = logging.getLogger(__name__)

MAINTENANCE_EVAL_STREAM = "cryo:maintenance-eval"


@dataclass(frozen=True)
class WatchTarget:
    table_id: str
    source_id: str
    schema_name: str
    table_name: str
    table_ref: str
    snapshot_id: int | None = None
    sequence_number: int | None = None


class LiveCatalogSnapshotProbe:
    """Read a watched table's current snapshot, and the commits since the last one seen,
    straight from the catalog.

    One ``load_table`` per probe: the table metadata already lists every live snapshot with
    its summary, so no manifest or data file is read. Before #77 the probe read the snapshot
    id from Cryo's own last scan (``TableStats.raw``), so the watcher only ever noticed
    commits the scanner had already seen.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        catalog_factory: Callable[[Any], Any] | None = None,
        timeout_seconds: float = 10.0,
        max_commits: int = DEFAULT_MAX_COMMITS_PER_OBSERVATION,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._catalog_factory = catalog_factory
        self._timeout_seconds = timeout_seconds
        self._max_commits = max_commits
        self._executor = executor
        self._targets: dict[str, WatchTarget] = {}
        self._catalogs: dict[str, Any] = {}

    def set_targets(self, targets: Iterable[WatchTarget]) -> None:
        self._targets = {target.table_ref: target for target in targets}

    def target(self, table_ref: str) -> WatchTarget | None:
        return self._targets.get(table_ref)

    def current_snapshot_id(self, table_ref: str) -> int | None:
        observation = self.observe(
            table_ref, last_seen_snapshot_id=None, last_seen_sequence_number=None
        )
        return observation.snapshot_id if observation is not None else None

    def observe(
        self,
        table_ref: str,
        *,
        last_seen_snapshot_id: int | None,
        last_seen_sequence_number: int | None,
    ) -> SnapshotObservation | None:
        target = self._targets.get(table_ref)
        if target is None:
            return None
        metadata = self._load_metadata(target)
        return observe_metadata(
            metadata,
            last_seen_snapshot_id=last_seen_snapshot_id,
            last_seen_sequence_number=last_seen_sequence_number,
            max_commits=self._max_commits,
        )

    def _load_metadata(self, target: WatchTarget) -> Any:
        catalog = self._catalog(target.source_id)

        def _read() -> Any:
            table = catalog.load_table(target.schema_name, target.table_name)
            metadata = getattr(table, "metadata", None)
            if metadata is None:
                raise RuntimeError("catalog returned a table without metadata")
            return metadata

        try:
            future = self._pool().submit(_read)
            return future.result(timeout=self._timeout_seconds)
        except FuturesTimeoutError as exc:
            raise TimeoutError(
                f"load_table({target.schema_name}.{target.table_name}) exceeded "
                f"{self._timeout_seconds:.0f}s"
            ) from exc
        except Exception:
            # A broken client (expired token, closed Thrift transport) is rebuilt next time.
            self._catalogs.pop(target.source_id, None)
            raise

    def _catalog(self, source_id: str) -> Any:
        catalog = self._catalogs.get(source_id)
        if catalog is not None:
            return catalog
        with self._session_factory() as session:
            source = session.get(Source, uuid.UUID(source_id))
            if source is None:
                raise LookupError(f"source {source_id} not found")
            view: dict[str, Any] = {
                "id": str(source.id),
                "name": source.name,
                "type": source.type,
                "config": dict(source.config or {}),
            }
        factory = self._catalog_factory
        if factory is None:
            from cryo.sources.factory import source_catalog_for

            factory = source_catalog_for
        catalog = factory(view)
        self._catalogs[source_id] = catalog
        return catalog

    def _pool(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=4, thread_name_prefix="cryo-events-probe"
            )
        return self._executor


class RefreshingCommitWatcher:
    """A commit watcher whose watch list is re-read from the database every so often, so a
    table that becomes streaming (or leaves the fleet) is picked up without a restart."""

    def __init__(
        self,
        watcher: TableCommitWatcher,
        probe: LiveCatalogSnapshotProbe,
        load_targets: Callable[[], list[WatchTarget]],
        *,
        refresh_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.watcher = watcher
        self._probe = probe
        self._load_targets = load_targets
        self._refresh_seconds = refresh_seconds
        self._clock = clock
        self._next_refresh = 0.0

    def refresh(self) -> None:
        targets = self._load_targets()
        self._probe.set_targets(targets)
        wanted = {target.table_ref for target in targets}
        for table_ref in self.watcher.watched - wanted:
            self.watcher.unwatch(table_ref)
        for target in targets:
            self.watcher.watch(
                target.table_ref,
                table_id=target.table_id,
                snapshot_id=target.snapshot_id,
                sequence_number=target.sequence_number,
            )
        self._next_refresh = self._clock() + self._refresh_seconds

    def poll_once(self, *, now: datetime | None = None) -> list[TableCommitted]:
        if self._clock() >= self._next_refresh:
            try:
                self.refresh()
            except Exception as exc:
                logger.warning("Commit watcher target refresh failed: %s", exc)
                self._next_refresh = self._clock() + self._refresh_seconds
        return self.watcher.poll_once(now=now)


def build_event_consumer(wiring: RuntimeWiring | None = None) -> TableCommitConsumer:
    wiring = wiring or build_runtime_wiring()
    stream = RedisEventStream(wiring.redis_client)
    return TableCommitConsumer(
        stream, on_commit=partial(_on_table_committed, redis_client=wiring.redis_client)
    )


def build_event_watcher(wiring: RuntimeWiring | None = None) -> RefreshingCommitWatcher:
    from cryo.config import get_settings

    settings = get_settings()
    wiring = wiring or build_runtime_wiring()
    stream = RedisEventStream(wiring.redis_client)
    consumer = TableCommitConsumer(stream)

    def publish(event: TableCommitted) -> None:
        consumer.publish(event)

    probe = LiveCatalogSnapshotProbe(
        SessionLocal, timeout_seconds=settings.events_probe_timeout_seconds
    )
    watcher = TableCommitWatcher(
        probe,
        publish,
        max_tables_per_poll=settings.events_probe_max_tables_per_poll,
        min_table_interval_seconds=settings.events_probe_min_table_interval_seconds,
    )
    return RefreshingCommitWatcher(
        watcher,
        probe,
        lambda: load_watch_targets(SessionLocal),
        refresh_seconds=settings.events_target_refresh_seconds,
    )


def poll_events_once(wiring: RuntimeWiring | None = None) -> dict[str, int]:
    consumer = build_event_consumer(wiring)
    result = consumer.consume_once()
    return {"handled": result.handled, "failed": result.failed}


def load_watch_targets(session_factory: Callable[[], Session]) -> list[WatchTarget]:
    """Active streaming tables -- the ones whose health moves between nightly scans."""
    from cryo.db.model_utils import qualified_table_name
    from cryo.db.repositories.exclusions import table_is_excluded

    with session_factory() as session:
        rows = session.execute(
            select(Table, TableStats.snapshot_watermark, TableStats.raw)
            .join(TableStats, TableStats.table_id == Table.id)
            .options(selectinload(Table.source))
            .where(
                Table.is_active.is_(True),
                ~table_is_excluded(),
                TableStats.raw["is_streaming"].as_boolean().is_(True),
            )
        ).all()
        targets: list[WatchTarget] = []
        for table, watermark, raw in rows:
            if table.source is None:
                continue
            smart = dict((raw or {}).get("smart_maintenance") or {})
            sequence = smart.get("last_commit_sequence_number")
            targets.append(
                WatchTarget(
                    table_id=str(table.id),
                    source_id=str(table.source_id),
                    schema_name=table.schema_name,
                    table_name=table.table_name,
                    table_ref=qualified_table_name(table),
                    snapshot_id=int(watermark) if watermark is not None else None,
                    sequence_number=sequence if isinstance(sequence, int) else None,
                )
            )
    return targets


def _resolve_table(session: Session, event: TableCommitted) -> Table | None:
    if event.table_id:
        try:
            return session.get(Table, uuid.UUID(event.table_id))
        except ValueError:
            return None
    # Events published before table_id existed carry only the dotted name.
    parts = event.table_ref.split(".")
    if len(parts) < 3:
        return None
    schema, table_name = parts[-2], parts[-1]
    return session.scalar(
        select(Table).where(Table.schema_name == schema, Table.table_name == table_name).limit(1)
    )


def _on_table_committed(
    event: TableCommitted,
    *,
    redis_client: Any | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
) -> None:
    logger.info(
        "TableCommitted %s snapshot=%s commits=%s at %s",
        event.table_ref,
        event.snapshot_id,
        event.snapshots_added,
        event.observed_at.isoformat(),
    )
    with session_factory() as session:
        table = _resolve_table(session, event)
        if table is None:
            return
        stats = session.get(TableStats, table.id)
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
                committed_at=event.committed_at or event.observed_at,
                sequence_number=event.sequence_number,
            ),
        )
        if not applied:
            session.rollback()
            return
        accumulate_drift(
            session,
            table_id=table.id,
            stats=stats,
            event=event,
            small_file_bytes=small_file_bytes_from_settings(),
        )
        session.commit()
        table_id = str(table.id)
        source_id = str(table.source_id)
        reconcile_due = _reconciliation_due(stats)
    _enqueue_maintenance_evaluation(
        table_id=table_id,
        source_id=source_id,
        trigger="commit_event",
        snapshot_id=event.snapshot_id,
        reconcile_due=reconcile_due,
        writer_commits=max(0, event.snapshots_added - event.maintenance_commits),
        redis_client=redis_client,
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
    writer_commits: int = 0,
    redis_client: Any | None = None,
) -> None:
    try:
        client = redis_client if redis_client is not None else build_runtime_wiring().redis_client
        client.xadd(
            MAINTENANCE_EVAL_STREAM,
            {
                "table_id": table_id,
                "source_id": source_id,
                "trigger": trigger,
                "snapshot_id": str(snapshot_id),
                "writer_commits": str(writer_commits),
                "reconcile_due": "1" if reconcile_due else "0",
                "enqueued_at": datetime.now(UTC).isoformat(),
            },
            maxlen=10_000,
            approximate=True,
        )
    except Exception as exc:  # pragma: no cover - best effort path
        logger.warning("Failed to enqueue maintenance evaluation for table %s: %s", table_id, exc)
