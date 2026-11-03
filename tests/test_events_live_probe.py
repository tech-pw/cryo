"""Live-catalog commit watcher (#77): snapshot-summary deltas, probe budget, drift."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from pyiceberg.table.snapshots import Operation, Snapshot, Summary

from cryo.db.models import Source, Table, TableDrift, TableStats
from cryo.events.consumer import InMemoryEventStream, TableCommitConsumer
from cryo.events.drift import reset_drift, small_files_in_commit
from cryo.events.service import (
    MAINTENANCE_EVAL_STREAM,
    LiveCatalogSnapshotProbe,
    RefreshingCommitWatcher,
    WatchTarget,
    _on_table_committed,
    load_watch_targets,
)
from cryo.events.snapshots import (
    SnapshotCommit,
    SnapshotObservation,
    observe_metadata,
    summary_deltas,
)
from cryo.events.watcher import TableCommitted, TableCommitWatcher
from cryo.scanner.incremental import CommitDelta, apply_commit_delta

# Snapshot summaries as Spark / Flink / Trino write them.
SPARK_APPEND = {
    "operation": "append",
    "added-data-files": "12",
    "added-records": "1200",
    "added-files-size": str(12 * 2 * 1024 * 1024),
    "total-data-files": "512",
}
FLINK_UPSERT = {
    "operation": "overwrite",
    "added-data-files": "2",
    "added-delete-files": "3",
    "added-equality-delete-files": "2",
    "added-position-delete-files": "1",
    "added-files-size": str(3 * 1024 * 1024),
}
LEGACY_DELETES = {"operation": "delete", "added-delete-files": "4"}
TRINO_OPTIMIZE = {
    "operation": "replace",
    "added-data-files": "1",
    "deleted-data-files": "40",
    "added-files-size": str(512 * 1024 * 1024),
    "removed-files-size": str(500 * 1024 * 1024),
    "removed-position-delete-files": "5",
}


def _commit(snapshot_id: int, parent: int | None, seq: int, summary: dict[str, str]) -> Snapshot:
    fields = dict(summary)
    operation = Operation(fields.pop("operation"))
    return Snapshot(
        snapshot_id=snapshot_id,
        parent_snapshot_id=parent,
        sequence_number=seq,
        timestamp_ms=1_700_000_000_000 + seq * 1000,
        manifest_list=f"s3://example/metadata/snap-{snapshot_id}.avro",
        summary=Summary(operation, **fields),
    )


def _metadata(*snapshots: Snapshot) -> SimpleNamespace:
    return SimpleNamespace(current_snapshot_id=snapshots[-1].snapshot_id, snapshots=list(snapshots))


def test_summary_deltas_read_spark_append() -> None:
    deltas = summary_deltas(_as_commit(SPARK_APPEND))
    assert deltas.files_added == 12
    assert deltas.bytes_added == 12 * 2 * 1024 * 1024
    assert deltas.delete_files_added == 0
    assert deltas.snapshots_added == 1
    assert deltas.maintenance_commits == 0


def test_summary_deltas_read_flink_upsert_delete_counters() -> None:
    deltas = summary_deltas(_as_commit(FLINK_UPSERT))
    assert deltas.files_added == 2
    assert deltas.delete_files_added == 3
    assert deltas.equality_delete_files_added == 2
    assert deltas.position_delete_files_added == 1


def test_summary_deltas_fall_back_to_combined_delete_counter() -> None:
    deltas = summary_deltas(_as_commit(LEGACY_DELETES))
    assert deltas.delete_files_added == 4


def test_summary_deltas_mark_compaction_as_maintenance() -> None:
    deltas = summary_deltas(_as_commit(TRINO_OPTIMIZE))
    assert deltas.maintenance_commits == 1
    assert deltas.files_removed == 40
    assert deltas.position_delete_files_removed == 5
    assert deltas.delete_files_removed == 5


def _as_commit(summary: dict[str, str]) -> SnapshotCommit:
    return SnapshotCommit(
        snapshot_id=1,
        parent_snapshot_id=None,
        sequence_number=1,
        timestamp_ms=None,
        operation=summary["operation"],
        summary=summary,
    )


def test_observe_metadata_sums_every_commit_since_last_seen() -> None:
    # Ids deliberately not increasing: Iceberg snapshot ids are random.
    metadata = _metadata(
        _commit(900, None, 1, SPARK_APPEND),
        _commit(42, 900, 2, SPARK_APPEND),
        _commit(7_000, 42, 3, FLINK_UPSERT),
        _commit(5, 7_000, 4, TRINO_OPTIMIZE),
    )
    observation = observe_metadata(metadata, last_seen_snapshot_id=900)
    assert observation is not None
    assert observation.complete is True
    assert [c.snapshot_id for c in observation.commits] == [42, 7_000, 5]
    deltas = observation.deltas()
    assert deltas.snapshots_added == 3
    assert deltas.maintenance_commits == 1
    assert deltas.files_added == 12 + 2 + 1
    assert deltas.delete_files_added == 3


def test_observe_metadata_unchanged_snapshot_has_no_commits() -> None:
    metadata = _metadata(_commit(900, None, 1, SPARK_APPEND))
    observation = observe_metadata(metadata, last_seen_snapshot_id=900)
    assert observation == SnapshotObservation(snapshot_id=900)


def test_observe_metadata_first_sight_only_seeds() -> None:
    metadata = _metadata(_commit(900, None, 1, SPARK_APPEND), _commit(1, 900, 2, SPARK_APPEND))
    observation = observe_metadata(metadata, last_seen_snapshot_id=None)
    assert observation is not None
    assert observation.snapshot_id == 1
    assert observation.commits == ()


def test_observe_metadata_stops_at_sequence_number_when_parent_is_gone() -> None:
    # The last snapshot we saw (seq 2) was rolled back / expired; only seq 3+ are new.
    metadata = _metadata(
        _commit(11, None, 1, SPARK_APPEND),
        _commit(12, 11, 2, SPARK_APPEND),
        _commit(13, 12, 3, SPARK_APPEND),
        _commit(14, 13, 4, SPARK_APPEND),
    )
    observation = observe_metadata(metadata, last_seen_snapshot_id=999, last_seen_sequence_number=2)
    assert observation is not None
    assert [c.snapshot_id for c in observation.commits] == [13, 14]
    assert observation.complete is False


def test_observe_metadata_caps_the_walk() -> None:
    snapshots = [_commit(1, None, 1, SPARK_APPEND)]
    for seq in range(2, 30):
        snapshots.append(_commit(seq, seq - 1, seq, SPARK_APPEND))
    observation = observe_metadata(_metadata(*snapshots), last_seen_snapshot_id=1, max_commits=5)
    assert observation is not None
    assert len(observation.commits) == 5
    assert observation.complete is False


class _FakeCatalog:
    def __init__(self, metadata_by_table: dict[tuple[str, str], Any]) -> None:
        self.metadata_by_table = metadata_by_table
        self.loads: list[tuple[str, str]] = []

    def load_table(self, schema: str, table: str) -> Any:
        self.loads.append((schema, table))
        return SimpleNamespace(metadata=self.metadata_by_table[(schema, table)])


def _add_source(session: Any) -> uuid.UUID:
    source_id = uuid.uuid4()
    session.add(Source(id=source_id, name=f"lake-{source_id}", type="rest", config={}))
    return source_id


def _add_table(
    session: Any,
    source_id: uuid.UUID,
    name: str,
    *,
    streaming: bool = True,
    watermark: int | None = 900,
) -> uuid.UUID:
    table_id = uuid.uuid4()
    session.add(
        Table(
            id=table_id,
            source_id=source_id,
            schema_name="analytics",
            table_name=name,
            first_seen_at=datetime(2026, 10, 1, tzinfo=UTC),
            is_active=True,
        )
    )
    session.add(
        TableStats(
            table_id=table_id,
            scan_at=datetime(2026, 11, 1, tzinfo=UTC),
            needs_compaction=False,
            compaction_priority="LOW",
            grade="A",
            overall_score=95,
            total_files=500,
            small_files_count=0,
            total_size_gb=100.0,
            avg_file_size_mb=200.0,
            total_partitions=10,
            total_snapshots=20,
            total_delete_files=0,
            delete_file_pct=0.0,
            snapshot_watermark=watermark,
            raw={"is_streaming": streaming},
        )
    )
    return table_id


def test_live_probe_reads_the_catalog_not_the_last_scan(session_factory: Any) -> None:
    with session_factory() as session:
        source_id = _add_source(session)
        table_id = _add_table(session, source_id, "events")
        session.commit()
    metadata = _metadata(_commit(900, None, 1, SPARK_APPEND), _commit(77, 900, 2, FLINK_UPSERT))
    catalog = _FakeCatalog({("analytics", "events"): metadata})
    built: list[dict[str, Any]] = []

    def factory(view: dict[str, Any]) -> _FakeCatalog:
        built.append(view)
        return catalog

    probe = LiveCatalogSnapshotProbe(session_factory, catalog_factory=factory)
    probe.set_targets(
        [
            WatchTarget(
                table_id=str(table_id),
                source_id=str(source_id),
                schema_name="analytics",
                table_name="events",
                table_ref="lake.analytics.events",
            )
        ]
    )

    observation = probe.observe(
        "lake.analytics.events", last_seen_snapshot_id=900, last_seen_sequence_number=1
    )
    assert observation is not None
    assert observation.snapshot_id == 77
    assert observation.deltas().delete_files_added == 3
    assert probe.current_snapshot_id("lake.analytics.events") == 77
    # One catalog client per source, reused across probes.
    assert len(built) == 1
    assert catalog.loads == [("analytics", "events"), ("analytics", "events")]


def test_live_probe_rebuilds_catalog_after_a_failure(session_factory: Any) -> None:
    with session_factory() as session:
        source_id = _add_source(session)
        session.commit()
    calls = {"n": 0}

    class _Broken:
        def load_table(self, schema: str, table: str) -> Any:
            calls["n"] += 1
            raise ConnectionError("transport closed")

    builds: list[int] = []

    def factory(view: dict[str, Any]) -> _Broken:
        builds.append(1)
        return _Broken()

    probe = LiveCatalogSnapshotProbe(session_factory, catalog_factory=factory)
    probe.set_targets(
        [WatchTarget(str(uuid.uuid4()), str(source_id), "analytics", "t", "lake.analytics.t")]
    )
    for _ in range(2):
        with pytest.raises(ConnectionError):
            probe.current_snapshot_id("lake.analytics.t")
    assert len(builds) == 2


class _CountingProbe:
    """CommitObservingProbe over in-memory metadata, counting catalog loads."""

    def __init__(self) -> None:
        self.metadata: dict[str, SimpleNamespace] = {}
        self.loads = 0

    def current_snapshot_id(self, table_ref: str) -> int | None:
        return self.metadata[table_ref].current_snapshot_id

    def observe(
        self,
        table_ref: str,
        *,
        last_seen_snapshot_id: int | None,
        last_seen_sequence_number: int | None,
    ) -> SnapshotObservation | None:
        self.loads += 1
        return observe_metadata(
            self.metadata[table_ref],
            last_seen_snapshot_id=last_seen_snapshot_id,
            last_seen_sequence_number=last_seen_sequence_number,
        )


def test_watcher_publishes_summary_deltas() -> None:
    probe = _CountingProbe()
    probe.metadata["t"] = _metadata(_commit(900, None, 1, SPARK_APPEND))
    events: list[TableCommitted] = []
    watcher = TableCommitWatcher(probe, events.append)
    watcher.watch("t", table_id="tid")

    assert watcher.poll_once() == []  # first sight seeds the baseline
    probe.metadata["t"] = _metadata(
        _commit(900, None, 1, SPARK_APPEND),
        _commit(3, 900, 2, SPARK_APPEND),
        _commit(8, 3, 3, FLINK_UPSERT),
    )
    (event,) = watcher.poll_once()
    assert event.table_id == "tid"
    assert event.snapshot_id == 8
    assert event.parent_snapshot_id == 900
    assert event.sequence_number == 3
    assert event.snapshots_added == 2
    assert event.files_added == 14
    assert event.delete_files_added == 3
    assert event.equality_delete_files_added == 2
    assert event.complete is True
    assert event.committed_at == datetime.fromtimestamp(1_700_000_003, UTC)
    assert events == [event]


def test_watcher_reports_commits_made_while_it_was_down() -> None:
    probe = _CountingProbe()
    probe.metadata["t"] = _metadata(
        _commit(900, None, 1, SPARK_APPEND), _commit(5, 900, 2, SPARK_APPEND)
    )
    watcher = TableCommitWatcher(probe, lambda _e: None)
    watcher.watch("t", snapshot_id=900, sequence_number=1)
    (event,) = watcher.poll_once()
    assert event.snapshot_id == 5
    assert event.files_added == 12


def test_watcher_keeps_watermark_only_events_for_id_only_probes() -> None:
    from cryo.events.watcher import InMemorySnapshotProbe

    probe = InMemorySnapshotProbe({"t": 10})
    watcher = TableCommitWatcher(probe, lambda _e: None, hot_table_refs={"t"})
    watcher.poll_once()
    probe.commit("t", 3)
    (event,) = watcher.poll_once()
    assert event.snapshot_id == 3
    assert event.snapshots_added == 0
    assert event.complete is False


def test_watcher_survives_a_failing_probe() -> None:
    class _Flaky(_CountingProbe):
        def observe(self, table_ref: str, **kwargs: Any) -> SnapshotObservation | None:
            if table_ref == "bad":
                raise TimeoutError("catalog slow")
            return super().observe(table_ref, **kwargs)

    probe = _Flaky()
    probe.metadata["good"] = _metadata(_commit(1, None, 1, SPARK_APPEND))
    watcher = TableCommitWatcher(probe, lambda _e: None, hot_table_refs={"bad", "good"})
    watcher.poll_once()
    probe.metadata["good"] = _metadata(
        _commit(1, None, 1, SPARK_APPEND), _commit(2, 1, 2, SPARK_APPEND)
    )
    assert [e.table_ref for e in watcher.poll_once()] == ["good"]


def test_watcher_bounds_catalog_load_for_a_thousand_tables() -> None:
    """Load target for 1k streaming tables at a 5s poll: never more than the per-poll budget
    of catalog loads, every table still swept, and no table re-probed inside its interval."""
    probe = _CountingProbe()
    clock = {"t": 0.0}
    watcher = TableCommitWatcher(
        probe,
        lambda _e: None,
        max_tables_per_poll=100,
        min_table_interval_seconds=30.0,
        clock=lambda: clock["t"],
    )
    for index in range(1000):
        ref = f"t{index}"
        probe.metadata[ref] = _metadata(_commit(index + 1, None, 1, SPARK_APPEND))
        watcher.watch(ref)

    loads_per_poll: list[int] = []
    for _ in range(12):
        before = probe.loads
        watcher.poll_once()
        loads_per_poll.append(probe.loads - before)
        clock["t"] += 5.0

    assert max(loads_per_poll) == 100
    # The first 10 polls reach every table exactly once: oldest-probed first, so nothing is
    # probed twice before the whole list has had a turn.
    assert sum(loads_per_poll[:10]) == 1000
    assert probe.loads == 12 * 100


def test_watcher_interval_skips_recently_probed_tables() -> None:
    probe = _CountingProbe()
    probe.metadata["t"] = _metadata(_commit(1, None, 1, SPARK_APPEND))
    clock = {"t": 0.0}
    watcher = TableCommitWatcher(
        probe, lambda _e: None, min_table_interval_seconds=30.0, clock=lambda: clock["t"]
    )
    watcher.watch("t")
    watcher.poll_once()
    clock["t"] = 10.0
    watcher.poll_once()
    assert probe.loads == 1
    clock["t"] = 31.0
    watcher.poll_once()
    assert probe.loads == 2


def test_load_watch_targets_selects_active_streaming_tables(session_factory: Any) -> None:
    with session_factory() as session:
        source_id = _add_source(session)
        streaming = _add_table(session, source_id, "clicks", streaming=True, watermark=55)
        _add_table(session, source_id, "dim_users", streaming=False)
        session.commit()
    targets = load_watch_targets(session_factory)
    assert [t.table_id for t in targets] == [str(streaming)]
    assert targets[0].snapshot_id == 55
    assert targets[0].table_ref.endswith("analytics.clicks")


def test_refreshing_watcher_follows_the_target_list() -> None:
    probe = LiveCatalogSnapshotProbe(lambda: None)  # type: ignore[arg-type,return-value]
    watcher = TableCommitWatcher(probe, lambda _e: None)
    targets = [WatchTarget("a", "s", "db", "a", "lake.db.a", snapshot_id=1)]
    refreshing = RefreshingCommitWatcher(
        watcher, probe, lambda: list(targets), refresh_seconds=60, clock=lambda: 0.0
    )
    refreshing.refresh()
    assert watcher.watched == {"lake.db.a"}
    targets[:] = [WatchTarget("b", "s", "db", "b", "lake.db.b")]
    refreshing.refresh()
    assert watcher.watched == {"lake.db.b"}


class _FakeRedis:
    def __init__(self) -> None:
        self.added: list[tuple[str, dict[str, str]]] = []

    def xadd(self, stream: str, fields: dict[str, str], **_kwargs: Any) -> str:
        self.added.append((stream, fields))
        return "1-0"


def _event(table_id: uuid.UUID, snapshot_id: int, **fields: Any) -> TableCommitted:
    return TableCommitted(
        table_ref="lake.analytics.events",
        snapshot_id=snapshot_id,
        observed_at=datetime(2026, 11, 3, 10, 0, tzinfo=UTC),
        parent_snapshot_id=900,
        table_id=str(table_id),
        **fields,
    )


def test_commit_event_applies_deltas_and_accumulates_drift(session_factory: Any) -> None:
    with session_factory() as session:
        source_id = _add_source(session)
        table_id = _add_table(session, source_id, "events")
        session.commit()
    redis = _FakeRedis()
    event = _event(
        table_id,
        4,
        files_added=200,
        bytes_added=200 * 1024 * 1024,
        delete_files_added=6,
        snapshots_added=5,
        sequence_number=12,
        committed_at=datetime(2026, 11, 3, 9, 59, tzinfo=UTC),
    )

    _on_table_committed(event, redis_client=redis, session_factory=session_factory)

    with session_factory() as session:
        stats = session.get(TableStats, table_id)
        drift = session.get(TableDrift, table_id)
        assert stats is not None and drift is not None
        assert stats.snapshot_watermark == 4
        assert stats.total_files == 700
        assert drift.commits == 5
        assert drift.data_files_added == 200
        # 1 MiB average files are well under the small-file size.
        assert drift.small_files_added == 200
        assert drift.delete_files_added == 6
        assert drift.baseline_score == 95
        assert drift.estimated_score is not None and drift.estimated_score < 95
        assert drift.first_commit_at is not None
    ((stream, fields),) = redis.added
    assert stream == MAINTENANCE_EVAL_STREAM
    assert fields["table_id"] == str(table_id)
    assert fields["snapshot_id"] == "4"
    assert fields["writer_commits"] == "5"

    # Replaying the same snapshot is a no-op: no second drift increment, no second eval.
    _on_table_committed(event, redis_client=redis, session_factory=session_factory)
    with session_factory() as session:
        drift = session.get(TableDrift, table_id)
        assert drift is not None and drift.commits == 5
    assert len(redis.added) == 1


def test_compaction_commits_are_not_drift(session_factory: Any) -> None:
    with session_factory() as session:
        source_id = _add_source(session)
        table_id = _add_table(session, source_id, "events")
        session.commit()
    event = _event(
        table_id, 8, files_added=1, files_removed=40, snapshots_added=1, maintenance_commits=1
    )
    _on_table_committed(event, redis_client=_FakeRedis(), session_factory=session_factory)
    with session_factory() as session:
        drift = session.get(TableDrift, table_id)
        assert drift is not None
        assert drift.commits == 0
        assert drift.maintenance_commits == 1
        assert drift.first_commit_at is None


def test_reset_drift_zeroes_counters(session_factory: Any) -> None:
    with session_factory() as session:
        source_id = _add_source(session)
        table_id = _add_table(session, source_id, "events")
        session.commit()
    _on_table_committed(
        _event(table_id, 4, files_added=3, snapshots_added=3),
        redis_client=_FakeRedis(),
        session_factory=session_factory,
    )
    with session_factory() as session:
        reset = reset_drift(session, table_id, at=datetime(2026, 11, 3, 11, tzinfo=UTC))
        session.commit()
    assert reset is not None and reset.commits == 3
    with session_factory() as session:
        drift = session.get(TableDrift, table_id)
        assert drift is not None
        assert (drift.commits, drift.data_files_added, drift.first_commit_at) == (0, 0, None)
        assert drift.reset_at is not None


def test_job_store_reset_records_drift_to_completion(session_factory: Any) -> None:
    from cryo.db.repositories.job_store import SQLJobStore
    from cryo.observability.metrics import continuous_drift_to_completion_seconds

    with session_factory() as session:
        source_id = _add_source(session)
        table_id = _add_table(session, source_id, "events")
        session.commit()
    _on_table_committed(
        _event(table_id, 4, files_added=3, snapshots_added=3),
        redis_client=_FakeRedis(),
        session_factory=session_factory,
    )
    before = _histogram_count(continuous_drift_to_completion_seconds)
    SQLJobStore(session_factory).reset_table_drift(str(table_id))
    assert _histogram_count(continuous_drift_to_completion_seconds) == before + 1
    # No drift row: nothing to observe, nothing raised.
    SQLJobStore(session_factory).reset_table_drift(str(uuid.uuid4()))
    assert _histogram_count(continuous_drift_to_completion_seconds) == before + 1


def _histogram_count(histogram: Any) -> float:
    for metric in histogram.collect():
        for sample in metric.samples:
            if sample.name.endswith("_count"):
                return float(sample.value)
    return 0.0


def test_small_files_estimate_uses_average_added_size() -> None:
    mb = 1024 * 1024
    assert small_files_in_commit(10, 10 * 4 * mb, small_file_bytes=64 * mb) == 10
    assert small_files_in_commit(2, 2 * 256 * mb, small_file_bytes=64 * mb) == 0
    assert small_files_in_commit(0, 0, small_file_bytes=64 * mb) == 0


def test_apply_commit_delta_accepts_a_numerically_smaller_snapshot_id() -> None:
    """Snapshot ids are random; ``<=`` against the watermark dropped real commits."""
    table_id = uuid.uuid4()
    stats = SimpleNamespace(
        table_id=table_id,
        snapshot_watermark=8_000_000_000,
        scan_at=datetime(2026, 7, 1, tzinfo=UTC),
        total_files=10,
        total_size_gb=1.0,
        total_delete_files=0,
        equality_delete_files=0,
        position_delete_files=0,
        delete_file_pct=0.0,
        query_count_30d=0,
        small_files_count=0,
        raw={},
        detection_tier=None,
    )
    session = SimpleNamespace(get=lambda _model, _key: stats)
    delta = CommitDelta(
        table_id=table_id,
        snapshot_id=12,
        parent_snapshot_id=8_000_000_000,
        files_added=1,
        files_removed=0,
        bytes_added=0,
        bytes_removed=0,
        delete_files_added=0,
        sequence_number=5,
    )
    assert apply_commit_delta(session, delta) is True  # type: ignore[arg-type]
    assert stats.snapshot_watermark == 12
    assert stats.raw["smart_maintenance"]["last_commit_sequence_number"] == 5
    # An older sequence number is stale even with a different id.
    older = CommitDelta(**{**delta.__dict__, "snapshot_id": 99, "sequence_number": 4})
    assert apply_commit_delta(session, older) is False  # type: ignore[arg-type]


def test_consumer_round_trips_new_event_fields() -> None:
    stream = InMemoryEventStream()
    received: list[TableCommitted] = []
    consumer = TableCommitConsumer(stream, on_commit=received.append)
    event = _event(
        uuid.uuid4(),
        4,
        sequence_number=9,
        maintenance_commits=1,
        snapshots_added=2,
        committed_at=datetime(2026, 11, 3, 9, 0, tzinfo=UTC),
        complete=False,
    )
    consumer.publish(event)
    consumer.consume_once()
    assert received == [event]
